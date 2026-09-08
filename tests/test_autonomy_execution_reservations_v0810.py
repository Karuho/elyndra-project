from __future__ import annotations

import shutil
import sqlite3
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from elyndra.autonomy import (
    AutonomyExecutionBinding,
    AutonomyRepository,
    AutonomyRun,
    AutonomyRunStatus,
    Capability,
    CapabilityGrant,
    CommandSnapshot,
    CommandSpec,
    ExecutionDenied,
    ExecutionRequest,
    HumanGateStatus,
    RunPlan,
    RunStep,
    WorkspaceScope,
)
from elyndra.db import Database


def _state(
    tmp_path: Path,
    *,
    max_commands: int = 5,
    max_retries: int = 2,
    max_runtime_seconds: int = 60,
    requires_human_gate: bool = False,
) -> tuple[Database, AutonomyRepository, AutonomyRun]:
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)

    src = root / "src"
    src.mkdir(exist_ok=True)

    main = src / "main.py"
    main.write_text("print('ok')\n", encoding="utf-8")

    now = datetime.now(UTC)

    grant = CapabilityGrant(
        capabilities=frozenset({Capability.WORKSPACE_READ}),
        issued_at=now,
        expires_at=now + timedelta(hours=1),
        max_steps=5,
        max_commands=max_commands,
        max_retries=max_retries,
        max_runtime_seconds=max_runtime_seconds,
    )

    plan = RunPlan(
        objective="Inspect source with durable execution budget",
        steps=(
            RunStep(
                step_id="inspect",
                capability=Capability.WORKSPACE_READ,
                action="inspect source",
                target="src/main.py",
                requires_human_gate=requires_human_gate,
            ),
        ),
    )

    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(root),
        grant=grant,
        plan=plan,
    )

    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()

    repository = AutonomyRepository(database)
    repository.create(run)

    return database, repository, run


def _start(
    repository: AutonomyRepository,
    run: AutonomyRun,
) -> None:
    repository.transition(
        run.run_id,
        AutonomyRunStatus.RUNNING,
        actor="owner",
        summary="Run iniciado.",
    )


def _request(
    run: AutonomyRun,
    *,
    request_id: str | None = None,
) -> ExecutionRequest:
    step = run.plan.steps[0]

    return ExecutionRequest(
        run_id=run.run_id,
        step_id=step.step_id,
        capability=step.capability,
        action=step.action,
        target=step.target,
        requires_human_gate=step.requires_human_gate,
        request_id=request_id or uuid.uuid4().hex,
    )


def test_schema_54_reservation_ledger_is_vault_scoped_and_idempotent(
    tmp_path: Path,
) -> None:
    root = Database(tmp_path / "root.sqlite3", role="root")
    vault = Database(tmp_path / "vault.sqlite3", role="vault")

    root.migrate()
    vault.migrate()
    root.migrate()
    vault.migrate()

    with root.connect() as connection:
        assert connection.execute(
            """
            SELECT value FROM schema_meta
            WHERE key='schema_version'
            """
        ).fetchone()[0] == "54"

        assert connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE
                type='table'
                AND name='assistant_autonomy_execution_reservations'
            """
        ).fetchone() is None

    with vault.connect() as connection:
        assert connection.execute(
            """
            SELECT value FROM schema_meta
            WHERE key='schema_version'
            """
        ).fetchone()[0] == "54"

        assert connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE
                type='table'
                AND name='assistant_autonomy_execution_reservations'
            """
        ).fetchone()


def test_schema_51_upgrade_preserves_run_and_creates_ledger(
    tmp_path: Path,
) -> None:
    database, _repository, run = _state(tmp_path)

    with database.connect() as connection:
        connection.execute(
            "DROP TABLE assistant_autonomy_execution_reservations"
        )
        connection.execute(
            """
            UPDATE schema_meta
            SET value='51'
            WHERE key='schema_version'
            """
        )
        connection.execute(
            """
            INSERT INTO memories(
                kind,
                content,
                source,
                created_at,
                updated_at
            ) VALUES(
                'fact',
                'preserve-schema-51-autonomy',
                'owner',
                '2026-09-04',
                '2026-09-04'
            )
            """
        )

    database.migrate()

    with database.connect() as connection:
        assert connection.execute(
            """
            SELECT value FROM schema_meta
            WHERE key='schema_version'
            """
        ).fetchone()[0] == "54"

        assert connection.execute(
            """
            SELECT public_id
            FROM assistant_autonomy_runs
            WHERE public_id = ?
            """,
            (run.run_id,),
        ).fetchone()[0] == run.run_id

        assert connection.execute(
            """
            SELECT content
            FROM memories
            WHERE content='preserve-schema-51-autonomy'
            """
        ).fetchone()[0] == "preserve-schema-51-autonomy"

        assert connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE
                type='table'
                AND name='assistant_autonomy_execution_reservations'
            """
        ).fetchone()


def test_reservation_rows_are_append_only(tmp_path: Path) -> None:
    database, repository, run = _state(tmp_path)
    _start(repository, run)

    repository.reserve_execution(
        _request(run),
        actor="owner",
        runtime_seconds=3,
    )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="autonomy_execution_reservations_append_only",
    ), database.connect() as connection:
        connection.execute(
            """
            UPDATE assistant_autonomy_execution_reservations
            SET runtime_seconds = 0
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="autonomy_execution_reservations_append_only",
    ), database.connect() as connection:
        connection.execute(
            "DELETE FROM assistant_autonomy_execution_reservations"
        )


def test_bound_contract_persists_and_restores_budget(
    tmp_path: Path,
) -> None:
    _database, repository, run = _state(tmp_path)
    _start(repository, run)

    first = AutonomyExecutionBinding(repository).bind(
        run.run_id,
        actor="owner",
    )

    prepared = first.prepare(
        "inspect",
        runtime_seconds=5,
    )

    assert prepared.budget.commands_reserved == 1
    assert prepared.budget.runtime_seconds_reserved == 5

    rebound = AutonomyExecutionBinding(repository).bind(
        run.run_id,
        actor="owner",
    )

    snapshot = rebound.budget.snapshot()
    assert snapshot.commands_reserved == 1
    assert snapshot.retries_reserved == 0
    assert snapshot.runtime_seconds_reserved == 5


def test_command_budget_survives_rebind(tmp_path: Path) -> None:
    _database, repository, run = _state(
        tmp_path,
        max_commands=1,
    )
    _start(repository, run)

    first = AutonomyExecutionBinding(repository).bind(
        run.run_id,
        actor="owner",
    )
    first.prepare("inspect")

    rebound = AutonomyExecutionBinding(repository).bind(
        run.run_id,
        actor="owner",
    )

    with pytest.raises(ExecutionDenied, match="max_commands"):
        rebound.prepare("inspect")

    assert repository.execution_budget(
        run.run_id,
        actor="owner",
    ).snapshot().commands_reserved == 1


def test_retry_budget_survives_rebind(tmp_path: Path) -> None:
    _database, repository, run = _state(
        tmp_path,
        max_commands=5,
        max_retries=1,
    )
    _start(repository, run)

    first = AutonomyExecutionBinding(repository).bind(
        run.run_id,
        actor="owner",
    )
    first.prepare("inspect", retry=True)

    rebound = AutonomyExecutionBinding(repository).bind(
        run.run_id,
        actor="owner",
    )

    with pytest.raises(ExecutionDenied, match="max_retries"):
        rebound.prepare("inspect", retry=True)

    snapshot = repository.execution_budget(
        run.run_id,
        actor="owner",
    ).snapshot()

    assert snapshot.commands_reserved == 1
    assert snapshot.retries_reserved == 1


def test_runtime_budget_survives_rebind(tmp_path: Path) -> None:
    _database, repository, run = _state(
        tmp_path,
        max_commands=5,
        max_runtime_seconds=5,
    )
    _start(repository, run)

    first = AutonomyExecutionBinding(repository).bind(
        run.run_id,
        actor="owner",
    )
    first.prepare("inspect", runtime_seconds=4)

    rebound = AutonomyExecutionBinding(repository).bind(
        run.run_id,
        actor="owner",
    )

    with pytest.raises(
        ExecutionDenied,
        match="max_runtime_seconds",
    ):
        rebound.prepare("inspect", runtime_seconds=2)

    snapshot = repository.execution_budget(
        run.run_id,
        actor="owner",
    ).snapshot()

    assert snapshot.commands_reserved == 1
    assert snapshot.runtime_seconds_reserved == 4


def test_duplicate_request_id_is_idempotent(tmp_path: Path) -> None:
    database, repository, run = _state(tmp_path)
    _start(repository, run)

    request = _request(
        run,
        request_id="request-idempotent",
    )

    first = repository.reserve_execution(
        request,
        actor="owner",
        runtime_seconds=3,
        retry=True,
    )

    second = repository.reserve_execution(
        request,
        actor="owner",
        runtime_seconds=3,
        retry=True,
    )

    assert first.commands_reserved == 1
    assert second.commands_reserved == 1
    assert second.retries_reserved == 1
    assert second.runtime_seconds_reserved == 3

    with database.connect() as connection:
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM assistant_autonomy_execution_reservations
            WHERE request_id='request-idempotent'
            """
        ).fetchone()[0] == 1


def test_duplicate_request_id_with_different_reservation_fails_closed(
    tmp_path: Path,
) -> None:
    _database, repository, run = _state(tmp_path)
    _start(repository, run)

    request = _request(
        run,
        request_id="request-conflict",
    )

    repository.reserve_execution(
        request,
        actor="owner",
        runtime_seconds=1,
    )

    with pytest.raises(
        PermissionError,
        match="request_id reutilizado",
    ):
        repository.reserve_execution(
            request,
            actor="owner",
            runtime_seconds=2,
        )

    snapshot = repository.execution_budget(
        run.run_id,
        actor="owner",
    ).snapshot()

    assert snapshot.commands_reserved == 1
    assert snapshot.runtime_seconds_reserved == 1


def test_wrong_actor_cannot_reserve_execution(tmp_path: Path) -> None:
    _database, repository, run = _state(tmp_path)
    _start(repository, run)

    with pytest.raises(PermissionError, match="otro propietario"):
        repository.reserve_execution(
            _request(run),
            actor="intruder",
        )

    assert repository.execution_budget(
        run.run_id,
        actor="owner",
    ).snapshot().commands_reserved == 0


def test_non_running_run_cannot_reserve_execution(
    tmp_path: Path,
) -> None:
    _database, repository, run = _state(tmp_path)

    with pytest.raises(PermissionError, match="running"):
        repository.reserve_execution(
            _request(run),
            actor="owner",
        )

    assert repository.execution_budget(
        run.run_id,
        actor="owner",
    ).snapshot().commands_reserved == 0

def test_direct_repository_reservation_requires_approved_human_gate(
    tmp_path: Path,
) -> None:
    _database, repository, run = _state(
        tmp_path,
        requires_human_gate=True,
    )
    _start(repository, run)

    request = _request(run)

    with pytest.raises(
        PermissionError,
        match="requiere HumanGate aprobado",
    ):
        repository.reserve_execution(
            request,
            actor="owner",
        )

    assert repository.execution_budget(
        run.run_id,
        actor="owner",
    ).snapshot().commands_reserved == 0

    waiting = repository.request_human_gate(
        run.run_id,
        actor="owner",
        reason="Aprobar ejecución del step inspect.",
        step_id="inspect",
    )

    gate_id = waiting["human_gates"][0]["public_id"]

    repository.resolve_human_gate(
        gate_id,
        actor="owner",
        decision=HumanGateStatus.APPROVED,
    )

    snapshot = repository.reserve_execution(
        request,
        actor="owner",
    )

    assert snapshot.commands_reserved == 1


def test_concurrent_reservations_are_serialized_by_sqlite(
    tmp_path: Path,
) -> None:
    _database, repository, run = _state(
        tmp_path,
        max_commands=1,
    )
    _start(repository, run)

    first = AutonomyExecutionBinding(repository).bind(
        run.run_id,
        actor="owner",
    )
    second = AutonomyExecutionBinding(repository).bind(
        run.run_id,
        actor="owner",
    )

    barrier = threading.Barrier(2)

    def prepare(contract: object) -> str:
        barrier.wait()
        try:
            contract.prepare("inspect")  # type: ignore[attr-defined]
        except ExecutionDenied:
            return "denied"
        return "reserved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(prepare, first),
            executor.submit(prepare, second),
        ]
        results = sorted(
            future.result(timeout=10)
            for future in futures
        )

    assert results == ["denied", "reserved"]

    snapshot = repository.execution_budget(
        run.run_id,
        actor="owner",
    ).snapshot()

    assert snapshot.commands_reserved == 1

def _process_state(
    tmp_path: Path,
    *,
    executable: str | None = None,
    timeout_seconds: int = 7,
) -> tuple[Database, AutonomyRepository, AutonomyRun]:
    root = tmp_path / "process-project"
    root.mkdir(exist_ok=True)

    resolved_executable = (
        executable
        or str(Path(sys.executable).resolve(strict=True))
    )

    now = datetime.now(UTC)

    command = CommandSpec(
        executable=resolved_executable,
        argv=(resolved_executable, "--version"),
        cwd=".",
        timeout_seconds=timeout_seconds,
    )

    grant = CapabilityGrant(
        capabilities=frozenset({Capability.PROCESS_EXEC}),
        issued_at=now,
        expires_at=now + timedelta(hours=1),
        max_steps=2,
        max_commands=3,
        max_retries=1,
        max_runtime_seconds=60,
        allowed_executables=(resolved_executable,),
    )

    plan = RunPlan(
        objective="Prepare one durable process command",
        steps=(
            RunStep(
                step_id="run",
                capability=Capability.PROCESS_EXEC,
                action="run version check",
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
        tmp_path / "process-vault.sqlite3",
        role="vault",
    )
    database.migrate()

    repository = AutonomyRepository(database)
    repository.create(run)

    return database, repository, run


def _process_request(
    run: AutonomyRun,
    *,
    command_sha256: str,
    request_id: str | None = None,
) -> ExecutionRequest:
    step = run.plan.steps[0]

    return ExecutionRequest(
        run_id=run.run_id,
        step_id=step.step_id,
        capability=step.capability,
        action=step.action,
        target=step.target,
        requires_human_gate=step.requires_human_gate,
        command_sha256=command_sha256,
        request_id=request_id or uuid.uuid4().hex,
    )


def test_schema_53_upgrade_adds_command_sha256_without_losing_reservation(
    tmp_path: Path,
) -> None:
    database, repository, run = _state(tmp_path)
    _start(repository, run)

    request = _request(
        run,
        request_id="schema-53-reservation",
    )

    repository.reserve_execution(
        request,
        actor="owner",
        runtime_seconds=3,
    )

    with database.connect() as connection:
        connection.execute(
            """
            ALTER TABLE assistant_autonomy_execution_reservations
            DROP COLUMN command_sha256
            """
        )
        connection.execute(
            """
            UPDATE schema_meta
            SET value='53'
            WHERE key='schema_version'
            """
        )

    database.migrate()

    with database.connect() as connection:
        assert connection.execute(
            """
            SELECT value FROM schema_meta
            WHERE key='schema_version'
            """
        ).fetchone()[0] == "54"

        columns = {
            str(row[1])
            for row in connection.execute(
                """
                PRAGMA table_info(
                    assistant_autonomy_execution_reservations
                )
                """
            )
        }
        assert "command_sha256" in columns

        stored = connection.execute(
            """
            SELECT request_id, command_sha256
            FROM assistant_autonomy_execution_reservations
            WHERE request_id = ?
            """,
            (request.request_id,),
        ).fetchone()

        assert stored is not None
        assert stored["request_id"] == request.request_id
        assert stored["command_sha256"] is None

    replayed = repository.reserve_execution(
        request,
        actor="owner",
        runtime_seconds=3,
    )

    assert replayed.commands_reserved == 1
    assert replayed.runtime_seconds_reserved == 3


def test_bound_process_exec_reserves_exact_command_snapshot(
    tmp_path: Path,
) -> None:
    database, repository, run = _process_state(tmp_path)
    _start(repository, run)

    contract = AutonomyExecutionBinding(repository).bind(
        run.run_id,
        actor="owner",
    )

    prepared = contract.prepare("run")

    assert prepared.command_snapshot is not None
    assert (
        prepared.request.command_sha256
        == prepared.command_snapshot.command_sha256
    )
    assert prepared.resolved_target == str(run.workspace.root)
    assert prepared.budget.commands_reserved == 1
    assert prepared.budget.runtime_seconds_reserved == 7

    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT command_sha256, runtime_seconds
            FROM assistant_autonomy_execution_reservations
            WHERE request_id = ?
            """,
            (prepared.request.request_id,),
        ).fetchone()

    assert row is not None
    assert row["command_sha256"] == prepared.request.command_sha256
    assert row["runtime_seconds"] == 7


def test_direct_process_reservation_rejects_forged_snapshot(
    tmp_path: Path,
) -> None:
    _database, repository, run = _process_state(tmp_path)
    _start(repository, run)

    request = _process_request(
        run,
        command_sha256="0" * 64,
    )

    with pytest.raises(
        PermissionError,
        match="CommandSnapshot actual",
    ):
        repository.reserve_execution(
            request,
            actor="owner",
            runtime_seconds=7,
        )

    assert repository.execution_budget(
        run.run_id,
        actor="owner",
    ).snapshot().commands_reserved == 0


def test_direct_process_reservation_cannot_underreserve_timeout(
    tmp_path: Path,
) -> None:
    _database, repository, run = _process_state(tmp_path)
    _start(repository, run)

    command = run.plan.steps[0].command
    assert command is not None

    snapshot = CommandSnapshot.capture(
        command,
        resolved_cwd=run.workspace.root,
    )

    request = _process_request(
        run,
        command_sha256=snapshot.command_sha256,
    )

    with pytest.raises(
        PermissionError,
        match="timeout exacto",
    ):
        repository.reserve_execution(
            request,
            actor="owner",
            runtime_seconds=1,
        )

    assert repository.execution_budget(
        run.run_id,
        actor="owner",
    ).snapshot().commands_reserved == 0


def test_process_reservation_detects_executable_change_after_snapshot(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "python-copy"
    shutil.copy2(
        Path(sys.executable).resolve(strict=True),
        executable,
    )
    executable.chmod(0o755)

    _database, repository, run = _process_state(
        tmp_path,
        executable=str(executable),
    )
    _start(repository, run)

    command = run.plan.steps[0].command
    assert command is not None

    snapshot = CommandSnapshot.capture(
        command,
        resolved_cwd=run.workspace.root,
    )

    with executable.open("ab") as handle:
        handle.write(b"\x00")

    request = _process_request(
        run,
        command_sha256=snapshot.command_sha256,
    )

    with pytest.raises(
        PermissionError,
        match="CommandSnapshot actual",
    ):
        repository.reserve_execution(
            request,
            actor="owner",
            runtime_seconds=7,
        )

    assert repository.execution_budget(
        run.run_id,
        actor="owner",
    ).snapshot().commands_reserved == 0
