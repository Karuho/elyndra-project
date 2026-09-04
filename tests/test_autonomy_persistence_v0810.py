from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import elyndra.autonomy.repository as autonomy_repository
from elyndra.autonomy import (
    AutonomyRepository,
    AutonomyRun,
    AutonomyRunStatus,
    Capability,
    CapabilityGrant,
    HumanGateStatus,
    RunPlan,
    RunStep,
    WorkspaceScope,
)
from elyndra.db import Database


def _database(path: Path, *, role: str = "vault") -> Database:
    database = Database(path, role=role)
    database.migrate()
    return database


def _run(tmp_path: Path) -> AutonomyRun:
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)

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
        max_steps=5,
        max_retries=1,
        max_commands=10,
        max_runtime_seconds=600,
    )

    plan = RunPlan(
        objective="Inspect project and prepare one controlled change",
        steps=(
            RunStep(
                step_id="inspect",
                capability=Capability.WORKSPACE_READ,
                action="inspect project",
            ),
            RunStep(
                step_id="prepare",
                capability=Capability.WORKSPACE_WRITE,
                action="prepare controlled edit",
                requires_human_gate=True,
            ),
        ),
    )

    return AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(root),
        grant=grant,
        plan=plan,
    )


def test_schema_51_is_vault_scoped_and_idempotent(tmp_path: Path) -> None:
    root = _database(tmp_path / "root.sqlite3", role="root")
    vault = _database(tmp_path / "vault.sqlite3", role="vault")

    root.migrate()
    vault.migrate()

    with root.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "51"

        assert connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='assistant_autonomy_runs'
            """
        ).fetchone() is None

    with vault.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "51"

        for table in (
            "assistant_autonomy_runs",
            "assistant_autonomy_events",
            "assistant_autonomy_human_gates",
        ):
            assert connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name=?
                """,
                (table,),
            ).fetchone()


def test_schema_50_vault_upgrade_preserves_existing_data(tmp_path: Path) -> None:
    database = _database(tmp_path / "vault.sqlite3")

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO memories(
                kind, content, source, created_at, updated_at
            ) VALUES(
                'fact',
                'preserve-schema-50-data',
                'owner',
                '2026-09-03',
                '2026-09-03'
            )
            """
        )

        connection.execute("DROP TABLE assistant_autonomy_human_gates")
        connection.execute("DROP TABLE assistant_autonomy_events")
        connection.execute("DROP TABLE assistant_autonomy_runs")
        connection.execute(
            """
            UPDATE schema_meta
            SET value='50'
            WHERE key='schema_version'
            """
        )

    database.migrate()

    with database.connect() as connection:
        assert connection.execute(
            """
            SELECT content FROM memories
            WHERE content='preserve-schema-50-data'
            """
        ).fetchone()[0] == "preserve-schema-50-data"

        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "51"

        assert connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='assistant_autonomy_runs'
            """
        ).fetchone()


def test_repository_rejects_root_database(tmp_path: Path) -> None:
    database = _database(tmp_path / "root.sqlite3", role="root")

    with pytest.raises(ValueError, match="vault"):
        AutonomyRepository(database)


def test_create_persists_frozen_authority_and_initial_event(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "vault.sqlite3")
    repository = AutonomyRepository(database)
    run = _run(tmp_path)

    item = repository.create(run)

    assert item["public_id"] == run.run_id
    assert item["status"] == "planned"
    assert item["workspace_root"] == str(run.workspace.root)
    assert item["grant"]["capabilities"] == [
        "workspace.read",
        "workspace.write",
    ]
    assert item["plan"]["objective"] == run.plan.objective
    assert item["events"] == [
        {
            "sequence": 1,
            "event_type": "run_created",
            "from_status": None,
            "to_status": "planned",
            "step_id": "",
            "summary": "Run autónomo creado con autoridad congelada.",
            "created_at": run.created_at.isoformat(),
            "payload": {},
        }
    ]


def test_legal_transitions_reach_terminal_state_once(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "vault.sqlite3")
    repository = AutonomyRepository(database)
    run = _run(tmp_path)
    repository.create(run)

    running = repository.transition(
        run.run_id,
        AutonomyRunStatus.RUNNING,
        actor="owner",
        summary="Run iniciado.",
    )
    assert running["status"] == "running"
    assert running["started_at"] is not None

    completed = repository.transition(
        run.run_id,
        AutonomyRunStatus.COMPLETED,
        actor="owner",
        summary="Validación completada.",
    )
    assert completed["status"] == "completed"
    assert completed["finished_at"] is not None
    assert [event["event_type"] for event in completed["events"]] == [
        "run_created",
        "run_started",
        "run_completed",
    ]

    with pytest.raises(ValueError, match="Transición"):
        repository.transition(
            run.run_id,
            AutonomyRunStatus.RUNNING,
            actor="owner",
            summary="No debe reabrirse.",
        )


def test_invalid_transition_is_rejected_without_audit_mutation(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "vault.sqlite3")
    repository = AutonomyRepository(database)
    run = _run(tmp_path)
    repository.create(run)

    with pytest.raises(ValueError, match="planned -> completed"):
        repository.transition(
            run.run_id,
            AutonomyRunStatus.COMPLETED,
            actor="owner",
            summary="No permitido.",
        )

    item = repository.get(run.run_id)
    assert item is not None
    assert item["status"] == "planned"
    assert len(item["events"]) == 1


def test_human_gate_approval_resumes_without_expanding_authority(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "vault.sqlite3")
    repository = AutonomyRepository(database)
    run = _run(tmp_path)
    repository.create(run)

    repository.transition(
        run.run_id,
        AutonomyRunStatus.RUNNING,
        actor="owner",
        summary="Run iniciado.",
    )

    with database.connect() as connection:
        before = connection.execute(
            """
            SELECT grant_json, plan_json
            FROM assistant_autonomy_runs
            WHERE public_id = ?
            """,
            (run.run_id,),
        ).fetchone()

    waiting = repository.request_human_gate(
        run.run_id,
        actor="owner",
        reason="Revisar el cambio antes de continuar.",
        step_id="prepare",
    )

    assert waiting["status"] == "waiting_human"
    assert len(waiting["human_gates"]) == 1
    gate_id = waiting["human_gates"][0]["public_id"]

    resumed = repository.resolve_human_gate(
        gate_id,
        actor="owner",
        decision=HumanGateStatus.APPROVED,
    )

    assert resumed["status"] == "running"
    assert resumed["human_gates"][0]["status"] == "approved"

    with database.connect() as connection:
        after = connection.execute(
            """
            SELECT grant_json, plan_json
            FROM assistant_autonomy_runs
            WHERE public_id = ?
            """,
            (run.run_id,),
        ).fetchone()

    assert tuple(before) == tuple(after)


def test_human_gate_rejection_cancels_run(tmp_path: Path) -> None:
    database = _database(tmp_path / "vault.sqlite3")
    repository = AutonomyRepository(database)
    run = _run(tmp_path)
    repository.create(run)

    repository.transition(
        run.run_id,
        "running",
        actor="owner",
        summary="Run iniciado.",
    )

    waiting = repository.request_human_gate(
        run.run_id,
        actor="owner",
        reason="Revisión requerida.",
    )
    gate_id = waiting["human_gates"][0]["public_id"]

    cancelled = repository.resolve_human_gate(
        gate_id,
        actor="owner",
        decision="rejected",
    )

    assert cancelled["status"] == "cancelled"
    assert cancelled["finished_at"] is not None
    assert cancelled["human_gates"][0]["status"] == "rejected"


def test_expired_persisted_grant_cannot_start_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path / "vault.sqlite3")
    repository = AutonomyRepository(database)
    run = _run(tmp_path)
    repository.create(run)

    future = run.grant.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(
        autonomy_repository,
        "_utcnow",
        lambda: future,
    )

    with pytest.raises(PermissionError, match="CapabilityGrant expirado"):
        repository.transition(
            run.run_id,
            AutonomyRunStatus.RUNNING,
            actor="owner",
            summary="No debe arrancar.",
        )

    item = repository.get(run.run_id)
    assert item is not None
    assert item["status"] == "planned"
    assert [event["event_type"] for event in item["events"]] == [
        "run_created",
    ]


def test_expired_grant_cannot_resume_after_human_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path / "vault.sqlite3")
    repository = AutonomyRepository(database)
    run = _run(tmp_path)
    repository.create(run)

    repository.transition(
        run.run_id,
        AutonomyRunStatus.RUNNING,
        actor="owner",
        summary="Run iniciado.",
    )

    waiting = repository.request_human_gate(
        run.run_id,
        actor="owner",
        reason="Revisión requerida.",
    )
    gate_id = waiting["human_gates"][0]["public_id"]

    future = run.grant.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(
        autonomy_repository,
        "_utcnow",
        lambda: future,
    )

    with pytest.raises(PermissionError, match="CapabilityGrant expirado"):
        repository.resolve_human_gate(
            gate_id,
            actor="owner",
            decision=HumanGateStatus.APPROVED,
        )

    item = repository.get(run.run_id)
    assert item is not None
    assert item["status"] == "waiting_human"
    assert item["human_gates"][0]["status"] == "pending"
    assert item["events"][-1]["event_type"] == "human_gate_requested"


def test_autonomy_events_are_append_only_in_sqlite(tmp_path: Path) -> None:
    database = _database(tmp_path / "vault.sqlite3")
    repository = AutonomyRepository(database)
    run = _run(tmp_path)
    repository.create(run)

    with pytest.raises(
        sqlite3.IntegrityError,
        match="autonomy_events_append_only",
    ), database.connect() as connection:
        connection.execute(
            """
                UPDATE assistant_autonomy_events
                SET summary='tampered'
                WHERE sequence=1
                """
        )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="autonomy_events_append_only",
    ), database.connect() as connection:
        connection.execute(
            "DELETE FROM assistant_autonomy_events WHERE sequence=1"
        )


def test_run_authority_snapshot_is_sql_immutable(tmp_path: Path) -> None:
    database = _database(tmp_path / "vault.sqlite3")
    repository = AutonomyRepository(database)
    run = _run(tmp_path)
    repository.create(run)

    with pytest.raises(
        sqlite3.IntegrityError,
        match="autonomy_run_authority_immutable",
    ), database.connect() as connection:
        connection.execute(
            """
                UPDATE assistant_autonomy_runs
                SET grant_json = ?
                WHERE public_id = ?
                """,
            (
                json.dumps({"capabilities": ["git.push"]}),
                run.run_id,
            ),
        )


def test_transition_and_event_are_one_sqlite_transaction(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "vault.sqlite3")
    repository = AutonomyRepository(database)
    run = _run(tmp_path)
    repository.create(run)

    repository.transition(
        run.run_id,
        "running",
        actor="owner",
        summary="Run iniciado.",
    )

    with database.connect() as connection:
        connection.executescript(
            """
            CREATE TRIGGER abort_completed_autonomy_event
            BEFORE INSERT ON assistant_autonomy_events
            WHEN NEW.event_type = 'run_completed'
            BEGIN
                SELECT RAISE(ABORT, 'forced_event_failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced_event_failure"):
        repository.transition(
            run.run_id,
            "completed",
            actor="owner",
            summary="Debe hacer rollback.",
        )

    item = repository.get(run.run_id)
    assert item is not None
    assert item["status"] == "running"
    assert [event["event_type"] for event in item["events"]] == [
        "run_created",
        "run_started",
    ]
