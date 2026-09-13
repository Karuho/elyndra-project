from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from test_autonomy_mutation_reconciliation_v0811 import (
    _apply,
    _attempt,
    _coordinator,
    _crash,
    _foundation,
    _records,
    _run_status,
)

import elyndra.autonomy.mutation_recovery as recovery_module
from elyndra.autonomy import MutationItem, MutationOperation
from elyndra.autonomy.mutation_recovery import (
    MutationBlockadeState,
    MutationRecoveryDisposition,
    MutationRecoveryFileObservation,
    MutationRecoveryPhysicalState,
    MutationRecoveryReconciler,
    _rollback_matrix_valid,
)


def _replace(path: str, old: bytes, new: bytes) -> MutationItem:
    return MutationItem(
        path,
        MutationOperation.REPLACE,
        True,
        hashlib.sha256(old).hexdigest(),
        len(old),
        new,
    )


def _partial_publication(tmp_path: Path, *items: MutationItem, published: int = 1):
    values = _foundation(tmp_path, *items)
    database, repository, applicator, inspector, reconciler, proposal, sha, gate, workspace = (
        values
    )
    original = applicator._publish
    calls = 0

    def publish(*args):
        nonlocal calls
        if calls == published:
            raise SystemExit
        original(*args)
        calls += 1
        if calls == published:
            raise SystemExit

    applicator._publish = publish  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal, sha, gate)
    return database, repository, inspector, reconciler, _attempt(database), workspace


def _file_states(database) -> list[str]:
    with database.connect() as connection:
        return [
            str(row[0])
            for row in connection.execute(
                "SELECT state FROM assistant_autonomy_mutation_attempt_files ORDER BY ordinal"
            )
        ]


def _replace_publication(tmp_path: Path):
    old = b"old-content"
    new = b"new-content"
    values = _foundation(
        tmp_path,
        _replace("src/a.py", old, new),
        MutationItem("src/b.py", MutationOperation.CREATE, False, None, None, b"b"),
    )
    database, repository, applicator, inspector, _reconciler, proposal, sha, gate, workspace = (
        values
    )
    target = workspace / "src/a.py"
    target.write_bytes(old)
    original = applicator._publish

    def publish_one(*args):
        original(*args)
        raise SystemExit

    applicator._publish = publish_one  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal, sha, gate)
    return database, repository, inspector, _attempt(database), workspace, old, new


def _reach_rollback_intent(
    tmp_path: Path,
    repository,
    workspace: Path,
    attempt: str,
    *,
    ordinal: int = 0,
):
    coordinator = _coordinator(tmp_path, f"rollback-intent-{ordinal}")

    def stop(point: str) -> None:
        if point == f"recovery_rollback_intent_durable:{ordinal}":
            raise SystemExit

    with pytest.raises(SystemExit):
        MutationRecoveryReconciler(
            repository,
            workspace_lease_coordinator=coordinator,
            crash_hook=stop,
        ).reconcile(str(workspace), attempt_public_id=attempt)
    return coordinator


def _cleanup_ready_initial_blockade(tmp_path: Path):
    database, repository, inspector, _reconciler, attempt, workspace = _crash(
        tmp_path, "result_durable"
    )
    coordinator = _coordinator(tmp_path, "cleanup-ready-initial")

    def stop_append(point: str) -> None:
        if point == "recovery_cleanup_ready_durable":
            raise SystemExit

    with pytest.raises(SystemExit):
        MutationRecoveryReconciler(
            repository,
            workspace_lease_coordinator=coordinator,
            crash_hook=stop_append,
        ).reconcile(str(workspace), attempt_public_id=attempt)
    original_sync = repository._reconcile_mutation_cleanup_manifest_pointer

    def sync_then_stop(**kwargs):
        original_sync(**kwargs)
        raise SystemExit

    repository._reconcile_mutation_cleanup_manifest_pointer = sync_then_stop
    with pytest.raises(SystemExit):
        MutationRecoveryReconciler(
            repository, workspace_lease_coordinator=coordinator
        ).reconcile(str(workspace), attempt_public_id=attempt)
    repository._reconcile_mutation_cleanup_manifest_pointer = original_sync
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.blockade_state is MutationBlockadeState.EXACT_INITIAL
    assert plan.manifest is not None and plan.manifest.db_pointer == "exact_tail"
    return database, repository, inspector, coordinator, attempt, workspace


def _emergency_removal_ready(tmp_path: Path):
    database, repository, inspector, _reconciler, attempt, workspace = _crash(
        tmp_path, "claim_durable"
    )
    blockade = workspace / ".elyndra-mutation-journal/blockade.json"
    blockade.unlink()
    coordinator = _coordinator(tmp_path, "emergency-removal-ready")
    captured: dict[str, bytes] = {}

    def stop(point: str) -> None:
        if point == "emergency_blockade_durable":
            captured["initial"] = blockade.read_bytes()
        if point == "recovery_removal_ready_durable":
            raise SystemExit

    with pytest.raises(SystemExit):
        MutationRecoveryReconciler(
            repository,
            workspace_lease_coordinator=coordinator,
            crash_hook=stop,
        ).reconcile(str(workspace), attempt_public_id=attempt)
    return database, repository, inspector, coordinator, attempt, workspace, captured["initial"]


def test_schema60_refreshes_old_file_transition_trigger_idempotently(tmp_path: Path) -> None:
    database, _repo, _inspector, _reconciler, _attempt_id, _workspace = (
        _partial_publication(
            tmp_path,
            MutationItem("src/a.py", MutationOperation.CREATE, False, None, None, b"a"),
            MutationItem("src/b.py", MutationOperation.CREATE, False, None, None, b"b"),
        )
    )
    old_trigger = """
        CREATE TRIGGER trg_autonomy_mutation_attempt_file_transition
        BEFORE UPDATE OF state ON assistant_autonomy_mutation_attempt_files
        WHEN NOT (
            (OLD.state='planned' AND NEW.state IN
                ('staged','discarded','manual_intervention_required')) OR
            (OLD.state='staged' AND NEW.state IN
                ('publication_intent','discarded','manual_intervention_required')) OR
            (OLD.state='publication_intent' AND NEW.state IN
                ('published','manual_intervention_required')) OR
            (OLD.state='published' AND NEW.state IN
                ('rollback_intent','manual_intervention_required')) OR
            (OLD.state='rollback_intent' AND NEW.state IN
                ('rolled_back','manual_intervention_required'))
        ) BEGIN SELECT RAISE(ABORT, 'old_transition'); END
    """
    with database.connect() as connection:
        connection.execute("DROP TRIGGER trg_autonomy_mutation_attempt_file_transition")
        connection.execute(old_trigger)
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempt_files "
            "SET state='publication_intent' WHERE ordinal=1"
        )
        with pytest.raises(sqlite3.IntegrityError, match="old_transition"):
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempt_files "
                "SET state='discarded' WHERE ordinal=1"
            )
    database.migrate()
    database.migrate()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "61"
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempt_files "
            "SET state='discarded' WHERE ordinal=1"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempt_files "
                "SET state='published' WHERE ordinal=1"
            )


def _observation(db_state: str, physical: MutationRecoveryPhysicalState):
    return MutationRecoveryFileObservation(0, "src/a.py", "create", db_state, physical)


@pytest.mark.parametrize(
    ("attempt_state", "db_state", "physical", "valid"),
    [
        ("publishing", "staged", MutationRecoveryPhysicalState.EXACT_UNPUBLISHED, True),
        ("publishing", "publication_intent", MutationRecoveryPhysicalState.EXACT_PUBLISHED, True),
        ("publishing", "planned", MutationRecoveryPhysicalState.EXACT_UNPUBLISHED, False),
        ("publishing", "rollback_intent", MutationRecoveryPhysicalState.EXACT_PUBLISHED, False),
        ("recovery_required", "planned", MutationRecoveryPhysicalState.EXACT_UNPUBLISHED, True),
        ("recovery_required", "discarded", MutationRecoveryPhysicalState.EXACT_UNPUBLISHED, True),
        (
            "recovery_required",
            "rollback_intent",
            MutationRecoveryPhysicalState.EXACT_PUBLISHED,
            True,
        ),
        ("recovery_required", "rolled_back", MutationRecoveryPhysicalState.EXACT_ROLLED_BACK, True),
        ("recovery_required", "published", MutationRecoveryPhysicalState.EXACT_UNPUBLISHED, False),
        ("recovery_required", "staged", MutationRecoveryPhysicalState.AMBIGUOUS, False),
    ],
)
def test_frozen_rollback_matrix(attempt_state, db_state, physical, valid) -> None:
    assert _rollback_matrix_valid(attempt_state, (_observation(db_state, physical),)) is valid


def test_publication_lag_exact_unpublished_becomes_discarded_without_target_syscall(
    tmp_path: Path,
) -> None:
    database, _repo, _inspector, _reconciler, attempt, workspace = _partial_publication(
        tmp_path,
        MutationItem("src/a.py", MutationOperation.CREATE, False, None, None, b"a"),
        MutationItem("src/b.py", MutationOperation.CREATE, False, None, None, b"b"),
        published=0,
    )
    with database.connect() as connection:
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempt_files "
            "SET state='publication_intent' WHERE ordinal=1"
        )
    reconciler = MutationRecoveryReconciler(
        _repo, workspace_lease_coordinator=_coordinator(tmp_path, "lag-unpublished")
    )
    result = reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is MutationRecoveryDisposition.ALREADY_TERMINAL
    assert not (workspace / "src/b.py").exists()
    assert _file_states(database) == ["discarded", "discarded"]


def test_create_rollback_crash_windows_and_replay_do_not_repeat_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, repository, _inspector, _reconciler, attempt, workspace = (
        _partial_publication(
            tmp_path,
            MutationItem("src/a.py", MutationOperation.CREATE, False, None, None, b"a"),
            MutationItem("src/b.py", MutationOperation.CREATE, False, None, None, b"b"),
        )
    )
    coordinator = _coordinator(tmp_path, "create-rollback")

    def stop_intent(point: str) -> None:
        if point == "recovery_rollback_intent_durable:0":
            raise SystemExit

    crashing = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=coordinator, crash_hook=stop_intent
    )
    with pytest.raises(SystemExit):
        crashing.reconcile(str(workspace), attempt_public_id=attempt)
    assert (workspace / "src/a.py").read_bytes() == b"a"
    assert _file_states(database)[0] == "rollback_intent"

    def stop_syscall(point: str) -> None:
        if point == "recovery_rollback_syscall_durable:0":
            raise SystemExit

    crashing = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=coordinator, crash_hook=stop_syscall
    )
    with pytest.raises(SystemExit):
        crashing.reconcile(str(workspace), attempt_public_id=attempt)
    assert not (workspace / "src/a.py").exists()
    assert _file_states(database)[0] == "rollback_intent"
    target_unlinks = 0
    original_unlink = recovery_module.unlinkat

    def counted(*args, **kwargs):
        nonlocal target_unlinks
        if len(args) > 1 and args[1] == "a.py":
            target_unlinks += 1
        return original_unlink(*args, **kwargs)

    monkeypatch.setattr(recovery_module, "unlinkat", counted)
    fresh = MutationRecoveryReconciler(repository, workspace_lease_coordinator=coordinator)
    result = fresh.reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is MutationRecoveryDisposition.ALREADY_TERMINAL
    assert target_unlinks == 0
    assert _file_states(database)[0] == "rolled_back"
    assert _run_status(database) == "waiting_human"


def test_replace_rollback_restores_exact_original_and_retains_no_model_intent(
    tmp_path: Path,
) -> None:
    old = b"old-content"
    new = b"new-content"
    values = _foundation(
        tmp_path,
        _replace("src/a.py", old, new),
        MutationItem("src/b.py", MutationOperation.CREATE, False, None, None, b"b"),
    )
    database, _repository, applicator, _inspector, reconciler, proposal, sha, gate, workspace = (
        values
    )
    target = workspace / "src/a.py"
    target.write_bytes(old)
    original = applicator._publish

    def publish_one(*args):
        original(*args)
        raise SystemExit

    applicator._publish = publish_one  # type: ignore[method-assign]
    with pytest.raises(SystemExit):
        _apply(applicator, proposal, sha, gate)
    attempt = _attempt(database)
    assert target.read_bytes() == new
    result = reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is MutationRecoveryDisposition.ALREADY_TERMINAL
    assert target.read_bytes() == old
    assert _file_states(database)[0] == "rolled_back"
    assert _run_status(database) == "waiting_human"


@pytest.mark.parametrize("operation", ["create", "replace"])
def test_same_inode_postimage_tamper_refuses_destructive_rollback(
    tmp_path: Path, operation: str
) -> None:
    if operation == "create":
        items = (
            MutationItem("src/a.py", MutationOperation.CREATE, False, None, None, b"a"),
            MutationItem("src/b.py", MutationOperation.CREATE, False, None, None, b"b"),
        )
        database, repository, _inspector, _reconciler, attempt, workspace = (
            _partial_publication(tmp_path, *items)
        )
    else:
        old, new = b"old", b"new"
        values = _foundation(
            tmp_path,
            _replace("src/a.py", old, new),
            MutationItem("src/b.py", MutationOperation.CREATE, False, None, None, b"b"),
        )
        database, repository, applicator, _inspector, _rec, proposal, sha, gate, workspace = (
            values
        )
        (workspace / "src/a.py").write_bytes(old)
        original = applicator._publish
        def publish_one(*args):
            original(*args)
            raise SystemExit

        applicator._publish = publish_one  # type: ignore[method-assign]
        with pytest.raises(SystemExit):
            _apply(applicator, proposal, sha, gate)
        attempt = _attempt(database)
    coordinator = _coordinator(tmp_path, f"tamper-{operation}")

    def stop(point: str) -> None:
        if point == "recovery_rollback_intent_durable:0":
            raise SystemExit

    with pytest.raises(SystemExit):
        MutationRecoveryReconciler(
            repository, workspace_lease_coordinator=coordinator, crash_hook=stop
        ).reconcile(str(workspace), attempt_public_id=attempt)
    target = workspace / "src/a.py"
    target.write_bytes(b"tampered")
    result = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=coordinator
    ).reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    assert target.read_bytes() == b"tampered"


def test_create_metadata_change_refuses_before_unlink_and_preserves_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _database, repository, _inspector, _reconciler, attempt, workspace = (
        _partial_publication(
            tmp_path,
            MutationItem("src/a.py", MutationOperation.CREATE, False, None, None, b"a"),
            MutationItem("src/b.py", MutationOperation.CREATE, False, None, None, b"b"),
        )
    )
    coordinator = _reach_rollback_intent(
        tmp_path, repository, workspace, attempt
    )
    target = workspace / "src/a.py"
    target.chmod(0o640)
    unlinked = False
    original = recovery_module.unlinkat

    def observe_unlink(*args, **kwargs):
        nonlocal unlinked
        if len(args) > 1 and args[1] == "a.py":
            unlinked = True
        return original(*args, **kwargs)

    monkeypatch.setattr(recovery_module, "unlinkat", observe_unlink)
    with pytest.raises(recovery_module.MutationRecoveryError, match="metadata"):
        MutationRecoveryReconciler(
            repository, workspace_lease_coordinator=coordinator
        ).reconcile(str(workspace), attempt_public_id=attempt)
    assert not unlinked
    assert target.read_bytes() == b"a"


@pytest.mark.parametrize("evidence", ["backup", "witness"])
def test_replace_metadata_or_backup_tamper_refuses_before_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence: str
) -> None:
    database, repository, _inspector, attempt, workspace, _old, new = (
        _replace_publication(tmp_path)
    )
    coordinator = _reach_rollback_intent(
        tmp_path, repository, workspace, attempt
    )
    with database.connect() as connection:
        row = connection.execute(
            "SELECT artifact_name,witness_name FROM "
            "assistant_autonomy_mutation_attempt_files WHERE ordinal=0"
        ).fetchone()
    attempt_dir = workspace / ".elyndra-mutation-journal" / attempt
    if evidence == "backup":
        (attempt_dir / row["artifact_name"]).write_bytes(b"tampered-backup")
    else:
        (attempt_dir / row["witness_name"]).chmod(0o640)
    exchanges = 0
    original = recovery_module.renameat2

    def observe_exchange(*args, **kwargs):
        nonlocal exchanges
        if args[-1] == recovery_module.RENAME_EXCHANGE:
            exchanges += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(recovery_module, "renameat2", observe_exchange)
    reconciled = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=coordinator
    )
    if evidence == "backup":
        result = reconciled.reconcile(str(workspace), attempt_public_id=attempt)
        assert result.final_disposition is (
            MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
        )
    else:
        with pytest.raises(recovery_module.MutationRecoveryError, match="metadata"):
            reconciled.reconcile(str(workspace), attempt_public_id=attempt)
    assert exchanges == 0
    assert (workspace / "src/a.py").read_bytes() == new


def test_parent_identity_mismatch_refuses_before_create_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, repository, _inspector, _reconciler, attempt, workspace = (
        _partial_publication(
            tmp_path,
            MutationItem("src/a.py", MutationOperation.CREATE, False, None, None, b"a"),
            MutationItem("src/b.py", MutationOperation.CREATE, False, None, None, b"b"),
        )
    )
    coordinator = _reach_rollback_intent(
        tmp_path, repository, workspace, attempt
    )
    with database.connect() as connection:
        connection.execute(
            "DROP TRIGGER trg_autonomy_mutation_attempt_files_identity_no_update"
        )
        connection.execute(
            "UPDATE assistant_autonomy_mutation_attempt_files "
            "SET parent_st_ino=parent_st_ino+1 WHERE ordinal=0"
        )
    unlinked = False
    original = recovery_module.unlinkat

    def observe_unlink(*args, **kwargs):
        nonlocal unlinked
        if len(args) > 1 and args[1] == "a.py":
            unlinked = True
        return original(*args, **kwargs)

    monkeypatch.setattr(recovery_module, "unlinkat", observe_unlink)
    result = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=coordinator
    ).reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    assert not unlinked and (workspace / "src/a.py").read_bytes() == b"a"


def test_rollback_actions_use_descending_ordinals_one_boundary_at_a_time(
    tmp_path: Path,
) -> None:
    database, repository, _inspector, _reconciler, attempt, workspace = (
        _partial_publication(
            tmp_path,
            MutationItem("src/a.py", MutationOperation.CREATE, False, None, None, b"a"),
            MutationItem("src/b.py", MutationOperation.CREATE, False, None, None, b"b"),
            MutationItem("src/c.py", MutationOperation.CREATE, False, None, None, b"c"),
            published=2,
        )
    )
    coordinator = _reach_rollback_intent(
        tmp_path, repository, workspace, attempt, ordinal=1
    )
    assert _file_states(database) == ["published", "rollback_intent", "staged"]
    assert (workspace / "src/a.py").exists()
    assert (workspace / "src/b.py").exists()

    def stop(point: str) -> None:
        if point == "recovery_rollback_syscall_durable:1":
            raise SystemExit

    with pytest.raises(SystemExit):
        MutationRecoveryReconciler(
            repository,
            workspace_lease_coordinator=coordinator,
            crash_hook=stop,
        ).reconcile(str(workspace), attempt_public_id=attempt)
    assert _file_states(database) == ["published", "rollback_intent", "staged"]
    assert (workspace / "src/a.py").exists()
    assert not (workspace / "src/b.py").exists()


def test_unknown_cleanup_entry_blocks_cleanup_ready_and_is_preserved(tmp_path: Path) -> None:
    database, repository, inspector, _reconciler, attempt, workspace = _crash(
        tmp_path, "result_durable"
    )
    unknown = workspace / ".elyndra-mutation-journal" / attempt / "unknown"
    unknown.write_bytes(b"owner data")
    reconciler = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=_coordinator(tmp_path, "unknown-cleanup")
    )
    with pytest.raises(recovery_module.MutationRecoveryError, match="desconocidos"):
        reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert unknown.read_bytes() == b"owner data"
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.manifest is not None
    assert "cleanup_ready" not in plan.manifest.record_states


def test_changed_cleanup_artifact_is_preserved_fail_closed(tmp_path: Path) -> None:
    _database, repository, _inspector, _reconciler, attempt, workspace = _crash(
        tmp_path, "result_durable"
    )
    artifact = workspace / ".elyndra-mutation-journal" / attempt / "stage-0"
    artifact.write_bytes(b"changed")
    reconciler = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=_coordinator(tmp_path, "changed-cleanup")
    )
    with pytest.raises(recovery_module.MutationRecoveryError, match="no coincide"):
        reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert artifact.read_bytes() == b"changed"


def test_removal_ready_without_cleanup_ready_is_manual(tmp_path: Path) -> None:
    database, _repository, inspector, _reconciler, attempt, workspace = _crash(
        tmp_path, "result_durable"
    )
    blockade = workspace / ".elyndra-mutation-journal/blockade.json"
    value = json.loads(blockade.read_text())
    with database.connect() as connection:
        result = dict(
            connection.execute(
                "SELECT * FROM assistant_autonomy_mutation_results"
            ).fetchone()
        )
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.manifest is not None
    value.update(
        {
            "cleanup_manifest_sequence": plan.manifest.sequence,
            "cleanup_manifest_tail_sha256": plan.manifest.tail_sha256,
            "removal_ready": True,
            "result_final_manifest_sequence": result["final_manifest_sequence"],
            "result_final_manifest_sha256": result["final_manifest_sha256"],
            "result_outcome": result["outcome"],
            "result_public_id": result["public_id"],
            "updated_at": "2026-01-01T00:00:00.000000Z",
        }
    )
    blockade.write_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )
    observed = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert observed.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED


def test_blocked_preclaim_orphan_removes_only_exact_blockade(tmp_path: Path) -> None:
    database, _repository, _inspector, reconciler, attempt, workspace = _crash(
        tmp_path, "blockade_durable"
    )
    attempt_dir = workspace / ".elyndra-mutation-journal" / attempt
    manifest = (attempt_dir / "manifest.jsonl").read_bytes()
    result = reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is MutationRecoveryDisposition.PRECLAIM_ORPHAN_UNBLOCKED
    assert (attempt_dir / "manifest.jsonl").read_bytes() == manifest
    assert not (workspace / ".elyndra-mutation-journal/blockade.json").exists()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_attempts"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_results"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "crash_point",
    [
        "recovery_cleanup_ready_durable",
        "recovery_removal_ready_durable",
        "recovery_blockade_removed",
    ],
)
def test_cleanup_namespace_boundaries_replay_without_duplicates(
    tmp_path: Path, crash_point: str
) -> None:
    database, repository, _inspector, _reconciler, attempt, workspace = _crash(
        tmp_path, "result_durable"
    )
    coordinator = _coordinator(tmp_path, "cleanup-boundary-" + crash_point)

    def stop(point: str) -> None:
        if point == crash_point:
            raise SystemExit

    with pytest.raises(SystemExit):
        MutationRecoveryReconciler(
            repository,
            workspace_lease_coordinator=coordinator,
            crash_hook=stop,
        ).reconcile(str(workspace), attempt_public_id=attempt)
    records = _records(workspace, attempt)
    assert sum(record["attempt_state"] == "cleanup_ready" for record in records) == 1
    result = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=coordinator
    ).reconcile(str(workspace), attempt_public_id=attempt)
    assert result.final_disposition is MutationRecoveryDisposition.ALREADY_TERMINAL
    assert sum(
        record["attempt_state"] == "cleanup_ready"
        for record in _records(workspace, attempt)
    ) == 1
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_results"
        ).fetchone()[0] == 1


def test_preclaim_blockade_removal_crash_replays_to_unblocked(tmp_path: Path) -> None:
    database, repository, _inspector, _reconciler, attempt, workspace = _crash(
        tmp_path, "blockade_durable"
    )
    attempt_dir = workspace / ".elyndra-mutation-journal" / attempt
    manifest = (attempt_dir / "manifest.jsonl").read_bytes()
    coordinator = _coordinator(tmp_path, "preclaim-remove-crash")

    def stop(point: str) -> None:
        if point == "recovery_preclaim_blockade_removed":
            raise SystemExit

    with pytest.raises(SystemExit):
        MutationRecoveryReconciler(
            repository,
            workspace_lease_coordinator=coordinator,
            crash_hook=stop,
        ).reconcile(str(workspace), attempt_public_id=attempt)
    replay = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=coordinator
    ).reconcile(str(workspace), attempt_public_id=attempt)
    assert replay.final_disposition is MutationRecoveryDisposition.PRECLAIM_ORPHAN_UNBLOCKED
    assert (attempt_dir / "manifest.jsonl").read_bytes() == manifest
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_attempts"
        ).fetchone()[0] == 0


def test_removal_ready_refuses_changed_same_vault_lineage_before_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _database, repository, _inspector, coordinator, attempt, workspace = (
        _cleanup_ready_initial_blockade(tmp_path)
    )
    blockade_path = workspace / ".elyndra-mutation-journal/blockade.json"
    changed = json.loads(blockade_path.read_text())
    changed["proposal_sha256"] = "f" * 64
    changed_raw = json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()
    reconciler = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=coordinator
    )
    original_read = reconciler.inspector._read_blockade
    reads = 0

    def change_before_exchange(journal_fd: int):
        nonlocal reads
        reads += 1
        if reads == 3:
            blockade_path.write_bytes(changed_raw)
        return original_read(journal_fd)

    monkeypatch.setattr(reconciler.inspector, "_read_blockade", change_before_exchange)
    with pytest.raises(recovery_module.MutationRecoveryError, match="cambió"):
        reconciler.reconcile(str(workspace), attempt_public_id=attempt)
    assert blockade_path.read_bytes() == changed_raw


def test_exchange_out_mismatch_restores_changed_predecessor_without_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _database, repository, _inspector, coordinator, attempt, workspace = (
        _cleanup_ready_initial_blockade(tmp_path)
    )
    blockade_path = workspace / ".elyndra-mutation-journal/blockade.json"
    changed = json.loads(blockade_path.read_text())
    changed["proposal_sha256"] = "e" * 64
    changed_raw = json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()
    original_rename = recovery_module.renameat2
    injected = False

    def race_exchange(old_fd, old_name, new_fd, new_name, flags):
        nonlocal injected
        if (
            not injected
            and flags == recovery_module.RENAME_EXCHANGE
            and str(old_name).startswith(".blockade-ready-")
        ):
            injected = True
            blockade_path.write_bytes(changed_raw)
        return original_rename(old_fd, old_name, new_fd, new_name, flags)

    monkeypatch.setattr(recovery_module, "renameat2", race_exchange)
    with pytest.raises(recovery_module.MutationRecoveryError, match="exchanged-out"):
        MutationRecoveryReconciler(
            repository, workspace_lease_coordinator=coordinator
        ).reconcile(str(workspace), attempt_public_id=attempt)
    assert injected and blockade_path.read_bytes() == changed_raw
    assert not tuple(blockade_path.parent.glob(".blockade-ready-*"))


def test_emergency_removal_ready_preserves_complete_frozen_lineage(tmp_path: Path) -> None:
    _database, _repository, inspector, _coordinator_value, attempt, workspace, initial = (
        _emergency_removal_ready(tmp_path)
    )
    blockade_path = workspace / ".elyndra-mutation-journal/blockade.json"
    initial_value = json.loads(initial)
    ready_value = json.loads(blockade_path.read_bytes())
    assert set(initial_value) == recovery_module._EMERGENCY_BLOCKADE_KEYS
    assert set(ready_value) == (
        recovery_module._EMERGENCY_BLOCKADE_KEYS
        | recovery_module._REMOVAL_READY_KEYS
    )
    assert all(ready_value[key] == value for key, value in initial_value.items())
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.blockade_state is MutationBlockadeState.EXACT_REMOVAL_READY
    assert plan.disposition is MutationRecoveryDisposition.CLEANUP_REQUIRED


@pytest.mark.parametrize("mutation", ["missing", "extra", "changed"])
def test_invalid_emergency_removal_ready_is_manual_and_preserved(
    tmp_path: Path, mutation: str
) -> None:
    _database, repository, inspector, coordinator, attempt, workspace, _initial = (
        _emergency_removal_ready(tmp_path)
    )
    blockade_path = workspace / ".elyndra-mutation-journal/blockade.json"
    value = json.loads(blockade_path.read_text())
    if mutation == "missing":
        del value["recovery_code"]
    elif mutation == "extra":
        value["unexpected"] = True
    else:
        value["initial_blockade_sha256"] = "0" * 64
    changed = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    blockade_path.write_bytes(changed)
    plan = inspector.inspect(str(workspace), attempt_public_id=attempt)
    assert plan.disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    replay = MutationRecoveryReconciler(
        repository, workspace_lease_coordinator=coordinator
    ).reconcile(str(workspace), attempt_public_id=attempt)
    assert replay.final_disposition is MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
    assert blockade_path.read_bytes() == changed


def test_changed_removal_ready_is_restored_by_tombstone_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _database, repository, _inspector, coordinator, attempt, workspace = (
        _cleanup_ready_initial_blockade(tmp_path)
    )
    blockade_path = workspace / ".elyndra-mutation-journal/blockade.json"

    def stop(point: str) -> None:
        if point == "recovery_removal_ready_durable":
            raise SystemExit

    with pytest.raises(SystemExit):
        MutationRecoveryReconciler(
            repository,
            workspace_lease_coordinator=coordinator,
            crash_hook=stop,
        ).reconcile(str(workspace), attempt_public_id=attempt)
    changed_value = json.loads(blockade_path.read_text())
    changed_value["result_public_id"] = "changed-result"
    changed = json.dumps(
        changed_value, sort_keys=True, separators=(",", ":")
    ).encode()
    original_rename = recovery_module.renameat2
    injected = False

    def race_tombstone(old_fd, old_name, new_fd, new_name, flags):
        nonlocal injected
        if (
            not injected
            and old_name == "blockade.json"
            and str(new_name).startswith(".blockade-remove-")
        ):
            injected = True
            blockade_path.write_bytes(changed)
        return original_rename(old_fd, old_name, new_fd, new_name, flags)

    monkeypatch.setattr(recovery_module, "renameat2", race_tombstone)
    with pytest.raises(recovery_module.MutationRecoveryError, match="Tombstone"):
        MutationRecoveryReconciler(
            repository, workspace_lease_coordinator=coordinator
        ).reconcile(str(workspace), attempt_public_id=attempt)
    assert injected and blockade_path.read_bytes() == changed
