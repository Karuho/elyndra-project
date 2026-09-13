from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

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
from elyndra.autonomy.linux_fs import LinuxFilesystemError
from elyndra.autonomy.mutation_application import (
    MutationApplicationError,
    MutationApplicator,
    _is_enoent,
)
from elyndra.db import Database


def _approved(
    tmp_path: Path,
    *items: MutationItem,
    grant_minutes: int = 60,
) -> tuple[Database, AutonomyRepository, MutationApplicator, MutationProposal, str, Path]:
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
            expires_at=now + timedelta(minutes=grant_minutes),
            max_steps=1,
        ),
        plan=RunPlan(
            objective="exact mutation",
            steps=(RunStep("mutate", Capability.SELF_MODIFY, "apply exact files"),),
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
        proposal, request_key="proposal", actor="owner"
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
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime),
    )
    return database, repository, applicator, proposal, review.gate_id, workspace


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


def _apply(applicator: MutationApplicator, proposal: MutationProposal, gate: str):
    return applicator.apply(
        _proposal_id(applicator),
        proposal.proposal_sha256,
        gate,
        actor="owner",
        apply_request_key="apply-once",
    )


def _proposal_id(applicator: MutationApplicator) -> str:
    with applicator.repository.database.connect() as connection:
        return str(
            connection.execute(
                "SELECT public_id FROM assistant_autonomy_mutation_proposals"
            ).fetchone()[0]
        )


def test_exact_approved_review_creates_one_successful_attempt(tmp_path: Path) -> None:
    database, _repository, applicator, proposal, gate, workspace = _approved(tmp_path)
    result = _apply(applicator, proposal, gate)
    assert result.state == "succeeded"
    assert result.outcome == "filesystem_succeeded"
    assert (workspace / "src/new.py").read_bytes() == b"new\n"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_attempts"
        ).fetchone()[0] == 1


def test_exact_terminal_replay_is_read_only_even_after_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import elyndra.autonomy.repository as repository_module

    database, _repository, applicator, proposal, gate, _workspace = _approved(tmp_path)
    first = _apply(applicator, proposal, gate)
    monkeypatch.setattr(
        repository_module,
        "_utcnow",
        lambda: proposal.expires_at + timedelta(days=1),
    )
    replay = _apply(applicator, proposal, gate)
    assert replay.attempt_public_id == first.attempt_public_id
    assert replay.replayed is True


def test_conflicting_request_key_is_denied(tmp_path: Path) -> None:
    _database, _repository, applicator, proposal, gate, _workspace = _approved(tmp_path)
    _apply(applicator, proposal, gate)
    with pytest.raises(PermissionError, match="consumida"):
        applicator.apply(
            _proposal_id(applicator),
            proposal.proposal_sha256,
            gate,
            actor="owner",
            apply_request_key="different-key",
        )


def test_create_uses_exact_bytes_and_removes_blockade(tmp_path: Path) -> None:
    content = "á\nno-normalize\r\n".encode()
    _db, _repo, applicator, proposal, gate, workspace = _approved(
        tmp_path, _create(content=content)
    )
    _apply(applicator, proposal, gate)
    assert (workspace / "src/new.py").read_bytes() == content
    assert not (workspace / ".elyndra-mutation-journal/blockade.json").exists()


def test_replace_exchange_preserves_mode_and_installs_exact_bytes(tmp_path: Path) -> None:
    old, new = b"old\n", b"new exact\n"
    database, _repo, applicator, proposal, gate, workspace = _approved(
        tmp_path, _replace("src/existing.py", old, new)
    )
    target = workspace / "src/existing.py"
    target.write_bytes(old)
    target.chmod(0o640)
    _apply(applicator, proposal, gate)
    assert target.read_bytes() == new
    assert target.stat().st_mode & 0o777 == 0o640
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM assistant_autonomy_mutation_attempt_files"
        ).fetchone()
        assert row["preimage_st_ino"] is not None
        assert row["stage_st_ino"] is not None


def test_stale_preimage_before_capture_is_clean_stale(tmp_path: Path) -> None:
    old = b"old\n"
    _db, _repo, applicator, proposal, gate, workspace = _approved(
        tmp_path, _replace("src/existing.py", old, b"new\n")
    )
    (workspace / "src/existing.py").write_bytes(b"changed\n")
    result = _apply(applicator, proposal, gate)
    assert result.state == "stale"
    assert result.outcome == "stale"
    assert (workspace / "src/existing.py").read_bytes() == b"changed\n"


def test_manifest_then_blockade_precede_database_claim(tmp_path: Path) -> None:
    observations: list[str] = []
    database, repository, _applicator, proposal, gate, workspace = _approved(tmp_path)

    def hook(point: str) -> None:
        observations.append(point)
        if point == "blockade_durable":
            blockade = workspace / ".elyndra-mutation-journal/blockade.json"
            assert blockade.exists()
            with database.connect() as connection:
                assert connection.execute(
                    "SELECT COUNT(*) FROM assistant_autonomy_mutation_attempts"
                ).fetchone()[0] == 0

    runtime = tmp_path / "runtime-2"
    runtime.mkdir(mode=0o700)
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime),
        crash_hook=hook,
    )
    _apply(applicator, proposal, gate)
    assert observations[:3] == ["manifest_durable", "blockade_durable", "claim_durable"]


def test_blockade_before_claim_crash_leaves_classifiable_orphan(tmp_path: Path) -> None:
    database, repository, _applicator, proposal, gate, workspace = _approved(tmp_path)

    def hook(point: str) -> None:
        if point == "blockade_durable":
            raise SystemExit("crash")

    runtime = tmp_path / "runtime-2"
    runtime.mkdir(mode=0o700)
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime),
        crash_hook=hook,
    )
    with pytest.raises(SystemExit):
        _apply(applicator, proposal, gate)
    blockade = workspace / ".elyndra-mutation-journal/blockade.json"
    assert blockade.exists()
    attempt_dir = next(
        path
        for path in (workspace / ".elyndra-mutation-journal").iterdir()
        if path.is_dir()
    )
    assert (attempt_dir / "manifest.jsonl").exists()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_attempts"
        ).fetchone()[0] == 0


def test_blockade_is_canonical_bounded_and_contains_no_source(tmp_path: Path) -> None:
    secret_marker = b"unique-source-marker\n"
    _db, repository, _applicator, proposal, gate, workspace = _approved(
        tmp_path, _create(content=secret_marker)
    )

    def hook(point: str) -> None:
        if point == "blockade_durable":
            raise SystemExit

    runtime = tmp_path / "runtime-2"
    runtime.mkdir(mode=0o700)
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime),
        crash_hook=hook,
    )
    with pytest.raises(SystemExit):
        _apply(applicator, proposal, gate)
    raw = (workspace / ".elyndra-mutation-journal/blockade.json").read_bytes()
    value = json.loads(raw)
    assert raw == json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert secret_marker not in raw
    assert value["mutation_vault_id"]
    assert "initial_blockade_sha256" not in value


def test_run_always_remains_waiting_human(tmp_path: Path) -> None:
    database, _repository, applicator, proposal, gate, _workspace = _approved(tmp_path)
    _apply(applicator, proposal, gate)
    with database.connect() as connection:
        assert connection.execute(
            "SELECT status FROM assistant_autonomy_runs"
        ).fetchone()[0] == "waiting_human"


def test_schema_remains_60_and_no_generic_events_disclose_content(tmp_path: Path) -> None:
    marker = b"private-marker\n"
    database, _repository, applicator, proposal, gate, _workspace = _approved(
        tmp_path, _create(content=marker)
    )
    _apply(applicator, proposal, gate)
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "60"
        payloads = "".join(
            str(row[0])
            for row in connection.execute("SELECT payload_json FROM assistant_autonomy_events")
        )
    assert marker.decode().strip() not in payloads


def test_module_has_no_public_surface_exposure() -> None:
    root = Path(__file__).parents[1] / "src/elyndra"
    for relative in ("application.py", "cli.py", "cognitive_loop.py", "web/app.py"):
        path = root / relative
        if path.exists():
            assert "MutationApplicator" not in path.read_text(encoding="utf-8")


def test_stage_and_witness_record_same_inode(tmp_path: Path) -> None:
    database, _repository, applicator, proposal, gate, _workspace = _approved(tmp_path)
    _apply(applicator, proposal, gate)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT stage_st_dev,stage_st_ino FROM assistant_autonomy_mutation_attempt_files"
        ).fetchone()
    assert row["stage_st_dev"] is not None and row["stage_st_ino"] is not None


def test_canonical_publication_order(tmp_path: Path) -> None:
    _db, _repo, applicator, proposal, gate, workspace = _approved(
        tmp_path, _create("src/z.py", b"z"), _create("src/a.py", b"a")
    )
    _apply(applicator, proposal, gate)
    attempt_dir = next(
        path
        for path in (workspace / ".elyndra-mutation-journal").iterdir()
        if path.is_dir()
    )
    manifest_text = (attempt_dir / "manifest.jsonl").read_text()
    records = [json.loads(line) for line in manifest_text.splitlines()]
    published = [
        record["files"][0]["relative_path"]
        for record in records
        if record["files"] and record["files"][0]["state"] == "published"
    ]
    assert published == ["src/a.py", "src/z.py"]


def test_create_noreplace_race_rolls_back_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db, _repo, applicator, proposal, gate, workspace = _approved(tmp_path)
    original_intent = applicator._publication_intent

    def race(attempt, file, manifest):
        original_intent(attempt, file, manifest)
        (workspace / file.item.relative_path).write_bytes(b"racer\n")

    monkeypatch.setattr(applicator, "_publication_intent", race)
    result = _apply(applicator, proposal, gate)
    assert result.state == "rolled_back"
    assert (workspace / "src/new.py").read_bytes() == b"racer\n"


def test_cleanup_failure_keeps_blockade_and_cleanup_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db, _repo, applicator, proposal, gate, workspace = _approved(tmp_path)
    monkeypatch.setattr(applicator, "_cleanup", lambda *args: (_ for _ in ()).throw(OSError()))
    result = _apply(applicator, proposal, gate)
    assert result.state == "cleanup_pending"
    assert result.outcome == "filesystem_succeeded"
    assert (workspace / ".elyndra-mutation-journal/blockade.json").exists()


def test_immutable_result_precedes_cleanup_terminalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, _repo, applicator, proposal, gate, _workspace = _approved(tmp_path)

    def inspect(*_args) -> None:
        with database.connect() as connection:
            assert connection.execute(
                "SELECT outcome FROM assistant_autonomy_mutation_results"
            ).fetchone()[0] == "filesystem_succeeded"
            assert connection.execute(
                "SELECT state FROM assistant_autonomy_mutation_attempts"
            ).fetchone()[0] == "cleanup_pending"
        raise OSError

    monkeypatch.setattr(applicator, "_cleanup", inspect)
    assert _apply(applicator, proposal, gate).state == "cleanup_pending"


def test_no_source_or_diff_in_manifest(tmp_path: Path) -> None:
    marker = b"manifest-private-source-marker\n"
    _db, _repo, applicator, proposal, gate, workspace = _approved(
        tmp_path, _create(content=marker)
    )
    _apply(applicator, proposal, gate)
    attempt_dir = next(
        path
        for path in (workspace / ".elyndra-mutation-journal").iterdir()
        if path.is_dir()
    )
    raw = (attempt_dir / "manifest.jsonl").read_bytes()
    assert marker not in raw
    assert b'"diff"' not in raw


def test_attempt_initial_blockade_digest_matches_exact_bytes(tmp_path: Path) -> None:
    database, repository, _applicator, proposal, gate, workspace = _approved(tmp_path)

    def hook(point: str) -> None:
        if point == "claim_durable":
            raise SystemExit

    runtime = tmp_path / "runtime-2"
    runtime.mkdir(mode=0o700)
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime),
        crash_hook=hook,
    )
    with pytest.raises(SystemExit):
        _apply(applicator, proposal, gate)
    raw = (workspace / ".elyndra-mutation-journal/blockade.json").read_bytes()
    with database.connect() as connection:
        stored = connection.execute(
            "SELECT initial_blockade_sha256 FROM assistant_autonomy_mutation_attempts"
        ).fetchone()[0]
    assert stored == hashlib.sha256(raw).hexdigest()


def test_manifest_hash_chain_is_domain_separated_and_complete(tmp_path: Path) -> None:
    _db, _repo, applicator, proposal, gate, workspace = _approved(tmp_path)
    _apply(applicator, proposal, gate)
    attempt_dir = next(
        path
        for path in (workspace / ".elyndra-mutation-journal").iterdir()
        if path.is_dir()
    )
    previous = "0" * 64
    for sequence, line in enumerate((attempt_dir / "manifest.jsonl").read_bytes().splitlines()):
        record = json.loads(line)
        claimed = record.pop("record_sha256")
        canonical = json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        assert claimed == hashlib.sha256(
            b"elyndra.mutation-manifest.v1\0" + canonical
        ).hexdigest()
        assert record["sequence"] == sequence
        assert record["previous_record_sha256"] == previous
        previous = claimed


def test_attempt_directory_is_private(tmp_path: Path) -> None:
    _db, _repo, applicator, proposal, gate, workspace = _approved(tmp_path)
    _apply(applicator, proposal, gate)
    attempt_dir = next(
        path
        for path in (workspace / ".elyndra-mutation-journal").iterdir()
        if path.is_dir()
    )
    assert attempt_dir.stat().st_mode & 0o777 == 0o700


def test_apply_request_key_is_hashed_in_blockade(tmp_path: Path) -> None:
    _db, repository, _applicator, proposal, gate, workspace = _approved(tmp_path)

    def hook(point: str) -> None:
        if point == "blockade_durable":
            raise SystemExit

    runtime = tmp_path / "runtime-2"
    runtime.mkdir(mode=0o700)
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime),
        crash_hook=hook,
    )
    with pytest.raises(SystemExit):
        _apply(applicator, proposal, gate)
    value = json.loads(
        (workspace / ".elyndra-mutation-journal/blockade.json").read_text()
    )
    assert value["apply_request_key_sha256"] == hashlib.sha256(b"apply-once").hexdigest()
    assert "apply-once" not in value.values()


def test_terminal_state_is_after_blockade_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, _repo, applicator, proposal, gate, workspace = _approved(tmp_path)
    original = applicator._set_attempt

    def observe(attempt, state, **fields):
        if state == "succeeded":
            assert not (workspace / ".elyndra-mutation-journal/blockade.json").exists()
        return original(attempt, state, **fields)

    monkeypatch.setattr(applicator, "_set_attempt", observe)
    assert _apply(applicator, proposal, gate).state == "succeeded"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT state FROM assistant_autonomy_mutation_attempts"
        ).fetchone()[0] == "succeeded"


def test_no_publication_occurs_until_every_file_is_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, _repo, applicator, proposal, gate, _workspace = _approved(
        tmp_path, _create("src/a.py"), _create("src/b.py")
    )
    original = applicator._publish
    calls = 0

    def inspect(attempt, file, attempt_fd, identity, manifest):
        nonlocal calls
        if calls == 0:
            with database.connect() as connection:
                states = {
                    row[0]
                    for row in connection.execute(
                        "SELECT state FROM assistant_autonomy_mutation_attempt_files"
                    )
                }
            assert states <= {"staged", "publication_intent"}
        calls += 1
        return original(attempt, file, attempt_fd, identity, manifest)

    monkeypatch.setattr(applicator, "_publish", inspect)
    assert _apply(applicator, proposal, gate).state == "succeeded"


def test_no_generic_cancellation_parameter_or_run_resume_surface() -> None:
    import inspect

    signature = inspect.signature(MutationApplicator.apply)
    assert "cancellation" not in signature.parameters
    assert "resume" not in signature.parameters


def test_create_target_must_be_absent_at_capture(tmp_path: Path) -> None:
    _db, _repo, applicator, proposal, gate, workspace = _approved(tmp_path)
    (workspace / "src/new.py").write_bytes(b"existing")
    result = _apply(applicator, proposal, gate)
    assert result.state == "stale"
    assert (workspace / "src/new.py").read_bytes() == b"existing"


def test_replace_requires_single_link_preimage(tmp_path: Path) -> None:
    old = b"old\n"
    _db, _repo, applicator, proposal, gate, workspace = _approved(
        tmp_path, _replace("src/existing.py", old, b"new")
    )
    target = workspace / "src/existing.py"
    target.write_bytes(old)
    os.link(target, workspace / "src/other-link.py")
    assert _apply(applicator, proposal, gate).state == "stale"


def test_replace_rejects_symlink_target(tmp_path: Path) -> None:
    old = b"old\n"
    _db, _repo, applicator, proposal, gate, workspace = _approved(
        tmp_path, _replace("src/existing.py", old, b"new")
    )
    outside = workspace / "outside.py"
    outside.write_bytes(old)
    (workspace / "src/existing.py").symlink_to(outside)
    result = _apply(applicator, proposal, gate)
    assert result.state == "failed_before_publication"
    assert outside.read_bytes() == old


def test_expired_new_claim_is_denied_after_durable_preclaim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import elyndra.autonomy.repository as repository_module

    database, _repo, applicator, proposal, gate, workspace = _approved(tmp_path)
    monkeypatch.setattr(
        repository_module,
        "_utcnow",
        lambda: proposal.expires_at + timedelta(seconds=1),
    )
    with pytest.raises(PermissionError, match="expiró"):
        _apply(applicator, proposal, gate)
    assert (workspace / ".elyndra-mutation-journal/blockade.json").exists()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_attempts"
        ).fetchone()[0] == 0


def test_expiry_at_final_boundary_yields_clean_expired(tmp_path: Path, monkeypatch) -> None:
    _db, _repo, applicator, proposal, gate, workspace = _approved(tmp_path)
    monkeypatch.setattr(applicator, "_final_boundary", lambda *_args: "expired")
    result = _apply(applicator, proposal, gate)
    assert result.state == "expired"
    assert result.outcome == "expired"
    assert not (workspace / "src/new.py").exists()
    assert not (workspace / ".elyndra-mutation-journal/blockade.json").exists()


def test_expiry_after_publishing_does_not_interrupt_consistency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import elyndra.autonomy.mutation_application as module

    _db, _repo, applicator, proposal, gate, workspace = _approved(
        tmp_path, _create("src/a.py"), _create("src/b.py")
    )
    original = applicator._publish
    calls = 0

    def publish(*args):
        nonlocal calls
        result = original(*args)
        calls += 1
        if calls == 1:
            class FutureDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return proposal.expires_at + timedelta(days=1)

            monkeypatch.setattr(module, "datetime", FutureDateTime)
        return result

    monkeypatch.setattr(applicator, "_publish", publish)
    result = _apply(applicator, proposal, gate)
    assert result.state == "succeeded"
    assert (workspace / "src/a.py").exists()
    assert (workspace / "src/b.py").exists()


def test_partial_failure_rolls_back_in_reverse_publication_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db, _repo, applicator, proposal, gate, workspace = _approved(
        tmp_path,
        _create("src/a.py", b"a"),
        _create("src/b.py", b"b"),
        _create("src/c.py", b"c"),
    )
    original_publish = applicator._publish
    original_rollback = applicator._rollback_file
    publications = 0
    rollback_order: list[str] = []

    def publish(*args):
        nonlocal publications
        publications += 1
        if publications == 3:
            raise OSError("injected publication failure")
        return original_publish(*args)

    def rollback(attempt, file, attempt_fd, manifest):
        rollback_order.append(file.item.relative_path)
        return original_rollback(attempt, file, attempt_fd, manifest)

    monkeypatch.setattr(applicator, "_publish", publish)
    monkeypatch.setattr(applicator, "_rollback_file", rollback)
    result = _apply(applicator, proposal, gate)
    assert result.state == "rolled_back"
    assert rollback_order == ["src/b.py", "src/a.py"]
    assert not (workspace / "src/a.py").exists()
    assert not (workspace / "src/b.py").exists()


def test_create_rollback_requires_exact_witness_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db, _repo, applicator, proposal, gate, workspace = _approved(
        tmp_path, _create("src/a.py"), _create("src/b.py")
    )
    original = applicator._publish
    calls = 0

    def publish(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            target = workspace / "src/a.py"
            target.unlink()
            target.write_bytes(b"foreign")
            raise OSError("fail second")
        return original(*args)

    monkeypatch.setattr(applicator, "_publish", publish)
    result = _apply(applicator, proposal, gate)
    assert result.state == "manual_intervention_required"
    assert (workspace / ".elyndra-mutation-journal/blockade.json").exists()
    assert (workspace / "src/a.py").read_bytes() == b"foreign"


def test_replace_rollback_restores_exact_original_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = b"old\n"
    _db, _repo, applicator, proposal, gate, workspace = _approved(
        tmp_path,
        _replace("src/a.py", old, b"new\n"),
        _create("src/b.py"),
    )
    target = workspace / "src/a.py"
    target.write_bytes(old)
    original_inode = target.stat().st_ino
    original = applicator._publish
    calls = 0

    def publish(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("fail second")
        return original(*args)

    monkeypatch.setattr(applicator, "_publish", publish)
    result = _apply(applicator, proposal, gate)
    assert result.state == "rolled_back"
    assert target.read_bytes() == old
    assert target.stat().st_ino == original_inode


def test_failed_preparation_has_immutable_clean_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, _repo, applicator, proposal, gate, workspace = _approved(tmp_path)
    monkeypatch.setattr(
        applicator,
        "_stage",
        lambda *_args: (_ for _ in ()).throw(OSError("stage failure")),
    )
    result = _apply(applicator, proposal, gate)
    assert result.state == "failed_before_publication"
    assert not (workspace / "src/new.py").exists()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT outcome FROM assistant_autonomy_mutation_results"
        ).fetchone()[0] == "failed_before_publication"


def test_root_fd_identity_rejects_workspace_path_swap(tmp_path: Path) -> None:
    _db, _repo, applicator, _proposal, _gate, workspace = _approved(tmp_path)
    identity = applicator.coordinator.identity(workspace)
    original = tmp_path / "original-workspace"
    workspace.rename(original)
    workspace.mkdir()
    (workspace / ".elyndra-mutation-journal").mkdir(mode=0o700)

    with pytest.raises(MutationApplicationError, match="Root fd"):
        applicator._create_attempt_dir(identity, "must-not-exist")

    assert not (workspace / ".elyndra-mutation-journal/must-not-exist").exists()


def test_concurrent_exact_replay_rechecks_under_exclusive_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, repository, applicator, proposal, gate, workspace = _approved(tmp_path)
    original = repository._mutation_application_replay
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    early_calls = 0

    def synchronized(**kwargs):
        nonlocal early_calls
        with lock:
            early_calls += 1
            call = early_calls
        result = original(**kwargs)
        if call <= 2:
            barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(repository, "_mutation_application_replay", synchronized)
    results: list[object] = []

    def run() -> None:
        try:
            results.append(_apply(applicator, proposal, gate))
        except BaseException as exc:  # assertion captures thread failures
            results.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(results) == 2
    assert not [result for result in results if isinstance(result, BaseException)]
    assert len({result.attempt_public_id for result in results}) == 1  # type: ignore[union-attr]
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_attempts"
        ).fetchone()[0] == 1
    journal_dirs = [
        path
        for path in (workspace / ".elyndra-mutation-journal").iterdir()
        if path.is_dir()
    ]
    assert len(journal_dirs) == 1
    assert not (workspace / ".elyndra-mutation-journal/blockade.json").exists()


def test_historical_replay_does_not_resolve_live_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db, _repo, applicator, proposal, gate, workspace = _approved(tmp_path)
    first = _apply(applicator, proposal, gate)
    workspace.rename(tmp_path / "workspace-gone")
    monkeypatch.setattr(
        applicator.coordinator,
        "identity",
        lambda _root: (_ for _ in ()).throw(AssertionError("workspace resolved")),
    )
    replay = _apply(applicator, proposal, gate)
    assert replay.attempt_public_id == first.attempt_public_id
    assert replay.replayed


def test_only_structured_enoent_means_absent() -> None:
    missing = LinuxFilesystemError("opaque")
    missing.__cause__ = OSError(errno.ENOENT, "missing")
    denied = LinuxFilesystemError("errno=2 misleading text")
    denied.__cause__ = OSError(errno.EACCES, "denied")
    assert _is_enoent(missing)
    assert not _is_enoent(denied)


def test_target_match_fails_closed_on_non_enoent_open_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import elyndra.autonomy.mutation_application as module

    _db, _repo, applicator, proposal, gate, _workspace = _approved(tmp_path)
    seen: list[object] = []
    original = applicator._final_boundary

    def boundary(attempt, persisted, identity, receipt, files):
        seen.extend(files)
        return original(attempt, persisted, identity, receipt, files)

    real_openat2 = module.openat2

    def denied(directory_fd, path, flags, **kwargs):
        if seen and path == seen[0].target_name:
            error = LinuxFilesystemError("open failure")
            error.__cause__ = OSError(errno.EACCES, "denied")
            raise error
        return real_openat2(directory_fd, path, flags, **kwargs)

    monkeypatch.setattr(applicator, "_final_boundary", boundary)
    monkeypatch.setattr(module, "openat2", denied)
    result = _apply(applicator, proposal, gate)
    assert result.state == "failed_before_publication"


def test_create_same_inode_content_change_refuses_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db, _repo, applicator, proposal, gate, workspace = _approved(
        tmp_path, _create("src/a.py", b"original-postimage"), _create("src/b.py")
    )
    original = applicator._publish
    calls = 0

    def publish(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            (workspace / "src/a.py").write_bytes(b"modified-in-place")
            raise OSError("second publication failed")
        return original(*args)

    monkeypatch.setattr(applicator, "_publish", publish)
    result = _apply(applicator, proposal, gate)
    assert result.state == "manual_intervention_required"
    assert (workspace / "src/a.py").read_bytes() == b"modified-in-place"


def test_replace_same_inode_postimage_change_refuses_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = b"old"
    _db, _repo, applicator, proposal, gate, workspace = _approved(
        tmp_path, _replace("src/a.py", old, b"new"), _create("src/b.py")
    )
    (workspace / "src/a.py").write_bytes(old)
    original = applicator._publish
    calls = 0

    def publish(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            (workspace / "src/a.py").write_bytes(b"tampered")
            raise OSError("second publication failed")
        return original(*args)

    monkeypatch.setattr(applicator, "_publish", publish)
    result = _apply(applicator, proposal, gate)
    assert result.state == "manual_intervention_required"
    assert (workspace / "src/a.py").read_bytes() == b"tampered"


def test_replace_same_inode_backup_change_refuses_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = b"old"
    _db, _repo, applicator, proposal, gate, workspace = _approved(
        tmp_path, _replace("src/a.py", old, b"new"), _create("src/b.py")
    )
    (workspace / "src/a.py").write_bytes(old)
    original = applicator._publish
    calls = 0

    def publish(attempt, file, attempt_fd, identity, manifest):
        nonlocal calls
        calls += 1
        if calls == 2:
            fd = os.open("stage-0", os.O_WRONLY | os.O_TRUNC, dir_fd=attempt_fd)
            try:
                os.write(fd, b"tampered-backup")
            finally:
                os.close(fd)
            raise OSError("second publication failed")
        return original(attempt, file, attempt_fd, identity, manifest)

    monkeypatch.setattr(applicator, "_publish", publish)
    result = _apply(applicator, proposal, gate)
    assert result.state == "manual_intervention_required"
    assert (workspace / "src/a.py").read_bytes() == b"new"


def test_result_durable_failure_never_rolls_back_or_inserts_second_result(
    tmp_path: Path
) -> None:
    def hook(point: str) -> None:
        if point == "result_durable":
            raise OSError("after result")

    database, repository, _applicator, proposal, gate, workspace = _approved(tmp_path)
    runtime = tmp_path / "runtime-2"
    runtime.mkdir(mode=0o700)
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime),
        crash_hook=hook,
    )
    result = _apply(applicator, proposal, gate)
    assert result.outcome == "filesystem_succeeded"
    assert (workspace / "src/new.py").read_bytes() == b"new\n"
    assert (workspace / ".elyndra-mutation-journal/blockade.json").exists()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_results"
        ).fetchone()[0] == 1


def test_stale_result_durable_failure_does_not_insert_second_result(
    tmp_path: Path
) -> None:
    def hook(point: str) -> None:
        if point == "result_durable":
            raise OSError("after stale result")

    database, repository, _applicator, proposal, gate, workspace = _approved(tmp_path)
    (workspace / "src/new.py").write_bytes(b"foreign")
    runtime = tmp_path / "runtime-2"
    runtime.mkdir(mode=0o700)
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime),
        crash_hook=hook,
    )
    with pytest.raises(OSError, match="after stale result"):
        _apply(applicator, proposal, gate)
    assert (workspace / "src/new.py").read_bytes() == b"foreign"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_results"
        ).fetchone()[0] == 1


@pytest.mark.parametrize("replacement", [b"{}", b"{"])
def test_changed_or_malformed_blockade_is_preserved(
    tmp_path: Path, replacement: bytes
) -> None:
    database, repository, _applicator, proposal, gate, workspace = _approved(tmp_path)

    def hook(point: str) -> None:
        if point == "result_durable":
            blockade = workspace / ".elyndra-mutation-journal/blockade.json"
            blockade.write_bytes(replacement)
            blockade.chmod(0o600)

    runtime = tmp_path / "runtime-2"
    runtime.mkdir(mode=0o700)
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime),
        crash_hook=hook,
    )
    result = _apply(applicator, proposal, gate)
    assert result.state == "cleanup_pending"
    assert (workspace / ".elyndra-mutation-journal/blockade.json").read_bytes() == replacement
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_results"
        ).fetchone()[0] == 1


def test_removal_ready_binds_exact_result_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, _repo, applicator, proposal, gate, _workspace = _approved(tmp_path)
    observed: dict[str, object] = {}
    original = applicator._replace_blockade

    def inspect(journal_fd: int, payload: bytes, **kwargs) -> None:
        observed.update(json.loads(payload))
        original(journal_fd, payload, **kwargs)

    monkeypatch.setattr(applicator, "_replace_blockade", inspect)
    _apply(applicator, proposal, gate)
    with database.connect() as connection:
        result = connection.execute(
            "SELECT * FROM assistant_autonomy_mutation_results"
        ).fetchone()
        attempt = connection.execute(
            "SELECT * FROM assistant_autonomy_mutation_attempts"
        ).fetchone()
    assert observed["removal_ready"] is True
    assert observed["attempt_public_id"] == attempt["public_id"]
    assert observed["result_public_id"] == result["public_id"]
    assert observed["result_outcome"] == result["outcome"]
    assert observed["result_final_manifest_sequence"] == result["final_manifest_sequence"]
    assert observed["result_final_manifest_sha256"] == result["final_manifest_sha256"]
    assert observed["cleanup_manifest_sequence"] == attempt["manifest_sequence"]
    assert observed["cleanup_manifest_tail_sha256"] == attempt["manifest_tail_sha256"]


def test_create_mode_is_exact_0600_under_custom_umask(tmp_path: Path) -> None:
    _db, _repo, applicator, proposal, gate, workspace = _approved(tmp_path)
    previous = os.umask(0o077)
    try:
        _apply(applicator, proposal, gate)
    finally:
        os.umask(previous)
    assert stat.S_IMODE((workspace / "src/new.py").stat().st_mode) == 0o600


def test_manifest_inode_replacement_fails_closed(
    tmp_path: Path
) -> None:
    database, repository, _applicator, proposal, gate, workspace = _approved(tmp_path)

    def hook(point: str) -> None:
        if point == "claim_durable":
            attempt_dir = next(
                path
                for path in (workspace / ".elyndra-mutation-journal").iterdir()
                if path.is_dir()
            )
            manifest = attempt_dir / "manifest.jsonl"
            manifest.unlink()
            manifest.write_text("foreign\n")
            manifest.chmod(0o600)

    runtime = tmp_path / "runtime-2"
    runtime.mkdir(mode=0o700)
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime),
        crash_hook=hook,
    )
    with pytest.raises(MutationApplicationError, match="inode"):
        _apply(applicator, proposal, gate)
    assert not (workspace / "src/new.py").exists()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_results"
        ).fetchone()[0] == 0


def test_cleanup_failure_closes_every_parent_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db, _repo, applicator, proposal, gate, _workspace = _approved(
        tmp_path, _create("src/a.py"), _create("src/b.py")
    )
    parent_fds: list[int] = []

    def fail_cleanup(_attempt, files, *_args):
        parent_fds.extend(file.parent_fd for file in files)
        raise OSError("cleanup failure")

    monkeypatch.setattr(applicator, "_cleanup", fail_cleanup)
    assert _apply(applicator, proposal, gate).state == "cleanup_pending"
    assert parent_fds
    for fd in parent_fds:
        with pytest.raises(OSError) as captured:
            os.fstat(fd)
        assert captured.value.errno == errno.EBADF


def test_create_tamper_after_rollback_intent_refuses_unlink(tmp_path: Path) -> None:
    workspace_holder: list[Path] = []

    def hook(point: str) -> None:
        if point == "rollback_intent_durable":
            (workspace_holder[0] / "src/a.py").write_bytes(b"late-tamper")

    _db, repository, _applicator, proposal, gate, workspace = _approved(
        tmp_path, _create("src/a.py", b"new"), _create("src/b.py")
    )
    workspace_holder.append(workspace)
    runtime = tmp_path / "runtime-2"
    runtime.mkdir(mode=0o700)
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime),
        crash_hook=hook,
    )
    original = applicator._publish
    calls = 0

    def publish(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("force rollback")
        return original(*args)

    applicator._publish = publish  # type: ignore[method-assign]
    result = _apply(applicator, proposal, gate)
    assert result.state == "manual_intervention_required"
    assert (workspace / "src/a.py").read_bytes() == b"late-tamper"
    assert (workspace / ".elyndra-mutation-journal/blockade.json").exists()


def test_replace_tamper_after_rollback_intent_refuses_exchange(tmp_path: Path) -> None:
    workspace_holder: list[Path] = []

    def hook(point: str) -> None:
        if point == "rollback_intent_durable":
            (workspace_holder[0] / "src/a.py").write_bytes(b"late-postimage-tamper")

    old = b"old"
    _db, repository, _applicator, proposal, gate, workspace = _approved(
        tmp_path, _replace("src/a.py", old, b"new"), _create("src/b.py")
    )
    (workspace / "src/a.py").write_bytes(old)
    workspace_holder.append(workspace)
    runtime = tmp_path / "runtime-2"
    runtime.mkdir(mode=0o700)
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime),
        crash_hook=hook,
    )
    original = applicator._publish
    calls = 0

    def publish(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("force rollback")
        return original(*args)

    applicator._publish = publish  # type: ignore[method-assign]
    result = _apply(applicator, proposal, gate)
    assert result.state == "manual_intervention_required"
    assert (workspace / "src/a.py").read_bytes() == b"late-postimage-tamper"
    assert (workspace / ".elyndra-mutation-journal/blockade.json").exists()


def test_replace_backup_tamper_after_rollback_intent_refuses_exchange(tmp_path: Path) -> None:
    workspace_holder: list[Path] = []

    def hook(point: str) -> None:
        if point == "rollback_intent_durable":
            attempt_dir = next(
                path
                for path in (workspace_holder[0] / ".elyndra-mutation-journal").iterdir()
                if path.is_dir()
            )
            (attempt_dir / "stage-0").write_bytes(b"late-backup-tamper")

    old = b"old"
    _db, repository, _applicator, proposal, gate, workspace = _approved(
        tmp_path, _replace("src/a.py", old, b"new"), _create("src/b.py")
    )
    (workspace / "src/a.py").write_bytes(old)
    workspace_holder.append(workspace)
    runtime = tmp_path / "runtime-2"
    runtime.mkdir(mode=0o700)
    applicator = MutationApplicator(
        repository,
        workspace_lease_coordinator=WorkspaceLeaseCoordinator._for_test(runtime),
        crash_hook=hook,
    )
    original = applicator._publish
    calls = 0

    def publish(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("force rollback")
        return original(*args)

    applicator._publish = publish  # type: ignore[method-assign]
    result = _apply(applicator, proposal, gate)
    assert result.state == "manual_intervention_required"
    assert (workspace / "src/a.py").read_bytes() == b"new"
    assert (workspace / ".elyndra-mutation-journal/blockade.json").exists()


def test_blockade_changed_after_cleanup_validation_before_exchange_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db, _repo, applicator, proposal, gate, workspace = _approved(tmp_path)
    foreign = b'{"foreign":"late"}'
    original = applicator._replace_blockade

    def replace(journal_fd: int, payload: bytes, **kwargs) -> None:
        blockade = workspace / ".elyndra-mutation-journal/blockade.json"
        blockade.write_bytes(foreign)
        blockade.chmod(0o600)
        original(journal_fd, payload, **kwargs)

    monkeypatch.setattr(applicator, "_replace_blockade", replace)
    result = _apply(applicator, proposal, gate)
    assert result.state == "cleanup_pending"
    assert (workspace / ".elyndra-mutation-journal/blockade.json").read_bytes() == foreign


def test_exchanged_previous_blockade_mismatch_is_exchanged_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import elyndra.autonomy.mutation_application as module

    _db, _repo, applicator, proposal, gate, workspace = _approved(tmp_path)
    foreign = b'{"foreign":"exchange-window"}'
    original = module.renameat2
    injected = False

    def rename(old_fd, old, new_fd, new, flags):
        nonlocal injected
        if not injected and old.startswith(".blockade-ready-") and new == "blockade.json":
            injected = True
            blockade = workspace / ".elyndra-mutation-journal/blockade.json"
            blockade.write_bytes(foreign)
            blockade.chmod(0o600)
        return original(old_fd, old, new_fd, new, flags)

    monkeypatch.setattr(module, "renameat2", rename)
    result = _apply(applicator, proposal, gate)
    assert result.state == "cleanup_pending"
    assert (workspace / ".elyndra-mutation-journal/blockade.json").read_bytes() == foreign
    assert not list((workspace / ".elyndra-mutation-journal").glob(".blockade-ready-*"))


def test_changed_removal_ready_blockade_is_restored_not_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db, _repo, applicator, proposal, gate, workspace = _approved(tmp_path)
    foreign = b'{"foreign":"removal-window"}'
    original = applicator._remove_verified_blockade

    def remove(journal_fd: int, payload: bytes) -> None:
        blockade = workspace / ".elyndra-mutation-journal/blockade.json"
        blockade.write_bytes(foreign)
        blockade.chmod(0o600)
        original(journal_fd, payload)

    monkeypatch.setattr(applicator, "_remove_verified_blockade", remove)
    result = _apply(applicator, proposal, gate)
    assert result.state == "cleanup_pending"
    assert (workspace / ".elyndra-mutation-journal/blockade.json").read_bytes() == foreign


def test_valid_verified_removal_still_terminalizes(tmp_path: Path) -> None:
    _db, _repo, applicator, proposal, gate, workspace = _approved(tmp_path)
    result = _apply(applicator, proposal, gate)
    assert result.state == "succeeded"
    assert not (workspace / ".elyndra-mutation-journal/blockade.json").exists()
