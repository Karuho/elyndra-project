"""Trusted transactional applicator for one exact approved mutation review."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from elyndra.autonomy.capabilities import Capability
from elyndra.autonomy.linux_fs import (
    RENAME_EXCHANGE,
    RENAME_NOREPLACE,
    LinuxFilesystemError,
    LinuxStat,
    fsync_fd,
    linkat,
    openat2,
    renameat2,
    statx_fd,
    unlinkat,
    validate_metadata,
)
from elyndra.autonomy.models import AutonomyRunStatus
from elyndra.autonomy.mutations import MutationItem, MutationOperation, PersistedMutationProposal
from elyndra.autonomy.repository import AutonomyRepository, _grant_from_json, _plan_from_json
from elyndra.autonomy.workspace_lease import (
    BLOCKADE_NAME,
    JOURNAL_NAME,
    WorkspaceIdentity,
    WorkspaceLeaseCoordinator,
    WorkspaceLeaseMode,
)

_MANIFEST_DOMAIN = b"elyndra.mutation-manifest.v1\0"
_FORMAT_VERSION = "v1"
_MAX_REQUEST_KEY = 128


class MutationApplicationError(PermissionError):
    """A trusted mutation could not be applied without weakening provenance."""


@dataclass(frozen=True, slots=True)
class MutationApplicationResult:
    """Bounded read-only observation of one application attempt."""

    attempt_public_id: str
    state: str
    outcome: str | None
    replayed: bool


@dataclass(slots=True)
class _File:
    item_id: int
    ordinal: int
    item: MutationItem
    parent_fd: int
    parent: LinuxStat
    target_name: str
    preimage: LinuxStat | None
    artifact_name: str
    witness_name: str
    stage: LinuxStat | None = None
    published: bool = False


class _Journal:
    def __init__(
        self,
        *,
        journal_fd: int,
        attempt_fd: int,
        identity: dict[str, Any],
    ) -> None:
        self.journal_fd = journal_fd
        self.attempt_fd = attempt_fd
        self.identity = identity
        self.sequence = -1
        self.tail = "0" * 64
        self.file_identity: tuple[int, int] | None = None

    def append(
        self,
        state: str,
        *,
        files: list[dict[str, Any]] | None = None,
        timestamp: datetime | None = None,
    ) -> str:
        self._validate_tail()
        self.sequence += 1
        record: dict[str, Any] = {
            **self.identity,
            "attempt_state": state,
            "files": files or [],
            "format_version": _FORMAT_VERSION,
            "manifest_domain": "elyndra.mutation-manifest.v1",
            "previous_record_sha256": self.tail,
            "sequence": self.sequence,
            "timestamp": _time_text(timestamp),
        }
        digest = hashlib.sha256(_MANIFEST_DOMAIN + _canonical(record)).hexdigest()
        encoded = _canonical({**record, "record_sha256": digest}) + b"\n"
        if self.sequence == 0:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
        else:
            flags = os.O_WRONLY | os.O_APPEND | os.O_CLOEXEC | os.O_NOFOLLOW
        fd = os.open("manifest.jsonl", flags, 0o600, dir_fd=self.attempt_fd)
        try:
            if self.sequence == 0:
                os.fchmod(fd, 0o600)
            metadata = validate_metadata(fd, directory=False)
            current_identity = (metadata.device, metadata.inode)
            if (
                metadata.uid != os.geteuid()
                or metadata.nlink != 1
                or stat.S_IMODE(metadata.mode) != 0o600
                or (
                    self.file_identity is not None
                    and current_identity != self.file_identity
                )
            ):
                raise MutationApplicationError("Manifest inode o metadata no confiable.")
            if self.file_identity is None:
                self.file_identity = current_identity
            _write_all(fd, encoded)
            fsync_fd(fd)
        finally:
            os.close(fd)
        if self.sequence == 0:
            fsync_fd(self.attempt_fd)
        self.tail = digest
        return digest

    def _validate_tail(self) -> None:
        if self.sequence < 0:
            return
        fd = os.open(
            "manifest.jsonl", os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=self.attempt_fd
        )
        try:
            metadata = validate_metadata(fd, directory=False)
            if self.file_identity != (metadata.device, metadata.inode):
                raise MutationApplicationError("Manifest inode fue reemplazado.")
            raw = b""
            while True:
                chunk = os.read(fd, 16_384)
                if not chunk:
                    break
                raw += chunk
                if len(raw) > 262_144:
                    raise MutationApplicationError("Manifest excede límite de seguridad.")
        finally:
            os.close(fd)
        lines = raw.splitlines()
        if len(lines) != self.sequence + 1:
            raise MutationApplicationError("Manifest tail ambiguo.")
        try:
            tail = json.loads(lines[-1].decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MutationApplicationError("Manifest tail malformado.") from exc
        claimed = tail.pop("record_sha256", None)
        actual = hashlib.sha256(_MANIFEST_DOMAIN + _canonical(tail)).hexdigest()
        if claimed != self.tail or actual != self.tail:
            raise MutationApplicationError("Manifest tail no coincide.")


class MutationApplicator:
    """Apply one already-approved exact mutation under an exclusive workspace lease."""

    def __init__(
        self,
        repository: AutonomyRepository,
        *,
        workspace_lease_coordinator: WorkspaceLeaseCoordinator | None = None,
        crash_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.repository = repository
        self.coordinator = workspace_lease_coordinator or WorkspaceLeaseCoordinator()
        self._crash_hook = crash_hook or (lambda _point: None)

    def apply(
        self,
        proposal_public_id: str,
        proposal_sha256: str,
        gate_id: str,
        *,
        actor: str,
        apply_request_key: str,
    ) -> MutationApplicationResult:
        """Apply an exact review once; historical exact calls are read-only replays."""

        _required(proposal_public_id, "proposal_public_id", 128)
        _sha256(proposal_sha256, "proposal_sha256")
        _required(gate_id, "gate_id", 128)
        _required(actor, "actor", 200)
        _required(apply_request_key, "apply_request_key", _MAX_REQUEST_KEY)
        replay = self.repository._mutation_application_replay(
            apply_request_key=apply_request_key,
            proposal_public_id=proposal_public_id,
            proposal_sha256=proposal_sha256,
            gate_id=gate_id,
            actor=actor,
        )
        if replay is not None:
            return self._result(replay, replayed=True)
        proposal = self._load_proposal(proposal_public_id)
        if (
            proposal.proposal.proposal_sha256 != proposal_sha256
            or proposal.proposal.actor != actor
        ):
            raise PermissionError("Identidad exacta de propuesta incorrecta.")
        identity = self.coordinator.identity(proposal.proposal.workspace_root)
        self.coordinator._bootstrap_journal(  # trusted internal peer
            identity, cancellation=None, timeout_seconds=10.0
        )
        lease = self.coordinator.acquire(identity, WorkspaceLeaseMode.EXCLUSIVE)
        try:
            if self.coordinator.identity(identity.canonical_root) != identity:
                raise MutationApplicationError("Workspace cambió tras adquirir lease.")
            self.coordinator._validate_journal_and_blockade(identity)
            replay = self.repository._mutation_application_replay(
                apply_request_key=apply_request_key,
                proposal_public_id=proposal_public_id,
                proposal_sha256=proposal_sha256,
                gate_id=gate_id,
                actor=actor,
            )
            if replay is not None:
                return self._result(replay, replayed=True)
            attempt_id = uuid.uuid4().hex
            root_fd, journal_fd, attempt_fd = self._create_attempt_dir(identity, attempt_id)
            try:
                frozen = self._preclaim_identity(
                    attempt_id=attempt_id,
                    proposal=proposal,
                    gate_id=gate_id,
                    actor=actor,
                    apply_request_key=apply_request_key,
                    workspace=identity,
                )
                with self.repository.database.connect() as connection:
                    vault_id = connection.execute(
                        "SELECT value FROM schema_meta WHERE key='mutation_vault_id'"
                    ).fetchone()
                if vault_id is None:
                    raise MutationApplicationError("mutation_vault_id ausente.")
                frozen["blockade"]["mutation_vault_id"] = str(vault_id[0])
                manifest = _Journal(
                    journal_fd=journal_fd,
                    attempt_fd=attempt_fd,
                    identity=frozen["manifest_identity"],
                )
                initial_manifest = manifest.append("preclaim")
                self._crash_hook("manifest_durable")
                blockade = {
                    **frozen["blockade"],
                    "initial_manifest_sequence": 0,
                    "initial_manifest_sha256": initial_manifest,
                }
                blockade_bytes = _canonical(blockade)
                blockade_sha = hashlib.sha256(blockade_bytes).hexdigest()
                self._create_blockade(journal_fd, blockade_bytes)
                self._crash_hook("blockade_durable")
                attempt, claimed_proposal, replayed = self.repository._claim_mutation_application(
                    attempt_public_id=attempt_id,
                    apply_request_key=apply_request_key,
                    proposal_public_id=proposal_public_id,
                    proposal_sha256=proposal_sha256,
                    gate_id=gate_id,
                    actor=actor,
                    workspace_identity=identity,
                    lease_receipt=lease.receipt,
                    initial_blockade_sha256=blockade_sha,
                    initial_manifest_sha256=initial_manifest,
                )
                if replayed:
                    return self._result(attempt, replayed=True)
                self._crash_hook("claim_durable")
                return self._apply_claimed(
                    attempt,
                    claimed_proposal,
                    identity,
                    lease.receipt,
                    root_fd,
                    journal_fd,
                    attempt_fd,
                    manifest,
                    blockade,
                )
            finally:
                os.close(attempt_fd)
                os.close(journal_fd)
                os.close(root_fd)
        finally:
            lease.close()

    def _load_proposal(self, public_id: str) -> PersistedMutationProposal:
        with self.repository.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_autonomy_mutation_proposals WHERE public_id=?",
                (public_id,),
            ).fetchone()
            if row is None:
                raise ValueError("MutationProposal no encontrada.")
            return self.repository._mutation_proposal_from_row(connection, row)

    def _create_attempt_dir(
        self, identity: WorkspaceIdentity, attempt_id: str
    ) -> tuple[int, int, int]:
        root_fd = os.open(
            identity.canonical_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            root_metadata = statx_fd(root_fd)
            if (
                not stat.S_ISDIR(root_metadata.mode)
                or root_metadata.device != identity.st_dev
                or root_metadata.inode != identity.st_ino
                or root_metadata.mount_id != identity.mount_id
            ):
                raise MutationApplicationError("Root fd no coincide con el workspace lease.")
            journal_fd = openat2(
                root_fd, JOURNAL_NAME, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
            )
            try:
                try:
                    os.mkdir(attempt_id, 0o700, dir_fd=journal_fd)
                except FileExistsError as exc:
                    raise MutationApplicationError("Attempt journal ya existe.") from exc
                fsync_fd(journal_fd)
                attempt_fd = openat2(
                    journal_fd, attempt_id, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
                )
                try:
                    metadata = validate_metadata(attempt_fd, directory=True)
                    if (
                        metadata.uid != os.geteuid()
                        or stat.S_IMODE(metadata.mode) != 0o700
                    ):
                        raise MutationApplicationError("Attempt journal no es privado.")
                    return root_fd, journal_fd, attempt_fd
                except BaseException:
                    os.close(attempt_fd)
                    raise
            except BaseException:
                os.close(journal_fd)
                raise
        except BaseException:
            os.close(root_fd)
            raise

    @staticmethod
    def _preclaim_identity(
        *,
        attempt_id: str,
        proposal: PersistedMutationProposal,
        gate_id: str,
        actor: str,
        apply_request_key: str,
        workspace: WorkspaceIdentity,
    ) -> dict[str, dict[str, Any]]:
        common = {
            "attempt_public_id": attempt_id,
            "gate_id": gate_id,
            "proposal_public_id": proposal.public_id,
            "proposal_sha256": proposal.proposal.proposal_sha256,
            "run_public_id": proposal.proposal.run_id,
            "step_id": proposal.proposal.step_id,
            "workspace_mount_id": workspace.mount_id,
            "workspace_st_dev": workspace.st_dev,
            "workspace_st_ino": workspace.st_ino,
        }
        return {
            "manifest_identity": common,
            "blockade": {
                **common,
                "actor_sha256": _utf8_sha(actor),
                "apply_request_key_sha256": _utf8_sha(apply_request_key),
                "canonical_workspace_root_sha256": _utf8_sha(workspace.canonical_root),
                "created_at": _time_text(),
                "domain": "elyndra.mutation-blockade.v1",
                "format_version": _FORMAT_VERSION,
                "mutation_vault_id": "",  # filled before serialization
            },
        }

    def _create_blockade(self, journal_fd: int, payload: bytes) -> None:
        if len(payload) > 16_384:
            raise MutationApplicationError("Blockade excede límite.")
        temp = ".blockade-" + uuid.uuid4().hex + ".tmp"
        fd = os.open(
            temp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=journal_fd,
        )
        try:
            os.fchmod(fd, 0o600)
            _write_all(fd, payload)
            fsync_fd(fd)
        finally:
            os.close(fd)
        renameat2(journal_fd, temp, journal_fd, BLOCKADE_NAME, RENAME_NOREPLACE)
        fsync_fd(journal_fd)

    def _apply_claimed(
        self,
        attempt: dict[str, Any],
        proposal: PersistedMutationProposal,
        identity: WorkspaceIdentity,
        receipt: Any,
        root_fd: int,
        journal_fd: int,
        attempt_fd: int,
        manifest: _Journal,
        blockade: dict[str, Any],
    ) -> MutationApplicationResult:
        files: list[_File] = []
        published: list[_File] = []
        try:
            self._attempt_state(attempt, "preparing", manifest)
            self._capture_files(attempt, proposal, identity, root_fd, files)
            for file in files:
                self._stage(attempt, file, attempt_fd, manifest)
            self._attempt_state(attempt, "prepared", manifest)
            boundary = self._final_boundary(attempt, proposal, identity, receipt, files)
            if boundary is not None:
                return self._clean_outcome(
                    attempt, boundary, files, journal_fd, attempt_fd, manifest, blockade
                )
            manifest.append("publishing")
            self._sync_manifest(attempt, manifest)
            self._set_attempt(attempt, "publishing", publication_started_at=_time_text())
            self._crash_hook("publishing_durable")
            for file in files:
                self._publication_intent(attempt, file, manifest)
                self._publish(attempt, file, attempt_fd, identity, manifest)
                published.append(file)
            self._attempt_state(
                attempt, "filesystem_applied", manifest, filesystem_applied_at=_time_text()
            )
            self._insert_result(attempt, "filesystem_succeeded", manifest, len(published), 0)
            self._set_attempt(attempt, "cleanup_pending")
            try:
                self._cleanup(attempt, files, journal_fd, attempt_fd, manifest)
            except BaseException:
                return self._result(attempt, outcome="filesystem_succeeded")
            self._set_attempt(attempt, "succeeded", terminal_at=_time_text())
            return self._result(attempt, outcome="filesystem_succeeded")
        except _Stale:
            existing = self._existing_result(attempt)
            if existing is not None:
                return self._result(attempt, outcome=str(existing["outcome"]))
            return self._clean_outcome(
                attempt, "stale", files, journal_fd, attempt_fd, manifest, blockade
            )
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            existing = self._existing_result(attempt)
            if existing is not None:
                return self._result(attempt, outcome=str(existing["outcome"]))
            if attempt["state"] in {"publishing", "recovery_required"} or published:
                actual = [file for file in files if file.published]
                return self._rollback(attempt, files, actual, journal_fd, attempt_fd, manifest)
            return self._clean_outcome(
                attempt,
                "failed_before_publication",
                files,
                journal_fd,
                attempt_fd,
                manifest,
                blockade,
                code="preparation_failed",
            )
        finally:
            for file in files:
                os.close(file.parent_fd)

    def _capture_files(
        self,
        attempt: dict[str, Any],
        proposal: PersistedMutationProposal,
        identity: WorkspaceIdentity,
        root_fd: int,
        captured: list[_File],
    ) -> None:
        with self.repository.database.connect() as connection:
            item_rows = connection.execute(
                "SELECT * FROM assistant_autonomy_mutation_items WHERE proposal_id=? "
                "ORDER BY ordinal",
                (int(attempt["proposal_id"]),),
            ).fetchall()
        for row, item in zip(item_rows, proposal.proposal.items, strict=True):
            parts = item.relative_path.split("/")
            parent_path = "." if len(parts) == 1 else "/".join(parts[:-1])
            parent_fd = openat2(
                root_fd, parent_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
            )
            try:
                parent = validate_metadata(parent_fd, directory=True)
                if parent.mount_id != identity.mount_id:
                    raise MutationApplicationError("Parent fuera del workspace mount.")
                preimage: LinuxStat | None = None
                try:
                    target_fd = openat2(parent_fd, parts[-1], os.O_RDONLY | os.O_CLOEXEC)
                except LinuxFilesystemError as exc:
                    if not _is_enoent(exc):
                        raise
                    target_fd = -1
                if item.operation is MutationOperation.CREATE:
                    if target_fd >= 0:
                        os.close(target_fd)
                        raise _Stale("CREATE target ya existe.")
                else:
                    if target_fd < 0:
                        raise _Stale("REPLACE target ausente.")
                    try:
                        preimage = self._validate_preimage(target_fd, item, identity)
                    finally:
                        os.close(target_fd)
                file = _File(
                    item_id=int(row["id"]),
                    ordinal=int(row["ordinal"]),
                    item=item,
                    parent_fd=parent_fd,
                    parent=parent,
                    target_name=parts[-1],
                    preimage=preimage,
                    artifact_name=f"stage-{int(row['ordinal'])}",
                    witness_name=f"witness-{int(row['ordinal'])}",
                )
                self._insert_file(attempt, file)
                captured.append(file)
            except BaseException:
                os.close(parent_fd)
                raise

    @staticmethod
    def _validate_preimage(fd: int, item: MutationItem, identity: WorkspaceIdentity) -> LinuxStat:
        metadata = validate_metadata(fd, directory=False)
        if (
            metadata.mount_id != identity.mount_id
            or metadata.uid != os.geteuid()
            or metadata.nlink != 1
            or metadata.mode & (stat.S_ISUID | stat.S_ISGID)
        ):
            raise _Stale("Preimagen metadata inválida.")
        size, digest = _hash_fd(fd)
        if size != item.original_size or digest != item.original_sha256:
            raise _Stale("Preimagen ya no coincide.")
        return metadata

    def _insert_file(self, attempt: dict[str, Any], file: _File) -> None:
        pre = file.preimage
        now = _time_text()
        with self.repository.database.connect_mutation_durable() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO assistant_autonomy_mutation_attempt_files(
                       attempt_id,proposal_item_id,ordinal,relative_path,operation,
                       expected_preimage_sha256,expected_preimage_size,
                       expected_postimage_sha256,expected_postimage_size,
                       parent_st_dev,parent_st_ino,parent_mount_id,
                       preimage_st_dev,preimage_st_ino,preimage_uid,preimage_gid,
                       preimage_mode,preimage_nlink,artifact_name,witness_name,state,state_updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'planned',?)""",
                (
                    int(attempt["id"]), file.item_id, file.ordinal, file.item.relative_path,
                    file.item.operation.value, file.item.original_sha256,
                    file.item.original_size, file.item.proposed_sha256,
                    file.item.proposed_size, file.parent.device, file.parent.inode,
                    file.parent.mount_id, None if pre is None else pre.device,
                    None if pre is None else pre.inode, None if pre is None else pre.uid,
                    None if pre is None else pre.gid, None if pre is None else pre.mode,
                    None if pre is None else pre.nlink, file.artifact_name,
                    file.witness_name, now,
                ),
            )

    def _stage(
        self, attempt: dict[str, Any], file: _File, attempt_fd: int, manifest: _Journal
    ) -> None:
        fd = os.open(
            file.artifact_name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=attempt_fd,
        )
        try:
            if file.preimage is not None:
                os.fchown(fd, file.preimage.uid, file.preimage.gid)
                os.fchmod(fd, stat.S_IMODE(file.preimage.mode))
            else:
                os.fchmod(fd, 0o600)
            _write_all(fd, file.item.proposed_content)
            fsync_fd(fd)
            metadata = validate_metadata(fd, directory=False)
            size, digest = _hash_fd(fd)
            if size != file.item.proposed_size or digest != file.item.proposed_sha256:
                raise MutationApplicationError("Stage postimage no coincide.")
            if metadata.mount_id != file.parent.mount_id or metadata.nlink != 1:
                raise MutationApplicationError("Stage provenance inválida.")
            expected_mode = (
                0o600 if file.preimage is None else stat.S_IMODE(file.preimage.mode)
            )
            if stat.S_IMODE(metadata.mode) != expected_mode:
                raise MutationApplicationError("Stage mode no coincide.")
            file.stage = metadata
        finally:
            os.close(fd)
        linkat(attempt_fd, file.artifact_name, attempt_fd, file.witness_name)
        witness_fd = openat2(attempt_fd, file.witness_name, os.O_RDONLY | os.O_CLOEXEC)
        try:
            witness = validate_metadata(witness_fd, directory=False)
        finally:
            os.close(witness_fd)
        if file.stage is None or not _same_inode(file.stage, witness):
            raise MutationApplicationError("Witness no prueba inode de stage.")
        fsync_fd(attempt_fd)
        with self.repository.database.connect_mutation_durable() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempt_files SET state='staged',"
                "stage_st_dev=?,stage_st_ino=?,state_updated_at=? WHERE attempt_id=? AND ordinal=?",
                (
                    file.stage.device,
                    file.stage.inode,
                    _time_text(),
                    int(attempt["id"]),
                    file.ordinal,
                ),
            )
        manifest.append("preparing", files=[self._observation(file, "staged")])
        self._sync_manifest(attempt, manifest)

    def _final_boundary(
        self,
        attempt: dict[str, Any],
        proposal: PersistedMutationProposal,
        identity: WorkspaceIdentity,
        receipt: Any,
        files: list[_File],
    ) -> str | None:
        receipt.require_live(mode=WorkspaceLeaseMode.EXCLUSIVE, identity=identity)
        if self.coordinator.identity(identity.canonical_root) != identity:
            return "stale"
        for file in files:
            if not self._target_matches(file, identity):
                return "stale"
        with self.repository.database.connect_mutation_durable() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = datetime.now(UTC)
            run = connection.execute(
                "SELECT * FROM assistant_autonomy_runs WHERE id=?", (int(attempt["run_id"]),)
            ).fetchone()
            if run is None or str(run["status"]) != AutonomyRunStatus.WAITING_HUMAN.value:
                return "expired"
            grant = _grant_from_json(str(run["grant_json"]))
            if proposal.proposal.expires_at <= now or grant.is_expired(at=now):
                return "expired"
            try:
                grant.require(Capability.SELF_MODIFY, at=now)
            except PermissionError:
                return "expired"
            plan = _plan_from_json(str(run["plan_json"]))
            step = self.repository._first_incomplete_plan_step_connection(
                connection, run_db_id=int(run["id"]), plan=plan
            )
            if step is None or step.step_id != attempt["step_id"]:
                return "stale"
            self.repository._require_no_execution_gap_connection(
                connection, proposal.proposal.run_id, actor=proposal.proposal.actor
            )
        return None

    def _target_matches(self, file: _File, identity: WorkspaceIdentity) -> bool:
        try:
            fd = openat2(file.parent_fd, file.target_name, os.O_RDONLY | os.O_CLOEXEC)
        except LinuxFilesystemError as exc:
            if _is_enoent(exc):
                return file.item.operation is MutationOperation.CREATE
            raise
        try:
            if file.item.operation is MutationOperation.CREATE:
                return False
            current = self._validate_preimage(fd, file.item, identity)
            return file.preimage is not None and _same_inode(current, file.preimage)
        except (LinuxFilesystemError, _Stale):
            return False
        finally:
            os.close(fd)

    def _publication_intent(
        self, attempt: dict[str, Any], file: _File, manifest: _Journal
    ) -> None:
        with self.repository.database.connect_mutation_durable() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempt_files SET state='publication_intent',"
                "state_updated_at=? WHERE attempt_id=? AND ordinal=?",
                (_time_text(), int(attempt["id"]), file.ordinal),
            )
        manifest.append("publishing", files=[self._observation(file, "publication_intent")])
        self._sync_manifest(attempt, manifest)

    def _publish(
        self,
        attempt: dict[str, Any],
        file: _File,
        attempt_fd: int,
        identity: WorkspaceIdentity,
        manifest: _Journal,
    ) -> None:
        if file.item.operation is MutationOperation.CREATE:
            renameat2(
                attempt_fd, file.artifact_name, file.parent_fd, file.target_name, RENAME_NOREPLACE
            )
        else:
            if not self._target_matches(file, identity):
                raise MutationApplicationError("Preimagen cambió antes de exchange.")
            renameat2(
                attempt_fd, file.artifact_name, file.parent_fd, file.target_name, RENAME_EXCHANGE
            )
        file.published = True
        fsync_fd(file.parent_fd)
        fsync_fd(attempt_fd)
        target_fd = openat2(file.parent_fd, file.target_name, os.O_RDONLY | os.O_CLOEXEC)
        try:
            installed = validate_metadata(target_fd, directory=False)
            size, digest = _hash_fd(target_fd)
        finally:
            os.close(target_fd)
        if file.stage is None or not _same_inode(installed, file.stage):
            raise MutationApplicationError("Postimage instalada no coincide con witness inode.")
        if size != file.item.proposed_size or digest != file.item.proposed_sha256:
            raise MutationApplicationError("Postimage instalada no coincide.")
        if file.item.operation is MutationOperation.REPLACE:
            backup_fd = openat2(attempt_fd, file.artifact_name, os.O_RDONLY | os.O_CLOEXEC)
            try:
                backup = self._validate_preimage(backup_fd, file.item, identity)
            finally:
                os.close(backup_fd)
            if file.preimage is None or not _same_inode(backup, file.preimage):
                raise MutationApplicationError("Backup original perdió provenance.")
        with self.repository.database.connect_mutation_durable() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempt_files SET state='published',"
                "published_at=?,state_updated_at=? WHERE attempt_id=? AND ordinal=?",
                (_time_text(), _time_text(), int(attempt["id"]), file.ordinal),
            )
        manifest.append("publishing", files=[self._observation(file, "published")])
        self._sync_manifest(attempt, manifest)

    def _rollback(
        self,
        attempt: dict[str, Any],
        files: list[_File],
        published: list[_File],
        journal_fd: int,
        attempt_fd: int,
        manifest: _Journal,
    ) -> MutationApplicationResult:
        self._set_attempt(attempt, "recovery_required", recovery_code="publication_failed")
        manifest.append("recovery_required")
        self._sync_manifest(attempt, manifest)
        restored = 0
        for file in reversed(published):
            try:
                self._rollback_file(attempt, file, attempt_fd, manifest)
                restored += 1
            except BaseException:
                self._set_file_manual(attempt, file)
                self._set_attempt(
                    attempt, "manual_intervention_required", recovery_code="rollback_ambiguous"
                )
                manifest.append("manual_intervention_required")
                self._sync_manifest(attempt, manifest)
                return self._result(attempt)
        self._insert_result(attempt, "rolled_back", manifest, len(published), restored)
        self._set_attempt(attempt, "cleanup_pending")
        try:
            self._cleanup(attempt, files, journal_fd, attempt_fd, manifest)
        except BaseException:
            return self._result(attempt, outcome="rolled_back")
        self._set_attempt(attempt, "rolled_back", terminal_at=_time_text())
        return self._result(attempt, outcome="rolled_back")

    def _rollback_file(
        self, attempt: dict[str, Any], file: _File, attempt_fd: int, manifest: _Journal
    ) -> None:
        target_fd = openat2(file.parent_fd, file.target_name, os.O_RDONLY | os.O_CLOEXEC)
        try:
            target = validate_metadata(target_fd, directory=False)
            target_size, target_sha = _hash_fd(target_fd)
        finally:
            os.close(target_fd)
        if file.stage is None or not _same_inode(target, file.stage):
            raise MutationApplicationError("Rollback target provenance ambigua.")
        if target_size != file.item.proposed_size or target_sha != file.item.proposed_sha256:
            raise MutationApplicationError("Rollback postimage content cambió.")
        with self.repository.database.connect_mutation_durable() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempt_files SET state='rollback_intent',"
                "state_updated_at=? WHERE attempt_id=? AND ordinal=?",
                (_time_text(), int(attempt["id"]), file.ordinal),
            )
        manifest.append("recovery_required", files=[self._observation(file, "rollback_intent")])
        self._sync_manifest(attempt, manifest)
        self._crash_hook("rollback_intent_durable")
        self._verify_rollback_postimage(file, attempt_fd)
        if file.item.operation is MutationOperation.CREATE:
            unlinkat(file.parent_fd, file.target_name)
            fsync_fd(file.parent_fd)
            try:
                unexpected_fd = openat2(
                    file.parent_fd, file.target_name, os.O_RDONLY | os.O_CLOEXEC
                )
            except LinuxFilesystemError as exc:
                if not _is_enoent(exc):
                    raise
            else:
                os.close(unexpected_fd)
                raise MutationApplicationError("CREATE rollback target aún existe.")
        else:
            self._verify_rollback_backup(file, attempt_fd)
            renameat2(
                attempt_fd, file.artifact_name, file.parent_fd, file.target_name, RENAME_EXCHANGE
            )
        fsync_fd(file.parent_fd)
        fsync_fd(attempt_fd)
        if file.item.operation is MutationOperation.REPLACE:
            restored_fd = openat2(file.parent_fd, file.target_name, os.O_RDONLY | os.O_CLOEXEC)
            try:
                restored = validate_metadata(restored_fd, directory=False)
                size, digest = _hash_fd(restored_fd)
            finally:
                os.close(restored_fd)
            if (
                file.preimage is None
                or not _same_inode(restored, file.preimage)
                or size != file.item.original_size
                or digest != file.item.original_sha256
            ):
                raise MutationApplicationError("Rollback restore no coincide.")
        with self.repository.database.connect_mutation_durable() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempt_files SET state='rolled_back',"
                "state_updated_at=? WHERE attempt_id=? AND ordinal=?",
                (_time_text(), int(attempt["id"]), file.ordinal),
            )
        manifest.append("recovery_required", files=[self._observation(file, "rolled_back")])
        self._sync_manifest(attempt, manifest)

    @staticmethod
    def _verify_rollback_postimage(file: _File, attempt_fd: int) -> None:
        target_fd = openat2(file.parent_fd, file.target_name, os.O_RDONLY | os.O_CLOEXEC)
        witness_fd = openat2(attempt_fd, file.witness_name, os.O_RDONLY | os.O_CLOEXEC)
        try:
            target = validate_metadata(target_fd, directory=False)
            witness = validate_metadata(witness_fd, directory=False)
            size, digest = _hash_fd(target_fd)
        finally:
            os.close(witness_fd)
            os.close(target_fd)
        if (
            file.stage is None
            or not _same_inode(target, file.stage)
            or not _same_inode(witness, file.stage)
            or size != file.item.proposed_size
            or digest != file.item.proposed_sha256
        ):
            raise MutationApplicationError("Rollback postimage final no coincide.")

    @staticmethod
    def _verify_rollback_backup(file: _File, attempt_fd: int) -> None:
        backup_fd = openat2(attempt_fd, file.artifact_name, os.O_RDONLY | os.O_CLOEXEC)
        try:
            backup = validate_metadata(backup_fd, directory=False)
            size, digest = _hash_fd(backup_fd)
        finally:
            os.close(backup_fd)
        if (
            file.preimage is None
            or not _same_inode(backup, file.preimage)
            or size != file.item.original_size
            or digest != file.item.original_sha256
            or backup.uid != file.preimage.uid
            or backup.gid != file.preimage.gid
            or backup.mode != file.preimage.mode
            or backup.nlink != file.preimage.nlink
        ):
            raise MutationApplicationError("Rollback backup final no coincide.")

    def _clean_outcome(
        self,
        attempt: dict[str, Any],
        outcome: str,
        files: list[_File],
        journal_fd: int,
        attempt_fd: int,
        manifest: _Journal,
        blockade: dict[str, Any],
        *,
        code: str | None = None,
    ) -> MutationApplicationResult:
        self._insert_result(attempt, outcome, manifest, 0, 0, code=code)
        self._set_attempt(attempt, "cleanup_pending", recovery_code=code)
        try:
            self._cleanup(attempt, files, journal_fd, attempt_fd, manifest)
        except BaseException:
            return self._result(attempt, outcome=outcome)
        self._set_attempt(attempt, outcome, terminal_at=_time_text())
        return self._result(attempt, outcome=outcome)

    def _cleanup(
        self,
        attempt: dict[str, Any],
        files: list[_File],
        journal_fd: int,
        attempt_fd: int,
        manifest: _Journal,
    ) -> None:
        blockade = self._validated_initial_blockade(attempt, journal_fd)
        result = self._existing_result(attempt)
        if result is None:
            raise MutationApplicationError("Cleanup requiere resultado inmutable.")
        for file in files:
            for name in (file.artifact_name, file.witness_name):
                try:
                    unlinkat(attempt_fd, name)
                except LinuxFilesystemError as exc:
                    if not _is_enoent(exc):
                        raise
            self._discard_file_if_needed(file)
        fsync_fd(attempt_fd)
        manifest.append("cleanup_ready")
        self._sync_manifest(attempt, manifest)
        removal_ready = {
            **blockade,
            "cleanup_manifest_sequence": manifest.sequence,
            "cleanup_manifest_tail_sha256": manifest.tail,
            "removal_ready": True,
            "result_final_manifest_sequence": int(result["final_manifest_sequence"]),
            "result_final_manifest_sha256": str(result["final_manifest_sha256"]),
            "result_outcome": str(result["outcome"]),
            "result_public_id": str(result["public_id"]),
            "updated_at": _time_text(),
        }
        self._validated_initial_blockade(attempt, journal_fd)
        removal_ready_bytes = _canonical(removal_ready)
        self._replace_blockade(journal_fd, removal_ready_bytes, attempt=attempt)
        self._remove_verified_blockade(journal_fd, removal_ready_bytes)

    def _replace_blockade(
        self, journal_fd: int, payload: bytes, *, attempt: dict[str, Any]
    ) -> None:
        if len(payload) > 16_384:
            raise MutationApplicationError("Blockade removal-ready excede límite.")
        temp = ".blockade-ready-" + uuid.uuid4().hex + ".tmp"
        fd = os.open(
            temp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=journal_fd,
        )
        try:
            os.fchmod(fd, 0o600)
            _write_all(fd, payload)
            fsync_fd(fd)
            removal_metadata = validate_metadata(fd, directory=False)
        finally:
            os.close(fd)
        self._validated_initial_blockade(attempt, journal_fd)
        renameat2(journal_fd, temp, journal_fd, BLOCKADE_NAME, RENAME_EXCHANGE)
        fsync_fd(journal_fd)
        try:
            self._validated_blockade_path(
                journal_fd,
                temp,
                expected_sha256=str(attempt["initial_blockade_sha256"]),
                expected_attempt_id=str(attempt["public_id"]),
            )
        except BaseException as exc:
            renameat2(journal_fd, temp, journal_fd, BLOCKADE_NAME, RENAME_EXCHANGE)
            fsync_fd(journal_fd)
            own_fd = openat2(journal_fd, temp, os.O_RDONLY | os.O_CLOEXEC)
            try:
                own = validate_metadata(own_fd, directory=False)
                own_bytes = _read_bounded(own_fd, 16_384)
            finally:
                os.close(own_fd)
            if not _same_inode(own, removal_metadata) or own_bytes != payload:
                raise MutationApplicationError(
                    "Blockade ajeno preservado; temp ambiguo."
                ) from exc
            unlinkat(journal_fd, temp)
            fsync_fd(journal_fd)
            raise
        unlinkat(journal_fd, temp)
        fsync_fd(journal_fd)

    def _remove_verified_blockade(self, journal_fd: int, payload: bytes) -> None:
        expected_sha = hashlib.sha256(payload).hexdigest()
        tombstone = ".blockade-remove-" + uuid.uuid4().hex + ".tmp"
        renameat2(journal_fd, BLOCKADE_NAME, journal_fd, tombstone, RENAME_NOREPLACE)
        fsync_fd(journal_fd)
        try:
            self._validated_blockade_path(
                journal_fd,
                tombstone,
                expected_sha256=expected_sha,
                expected_attempt_id=str(json.loads(payload)["attempt_public_id"]),
                require_removal_ready=True,
            )
        except BaseException:
            try:
                renameat2(journal_fd, tombstone, journal_fd, BLOCKADE_NAME, RENAME_NOREPLACE)
                fsync_fd(journal_fd)
            except BaseException:
                pass
            raise
        unlinkat(journal_fd, tombstone)
        fsync_fd(journal_fd)

    def _discard_file_if_needed(self, file: _File) -> None:
        with self.repository.database.connect_mutation_durable() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state FROM assistant_autonomy_mutation_attempt_files "
                "WHERE proposal_item_id=?",
                (file.item_id,),
            ).fetchone()
            if row is not None and str(row["state"]) in {"planned", "staged"}:
                connection.execute(
                    "UPDATE assistant_autonomy_mutation_attempt_files SET state='discarded',"
                    "state_updated_at=? WHERE proposal_item_id=?",
                    (_time_text(), file.item_id),
                )

    def _validated_initial_blockade(
        self, attempt: dict[str, Any], journal_fd: int
    ) -> dict[str, Any]:
        value = self._validated_blockade_path(
            journal_fd,
            BLOCKADE_NAME,
            expected_sha256=str(attempt["initial_blockade_sha256"]),
            expected_attempt_id=str(attempt["public_id"]),
        )
        if (
            value.get("proposal_public_id") != attempt["proposal_public_id"]
            or value.get("proposal_sha256") != attempt["proposal_sha256"]
            or value.get("gate_id") != attempt["gate_id"]
            or "removal_ready" in value
        ):
            raise MutationApplicationError("Blockade generation no coincide.")
        return value

    @staticmethod
    def _validated_blockade_path(
        journal_fd: int,
        name: str,
        *,
        expected_sha256: str,
        expected_attempt_id: str,
        require_removal_ready: bool = False,
    ) -> dict[str, Any]:
        fd = openat2(journal_fd, name, os.O_RDONLY | os.O_CLOEXEC)
        try:
            metadata = validate_metadata(fd, directory=False)
            raw = _read_bounded(fd, 16_384)
        finally:
            os.close(fd)
        if (
            metadata.uid != os.geteuid()
            or metadata.nlink != 1
            or stat.S_IMODE(metadata.mode) != 0o600
        ):
            raise MutationApplicationError("Blockade no es confiable.")
        try:
            value = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MutationApplicationError("Blockade inválido.") from exc
        if (
            not isinstance(value, dict)
            or hashlib.sha256(raw).hexdigest() != expected_sha256
            or value.get("attempt_public_id") != expected_attempt_id
            or (require_removal_ready and value.get("removal_ready") is not True)
        ):
            raise MutationApplicationError("Blockade generation no coincide.")
        return value

    def _existing_result(self, attempt: dict[str, Any]) -> dict[str, Any] | None:
        with self.repository.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_autonomy_mutation_results WHERE attempt_id=?",
                (int(attempt["id"]),),
            ).fetchone()
            return None if row is None else dict(row)

    def _attempt_state(
        self,
        attempt: dict[str, Any],
        state: str,
        manifest: _Journal,
        **fields: str,
    ) -> None:
        manifest.append(state)
        self._sync_manifest(attempt, manifest)
        self._set_attempt(attempt, state, **fields)

    def _set_attempt(self, attempt: dict[str, Any], state: str, **fields: str | None) -> None:
        assignments = ["state=?", "state_updated_at=?"]
        values: list[Any] = [state, _time_text()]
        for key, value in fields.items():
            assignments.append(f"{key}=?")
            values.append(value)
        values.append(int(attempt["id"]))
        with self.repository.database.connect_mutation_durable() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempts SET "
                f"{','.join(assignments)} WHERE id=?",
                values,
            )
        attempt["state"] = state

    def _sync_manifest(self, attempt: dict[str, Any], manifest: _Journal) -> None:
        with self.repository.database.connect_mutation_durable() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempts SET manifest_sequence=?,"
                "manifest_tail_sha256=?,state_updated_at=? WHERE id=?",
                (manifest.sequence, manifest.tail, _time_text(), int(attempt["id"])),
            )

    def _insert_result(
        self,
        attempt: dict[str, Any],
        outcome: str,
        manifest: _Journal,
        published: int,
        restored: int,
        *,
        code: str | None = None,
    ) -> None:
        manifest.append(outcome)
        self._sync_manifest(attempt, manifest)
        with self.repository.database.connect_mutation_durable() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO assistant_autonomy_mutation_results(
                       public_id,attempt_id,outcome,published_count,restored_count,
                       final_manifest_sequence,final_manifest_sha256,observation_code,
                       summary,observed_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    uuid.uuid4().hex, int(attempt["id"]), outcome, published, restored,
                    manifest.sequence, manifest.tail, code, _summary(outcome), _time_text(),
                ),
            )
        self._crash_hook("result_durable")

    def _set_file_manual(self, attempt: dict[str, Any], file: _File) -> None:
        with self.repository.database.connect_mutation_durable() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE assistant_autonomy_mutation_attempt_files "
                "SET state='manual_intervention_required',error_code='rollback_ambiguous',"
                "state_updated_at=? WHERE attempt_id=? AND ordinal=?",
                (_time_text(), int(attempt["id"]), file.ordinal),
            )

    def _result(
        self,
        attempt: dict[str, Any],
        *,
        outcome: str | None = None,
        replayed: bool = False,
    ) -> MutationApplicationResult:
        if outcome is None:
            with self.repository.database.connect() as connection:
                row = connection.execute(
                    "SELECT outcome FROM assistant_autonomy_mutation_results WHERE attempt_id=?",
                    (int(attempt["id"]),),
                ).fetchone()
                outcome = None if row is None else str(row["outcome"])
                state = connection.execute(
                    "SELECT state FROM assistant_autonomy_mutation_attempts WHERE id=?",
                    (int(attempt["id"]),),
                ).fetchone()["state"]
        else:
            state = attempt["state"]
        return MutationApplicationResult(str(attempt["public_id"]), str(state), outcome, replayed)

    @staticmethod
    def _observation(file: _File, state: str) -> dict[str, Any]:
        return {
            "ordinal": file.ordinal,
            "relative_path": file.item.relative_path,
            "state": state,
            "stage_st_dev": None if file.stage is None else file.stage.device,
            "stage_st_ino": None if file.stage is None else file.stage.inode,
        }


class _Stale(MutationApplicationError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _time_text(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _utf8_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required(value: str, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise ValueError(f"{label} inválido.")
    if len(value) > maximum:
        raise ValueError(f"{label} excede límite.")
    return value


def _sha256(value: str, label: str) -> str:
    if len(_required(value, label, 64)) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{label} inválido.")
    return value


def _write_all(fd: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = os.write(fd, value[offset:])
        if written <= 0:
            raise MutationApplicationError("Escritura durable incompleta.")
        offset += written


def _hash_fd(fd: int) -> tuple[int, str]:
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = os.read(fd, 65_536)
        if not chunk:
            break
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def _read_bounded(fd: int, maximum: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    value = os.read(fd, maximum + 1)
    if len(value) > maximum:
        raise MutationApplicationError("Archivo confiable excede límite.")
    return value


def _same_inode(left: LinuxStat, right: LinuxStat) -> bool:
    return left.device == right.device and left.inode == right.inode


def _is_enoent(exc: LinuxFilesystemError) -> bool:
    cause = exc.__cause__
    return isinstance(cause, OSError) and cause.errno == errno.ENOENT


def _summary(outcome: str) -> str:
    return {
        "filesystem_succeeded": "Filesystem mutation completed durably.",
        "expired": "Mutation authority expired before publication.",
        "stale": "Mutation preimage became stale before publication.",
        "failed_before_publication": "Mutation preparation failed before publication.",
        "rolled_back": "Published mutation was restored from exact inode provenance.",
    }[outcome]
