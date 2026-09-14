from __future__ import annotations

import copy
import hashlib
import json
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
    ExecutionContract,
    ExecutionOutcome,
    HumanGateStatus,
    MutationItem,
    MutationOperation,
    MutationProposal,
    PublicProjectMutationPolicy,
    RunPlan,
    RunStep,
    WorkspaceLeaseCoordinator,
    WorkspaceLeaseMode,
    WorkspaceScope,
)
from elyndra.autonomy.binding import (
    ExecutionBindingError,
    _approved_step_ids,
    _rebuild_grant,
    _rebuild_plan,
)
from elyndra.autonomy.bubblewrap_executor import BubblewrapExecutor
from elyndra.autonomy.mutation_application import MutationApplicator
from elyndra.cognitive_loop import LocalCognitiveActionLoop, _parse_model_decision
from elyndra.db import Database
from elyndra.engines import LanguageReply


class _Engine:
    name = "model-mutation-test"
    supports_vision = False

    def __init__(self, reply: str) -> None:
        self.raw_reply = reply
        self.calls: list[dict[str, object]] = []

    def reply(self, prompt: str, **kwargs: object) -> LanguageReply:
        self.calls.append({"prompt": prompt, **kwargs})
        return LanguageReply(self.raw_reply, self.name, True)

    def release(self) -> None:
        return None


class _Memory:
    def recall(self, *args: object, **kwargs: object) -> object:
        return type("Recall", (), {"items": []})()


def _approved_review_state(tmp_path: Path, *, gated_process: bool = False):
    database, repository, loop, run, _engine, _workspace, cycle = _state(
        tmp_path, include_process=True, process_requires_gate=gated_process
    )
    loop.advance(cycle, actor="owner")
    proposal = repository.list_mutation_proposals(run.run_id, actor="owner")[0]
    wait = loop.list_owner_waits(actor="owner")[0]
    repository.resolve_mutation_review(
        proposal.public_id,
        proposal.proposal.proposal_sha256,
        str(wait["gate_id"]),
        actor="owner",
        decision=HumanGateStatus.APPROVED,
    )
    item = repository.get(run.run_id)
    assert item is not None
    return database, repository, run, item


def _state(
    tmp_path: Path,
    *,
    content: str = "VALUE = 2\n",
    existing: bytes | None = None,
    target: str = "src/generated.py",
    include_process: bool = False,
    process_requires_gate: bool = False,
    protect_workspace: bool = False,
) -> tuple[Database, AutonomyRepository, LocalCognitiveActionLoop, AutonomyRun, _Engine, Path, str]:
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    if existing is not None:
        (workspace / "src" / "generated.py").write_bytes(existing)
    now = datetime.now(UTC)
    mutate = RunStep(
        "mutate",
        Capability.SELF_MODIFY,
        "generate source",
        target=target,
        requires_human_gate=True,
    )
    steps = (mutate,)
    capabilities = {Capability.SELF_MODIFY}
    allowed_executables: tuple[str, ...] = ()
    if include_process:
        executable = str(Path(sys.executable).resolve(strict=True))
        steps += (
            RunStep(
                "verify",
                Capability.PROCESS_EXEC,
                "validate generated source",
                target=".",
                requires_human_gate=process_requires_gate,
                command=CommandSpec(
                    executable=executable,
                    argv=(executable, "-c", "print('validated')"),
                    cwd=".",
                    timeout_seconds=3,
                ),
            ),
        )
        capabilities.add(Capability.PROCESS_EXEC)
        allowed_executables = (executable,)
    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(workspace),
        grant=CapabilityGrant(
            capabilities=frozenset(capabilities),
            issued_at=now,
            expires_at=now + timedelta(minutes=20),
            max_steps=len(steps),
            max_commands=1,
            max_runtime_seconds=3 if include_process else 1,
            allowed_executables=allowed_executables,
        ),
        plan=RunPlan(
            objective="generate exact reviewed source",
            steps=steps,
        ),
    )
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    repository.create(run)
    repository.transition(run.run_id, "running", actor="owner", summary="start")
    raw = json.dumps(
        {
            "decision": "execute_next",
            "step_id": "mutate",
            "mutation": {"content": content},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    engine = _Engine(raw)
    loop = LocalCognitiveActionLoop(
        database,
        language_engine=engine,
        memory=_Memory(),  # type: ignore[arg-type]
        mutation_workspace_policy=PublicProjectMutationPolicy(
            (
                workspace
                if protect_workspace
                else Path(__file__).resolve(strict=True).parent,
            )
        ),
    )
    cycle = loop.create_cycle(run.run_id, actor="owner")
    return database, repository, loop, run, engine, workspace, str(cycle["public_id"])


def test_denied_product_workspace_stops_before_snapshot_model_and_mutation_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, repository, loop, run, engine, _workspace, cycle = _state(
        tmp_path, existing=b"PRIVATE_SOURCE = True\n", protect_workspace=True
    )

    def unexpected_snapshot(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("source snapshot must not be captured")

    monkeypatch.setattr("elyndra.cognitive_loop._capture_source_snapshot", unexpected_snapshot)
    with pytest.raises(PermissionError, match="se superpone"):
        loop.advance(cycle, actor="owner")

    assert engine.calls == []
    assert repository.list_mutation_proposals(run.run_id, actor="owner") == []
    with database.connect() as connection:
        for table in (
            "assistant_cognitive_model_mutation_origins",
            "assistant_autonomy_human_gates",
            "assistant_autonomy_mutation_attempts",
            "assistant_autonomy_mutation_results",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_turns"
        ).fetchone()[0] == 0


def test_schema63_to_64_is_vault_only_idempotent_and_preserves_owner_proposals(
    tmp_path: Path,
) -> None:
    database, repository, _loop, run, _engine, workspace, _cycle = _state(tmp_path)
    now = datetime.now(UTC)
    owner = repository.create_mutation_proposal(
        MutationProposal(
            run_id=run.run_id,
            step_id="mutate",
            actor="owner",
            workspace_root=str(workspace),
            items=(
                MutationItem(
                    "src/owner.py", MutationOperation.CREATE, False, None, None, "x=1\n"
                ),
            ),
            created_at=now,
            expires_at=now + timedelta(minutes=1),
        ),
        request_key="owner-before-64",
        actor="owner",
    )
    with database.connect() as connection:
        connection.executescript(
            """
            DROP TRIGGER trg_cognitive_model_mutation_origin_integrity;
            DROP TRIGGER trg_cognitive_model_mutation_origin_no_update;
            DROP TRIGGER trg_cognitive_model_mutation_origin_no_delete;
            DROP TABLE assistant_cognitive_model_mutation_origins;
            UPDATE schema_meta SET value='63' WHERE key='schema_version';
            """
        )
    database.migrate()
    database.migrate()
    loaded = repository.mutation_proposal(owner.public_id, actor="owner")
    assert loaded is not None and loaded.proposal_origin == "owner"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "64"
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_model_mutation_origins"
        ).fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    root = Database(tmp_path / "root.sqlite3", role="root")
    root.migrate()
    with root.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "64"
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='assistant_cognitive_model_mutation_origins'"
        ).fetchone() is None


@pytest.mark.parametrize("existing", [None, b"VALUE = 1\n"])
def test_model_mutation_derives_exact_proposal_and_pending_review(
    tmp_path: Path, existing: bytes | None
) -> None:
    database, repository, loop, run, engine, workspace, cycle = _state(
        tmp_path, existing=existing
    )
    before = (workspace / "src" / "generated.py").read_bytes() if existing else None
    result = loop.advance(cycle, actor="owner")
    assert result.status == "waiting_owner"
    assert result.decision == "execute_next"
    assert result.disposition == "mutation_review_requested"
    assert len(engine.calls) == 1
    proposals = repository.list_mutation_proposals(run.run_id, actor="owner")
    assert len(proposals) == 1
    persisted = proposals[0]
    assert persisted.proposal_origin == "model"
    assert persisted.source_model_turn_id == result.turn_id
    assert persisted.model_reply_sha256 is not None
    assert persisted.source_snapshot_sha256 is not None
    item = persisted.proposal.items[0]
    assert item.relative_path == "src/generated.py"
    assert item.operation is (
        MutationOperation.REPLACE if existing is not None else MutationOperation.CREATE
    )
    assert item.original_exists is (existing is not None)
    assert item.original_sha256 == (
        hashlib.sha256(existing).hexdigest() if existing is not None else None
    )
    assert item.original_size == (len(existing) if existing is not None else None)
    assert item.proposed_content == b"VALUE = 2\n"
    assert persisted.proposal.proposal_sha256 == persisted.proposal.proposal_sha256
    assert (workspace / "src" / "generated.py").exists() is (existing is not None)
    if existing is not None:
        assert (workspace / "src" / "generated.py").read_bytes() == before
    with database.connect() as connection:
        origin = connection.execute(
            "SELECT * FROM assistant_cognitive_model_mutation_origins"
        ).fetchone()
        assert origin is not None
        assert origin["proposal_sha256"] == persisted.proposal.proposal_sha256
        assert engine.raw_reply not in tuple(
            str(value) for value in origin if value is not None
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_attempts"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_results"
        ).fetchone()[0] == 0
        gate = connection.execute(
            "SELECT * FROM assistant_autonomy_human_gates"
        ).fetchone()
        assert gate is not None and gate["kind"] == "mutation_review"
        assert gate["status"] == "pending"
        wait = connection.execute("SELECT * FROM assistant_cognitive_owner_waits").fetchone()
        assert wait is not None and wait["reason"] == "mutation_review_required"
        assert connection.execute(
            "SELECT status FROM assistant_autonomy_runs WHERE public_id=?", (run.run_id,)
        ).fetchone()[0] == AutonomyRunStatus.WAITING_HUMAN.value


def test_model_mutation_origin_is_immutable_and_owner_rejection_has_no_effect(
    tmp_path: Path,
) -> None:
    database, repository, loop, run, _engine, workspace, cycle = _state(tmp_path)
    loop.advance(cycle, actor="owner")
    proposal = repository.list_mutation_proposals(run.run_id, actor="owner")[0]
    with database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE assistant_cognitive_model_mutation_origins SET created_at='changed'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM assistant_cognitive_model_mutation_origins")
        gate_id = str(
            connection.execute(
                "SELECT public_id FROM assistant_autonomy_human_gates"
            ).fetchone()[0]
        )
    repository.resolve_mutation_review(
        proposal.public_id,
        proposal.proposal.proposal_sha256,
        gate_id,
        actor="owner",
        decision=HumanGateStatus.REJECTED,
    )
    assert not (workspace / "src" / "generated.py").exists()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_attempts"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "extra",
    [
        {"path": "src/other.py"},
        {"operation": "replace"},
        {"original_sha256": "0" * 64},
        {"original_size": 1},
        {"actor": "owner"},
        {"workspace_root": "/tmp"},
        {"run_id": "run"},
        {"expires_at": "later"},
        {"request_key": "key"},
        {"gate": "approved"},
        {"approval": True},
        {"capabilities": ["self.modify"]},
        {"grant": {}},
        {"executable": "/bin/sh"},
        {"argv": ["sh"]},
        {"command": {}},
    ],
)
def test_mutation_contract_rejects_every_non_content_field(extra: dict[str, object]) -> None:
    mutation: dict[str, object] = {"content": "x=1\n", **extra}
    raw = json.dumps(
        {"decision": "execute_next", "step_id": "mutate", "mutation": mutation}
    )
    with pytest.raises(ValueError):
        _parse_model_decision(raw, expected_step="mutate", allow_mutation=True)


def test_mutation_contract_is_contextual_and_bounded() -> None:
    raw = '{"decision":"execute_next","step_id":"mutate","mutation":{"content":"x"}}'
    with pytest.raises(ValueError):
        _parse_model_decision(raw, expected_step="mutate")
    for decision in ("request_human", "insufficient_evidence", "propose_replan", "stop"):
        invalid = json.dumps({"decision": decision, "mutation": {"content": "x"}})
        with pytest.raises(ValueError):
            _parse_model_decision(invalid, expected_step="mutate", allow_mutation=True)
    oversized = json.dumps(
        {
            "decision": "execute_next",
            "step_id": "mutate",
            "mutation": {"content": "x" * 16_385},
        }
    )
    with pytest.raises(ValueError):
        _parse_model_decision(oversized, expected_step="mutate", allow_mutation=True)


@pytest.mark.parametrize("target", ["../escape.py", ".git/config", "/absolute.py"])
def test_unsafe_frozen_target_cannot_create_model_mutation(tmp_path: Path, target: str) -> None:
    _database, _repository, loop, _run, engine, workspace, cycle = _state(
        tmp_path, target=target
    )
    result = loop.advance(cycle, actor="owner")
    assert result.status == "waiting_owner"
    assert len(engine.calls) == 1
    assert tuple(workspace.rglob("escape.py")) == ()


def test_non_utf8_and_oversized_source_cannot_create_model_mutation(tmp_path: Path) -> None:
    for label, content in (("binary", b"\xff"), ("large", b"x" * 16_385)):
        case = tmp_path / label
        _database, _repository, loop, _run, engine, _workspace, cycle = _state(
            case, existing=content
        )
        assert loop.advance(cycle, actor="owner").status == "waiting_owner"
        assert len(engine.calls) == 1


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_directory_and_symlink_targets_cannot_create_model_mutation(
    tmp_path: Path, kind: str
) -> None:
    target = "src" if kind == "directory" else "src/generated.py"
    database, repository, loop, run, engine, workspace, cycle = _state(
        tmp_path, target=target
    )
    if kind == "symlink":
        (workspace / "outside.py").write_text("outside = True\n", encoding="utf-8")
        (workspace / "src" / "generated.py").symlink_to(workspace / "outside.py")
    result = loop.advance(cycle, actor="owner")
    assert result.status == "waiting_owner"
    assert len(engine.calls) == 1
    assert repository.list_mutation_proposals(run.run_id, actor="owner") == []
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_model_mutation_origins"
        ).fetchone()[0] == 0


def _specialized_approved_ids(
    repository: AutonomyRepository, run: AutonomyRun, item: dict[str, object]
) -> frozenset[str]:
    return _approved_step_ids(
        item,
        plan=_rebuild_plan(item["plan"]),
        repository=repository,
        run_id=run.run_id,
        actor="owner",
    )


def test_approved_mutation_review_grants_zero_ordinary_step_authority(
    tmp_path: Path,
) -> None:
    _database, repository, run, item = _approved_review_state(tmp_path)
    assert _specialized_approved_ids(repository, run, item) == frozenset()


def test_mutation_review_cannot_authorize_independently_gated_process(
    tmp_path: Path,
) -> None:
    _database, repository, run, item = _approved_review_state(
        tmp_path, gated_process=True
    )
    plan = _rebuild_plan(item["plan"])
    approved = _specialized_approved_ids(repository, run, item)
    contract = ExecutionContract(
        run_id=run.run_id,
        plan=plan,
        workspace=WorkspaceScope.from_root(item["workspace_root"]),
        grant=_rebuild_grant(item["grant"]),
        approved_step_ids=approved,
    )
    with pytest.raises(PermissionError):
        contract.prepare("verify")


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_request",
        "missing_approval",
        "gate_id",
        "step_id",
        "proposal_id",
        "proposal_sha256",
        "sequence",
        "duplicate_request",
        "duplicate_approval",
        "wrong_kind",
    ],
)
def test_specialized_mutation_review_audit_tampering_fails_closed(
    tmp_path: Path, tamper: str
) -> None:
    _database, repository, run, original = _approved_review_state(tmp_path)
    item = copy.deepcopy(original)
    events = item["events"]
    gates = item["human_gates"]
    assert isinstance(events, list) and isinstance(gates, list)
    request = next(event for event in events if event["event_type"] == "mutation_review_requested")
    approval = next(event for event in events if event["event_type"] == "mutation_review_approved")
    if tamper == "missing_request":
        events.remove(request)
    elif tamper == "missing_approval":
        events.remove(approval)
    elif tamper == "gate_id":
        request["payload"]["gate_id"] = "forged-gate"
        approval["payload"]["gate_id"] = "forged-gate"
    elif tamper == "step_id":
        request["step_id"] = request["payload"]["step_id"] = "forged-step"
        approval["step_id"] = approval["payload"]["step_id"] = "forged-step"
    elif tamper == "proposal_id":
        request["payload"]["proposal_id"] = "forged-proposal"
        approval["payload"]["proposal_id"] = "forged-proposal"
    elif tamper == "proposal_sha256":
        request["payload"]["proposal_sha256"] = "0" * 64
        approval["payload"]["proposal_sha256"] = "0" * 64
    elif tamper == "sequence":
        approval["sequence"] = request["sequence"]
    elif tamper == "duplicate_request":
        events.append(copy.deepcopy(request))
    elif tamper == "duplicate_approval":
        events.append(copy.deepcopy(approval))
    else:
        gates[0]["kind"] = "approval"
    with pytest.raises(ExecutionBindingError):
        _specialized_approved_ids(repository, run, item)


def test_approved_mutation_gate_without_durable_binding_fails_closed(tmp_path: Path) -> None:
    database, repository, run, item = _approved_review_state(tmp_path)
    with database.connect() as connection:
        connection.executescript(
            """
            DROP TRIGGER trg_autonomy_mutation_bindings_no_delete;
            DELETE FROM assistant_autonomy_mutation_gate_bindings;
            """
        )
    with pytest.raises(ExecutionBindingError):
        _specialized_approved_ids(repository, run, item)


def test_end_to_end_model_proposal_owner_apply_handoff_and_frozen_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_cognitive_action_loop_v0810 import _fake_execution

    database, repository, loop, run, engine, workspace, cycle = _state(
        tmp_path, include_process=True
    )
    proposed = loop.advance(cycle, actor="owner")
    assert proposed.status == "waiting_owner"
    proposal = repository.list_mutation_proposals(run.run_id, actor="owner")[0]
    wait = loop.list_owner_waits(actor="owner")[0]
    review = repository.resolve_mutation_review(
        proposal.public_id,
        proposal.proposal.proposal_sha256,
        str(wait["gate_id"]),
        actor="owner",
        decision=HumanGateStatus.APPROVED,
    )
    assert not (workspace / "src" / "generated.py").exists()
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    coordinator = WorkspaceLeaseCoordinator._for_test(runtime)
    applied = MutationApplicator(
        repository, workspace_lease_coordinator=coordinator
    ).apply(
        proposal.public_id,
        proposal.proposal.proposal_sha256,
        review.gate_id,
        actor="owner",
        apply_request_key="model-mutation-e2e-apply",
    )
    assert (workspace / "src" / "generated.py").read_bytes() == b"VALUE = 2\n"
    identity = coordinator.identity(workspace)
    lease = coordinator.acquire(identity, WorkspaceLeaseMode.EXCLUSIVE)
    try:
        handoff = loop.handoff_mutation_success(
            cycle,
            str(wait["public_id"]),
            applied.attempt_public_id,
            request_key="model-mutation-e2e-handoff",
            workspace_identity=identity,
            lease_receipt=lease.receipt,
            actor="owner",
            workspace_lease_coordinator=coordinator,
        )
    finally:
        lease.close()
    loop.continue_after_mutation_handoff(str(handoff["public_id"]), actor="owner")
    engine.raw_reply = '{"decision":"execute_next","step_id":"verify"}'
    assert loop.advance(cycle, actor="owner").status == "action_ready"
    monkeypatch.setattr(BubblewrapExecutor, "__init__", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(
        BubblewrapExecutor,
        "execute",
        _fake_execution(repository, ExecutionOutcome.SUCCEEDED),
    )
    observed = loop.advance(cycle, actor="owner")
    assert observed.status == "evaluation_ready"
    assert repository.execution_result(
        run.run_id, observed.source_request_id, actor="owner"
    ) is not None
    assert loop.advance(cycle, actor="owner").status == "completed"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_model_mutation_origins"
        ).fetchone()[0] == 1
