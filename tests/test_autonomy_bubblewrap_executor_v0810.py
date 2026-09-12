from __future__ import annotations

import io
import json
import shutil
import sqlite3
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
    ExecutionResult,
    PreparedExecution,
    RunPlan,
    RunStep,
    WorkspaceScope,
)
from elyndra.autonomy.bubblewrap_executor import _TailCollector
from elyndra.autonomy.repository import _ExecutionObservationReceipt
from elyndra.db import Database


def _sandbox_python() -> str:
    candidate = Path("/usr/bin/python3")
    if not candidate.exists():
        pytest.fail("El Python del sandbox no está disponible en /usr/bin/python3.")
    return str(candidate.resolve(strict=True))


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
            _sandbox_python(),
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

    resolved_executable = executable or _sandbox_python()

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


def test_bubblewrap_executor_invalid_utf8_respects_exact_byte_limits(
    tmp_path: Path,
) -> None:
    _require_runtime()

    code = """
import os

os.write(1, b"A" + (b"\\xff" * 64))
os.write(2, b"B" + (b"\\xfe" * 64))
"""

    _database, repository, run = _state(
        tmp_path,
        code=code,
        stdout_limit_bytes=17,
        stderr_limit_bytes=19,
    )

    result = _execute(
        repository,
        _prepare(repository, run),
    )

    assert result.outcome is ExecutionOutcome.SUCCEEDED
    assert result.stdout_truncated
    assert result.stderr_truncated
    assert len(result.stdout.encode("utf-8")) <= 17
    assert len(result.stderr.encode("utf-8")) <= 19


@pytest.mark.parametrize("limit", (1, 2, 3, 7, 17))
def test_tail_collector_invalid_utf8_is_byte_safe(limit: int) -> None:
    collector = _TailCollector.create(
        io.BytesIO(b"prefix-" + (b"\xff" * 64)),
        limit,
    )
    collector.collect()

    text = collector.text()

    assert collector.truncated
    assert len(text.encode("utf-8")) <= limit


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


def test_bubblewrap_executor_persists_durable_observation(
    tmp_path: Path,
) -> None:
    _require_runtime()

    database, repository, run = _state(
        tmp_path,
        code="print('OBSERVED_OK')",
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

    observations = repository.execution_results(
        run.run_id,
        actor="owner",
    )

    assert len(observations) == 1

    observation = observations[0]

    assert observation["sequence"] == 1
    assert (
        observation["request_id"]
        == prepared.request.request_id
    )
    assert observation["step_id"] == "run"
    assert observation["outcome"] == "succeeded"
    assert observation["exit_code"] == 0
    assert observation["stdout"] == "OBSERVED_OK"
    assert observation["stderr"] == ""
    assert not observation["timed_out"]
    assert not observation["is_retry"]
    assert len(observation["stdout_sha256"]) == 64
    assert len(observation["stderr_sha256"]) == 64

    with database.connect() as connection:
        event = connection.execute(
            """
            SELECT
                event_type,
                step_id,
                payload_json
            FROM assistant_autonomy_events
            WHERE
                event_type='execution_observed'
                AND step_id='run'
            """
        ).fetchone()

    assert event is not None
    assert event["event_type"] == "execution_observed"


def test_execution_observation_rejects_duplicate_record(
    tmp_path: Path,
) -> None:
    _database, repository, run = _state(
        tmp_path,
        code="print('unused')",
    )

    prepared = _prepare(
        repository,
        run,
    )

    receipt = repository._claim_execution_launch(
        prepared.request,
        actor="owner",
        runtime_seconds=prepared.reserved_runtime_seconds,
        retry=prepared.retry,
    )
    result = ExecutionResult(
        request_id=prepared.request.request_id,
        outcome=ExecutionOutcome.SUCCEEDED,
        summary="Observación única.",
        exit_code=0,
    )
    repository._record_execution_result(
        prepared.request,
        result,
        actor="owner",
        receipt=receipt,
    )

    with pytest.raises(
        PermissionError,
        match="ya tiene una observación durable",
    ):
        repository._record_execution_result(
            prepared.request,
            result,
            actor="owner",
            receipt=receipt,
        )

    assert len(
        repository.execution_results(
            run.run_id,
            actor="owner",
        )
    ) == 1


def test_execution_observation_rows_are_append_only(
    tmp_path: Path,
) -> None:
    _require_runtime()

    database, repository, run = _state(
        tmp_path,
        code="print('IMMUTABLE_RESULT')",
    )

    _execute(
        repository,
        _prepare(repository, run),
    )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="autonomy_execution_results_append_only",
    ), database.connect() as connection:
        connection.execute(
            """
            UPDATE assistant_autonomy_execution_results
            SET stdout = 'tampered'
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="autonomy_execution_results_append_only",
    ), database.connect() as connection:
        connection.execute(
            """
            DELETE FROM assistant_autonomy_execution_results
            """
        )


def test_execution_observation_requires_executor_receipt(
    tmp_path: Path,
) -> None:
    _database, repository, run = _state(
        tmp_path,
        code="print('MUST_NOT_BE_OBSERVED')",
    )

    prepared = _prepare(
        repository,
        run,
    )
    real_receipt = repository._claim_execution_launch(
        prepared.request,
        actor="owner",
        runtime_seconds=prepared.reserved_runtime_seconds,
        retry=prepared.retry,
    )

    forged = ExecutionResult(
        request_id=prepared.request.request_id,
        outcome=ExecutionOutcome.SUCCEEDED,
        summary="Resultado fabricado.",
        exit_code=0,
    )

    with pytest.raises(
        PermissionError,
        match="Comprobante de observación inválido",
    ):
        repository._record_execution_result(
            prepared.request,
            forged,
            actor="owner",
            receipt=_ExecutionObservationReceipt(
                request_id=prepared.request.request_id,
                secret=b"x" * 32,
            ),
        )

    with pytest.raises(
        PermissionError,
        match="no pertenece al ExecutionRequest",
    ):
        repository._record_execution_result(
            prepared.request,
            forged,
            actor="owner",
            receipt=_ExecutionObservationReceipt(
                request_id="different-request-id",
                secret=real_receipt.secret,
            ),
        )

    assert not hasattr(repository, "record_execution_result")
    assert repository.execution_results(
        run.run_id,
        actor="owner",
    ) == []


def test_execution_observation_enforces_command_output_bound(
    tmp_path: Path,
) -> None:
    _database, repository, run = _state(
        tmp_path,
        code="print('unused')",
        stdout_limit_bytes=64,
    )

    prepared = _prepare(
        repository,
        run,
    )

    receipt = repository._claim_execution_launch(
        prepared.request,
        actor="owner",
        runtime_seconds=(
            prepared.reserved_runtime_seconds
        ),
        retry=prepared.retry,
    )

    forged = ExecutionResult(
        request_id=prepared.request.request_id,
        outcome=ExecutionOutcome.SUCCEEDED,
        summary="Output fuera de límite.",
        exit_code=0,
        stdout="X" * 65,
    )

    with pytest.raises(
        PermissionError,
        match="stdout excede",
    ):
        repository._record_execution_result(
            prepared.request,
            forged,
            actor="owner",
            receipt=receipt,
        )

    assert repository.execution_results(
        run.run_id,
        actor="owner",
    ) == []


def test_execution_observation_gap_is_explicit_until_result(
    tmp_path: Path,
) -> None:
    _database, repository, run = _state(
        tmp_path,
        code="print('unused')",
    )
    prepared = _prepare(repository, run)
    receipt = repository._claim_execution_launch(
        prepared.request,
        actor="owner",
        runtime_seconds=prepared.reserved_runtime_seconds,
        retry=prepared.retry,
    )

    gaps = repository.execution_observation_gaps(
        run.run_id,
        actor="owner",
    )
    assert gaps == [
        {
            "run_id": run.run_id,
            "request_id": prepared.request.request_id,
            "step_id": "run",
            "command_sha256": prepared.request.command_sha256,
            "state": "observation_unresolved",
            "launched_at": gaps[0]["launched_at"],
        }
    ]
    assert repository.execution_results(
        run.run_id,
        actor="owner",
    ) == []

    result = ExecutionResult(
        request_id=prepared.request.request_id,
        outcome=ExecutionOutcome.SUCCEEDED,
        summary="Observación válida.",
        exit_code=0,
    )
    repository._record_execution_result(
        prepared.request,
        result,
        actor="owner",
        receipt=receipt,
    )

    assert repository.execution_observation_gaps(
        run.run_id,
        actor="owner",
    ) == []
    assert len(repository.execution_results(run.run_id, actor="owner")) == 1


@pytest.mark.parametrize(
    ("outcome", "exit_code", "timed_out", "error_code"),
    (
        (ExecutionOutcome.SUCCEEDED, None, False, ""),
        (ExecutionOutcome.SUCCEEDED, 1, False, ""),
        (ExecutionOutcome.SUCCEEDED, 0, True, ""),
        (ExecutionOutcome.SUCCEEDED, 0, False, "unexpected"),
        (ExecutionOutcome.FAILED, None, False, "process_exit_nonzero"),
        (ExecutionOutcome.FAILED, 0, False, "process_exit_nonzero"),
        (ExecutionOutcome.FAILED, 1, True, "process_exit_nonzero"),
        (ExecutionOutcome.FAILED, 1, False, "process_timeout"),
        (ExecutionOutcome.FAILED, 0, True, "process_timeout"),
        (ExecutionOutcome.FAILED, 1, False, "sandbox_launch_failed"),
        (ExecutionOutcome.FAILED, None, True, "sandbox_launch_failed"),
        (ExecutionOutcome.CANCELLED, 0, False, "cancelled"),
        (ExecutionOutcome.CANCELLED, None, True, "cancelled"),
        (ExecutionOutcome.CANCELLED, None, False, "wrong"),
    ),
)
def test_execution_observation_rejects_impossible_result_semantics(
    tmp_path: Path,
    outcome: ExecutionOutcome,
    exit_code: int | None,
    timed_out: bool,
    error_code: str,
) -> None:
    _database, repository, run = _state(
        tmp_path,
        code="print('unused')",
    )
    prepared = _prepare(repository, run)
    receipt = repository._claim_execution_launch(
        prepared.request,
        actor="owner",
        runtime_seconds=prepared.reserved_runtime_seconds,
        retry=prepared.retry,
    )
    result = ExecutionResult(
        request_id=prepared.request.request_id,
        outcome=outcome,
        summary="Combinación imposible.",
        exit_code=exit_code,
        timed_out=timed_out,
        error_code=error_code,
    )

    with pytest.raises(PermissionError, match="inconsistente|requiere|Solo"):
        repository._record_execution_result(
            prepared.request,
            result,
            actor="owner",
            receipt=receipt,
        )

    assert repository.execution_results(run.run_id, actor="owner") == []
    assert len(repository.execution_observation_gaps(run.run_id, actor="owner")) == 1


@pytest.mark.parametrize("exit_code", (None, -15))
def test_execution_observation_accepts_cancellation_before_or_after_popen(
    tmp_path: Path,
    exit_code: int | None,
) -> None:
    _database, repository, run = _state(
        tmp_path,
        code="print('unused')",
    )
    prepared = _prepare(repository, run)
    receipt = repository._claim_execution_launch(
        prepared.request,
        actor="owner",
        runtime_seconds=prepared.reserved_runtime_seconds,
        retry=prepared.retry,
    )
    result = ExecutionResult(
        request_id=prepared.request.request_id,
        outcome=ExecutionOutcome.CANCELLED,
        summary="Cancelado.",
        exit_code=exit_code,
        error_code="cancelled",
    )

    repository._record_execution_result(
        prepared.request,
        result,
        actor="owner",
        receipt=receipt,
    )

    assert repository.execution_results(run.run_id, actor="owner")[0][
        "outcome"
    ] == "cancelled"


def test_execution_observed_audit_excludes_caller_controlled_secrets(
    tmp_path: Path,
) -> None:
    database, repository, run = _state(
        tmp_path,
        code="print('unused')",
    )
    prepared = _prepare(repository, run)
    receipt = repository._claim_execution_launch(
        prepared.request,
        actor="owner",
        runtime_seconds=prepared.reserved_runtime_seconds,
        retry=prepared.retry,
    )
    distinctive = "AUDIT_SECRET_DO_NOT_COPY_7B1"
    result = ExecutionResult(
        request_id=prepared.request.request_id,
        outcome=ExecutionOutcome.SUCCEEDED,
        summary=distinctive,
        exit_code=0,
        stdout=distinctive,
    )
    repository._record_execution_result(
        prepared.request,
        result,
        actor="owner",
        receipt=receipt,
    )

    with database.connect() as connection:
        event = connection.execute(
            """
            SELECT summary, payload_json
            FROM assistant_autonomy_events
            WHERE event_type = 'execution_observed'
            """
        ).fetchone()

    assert event is not None
    assert event["summary"] == "Ejecución observada: proceso completado."
    assert distinctive not in event["summary"]
    assert distinctive not in event["payload_json"]
    payload = json.loads(event["payload_json"])
    assert "stdout" not in payload
    assert "stderr" not in payload
    raw_receipt = receipt.secret.hex()
    assert raw_receipt not in event["summary"]
    assert raw_receipt not in event["payload_json"]
    assert raw_receipt not in str(
        repository.execution_results(run.run_id, actor="owner")
    )
