from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import elyndra.autonomy as autonomy_public
from elyndra.autonomy import (
    AutonomyRepository,
    AutonomyRun,
    Capability,
    CapabilityGrant,
    HumanGateStatus,
    MutationItem,
    MutationOperation,
    MutationProposal,
    RunPlan,
    RunStep,
    WorkspaceLeaseCoordinator,
    WorkspaceScope,
)
from elyndra.autonomy.mutation_application import MutationApplicator
from elyndra.autonomy.mutation_recovery import (
    MutationRecoveryDisposition,
    MutationRecoveryInspector,
    MutationRecoveryReconciler,
)
from elyndra.autonomy.workspace_lease import WorkspaceLeaseMode
from elyndra.db import Database


def _create(path: str = "src/new.py", content: bytes = b"new\n") -> MutationItem:
    return MutationItem(path, MutationOperation.CREATE, False, None, None, content)


def _foundation(tmp_path: Path, *items: MutationItem):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    now = datetime.now(UTC)
    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(workspace),
        grant=CapabilityGrant(
            capabilities=frozenset({Capability.SELF_MODIFY}),
            issued_at=now,
            expires_at=now + timedelta(hours=1),
            max_steps=3,
        ),
        plan=RunPlan(
            objective="reconcile mutation",
            steps=(RunStep("mutate", Capability.SELF_MODIFY, "exact mutation"),),
        ),
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
        items=items or (_create(),),
        created_at=now,
        expires_at=now + timedelta(minutes=20),
    )
    persisted = repository.create_mutation_proposal(
        proposal, request_key="reconcile-proposal", actor="owner"
    )
    repository.transition(run.run_id, "running", actor="owner", summary="start")
    review = repository.request_mutation_review(
        persisted.public_id, proposal.proposal_sha256, actor="owner"
    )
    repository.resolve_mutation_review(
        persisted.public_id,
        proposal.proposal_sha256,
        review.gate_id,
        actor="owner",
        decision=HumanGateStatus.APPROVED,
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    coordinator = WorkspaceLeaseCoordinator._for_test(runtime)
    applicator = MutationApplicator(repository, workspace_lease_coordinator=coordinator)
    inspector = MutationRecoveryInspector(repository, workspace_lease_coordinator=coordinator)
    reconciler = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=coordinator
    )
    return (
        database,
        repository,
        applicator,
        inspector,
        reconciler,
        persisted.public_id,
        proposal.proposal_sha256,
        review.gate_id,
        workspace,
    )


def _apply(applicator, proposal: str, proposal_sha256: str, gate: str):
    return applicator.apply(
        proposal,
        proposal_sha256,
        gate,
        actor="owner",
        apply_request_key="reconcile-apply",
    )


def _attempt(database: Database) -> str:
    with database.connect() as connection:
        return str(
            connection.execute(
                "SELECT public_id FROM assistant_autonomy_mutation_attempts"
            ).fetchone()[0]
        )


def _manifest(workspace: Path, attempt: str) -> Path:
    return workspace / ".elyndra-mutation-journal" / attempt / "manifest.jsonl"


def _records(workspace: Path, attempt: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in _manifest(workspace, attempt).read_text().splitlines()]


def _crash(tmp_path: Path, point: str, *items: MutationItem):
    values = _foundation(tmp_path, *items)
    database, repository, _applicator, inspector, reconciler, proposal, sha, gate, workspace = (
        values
    )

    def hook(current: str) -> None:
        if current == point:
            raise SystemExit

    crash_runtime = tmp_path / "crash-runtime"
    crash_runtime.mkdir(mode=0o700)
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(crash_runtime),
        crash_hook=hook,
    )
    with pytest.raises(SystemExit):
        _apply(applicator, proposal, sha, gate)
    attempt = (
        _attempt(database)
        if point != "blockade_durable"
        else json.loads(
            (workspace / ".elyndra-mutation-journal/blockade.json").read_text()
        )["attempt_public_id"]
    )
    return database, repository, inspector, reconciler, attempt, workspace


def _publishing_complete(tmp_path: Path):
    values = _foundation(tmp_path, _create("src/a.py", b"a"), _create("src/b.py", b"b"))
    database, _repository, applicator, inspector, reconciler, proposal, sha, gate, workspace = (
        values
    )
    original = applicator._publish
    calls = 0

    def publish_then_stop(*args):
        nonlocal calls
        original(*args)
        calls += 1
        if calls == 2:
            raise SystemExit

    applicator._publish = publish_then_stop  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal, sha, gate)
    return database, inspector, reconciler, _attempt(database), workspace


def _run_status(database: Database) -> str:
    with database.connect() as connection:
        return str(connection.execute("SELECT status FROM assistant_autonomy_runs").fetchone()[0])


def _coordinator(tmp_path: Path, name: str) -> WorkspaceLeaseCoordinator:
    runtime = tmp_path / name
    runtime.mkdir(mode=0o700)
    return WorkspaceLeaseCoordinator._for_test(runtime)


def test_reconciler_reuses_one_live_ex_lease_without_nested_inspect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db, _repo, _inspector, reconciler, attempt, workspace = _crash(
        tmp_path, "claim_durable"
    )
    calls = 0
    original = reconciler.coordinator.acquire

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(reconciler.coordinator, "acquire", counted)
    monkeypatch.setattr(
        reconciler.inspector,
        "inspect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("nested inspect")),
    )
    result = reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is MutationRecoveryDisposition.ALREADY_TERMINAL
    assert calls == 1


@pytest.mark.parametrize(
    ("fixture", "expected", "deferred"),
    [
        ("preclaim", MutationRecoveryDisposition.PRECLAIM_ORPHAN_UNBLOCKED, False),
        ("manual", MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED, False),
        ("terminal", MutationRecoveryDisposition.ALREADY_TERMINAL, False),
    ],
)
def test_noop_and_deferred_dispositions(tmp_path: Path, fixture, expected, deferred) -> None:
    if fixture == "preclaim":
        _db, _repo, _inspector, reconciler, attempt, workspace = _crash(
            tmp_path, "blockade_durable"
        )
    elif fixture == "manual":
        database, _inspector, _reconciler, attempt, workspace = _publishing_complete(tmp_path)
        with database.connect() as connection:
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempts SET state='recovery_required'"
            )
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempts "
                "SET state='manual_intervention_required'"
            )
        reconciler = _reconciler
    else:
        values = _foundation(tmp_path)
        database, _repo, applicator, _inspector, reconciler, proposal, sha, gate, workspace = values
        _apply(applicator, proposal, sha, gate)
        attempt = _attempt(database)
    blockade = workspace / ".elyndra-mutation-journal/blockade.json"
    blockade_before = blockade.read_bytes() if blockade.exists() else None
    result = reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is expected
    assert result.deferred is deferred
    blockade_after = blockade.read_bytes() if blockade.exists() else None
    if fixture == "preclaim":
        assert blockade_before is not None and blockade_after is None
    else:
        assert blockade_after == blockade_before


def test_preclaim_orphan_unblocked_is_noop(tmp_path: Path) -> None:
    _db, _repo, _inspector, reconciler, attempt, workspace = _crash(
        tmp_path, "blockade_durable"
    )
    (workspace / ".elyndra-mutation-journal/blockade.json").unlink()
    result = reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is MutationRecoveryDisposition.PRECLAIM_ORPHAN_UNBLOCKED
    assert result.steps_performed == 0


def test_emergency_blockade_is_exact_deterministic_and_never_overwritten(
    tmp_path: Path,
) -> None:
    database, _repo, inspector, reconciler, attempt, workspace = _crash(
        tmp_path, "claim_durable"
    )
    blockade = workspace / ".elyndra-mutation-journal/blockade.json"
    blockade.unlink()
    original_target = workspace / "src/new.py"
    result = reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is MutationRecoveryDisposition.ALREADY_TERMINAL
    assert not blockade.exists()
    replay = reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert replay.final_disposition is MutationRecoveryDisposition.ALREADY_TERMINAL
    assert not original_target.exists()
    assert _run_status(database) == "waiting_human"


def test_emergency_blockade_crash_replays_exactly(tmp_path: Path) -> None:
    database, repository, _inspector, _reconciler, attempt, workspace = _crash(
        tmp_path, "claim_durable"
    )
    blockade = workspace / ".elyndra-mutation-journal/blockade.json"
    blockade.unlink()
    coordinator = _coordinator(tmp_path, "replay-runtime")
    crashes = 0

    def crash(point: str) -> None:
        nonlocal crashes
        if point == "emergency_blockade_durable":
            crashes += 1
            raise SystemExit

    reconciler = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=coordinator, crash_hook=crash
    )
    with pytest.raises(SystemExit):
        reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    exact = blockade.read_bytes()
    value = json.loads(exact)
    assert set(value) == {
        "domain",
        "format_version",
        "generation",
        "mutation_vault_id",
        "attempt_public_id",
        "proposal_public_id",
        "proposal_sha256",
        "gate_id",
        "run_public_id",
        "step_id",
        "workspace_st_dev",
        "workspace_st_ino",
        "workspace_mount_id",
        "canonical_workspace_root_sha256",
        "initial_blockade_sha256",
        "manifest_sequence",
        "manifest_tail_sha256",
        "recovery_code",
    }
    assert value["domain"] == "elyndra.mutation-recovery-blockade.v1"
    assert value["generation"] == "emergency"
    assert value["recovery_code"] == "missing_blockade"
    fresh = MutationRecoveryReconciler(repository, workspace_lease_coordinator=coordinator)
    replay = fresh.reconcile(str(workspace), attempt_public_id=attempt)
    assert replay.final_disposition is MutationRecoveryDisposition.ALREADY_TERMINAL
    assert not blockade.exists() and exact and crashes == 1
    assert _run_status(database) == "waiting_human"


@pytest.mark.parametrize("disposition", ["failed_before_publication", "stale"])
def test_clean_outcome_append_and_adoption(tmp_path: Path, disposition: str) -> None:
    database, _repo, _inspector, reconciler, attempt, workspace = _crash(
        tmp_path, "claim_durable", _create("src/a.py"), _create("src/b.py")
    )
    if disposition == "stale":
        (workspace / "src/a.py").write_bytes(b"foreign")
    result = reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is MutationRecoveryDisposition.ALREADY_TERMINAL
    records = _records(workspace, attempt)
    assert [record["attempt_state"] for record in records].count(disposition) == 1
    with database.connect() as connection:
        observed = connection.execute(
            "SELECT outcome,published_count,restored_count "
            "FROM assistant_autonomy_mutation_results"
        ).fetchone()
        assert tuple(observed) == (disposition, 0, 0)
    assert _run_status(database) == "waiting_human"


def test_clean_manifest_crash_adopts_without_duplicate_with_partial_coverage(
    tmp_path: Path,
) -> None:
    database, repository, _inspector, _reconciler, attempt, workspace = _crash(
        tmp_path, "claim_durable", _create("src/a.py"), _create("src/b.py")
    )
    coordinator = _coordinator(tmp_path, "manifest-replay-runtime")

    def crash(point: str) -> None:
        if point == "recovery_manifest_durable:failed_before_publication":
            raise SystemExit

    crashing = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=coordinator, crash_hook=crash
    )
    with pytest.raises(SystemExit):
        crashing.reconcile(str(workspace), attempt_public_id=attempt)
    before = _records(workspace, attempt)
    with database.connect() as connection:
        connection.execute("DROP TRIGGER trg_autonomy_mutation_attempt_files_no_delete")
        connection.execute(
            "DELETE FROM assistant_autonomy_mutation_attempt_files WHERE ordinal=1"
        )
    fresh = MutationRecoveryReconciler(repository, workspace_lease_coordinator=coordinator)
    assert fresh.reconcile(str(workspace), attempt_public_id=attempt).final_disposition is (
        MutationRecoveryDisposition.ALREADY_TERMINAL
    )
    after = _records(workspace, attempt)
    assert after[:-1] == before
    assert after[-1]["attempt_state"] == "cleanup_ready"
    assert sum(record["attempt_state"] == "failed_before_publication" for record in after) == 1


def test_complete_filesystem_success_reconciles_db_without_target_changes(
    tmp_path: Path,
) -> None:
    database, _inspector, reconciler, attempt, workspace = _publishing_complete(tmp_path)
    targets = {path.name: path.read_bytes() for path in (workspace / "src").iterdir()}
    with database.connect() as connection:
        connection.execute("DROP TRIGGER trg_autonomy_mutation_attempt_file_transition")
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempt_files "
            "SET state='publication_intent'"
        )
    result = reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is MutationRecoveryDisposition.ALREADY_TERMINAL
    with database.connect() as connection:
        states = {
            row[0]
            for row in connection.execute(
                "SELECT state FROM assistant_autonomy_mutation_attempt_files"
            )
        }
        attempt_state = connection.execute(
            "SELECT state FROM assistant_autonomy_mutation_attempts"
        ).fetchone()[0]
        observed = connection.execute(
            "SELECT outcome,published_count,restored_count "
            "FROM assistant_autonomy_mutation_results"
        ).fetchone()
    assert states == {"published"} and attempt_state == "succeeded"
    assert tuple(observed) == ("filesystem_succeeded", 2, 0)
    assert {path.name: path.read_bytes() for path in (workspace / "src").iterdir()} == targets
    assert _run_status(database) == "waiting_human"


def test_invalid_file_db_state_blocks_success_reconciliation(tmp_path: Path) -> None:
    database, _inspector, reconciler, attempt, workspace = _publishing_complete(tmp_path)
    with database.connect() as connection:
        connection.execute("DROP TRIGGER trg_autonomy_mutation_attempt_file_transition")
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempt_files SET state='staged' WHERE ordinal=0"
        )
    before = (workspace / "src/a.py").read_bytes()
    result = reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    assert (workspace / "src/a.py").read_bytes() == before


@pytest.mark.parametrize(
    "crash_point",
    [
        "recovery_manifest_durable:filesystem_applied",
        "recovery_manifest_durable:filesystem_succeeded",
        "recovery_result_durable",
    ],
)
def test_filesystem_success_crash_replay_is_idempotent(
    tmp_path: Path, crash_point: str
) -> None:
    database, _inspector, _reconciler, attempt, workspace = _publishing_complete(tmp_path)
    repository = AutonomyRepository(database)
    coordinator = _coordinator(tmp_path, "success-replay-runtime")

    def crash(point: str) -> None:
        if point == crash_point:
            raise SystemExit

    crashing = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=coordinator, crash_hook=crash
    )
    with pytest.raises(SystemExit):
        crashing.reconcile(str(workspace), attempt_public_id=attempt)
    fresh = MutationRecoveryReconciler(repository, workspace_lease_coordinator=coordinator)
    assert fresh.reconcile(str(workspace), attempt_public_id=attempt).final_disposition is (
        MutationRecoveryDisposition.ALREADY_TERMINAL
    )
    records = _records(workspace, attempt)
    assert sum(record["attempt_state"] == "filesystem_applied" for record in records) == 1
    assert sum(record["attempt_state"] == "filesystem_succeeded" for record in records) == 1
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_results"
        ).fetchone()[0] == 1


def test_terminalization_and_crash_replay_preserve_cleanup_evidence(tmp_path: Path) -> None:
    values = _foundation(tmp_path)
    database, repository, applicator, _inspector, _reconciler, proposal, sha, gate, workspace = (
        values
    )
    original_set = applicator._set_attempt

    def stop_terminal(attempt, state, **fields):
        if state == "succeeded":
            raise SystemExit
        original_set(attempt, state, **fields)

    applicator._set_attempt = stop_terminal  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal, sha, gate)
    attempt = _attempt(database)
    coordinator = _coordinator(tmp_path, "terminal-runtime")

    def crash(point: str) -> None:
        if point == "recovery_terminal_state_durable":
            raise SystemExit

    crashing = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=coordinator, crash_hook=crash
    )
    with pytest.raises(SystemExit):
        crashing.reconcile(str(workspace), attempt_public_id=attempt)
    fresh = MutationRecoveryReconciler(repository, workspace_lease_coordinator=coordinator)
    result = fresh.reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is MutationRecoveryDisposition.ALREADY_TERMINAL
    with database.connect() as connection:
        assert connection.execute(
            "SELECT state FROM assistant_autonomy_mutation_attempts"
        ).fetchone()[0] == "succeeded"
    assert _run_status(database) == "waiting_human"


def test_rollback_required_converges_without_unrelated_target_mutation(tmp_path: Path) -> None:
    values = _foundation(tmp_path, _create("src/a.py", b"a"), _create("src/b.py", b"b"))
    database, _repo, applicator, _inspector, reconciler, proposal, sha, gate, workspace = values
    original = applicator._publish

    def publish_one(*args):
        original(*args)
        raise SystemExit

    applicator._publish = publish_one  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal, sha, gate)
    attempt = _attempt(database)
    before = {path.name: path.read_bytes() for path in (workspace / "src").iterdir()}
    blockade = workspace / ".elyndra-mutation-journal/blockade.json"
    blockade_bytes = blockade.read_bytes()
    result = reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is MutationRecoveryDisposition.ALREADY_TERMINAL
    assert not result.deferred
    assert before == {"a.py": b"a"}
    assert not any((workspace / "src").iterdir())
    assert blockade_bytes and not blockade.exists()


def test_existing_durable_rolled_back_outcome_is_adopted(tmp_path: Path) -> None:
    values = _foundation(tmp_path, _create("src/a.py", b"a"), _create("src/b.py", b"b"))
    database, _repo, applicator, _inspector, reconciler, proposal, sha, gate, workspace = values
    original_publish = applicator._publish
    calls = 0

    def fail_second_publish(*args):
        nonlocal calls
        if calls:
            raise RuntimeError("publication failure")
        calls += 1
        return original_publish(*args)

    def leave_durable_outcome(attempt, outcome, manifest, *_args, **_kwargs):
        assert outcome == "rolled_back"
        manifest.append(outcome)
        applicator._sync_manifest(attempt, manifest)
        raise SystemExit

    applicator._publish = fail_second_publish  # type: ignore[method-assign]
    applicator._insert_result = leave_durable_outcome  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal, sha, gate)
    attempt = _attempt(database)
    result = reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert result.initial_disposition is MutationRecoveryDisposition.ADOPT_DURABLE_OUTCOME
    assert result.final_disposition is MutationRecoveryDisposition.ALREADY_TERMINAL
    with database.connect() as connection:
        observed = connection.execute(
            "SELECT outcome,published_count,restored_count "
            "FROM assistant_autonomy_mutation_results"
        ).fetchone()
    assert tuple(observed) == ("rolled_back", 1, 1)
    assert _run_status(database) == "waiting_human"


def test_caller_cannot_supply_stale_recovery_plan(tmp_path: Path) -> None:
    _database, _repo, inspector, reconciler, attempt, workspace = _crash(
        tmp_path, "claim_durable"
    )
    stale = inspector.inspect(str(workspace), attempt_public_id=attempt)
    with pytest.raises(TypeError):
        reconciler.reconcile(  # type: ignore[call-arg]
            str(workspace), attempt_public_id=attempt, plan=stale
        )


def test_reconciler_has_no_public_autonomy_exposure() -> None:
    assert not hasattr(autonomy_public, "MutationRecoveryReconciler")
    assert hashlib.sha256(b"no-public-api").hexdigest()


def _recovery_db_snapshot(database: Database) -> tuple[tuple[object, ...], ...]:
    with database.connect() as connection:
        attempts = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM assistant_autonomy_mutation_attempts ORDER BY id"
            )
        )
        files = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM assistant_autonomy_mutation_attempt_files ORDER BY id"
            )
        )
        results = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM assistant_autonomy_mutation_results ORDER BY id"
            )
        )
    return attempts + files + results


class _FailSecondReceipt:
    def __init__(self) -> None:
        self.calls = 0

    def require_live(self, **_kwargs) -> None:
        self.calls += 1
        if self.calls == 2:
            raise PermissionError("receipt closed after BEGIN")


@pytest.mark.parametrize("method", ["filesystem", "result", "terminal"])
def test_invalid_post_begin_receipt_cannot_mutate_recovery_state(
    tmp_path: Path, method: str
) -> None:
    database, repository, inspector, _reconciler, attempt, workspace = _crash(
        tmp_path, "claim_durable"
    )
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.manifest is not None
    receipt = _FailSecondReceipt()
    before = _recovery_db_snapshot(database)
    common = {
        "attempt_public_id": attempt,
        "workspace_identity": plan.workspace,
        "lease_receipt": receipt,
    }
    with pytest.raises(PermissionError, match="receipt closed after BEGIN"):
        if method == "filesystem":
            repository._reconcile_mutation_filesystem_applied(
                **common,
                expected_manifest_sequence=plan.manifest.sequence,
                expected_manifest_sha256=plan.manifest.tail_sha256,
                manifest_sequence=plan.manifest.sequence,
                manifest_sha256=plan.manifest.tail_sha256,
            )
        elif method == "result":
            repository._reconcile_mutation_result(
                **common,
                expected_manifest_sequence=plan.manifest.sequence,
                expected_manifest_sha256=plan.manifest.tail_sha256,
                outcome="failed_before_publication",
                outcome_sequence=plan.manifest.sequence,
                outcome_sha256=plan.manifest.tail_sha256,
                published_count=0,
                restored_count=0,
            )
        else:
            repository._reconcile_mutation_terminal(
                **common,
                result_outcome="failed_before_publication",
                result_public_id="missing",
                manifest_sequence=plan.manifest.sequence,
                manifest_sha256=plan.manifest.tail_sha256,
            )
    assert receipt.calls == 2
    assert _recovery_db_snapshot(database) == before


def test_recovery_attempt_accepts_waiting_human_run(tmp_path: Path) -> None:
    database, repository, inspector, _reconciler, attempt, workspace = _crash(
        tmp_path, "claim_durable"
    )
    identity = inspector.inspect(str(workspace), attempt_public_id=attempt).workspace
    with database.connect() as connection:
        observed = repository._recovery_attempt(connection, attempt, identity)
    assert observed["public_id"] == attempt
    assert _run_status(database) == "waiting_human"


@pytest.mark.parametrize("run_status", ["running", "cancelled"])
def test_recovery_attempt_rejects_nonwaiting_run_without_db_changes(
    tmp_path: Path, run_status: str
) -> None:
    database, repository, inspector, reconciler, attempt, workspace = _crash(
        tmp_path, "claim_durable"
    )
    with database.connect() as connection:
        connection.execute(
            "UPDATE assistant_autonomy_runs SET status=?",
            (run_status,),
        )
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.manifest is not None
    before = _recovery_db_snapshot(database)
    lease = reconciler.coordinator.acquire(plan.workspace, WorkspaceLeaseMode.EXCLUSIVE)
    try:
        with pytest.raises(PermissionError, match="requiere run waiting_human"):
            repository._reconcile_mutation_filesystem_applied(
                attempt_public_id=attempt,
                workspace_identity=plan.workspace,
                lease_receipt=lease.receipt,
                expected_manifest_sequence=plan.manifest.sequence,
                expected_manifest_sha256=plan.manifest.tail_sha256,
                manifest_sequence=plan.manifest.sequence,
                manifest_sha256=plan.manifest.tail_sha256,
            )
    finally:
        lease.close()
    assert _recovery_db_snapshot(database) == before
    assert _run_status(database) == run_status
