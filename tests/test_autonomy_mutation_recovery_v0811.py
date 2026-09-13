from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import elyndra.autonomy.mutation_recovery as recovery_module
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
from elyndra.autonomy.linux_fs import LinuxStat
from elyndra.autonomy.mutation_application import MutationApplicator
from elyndra.autonomy.mutation_recovery import (
    MutationBlockadeState,
    MutationRecoveryDisposition,
    MutationRecoveryError,
    MutationRecoveryInspector,
    _classify_physical,
    _ObjectObservation,
)
from elyndra.db import Database


def _create(path: str = "src/new.py", content: bytes = b"new\n") -> MutationItem:
    return MutationItem(path, MutationOperation.CREATE, False, None, None, content)


def _replace(path: str, old: bytes, new: bytes) -> MutationItem:
    return MutationItem(
        path,
        MutationOperation.REPLACE,
        True,
        hashlib.sha256(old).hexdigest(),
        len(old),
        new,
    )


def _foundation(
    tmp_path: Path, *items: MutationItem
) -> tuple[
    Database, AutonomyRepository, MutationApplicator, MutationRecoveryInspector, str, str, Path
]:
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
            objective="recovery inspection",
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
        proposal, request_key="recovery-proposal", actor="owner"
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
    return (
        database,
        repository,
        applicator,
        inspector,
        persisted.public_id,
        review.gate_id,
        workspace,
    )


def _apply(applicator: MutationApplicator, proposal_id: str, proposal_sha: str, gate_id: str):
    return applicator.apply(
        proposal_id,
        proposal_sha,
        gate_id,
        actor="owner",
        apply_request_key="recovery-apply",
    )


def _proposal_sha(database: Database) -> str:
    with database.connect() as connection:
        return str(
            connection.execute(
                "SELECT proposal_sha256 FROM assistant_autonomy_mutation_proposals"
            ).fetchone()[0]
        )


def _attempt_id(database: Database) -> str:
    with database.connect() as connection:
        return str(
            connection.execute(
                "SELECT public_id FROM assistant_autonomy_mutation_attempts"
            ).fetchone()[0]
        )


def _crash_at(
    tmp_path: Path,
    point: str,
    *items: MutationItem,
) -> tuple[Database, MutationRecoveryInspector, str, Path]:
    database, repository, _applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path, *items
    )

    def hook(current: str) -> None:
        if current == point:
            raise SystemExit(point)

    runtime = tmp_path / "runtime-crash"
    runtime.mkdir(mode=0o700)
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime),
        crash_hook=hook,
    )
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    attempt = (
        _attempt_id(database)
        if point != "blockade_durable"
        else json.loads((workspace / ".elyndra-mutation-journal/blockade.json").read_text())[
            "attempt_public_id"
        ]
    )
    return database, inspector, attempt, workspace


def _manifest_path(workspace: Path, attempt: str) -> Path:
    return workspace / ".elyndra-mutation-journal" / attempt / "manifest.jsonl"


def test_full_valid_manifest_chain_and_exact_db_tail(tmp_path: Path) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "publishing_durable")
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.manifest is not None
    assert plan.manifest.sequence >= 4
    assert plan.manifest.db_pointer == "exact_tail"
    assert plan.manifest.record_hashes[-1] == plan.manifest.tail_sha256


def test_broken_previous_manifest_hash_is_rejected(tmp_path: Path) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "publishing_durable")
    path = _manifest_path(workspace, attempt)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records[1]["previous_record_sha256"] = "f" * 64
    path.write_bytes(b"\n".join(_canonical(record) for record in records) + b"\n")
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    assert plan.recovery_code == "manifest_invalid"


@pytest.mark.parametrize("raw", [b"{\n", b'{"z":1,"a":2}\n'])
def test_malformed_or_noncanonical_manifest_is_rejected(tmp_path: Path, raw: bytes) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "claim_durable")
    _manifest_path(workspace, attempt).write_bytes(raw)
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    assert plan.recovery_code == "manifest_invalid"


def test_db_manifest_pointer_valid_prefix_is_reported(tmp_path: Path) -> None:
    database, inspector, attempt, workspace = _crash_at(tmp_path, "publishing_durable")
    first = json.loads(_manifest_path(workspace, attempt).read_text().splitlines()[0])
    with database.connect() as connection:
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempts "
            "SET manifest_sequence=0,manifest_tail_sha256=? WHERE public_id=?",
            (first["record_sha256"], attempt),
        )
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.manifest is not None and plan.manifest.db_pointer == "valid_prefix"


def test_db_manifest_pointer_unknown_hash_is_rejected(tmp_path: Path) -> None:
    database, inspector, attempt, workspace = _crash_at(tmp_path, "publishing_durable")
    with database.connect() as connection:
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempts "
            "SET manifest_sequence=0,manifest_tail_sha256=? WHERE public_id=?",
            ("f" * 64, attempt),
        )
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    assert plan.recovery_code == "manifest_invalid"


def test_exact_initial_blockade_is_classified(tmp_path: Path) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "claim_durable")
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.blockade_state is MutationBlockadeState.EXACT_INITIAL


def test_current_vault_blocked_preclaim_orphan(tmp_path: Path) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "blockade_durable")
    plan = inspector.inspect(str(workspace))
    assert plan.attempt_public_id == attempt
    assert plan.disposition is MutationRecoveryDisposition.BLOCKED_PRECLAIM_ORPHAN


def test_foreign_vault_blockade_is_manual(tmp_path: Path) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "blockade_durable")
    blockade = workspace / ".elyndra-mutation-journal/blockade.json"
    value = json.loads(blockade.read_text())
    value["mutation_vault_id"] = "foreign"
    blockade.write_bytes(_canonical(value))
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.blockade_state is MutationBlockadeState.FOREIGN_VAULT
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED


def test_malformed_blockade_is_manual(tmp_path: Path) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "blockade_durable")
    (workspace / ".elyndra-mutation-journal/blockade.json").write_text("{")
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.blockade_state is MutationBlockadeState.MALFORMED
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED


def test_manifest_only_orphan_is_unblocked(tmp_path: Path) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "blockade_durable")
    (workspace / ".elyndra-mutation-journal/blockade.json").unlink()
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.disposition is MutationRecoveryDisposition.PRECLAIM_ORPHAN_UNBLOCKED


def _metadata(device: int, inode: int, *, mode: int = 0o100600) -> LinuxStat:
    return LinuxStat(device, inode, 7, mode, os.geteuid(), os.getegid(), 1)


def _object(device: int, inode: int, content: bytes, *, mode: int = 0o100600):
    return _ObjectObservation(
        _metadata(device, inode, mode=mode), len(content), hashlib.sha256(content).hexdigest()
    )


def _file_row(operation: str, state: str = "staged") -> dict[str, object]:
    old, new = b"old", b"new"
    return {
        "ordinal": 0,
        "relative_path": "src/a.py",
        "operation": operation,
        "state": state,
        "parent_mount_id": 7,
        "stage_st_dev": 1,
        "stage_st_ino": 20,
        "expected_postimage_size": len(new),
        "expected_postimage_sha256": hashlib.sha256(new).hexdigest(),
        "preimage_st_dev": None if operation == "create" else 1,
        "preimage_st_ino": None if operation == "create" else 10,
        "expected_preimage_size": None if operation == "create" else len(old),
        "expected_preimage_sha256": (
            None if operation == "create" else hashlib.sha256(old).hexdigest()
        ),
        "preimage_uid": None if operation == "create" else os.geteuid(),
        "preimage_gid": None if operation == "create" else os.getegid(),
        "preimage_mode": None if operation == "create" else 0o100600,
        "preimage_nlink": None if operation == "create" else 1,
    }


@pytest.mark.parametrize(
    ("operation", "db_state", "target", "stage", "witness", "expected"),
    [
        (
            "create",
            "staged",
            None,
            _object(1, 20, b"new"),
            _object(1, 20, b"new"),
            "exact_unpublished",
        ),
        (
            "create",
            "published",
            _object(1, 20, b"new"),
            None,
            _object(1, 20, b"new"),
            "exact_published",
        ),
        ("create", "rolled_back", None, None, _object(1, 20, b"new"), "exact_rolled_back"),
        (
            "replace",
            "staged",
            _object(1, 10, b"old"),
            _object(1, 20, b"new"),
            _object(1, 20, b"new"),
            "exact_unpublished",
        ),
        (
            "replace",
            "published",
            _object(1, 20, b"new"),
            _object(1, 10, b"old"),
            _object(1, 20, b"new"),
            "exact_published",
        ),
        (
            "replace",
            "rolled_back",
            _object(1, 10, b"old"),
            _object(1, 20, b"new"),
            _object(1, 20, b"new"),
            "exact_rolled_back",
        ),
        (
            "create",
            "publication_intent",
            _object(1, 20, b"new"),
            None,
            _object(1, 20, b"new"),
            "exact_published",
        ),
        (
            "create",
            "publication_intent",
            None,
            _object(1, 20, b"new"),
            _object(1, 20, b"new"),
            "exact_unpublished",
        ),
        (
            "replace",
            "rollback_intent",
            _object(1, 10, b"old"),
            _object(1, 20, b"new"),
            _object(1, 20, b"new"),
            "exact_rolled_back",
        ),
    ],
)
def test_physical_file_classifications(
    operation, db_state, target, stage, witness, expected
) -> None:
    row = _file_row(operation, db_state)
    assert _classify_physical(row, target=target, stage=stage, witness=witness).value == expected


def test_claimed_crash_is_failed_before_publication(tmp_path: Path) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "claim_durable")
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.disposition is MutationRecoveryDisposition.FAILED_BEFORE_PUBLICATION


def test_prepared_exact_files_are_failed_before_publication(tmp_path: Path) -> None:
    database, repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path
    )
    original = applicator._final_boundary

    def stop(*args):
        original(*args)
        raise SystemExit("prepared")

    applicator._final_boundary = stop  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    plan = inspector.inspect(str(workspace), attempt_public_id=_attempt_id(database))
    assert plan.attempt_state == "prepared"
    assert plan.disposition is MutationRecoveryDisposition.FAILED_BEFORE_PUBLICATION


def test_stale_prepublication_target_is_stale(tmp_path: Path) -> None:
    database, repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path
    )

    def stop(*_args):
        (workspace / "src/new.py").write_bytes(b"foreign")
        raise SystemExit("stale")

    applicator._final_boundary = stop  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    plan = inspector.inspect(str(workspace), attempt_public_id=_attempt_id(database))
    assert plan.disposition is MutationRecoveryDisposition.STALE


def _publishing_crash(
    tmp_path: Path, *, published: int, total: int = 2
) -> tuple[Database, MutationRecoveryInspector, str, Path]:
    items = tuple(_create(f"src/{letter}.py", letter.encode()) for letter in "abc"[:total])
    database, repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path, *items
    )
    original = applicator._publish
    calls = 0

    def publish(*args):
        nonlocal calls
        if calls == published:
            raise SystemExit("publication crash")
        result = original(*args)
        calls += 1
        if calls == published:
            raise SystemExit("publication crash")
        return result

    applicator._publish = publish  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    return database, inspector, _attempt_id(database), workspace


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        (0, MutationRecoveryDisposition.ROLLBACK_REQUIRED),
        (1, MutationRecoveryDisposition.ROLLBACK_REQUIRED),
        (2, MutationRecoveryDisposition.COMPLETE_FILESYSTEM_SUCCESS),
    ],
)
def test_publishing_decision_matrix(tmp_path: Path, published: int, expected) -> None:
    _db, inspector, attempt, workspace = _publishing_crash(tmp_path, published=published)
    assert inspector.inspect(str(workspace), attempt_public_id=attempt).disposition is expected


def test_publishing_ambiguity_is_manual(tmp_path: Path) -> None:
    _db, inspector, attempt, workspace = _publishing_crash(tmp_path, published=1)
    (workspace / "src/a.py").write_bytes(b"tampered")
    assert (
        inspector.inspect(str(workspace), attempt_public_id=attempt).disposition
        is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    )


def test_recovery_required_never_promotes_success(tmp_path: Path) -> None:
    database, inspector, attempt, workspace = _publishing_crash(tmp_path, published=2)
    with database.connect() as connection:
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempts SET state='recovery_required' "
            "WHERE public_id=?",
            (attempt,),
        )
    assert (
        inspector.inspect(str(workspace), attempt_public_id=attempt).disposition
        is MutationRecoveryDisposition.ROLLBACK_REQUIRED
    )


def test_filesystem_applied_exact_requests_success_completion(tmp_path: Path) -> None:
    database, _repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path
    )

    def stop_before_result(*_args, **_kwargs) -> None:
        raise SystemExit

    applicator._insert_result = stop_before_result  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    plan = inspector.inspect(str(workspace), attempt_public_id=_attempt_id(database))
    assert plan.disposition is MutationRecoveryDisposition.COMPLETE_FILESYSTEM_SUCCESS


def test_immutable_result_never_returns_rollback(tmp_path: Path) -> None:
    database, inspector, attempt, workspace = _crash_at(tmp_path, "result_durable")
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.result_outcome == "filesystem_succeeded"
    assert plan.disposition is MutationRecoveryDisposition.CLEANUP_REQUIRED
    assert plan.disposition is not MutationRecoveryDisposition.ROLLBACK_REQUIRED


def test_result_without_blockade_or_cleanup_ready_is_manual(tmp_path: Path) -> None:
    database, inspector, attempt, workspace = _crash_at(tmp_path, "result_durable")
    with database.connect() as connection:
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempts SET state='cleanup_pending' "
            "WHERE public_id=?",
            (attempt,),
        )
    (workspace / ".elyndra-mutation-journal/blockade.json").unlink()
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED


def test_nonterminal_without_result_or_blockade_requires_emergency_blockade(
    tmp_path: Path,
) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "claim_durable")
    (workspace / ".elyndra-mutation-journal/blockade.json").unlink()
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.disposition is MutationRecoveryDisposition.EMERGENCY_BLOCKADE_REQUIRED


def test_manual_attempt_remains_manual(tmp_path: Path) -> None:
    database, inspector, attempt, workspace = _publishing_crash(tmp_path, published=1)
    with database.connect() as connection:
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempts "
            "SET state='recovery_required' WHERE public_id=?",
            (attempt,),
        )
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempts "
            "SET state='manual_intervention_required' WHERE public_id=?",
            (attempt,),
        )
    assert (
        inspector.inspect(str(workspace), attempt_public_id=attempt).disposition
        is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    )


def test_terminal_attempt_is_already_terminal(tmp_path: Path) -> None:
    database, repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path
    )
    _apply(applicator, proposal_id, _proposal_sha(database), gate)
    plan = inspector.inspect(str(workspace), attempt_public_id=_attempt_id(database))
    assert plan.disposition is MutationRecoveryDisposition.ALREADY_TERMINAL


def test_inspection_is_read_only_and_run_stays_waiting_human(tmp_path: Path) -> None:
    database, inspector, attempt, workspace = _publishing_crash(tmp_path, published=1)
    target = workspace / "src/a.py"
    before_bytes = target.read_bytes()
    with database.connect() as connection:
        before = tuple(
            connection.execute(
                "SELECT state,manifest_sequence,manifest_tail_sha256 "
                "FROM assistant_autonomy_mutation_attempts WHERE public_id=?",
                (attempt,),
            ).fetchone()
        )
        result_count = connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_results"
        ).fetchone()[0]
    inspector.inspect(str(workspace), attempt_public_id=attempt)
    with database.connect() as connection:
        after = tuple(
            connection.execute(
                "SELECT state,manifest_sequence,manifest_tail_sha256 "
                "FROM assistant_autonomy_mutation_attempts WHERE public_id=?",
                (attempt,),
            ).fetchone()
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM assistant_autonomy_mutation_results"
            ).fetchone()[0]
            == result_count
        )
        assert (
            connection.execute("SELECT status FROM assistant_autonomy_runs").fetchone()[0]
            == "waiting_human"
        )
    assert after == before
    assert target.read_bytes() == before_bytes


def test_outcome_manifest_without_result_is_reported(tmp_path: Path) -> None:
    database, _repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path
    )

    def append_without_result(attempt, outcome, manifest, *_args, **_kwargs):
        manifest.append(outcome)
        applicator._sync_manifest(attempt, manifest)
        raise SystemExit("before result insert")

    applicator._insert_result = append_without_result  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    plan = inspector.inspect(str(workspace), attempt_public_id=_attempt_id(database))
    assert plan.result_public_id is None
    assert plan.outcome_manifest_without_result == "filesystem_succeeded"
    assert plan.disposition is MutationRecoveryDisposition.ADOPT_DURABLE_OUTCOME
    assert plan.manifest is not None
    assert plan.outcome_sequence == plan.manifest.sequence
    assert plan.outcome_sha256 == plan.manifest.tail_sha256


def test_exact_removal_ready_generation_is_cleanup_required(tmp_path: Path) -> None:
    database, _repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path
    )
    applicator._remove_verified_blockade = (  # type: ignore[method-assign]
        lambda *_args: (_ for _ in ()).throw(SystemExit("leave removal ready"))
    )
    assert _apply(applicator, proposal_id, _proposal_sha(database), gate).state == "cleanup_pending"
    plan = inspector.inspect(str(workspace), attempt_public_id=_attempt_id(database))
    assert plan.blockade_state is MutationBlockadeState.EXACT_REMOVAL_READY
    assert plan.disposition is MutationRecoveryDisposition.CLEANUP_REQUIRED


def test_mismatched_removal_ready_generation_is_manual(tmp_path: Path) -> None:
    database, _repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path
    )
    applicator._remove_verified_blockade = (  # type: ignore[method-assign]
        lambda *_args: (_ for _ in ()).throw(SystemExit("leave removal ready"))
    )
    assert _apply(applicator, proposal_id, _proposal_sha(database), gate).state == "cleanup_pending"
    blockade = workspace / ".elyndra-mutation-journal/blockade.json"
    value = json.loads(blockade.read_text())
    value["result_public_id"] = "mismatch"
    blockade.write_bytes(_canonical(value))
    plan = inspector.inspect(str(workspace), attempt_public_id=_attempt_id(database))
    assert plan.blockade_state is MutationBlockadeState.EXACT_REMOVAL_READY
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED


def test_attempt_workspace_binding_rejects_copied_journal(tmp_path: Path) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "claim_durable")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    shutil.copytree(
        workspace / ".elyndra-mutation-journal",
        replacement / ".elyndra-mutation-journal",
    )
    plan = inspector.inspect(str(replacement), attempt_public_id=attempt)
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    assert plan.recovery_code == "attempt_workspace_mismatch"


def test_manual_attempt_precedes_missing_blockade_rule(tmp_path: Path) -> None:
    database, inspector, attempt, workspace = _publishing_crash(tmp_path, published=1)
    with database.connect() as connection:
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempts "
            "SET state='recovery_required' WHERE public_id=?",
            (attempt,),
        )
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempts "
            "SET state='manual_intervention_required' WHERE public_id=?",
            (attempt,),
        )
    (workspace / ".elyndra-mutation-journal/blockade.json").unlink()
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED


def _append_manifest_record(path: Path, state: str) -> dict[str, object]:
    records = [json.loads(line) for line in path.read_text().splitlines()]
    record = dict(records[-1])
    record.pop("record_sha256")
    record["sequence"] = len(records)
    record["previous_record_sha256"] = records[-1]["record_sha256"]
    record["attempt_state"] = state
    digest = hashlib.sha256(
        b"elyndra.mutation-manifest.v1\0" + _canonical(record)
    ).hexdigest()
    record["record_sha256"] = digest
    with path.open("ab") as stream:
        stream.write(_canonical(record) + b"\n")
    return record


def test_preclaim_requires_exactly_one_record(tmp_path: Path) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "blockade_durable")
    _append_manifest_record(_manifest_path(workspace, attempt), "preparing")
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED


def test_preclaim_requires_preclaim_initial_state(tmp_path: Path) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "blockade_durable")
    path = _manifest_path(workspace, attempt)
    record = json.loads(path.read_text())
    record["attempt_state"] = "preparing"
    record.pop("record_sha256")
    record["record_sha256"] = hashlib.sha256(
        b"elyndra.mutation-manifest.v1\0" + _canonical(record)
    ).hexdigest()
    path.write_bytes(_canonical(record) + b"\n")
    blockade = workspace / ".elyndra-mutation-journal/blockade.json"
    value = json.loads(blockade.read_text())
    value["initial_manifest_sha256"] = record["record_sha256"]
    blockade.write_bytes(_canonical(value))
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED


def test_preclaim_workspace_root_hash_mismatch_is_manual(tmp_path: Path) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "blockade_durable")
    blockade = workspace / ".elyndra-mutation-journal/blockade.json"
    value = json.loads(blockade.read_text())
    value["canonical_workspace_root_sha256"] = "f" * 64
    blockade.write_bytes(_canonical(value))
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED


def test_removal_ready_without_result_is_manual(tmp_path: Path) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "claim_durable")
    blockade = workspace / ".elyndra-mutation-journal/blockade.json"
    value = json.loads(blockade.read_text())
    value["removal_ready"] = True
    blockade.write_bytes(_canonical(value))
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.blockade_state is MutationBlockadeState.EXACT_REMOVAL_READY
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED


@pytest.mark.parametrize("second_outcome", ["filesystem_succeeded", "rolled_back"])
def test_duplicate_outcome_records_are_rejected(
    tmp_path: Path, second_outcome: str
) -> None:
    database, _repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path
    )

    def duplicate(attempt, outcome, manifest, *_args, **_kwargs):
        manifest.append(outcome)
        applicator._sync_manifest(attempt, manifest)
        manifest.append(second_outcome)
        raise SystemExit

    applicator._insert_result = duplicate  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    plan = inspector.inspect(str(workspace), attempt_public_id=_attempt_id(database))
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    assert plan.recovery_code == "manifest_invalid"


def test_outcome_one_record_ahead_of_db_is_adoptable(tmp_path: Path) -> None:
    database, _repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path
    )

    def append_without_sync(_attempt, outcome, manifest, *_args, **_kwargs):
        manifest.append(outcome)
        raise SystemExit

    applicator._insert_result = append_without_sync  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    plan = inspector.inspect(str(workspace), attempt_public_id=_attempt_id(database))
    assert plan.disposition is MutationRecoveryDisposition.ADOPT_DURABLE_OUTCOME
    assert plan.manifest is not None and plan.manifest.db_pointer == "valid_prefix"
    assert plan.outcome_sequence == plan.manifest.sequence
    assert plan.outcome_sha256 == plan.manifest.tail_sha256


def test_records_after_unadopted_outcome_are_manual(tmp_path: Path) -> None:
    database, _repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path
    )

    def append_later_record(_attempt, outcome, manifest, *_args, **_kwargs):
        manifest.append(outcome)
        manifest.append("cleanup_pending")
        raise SystemExit

    applicator._insert_result = append_later_record  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    plan = inspector.inspect(str(workspace), attempt_public_id=_attempt_id(database))
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED


def test_publishing_rejects_exact_rolled_back_physical_state(tmp_path: Path) -> None:
    database, inspector, attempt, workspace = _publishing_crash(
        tmp_path, published=1, total=1
    )
    with database.connect() as connection:
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempt_files "
            "SET state='rollback_intent' WHERE attempt_id=("
            "SELECT id FROM assistant_autonomy_mutation_attempts WHERE public_id=?)",
            (attempt,),
        )
    (workspace / "src/a.py").unlink()
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.files[0].physical_state.value == "exact_rolled_back"
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED


def _emergency_blockade(database: Database, inspector, attempt: str, workspace: Path):
    initial = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert initial.manifest is not None
    with database.connect() as connection:
        row = dict(
            connection.execute(
                "SELECT attempt.*,run.public_id AS run_public_id "
                "FROM assistant_autonomy_mutation_attempts AS attempt "
                "JOIN assistant_autonomy_runs AS run ON run.id=attempt.run_id "
                "WHERE attempt.public_id=?",
                (attempt,),
            ).fetchone()
        )
        vault = connection.execute(
            "SELECT value FROM schema_meta WHERE key='mutation_vault_id'"
        ).fetchone()[0]
    return {
        "domain": "elyndra.mutation-recovery-blockade.v1",
        "format_version": "v1",
        "generation": "emergency",
        "mutation_vault_id": vault,
        "attempt_public_id": attempt,
        "proposal_public_id": row["proposal_public_id"],
        "proposal_sha256": row["proposal_sha256"],
        "gate_id": row["gate_id"],
        "run_public_id": row["run_public_id"],
        "step_id": row["step_id"],
        "workspace_st_dev": row["workspace_st_dev"],
        "workspace_st_ino": row["workspace_st_ino"],
        "workspace_mount_id": row["workspace_mount_id"],
        "canonical_workspace_root_sha256": hashlib.sha256(
            str(workspace.resolve()).encode()
        ).hexdigest(),
        "initial_blockade_sha256": row["initial_blockade_sha256"],
        "manifest_sequence": initial.manifest.sequence,
        "manifest_tail_sha256": initial.manifest.tail_sha256,
        "recovery_code": "missing_blockade",
    }


def test_strict_emergency_blockade_is_correlated(tmp_path: Path) -> None:
    database, inspector, attempt, workspace = _crash_at(tmp_path, "claim_durable")
    value = _emergency_blockade(database, inspector, attempt, workspace)
    blockade = workspace / ".elyndra-mutation-journal/blockade.json"
    blockade.write_bytes(_canonical(value))
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.blockade_state is MutationBlockadeState.EMERGENCY_RECOVERY
    assert plan.disposition is MutationRecoveryDisposition.FAILED_BEFORE_PUBLICATION


@pytest.mark.parametrize("change", ["extra", "tail"])
def test_invalid_emergency_blockade_is_manual(tmp_path: Path, change: str) -> None:
    database, inspector, attempt, workspace = _crash_at(tmp_path, "claim_durable")
    value = _emergency_blockade(database, inspector, attempt, workspace)
    if change == "extra":
        value["unexpected"] = "authority"
    else:
        value["manifest_tail_sha256"] = "f" * 64
    blockade = workspace / ".elyndra-mutation-journal/blockade.json"
    blockade.write_bytes(_canonical(value))
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED


def test_recovery_artifact_mount_mismatch_is_ambiguous() -> None:
    row = _file_row("create")
    stage = _object(1, 20, b"new")
    witness = _ObjectObservation(
        LinuxStat(1, 20, 8, 0o100600, os.geteuid(), os.getegid(), 1),
        3,
        hashlib.sha256(b"new").hexdigest(),
    )
    assert (
        _classify_physical(row, target=None, stage=stage, witness=witness).value
        == "ambiguous"
    )


@pytest.mark.parametrize("missing", ["manifest", "attempt_directory"])
def test_missing_db_attempt_journal_evidence_is_manual(
    tmp_path: Path, missing: str
) -> None:
    _db, inspector, attempt, workspace = _crash_at(tmp_path, "claim_durable")
    attempt_dir = workspace / ".elyndra-mutation-journal" / attempt
    if missing == "manifest":
        (attempt_dir / "manifest.jsonl").unlink()
    else:
        shutil.rmtree(attempt_dir)
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    assert plan.recovery_code == "manifest_missing"


def _set_manual(database: Database, attempt: str) -> None:
    with database.connect() as connection:
        state = connection.execute(
            "SELECT state FROM assistant_autonomy_mutation_attempts WHERE public_id=?",
            (attempt,),
        ).fetchone()[0]
        if state != "recovery_required":
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempts SET state='recovery_required' "
                "WHERE public_id=?",
                (attempt,),
            )
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempts "
            "SET state='manual_intervention_required' WHERE public_id=?",
            (attempt,),
        )


def _delete_attempt_file(database: Database, attempt: str, ordinal: int = 1) -> None:
    with database.connect() as connection:
        connection.execute("DROP TRIGGER trg_autonomy_mutation_attempt_files_no_delete")
        connection.execute(
            "DELETE FROM assistant_autonomy_mutation_attempt_files "
            "WHERE attempt_id=(SELECT id FROM assistant_autonomy_mutation_attempts "
            "WHERE public_id=?) AND ordinal=?",
            (attempt, ordinal),
        )


def _append_cleanup_ready_and_sync(database: Database, workspace: Path, attempt: str) -> None:
    record = _append_manifest_record(_manifest_path(workspace, attempt), "cleanup_ready")
    with database.connect() as connection:
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempts "
            "SET manifest_sequence=?,manifest_tail_sha256=? WHERE public_id=?",
            (record["sequence"], record["record_sha256"], attempt),
        )


def test_manual_without_result_and_without_blockade_remains_manual(tmp_path: Path) -> None:
    database, inspector, attempt, workspace = _publishing_crash(tmp_path, published=1)
    _set_manual(database, attempt)
    (workspace / ".elyndra-mutation-journal/blockade.json").unlink()
    assert (
        inspector.inspect(str(workspace), attempt_public_id=attempt).disposition
        is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    )


@pytest.mark.parametrize("blockade_present", [True, False])
def test_manual_with_immutable_result_always_remains_manual(
    tmp_path: Path, blockade_present: bool
) -> None:
    database, inspector, attempt, workspace = _crash_at(tmp_path, "result_durable")
    _set_manual(database, attempt)
    if not blockade_present:
        (workspace / ".elyndra-mutation-journal/blockade.json").unlink()
    assert (
        inspector.inspect(str(workspace), attempt_public_id=attempt).disposition
        is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    )


def test_partial_published_rows_cannot_complete_filesystem_success(tmp_path: Path) -> None:
    database, inspector, attempt, workspace = _publishing_crash(tmp_path, published=2)
    _delete_attempt_file(database, attempt)
    assert (
        inspector.inspect(str(workspace), attempt_public_id=attempt).disposition
        is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    )


def test_partial_rows_cannot_authorize_rollback(tmp_path: Path) -> None:
    database, inspector, attempt, workspace = _publishing_crash(tmp_path, published=1)
    _delete_attempt_file(database, attempt)
    assert (
        inspector.inspect(str(workspace), attempt_public_id=attempt).disposition
        is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    )


def test_partial_rows_cannot_adopt_filesystem_outcome(tmp_path: Path) -> None:
    database, repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path, _create("src/a.py", b"a"), _create("src/b.py", b"b")
    )

    def append_without_result(attempt, outcome, manifest, *_args, **_kwargs):
        manifest.append(outcome)
        applicator._sync_manifest(attempt, manifest)
        raise SystemExit

    applicator._insert_result = append_without_result  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    attempt = _attempt_id(database)
    _delete_attempt_file(database, attempt)
    assert (
        inspector.inspect(str(workspace), attempt_public_id=attempt).disposition
        is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    )


def test_preparing_partial_stage_with_unchanged_target_is_prepublication_failure(
    tmp_path: Path,
) -> None:
    database, repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path, _create("src/a.py", b"a"), _create("src/b.py", b"b")
    )
    original = applicator._stage
    calls = 0

    def partial_stage(*args):
        nonlocal calls
        if calls:
            raise SystemExit
        calls += 1
        original(*args)

    applicator._stage = partial_stage  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    assert (
        inspector.inspect(str(workspace), attempt_public_id=_attempt_id(database)).disposition
        is MutationRecoveryDisposition.FAILED_BEFORE_PUBLICATION
    )


def test_preparing_missing_file_rows_with_intact_targets_is_prepublication_failure(
    tmp_path: Path,
) -> None:
    database, repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path, _create("src/a.py", b"a"), _create("src/b.py", b"b")
    )
    original = applicator._insert_file
    calls = 0

    def partial_capture(*args):
        nonlocal calls
        if calls:
            raise SystemExit
        calls += 1
        original(*args)

    applicator._insert_file = partial_capture  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    assert (
        inspector.inspect(str(workspace), attempt_public_id=_attempt_id(database)).disposition
        is MutationRecoveryDisposition.FAILED_BEFORE_PUBLICATION
    )


def test_preparing_changed_replace_target_is_stale(tmp_path: Path) -> None:
    old = b"old"
    database, repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path, _replace("src/a.py", old, b"new")
    )
    (workspace / "src/a.py").write_bytes(old)

    def stop(*_args):
        (workspace / "src/a.py").write_bytes(b"changed")
        raise SystemExit

    applicator._stage = stop  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    assert (
        inspector.inspect(str(workspace), attempt_public_id=_attempt_id(database)).disposition
        is MutationRecoveryDisposition.STALE
    )


def test_preparing_create_target_now_exists_is_stale(tmp_path: Path) -> None:
    database, repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path
    )

    def stop(*_args):
        (workspace / "src/new.py").write_bytes(b"foreign")
        raise SystemExit

    applicator._stage = stop  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    assert (
        inspector.inspect(str(workspace), attempt_public_id=_attempt_id(database)).disposition
        is MutationRecoveryDisposition.STALE
    )


def test_prepublication_filesystem_ambiguity_is_manual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, inspector, attempt, workspace = _crash_at(tmp_path, "claim_durable")
    original = recovery_module.openat2

    def fail_target(directory_fd, path, flags, **kwargs):
        if path == "new.py":
            raise MutationRecoveryError("ambiguous target")
        return original(directory_fd, path, flags, **kwargs)

    monkeypatch.setattr(recovery_module, "openat2", fail_target)
    assert (
        inspector.inspect(str(workspace), attempt_public_id=attempt).disposition
        is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    )


def test_cleanup_ready_requires_exact_db_tail_pointer(tmp_path: Path) -> None:
    database, inspector, attempt, workspace = _crash_at(tmp_path, "result_durable")
    _append_manifest_record(_manifest_path(workspace, attempt), "cleanup_ready")
    (workspace / ".elyndra-mutation-journal/blockade.json").unlink()
    assert (
        inspector.inspect(str(workspace), attempt_public_id=attempt).disposition
        is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    )


def test_exact_cleanup_ready_without_blockade_requires_terminalization(tmp_path: Path) -> None:
    database, inspector, attempt, workspace = _crash_at(tmp_path, "result_durable")
    _append_cleanup_ready_and_sync(database, workspace, attempt)
    (workspace / ".elyndra-mutation-journal/blockade.json").unlink()
    assert (
        inspector.inspect(str(workspace), attempt_public_id=attempt).disposition
        is MutationRecoveryDisposition.TERMINALIZATION_REQUIRED
    )


def test_terminal_exact_cleanup_ready_without_blockade_is_already_terminal(
    tmp_path: Path,
) -> None:
    database, _repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path
    )
    _apply(applicator, proposal_id, _proposal_sha(database), gate)
    assert (
        inspector.inspect(str(workspace), attempt_public_id=_attempt_id(database)).disposition
        is MutationRecoveryDisposition.ALREADY_TERMINAL
    )


def test_unsafe_blockade_metadata_returns_manual_plan(tmp_path: Path) -> None:
    _database, inspector, attempt, workspace = _crash_at(tmp_path, "claim_durable")
    (workspace / ".elyndra-mutation-journal/blockade.json").chmod(0o644)
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.blockade_state is MutationBlockadeState.MALFORMED
    assert plan.recovery_code == "blockade_invalid"


def test_arbitrary_blockade_open_error_returns_manual_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _database, inspector, attempt, workspace = _crash_at(tmp_path, "claim_durable")

    def fail_blockade(_journal_fd: int) -> bytes | None:
        raise MutationRecoveryError("read failure")

    monkeypatch.setattr(inspector, "_read_blockade", fail_blockade)
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.blockade_state is MutationBlockadeState.MALFORMED
    assert plan.recovery_code == "blockade_invalid"


def test_real_blockade_enoent_remains_absent(tmp_path: Path) -> None:
    _database, inspector, attempt, workspace = _crash_at(tmp_path, "claim_durable")
    (workspace / ".elyndra-mutation-journal/blockade.json").unlink()
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.blockade_state is MutationBlockadeState.ABSENT
    assert plan.disposition is MutationRecoveryDisposition.EMERGENCY_BLOCKADE_REQUIRED


def _crash_clean_outcome_before_result(
    tmp_path: Path, outcome: str
) -> tuple[Database, MutationRecoveryInspector, str, Path]:
    items = (_create("src/a.py", b"a"), _create("src/b.py", b"b"))
    database, _repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path, *items
    )

    def append_without_result(attempt, observed, manifest, *_args, **_kwargs):
        assert observed == outcome
        manifest.append(observed)
        applicator._sync_manifest(attempt, manifest)
        raise SystemExit

    applicator._insert_result = append_without_result  # type: ignore[method-assign]
    if outcome == "failed_before_publication":
        applicator._stage = (  # type: ignore[method-assign]
            lambda *_args: (_ for _ in ()).throw(RuntimeError("preparation failed"))
        )
    else:
        (workspace / "src/b.py").write_bytes(b"foreign")
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    attempt = _attempt_id(database)
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_attempt_files"
        ).fetchone()[0]
    if count > 1:
        _delete_attempt_file(database, attempt)
    return database, inspector, attempt, workspace


@pytest.mark.parametrize("outcome", ["failed_before_publication", "stale"])
def test_partial_clean_manifest_outcome_is_adoptable(
    tmp_path: Path, outcome: str
) -> None:
    _database, inspector, attempt, workspace = _crash_clean_outcome_before_result(
        tmp_path, outcome
    )
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.outcome_manifest_without_result == outcome
    assert plan.disposition is MutationRecoveryDisposition.ADOPT_DURABLE_OUTCOME


def test_partial_clean_result_with_blockade_requires_cleanup(tmp_path: Path) -> None:
    database, repository, _applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path, _create("src/a.py", b"a"), _create("src/b.py", b"b")
    )

    def fail_stage(*_args):
        raise RuntimeError("preparation failed")

    def crash(point: str) -> None:
        if point == "result_durable":
            raise SystemExit

    runtime = tmp_path / "runtime-clean-result"
    runtime.mkdir(mode=0o700)
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime),
        crash_hook=crash,
    )
    applicator._stage = fail_stage  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    attempt = _attempt_id(database)
    _delete_attempt_file(database, attempt)
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.result_outcome == "failed_before_publication"
    assert plan.disposition is MutationRecoveryDisposition.CLEANUP_REQUIRED


def test_partial_clean_result_with_exact_cleanup_ready_requires_terminalization(
    tmp_path: Path,
) -> None:
    database, repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path, _create("src/a.py", b"a"), _create("src/b.py", b"b")
    )
    applicator._stage = (  # type: ignore[method-assign]
        lambda *_args: (_ for _ in ()).throw(RuntimeError("preparation failed"))
    )
    original_remove = applicator._remove_verified_blockade

    def remove_then_crash(*args):
        original_remove(*args)
        raise SystemExit

    applicator._remove_verified_blockade = remove_then_crash  # type: ignore[method-assign]
    assert _apply(applicator, proposal_id, _proposal_sha(database), gate).outcome == (
        "failed_before_publication"
    )
    attempt = _attempt_id(database)
    _delete_attempt_file(database, attempt)
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.blockade_state is MutationBlockadeState.ABSENT
    assert plan.disposition is MutationRecoveryDisposition.TERMINALIZATION_REQUIRED


def _crash_rolled_back(
    tmp_path: Path, *, before_result: bool
) -> tuple[Database, MutationRecoveryInspector, str, Path]:
    database, repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path, _create("src/a.py", b"a"), _create("src/b.py", b"b")
    )
    original_publish = applicator._publish
    calls = 0

    def fail_during_publication(*args):
        nonlocal calls
        if calls:
            raise RuntimeError("publication failed")
        calls += 1
        return original_publish(*args)

    applicator._publish = fail_during_publication  # type: ignore[method-assign]
    if before_result:
        original_insert = applicator._insert_result

        def append_without_result(attempt, outcome, manifest, *args, **kwargs):
            if outcome == "rolled_back":
                manifest.append(outcome)
                applicator._sync_manifest(attempt, manifest)
                raise SystemExit
            return original_insert(attempt, outcome, manifest, *args, **kwargs)

        applicator._insert_result = append_without_result  # type: ignore[method-assign]
    else:
        applicator._crash_hook = lambda point: (  # type: ignore[method-assign]
            (_ for _ in ()).throw(SystemExit) if point == "result_durable" else None
        )
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    attempt = _attempt_id(database)
    _delete_attempt_file(database, attempt)
    return database, inspector, attempt, workspace


@pytest.mark.parametrize("before_result", [True, False])
def test_partial_coverage_rejects_rolled_back_outcome_recovery(
    tmp_path: Path, before_result: bool
) -> None:
    _database, inspector, attempt, workspace = _crash_rolled_back(
        tmp_path, before_result=before_result
    )
    assert (
        inspector.inspect(str(workspace), attempt_public_id=attempt).disposition
        is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    )


def _force_exact_rolled_back(database: Database, attempt: str) -> None:
    with database.connect() as connection:
        for state in ("publication_intent", "published", "rollback_intent"):
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempt_files SET state=? "
                "WHERE attempt_id=(SELECT id FROM assistant_autonomy_mutation_attempts "
                "WHERE public_id=?)",
                (state, attempt),
            )


@pytest.mark.parametrize("attempt_state", ["preparing", "prepared"])
def test_prepublication_exact_rolled_back_is_manual(
    tmp_path: Path, attempt_state: str
) -> None:
    database, repository, applicator, inspector, proposal_id, gate, workspace = _foundation(
        tmp_path
    )
    if attempt_state == "preparing":
        original_stage = applicator._stage

        def stage_then_stop(*args):
            original_stage(*args)
            raise SystemExit

        applicator._stage = stage_then_stop  # type: ignore[method-assign]
    else:
        original_boundary = applicator._final_boundary

        def boundary_then_stop(*args):
            original_boundary(*args)
            raise SystemExit

        applicator._final_boundary = boundary_then_stop  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal_id, _proposal_sha(database), gate)
    attempt = _attempt_id(database)
    _force_exact_rolled_back(database, attempt)
    (workspace / ".elyndra-mutation-journal" / attempt / "stage-0").unlink()
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert (
        plan.files[0].physical_state
        is recovery_module.MutationRecoveryPhysicalState.EXACT_ROLLED_BACK
    )
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
