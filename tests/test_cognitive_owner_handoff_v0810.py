from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from elyndra.db import Database


def _schema58(tmp_path: Path) -> Database:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        connection.executescript(
            """
            DROP TABLE assistant_cognitive_successor_handoffs;
            DROP TABLE assistant_cognitive_owner_waits;
            UPDATE schema_meta SET value='58' WHERE key='schema_version';
            """
        )
        turn_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='assistant_cognitive_turns'"
            ).fetchone()[0]
        )
        assert "'limit_exhausted'" in turn_sql
        historical_sql = turn_sql.replace(
            ", 'limit_exhausted'\n                ))",
            "\n                ))",
        )
        assert "'limit_exhausted'" not in historical_sql
        schema_cookie = int(connection.execute("PRAGMA schema_version").fetchone()[0])
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_master SET sql=? WHERE type='table' "
            "AND name='assistant_cognitive_turns'",
            (historical_sql,),
        )
        connection.execute("PRAGMA writable_schema=OFF")
        connection.execute(f"PRAGMA schema_version={schema_cookie + 1}")
    return database


def _run(connection: sqlite3.Connection, label: str) -> int:
    connection.execute(
        """
        INSERT INTO assistant_autonomy_runs(
            public_id, actor, workspace_root, objective, status, grant_json,
            plan_json, created_at, updated_at, started_at, finished_at
        ) VALUES (?, 'owner', '/workspace', 'goal', 'running', '{}', '{}',
                  'created', 'updated', 'started', NULL)
        """,
        (f"run-{label}",),
    )
    return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])


def _cycle(
    connection: sqlite3.Connection,
    run_id: int,
    label: str,
    *,
    status: str = "waiting_owner",
) -> int:
    connection.execute(
        """
        INSERT INTO assistant_cognitive_cycles(
            public_id, autonomy_run_id, actor, status, max_advances,
            max_model_calls, max_replans, max_actions, created_at, updated_at
        ) VALUES (?, ?, 'owner', ?, 12, 8, 2, 4, 'created', 'updated')
        """,
        (f"cycle-{label}", run_id, status),
    )
    return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])


def _turn(
    connection: sqlite3.Connection,
    cycle_id: int,
    label: str,
    *,
    kind: str,
    state: str = "completed",
    decision: str | None = None,
    disposition: str | None = None,
    step_id: str = "run",
    source_request_id: str | None = None,
    created_at: str = "created",
) -> int:
    completed_at = "completed" if state == "completed" else None
    abandoned_at = "abandoned" if state == "abandoned" else None
    sequence = int(
        connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM assistant_cognitive_turns "
            "WHERE cycle_id=?",
            (cycle_id,),
        ).fetchone()[0]
    )
    connection.execute(
        """
        INSERT INTO assistant_cognitive_turns(
            public_id, cycle_id, sequence, kind, state, decision, disposition,
            step_id, source_request_id, created_at, completed_at, abandoned_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"turn-{label}",
            cycle_id,
            sequence,
            kind,
            state,
            decision,
            disposition,
            step_id,
            source_request_id,
            created_at,
            completed_at,
            abandoned_at,
        ),
    )
    return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])


def _event(
    connection: sqlite3.Connection,
    cycle_id: int,
    label: str,
    *,
    turn_id: int,
    event_type: str,
    gate_id: str | None = None,
) -> None:
    sequence = int(
        connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 "
            "FROM assistant_cognitive_cycle_events WHERE cycle_id=?",
            (cycle_id,),
        ).fetchone()[0]
    )
    connection.execute(
        """
        INSERT INTO assistant_cognitive_cycle_events(
            public_id, cycle_id, sequence, turn_id, event_type, from_status,
            to_status, gate_id, summary_code, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, 'action_reserved', 'waiting_owner', ?, ?, '{}', 'now')
        """,
        (
            f"event-{label}",
            cycle_id,
            sequence,
            turn_id,
            event_type,
            gate_id,
            event_type,
        ),
    )


def _result(
    connection: sqlite3.Connection,
    run_id: int,
    label: str,
    *,
    outcome: str = "failed",
) -> str:
    request_id = f"request-{label}"
    digest = "a" * 64
    sequence = int(
        connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 "
            "FROM assistant_autonomy_execution_reservations WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]
    )
    connection.execute(
        """
        INSERT INTO assistant_autonomy_execution_reservations(
            request_id, request_sha256, run_id, sequence, step_id, capability,
            runtime_seconds, is_retry, created_at, command_sha256
        ) VALUES (?, ?, ?, ?, 'run', 'process.exec', 1, 0, 'now', ?)
        """,
        (request_id, digest, run_id, sequence, digest),
    )
    connection.execute(
        """
        INSERT INTO assistant_autonomy_execution_launches(
            request_id, request_sha256, run_id, command_sha256, created_at,
            observation_receipt_sha256
        ) VALUES (?, ?, ?, ?, 'now', ?)
        """,
        (request_id, digest, run_id, digest, digest),
    )
    connection.execute(
        """
        INSERT INTO assistant_autonomy_execution_results(
            request_id, request_sha256, run_id, sequence, step_id,
            command_sha256, runtime_seconds, is_retry, outcome, exit_code,
            duration_ms, summary, error_code, stdout, stderr, stdout_sha256,
            stderr_sha256, timed_out, stdout_truncated, stderr_truncated, created_at
        ) VALUES (?, ?, ?, ?, 'run', ?, 1, 0, ?, 7, 1, 'summary',
                  'process_exit_nonzero', '', '', ?, ?, 0, 0, 0, 'now')
        """,
        (request_id, digest, run_id, sequence, digest, outcome, digest, digest),
    )
    return request_id


def _pending_wait(
    connection: sqlite3.Connection,
    cycle_id: int,
    source_turn_id: int | None,
    label: str,
    *,
    reason: str = "model_request_human",
    gate_id: str | None = None,
    source_request_id: str | None = None,
) -> int:
    connection.execute(
        """
        INSERT INTO assistant_cognitive_owner_waits(
            public_id, cycle_id, sequence, source_turn_id, reason, gate_id,
            source_request_id, state, created_at
        ) VALUES (?, ?, 1, ?, ?, ?, ?, 'pending', 'now')
        """,
        (f"wait-{label}", cycle_id, source_turn_id, reason, gate_id, source_request_id),
    )
    return int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])


def test_schema59_is_vault_only_preserves_58_and_is_idempotent(tmp_path: Path) -> None:
    root = Database(tmp_path / "root.sqlite3", role="root")
    root.migrate()
    with root.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "59"
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='assistant_cognitive_owner_waits'"
        ).fetchone() is None

    database = _schema58(tmp_path / "migration")
    with database.connect() as connection:
        old_turn_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='assistant_cognitive_turns'"
            ).fetchone()[0]
        )
        assert "'limit_exhausted'" not in old_turn_sql
        run_id = _run(connection, "preserved")
        cycle_id = _cycle(connection, run_id, "preserved", status="ready")
        request_id = _result(connection, run_id, "preserved")
        expected_turn_ids = (
            _turn(connection, cycle_id, "reason", kind="reason", decision="stop"),
            _turn(
                connection,
                cycle_id,
                "evaluate",
                kind="evaluate",
                disposition="model_error",
                source_request_id=request_id,
            ),
            _turn(
                connection,
                cycle_id,
                "act",
                kind="act",
                disposition="authority_blocked",
            ),
            _turn(connection, cycle_id, "abandoned", kind="act", state="abandoned"),
        )
        expected_turns = [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM assistant_cognitive_turns ORDER BY id"
            )
        ]
    database.migrate()
    database.migrate()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "59"
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_runs"
        ).fetchone()[0] == 1
        assert tuple(
            row[0]
            for row in connection.execute(
                "SELECT id FROM assistant_cognitive_turns ORDER BY id"
            )
        ) == expected_turn_ids
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM assistant_cognitive_turns ORDER BY id"
            )
        ] == expected_turns
        new_turn_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='assistant_cognitive_turns'"
            ).fetchone()[0]
        )
        assert "'limit_exhausted'" in new_turn_sql
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' "
            "AND name='idx_cognitive_one_reserved_turn'"
        ).fetchone() is not None
        trigger_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='assistant_cognitive_turns'"
            )
        }
        assert {
            "trg_cognitive_turn_identity_immutable",
            "trg_cognitive_turn_resolved_immutable",
            "trg_cognitive_turn_transition",
            "trg_cognitive_turn_act_source_insert",
            "trg_cognitive_turn_source_immutable",
            "trg_cognitive_turn_no_delete",
        } <= trigger_names
        event_fks = connection.execute(
            "PRAGMA foreign_key_list(assistant_cognitive_cycle_events)"
        ).fetchall()
        assert "assistant_cognitive_turns" in {str(row[2]) for row in event_fks}
        assert not any("phase8a" in str(row[2]) for row in event_fks)
        cycle = connection.execute(
            "SELECT public_id, status FROM assistant_cognitive_cycles WHERE id=?",
            (cycle_id,),
        ).fetchone()
        assert cycle is not None and tuple(cycle) == ("cycle-preserved", "ready")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE name='assistant_cognitive_successor_handoffs'"
        ).fetchone() is not None


def test_schema59_turn_dispositions_and_system_limit_transitions(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        run_id = _run(connection, "limits")
        cycle_id = _cycle(connection, run_id, "limits", status="ready")
        _turn(
            connection,
            cycle_id,
            "limit-disposition",
            kind="reason",
            disposition="limit_exhausted",
        )
        _turn(
            connection,
            cycle_id,
            "old-disposition",
            kind="reason",
            disposition="model_error",
        )
        _pending_wait(
            connection,
            cycle_id,
            None,
            "ready-limit",
            reason="limit_exhausted",
        )
        connection.execute(
            "UPDATE assistant_cognitive_cycles SET status='waiting_owner' WHERE id=?",
            (cycle_id,),
        )
        for status in ("action_ready", "evaluation_ready"):
            other_run = _run(connection, f"limit-{status}")
            other_cycle = _cycle(connection, other_run, f"limit-{status}", status=status)
            _pending_wait(
                connection,
                other_cycle,
                None,
                f"{status}-limit",
                reason="limit_exhausted",
            )
            connection.execute(
                "UPDATE assistant_cognitive_cycles SET status='waiting_owner' WHERE id=?",
                (other_cycle,),
            )
        illegal_run = _run(connection, "illegal-transition")
        illegal_cycle = _cycle(connection, illegal_run, "illegal-transition", status="ready")
        with pytest.raises(sqlite3.IntegrityError, match="invalid_transition"):
            connection.execute(
                "UPDATE assistant_cognitive_cycles SET status='waiting_owner' WHERE id=?",
                (illegal_cycle,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="invalid_transition"):
            connection.execute(
                "UPDATE assistant_cognitive_cycles SET status='action_ready' WHERE id=?",
                (illegal_cycle,),
            )


def test_wait_lifecycle_context_bytes_and_pending_uniqueness(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        run_id = _run(connection, "wait")
        cycle_id = _cycle(connection, run_id, "wait")
        turn_id = _turn(
            connection, cycle_id, "wait", kind="reason", decision="request_human"
        )
        wait_id = _pending_wait(connection, cycle_id, turn_id, "wait")
        with pytest.raises(sqlite3.IntegrityError):
            _pending_wait(connection, cycle_id, turn_id, "duplicate")
        connection.execute(
            """
            UPDATE assistant_cognitive_owner_waits
            SET state='resolved', resolution='context_continued', owner_context=?,
                resolved_at='done', resolved_by='owner'
            WHERE id=?
            """,
            ("á" * 1000, wait_id),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE assistant_cognitive_owner_waits SET owner_context=? WHERE id=?",
                ("á" * 1001, wait_id),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE assistant_cognitive_owner_waits SET state='pending' WHERE id=?",
                (wait_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM assistant_cognitive_owner_waits WHERE id=?", (wait_id,)
            )


def test_context_resumed_turn_is_same_cycle_first_model_turn_without_clock(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        run_id = _run(connection, "context")
        cycle_id = _cycle(connection, run_id, "context")
        source_id = _turn(
            connection, cycle_id, "source", kind="reason", decision="request_human"
        )
        wait_id = _pending_wait(connection, cycle_id, source_id, "context")
        connection.execute(
            "UPDATE assistant_cognitive_owner_waits SET state='resolved', "
            "resolution='context_continued', owner_context='context', "
            "resolved_at='later-clock', resolved_by='owner' WHERE id=?",
            (wait_id,),
        )
        other_run = _run(connection, "context-other")
        other_cycle = _cycle(connection, other_run, "context-other")
        wrong_cycle_id = _turn(
            connection, other_cycle, "wrong-cycle", kind="reason", state="reserved"
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid_resumed_turn"):
            connection.execute(
                "UPDATE assistant_cognitive_owner_waits SET resumed_turn_id=? WHERE id=?",
                (wrong_cycle_id, wait_id),
            )
        wrong_kind_id = _turn(
            connection, cycle_id, "wrong-kind", kind="act", state="reserved"
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid_resumed_turn"):
            connection.execute(
                "UPDATE assistant_cognitive_owner_waits SET resumed_turn_id=? WHERE id=?",
                (wrong_kind_id, wait_id),
            )
        connection.execute(
            "UPDATE assistant_cognitive_turns SET state='abandoned', abandoned_at='now' "
            "WHERE id=?",
            (wrong_kind_id,),
        )
        resumed_id = _turn(
            connection,
            cycle_id,
            "resumed",
            kind="reason",
            state="reserved",
            created_at="earlier-clock",
        )
        # The trigger uses durable IDs, not the deliberately older timestamp.
        connection.execute(
            "UPDATE assistant_cognitive_owner_waits SET resumed_turn_id=? WHERE id=?",
            (resumed_id, wait_id),
        )
        connection.execute(
            """
            INSERT INTO assistant_cognitive_owner_waits(
                public_id, cycle_id, sequence, source_turn_id, reason, state, created_at
            ) VALUES ('wait-context-second', ?, 2, ?, 'model_request_human',
                      'pending', 'now')
            """,
            (cycle_id, source_id),
        )
        second_wait_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            "UPDATE assistant_cognitive_owner_waits SET state='resolved', "
            "resolution='context_continued', owner_context='second', "
            "resolved_at='done', resolved_by='owner' WHERE id=?",
            (second_wait_id,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE assistant_cognitive_owner_waits SET resumed_turn_id=? WHERE id=?",
                (resumed_id, second_wait_id),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE assistant_cognitive_owner_waits SET resumed_turn_id=NULL WHERE id=?",
                (wait_id,),
            )


def test_wait_resolution_must_match_reason_and_context_is_purpose_specific(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        run_id = _run(connection, "resolution")
        cycle_id = _cycle(connection, run_id, "resolution")
        turn_id = _turn(
            connection,
            cycle_id,
            "resolution",
            kind="reason",
            decision="request_human",
        )
        wait_id = _pending_wait(connection, cycle_id, turn_id, "resolution")
        with pytest.raises(sqlite3.IntegrityError, match="reason_mismatch"):
            connection.execute(
                "UPDATE assistant_cognitive_owner_waits SET state='resolved', "
                "resolution='ordinary_gate_continued', resolved_at='now', "
                "resolved_by='owner' WHERE id=?",
                (wait_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE assistant_cognitive_owner_waits SET state='resolved', "
                "resolution='reasoning_retried', owner_context='not allowed', "
                "resolved_at='now', resolved_by='owner' WHERE id=?",
                (wait_id,),
            )


@pytest.mark.parametrize(
    ("decision", "disposition", "expected"),
    (
        ("request_human", None, "model_request_human"),
        ("insufficient_evidence", None, "insufficient_evidence"),
        ("propose_replan", None, "replan_requested"),
        (None, "model_unavailable", "model_unavailable"),
        (None, "model_error", "model_error"),
        (None, "malformed_model_output", "malformed_model_output"),
    ),
)
def test_backfill_model_waits(
    tmp_path: Path,
    decision: str | None,
    disposition: str | None,
    expected: str,
) -> None:
    database = _schema58(tmp_path)
    with database.connect() as connection:
        run_id = _run(connection, expected)
        cycle_id = _cycle(connection, run_id, expected)
        turn_id = _turn(
            connection,
            cycle_id,
            expected,
            kind="reason",
            decision=decision,
            disposition=disposition,
        )
    database.migrate()
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM assistant_cognitive_owner_waits").fetchone()
        assert row is not None
        assert row["reason"] == expected
        assert int(row["source_turn_id"]) == turn_id
        assert row["state"] == "pending"


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("abandoned", "abandoned_before_delegation"),
        ("delegated", "execution_linkage_unknown"),
        ("incomplete", "incomplete_attempt"),
        ("ambiguous", "recovery_required"),
    ),
)
def test_backfill_action_and_ambiguous_waits(
    tmp_path: Path, case: str, expected: str
) -> None:
    database = _schema58(tmp_path)
    with database.connect() as connection:
        run_id = _run(connection, case)
        status = "blocked_incomplete" if case == "incomplete" else "waiting_owner"
        cycle_id = _cycle(connection, run_id, case, status=status)
        if case in {"abandoned", "delegated"}:
            turn_id = _turn(connection, cycle_id, case, kind="act", state="abandoned")
            if case == "delegated":
                _event(
                    connection,
                    cycle_id,
                    case,
                    turn_id=turn_id,
                    event_type="action_delegated",
                )
        elif case == "incomplete":
            turn_id = _turn(
                connection,
                cycle_id,
                case,
                kind="act",
                disposition="incomplete_attempt",
            )
        else:
            turn_id = _turn(
                connection, cycle_id, case, kind="reason", decision="stop"
            )
    database.migrate()
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM assistant_cognitive_owner_waits").fetchone()
        assert row is not None and row["reason"] == expected
        assert int(row["source_turn_id"]) == turn_id


def test_backfill_ordinary_gate_and_retry_result(tmp_path: Path) -> None:
    database = _schema58(tmp_path)
    with database.connect() as connection:
        gate_run = _run(connection, "gate")
        gate_cycle = _cycle(connection, gate_run, "gate")
        gate_turn = _turn(
            connection, gate_cycle, "gate", kind="act", disposition="authority_blocked"
        )
        connection.execute(
            """
            INSERT INTO assistant_autonomy_human_gates(
                public_id, run_id, kind, status, reason, created_at
            ) VALUES ('gate-exact', ?, 'approval', 'pending', 'reason', 'now')
            """,
            (gate_run,),
        )
        _event(
            connection,
            gate_cycle,
            "gate",
            turn_id=gate_turn,
            event_type="action_blocked",
            gate_id="gate-exact",
        )

        retry_run = _run(connection, "retry")
        retry_cycle = _cycle(connection, retry_run, "retry")
        retry_turn = _turn(
            connection, retry_cycle, "retry", kind="act", disposition="authority_blocked"
        )
        request_id = _result(connection, retry_run, "retry")
    database.migrate()
    with database.connect() as connection:
        rows = {
            row["reason"]: row
            for row in connection.execute("SELECT * FROM assistant_cognitive_owner_waits")
        }
        assert rows["ordinary_gate_required"]["gate_id"] == "gate-exact"
        assert rows["retry_review_required"]["source_request_id"] == request_id
        assert int(rows["retry_review_required"]["source_turn_id"]) == retry_turn


def test_backfill_conflicting_ordinary_gates_is_recovery_required(tmp_path: Path) -> None:
    database = _schema58(tmp_path)
    with database.connect() as connection:
        run_id = _run(connection, "gate-conflict")
        cycle_id = _cycle(connection, run_id, "gate-conflict")
        turn_id = _turn(
            connection,
            cycle_id,
            "gate-conflict",
            kind="act",
            disposition="authority_blocked",
        )
        for suffix in ("one", "two"):
            gate_id = f"gate-conflict-{suffix}"
            connection.execute(
                "INSERT INTO assistant_autonomy_human_gates(public_id, run_id, kind, "
                "status, reason, created_at) VALUES (?, ?, 'approval', 'pending', "
                "'reason', 'now')",
                (gate_id, run_id),
            )
            _event(
                connection,
                cycle_id,
                f"gate-conflict-{suffix}",
                turn_id=turn_id,
                event_type="action_blocked",
                gate_id=gate_id,
            )
            if suffix == "one":
                connection.execute(
                    "UPDATE assistant_autonomy_human_gates SET status='rejected', "
                    "resolved_at='done', resolved_by='owner' WHERE public_id=?",
                    (gate_id,),
                )
    database.migrate()
    with database.connect() as connection:
        wait = connection.execute(
            "SELECT reason, gate_id FROM assistant_cognitive_owner_waits"
        ).fetchone()
        assert wait is not None and tuple(wait) == ("recovery_required", None)


@pytest.mark.parametrize(
    "outcomes",
    (("failed", "cancelled"), ("failed", "succeeded")),
)
def test_backfill_ambiguous_execution_history_is_recovery_required(
    tmp_path: Path, outcomes: tuple[str, str]
) -> None:
    database = _schema58(tmp_path)
    with database.connect() as connection:
        run_id = _run(connection, "result-conflict")
        cycle_id = _cycle(connection, run_id, "result-conflict")
        _turn(
            connection,
            cycle_id,
            "result-conflict",
            kind="act",
            disposition="authority_blocked",
        )
        for sequence, outcome in enumerate(outcomes, start=1):
            _result(
                connection,
                run_id,
                f"result-conflict-{sequence}",
                outcome=outcome,
            )
    database.migrate()
    with database.connect() as connection:
        wait = connection.execute(
            "SELECT reason, source_request_id FROM assistant_cognitive_owner_waits"
        ).fetchone()
        assert wait is not None and tuple(wait) == ("recovery_required", None)


def test_backfill_provable_replan_limit_exhaustion(tmp_path: Path) -> None:
    database = _schema58(tmp_path)
    with database.connect() as connection:
        run_id = _run(connection, "limit")
        cycle_id = _cycle(connection, run_id, "limit")
        _turn(connection, cycle_id, "replan-1", kind="reason", decision="propose_replan")
        _turn(connection, cycle_id, "replan-2", kind="reason", decision="propose_replan")
        exhausted_id = _turn(
            connection,
            cycle_id,
            "limit",
            kind="reason",
            disposition="authority_blocked",
        )
    database.migrate()
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM assistant_cognitive_owner_waits").fetchone()
        assert row is not None and row["reason"] == "limit_exhausted"
        assert int(row["source_turn_id"]) == exhausted_id


def test_wait_provenance_rejects_wrong_cycle_gate_result_and_turn_kind(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        run_a = _run(connection, "a")
        cycle_a = _cycle(connection, run_a, "a")
        model_turn = _turn(
            connection, cycle_a, "model", kind="reason", decision="request_human"
        )
        run_b = _run(connection, "b")
        cycle_b = _cycle(connection, run_b, "b")
        with pytest.raises(sqlite3.IntegrityError, match="invalid_provenance"):
            _pending_wait(connection, cycle_b, model_turn, "wrong-cycle")
        act_turn = _turn(
            connection, cycle_a, "act", kind="act", disposition="authority_blocked"
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid_provenance"):
            _pending_wait(connection, cycle_a, act_turn, "wrong-kind")
        connection.execute(
            """
            INSERT INTO assistant_autonomy_human_gates(
                public_id, run_id, kind, status, reason, created_at
            ) VALUES ('wrong-run-gate', ?, 'approval', 'pending', 'reason', 'now')
            """,
            (run_b,),
        )
        _event(
            connection,
            cycle_a,
            "wrong-run-gate",
            turn_id=act_turn,
            event_type="action_blocked",
            gate_id="wrong-run-gate",
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid_provenance"):
            _pending_wait(
                connection,
                cycle_a,
                act_turn,
                "wrong-gate",
                reason="ordinary_gate_required",
                gate_id="wrong-run-gate",
            )
        request_b = _result(connection, run_b, "b")
        with pytest.raises(sqlite3.IntegrityError, match="invalid_provenance"):
            _pending_wait(
                connection,
                cycle_a,
                act_turn,
                "wrong-result",
                reason="retry_review_required",
                source_request_id=request_b,
            )


def test_wait_provenance_requires_exact_completed_model_cause(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        run_id = _run(connection, "model-cause")
        cycle_id = _cycle(connection, run_id, "model-cause")
        wrong_decision = _turn(
            connection, cycle_id, "wrong-decision", kind="reason", decision="stop"
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid_provenance"):
            _pending_wait(connection, cycle_id, wrong_decision, "wrong-decision")
        wrong_disposition = _turn(
            connection,
            cycle_id,
            "wrong-disposition",
            kind="reason",
            disposition="model_error",
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid_provenance"):
            _pending_wait(
                connection,
                cycle_id,
                wrong_disposition,
                "wrong-disposition",
                reason="model_unavailable",
            )
        reserved = _turn(
            connection, cycle_id, "reserved-model", kind="reason", state="reserved"
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid_provenance"):
            _pending_wait(connection, cycle_id, reserved, "reserved-model")


def test_wait_provenance_requires_exact_completed_act_cause(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        run_id = _run(connection, "act-cause")
        cycle_id = _cycle(connection, run_id, "act-cause")
        gate_id = "gate-act-cause"
        connection.execute(
            "INSERT INTO assistant_autonomy_human_gates(public_id, run_id, kind, "
            "status, reason, created_at) VALUES (?, ?, 'approval', 'pending', "
            "'reason', 'now')",
            (gate_id, run_id),
        )
        wrong_gate_turn = _turn(
            connection,
            cycle_id,
            "wrong-gate-cause",
            kind="act",
            disposition="incomplete_attempt",
        )
        _event(
            connection,
            cycle_id,
            "wrong-gate-cause",
            turn_id=wrong_gate_turn,
            event_type="action_blocked",
            gate_id=gate_id,
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid_provenance"):
            _pending_wait(
                connection,
                cycle_id,
                wrong_gate_turn,
                "wrong-gate-cause",
                reason="ordinary_gate_required",
                gate_id=gate_id,
            )
        wrong_retry_turn = _turn(
            connection,
            cycle_id,
            "wrong-retry-cause",
            kind="act",
            disposition="incomplete_attempt",
        )
        failed_id = _result(connection, run_id, "wrong-retry-cause")
        with pytest.raises(sqlite3.IntegrityError, match="invalid_provenance"):
            _pending_wait(
                connection,
                cycle_id,
                wrong_retry_turn,
                "wrong-retry-cause",
                reason="retry_review_required",
                source_request_id=failed_id,
            )
        succeeded_turn = _turn(
            connection,
            cycle_id,
            "succeeded-retry",
            kind="act",
            disposition="authority_blocked",
        )
        succeeded_id = _result(
            connection, run_id, "succeeded-retry", outcome="succeeded"
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid_provenance"):
            _pending_wait(
                connection,
                cycle_id,
                succeeded_turn,
                "succeeded-retry",
                reason="retry_review_required",
                source_request_id=succeeded_id,
            )


def test_post_model_limit_wait_requires_limit_disposition(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        run_id = _run(connection, "post-limit")
        cycle_id = _cycle(connection, run_id, "post-limit")
        wrong_turn = _turn(
            connection, cycle_id, "post-limit-wrong", kind="reason", decision="stop"
        )
        with pytest.raises(sqlite3.IntegrityError, match="invalid_provenance"):
            _pending_wait(
                connection,
                cycle_id,
                wrong_turn,
                "post-limit-wrong",
                reason="limit_exhausted",
            )
        exact_turn = _turn(
            connection,
            cycle_id,
            "post-limit-exact",
            kind="reason",
            disposition="limit_exhausted",
        )
        assert _pending_wait(
            connection,
            cycle_id,
            exact_turn,
            "post-limit-exact",
            reason="limit_exhausted",
        ) > 0


def _handoff_values(wait_id: int, cycle_id: int, label: str, status: str) -> tuple:
    successor = None
    resolved_at = None
    resolved_by = None
    if status != "proposed":
        resolved_at = "done"
        resolved_by = "owner"
    return (
        f"handoff-{label}",
        f"request-{label}",
        wait_id,
        cycle_id,
        "a" * 64,
        "goal",
        "/workspace",
        "{}",
        "{}",
        "b" * 64,
        status,
        successor,
        "now",
        resolved_at,
        resolved_by,
    )


def _insert_handoff(connection: sqlite3.Connection, values: tuple) -> None:
    connection.execute(
        """
        INSERT INTO assistant_cognitive_successor_handoffs(
            public_id, request_key, wait_id, predecessor_cycle_id,
            predecessor_state_sha256, objective, workspace_root, plan_json,
            grant_spec_json, candidate_sha256, status, successor_run_id,
            created_at, resolved_at, resolved_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )


def test_handoff_partial_uniqueness_lifecycle_hash_json_and_immutability(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        run_id = _run(connection, "handoff")
        cycle_id = _cycle(connection, run_id, "handoff")
        turn_id = _turn(
            connection, cycle_id, "handoff", kind="reason", decision="propose_replan"
        )
        wait_id = _pending_wait(
            connection, cycle_id, turn_id, "handoff", reason="replan_requested"
        )
        _insert_handoff(connection, _handoff_values(wait_id, cycle_id, "one", "proposed"))
        with pytest.raises(sqlite3.IntegrityError):
            _insert_handoff(
                connection, _handoff_values(wait_id, cycle_id, "two", "proposed")
            )
        connection.execute(
            "UPDATE assistant_cognitive_successor_handoffs SET status='rejected', "
            "resolved_at='done', resolved_by='owner' WHERE public_id='handoff-one'"
        )
        _insert_handoff(connection, _handoff_values(wait_id, cycle_id, "two", "proposed"))
        connection.execute(
            "UPDATE assistant_cognitive_successor_handoffs SET status='rejected', "
            "resolved_at='done', resolved_by='owner' WHERE public_id='handoff-two'"
        )
        _insert_handoff(
            connection, _handoff_values(wait_id, cycle_id, "three", "proposed")
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_successor_handoffs "
            "WHERE wait_id=? AND status='rejected'",
            (wait_id,),
        ).fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match="resolved_immutable"):
            connection.execute(
                "UPDATE assistant_cognitive_successor_handoffs "
                "SET resolved_by='other' WHERE public_id='handoff-one'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE assistant_cognitive_successor_handoffs SET plan_json='x' "
                "WHERE public_id='handoff-three'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM assistant_cognitive_successor_handoffs "
                "WHERE public_id='handoff-one'"
            )


def test_handoff_wait_cycle_mismatch_and_accepted_constraints(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        run_a = _run(connection, "ha")
        cycle_a = _cycle(connection, run_a, "ha")
        turn_a = _turn(
            connection, cycle_a, "ha", kind="reason", decision="propose_replan"
        )
        wait_a = _pending_wait(
            connection, cycle_a, turn_a, "ha", reason="replan_requested"
        )
        run_b = _run(connection, "hb")
        cycle_b = _cycle(connection, run_b, "hb")
        with pytest.raises(sqlite3.IntegrityError, match="wait_cycle_mismatch"):
            _insert_handoff(
                connection, _handoff_values(wait_a, cycle_b, "mismatch", "proposed")
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_handoff(
                connection, _handoff_values(wait_a, cycle_a, "bad-hash", "accepted")
            )


def test_handoff_requires_pending_replan_wait(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        run_id = _run(connection, "handoff-reason")
        cycle_id = _cycle(connection, run_id, "handoff-reason")
        human_turn = _turn(
            connection, cycle_id, "handoff-human", kind="reason", decision="request_human"
        )
        human_wait = _pending_wait(connection, cycle_id, human_turn, "handoff-human")
        with pytest.raises(sqlite3.IntegrityError, match="wait_cycle_mismatch"):
            _insert_handoff(
                connection,
                _handoff_values(human_wait, cycle_id, "non-replan", "proposed"),
            )
        connection.execute(
            "UPDATE assistant_cognitive_owner_waits SET state='resolved', "
            "resolution='stopped', resolved_at='now', resolved_by='owner' WHERE id=?",
            (human_wait,),
        )
        replan_run = _run(connection, "handoff-replan")
        replan_cycle = _cycle(connection, replan_run, "handoff-replan")
        replan_turn = _turn(
            connection,
            replan_cycle,
            "handoff-replan",
            kind="reason",
            decision="propose_replan",
        )
        replan_wait = _pending_wait(
            connection,
            replan_cycle,
            replan_turn,
            "handoff-replan",
            reason="replan_requested",
        )
        _insert_handoff(
            connection,
            _handoff_values(
                replan_wait, replan_cycle, "pending-replan", "proposed"
            ),
        )
        connection.execute(
            "UPDATE assistant_cognitive_successor_handoffs SET status='rejected', "
            "resolved_at='now', resolved_by='owner' WHERE request_key='request-pending-replan'"
        )
        connection.execute(
            "UPDATE assistant_cognitive_owner_waits SET state='resolved', "
            "resolution='replan_declined', resolved_at='now', resolved_by='owner' WHERE id=?",
            (replan_wait,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="wait_cycle_mismatch"):
            _insert_handoff(
                connection,
                _handoff_values(
                    replan_wait, replan_cycle, "resolved-replan", "proposed"
                ),
            )


@pytest.mark.parametrize(
    ("field_index", "invalid_value"),
    (
        (4, "A" * 64),
        (4, "a" * 63),
        (9, "B" * 64),
        (9, "b" * 65),
        (7, "{"),
        (8, "not-json"),
        (1, ""),
        (5, ""),
        (6, ""),
    ),
)
def test_handoff_direct_hash_json_and_bounds_checks(
    tmp_path: Path, field_index: int, invalid_value: str
) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        run_id = _run(connection, "direct-check")
        cycle_id = _cycle(connection, run_id, "direct-check")
        turn_id = _turn(
            connection,
            cycle_id,
            "direct-check",
            kind="reason",
            decision="propose_replan",
        )
        wait_id = _pending_wait(
            connection,
            cycle_id,
            turn_id,
            "direct-check",
            reason="replan_requested",
        )
        values = list(_handoff_values(wait_id, cycle_id, "direct-check", "proposed"))
        values[field_index] = invalid_value
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            _insert_handoff(connection, tuple(values))


def test_handoff_accept_reject_lifecycle_and_one_accepted_per_cycle(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        run_id = _run(connection, "lifecycle")
        cycle_id = _cycle(connection, run_id, "lifecycle")
        turn_id = _turn(
            connection, cycle_id, "lifecycle", kind="reason", decision="propose_replan"
        )
        wait_id = _pending_wait(
            connection, cycle_id, turn_id, "lifecycle", reason="replan_requested"
        )
        _insert_handoff(connection, _handoff_values(wait_id, cycle_id, "accepted", "proposed"))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE assistant_cognitive_successor_handoffs SET status='accepted', "
                "resolved_at='done', resolved_by='owner' WHERE public_id='handoff-accepted'"
            )
        successor_id = _run(connection, "successor")
        connection.execute(
            "UPDATE assistant_cognitive_successor_handoffs SET status='accepted', "
            "successor_run_id=?, resolved_at='done', resolved_by='owner' "
            "WHERE public_id='handoff-accepted'",
            (successor_id,),
        )
        _insert_handoff(connection, _handoff_values(wait_id, cycle_id, "second", "proposed"))
        second_successor = _run(connection, "second-successor")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE assistant_cognitive_successor_handoffs SET status='accepted', "
                "successor_run_id=?, resolved_at='done', resolved_by='owner' "
                "WHERE public_id='handoff-second'",
                (second_successor,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE assistant_cognitive_successor_handoffs SET status='rejected', "
                "successor_run_id=?, resolved_at='done', resolved_by='owner' "
                "WHERE public_id='handoff-second'",
                (second_successor,),
            )
        connection.execute(
            "UPDATE assistant_cognitive_successor_handoffs SET status='rejected', "
            "resolved_at='done', resolved_by='owner' WHERE public_id='handoff-second'"
        )


@pytest.mark.parametrize("outcome", ("failed", "cancelled"))
def test_retry_wait_accepts_exact_failed_or_cancelled_result(
    tmp_path: Path, outcome: str
) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        run_id = _run(connection, outcome)
        cycle_id = _cycle(connection, run_id, outcome)
        turn_id = _turn(
            connection, cycle_id, outcome, kind="act", disposition="authority_blocked"
        )
        request_id = _result(connection, run_id, outcome, outcome=outcome)
        wait_id = _pending_wait(
            connection,
            cycle_id,
            turn_id,
            outcome,
            reason="retry_review_required",
            source_request_id=request_id,
        )
        assert wait_id > 0
