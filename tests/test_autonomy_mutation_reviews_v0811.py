from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import elyndra.autonomy.repository as autonomy_repository
from elyndra.autonomy import (
    AutonomyExecutionBinding,
    AutonomyRepository,
    AutonomyRun,
    Capability,
    CapabilityGrant,
    CommandSpec,
    ExecutionOutcome,
    ExecutionRequest,
    ExecutionResult,
    HumanGateKind,
    HumanGateStatus,
    MutationItem,
    MutationOperation,
    MutationProposal,
    RunPlan,
    RunStep,
    SupervisedAutonomyRunner,
    SupervisedTickOutcome,
    WorkspaceLeaseCoordinator,
    WorkspaceScope,
)
from elyndra.cognitive_loop import LocalCognitiveActionLoop
from elyndra.db import Database


def _state(
    tmp_path: Path,
    *,
    actor: str = "owner",
    grant_minutes: int = 60,
) -> tuple[Database, AutonomyRepository, AutonomyRun, MutationProposal, object]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    now = datetime.now(UTC)
    run = AutonomyRun(
        actor=actor,
        workspace=WorkspaceScope.from_root(workspace),
        grant=CapabilityGrant(
            capabilities=frozenset({Capability.SELF_MODIFY}),
            issued_at=now,
            expires_at=now + timedelta(minutes=grant_minutes),
            max_steps=1,
        ),
        plan=RunPlan(
            objective="Review exact mutation",
            steps=(
                RunStep(
                    step_id="mutate",
                    capability=Capability.SELF_MODIFY,
                    action="propose exact mutation",
                ),
            ),
        ),
    )
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    repository.create(run)
    item = MutationItem(
        "src/new.py",
        MutationOperation.CREATE,
        False,
        None,
        None,
        b"print('bounded review')\n",
    )
    proposal = MutationProposal(
        run_id=run.run_id,
        step_id="mutate",
        actor=actor,
        workspace_root=str(run.workspace.root),
        items=(item,),
        created_at=now,
        expires_at=now + timedelta(minutes=20),
    )
    persisted = repository.create_mutation_proposal(
        proposal,
        request_key="mutation-review-proposal",
        actor=actor,
    )
    repository.transition(run.run_id, "running", actor=actor, summary="start")
    return database, repository, run, proposal, persisted


def _request(
    repository: AutonomyRepository,
    persisted: object,
    *,
    actor: str = "owner",
):
    return repository.request_mutation_review(
        persisted.public_id,  # type: ignore[attr-defined]
        persisted.proposal.proposal_sha256,  # type: ignore[attr-defined]
        actor=actor,
    )


def _track_begin_immediate(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, bool]:
    state = {"locked": False}
    original_connect = database.connect

    class _TrackedConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args: object):
            return self.connection.__exit__(*args)

        def execute(self, sql: str, *args: object):
            result = self.connection.execute(sql, *args)
            if sql == "BEGIN IMMEDIATE":
                state["locked"] = True
            return result

        def __getattr__(self, name: str):
            return getattr(self.connection, name)

    monkeypatch.setattr(
        database,
        "connect",
        lambda: _TrackedConnection(original_connect()),
    )
    return state


def _resolve(
    repository: AutonomyRepository,
    persisted: object,
    gate_id: str,
    decision: HumanGateStatus | str,
    *,
    actor: str = "owner",
):
    return repository.resolve_mutation_review(
        persisted.public_id,  # type: ignore[attr-defined]
        persisted.proposal.proposal_sha256,  # type: ignore[attr-defined]
        gate_id,
        actor=actor,
        decision=decision,
    )


def test_request_is_atomic_exact_bounded_and_idempotent(tmp_path: Path) -> None:
    database, repository, run, _proposal, persisted = _state(tmp_path)
    review = _request(repository, persisted)
    replay = _request(repository, persisted)

    assert replay == review
    assert review.proposal_id == persisted.public_id
    assert review.proposal_sha256 == persisted.proposal.proposal_sha256
    assert review.run_id == run.run_id
    assert review.step_id == "mutate"
    assert review.actor == "owner"
    assert review.gate_status == "pending"
    assert repository.get(run.run_id)["status"] == "waiting_human"  # type: ignore[index]

    with database.connect() as connection:
        binding = connection.execute(
            "SELECT * FROM assistant_autonomy_mutation_gate_bindings"
        ).fetchone()
        gate = connection.execute(
            "SELECT * FROM assistant_autonomy_human_gates WHERE public_id=?",
            (review.gate_id,),
        ).fetchone()
        event = connection.execute(
            "SELECT * FROM assistant_autonomy_events "
            "WHERE event_type='mutation_review_requested'"
        ).fetchone()
        payload = json.loads(str(event["payload_json"]))
        assert binding is not None and gate is not None
        assert binding["proposal_public_id"] == review.proposal_id
        assert binding["proposal_sha256"] == review.proposal_sha256
        assert binding["gate_id"] == review.gate_id
        assert binding["run_id"] == gate["run_id"]
        assert binding["step_id"] == "mutate"
        assert binding["actor"] == "owner"
        assert payload == {
            "gate_id": review.gate_id,
            "proposal_id": review.proposal_id,
            "proposal_sha256": review.proposal_sha256,
            "state": "pending",
            "step_id": "mutate",
        }
        serialized = json.dumps(payload)
        assert "src/new.py" not in serialized
        assert "bounded review" not in serialized
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_execution_reservations"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_execution_launches"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_execution_results"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_attempts"
        ).fetchone()[0] == 0


def test_review_authority_time_is_captured_after_write_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, repository, _run, _proposal, persisted = _state(tmp_path)
    state = _track_begin_immediate(database, monkeypatch)

    def post_lock_now() -> datetime:
        assert state["locked"]
        return datetime.now(UTC)

    monkeypatch.setattr(autonomy_repository, "_utcnow", post_lock_now)
    assert _request(repository, persisted).gate_status == "pending"


def test_new_review_uses_expired_post_lock_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, repository, run, proposal, persisted = _state(tmp_path)
    state = _track_begin_immediate(database, monkeypatch)

    def expired_post_lock_now() -> datetime:
        assert state["locked"]
        return max(proposal.expires_at, run.grant.expires_at) + timedelta(seconds=1)

    monkeypatch.setattr(autonomy_repository, "_utcnow", expired_post_lock_now)
    with pytest.raises(PermissionError, match="expirada"):
        _request(repository, persisted)
    assert repository.get(run.run_id)["status"] == "running"  # type: ignore[index]
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_gate_bindings"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_human_gates"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_events "
            "WHERE event_type='mutation_review_requested'"
        ).fetchone()[0] == 0


def test_proposal_creation_authority_time_is_captured_after_write_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, repository, run, _proposal, _persisted = _state(tmp_path)
    now = datetime.now(UTC)
    proposal = MutationProposal(
        run_id=run.run_id,
        step_id="mutate",
        actor="owner",
        workspace_root=str(run.workspace.root),
        items=(
            MutationItem(
                "src/second.py",
                MutationOperation.CREATE,
                False,
                None,
                None,
                b"print('second')\n",
            ),
        ),
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    state = _track_begin_immediate(database, monkeypatch)

    def expired_post_lock_now() -> datetime:
        assert state["locked"]
        return proposal.expires_at + timedelta(seconds=1)

    monkeypatch.setattr(autonomy_repository, "_utcnow", expired_post_lock_now)
    with pytest.raises(PermissionError, match="expiró"):
        repository.create_mutation_proposal(
            proposal,
            request_key="post-lock-expired",
            actor="owner",
        )


def test_review_requires_exact_first_incomplete_plan_step(tmp_path: Path) -> None:
    workspace = tmp_path / "ordered-workspace"
    workspace.mkdir()
    executable = str(Path(sys.executable).resolve(strict=True))
    now = datetime.now(UTC)
    process = RunStep(
        step_id="inspect",
        capability=Capability.PROCESS_EXEC,
        action="inspect first",
        target=".",
        command=CommandSpec(
            executable=executable,
            argv=(executable, "-c", "print('ok')"),
            cwd=".",
            timeout_seconds=3,
        ),
    )
    mutate = RunStep(
        step_id="mutate",
        capability=Capability.SELF_MODIFY,
        action="propose exact mutation",
    )
    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(workspace),
        grant=CapabilityGrant(
            capabilities=frozenset(
                {Capability.PROCESS_EXEC, Capability.SELF_MODIFY}
            ),
            issued_at=now,
            expires_at=now + timedelta(hours=1),
            max_steps=2,
            max_commands=1,
            max_runtime_seconds=3,
            allowed_executables=(executable,),
        ),
        plan=RunPlan(objective="ordered mutation", steps=(process, mutate)),
    )
    database = Database(tmp_path / "ordered-vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    repository.create(run)
    proposal = MutationProposal(
        run_id=run.run_id,
        step_id="mutate",
        actor="owner",
        workspace_root=str(run.workspace.root),
        items=(
            MutationItem(
                "src/ordered.py",
                MutationOperation.CREATE,
                False,
                None,
                None,
                b"print('ordered')\n",
            ),
        ),
        created_at=now,
        expires_at=now + timedelta(minutes=20),
    )
    persisted = repository.create_mutation_proposal(
        proposal,
        request_key="ordered-proposal",
        actor="owner",
    )
    repository.transition(run.run_id, "running", actor="owner", summary="start")

    with pytest.raises(PermissionError, match="primer step incompleto"):
        _request(repository, persisted)
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_gate_bindings"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_human_gates"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_events "
            "WHERE event_type='mutation_review_requested'"
        ).fetchone()[0] == 0
    assert repository.get(run.run_id)["status"] == "running"  # type: ignore[index]

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(mode=0o700)
    prepared = AutonomyExecutionBinding(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime_root),
    ).bind(
        run.run_id,
        actor="owner",
    ).prepare("inspect")
    receipt = repository._claim_execution_launch(
        prepared.request,
        actor="owner",
        runtime_seconds=prepared.reserved_runtime_seconds,
        retry=False,
        workspace_lease_receipt=prepared.workspace_session.receipt,
    )
    repository._record_execution_result(
        prepared.request,
        ExecutionResult(
            request_id=prepared.request.request_id,
            outcome=ExecutionOutcome.SUCCEEDED,
            summary="durable success",
            exit_code=0,
        ),
        actor="owner",
        receipt=receipt,
    )
    prepared.close_workspace_session()
    assert _request(repository, persisted).gate_status == "pending"


def test_generic_gate_apis_and_cancellation_cannot_bypass_review(
    tmp_path: Path,
) -> None:
    database, repository, run, _proposal, persisted = _state(tmp_path)
    with pytest.raises(PermissionError, match="especializado"):
        repository.request_human_gate(
            run.run_id,
            actor="owner",
            reason="invalid generic path",
            kind=HumanGateKind.MUTATION_REVIEW,
        )
    review = _request(repository, persisted)
    with pytest.raises(PermissionError, match="especializada"):
        repository.resolve_human_gate(
            review.gate_id,
            actor="owner",
            decision="approved",
        )
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(PermissionError, match="resolución especializada"):
            repository._cancel_run_connection(
                connection,
                run.run_id,
                actor="owner",
                summary="generic cancel",
            )

    approved = _resolve(repository, persisted, review.gate_id, "approved")
    assert approved.gate_status == "approved"
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(PermissionError, match="resolución especializada"):
            repository._cancel_run_connection(
                connection,
                run.run_id,
                actor="owner",
                summary="generic cancel after approval",
            )


def test_approval_wait_barrier_blocks_runner_and_execution_reservation(
    tmp_path: Path,
) -> None:
    _database, repository, run, _proposal, persisted = _state(tmp_path)
    review = _request(repository, persisted)
    approved = _resolve(repository, persisted, review.gate_id, "approved")

    assert approved.gate_status == "approved"
    assert repository.get(run.run_id)["status"] == "waiting_human"  # type: ignore[index]
    tick = SupervisedAutonomyRunner(repository, actor="owner").tick(run.run_id)
    assert tick.outcome is SupervisedTickOutcome.NOT_RUNNING
    request = ExecutionRequest(
        run_id=run.run_id,
        step_id="mutate",
        capability=Capability.SELF_MODIFY,
        action="propose exact mutation",
        target="",
        requires_human_gate=False,
    )
    with pytest.raises(PermissionError, match="running"):
        repository.reserve_execution(request, actor="owner")


def test_cognitive_ordinary_continuation_rejects_mutation_gate(tmp_path: Path) -> None:
    database, repository, run, _proposal, persisted = _state(tmp_path)
    review = _request(repository, persisted)
    _resolve(repository, persisted, review.gate_id, "approved")
    now = datetime.now(UTC).isoformat()
    with database.connect() as connection:
        run_id = connection.execute(
            "SELECT id FROM assistant_autonomy_runs WHERE public_id=?",
            (run.run_id,),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO assistant_cognitive_cycles(
                   public_id, autonomy_run_id, actor, status, max_advances,
                   max_model_calls, max_replans, max_actions, created_at, updated_at
               ) VALUES ('cycle-mutation-guard', ?, 'owner', 'waiting_owner',
                         12, 8, 2, 4, ?, ?)""",
            (run_id, now, now),
        )
        cycle_id = connection.execute(
            "SELECT id FROM assistant_cognitive_cycles "
            "WHERE public_id='cycle-mutation-guard'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO assistant_cognitive_turns(
                   public_id, cycle_id, sequence, kind, state, decision,
                   disposition, step_id, source_request_id, created_at,
                   completed_at, abandoned_at
               ) VALUES ('turn-mutation-guard', ?, 1, 'act', 'completed', NULL,
                         'authority_blocked', 'mutate', NULL, ?, ?, NULL)""",
            (cycle_id, now, now),
        )
        turn_id = connection.execute(
            "SELECT id FROM assistant_cognitive_turns "
            "WHERE public_id='turn-mutation-guard'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO assistant_cognitive_cycle_events(
                   public_id, cycle_id, sequence, turn_id, event_type,
                   from_status, to_status, step_id, source_request_id, gate_id,
                   summary_code, payload_json, created_at
               ) VALUES ('event-mutation-guard', ?, 1, ?, 'action_blocked',
                         'action_reserved', 'waiting_owner', 'mutate', NULL, ?,
                         'authority_blocked', '{}', ?)""",
            (cycle_id, turn_id, review.gate_id, now),
        )
        connection.execute(
            """INSERT INTO assistant_cognitive_owner_waits(
                   public_id, cycle_id, sequence, source_turn_id, reason, gate_id,
                   source_request_id, state, resolution, owner_context,
                   resumed_turn_id, created_at, resolved_at, resolved_by
               ) VALUES ('wait-mutation-guard', ?, 1, ?, 'ordinary_gate_required', ?,
                         NULL, 'pending', NULL, NULL, NULL, ?, NULL, NULL)""",
            (cycle_id, turn_id, review.gate_id, now),
        )
        # Isolate the kind guard from the independent WAITING_HUMAN run barrier.
        connection.execute(
            "UPDATE assistant_autonomy_runs SET status='running' WHERE id=?",
            (run_id,),
        )

    loop = object.__new__(LocalCognitiveActionLoop)
    loop.database = database
    loop.autonomy = repository
    with pytest.raises(PermissionError, match="HumanGate exacto"):
        loop.continue_after_ordinary_gate("wait-mutation-guard", actor="owner")


@pytest.mark.parametrize("decision", ["rejected", "cancelled"])
def test_reject_or_cancel_is_terminal_and_exact_replay_is_read_only(
    tmp_path: Path,
    decision: str,
) -> None:
    database, repository, run, _proposal, persisted = _state(tmp_path)
    review = _request(repository, persisted)
    resolved = _resolve(repository, persisted, review.gate_id, decision)
    replay = _resolve(repository, persisted, review.gate_id, decision)

    assert replay == resolved
    assert resolved.gate_status == decision
    assert repository.get(run.run_id)["status"] == "cancelled"  # type: ignore[index]
    with pytest.raises(PermissionError, match="otra decisión"):
        _resolve(repository, persisted, review.gate_id, "approved")
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_events WHERE event_type=?",
            (f"mutation_review_{decision}",),
        ).fetchone()[0] == 1


def test_exact_approval_replay_precedes_later_expiry(tmp_path: Path) -> None:
    database, repository, _run, proposal, persisted = _state(tmp_path)
    review = _request(repository, persisted)
    approved = _resolve(repository, persisted, review.gate_id, "approved")
    later = proposal.expires_at + timedelta(hours=2)
    original_now = autonomy_repository._utcnow
    autonomy_repository._utcnow = lambda: later
    try:
        assert _request(repository, persisted) == approved
        assert _resolve(repository, persisted, review.gate_id, "approved") == approved
    finally:
        autonomy_repository._utcnow = original_now
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_events "
            "WHERE event_type='mutation_review_approved'"
        ).fetchone()[0] == 1


@pytest.mark.parametrize("expiry_kind", ["proposal", "grant"])
def test_pending_approval_requires_live_proposal_and_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expiry_kind: str,
) -> None:
    _database, repository, run, proposal, persisted = _state(tmp_path)
    review = _request(repository, persisted)
    if expiry_kind == "proposal":
        later = proposal.expires_at + timedelta(seconds=1)
        expected = "propuesta de mutación expiró"
    else:
        later = run.grant.expires_at + timedelta(seconds=1)
        expected = "Capability no concedida o expirada"
    monkeypatch.setattr(autonomy_repository, "_utcnow", lambda: later)
    with pytest.raises(PermissionError, match=expected):
        _resolve(repository, persisted, review.gate_id, "approved")


def test_wrong_identity_and_conflicting_resolution_fail_closed(tmp_path: Path) -> None:
    _database, repository, _run, _proposal, persisted = _state(tmp_path)
    review = _request(repository, persisted)
    bad_hash = hashlib.sha256(b"different").hexdigest()
    with pytest.raises(PermissionError, match="actor"):
        repository.request_mutation_review(
            persisted.public_id,
            persisted.proposal.proposal_sha256,
            actor="intruder",
        )
    with pytest.raises(PermissionError, match="SHA-256"):
        repository.resolve_mutation_review(
            persisted.public_id,
            bad_hash,
            review.gate_id,
            actor="owner",
            decision="approved",
        )
    with pytest.raises(PermissionError, match="HumanGate"):
        _resolve(repository, persisted, "wrong-gate", "approved")
    _resolve(repository, persisted, review.gate_id, "approved")
    with pytest.raises(PermissionError, match="otra decisión"):
        _resolve(repository, persisted, review.gate_id, "rejected")


def test_binding_is_append_only_unique_and_trigger_checks_lineage(tmp_path: Path) -> None:
    database, repository, _run, _proposal, persisted = _state(tmp_path)
    review = _request(repository, persisted)
    with database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            connection.execute(
                "UPDATE assistant_autonomy_mutation_gate_bindings SET actor='x'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            connection.execute("DELETE FROM assistant_autonomy_mutation_gate_bindings")
        row = connection.execute(
            "SELECT * FROM assistant_autonomy_mutation_gate_bindings"
        ).fetchone()
        assert row is not None
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO assistant_autonomy_mutation_gate_bindings(
                       proposal_id, proposal_public_id, proposal_sha256, gate_id,
                       run_id, step_id, actor, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["proposal_id"],
                    row["proposal_public_id"],
                    row["proposal_sha256"],
                    row["gate_id"],
                    row["run_id"],
                    row["step_id"],
                    row["actor"],
                    row["created_at"],
                ),
            )
        connection.execute(
            "UPDATE assistant_autonomy_human_gates SET status='approved', "
            "resolved_at=?, resolved_by='owner' WHERE public_id=?",
            (datetime.now(UTC).isoformat(), review.gate_id),
        )
        with pytest.raises(sqlite3.IntegrityError, match="binding_invalid"):
            connection.execute(
                """INSERT INTO assistant_autonomy_mutation_gate_bindings(
                       proposal_id, proposal_public_id, proposal_sha256, gate_id,
                       run_id, step_id, actor, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["proposal_id"],
                    "wrong-proposal",
                    row["proposal_sha256"],
                    review.gate_id,
                    row["run_id"],
                    row["step_id"],
                    row["actor"],
                    row["created_at"],
                ),
            )


def test_schema_60_extension_is_vault_only_idempotent_and_fk_clean(
    tmp_path: Path,
) -> None:
    root = Database(tmp_path / "root.sqlite3", role="root")
    vault = Database(tmp_path / "vault.sqlite3", role="vault")
    root.migrate()
    vault.migrate()
    vault.migrate()
    with root.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE name='assistant_autonomy_mutation_gate_bindings'"
        ).fetchone() is None
    with vault.connect() as connection:
        gate_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='assistant_autonomy_human_gates'"
        ).fetchone()[0]
        assert "'mutation_review'" in gate_sql
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_phase9a2_schema60_upgrade_preserves_gate_and_cognitive_fk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extension = Database._extend_human_gate_kinds_phase9a
    monkeypatch.setattr(
        Database,
        "_extend_human_gate_kinds_phase9a",
        staticmethod(lambda _connection: None),
    )
    database, repository, run, _proposal, _persisted = _state(tmp_path)
    repository.request_human_gate(
        run.run_id,
        actor="owner",
        reason="preserve ordinary gate",
        kind=HumanGateKind.APPROVAL,
        step_id="mutate",
    )
    with database.connect() as connection:
        gate_id = connection.execute(
            "SELECT public_id FROM assistant_autonomy_human_gates"
        ).fetchone()[0]
        run_id = connection.execute(
            "SELECT id FROM assistant_autonomy_runs WHERE public_id=?",
            (run.run_id,),
        ).fetchone()[0]
        connection.execute("DROP TRIGGER trg_cognitive_wait_provenance_insert")
        connection.execute(
            """INSERT INTO assistant_cognitive_cycles(
                   public_id, autonomy_run_id, actor, status, max_advances,
                   max_model_calls, max_replans, max_actions, created_at, updated_at
               ) VALUES ('cycle-preserved', ?, 'owner', 'waiting_owner',
                         12, 8, 2, 4, '2026-09-12T00:00:00+00:00',
                         '2026-09-12T00:00:00+00:00')""",
            (run_id,),
        )
        cycle_id = connection.execute(
            "SELECT id FROM assistant_cognitive_cycles WHERE public_id='cycle-preserved'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO assistant_cognitive_owner_waits(
                   public_id, cycle_id, sequence, source_turn_id, reason, gate_id,
                   source_request_id, state, resolution, owner_context,
                   resumed_turn_id, created_at, resolved_at, resolved_by
               ) VALUES ('wait-preserved', ?, 1, NULL, 'ordinary_gate_required', ?,
                         NULL, 'pending', NULL, NULL, NULL,
                         '2026-09-12T00:00:00+00:00', NULL, NULL)""",
            (cycle_id, gate_id),
        )
        connection.execute("DROP TABLE assistant_autonomy_mutation_gate_bindings")

    monkeypatch.setattr(
        Database,
        "_extend_human_gate_kinds_phase9a",
        staticmethod(extension),
    )
    database.migrate()
    database.migrate()

    with database.connect() as connection:
        assert connection.execute(
            "SELECT kind FROM assistant_autonomy_human_gates WHERE public_id=?",
            (gate_id,),
        ).fetchone()[0] == "approval"
        assert connection.execute(
            "SELECT gate_id FROM assistant_cognitive_owner_waits "
            "WHERE public_id='wait-preserved'"
        ).fetchone()[0] == gate_id
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_proposal_commitment_is_unchanged_by_review(tmp_path: Path) -> None:
    _database, repository, _run, proposal, persisted = _state(tmp_path)
    before = proposal.proposal_sha256
    review = _request(repository, persisted)
    _resolve(repository, persisted, review.gate_id, "approved")
    loaded = repository.mutation_proposal(persisted.public_id, actor="owner")
    assert loaded is not None
    assert loaded.proposal == proposal
    assert loaded.proposal.proposal_sha256 == before


def test_attempt_foundation_blocks_illegal_transition_and_cancellation(
    tmp_path: Path,
) -> None:
    database, repository, run, _proposal, persisted = _state(tmp_path)
    review = _request(repository, persisted)
    _resolve(repository, persisted, review.gate_id, "approved")
    with database.connect() as connection:
        proposal_id = connection.execute(
            "SELECT id FROM assistant_autonomy_mutation_proposals WHERE public_id=?",
            (persisted.public_id,),
        ).fetchone()[0]
        binding_id = connection.execute(
            "SELECT id FROM assistant_autonomy_mutation_gate_bindings WHERE gate_id=?",
            (review.gate_id,),
        ).fetchone()[0]
        run_id = connection.execute(
            "SELECT id FROM assistant_autonomy_runs WHERE public_id=?",
            (run.run_id,),
        ).fetchone()[0]
        metadata = run.workspace.root.stat()
        connection.execute(
            """INSERT INTO assistant_autonomy_mutation_attempts(
                public_id,request_key,proposal_id,binding_id,gate_id,
                proposal_public_id,proposal_sha256,run_id,step_id,actor,
                workspace_root,workspace_st_dev,workspace_st_ino,
                workspace_mount_id,state,claimed_at,state_updated_at,
                initial_blockade_sha256,initial_manifest_sha256,
                manifest_sequence,manifest_tail_sha256
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'claimed',?,?,?,?,0,?)""",
            (
                "attempt-foundation",
                "apply-foundation",
                proposal_id,
                binding_id,
                review.gate_id,
                persisted.public_id,
                persisted.proposal.proposal_sha256,
                run_id,
                "mutate",
                "owner",
                str(run.workspace.root),
                metadata.st_dev,
                metadata.st_ino,
                1,
                review.created_at.isoformat(),
                review.created_at.isoformat(),
                "1" * 64,
                "2" * 64,
                "2" * 64,
            ),
        )
        with pytest.raises(sqlite3.IntegrityError, match="transition_invalid"):
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempts "
                "SET state='publishing',publication_started_at=? WHERE public_id=?",
                (datetime.now(UTC).isoformat(), "attempt-foundation"),
            )
        for terminal in ("expired", "stale", "failed_before_publication"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE assistant_autonomy_mutation_attempts "
                    "SET state=?,terminal_at=? WHERE public_id=?",
                    (terminal, datetime.now(UTC).isoformat(), "attempt-foundation"),
                )
        with pytest.raises(sqlite3.IntegrityError, match="identity_immutable"):
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempts SET actor='other' "
                "WHERE public_id='attempt-foundation'"
            )
    with pytest.raises(PermissionError, match="liberación especializada"):
        repository.transition(
            run.run_id,
            "cancelled",
            actor="owner",
            summary="must not cancel",
        )
    assert not hasattr(repository, "claim_mutation_application")


def test_attempt_result_counts_and_stage_inode_write_once(tmp_path: Path) -> None:
    database, repository, run, _proposal, persisted = _state(tmp_path)
    review = _request(repository, persisted)
    _resolve(repository, persisted, review.gate_id, "approved")
    with database.connect() as connection:
        proposal_row = connection.execute(
            "SELECT id FROM assistant_autonomy_mutation_proposals WHERE public_id=?",
            (persisted.public_id,),
        ).fetchone()
        proposal_id = proposal_row[0]
        item_id = connection.execute(
            "SELECT id FROM assistant_autonomy_mutation_items WHERE proposal_id=?",
            (proposal_id,),
        ).fetchone()[0]
        binding_id = connection.execute(
            "SELECT id FROM assistant_autonomy_mutation_gate_bindings WHERE gate_id=?",
            (review.gate_id,),
        ).fetchone()[0]
        run_id = connection.execute(
            "SELECT id FROM assistant_autonomy_runs WHERE public_id=?", (run.run_id,)
        ).fetchone()[0]
        metadata = run.workspace.root.stat()
        connection.execute(
            """INSERT INTO assistant_autonomy_mutation_attempts(
                public_id,request_key,proposal_id,binding_id,gate_id,
                proposal_public_id,proposal_sha256,run_id,step_id,actor,
                workspace_root,workspace_st_dev,workspace_st_ino,
                workspace_mount_id,state,claimed_at,state_updated_at,
                initial_blockade_sha256,initial_manifest_sha256,
                manifest_sequence,manifest_tail_sha256
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'claimed',?,?,?,?,0,?)""",
            (
                "attempt-stage", "apply-stage", proposal_id, binding_id, review.gate_id,
                persisted.public_id, persisted.proposal.proposal_sha256, run_id, "mutate",
                "owner", str(run.workspace.root), metadata.st_dev, metadata.st_ino, 1,
                review.created_at.isoformat(), review.created_at.isoformat(), "1" * 64,
                "2" * 64, "2" * 64,
            ),
        )
        attempt_id = connection.execute(
            "SELECT id FROM assistant_autonomy_mutation_attempts WHERE public_id='attempt-stage'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO assistant_autonomy_mutation_attempt_files(
                attempt_id,proposal_item_id,ordinal,relative_path,operation,
                expected_preimage_sha256,expected_preimage_size,
                expected_postimage_sha256,expected_postimage_size,
                parent_st_dev,parent_st_ino,parent_mount_id,artifact_name,witness_name,
                state,state_updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                attempt_id, item_id, 0, "src/new.py", "create", None, None,
                persisted.proposal.items[0].proposed_sha256,
                persisted.proposal.items[0].proposed_size,
                metadata.st_dev, metadata.st_ino, 1, "stage", "witness", "planned",
                review.created_at.isoformat(),
            ),
        )
        with pytest.raises(sqlite3.IntegrityError, match="stage_immutable"):
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempt_files "
                "SET stage_st_dev=? WHERE attempt_id=?",
                (metadata.st_dev, attempt_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="stage_required"):
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempt_files "
                "SET state='staged',state_updated_at=? WHERE attempt_id=?",
                (datetime.now(UTC).isoformat(), attempt_id),
            )
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempt_files "
            "SET stage_st_dev=?,stage_st_ino=?,state='staged',state_updated_at=? "
            "WHERE attempt_id=?",
            (metadata.st_dev, 999, datetime.now(UTC).isoformat(), attempt_id),
        )
        with pytest.raises(sqlite3.IntegrityError, match="stage_immutable"):
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempt_files SET stage_st_ino=1000 "
                "WHERE attempt_id=?",
                (attempt_id,),
            )

        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempts "
            "SET state='recovery_required' WHERE id=?",
            (attempt_id,),
        )
        connection.execute(
            """INSERT INTO assistant_autonomy_mutation_results(
                public_id,attempt_id,outcome,published_count,restored_count,
                final_manifest_sequence,final_manifest_sha256,summary,observed_at
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                "result-rollback", attempt_id, "rolled_back", 3, 3, 1, "3" * 64,
                "rollback complete", datetime.now(UTC).isoformat(),
            ),
        )
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempts SET state='cleanup_pending' "
            "WHERE id=?",
            (attempt_id,),
        )
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempts "
            "SET state='rolled_back',terminal_at=? WHERE id=?",
            (datetime.now(UTC).isoformat(), attempt_id),
        )
