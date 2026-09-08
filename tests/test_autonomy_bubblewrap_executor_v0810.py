from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from elyndra.autonomy import (
    AutonomyExecutionBinding,
    AutonomyRepository,
    AutonomyRun,
    AutonomyRunStatus,
    BubblewrapExecutor,
    CancellationToken,
    Capability,
    CapabilityGrant,
    CommandSnapshot,
    CommandSpec,
    ExecutionBudget,
    ExecutionOutcome,
    ExecutionRequest,
    PreparedExecution,
    RunPlan,
    RunStep,
    WorkspaceScope,
)
from elyndra.db import Database


def _require_runtime() -> None:
    bwrap = Path("/usr/bin/bwrap")

    if not bwrap.exists():
        pytest.skip("Bubblewrap no está instalado.")

    command: list[str] = [
        str(bwrap),
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--cap-drop",
        "ALL",
        "--ro-bind",
        "/usr",
        "/usr",
    ]

    for system_path in (
        "/lib",
        "/lib64",
    ):
        if Path(system_path).exists():
            command.extend(
                (
                    "--ro-bind",
                    system_path,
                    system_path,
                )
            )

    command.extend(
        (
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--",
            "/usr/bin/python3.13",
            "--version",
        )
    )

    probe = subprocess.run(  # noqa: S603
        tuple(command),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=5,
        check=False,
    )

    if probe.returncode != 0:
        stderr = probe.stderr.decode(
            "utf-8",
            errors="replace",
        ).strip()

        pytest.fail(
            "Bubblewrap existe pero su runtime probe falló: "
            f"rc={probe.returncode}; stderr={stderr!r}"
        )


def _state(
    tmp_path: Path,
    *,
    code: str,
    timeout_seconds: int = 3,
    stdout_limit_bytes: int = 262_144,
    stderr_limit_bytes: int = 262_144,
    executable: str | None = None,
) -> tuple[
    Database,
    AutonomyRepository,
    AutonomyRun,
]:
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)

    resolved_executable = (
        executable
        or str(
            Path(sys.executable).resolve(
                strict=True
            )
        )
    )

    command = CommandSpec(
        executable=resolved_executable,
        argv=(
            resolved_executable,
            "-c",
            code,
        ),
        cwd=".",
        timeout_seconds=timeout_seconds,
        stdout_limit_bytes=stdout_limit_bytes,
        stderr_limit_bytes=stderr_limit_bytes,
    )

    now = datetime.now(UTC)

    grant = CapabilityGrant(
        capabilities=frozenset(
            {Capability.PROCESS_EXEC}
        ),
        issued_at=now,
        expires_at=now + timedelta(hours=1),
        max_steps=4,
        max_commands=8,
        max_retries=2,
        max_runtime_seconds=60,
        allowed_executables=(
            resolved_executable,
        ),
    )

    plan = RunPlan(
        objective="Execute sandbox runtime test",
        steps=(
            RunStep(
                step_id="run",
                capability=Capability.PROCESS_EXEC,
                action="execute sandbox runtime",
                target=".",
                command=command,
            ),
        ),
    )

    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(root),
        grant=grant,
        plan=plan,
    )

    database = Database(
        tmp_path / "vault.sqlite3",
        role="vault",
    )
    database.migrate()

    repository = AutonomyRepository(database)
    repository.create(run)

    repository.transition(
        run.run_id,
        AutonomyRunStatus.RUNNING,
        actor="owner",
        summary="Runtime test iniciado.",
    )

    return database, repository, run


def _prepare(
    repository: AutonomyRepository,
    run: AutonomyRun,
) -> PreparedExecution:
    contract = AutonomyExecutionBinding(
        repository
    ).bind(
        run.run_id,
        actor="owner",
    )

    return contract.prepare("run")


def _execute(
    repository: AutonomyRepository,
    prepared: PreparedExecution,
) -> object:
    executor = BubblewrapExecutor(
        repository,
        actor="owner",
    )

    return executor.execute(
        prepared,
        cancellation=CancellationToken(),
    )


def test_bubblewrap_executor_runs_real_process(
    tmp_path: Path,
) -> None:
    _require_runtime()

    _database, repository, run = _state(
        tmp_path,
        code="print('ELYNDRA_SANDBOX_OK')",
    )

    prepared = _prepare(
        repository,
        run,
    )

    result = _execute(
        repository,
        prepared,
    )

    assert result.outcome is ExecutionOutcome.SUCCEEDED
    assert result.exit_code == 0
    assert result.stdout == "ELYNDRA_SANDBOX_OK"
    assert not result.timed_out


def test_bubblewrap_executor_hides_host_etc_and_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_runtime()

    monkeypatch.setenv(
        "ELYNDRA_SECRET_PROBE",
        "MUST_NOT_LEAK",
    )

    code = """
import os
from pathlib import Path

assert not Path("/etc/passwd").exists()
assert os.environ.get("ELYNDRA_SECRET_PROBE") is None
print("ISOLATED")
"""

    _database, repository, run = _state(
        tmp_path,
        code=code,
    )

    result = _execute(
        repository,
        _prepare(repository, run),
    )

    assert result.outcome is ExecutionOutcome.SUCCEEDED
    assert result.stdout == "ISOLATED"


def test_bubblewrap_executor_blocks_network(
    tmp_path: Path,
) -> None:
    _require_runtime()

    code = """
import socket

sock = socket.socket(
    socket.AF_INET,
    socket.SOCK_STREAM,
)
sock.settimeout(0.25)

try:
    result = sock.connect_ex(("1.1.1.1", 53))
    assert result != 0
    print("NETWORK_BLOCKED")
finally:
    sock.close()
"""

    _database, repository, run = _state(
        tmp_path,
        code=code,
    )

    result = _execute(
        repository,
        _prepare(repository, run),
    )

    assert result.outcome is ExecutionOutcome.SUCCEEDED
    assert result.stdout == "NETWORK_BLOCKED"


def test_bubblewrap_executor_persists_workspace_write(
    tmp_path: Path,
) -> None:
    _require_runtime()

    code = """
from pathlib import Path

Path("sandbox-write.txt").write_text(
    "ELYNDRA_WRITE_OK\\n",
    encoding="utf-8",
)
print("WRITE_OK")
"""

    _database, repository, run = _state(
        tmp_path,
        code=code,
    )

    result = _execute(
        repository,
        _prepare(repository, run),
    )

    assert result.outcome is ExecutionOutcome.SUCCEEDED

    written = (
        run.workspace.root
        / "sandbox-write.txt"
    )

    assert written.read_text(
        encoding="utf-8"
    ) == "ELYNDRA_WRITE_OK\n"


def test_bubblewrap_executor_entrypoint_is_read_only(
    tmp_path: Path,
) -> None:
    _require_runtime()

    code = """
from pathlib import Path

path = Path("/run/elyndra/executable")

try:
    with path.open("ab") as handle:
        handle.write(b"x")
except OSError:
    print("SEALED_READ_ONLY")
else:
    raise SystemExit(7)
"""

    _database, repository, run = _state(
        tmp_path,
        code=code,
    )

    result = _execute(
        repository,
        _prepare(repository, run),
    )

    assert result.outcome is ExecutionOutcome.SUCCEEDED
    assert result.stdout == "SEALED_READ_ONLY"


def test_bubblewrap_executor_truncates_output(
    tmp_path: Path,
) -> None:
    _require_runtime()

    _database, repository, run = _state(
        tmp_path,
        code="print('X' * 4096)",
        stdout_limit_bytes=64,
    )

    result = _execute(
        repository,
        _prepare(repository, run),
    )

    assert result.outcome is ExecutionOutcome.SUCCEEDED
    assert result.stdout_truncated
    assert len(
        result.stdout.encode("utf-8")
    ) <= 64


def test_bubblewrap_executor_enforces_timeout(
    tmp_path: Path,
) -> None:
    _require_runtime()

    _database, repository, run = _state(
        tmp_path,
        code=(
            "import time; "
            "time.sleep(5); "
            "print('SHOULD_NOT_PRINT')"
        ),
        timeout_seconds=1,
    )

    result = _execute(
        repository,
        _prepare(repository, run),
    )

    assert result.outcome is ExecutionOutcome.FAILED
    assert result.timed_out
    assert result.error_code == "process_timeout"


def test_bubblewrap_executor_rejects_unreserved_request(
    tmp_path: Path,
) -> None:
    _require_runtime()

    _database, repository, run = _state(
        tmp_path,
        code="print('MUST_NOT_EXECUTE')",
    )

    step = run.plan.steps[0]
    command = step.command

    assert command is not None

    snapshot = CommandSnapshot.capture(
        command,
        resolved_cwd=run.workspace.root,
    )

    request = ExecutionRequest(
        run_id=run.run_id,
        step_id=step.step_id,
        capability=step.capability,
        action=step.action,
        target=step.target,
        requires_human_gate=False,
        command_sha256=snapshot.command_sha256,
    )

    prepared = PreparedExecution(
        request=request,
        resolved_target=str(
            run.workspace.root
        ),
        budget=ExecutionBudget.from_grant(
            run.grant
        ).snapshot(),
        command_snapshot=snapshot,
        reserved_runtime_seconds=(
            command.timeout_seconds
        ),
        retry=False,
    )

    executor = BubblewrapExecutor(
        repository,
        actor="owner",
    )

    with pytest.raises(
        PermissionError,
        match="No existe una reserva durable",
    ):
        executor.execute(
            prepared,
            cancellation=CancellationToken(),
        )

    assert repository.execution_budget(
        run.run_id,
        actor="owner",
    ).snapshot().commands_reserved == 0


def test_bubblewrap_executor_rejects_executable_drift(
    tmp_path: Path,
) -> None:
    _require_runtime()

    executable = tmp_path / "python-copy"

    shutil.copy2(
        Path(sys.executable).resolve(
            strict=True
        ),
        executable,
    )
    executable.chmod(0o755)

    _database, repository, run = _state(
        tmp_path,
        code="print('MUST_NOT_EXECUTE')",
        executable=str(executable),
    )

    prepared = _prepare(
        repository,
        run,
    )

    with executable.open("ab") as handle:
        handle.write(b"\x00")

    executor = BubblewrapExecutor(
        repository,
        actor="owner",
    )

    with pytest.raises(
        PermissionError,
    ):
        executor.execute(
            prepared,
            cancellation=CancellationToken(),
        )

    assert repository.execution_budget(
        run.run_id,
        actor="owner",
    ).snapshot().commands_reserved == 1


def test_bubblewrap_executor_rejects_replay(
    tmp_path: Path,
) -> None:
    _require_runtime()

    database, repository, run = _state(
        tmp_path,
        code="print('ONE_SHOT')",
    )

    prepared = _prepare(
        repository,
        run,
    )

    executor = BubblewrapExecutor(
        repository,
        actor="owner",
    )

    first = executor.execute(
        prepared,
        cancellation=CancellationToken(),
    )

    assert first.outcome is ExecutionOutcome.SUCCEEDED
    assert first.stdout == "ONE_SHOT"

    with pytest.raises(
        PermissionError,
        match="ya fue consumido para launch",
    ):
        executor.execute(
            prepared,
            cancellation=CancellationToken(),
        )

    assert repository.execution_budget(
        run.run_id,
        actor="owner",
    ).snapshot().commands_reserved == 1

    with database.connect() as connection:
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM assistant_autonomy_execution_launches
            WHERE request_id = ?
            """,
            (prepared.request.request_id,),
        ).fetchone()[0] == 1


def test_bubblewrap_executor_has_no_launcher_override() -> None:
    with pytest.raises(TypeError):
        BubblewrapExecutor(
            object(),  # type: ignore[arg-type]
            actor="owner",
            bwrap_path="/usr/bin/python3.13",  # type: ignore[call-arg]
        )
