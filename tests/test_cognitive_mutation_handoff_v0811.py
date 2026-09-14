from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_cognitive_action_loop_v0810 import _Engine

from elyndra.autonomy import (
    AutonomyRepository,
    AutonomyRun,
    Capability,
    CapabilityGrant,
    CommandSpec,
    HumanGateStatus,
    MutationItem,
    MutationOperation,
    MutationProposal,
    RunPlan,
    RunStep,
    SupervisedAutonomyRunner,
    SupervisedTickOutcome,
    WorkspaceLeaseCoordinator,
    WorkspaceLeaseMode,
    WorkspaceScope,
)
from elyndra.autonomy.mutation_application import MutationApplicator
from elyndra.cognitive_loop import LocalCognitiveActionLoop
from elyndra.db import Database
from elyndra.memory import (
    MemoryLifecycleRepository,
    MemoryRepository,
    TieredMemoryRepository,
)


def _state(tmp_path: Path, *, final_mutation: bool = False):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "src").mkdir()
    executable = str(Path(sys.executable).resolve(strict=True))
    now = datetime.now(UTC)
    mutate = RunStep(
        "mutate",
        Capability.SELF_MODIFY,
        "apply reviewed mutation",
        requires_human_gate=True,
    )
    process = RunStep(
        "verify",
        Capability.PROCESS_EXEC,
        "verify later",
        target=".",
        command=CommandSpec(
            executable=executable,
            argv=(executable, "-c", "print('verified')"),
            cwd=".",
            timeout_seconds=3,
        ),
    )
    steps = (mutate,) if final_mutation else (mutate, process)
    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(workspace),
        grant=CapabilityGrant(
            capabilities=frozenset({Capability.SELF_MODIFY, Capability.PROCESS_EXEC}),
            issued_at=now,
            expires_at=now + timedelta(hours=1),
            max_steps=4,
            max_commands=4,
            max_runtime_seconds=12,
            allowed_executables=(executable,),
        ),
        plan=RunPlan(objective="mixed exact plan", steps=steps),
    )
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    repository.create(run)
    proposal = MutationProposal(
        run_id=run.run_id,
        step_id="mutate",
        actor="owner",
        workspace_root=str(workspace),
        items=(
            MutationItem(
                "src/generated.py",
                MutationOperation.CREATE,
                False,
                None,
                None,
                b"VALUE = 1\n",
            ),
        ),
        created_at=now,
        expires_at=now + timedelta(minutes=20),
    )
    persisted = repository.create_mutation_proposal(
        proposal, request_key="cognitive-mutation-proposal", actor="owner"
    )
    repository.transition(run.run_id, "running", actor="owner", summary="start")
    engine = _Engine(['{"decision":"execute_next","step_id":"mutate"}'])
    memories = MemoryRepository(database)
    loop = LocalCognitiveActionLoop(
        database,
        language_engine=engine,
        memory=TieredMemoryRepository(
            database, memories, MemoryLifecycleRepository(database, memories)
        ),
    )
    cycle = loop.create_cycle(run.run_id, actor="owner")
    advanced = loop.advance(str(cycle["public_id"]), actor="owner")
    assert advanced.status == "action_ready"
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    coordinator = WorkspaceLeaseCoordinator._for_test(runtime)
    return database, repository, loop, engine, run, persisted, workspace, coordinator


def _request_and_apply(state, *, crash_hook=None):
    database, repository, loop, _engine, run, persisted, workspace, coordinator = state
    cycle = loop.get(next(iter(_cycle_ids(database))), actor="owner")
    assert cycle is not None
    wait = loop.request_mutation_review(
        str(cycle["public_id"]),
        persisted.public_id,
        persisted.proposal.proposal_sha256,
        actor="owner",
    )
    review = repository.resolve_mutation_review(
        persisted.public_id,
        persisted.proposal.proposal_sha256,
        str(wait["gate_id"]),
        actor="owner",
        decision=HumanGateStatus.APPROVED,
    )
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=coordinator,
        crash_hook=crash_hook,
    )
    result = applicator.apply(
        persisted.public_id,
        persisted.proposal.proposal_sha256,
        review.gate_id,
        actor="owner",
        apply_request_key="cognitive-mutation-apply",
    )
    return str(cycle["public_id"]), wait, result, run, workspace, coordinator


def _cycle_ids(database: Database) -> tuple[str, ...]:
    with database.connect() as connection:
        return tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT public_id FROM assistant_cognitive_cycles ORDER BY id"
            )
        )


def _handoff(
    state,
    cycle_id: str,
    wait: dict[str, object],
    attempt_id: str,
    *,
    request_key: str = "cognitive-mutation-handoff",
):
    _database, _repository, loop, _engine, _run, _persisted, workspace, coordinator = state
    identity = coordinator.identity(workspace)
    lease = coordinator.acquire(identity, WorkspaceLeaseMode.EXCLUSIVE)
    try:
        return loop.handoff_mutation_success(
            cycle_id,
            str(wait["public_id"]),
            attempt_id,
            request_key=request_key,
            workspace_identity=identity,
            lease_receipt=lease.receipt,
            actor="owner",
            workspace_lease_coordinator=coordinator,
        )
    finally:
        lease.close()


def test_schema61_cognitive_mutation_extension_is_idempotent_and_vault_only(
    tmp_path: Path,
) -> None:
    database, _repository, _loop, _engine, _run, _proposal, _workspace, _coordinator = (
        _state(tmp_path)
    )
    database.migrate()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "64"
        turn_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='assistant_cognitive_turns'"
        ).fetchone()[0]
        wait_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='assistant_cognitive_owner_waits'"
        ).fetchone()[0]
        assert "mutation_review_requested" in turn_sql
        assert "mutation_review_required" in wait_sql
        assert "mutation_handoff_continued" in wait_sql
        assert "mutation_applied" not in wait_sql
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE name='assistant_cognitive_mutation_handoffs'"
        ).fetchone() is not None
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_cycles"
        ).fetchone()[0] == 1
    root = Database(tmp_path / "root.sqlite3", role="root")
    root.migrate()
    with root.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "64"
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE name='assistant_cognitive_mutation_handoffs'"
        ).fetchone() is None


def test_mixed_plan_admitted_and_unsupported_or_unreviewed_plan_rejected(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path / "mixed")
    assert _cycle_ids(state[0])
    for capability, reviewed in (
        (Capability.WORKSPACE_READ, True),
        (Capability.SELF_MODIFY, False),
    ):
        workspace = tmp_path / capability.value.replace(".", "-")
        workspace.mkdir()
        now = datetime.now(UTC)
        run = AutonomyRun(
            actor="owner",
            workspace=WorkspaceScope.from_root(workspace),
            grant=CapabilityGrant(
                capabilities=frozenset({capability}),
                issued_at=now,
                expires_at=now + timedelta(hours=1),
            ),
            plan=RunPlan(
                "invalid cognitive plan",
                (RunStep("step", capability, "bounded", requires_human_gate=reviewed),),
            ),
        )
        database = Database(workspace / "vault.sqlite3", role="vault")
        database.migrate()
        repository = AutonomyRepository(database)
        repository.create(run)
        repository.transition(run.run_id, "running", actor="owner", summary="start")
        memories = MemoryRepository(database)
        loop = LocalCognitiveActionLoop(
            database,
            language_engine=_Engine([]),
            memory=TieredMemoryRepository(
                database, memories, MemoryLifecycleRepository(database, memories)
            ),
        )
        with pytest.raises(PermissionError):
            loop.create_cycle(run.run_id, actor="owner")


def test_ordinary_advance_on_self_modify_is_side_effect_free(tmp_path: Path) -> None:
    database, _repository, loop, _engine, _run, _proposal, _workspace, _coordinator = (
        _state(tmp_path)
    )
    cycle_id = _cycle_ids(database)[0]
    with database.connect() as connection:
        before = tuple(
            connection.execute(
                "SELECT (SELECT COUNT(*) FROM assistant_cognitive_turns),"
                "(SELECT COUNT(*) FROM assistant_autonomy_human_gates),"
                "(SELECT COUNT(*) FROM assistant_autonomy_execution_reservations)"
            ).fetchone()
        )
    with pytest.raises(PermissionError, match="request_mutation_review"):
        loop.advance(cycle_id, actor="owner")
    with database.connect() as connection:
        after = tuple(
            connection.execute(
                "SELECT (SELECT COUNT(*) FROM assistant_cognitive_turns),"
                "(SELECT COUNT(*) FROM assistant_autonomy_human_gates),"
                "(SELECT COUNT(*) FROM assistant_autonomy_execution_reservations)"
            ).fetchone()
        )
        status = connection.execute(
            "SELECT status FROM assistant_cognitive_cycles"
        ).fetchone()[0]
    assert after == before and status == "action_ready"


def test_atomic_mutation_review_request_and_approval_remain_waiting(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    database, repository, loop, _engine, run, persisted, _workspace, _coordinator = state
    cycle_id = _cycle_ids(database)[0]
    wait = loop.request_mutation_review(
        cycle_id,
        persisted.public_id,
        persisted.proposal.proposal_sha256,
        actor="owner",
    )
    assert wait["reason"] == "mutation_review_required"
    assert wait["source_request_id"] is None and wait["gate_id"]
    with database.connect() as connection:
        turn = connection.execute(
            "SELECT kind,state,disposition FROM assistant_cognitive_turns "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert tuple(turn) == ("act", "completed", "mutation_review_requested")
    repository.resolve_mutation_review(
        persisted.public_id,
        persisted.proposal.proposal_sha256,
        str(wait["gate_id"]),
        actor="owner",
        decision="approved",
    )
    assert repository.get(run.run_id)["status"] == "waiting_human"
    assert loop.get(cycle_id, actor="owner")["status"] == "waiting_owner"
    assert loop.owner_wait(str(wait["public_id"]), actor="owner")["state"] == "pending"
    with pytest.raises(PermissionError):
        loop.continue_after_ordinary_gate(str(wait["public_id"]), actor="owner")


@pytest.mark.parametrize("field", ["actor", "proposal", "sha"])
def test_mutation_review_request_mismatch_rolls_back(tmp_path: Path, field: str) -> None:
    state = _state(tmp_path)
    database, _repository, loop, _engine, _run, persisted, _workspace, _coordinator = state
    cycle_id = _cycle_ids(database)[0]
    before = loop.get(cycle_id, actor="owner")
    with pytest.raises((PermissionError, ValueError)):
        loop.request_mutation_review(
            cycle_id,
            "missing" if field == "proposal" else persisted.public_id,
            "0" * 64 if field == "sha" else persisted.proposal.proposal_sha256,
            actor="intruder" if field == "actor" else "owner",
        )
    after = loop.get(cycle_id, actor="owner")
    assert after["status"] == before["status"] == "action_ready"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_human_gates"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_owner_waits"
        ).fetchone()[0] == 0


def test_cleanup_pending_result_does_not_complete_mutation_step(tmp_path: Path) -> None:
    state = _state(tmp_path)

    def crash(point: str) -> None:
        if point == "result_durable":
            raise SystemExit

    with pytest.raises(SystemExit):
        _request_and_apply(state, crash_hook=crash)
    _database, repository, _loop, _engine, run, _proposal, _workspace, _coordinator = state
    step = repository.first_incomplete_plan_step(run.run_id, actor="owner")
    assert step is not None and step.step_id == "mutate"


def test_runner_stops_at_incomplete_self_modify(tmp_path: Path) -> None:
    _database, repository, _loop, _engine, run, _proposal, _workspace, _coordinator = (
        _state(tmp_path)
    )
    result = SupervisedAutonomyRunner(repository, actor="owner").tick(run.run_id)
    assert result.outcome is SupervisedTickOutcome.UNSUPPORTED_CAPABILITY
    assert result.step_id == "mutate"


def test_success_handoff_exposes_later_process_without_execution_or_model_call(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path)
    database, repository, loop, engine, run, _proposal, _workspace, _coordinator = state
    cycle_id, wait, result, _run, _workspace, _coordinator = _request_and_apply(state)
    before_calls = len(engine.calls)
    turns_before = len(loop.get(cycle_id, actor="owner")["turns"])
    handoff = _handoff(state, cycle_id, wait, result.attempt_public_id)
    assert handoff["request_key"] == "cognitive-mutation-handoff"
    assert handoff["attempt_id"] == result.attempt_public_id
    assert loop.get(cycle_id, actor="owner")["status"] == "waiting_owner"
    assert repository.get(run.run_id)["status"] == "running"
    step = repository.first_incomplete_plan_step(run.run_id, actor="owner")
    assert step is not None and step.step_id == "verify"
    assert len(engine.calls) == before_calls
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_execution_reservations"
        ).fetchone()[0] == 0
        owner_wait = connection.execute(
            "SELECT state,resolution FROM assistant_cognitive_owner_waits"
        ).fetchone()
        assert tuple(owner_wait) == ("pending", None)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE assistant_cognitive_mutation_handoffs SET actor='other'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM assistant_cognitive_mutation_handoffs")
    continued = loop.continue_after_mutation_handoff(
        str(handoff["public_id"]), actor="owner"
    )
    assert continued["status"] == "evaluation_ready"
    assert len(continued["turns"]) == turns_before
    assert repository.get(run.run_id)["status"] == "running"
    assert loop.owner_wait(str(wait["public_id"]), actor="owner")["resolution"] == (
        "mutation_handoff_continued"
    )
    replay = loop.continue_after_mutation_handoff(
        str(handoff["public_id"]), actor="owner"
    )
    assert replay["status"] == "evaluation_ready"
    assert len(replay["turns"]) == turns_before


def test_final_mutation_handoff_and_continuation_defer_completion(tmp_path: Path) -> None:
    state = _state(tmp_path, final_mutation=True)
    database, repository, loop, engine, run, _proposal, _workspace, _coordinator = state
    cycle_id, wait, result, _run, _workspace, _coordinator = _request_and_apply(state)
    handoff = _handoff(state, cycle_id, wait, result.attempt_public_id)
    assert loop.get(cycle_id, actor="owner")["status"] == "waiting_owner"
    assert repository.get(run.run_id)["status"] == "running"
    with database.connect() as connection:
        counts = tuple(
            connection.execute(
                "SELECT (SELECT COUNT(*) FROM assistant_autonomy_events "
                "WHERE event_type='mutation_success_handoff'),"
                "(SELECT COUNT(*) FROM assistant_cognitive_cycle_events "
                "WHERE summary_code='mutation_handoff_continued')"
            ).fetchone()
        )
    replay = _handoff(state, cycle_id, wait, result.attempt_public_id)
    assert replay == handoff
    with pytest.raises(PermissionError, match="otra request_key"):
        _handoff(
            state,
            cycle_id,
            wait,
            result.attempt_public_id,
            request_key="conflicting-handoff-key",
        )
    with database.connect() as connection:
        replay_counts = tuple(
            connection.execute(
                "SELECT (SELECT COUNT(*) FROM assistant_autonomy_events "
                "WHERE event_type='mutation_success_handoff'),"
                "(SELECT COUNT(*) FROM assistant_cognitive_cycle_events "
                "WHERE summary_code='mutation_handoff_continued')"
            ).fetchone()
        )
    assert replay_counts == counts == (1, 0)
    continued = loop.continue_after_mutation_handoff(
        str(handoff["public_id"]), actor="owner"
    )
    assert continued["status"] == "evaluation_ready"
    assert repository.get(run.run_id)["status"] == "running"
    advanced = loop.advance(cycle_id, actor="owner")
    assert advanced.status == "completed"
    assert repository.get(run.run_id)["status"] == "completed"
    assert _handoff(state, cycle_id, wait, result.attempt_public_id) == handoff
    assert len(engine.calls) == 1


def test_handoff_requires_live_exact_lease_and_fresh_clean_inspection(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _database, _repository, loop, _engine, _run, _proposal, workspace, coordinator = state
    cycle_id, wait, result, _run, _workspace, _coordinator = _request_and_apply(state)
    identity = coordinator.identity(workspace)
    released = coordinator.acquire(identity, WorkspaceLeaseMode.EXCLUSIVE)
    released.close()
    with pytest.raises(PermissionError):
        loop.handoff_mutation_success(
            cycle_id,
            str(wait["public_id"]),
            result.attempt_public_id,
            request_key="released-lease-handoff",
            workspace_identity=identity,
            lease_receipt=released.receipt,
            actor="owner",
            workspace_lease_coordinator=coordinator,
        )
    blockade = workspace / ".elyndra-mutation-journal/blockade.json"
    blockade.write_text(json.dumps({"unexpected": True}))
    blockade.chmod(0o600)
    live = coordinator.acquire(identity, WorkspaceLeaseMode.EXCLUSIVE)
    try:
        with pytest.raises(PermissionError, match="terminal limpio"):
            loop.handoff_mutation_success(
                cycle_id,
                str(wait["public_id"]),
                result.attempt_public_id,
                request_key="blocked-handoff",
                workspace_identity=identity,
                lease_receipt=live.receipt,
                actor="owner",
                workspace_lease_coordinator=coordinator,
            )
    finally:
        live.close()


def test_handoff_wrong_attempt_or_actor_is_denied(tmp_path: Path) -> None:
    state = _state(tmp_path)
    _database, _repository, loop, _engine, _run, _proposal, workspace, coordinator = state
    cycle_id, wait, result, _run, _workspace, _coordinator = _request_and_apply(state)
    identity = coordinator.identity(workspace)
    lease = coordinator.acquire(identity, WorkspaceLeaseMode.EXCLUSIVE)
    try:
        for attempt, actor in (
            ("missing", "owner"),
            (result.attempt_public_id, "intruder"),
        ):
            with pytest.raises(PermissionError):
                loop.handoff_mutation_success(
                    cycle_id,
                    str(wait["public_id"]),
                    attempt,
                    request_key=f"denied-{attempt}-{actor}",
                    workspace_identity=identity,
                    lease_receipt=lease.receipt,
                    actor=actor,
                    workspace_lease_coordinator=coordinator,
                )
    finally:
        lease.close()
