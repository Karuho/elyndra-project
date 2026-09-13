from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import stat
from pathlib import Path

import pytest

import elyndra.autonomy.linux_fs as linux_fs
from elyndra.autonomy import (
    WorkspaceLease,
    WorkspaceLeaseCoordinator,
    WorkspaceLeaseMode,
)
from elyndra.autonomy.linux_fs import LinuxFilesystemError, validate_metadata
from elyndra.autonomy.workspace_lease import (
    WorkspaceBlockedError,
    WorkspaceCoordinationError,
    WorkspaceLeaseReceipt,
    WorkspaceLeaseTimeout,
)
from elyndra.db import Database


def _coordinator(tmp_path: Path) -> WorkspaceLeaseCoordinator:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    return WorkspaceLeaseCoordinator._for_test(root)


def test_shared_leases_coexist_and_conflict_with_exclusive(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = coordinator.identity(workspace)
    first = coordinator.acquire(identity, WorkspaceLeaseMode.SHARED)
    second = coordinator.acquire(identity, WorkspaceLeaseMode.SHARED)
    try:
        with pytest.raises(WorkspaceLeaseTimeout):
            coordinator.acquire(
                identity,
                WorkspaceLeaseMode.EXCLUSIVE,
                timeout_seconds=0,
            )
    finally:
        first.close()
        second.close()

    exclusive = coordinator.acquire(identity, WorkspaceLeaseMode.EXCLUSIVE)
    try:
        for mode in (WorkspaceLeaseMode.SHARED, WorkspaceLeaseMode.EXCLUSIVE):
            with pytest.raises(WorkspaceLeaseTimeout):
                coordinator.acquire(identity, mode, timeout_seconds=0)
    finally:
        exclusive.close()


def _hold_exclusive_until_process_exit(
    runtime_root: str,
    workspace: str,
    connection,
) -> None:
    coordinator = WorkspaceLeaseCoordinator._for_test(Path(runtime_root))
    identity = coordinator.identity(workspace)
    lease = coordinator.acquire(identity, WorkspaceLeaseMode.EXCLUSIVE)
    connection.send("locked")
    connection.recv()
    assert not lease.closed
    os._exit(0)


def test_process_death_releases_flock(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = coordinator.identity(workspace)
    parent, child = multiprocessing.Pipe()
    process = multiprocessing.Process(
        target=_hold_exclusive_until_process_exit,
        args=(str(coordinator.runtime_root), str(workspace), child),
    )
    process.start()
    assert parent.recv() == "locked"
    with pytest.raises(WorkspaceLeaseTimeout):
        coordinator.acquire(identity, WorkspaceLeaseMode.SHARED, timeout_seconds=0)
    parent.send("exit")
    process.join(timeout=5)
    assert process.exitcode == 0
    coordinator.acquire(identity, WorkspaceLeaseMode.SHARED).close()


def test_receipt_is_factory_only_mode_bound_and_live(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = coordinator.identity(workspace)
    lease = coordinator.acquire(identity, WorkspaceLeaseMode.SHARED)
    lease.receipt.require_live(mode=WorkspaceLeaseMode.SHARED, identity=identity)
    with pytest.raises(TypeError):
        WorkspaceLeaseReceipt(lease, b"visible", object())
    with pytest.raises(TypeError):
        WorkspaceLease(  # type: ignore[call-arg]
            fd=0,
            identity=identity,
            mode=WorkspaceLeaseMode.SHARED,
            mask_path=coordinator.empty_mask_path,
        )
    with pytest.raises(WorkspaceCoordinationError, match="Modo"):
        lease.receipt.require_live(mode=WorkspaceLeaseMode.EXCLUSIVE)
    lease.close()
    with pytest.raises(WorkspaceCoordinationError, match="vivo"):
        lease.receipt.require_live(mode=WorkspaceLeaseMode.SHARED)


def test_journal_bootstrap_and_blockade_fail_closed(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = coordinator.execution_session(workspace)
    session.close()
    journal = workspace / ".elyndra-mutation-journal"
    assert stat.S_IMODE(journal.stat().st_mode) == 0o700

    orphan = journal / "orphan-attempt"
    orphan.mkdir()
    coordinator.execution_session(workspace).close()

    blockade = journal / "blockade.json"
    blockade.write_text(json.dumps({"format_version": "v1"}), encoding="utf-8")
    blockade.chmod(0o600)
    with pytest.raises(WorkspaceBlockedError, match="bloqueado"):
        coordinator.execution_session(workspace)

    blockade.write_text("{", encoding="utf-8")
    with pytest.raises(WorkspaceBlockedError, match="malformado"):
        coordinator.execution_session(workspace)


def test_unsafe_journal_symlink_is_rejected(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".elyndra-mutation-journal").symlink_to(tmp_path)
    with pytest.raises(WorkspaceCoordinationError, match="Journal"):
        coordinator.execution_session(workspace)


def test_runtime_root_policy_rejects_invalid_configured_xdg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid = tmp_path / "invalid"
    invalid.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(invalid))
    with pytest.raises(WorkspaceCoordinationError, match="confiable"):
        WorkspaceLeaseCoordinator()


def test_runtime_root_override_is_not_a_public_constructor_option(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    with pytest.raises(TypeError):
        WorkspaceLeaseCoordinator(runtime_root=root)  # type: ignore[call-arg]


def test_flag_allowlists_and_unknown_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = linux_fs.LinuxStat(1, 2, 3, stat.S_IFREG | 0o600, os.geteuid(), 0, 1)
    monkeypatch.setattr(linux_fs, "statx_fd", lambda _fd: metadata)
    monkeypatch.setattr(os, "listxattr", lambda _fd: [])
    monkeypatch.setattr(linux_fs, "filesystem_flags", lambda _fd: linux_fs.FS_EXTENT_FL)
    assert validate_metadata(3, directory=False) == metadata
    monkeypatch.setattr(linux_fs, "filesystem_flags", lambda _fd: linux_fs.FS_IMMUTABLE_FL)
    with pytest.raises(LinuxFilesystemError, match="flags"):
        validate_metadata(3, directory=False)
    monkeypatch.setattr(linux_fs, "filesystem_flags", lambda _fd: 0x00000001)
    with pytest.raises(LinuxFilesystemError, match="flags"):
        validate_metadata(3, directory=False)


def test_nonempty_xattrs_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = linux_fs.LinuxStat(1, 2, 3, stat.S_IFDIR | 0o700, os.geteuid(), 0, 2)
    monkeypatch.setattr(linux_fs, "statx_fd", lambda _fd: metadata)
    monkeypatch.setattr(linux_fs, "filesystem_flags", lambda _fd: linux_fs.FS_INDEX_FL)
    monkeypatch.setattr(os, "listxattr", lambda _fd: ["user.marker"])
    with pytest.raises(LinuxFilesystemError, match="xattrs"):
        validate_metadata(3, directory=True)


def test_openat2_rejects_nested_mount_crossing() -> None:
    root_fd = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        with pytest.raises(LinuxFilesystemError):
            linux_fs.openat2(
                root_fd,
                "proc",
                os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC,
            )
    finally:
        os.close(root_fd)


def test_schema60_foundation_is_vault_only_and_idempotent(tmp_path: Path) -> None:
    vault = Database(tmp_path / "vault.sqlite3", role="vault")
    root = Database(tmp_path / "root.sqlite3", role="root")
    vault.migrate()
    with vault.connect() as connection:
        vault_id = connection.execute(
            "SELECT value FROM schema_meta WHERE key='mutation_vault_id'"
        ).fetchone()[0]
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    vault.migrate()
    with vault.connect() as connection:
        assert (
            connection.execute(
                "SELECT value FROM schema_meta WHERE key='mutation_vault_id'"
            ).fetchone()[0]
            == vault_id
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert {
        "assistant_autonomy_mutation_attempts",
        "assistant_autonomy_mutation_attempt_files",
        "assistant_autonomy_mutation_results",
    } <= names

    root.migrate()
    with root.connect() as connection:
        root_names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert (
            connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            == "60"
        )
    assert not {
        "assistant_autonomy_mutation_attempts",
        "assistant_autonomy_mutation_attempt_files",
        "assistant_autonomy_mutation_results",
    }.intersection(root_names)


def test_mutation_connection_is_full_without_changing_normal(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as normal:
        assert normal.execute("PRAGMA synchronous").fetchone()[0] == 1
    with database.connect_mutation_durable() as durable:
        assert durable.in_transaction is False
        assert durable.execute("PRAGMA synchronous").fetchone()[0] == 2
        durable.execute("BEGIN IMMEDIATE")


def test_existing_schema60_result_constraint_is_reconciled_idempotently(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection:
        current_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='assistant_autonomy_mutation_results'"
        ).fetchone()[0]
        old_sql = current_sql.replace(
            "CHECK(restored_count <= published_count),\n"
            "                CHECK(outcome != 'filesystem_succeeded' OR restored_count = 0)",
            "CHECK(published_count + restored_count <= 3)",
        )
        connection.executescript(
            """
            DROP TRIGGER trg_autonomy_mutation_results_integrity;
            DROP TRIGGER trg_autonomy_mutation_results_no_update;
            DROP TRIGGER trg_autonomy_mutation_results_no_delete;
            DROP TRIGGER trg_autonomy_mutation_cleanup_requires_result;
            DROP TRIGGER trg_autonomy_mutation_terminal_requires_result;
            ALTER TABLE assistant_autonomy_mutation_results
                RENAME TO assistant_autonomy_mutation_results_current;
            """
        )
        connection.execute(old_sql)
        connection.execute("DROP TABLE assistant_autonomy_mutation_results_current")

    database.migrate()
    database.migrate()
    with database.connect() as connection:
        reconciled = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='assistant_autonomy_mutation_results'"
        ).fetchone()[0]
        assert "published_count + restored_count <= 3" not in reconciled
        assert "restored_count <= published_count" in reconciled
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_result_and_attempt_tables_are_not_publicly_writable_via_repository(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    with database.connect() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
                "INSERT INTO assistant_autonomy_mutation_attempts "
                "(public_id,request_key,proposal_id,binding_id,gate_id,"
                "proposal_public_id,proposal_sha256,run_id,step_id,actor,"
                "workspace_root,workspace_st_dev,workspace_st_ino,"
                "workspace_mount_id,state,claimed_at,state_updated_at,"
                "initial_blockade_sha256,initial_manifest_sha256,"
                "manifest_sequence,manifest_tail_sha256) "
                "VALUES ('a','r',1,1,'g','p',?,1,'s','owner','/w',1,1,1,"
                "'claimed','t','t',?,?,0,?)",
                ("0" * 64, "1" * 64, "2" * 64, "2" * 64),
        )
