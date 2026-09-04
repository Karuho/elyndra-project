from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import elyndra.autonomy.binding as binding_module
from elyndra.autonomy import (
    AutonomyExecutionBinding,
    AutonomyRepository,
    AutonomyRun,
    AutonomyRunStatus,
    Capability,
    CapabilityGrant,
    ExecutionBindingError,
    ExecutionDenied,
    HumanGateStatus,
    RunPlan,
    RunStep,
    WorkspaceScope,
)
from elyndra.db import Database


def _state(
    tmp_path: Path,
) -> tuple[Database, AutonomyRepository, AutonomyRun]:
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text(
        "print('ok')\n",
        encoding="utf-8",
    )

    now = datetime.now(UTC)

    grant = CapabilityGrant(
        capabilities=frozenset(
            {
                Capability.WORKSPACE_READ,
                Capability.WORKSPACE_WRITE,
            }
        ),
        issued_at=now,
        expires_at=now + timedelta(hours=1),
        max_steps=10,
        max_commands=10,
        max_retries=2,
        max_runtime_seconds=300,
    )

    plan = RunPlan(
        objective="Inspect and prepare one approved change",
        steps=(
            RunStep(
                step_id="inspect",
                capability=Capability.WORKSPACE_READ,
                action="inspect source",
                target="src/main.py",
            ),
            RunStep(
                step_id="write",
                capability=Capability.WORKSPACE_WRITE,
                action="prepare controlled write",
                target="src/new.py",
                requires_human_gate=True,
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


def test_binding_reconstructs_running_run_from_repository(
    tmp_path: Path,
) -> None:
    _database, repository, run = _state(tmp_path)
    _start(repository, run)

    contract = AutonomyExecutionBinding(repository).bind(
        run.run_id,
        actor="owner",
    )

    prepared = contract.prepare("inspect")

    assert contract.run_id == run.run_id
    assert contract.workspace.root == run.workspace.root
    assert contract.plan.objective == run.plan.objective
    assert contract.grant.capabilities == run.grant.capabilities
    assert prepared.request.step_id == "inspect"
    assert prepared.resolved_target == str(
        (run.workspace.root / "src" / "main.py").resolve()
    )


def test_binding_rejects_wrong_actor(tmp_path: Path) -> None:
    _database, repository, run = _state(tmp_path)
    _start(repository, run)

    with pytest.raises(ExecutionBindingError, match="otro propietario"):
        AutonomyExecutionBinding(repository).bind(
            run.run_id,
            actor="intruder",
        )


def test_binding_rejects_non_running_run(tmp_path: Path) -> None:
    _database, repository, run = _state(tmp_path)

    with pytest.raises(ExecutionBindingError, match="running"):
        AutonomyExecutionBinding(repository).bind(
            run.run_id,
            actor="owner",
        )


def test_pending_human_gate_blocks_binding(tmp_path: Path) -> None:
    _database, repository, run = _state(tmp_path)
    _start(repository, run)

    repository.request_human_gate(
        run.run_id,
        actor="owner",
        reason="Aprobación requerida.",
        step_id="write",
    )

    with pytest.raises(ExecutionBindingError, match="running"):
        AutonomyExecutionBinding(repository).bind(
            run.run_id,
            actor="owner",
        )


def test_approved_repository_gate_allows_exact_gated_step(
    tmp_path: Path,
) -> None:
    _database, repository, run = _state(tmp_path)
    _start(repository, run)

    waiting = repository.request_human_gate(
        run.run_id,
        actor="owner",
        reason="Aprobar write.",
        step_id="write",
    )
    gate_id = waiting["human_gates"][0]["public_id"]

    repository.resolve_human_gate(
        gate_id,
        actor="owner",
        decision=HumanGateStatus.APPROVED,
    )

    contract = AutonomyExecutionBinding(repository).bind(
        run.run_id,
        actor="owner",
    )

    prepared = contract.prepare("write")
    assert prepared.request.step_id == "write"


def test_generic_approved_gate_does_not_authorize_a_step(
    tmp_path: Path,
) -> None:
    _database, repository, run = _state(tmp_path)
    _start(repository, run)

    waiting = repository.request_human_gate(
        run.run_id,
        actor="owner",
        reason="Revisión general.",
    )
    gate_id = waiting["human_gates"][0]["public_id"]

    repository.resolve_human_gate(
        gate_id,
        actor="owner",
        decision="approved",
    )

    contract = AutonomyExecutionBinding(repository).bind(
        run.run_id,
        actor="owner",
    )

    with pytest.raises(ExecutionDenied, match="HumanGate"):
        contract.prepare("write")


def test_binding_does_not_accept_external_approved_step_ids(
    tmp_path: Path,
) -> None:
    _database, repository, run = _state(tmp_path)
    _start(repository, run)

    binding = AutonomyExecutionBinding(repository)

    with pytest.raises(TypeError):
        binding.bind(
            run.run_id,
            actor="owner",
            approved_step_ids=frozenset({"write"}),
        )


def test_approved_gate_without_request_audit_fails_closed(
    tmp_path: Path,
) -> None:
    database, repository, run = _state(tmp_path)
    _start(repository, run)

    now = datetime.now(UTC).isoformat()

    with database.connect() as connection:
        run_row = connection.execute(
            """
            SELECT id FROM assistant_autonomy_runs
            WHERE public_id = ?
            """,
            (run.run_id,),
        ).fetchone()
        assert run_row is not None

        connection.execute(
            """
            INSERT INTO assistant_autonomy_human_gates(
                public_id,
                run_id,
                kind,
                status,
                reason,
                created_at,
                resolved_at,
                resolved_by
            ) VALUES (?, ?, 'approval', 'approved', ?, ?, ?, 'owner')
            """,
            (
                uuid.uuid4().hex,
                int(run_row["id"]),
                "Injected orphan approval.",
                now,
                now,
            ),
        )

    with pytest.raises(
        ExecutionBindingError,
        match="sin evento append-only",
    ):
        AutonomyExecutionBinding(repository).bind(
            run.run_id,
            actor="owner",
        )


def test_approved_gate_without_approval_audit_fails_closed(
    tmp_path: Path,
) -> None:
    database, repository, run = _state(tmp_path)
    _start(repository, run)

    now = datetime.now(UTC).isoformat()
    gate_id = uuid.uuid4().hex

    with database.connect() as connection:
        run_row = connection.execute(
            """
            SELECT id FROM assistant_autonomy_runs
            WHERE public_id = ?
            """,
            (run.run_id,),
        ).fetchone()
        assert run_row is not None

        run_db_id = int(run_row["id"])

        sequence = int(
            connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1
                FROM assistant_autonomy_events
                WHERE run_id = ?
                """,
                (run_db_id,),
            ).fetchone()[0]
        )

        connection.execute(
            """
            INSERT INTO assistant_autonomy_events(
                run_id,
                sequence,
                event_type,
                from_status,
                to_status,
                step_id,
                summary,
                payload_json,
                created_at
            ) VALUES(
                ?,
                ?,
                'human_gate_requested',
                'running',
                'waiting_human',
                'write',
                'Synthetic request evidence.',
                ?,
                ?
            )
            """,
            (
                run_db_id,
                sequence,
                json.dumps(
                    {
                        "gate_id": gate_id,
                        "kind": "approval",
                    }
                ),
                now,
            ),
        )

        connection.execute(
            """
            INSERT INTO assistant_autonomy_human_gates(
                public_id,
                run_id,
                kind,
                status,
                reason,
                created_at,
                resolved_at,
                resolved_by
            ) VALUES(
                ?,
                ?,
                'approval',
                'approved',
                'Synthetic approved gate.',
                ?,
                ?,
                'owner'
            )
            """,
            (
                gate_id,
                run_db_id,
                now,
                now,
            ),
        )

    with pytest.raises(
        ExecutionBindingError,
        match="sin evento append-only de aprobación",
    ):
        AutonomyExecutionBinding(repository).bind(
            run.run_id,
            actor="owner",
        )


def test_expired_grant_fails_closed_during_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database, repository, run = _state(tmp_path)
    _start(repository, run)

    future = run.grant.expires_at + timedelta(seconds=1)

    monkeypatch.setattr(
        binding_module,
        "_utcnow",
        lambda: future,
    )

    with pytest.raises(ExecutionBindingError, match="expirado"):
        AutonomyExecutionBinding(repository).bind(
            run.run_id,
            actor="owner",
        )


def test_removed_workspace_fails_closed_during_binding(
    tmp_path: Path,
) -> None:
    _database, repository, run = _state(tmp_path)
    _start(repository, run)

    shutil.rmtree(run.workspace.root)

    with pytest.raises(ExecutionBindingError, match="workspace"):
        AutonomyExecutionBinding(repository).bind(
            run.run_id,
            actor="owner",
        )
