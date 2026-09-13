from __future__ import annotations

import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from elyndra.autonomy import (
    AutonomyExecutionBinding,
    AutonomyRepository,
    AutonomyRun,
    AutonomyRunStatus,
    BubblewrapExecutor,
    Capability,
    CapabilityGrant,
    CommandSpec,
    ExecutionOutcome,
    ExecutionResult,
    HumanGateKind,
    HumanGateStatus,
    RunPlan,
    RunStep,
    WorkspaceScope,
)
from elyndra.autonomy.supervised_runner import (
    SupervisedAutonomyRunner,
    SupervisedTickOutcome,
)
from elyndra.db import Database


def _state(
    tmp_path: Path,
    *,
    max_retries: int = 1,
    requires_gate: bool = False,
    max_commands: int = 2,
    max_runtime_seconds: int = 6,
    two_steps: bool = False,
):
    root = tmp_path / "project"
    root.mkdir(parents=True)
    executable = str(Path(sys.executable).resolve(strict=True))
    now = datetime.now(UTC)
    step = RunStep(
        step_id="run",
        capability=Capability.PROCESS_EXEC,
        action="execute",
        target=".",
        requires_human_gate=requires_gate,
        command=CommandSpec(
            executable=executable,
            argv=(executable, "-c", "raise SystemExit(7)"),
            cwd=".",
            timeout_seconds=3,
        ),
    )
    steps = (step,)
    if two_steps:
        steps += (replace(step, step_id="later", action="execute later"),)
    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(root),
        grant=CapabilityGrant(
            capabilities=frozenset({Capability.PROCESS_EXEC}),
            issued_at=now,
            expires_at=now + timedelta(hours=1),
            max_steps=len(steps),
            max_commands=max_commands,
            max_retries=max_retries,
            max_runtime_seconds=max_runtime_seconds,
            allowed_executables=(executable,),
        ),
        plan=RunPlan(objective="retry", steps=steps),
    )
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    repository.create(run)
    repository.transition(
        run.run_id,
        AutonomyRunStatus.RUNNING,
        actor="owner",
        summary="start",
    )
    return database, repository, run


def _failed_attempt(
    repository: AutonomyRepository,
    run: AutonomyRun,
    *,
    outcome: ExecutionOutcome = ExecutionOutcome.FAILED,
):
    prepared = AutonomyExecutionBinding(repository).bind(
        run.run_id, actor="owner"
    ).prepare("run")
    receipt = repository._claim_execution_launch(
        prepared.request,
        actor="owner",
        runtime_seconds=prepared.reserved_runtime_seconds,
        retry=False,
        workspace_lease_receipt=prepared.workspace_session.receipt,
    )
    repository._record_execution_result(
        prepared.request,
        _result(prepared.request.request_id, outcome),
        actor="owner",
        receipt=receipt,
    )
    return prepared


def _result(request_id: str, outcome: ExecutionOutcome) -> ExecutionResult:
    if outcome is ExecutionOutcome.CANCELLED:
        return ExecutionResult(
            request_id=request_id,
            outcome=outcome,
            summary="ignored process output",
            exit_code=None,
            error_code="cancelled",
        )
    if outcome is ExecutionOutcome.SUCCEEDED:
        return ExecutionResult(
            request_id=request_id,
            outcome=outcome,
            summary="ignored process output",
            exit_code=0,
        )
    return ExecutionResult(
        request_id=request_id,
        outcome=outcome,
        summary="ignored process output",
        exit_code=7,
        error_code="process_exit_nonzero",
    )


def test_schema_57_retry_tables_are_vault_only_idempotent_and_append_only(
    tmp_path: Path,
) -> None:
    root = Database(tmp_path / "root.sqlite3", role="root")
    vault = Database(tmp_path / "vault.sqlite3", role="vault")
    root.migrate()
    vault.migrate()
    vault.migrate()
    with root.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='assistant_autonomy_retry_reviews'"
        ).fetchone() is None
    database, repository, run = _state(tmp_path / "state")
    _failed_attempt(repository, run)
    review = repository.request_retry_review(run.run_id, "run", actor="owner")
    with pytest.raises(
        sqlite3.IntegrityError, match="append_only"
    ), database.connect() as connection:
        connection.execute("UPDATE assistant_autonomy_retry_reviews SET step_id='x'")
    with pytest.raises(
        sqlite3.IntegrityError, match="append_only"
    ), database.connect() as connection:
        connection.execute("DELETE FROM assistant_autonomy_retry_reviews")
    assert review["status"] == "pending"


def test_review_creation_is_exact_pending_and_consumes_no_budget(tmp_path: Path) -> None:
    database, repository, run = _state(tmp_path)
    source = _failed_attempt(repository, run)
    before = repository.execution_budget(run.run_id, actor="owner").snapshot()
    review = repository.request_retry_review(run.run_id, "run", actor="owner")
    after = repository.execution_budget(run.run_id, actor="owner").snapshot()
    assert before == after
    assert review["source_request_id"] == source.request.request_id
    with database.connect() as connection:
        event = connection.execute(
            "SELECT step_id FROM assistant_autonomy_events "
            "WHERE event_type='human_gate_requested' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert event["step_id"] == ""
    repository.resolve_human_gate(
        review["gate_id"], actor="owner", decision=HumanGateStatus.APPROVED
    )
    with pytest.raises(PermissionError):
        repository.request_retry_review(run.run_id, "run", actor="owner")


def test_generic_retry_gate_is_denied_and_zero_retry_cannot_review(tmp_path: Path) -> None:
    _database, repository, run = _state(tmp_path, max_retries=0)
    with pytest.raises(PermissionError):
        repository.request_human_gate(
            run.run_id,
            actor="owner",
            reason="not allowed",
            kind=HumanGateKind.RETRY_REVIEW,
        )
    _failed_attempt(repository, run)
    with pytest.raises(PermissionError):
        repository.request_retry_review(run.run_id, "run", actor="owner")


def test_approved_review_is_consumed_atomically_once(tmp_path: Path) -> None:
    database, repository, run = _state(tmp_path)
    _failed_attempt(repository, run)
    review = repository.request_retry_review(run.run_id, "run", actor="owner")
    repository.resolve_human_gate(
        review["gate_id"], actor="owner", decision=HumanGateStatus.APPROVED
    )
    retry = AutonomyExecutionBinding(repository).bind(
        run.run_id, actor="owner"
    ).prepare("run", retry=True)
    budget = repository.execution_budget(run.run_id, actor="owner").snapshot()
    assert (budget.commands_reserved, budget.retries_reserved) == (2, 1)
    assert budget.runtime_seconds_reserved == 6
    with database.connect() as connection:
        consumption = connection.execute(
            "SELECT retry_request_id FROM assistant_autonomy_retry_consumptions"
        ).fetchone()
    assert consumption["retry_request_id"] == retry.request.request_id
    with pytest.raises(PermissionError):
        AutonomyExecutionBinding(repository).bind(
            run.run_id, actor="owner"
        ).prepare("run", retry=True)


def test_unapproved_and_gap_attempts_never_authorize_retry(tmp_path: Path) -> None:
    _database, repository, run = _state(tmp_path)
    failed = _failed_attempt(repository, run)
    with pytest.raises(PermissionError):
        AutonomyExecutionBinding(repository).bind(
            run.run_id, actor="owner"
        ).prepare("run", retry=True)
    review = repository.request_retry_review(run.run_id, "run", actor="owner")
    repository.resolve_human_gate(
        review["gate_id"], actor="owner", decision=HumanGateStatus.APPROVED
    )
    retry = AutonomyExecutionBinding(repository).bind(
        run.run_id, actor="owner"
    ).prepare("run", retry=True)
    assert failed.request.request_id != retry.request.request_id
    assert repository.execution_attempt_gaps(run.run_id, actor="owner")[0][
        "state"
    ] == "reservation_unlaunched"
    assert not repository.retry_review_available(run.run_id, "run", actor="owner")


@pytest.mark.parametrize(
    "outcome",
    (ExecutionOutcome.FAILED, ExecutionOutcome.CANCELLED),
)
def test_failed_and_cancelled_sources_are_reviewable(
    tmp_path: Path,
    outcome: ExecutionOutcome,
) -> None:
    _database, repository, run = _state(tmp_path)
    source = _failed_attempt(repository, run, outcome=outcome)
    review = repository.request_retry_review(run.run_id, "run", actor="owner")
    assert review["source_request_id"] == source.request.request_id


def test_succeeded_source_and_unlaunched_reservation_are_denied(tmp_path: Path) -> None:
    _database, repository, run = _state(tmp_path)
    prepared = AutonomyExecutionBinding(repository).bind(
        run.run_id, actor="owner"
    ).prepare("run")
    with pytest.raises(PermissionError, match="incompleto"):
        repository.request_retry_review(run.run_id, "run", actor="owner")
    receipt = repository._claim_execution_launch(
        prepared.request,
        actor="owner",
        runtime_seconds=prepared.reserved_runtime_seconds,
        retry=False,
        workspace_lease_receipt=prepared.workspace_session.receipt,
    )
    repository._record_execution_result(
        prepared.request,
        _result(prepared.request.request_id, ExecutionOutcome.SUCCEEDED),
        actor="owner",
        receipt=receipt,
    )
    with pytest.raises(PermissionError, match="primer step incompleto"):
        repository.request_retry_review(run.run_id, "run", actor="owner")


@pytest.mark.parametrize(
    "decision",
    (HumanGateStatus.REJECTED, HumanGateStatus.CANCELLED),
)
def test_retry_review_rejection_and_cancellation_end_run(
    tmp_path: Path,
    decision: HumanGateStatus,
) -> None:
    _database, repository, run = _state(tmp_path)
    _failed_attempt(repository, run)
    review = repository.request_retry_review(run.run_id, "run", actor="owner")
    item = repository.resolve_human_gate(
        review["gate_id"], actor="owner", decision=decision
    )
    assert item["status"] == AutonomyRunStatus.CANCELLED.value


def test_retry_review_tables_enforce_foreign_keys_and_uniqueness(tmp_path: Path) -> None:
    database, repository, run = _state(tmp_path)
    _failed_attempt(repository, run)
    review = repository.request_retry_review(run.run_id, "run", actor="owner")
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM assistant_autonomy_retry_reviews"
        ).fetchone()
    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute(
            """
            INSERT INTO assistant_autonomy_retry_reviews(
                public_id, run_id, step_id, source_request_id, gate_id, created_at
            ) VALUES ('duplicate', ?, 'run', ?, ?, 'now')
            """,
            (row["run_id"], row["source_request_id"], review["gate_id"]),
        )
    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute(
            """
            INSERT INTO assistant_autonomy_retry_consumptions(
                retry_review_id, retry_request_id, created_at
            ) VALUES (?, 'missing-reservation', 'now')
            """,
            (row["id"],),
        )


def test_consumption_is_append_only_and_same_request_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    database, repository, run = _state(tmp_path)
    _failed_attempt(repository, run)
    review = repository.request_retry_review(run.run_id, "run", actor="owner")
    repository.resolve_human_gate(
        review["gate_id"], actor="owner", decision=HumanGateStatus.APPROVED
    )
    prepared = AutonomyExecutionBinding(repository).bind(
        run.run_id, actor="owner"
    ).prepare("run", retry=True)
    replay = repository.reserve_execution(
        prepared.request,
        actor="owner",
        runtime_seconds=prepared.reserved_runtime_seconds,
        retry=True,
    )
    assert replay.commands_reserved == 2
    with pytest.raises(PermissionError, match="step congelado"):
        repository.reserve_execution(
            replace(prepared.request, action="different"),
            actor="owner",
            runtime_seconds=prepared.reserved_runtime_seconds,
            retry=True,
        )
    with pytest.raises(
        sqlite3.IntegrityError, match="append_only"
    ), database.connect() as connection:
        connection.execute("UPDATE assistant_autonomy_retry_consumptions SET created_at='x'")
    with pytest.raises(
        sqlite3.IntegrityError, match="append_only"
    ), database.connect() as connection:
        connection.execute("DELETE FROM assistant_autonomy_retry_consumptions")


def test_two_concurrent_retry_preparations_consume_once(tmp_path: Path) -> None:
    database, repository, run = _state(tmp_path)
    _failed_attempt(repository, run)
    review = repository.request_retry_review(run.run_id, "run", actor="owner")
    repository.resolve_human_gate(
        review["gate_id"], actor="owner", decision=HumanGateStatus.APPROVED
    )

    def prepare() -> str:
        try:
            return AutonomyExecutionBinding(repository).bind(
                run.run_id, actor="owner"
            ).prepare("run", retry=True).request.request_id
        except PermissionError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _value: prepare(), range(2)))
    assert outcomes.count("denied") == 1
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_retry_consumptions"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_execution_reservations"
        ).fetchone()[0] == 2


def test_two_concurrent_runner_ticks_launch_one_approved_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, repository, run = _state(tmp_path)
    _failed_attempt(repository, run)
    review = repository.request_retry_review(run.run_id, "run", actor="owner")
    repository.resolve_human_gate(
        review["gate_id"], actor="owner", decision=HumanGateStatus.APPROVED
    )
    launches: list[str] = []

    def execute(executor, prepared, *, cancellation=None):
        launches.append(prepared.request.request_id)
        receipt = repository._claim_execution_launch(
            prepared.request,
            actor="owner",
            runtime_seconds=prepared.reserved_runtime_seconds,
            retry=True,
            workspace_lease_receipt=prepared.workspace_session.receipt,
        )
        result = _result(prepared.request.request_id, ExecutionOutcome.FAILED)
        repository._record_execution_result(
            prepared.request, result, actor="owner", receipt=receipt
        )
        return result

    monkeypatch.setattr(BubblewrapExecutor, "execute", execute)
    monkeypatch.setattr(BubblewrapExecutor, "__init__", lambda self, *args, **kwargs: None)

    def tick() -> SupervisedTickOutcome:
        return SupervisedAutonomyRunner(repository, actor="owner").tick(
            run.run_id
        ).outcome

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _value: tick(), range(2)))

    assert len(launches) == 1
    assert outcomes.count(SupervisedTickOutcome.EXECUTED_FAILED) == 1
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_retry_consumptions"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_execution_reservations"
        ).fetchone()[0] == 2


@pytest.mark.parametrize(
    "retry_outcome",
    (ExecutionOutcome.FAILED, ExecutionOutcome.CANCELLED),
)
def test_consumed_review_cannot_authorize_new_failed_or_cancelled_result(
    tmp_path: Path,
    retry_outcome: ExecutionOutcome,
) -> None:
    database, repository, run = _state(
        tmp_path,
        max_commands=3,
        max_retries=2,
        max_runtime_seconds=9,
    )
    initial = _failed_attempt(repository, run)
    first_review = repository.request_retry_review(run.run_id, "run", actor="owner")
    repository.resolve_human_gate(
        first_review["gate_id"], actor="owner", decision=HumanGateStatus.APPROVED
    )
    retry = AutonomyExecutionBinding(repository).bind(
        run.run_id, actor="owner"
    ).prepare("run", retry=True)
    receipt = repository._claim_execution_launch(
        retry.request,
        actor="owner",
        runtime_seconds=retry.reserved_runtime_seconds,
        retry=True,
        workspace_lease_receipt=retry.workspace_session.receipt,
    )
    repository._record_execution_result(
        retry.request,
        _result(retry.request.request_id, retry_outcome),
        actor="owner",
        receipt=receipt,
    )

    assert initial.request.request_id != retry.request.request_id
    assert not repository.retry_review_available(run.run_id, "run", actor="owner")
    with pytest.raises(PermissionError):
        AutonomyExecutionBinding(repository).bind(
            run.run_id, actor="owner"
        ).prepare("run", retry=True)

    second_review = repository.request_retry_review(run.run_id, "run", actor="owner")
    assert second_review["source_request_id"] == retry.request.request_id
    repository.resolve_human_gate(
        second_review["gate_id"], actor="owner", decision=HumanGateStatus.APPROVED
    )
    second_retry = AutonomyExecutionBinding(repository).bind(
        run.run_id, actor="owner"
    ).prepare("run", retry=True)
    assert second_retry.request.request_id != retry.request.request_id
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT review.source_request_id, consumption.retry_request_id
            FROM assistant_autonomy_retry_reviews AS review
            JOIN assistant_autonomy_retry_consumptions AS consumption
              ON consumption.retry_review_id = review.id
            ORDER BY review.id
            """
        ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        (initial.request.request_id, retry.request.request_id),
        (retry.request.request_id, second_retry.request.request_id),
    ]


@pytest.mark.parametrize(
    ("retry_outcome", "tick_outcome", "following_outcome"),
    (
        (
            ExecutionOutcome.FAILED,
            SupervisedTickOutcome.EXECUTED_FAILED,
            SupervisedTickOutcome.BLOCKED_PREVIOUS_RESULT,
        ),
        (
            ExecutionOutcome.SUCCEEDED,
            SupervisedTickOutcome.EXECUTED_SUCCEEDED,
            SupervisedTickOutcome.NOT_RUNNING,
        ),
    ),
)
def test_runner_executes_only_one_approved_retry_then_blocks_or_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retry_outcome: ExecutionOutcome,
    tick_outcome: SupervisedTickOutcome,
    following_outcome: SupervisedTickOutcome,
) -> None:
    _database, repository, run = _state(tmp_path)
    _failed_attempt(repository, run)
    runner = SupervisedAutonomyRunner(repository, actor="owner")
    assert runner.tick(run.run_id).outcome is SupervisedTickOutcome.BLOCKED_PREVIOUS_RESULT
    review = repository.request_retry_review(run.run_id, "run", actor="owner")
    repository.resolve_human_gate(
        review["gate_id"], actor="owner", decision=HumanGateStatus.APPROVED
    )
    calls: list[str] = []

    def execute(executor, prepared, *, cancellation=None):
        calls.append(prepared.request.request_id)
        receipt = repository._claim_execution_launch(
            prepared.request,
            actor="owner",
            runtime_seconds=prepared.reserved_runtime_seconds,
            retry=True,
            workspace_lease_receipt=prepared.workspace_session.receipt,
        )
        result = _result(prepared.request.request_id, retry_outcome)
        repository._record_execution_result(
            prepared.request, result, actor="owner", receipt=receipt
        )
        return result

    monkeypatch.setattr(BubblewrapExecutor, "execute", execute)
    monkeypatch.setattr(
        BubblewrapExecutor,
        "__init__",
        lambda self, *args, **kwargs: None,
    )
    assert runner.tick(run.run_id).outcome is tick_outcome
    assert len(calls) == 1
    assert runner.tick(run.run_id).outcome is following_outcome
    assert len(calls) == 1


def test_exact_schema_56_upgrade_preserves_data_without_fake_reviews(
    tmp_path: Path,
) -> None:
    database, repository, run = _state(tmp_path)
    source = _failed_attempt(repository, run)
    with database.connect() as connection:
        run_db_id = connection.execute(
            "SELECT id FROM assistant_autonomy_runs WHERE public_id = ?",
            (run.run_id,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO assistant_autonomy_human_gates(
                public_id, run_id, kind, status, reason, created_at,
                resolved_at, resolved_by
            ) VALUES (
                'schema56-gate', ?, 'review', 'approved', 'preserve exactly',
                '2026-01-02T03:04:05+00:00', '2026-01-02T03:05:06+00:00',
                'owner'
            )
            """,
            (run_db_id,),
        )
        gate_before = dict(
            connection.execute(
                "SELECT * FROM assistant_autonomy_human_gates "
                "WHERE public_id = 'schema56-gate'"
            ).fetchone()
        )
        connection.executescript(
            """
            DROP TABLE assistant_autonomy_mutation_results;
            DROP TABLE assistant_autonomy_mutation_attempt_files;
            DROP TABLE assistant_autonomy_mutation_attempts;
            DROP TABLE assistant_autonomy_mutation_gate_bindings;
            DROP TABLE assistant_autonomy_mutation_items;
            DROP TABLE assistant_autonomy_mutation_proposals;
            DROP TABLE assistant_cognitive_successor_handoffs;
            DROP TABLE assistant_cognitive_owner_waits;
            DROP TABLE assistant_cognitive_cycle_events;
            DROP TABLE assistant_cognitive_turns;
            DROP TABLE assistant_cognitive_cycles;
            DROP TABLE assistant_autonomy_retry_consumptions;
            DROP TABLE assistant_autonomy_retry_reviews;
            DROP INDEX idx_autonomy_gates_run;
            DROP INDEX idx_autonomy_one_pending_gate;
            DROP TRIGGER trg_autonomy_gate_identity_immutable;
            DROP TRIGGER trg_autonomy_gate_resolved_immutable;
            DROP TRIGGER trg_autonomy_gate_resolution_status;
            ALTER TABLE assistant_autonomy_human_gates RENAME TO gates57;
            CREATE TABLE assistant_autonomy_human_gates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT NOT NULL UNIQUE
                    CHECK(length(public_id) BETWEEN 1 AND 128),
                run_id INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN (
                    'approval', 'review', 'external_side_effect'
                )),
                status TEXT NOT NULL CHECK(status IN (
                    'pending', 'approved', 'rejected', 'cancelled'
                )),
                reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 2000),
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolved_by TEXT,
                FOREIGN KEY(run_id) REFERENCES assistant_autonomy_runs(id)
                    ON DELETE RESTRICT,
                CHECK((status='pending' AND resolved_at IS NULL
                       AND resolved_by IS NULL)
                   OR (status IN ('approved','rejected','cancelled')
                       AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL))
            );
            INSERT INTO assistant_autonomy_human_gates SELECT * FROM gates57;
            DROP TABLE gates57;
            UPDATE schema_meta SET value='56' WHERE key='schema_version';
            """
        )
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "56"
        for absent in (
            "assistant_autonomy_retry_reviews",
            "assistant_autonomy_retry_consumptions",
            "assistant_cognitive_owner_waits",
            "assistant_cognitive_successor_handoffs",
        ):
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name=?", (absent,)
            ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' "
            "AND sql LIKE '%assistant_autonomy_retry_reviews%'"
        ).fetchone() is None
    database.migrate()
    database.migrate()
    assert repository.get(run.run_id) is not None
    assert repository.execution_results(run.run_id, actor="owner")[0][
        "request_id"
    ] == source.request.request_id
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "60"
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_retry_reviews"
        ).fetchone()[0] == 0
        gate_after = dict(
            connection.execute(
                "SELECT * FROM assistant_autonomy_human_gates "
                "WHERE public_id = 'schema56-gate'"
            ).fetchone()
        )
        assert gate_after == gate_before
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize(
    "outcome",
    (ExecutionOutcome.FAILED, ExecutionOutcome.CANCELLED),
)
def test_non_process_step_cannot_request_retry_review_from_malformed_rows(
    tmp_path: Path,
    outcome: ExecutionOutcome,
) -> None:
    database, repository, run = _state(tmp_path)
    _failed_attempt(repository, run, outcome=outcome)
    with database.connect() as connection:
        connection.execute("DROP TRIGGER trg_autonomy_runs_authority_immutable")
        connection.execute(
            """
            UPDATE assistant_autonomy_runs
            SET plan_json = json_set(
                plan_json,
                '$.steps[0].capability',
                'workspace.read',
                '$.steps[0].command',
                NULL
            )
            WHERE public_id = ?
            """,
            (run.run_id,),
        )
    with pytest.raises(PermissionError, match="process.exec"):
        repository.request_retry_review(run.run_id, "run", actor="owner")


def test_malformed_retry_gate_audit_cannot_approve_normal_step(tmp_path: Path) -> None:
    database, repository, run = _state(tmp_path, requires_gate=True)
    item = repository.get(run.run_id)
    assert item is not None
    with database.connect() as connection:
        run_id = connection.execute(
            "SELECT id FROM assistant_autonomy_runs WHERE public_id=?",
            (run.run_id,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO assistant_autonomy_human_gates(
                public_id, run_id, kind, status, reason, created_at,
                resolved_at, resolved_by
            ) VALUES ('malformed-retry', ?, 'retry_review', 'approved',
                      'malformed', 'now', 'now', 'owner')
            """,
            (run_id,),
        )
        for sequence, event_type, from_status, to_status, payload in (
            (
                3,
                "human_gate_requested",
                "running",
                "waiting_human",
                '{"gate_id":"malformed-retry","kind":"retry_review"}',
            ),
            (
                4,
                "human_gate_approved",
                "waiting_human",
                "running",
                '{"decision":"approved","gate_id":"malformed-retry"}',
            ),
        ):
            connection.execute(
                """
                INSERT INTO assistant_autonomy_events(
                    run_id, sequence, event_type, from_status, to_status,
                    step_id, summary, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, 'run', 'malformed', ?, 'now')
                """,
                (run_id, sequence, event_type, from_status, to_status, payload),
            )
    contract = AutonomyExecutionBinding(repository).bind(run.run_id, actor="owner")
    with pytest.raises(PermissionError, match="HumanGate"):
        contract.prepare("run")


def test_observation_unresolved_denies_review(tmp_path: Path) -> None:
    _database, repository, run = _state(tmp_path)
    prepared = AutonomyExecutionBinding(repository).bind(
        run.run_id, actor="owner"
    ).prepare("run")
    repository._claim_execution_launch(
        prepared.request,
        actor="owner",
        runtime_seconds=prepared.reserved_runtime_seconds,
        retry=False,
        workspace_lease_receipt=prepared.workspace_session.receipt,
    )
    assert repository.execution_attempt_gaps(run.run_id, actor="owner")[0][
        "state"
    ] == "observation_unresolved"
    with pytest.raises(PermissionError, match="incompleto"):
        repository.request_retry_review(run.run_id, "run", actor="owner")


def test_later_step_cannot_be_selected_for_retry_review(tmp_path: Path) -> None:
    _database, repository, run = _state(tmp_path, two_steps=True)
    _failed_attempt(repository, run)
    with pytest.raises(PermissionError, match="primer step incompleto"):
        repository.request_retry_review(run.run_id, "later", actor="owner")


@pytest.mark.parametrize(
    ("max_commands", "max_retries", "max_runtime_seconds"),
    ((1, 1, 6), (2, 0, 6), (2, 1, 5)),
)
def test_exhausted_budget_denies_review_without_mutation(
    tmp_path: Path,
    max_commands: int,
    max_retries: int,
    max_runtime_seconds: int,
) -> None:
    _database, repository, run = _state(
        tmp_path,
        max_commands=max_commands,
        max_retries=max_retries,
        max_runtime_seconds=max_runtime_seconds,
    )
    _failed_attempt(repository, run)
    before = repository.execution_budget(run.run_id, actor="owner").snapshot()
    with pytest.raises(PermissionError):
        repository.request_retry_review(run.run_id, "run", actor="owner")
    assert repository.execution_budget(run.run_id, actor="owner").snapshot() == before


def test_pending_and_rejected_reviews_cannot_authorize_retry(tmp_path: Path) -> None:
    _database, repository, run = _state(tmp_path)
    _failed_attempt(repository, run)
    review = repository.request_retry_review(run.run_id, "run", actor="owner")
    with pytest.raises(PermissionError):
        AutonomyExecutionBinding(repository).bind(run.run_id, actor="owner")
    repository.resolve_human_gate(
        review["gate_id"], actor="owner", decision=HumanGateStatus.REJECTED
    )
    with pytest.raises(PermissionError):
        AutonomyExecutionBinding(repository).bind(run.run_id, actor="owner")


def test_approval_and_consumption_require_still_active_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, repository, run = _state(tmp_path)
    _failed_attempt(repository, run)
    review = repository.request_retry_review(run.run_id, "run", actor="owner")
    future = datetime.now(UTC) + timedelta(days=1)
    monkeypatch.setattr("elyndra.autonomy.repository._utcnow", lambda: future)
    with pytest.raises(PermissionError, match="expirado"):
        repository.resolve_human_gate(
            review["gate_id"], actor="owner", decision=HumanGateStatus.APPROVED
        )
    monkeypatch.undo()
    repository.resolve_human_gate(
        review["gate_id"], actor="owner", decision=HumanGateStatus.APPROVED
    )
    contract = AutonomyExecutionBinding(repository).bind(run.run_id, actor="owner")
    monkeypatch.setattr("elyndra.autonomy.repository._utcnow", lambda: future)
    with pytest.raises(PermissionError, match="expirado"):
        contract.prepare("run", retry=True)
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_retry_consumptions"
        ).fetchone()[0] == 0


def test_retry_gate_without_linkage_cannot_be_resolved(tmp_path: Path) -> None:
    database, repository, run = _state(tmp_path)
    with database.connect() as connection:
        run_db_id = connection.execute(
            "SELECT id FROM assistant_autonomy_runs WHERE public_id=?", (run.run_id,)
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO assistant_autonomy_human_gates(
                public_id, run_id, kind, status, reason, created_at
            ) VALUES ('orphan-retry', ?, 'retry_review', 'pending', 'orphan', 'now')
            """,
            (run_db_id,),
        )
        connection.execute(
            "UPDATE assistant_autonomy_runs SET status='waiting_human' WHERE id=?",
            (run_db_id,),
        )
    with pytest.raises(PermissionError, match="linkage"):
        repository.resolve_human_gate(
            "orphan-retry", actor="owner", decision=HumanGateStatus.APPROVED
        )


def test_consumption_insert_failure_rolls_back_reservation_and_budget(
    tmp_path: Path,
) -> None:
    database, repository, run = _state(tmp_path)
    _failed_attempt(repository, run)
    review = repository.request_retry_review(run.run_id, "run", actor="owner")
    repository.resolve_human_gate(
        review["gate_id"], actor="owner", decision=HumanGateStatus.APPROVED
    )
    before = repository.execution_budget(run.run_id, actor="owner").snapshot()
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER force_retry_consumption_failure
            BEFORE INSERT ON assistant_autonomy_retry_consumptions
            BEGIN SELECT RAISE(ABORT, 'forced_consumption_failure'); END
            """
        )
    with pytest.raises(sqlite3.IntegrityError, match="forced_consumption_failure"):
        AutonomyExecutionBinding(repository).bind(
            run.run_id, actor="owner"
        ).prepare("run", retry=True)
    assert repository.execution_budget(run.run_id, actor="owner").snapshot() == before
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_retry_consumptions"
        ).fetchone()[0] == 0
