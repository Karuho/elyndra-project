from __future__ import annotations

import re
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from elyndra.autonomy import (
    AutonomyRepository,
    AutonomyRun,
    AutonomyRunStatus,
    Capability,
    CapabilityGrant,
    CommandSpec,
    RunPlan,
    RunStep,
    WorkspaceScope,
)
from elyndra.cognitive_loop import LocalCognitiveActionLoop
from elyndra.db import Database


def _run(tmp_path: Path, label: str, *, actor: str = "owner") -> AutonomyRun:
    root = tmp_path / label
    root.mkdir(parents=True, exist_ok=True)
    executable = str(Path(sys.executable).resolve(strict=True))
    now = datetime.now(UTC)
    return AutonomyRun(
        actor=actor,
        workspace=WorkspaceScope.from_root(root),
        grant=CapabilityGrant(
            capabilities=frozenset({Capability.PROCESS_EXEC}),
            issued_at=now,
            expires_at=now + timedelta(hours=1),
            max_steps=4,
            max_retries=2,
            max_commands=4,
            max_runtime_seconds=12,
            allowed_executables=(executable,),
        ),
        plan=RunPlan(
            objective="Inspect exact predecessor",
            steps=(
                RunStep(
                    step_id="run",
                    capability=Capability.PROCESS_EXEC,
                    action="run",
                    target=".",
                    command=CommandSpec(
                        executable=executable,
                        argv=(executable, "-c", "print('ok')"),
                        cwd=".",
                        timeout_seconds=3,
                    ),
                ),
            ),
        ),
    )


def _loop(database: Database) -> LocalCognitiveActionLoop:
    return LocalCognitiveActionLoop(
        database,
        language_engine=object(),  # type: ignore[arg-type]
        memory=object(),  # type: ignore[arg-type]
    )


def _waiting_lineage(
    connection: sqlite3.Connection, run_db_id: int, label: str
) -> tuple[int, int]:
    connection.execute(
        """INSERT INTO assistant_cognitive_cycles(
               public_id, autonomy_run_id, actor, status, max_advances,
               max_model_calls, max_replans, max_actions, created_at, updated_at)
           VALUES (?, ?, 'owner', 'waiting_owner', 12, 8, 2, 4, 'c', 'u')""",
        (f"cycle-{label}", run_db_id),
    )
    cycle_db_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    connection.execute(
        """INSERT INTO assistant_cognitive_turns(
               public_id, cycle_id, sequence, kind, state, decision, step_id,
               created_at, completed_at)
           VALUES (?, ?, 1, 'reason', 'completed', 'propose_replan', 'run', 'c', 'd')""",
        (f"turn-{label}", cycle_db_id),
    )
    turn_db_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    connection.execute(
        """INSERT INTO assistant_cognitive_owner_waits(
               public_id, cycle_id, sequence, source_turn_id, reason, state, created_at)
           VALUES (?, ?, 1, ?, 'replan_requested', 'pending', 'w')""",
        (f"wait-{label}", cycle_db_id, turn_db_id),
    )
    return cycle_db_id, turn_db_id


def _proposed_handoff(
    connection: sqlite3.Connection, cycle_db_id: int, wait_db_id: int, label: str
) -> None:
    connection.execute(
        """INSERT INTO assistant_cognitive_successor_handoffs(
               public_id, request_key, wait_id, predecessor_cycle_id,
               predecessor_state_sha256, objective, workspace_root, plan_json,
               grant_spec_json, candidate_sha256, status, created_at)
           VALUES (?, ?, ?, ?, ?, 'Inspect exact predecessor', '/workspace', '{}',
                   '{}', ?, 'proposed', 'now')""",
        (f"handoff-{label}", f"request-{label}", wait_db_id, cycle_db_id, "a" * 64, "b" * 64),
    )


def test_connection_local_run_insert_and_public_regression(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    local = _run(tmp_path, "local")
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = repository._insert_run_connection(connection, local)
        assert row["grant_json"] and row["plan_json"]
        connection.rollback()
    assert repository.get(local.run_id) is None

    public = _run(tmp_path, "public")
    item = repository.create(public)
    assert item["status"] == "planned"
    assert item["events"][0]["event_type"] == "run_created"
    with database.connect() as connection:
        run_db_id = connection.execute(
            "SELECT id FROM assistant_autonomy_runs WHERE public_id=?", (public.run_id,)
        ).fetchone()[0]
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_cycles WHERE autonomy_run_id=?",
            (run_db_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_human_gates WHERE run_id=?",
            (run_db_id,),
        ).fetchone()[0] == 0


def test_connection_local_transition_commit_and_validation(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    run = _run(tmp_path, "transition")
    repository.create(run)
    repository.transition(run.run_id, "running", actor="owner", summary="start")
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository._transition_connection(
            connection,
            run.run_id,
            AutonomyRunStatus.CANCELLED,
            actor="owner",
            summary="handoff cancellation",
            now="terminal",
        )
        connection.commit()
    item = repository.get(run.run_id)
    assert item is not None and item["status"] == "cancelled"
    assert item["finished_at"] == "terminal"
    assert item["events"][-1]["event_type"] == "run_cancelled"
    with database.connect() as connection:
        with pytest.raises(PermissionError):
            repository._transition_connection(
                connection,
                run.run_id,
                AutonomyRunStatus.COMPLETED,
                actor="other",
                summary="denied",
            )
        with pytest.raises(ValueError):
            repository._transition_connection(
                connection,
                run.run_id,
                AutonomyRunStatus.COMPLETED,
                actor="owner",
                summary="denied",
            )


def test_cognitive_handoff_primitives_rollback_and_commit(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    loop = _loop(database)
    predecessor = _run(tmp_path, "predecessor")
    repository.create(predecessor)
    repository.transition(predecessor.run_id, "running", actor="owner", summary="start")
    successor = _run(tmp_path, "successor")
    with database.connect() as connection:
        run_db_id = int(
            connection.execute(
                "SELECT id FROM assistant_autonomy_runs WHERE public_id=?",
                (predecessor.run_id,),
            ).fetchone()[0]
        )
        cycle_db_id, _ = _waiting_lineage(connection, run_db_id, "handoff")
        wait_db_id = int(
            connection.execute(
                "SELECT id FROM assistant_cognitive_owner_waits WHERE public_id='wait-handoff'"
            ).fetchone()[0]
        )
        _proposed_handoff(connection, cycle_db_id, wait_db_id, "handoff")

    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository._insert_run_connection(connection, successor)
        loop._accept_handoff_connection(
            connection, "handoff-handoff", successor.run_id, actor="owner", now="accepted"
        )
        loop._resolve_successor_wait_connection(
            connection, "cycle-handoff", "wait-handoff", actor="owner", now="accepted"
        )
        loop._terminalize_cycle_connection(
            connection, "cycle-handoff", actor="owner", now="accepted"
        )
        repository._transition_connection(
            connection,
            predecessor.run_id,
            AutonomyRunStatus.CANCELLED,
            actor="owner",
            summary="superseded",
            now="accepted",
        )
        connection.rollback()
    assert repository.get(successor.run_id) is None
    with database.connect() as connection:
        assert connection.execute(
            "SELECT status FROM assistant_cognitive_cycles WHERE public_id='cycle-handoff'"
        ).fetchone()[0] == "waiting_owner"
        assert connection.execute(
            "SELECT state FROM assistant_cognitive_owner_waits WHERE public_id='wait-handoff'"
        ).fetchone()[0] == "pending"

    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        repository._insert_run_connection(connection, successor)
        loop._accept_handoff_connection(
            connection, "handoff-handoff", successor.run_id, actor="owner", now="accepted"
        )
        loop._resolve_successor_wait_connection(
            connection, "cycle-handoff", "wait-handoff", actor="owner", now="accepted"
        )
        loop._terminalize_cycle_connection(
            connection, "cycle-handoff", actor="owner", now="accepted"
        )
        connection.commit()
    assert repository.get(successor.run_id) is not None
    with database.connect() as connection:
        cycle = connection.execute(
            "SELECT status, finished_at FROM assistant_cognitive_cycles "
            "WHERE public_id='cycle-handoff'"
        ).fetchone()
        assert tuple(cycle) == ("stopped", "accepted")
        assert connection.execute(
            "SELECT summary_code FROM assistant_cognitive_cycle_events "
            "WHERE cycle_id=? ORDER BY id DESC LIMIT 1",
            (cycle_db_id,),
        ).fetchone()[0] == "cycle_superseded"


def test_private_cognitive_primitive_wrong_lineage_and_state_denied(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    run = _run(tmp_path, "denied")
    repository.create(run)
    repository.transition(run.run_id, "running", actor="owner", summary="start")
    loop = _loop(database)
    with database.connect() as connection:
        run_db_id = connection.execute(
            "SELECT id FROM assistant_autonomy_runs WHERE public_id=?", (run.run_id,)
        ).fetchone()[0]
        cycle_db_id, _ = _waiting_lineage(connection, int(run_db_id), "denied")
        wait_db_id = connection.execute(
            "SELECT id FROM assistant_cognitive_owner_waits WHERE public_id='wait-denied'"
        ).fetchone()[0]
        _proposed_handoff(connection, cycle_db_id, int(wait_db_id), "denied")
        with pytest.raises(PermissionError):
            loop._terminalize_cycle_connection(
                connection, "cycle-denied", actor="other"
            )
        with pytest.raises(PermissionError):
            loop._resolve_successor_wait_connection(
                connection, "wrong-cycle", "wait-denied", actor="owner"
            )
        connection.execute(
            "UPDATE assistant_cognitive_successor_handoffs SET status='rejected', "
            "resolved_at='now', resolved_by='owner' WHERE public_id='handoff-denied'"
        )
        with pytest.raises(PermissionError):
            loop._accept_handoff_connection(
                connection, "handoff-denied", run.run_id, actor="owner"
            )


def test_predecessor_fingerprint_is_exact_deterministic_and_mutation_sensitive(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    run = _run(tmp_path, "fingerprint")
    repository.create(run)
    repository.transition(run.run_id, "running", actor="owner", summary="start")
    with database.connect() as connection:
        run_db_id = int(connection.execute(
            "SELECT id FROM assistant_autonomy_runs WHERE public_id=?", (run.run_id,)
        ).fetchone()[0])
        cycle_db_id, _ = _waiting_lineage(connection, run_db_id, "fingerprint")

        def fingerprint() -> str:
            return repository._predecessor_state_sha256(
                connection,
                run.run_id,
                "cycle-fingerprint",
                "wait-fingerprint",
                actor="owner",
            )

        previous = fingerprint()
        assert previous == fingerprint()
        assert re.fullmatch(r"[0-9a-f]{64}", previous)

        def changed() -> None:
            nonlocal previous
            current = fingerprint()
            assert current != previous
            previous = current

        request_id = "fingerprint-request"
        digest = "c" * 64
        connection.execute(
            """INSERT INTO assistant_autonomy_execution_reservations(
                   request_id, request_sha256, run_id, sequence, step_id, capability,
                   runtime_seconds, is_retry, created_at, command_sha256)
               VALUES (?, ?, ?, 1, 'run', 'process.exec', 1, 0, 'r', ?)""",
            (request_id, digest, run_db_id, digest),
        )
        changed()
        connection.execute(
            """INSERT INTO assistant_autonomy_execution_launches(
                   request_id, request_sha256, run_id, command_sha256, created_at,
                   observation_receipt_sha256) VALUES (?, ?, ?, ?, 'l', ?)""",
            (request_id, digest, run_db_id, digest, digest),
        )
        changed()
        connection.execute(
            """INSERT INTO assistant_autonomy_execution_results(
                   request_id, request_sha256, run_id, sequence, step_id, command_sha256,
                   runtime_seconds, is_retry, outcome, exit_code, duration_ms, summary,
                   error_code, stdout, stderr, stdout_sha256, stderr_sha256, timed_out,
                   stdout_truncated, stderr_truncated, created_at)
               VALUES (?, ?, ?, 1, 'run', ?, 1, 0, 'failed', 1, 2, 'safe', 'exit',
                       '', '', ?, ?, 0, 0, 0, 'result')""",
            (request_id, digest, run_db_id, digest, digest, digest),
        )
        changed()
        connection.execute(
            """INSERT INTO assistant_autonomy_events(
                   run_id, sequence, event_type, from_status, to_status, step_id,
                   summary, payload_json, created_at)
               VALUES (?, 3, 'fingerprint_event', 'running', 'running', '',
                       'safe', '{}', 'event')""",
            (run_db_id,),
        )
        changed()
        connection.execute(
            """INSERT INTO assistant_autonomy_human_gates(
                   public_id, run_id, kind, status, reason, created_at)
               VALUES ('fingerprint-gate', ?, 'retry_review', 'pending', 'review', 'g')""",
            (run_db_id,),
        )
        changed()
        connection.execute(
            """UPDATE assistant_autonomy_human_gates SET status='approved',
                   resolved_at='ga', resolved_by='owner' WHERE public_id='fingerprint-gate'"""
        )
        changed()
        connection.execute(
            """INSERT INTO assistant_autonomy_retry_reviews(
                   public_id, run_id, step_id, source_request_id, gate_id, created_at)
               VALUES ('fingerprint-review', ?, 'run', ?, 'fingerprint-gate', 'rr')""",
            (run_db_id, request_id),
        )
        changed()
        retry_request = "fingerprint-retry"
        digest = "d" * 64
        connection.execute(
            """INSERT INTO assistant_autonomy_execution_reservations(
                   request_id, request_sha256, run_id, sequence, step_id, capability,
                   runtime_seconds, is_retry, created_at, command_sha256)
               VALUES (?, ?, ?, 2, 'run', 'process.exec', 1, 1, 'retry', ?)""",
            (retry_request, digest, run_db_id, digest),
        )
        changed()
        review_db_id = connection.execute(
            "SELECT id FROM assistant_autonomy_retry_reviews "
            "WHERE public_id='fingerprint-review'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO assistant_autonomy_retry_consumptions(
                   retry_review_id, retry_request_id, created_at) VALUES (?, ?, 'rc')""",
            (review_db_id, retry_request),
        )
        changed()
        connection.execute(
            """INSERT INTO assistant_cognitive_turns(
                   public_id, cycle_id, sequence, kind, state, decision, step_id,
                   created_at, completed_at)
               VALUES ('fingerprint-high-water-turn', ?, 2, 'reason', 'completed',
                       'stop', 'run', 'turn', 'turn')""",
            (cycle_db_id,),
        )
        changed()
        connection.execute(
            """INSERT INTO assistant_cognitive_cycle_events(
                   public_id, cycle_id, sequence, event_type, from_status, to_status,
                   summary_code, payload_json, created_at)
               VALUES ('fingerprint-cycle-event', ?, 1, 'owner_waiting',
                       'evaluation_reserved', 'waiting_owner', 'wait', '{}', 'ce')""",
            (cycle_db_id,),
        )
        changed()
        connection.execute(
            """UPDATE assistant_cognitive_owner_waits SET state='resolved',
                   resolution='replan_declined', resolved_at='wr', resolved_by='owner'
               WHERE public_id='wait-fingerprint'"""
        )
        changed()
        connection.execute(
            """UPDATE assistant_cognitive_cycles SET status='stopped', updated_at='cs',
                   finished_at='cs' WHERE public_id='cycle-fingerprint'"""
        )
        changed()
        repository._transition_connection(
            connection,
            run.run_id,
            AutonomyRunStatus.CANCELLED,
            actor="owner",
            summary="fingerprint status",
            now="cancelled",
        )
        changed()
        with pytest.raises(PermissionError):
            repository._predecessor_state_sha256(
                connection, run.run_id, "wrong", "wait-fingerprint", actor="owner"
            )
        with pytest.raises(PermissionError):
            repository._predecessor_state_sha256(
                connection,
                run.run_id,
                "cycle-fingerprint",
                "wait-fingerprint",
                actor="other",
            )


def test_predecessor_fingerprint_ignores_unrelated_run(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    run = _run(tmp_path, "isolated")
    other = _run(tmp_path, "unrelated")
    repository.create(run)
    repository.create(other)
    with database.connect() as connection:
        run_db_id = connection.execute(
            "SELECT id FROM assistant_autonomy_runs WHERE public_id=?", (run.run_id,)
        ).fetchone()[0]
        _waiting_lineage(connection, int(run_db_id), "isolated")
        before = repository._predecessor_state_sha256(
            connection, run.run_id, "cycle-isolated", "wait-isolated", actor="owner"
        )
        other_db_id = connection.execute(
            "SELECT id FROM assistant_autonomy_runs WHERE public_id=?", (other.run_id,)
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO assistant_autonomy_events(
                   run_id, sequence, event_type, to_status, summary, payload_json, created_at)
               VALUES (?, 2, 'unrelated', 'planned', 'safe', '{}', 'now')""",
            (other_db_id,),
        )
        assert before == repository._predecessor_state_sha256(
            connection, run.run_id, "cycle-isolated", "wait-isolated", actor="owner"
        )
