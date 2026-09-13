"""Read-only trusted inspection and classification of mutation recovery evidence."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from elyndra.autonomy.linux_fs import (
    LinuxFilesystemError,
    LinuxStat,
    openat2,
    statx_fd,
    validate_metadata,
)
from elyndra.autonomy.repository import AutonomyRepository
from elyndra.autonomy.workspace_lease import (
    BLOCKADE_NAME,
    JOURNAL_NAME,
    WorkspaceIdentity,
    WorkspaceLeaseCoordinator,
    WorkspaceLeaseMode,
)

_MANIFEST_DOMAIN_BYTES = b"elyndra.mutation-manifest.v1\0"
_MANIFEST_DOMAIN = "elyndra.mutation-manifest.v1"
_BLOCKADE_DOMAIN = "elyndra.mutation-blockade.v1"
_EMERGENCY_BLOCKADE_DOMAIN = "elyndra.mutation-recovery-blockade.v1"
_FORMAT_VERSION = "v1"
_MAX_MANIFEST_BYTES = 262_144
_MAX_BLOCKADE_BYTES = 16_384
_TERMINAL_STATES = frozenset(
    {"succeeded", "expired", "stale", "failed_before_publication", "rolled_back"}
)
_OUTCOME_STATES = frozenset(
    {"filesystem_succeeded", "expired", "stale", "failed_before_publication", "rolled_back"}
)
_EMERGENCY_BLOCKADE_KEYS = frozenset(
    {
        "domain", "format_version", "generation", "mutation_vault_id",
        "attempt_public_id", "proposal_public_id", "proposal_sha256", "gate_id",
        "run_public_id", "step_id", "workspace_st_dev", "workspace_st_ino",
        "workspace_mount_id", "canonical_workspace_root_sha256",
        "initial_blockade_sha256", "manifest_sequence", "manifest_tail_sha256",
        "recovery_code",
    }
)


class MutationRecoveryError(PermissionError):
    """Durable recovery evidence could not be trusted."""


class MutationRecoveryDisposition(StrEnum):
    PRECLAIM_ORPHAN_UNBLOCKED = "preclaim_orphan_unblocked"
    BLOCKED_PRECLAIM_ORPHAN = "blocked_preclaim_orphan"
    FAILED_BEFORE_PUBLICATION = "failed_before_publication"
    STALE = "stale"
    COMPLETE_FILESYSTEM_SUCCESS = "complete_filesystem_success"
    ADOPT_DURABLE_OUTCOME = "adopt_durable_outcome"
    ROLLBACK_REQUIRED = "rollback_required"
    CLEANUP_REQUIRED = "cleanup_required"
    TERMINALIZATION_REQUIRED = "terminalization_required"
    ALREADY_TERMINAL = "already_terminal"
    EMERGENCY_BLOCKADE_REQUIRED = "emergency_blockade_required"
    MANUAL_INTERVENTION_REQUIRED = "manual_intervention_required"


class MutationRecoveryPhysicalState(StrEnum):
    EXACT_UNPUBLISHED = "exact_unpublished"
    EXACT_PUBLISHED = "exact_published"
    EXACT_ROLLED_BACK = "exact_rolled_back"
    AMBIGUOUS = "ambiguous"


class MutationBlockadeState(StrEnum):
    ABSENT = "absent"
    EXACT_INITIAL = "exact_initial"
    EXACT_REMOVAL_READY = "exact_removal_ready"
    EMERGENCY_RECOVERY = "emergency_recovery"
    MALFORMED = "malformed"
    FOREIGN_VAULT = "foreign_vault"
    MISMATCHED = "mismatched"


@dataclass(frozen=True, slots=True)
class MutationManifestSnapshot:
    sequence: int
    tail_sha256: str
    record_hashes: tuple[str, ...]
    record_states: tuple[str, ...]
    outcome_record: str | None
    outcome_sequence: int | None
    outcome_sha256: str | None
    db_sequence: int | None
    db_sha256: str | None
    db_pointer: str


@dataclass(frozen=True, slots=True)
class MutationRecoveryFileObservation:
    ordinal: int
    relative_path: str
    operation: str
    db_state: str
    physical_state: MutationRecoveryPhysicalState
    recovery_code: str | None = None


@dataclass(frozen=True, slots=True)
class MutationRecoveryPlan:
    disposition: MutationRecoveryDisposition
    workspace: WorkspaceIdentity
    attempt_public_id: str | None
    proposal_public_id: str | None
    proposal_sha256: str | None
    gate_id: str | None
    run_public_id: str | None
    step_id: str | None
    attempt_state: str | None
    result_public_id: str | None
    result_outcome: str | None
    blockade_state: MutationBlockadeState
    manifest: MutationManifestSnapshot | None
    files: tuple[MutationRecoveryFileObservation, ...]
    outcome_manifest_without_result: str | None
    outcome_sequence: int | None
    outcome_sha256: str | None
    recovery_code: str | None = None


@dataclass(frozen=True, slots=True)
class _ObjectObservation:
    metadata: LinuxStat
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _BlockadeObservation:
    state: MutationBlockadeState
    value: dict[str, Any] | None
    raw_sha256: str | None


class MutationRecoveryInspector:
    """Create one deterministic recovery plan without changing durable state."""

    def __init__(
        self,
        repository: AutonomyRepository,
        *,
        workspace_lease_coordinator: WorkspaceLeaseCoordinator | None = None,
    ) -> None:
        self.repository = repository
        self.coordinator = workspace_lease_coordinator or WorkspaceLeaseCoordinator()

    def inspect(
        self,
        workspace_root: str,
        *,
        attempt_public_id: str | None = None,
    ) -> MutationRecoveryPlan:
        """Inspect one blockade/attempt under a stable exclusive workspace snapshot."""

        identity = self.coordinator.identity(workspace_root)
        lease = self.coordinator.acquire(identity, WorkspaceLeaseMode.EXCLUSIVE)
        try:
            lease.receipt.require_live(mode=WorkspaceLeaseMode.EXCLUSIVE, identity=identity)
            if self.coordinator.identity(workspace_root) != identity:
                raise MutationRecoveryError("Workspace cambió tras adquirir recovery lease.")
            root_fd = self._open_exact_root(identity)
            try:
                try:
                    journal_fd = openat2(
                        root_fd,
                        JOURNAL_NAME,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
                    )
                except LinuxFilesystemError as exc:
                    blockade = _BlockadeObservation(MutationBlockadeState.ABSENT, None, None)
                    return self._manual_plan(
                        identity,
                        blockade,
                        "journal_missing" if _is_enoent(exc) else "journal_invalid",
                        attempt_public_id,
                    )
                try:
                    try:
                        journal_metadata = validate_metadata(journal_fd, directory=True)
                    except (LinuxFilesystemError, OSError):
                        return self._manual_plan(
                            identity,
                            _BlockadeObservation(MutationBlockadeState.ABSENT, None, None),
                            "journal_invalid",
                            attempt_public_id,
                        )
                    if (
                        journal_metadata.uid != os.geteuid()
                        or stat.S_IMODE(journal_metadata.mode) != 0o700
                        or journal_metadata.mount_id != identity.mount_id
                    ):
                        return self._manual_plan(
                            identity,
                            _BlockadeObservation(MutationBlockadeState.ABSENT, None, None),
                            "journal_invalid",
                            attempt_public_id,
                        )
                    return self._inspect_locked(
                        identity,
                        root_fd,
                        journal_fd,
                        attempt_public_id=attempt_public_id,
                    )
                finally:
                    os.close(journal_fd)
            finally:
                os.close(root_fd)
        finally:
            lease.close()

    @staticmethod
    def _open_exact_root(identity: WorkspaceIdentity) -> int:
        fd = os.open(
            identity.canonical_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            metadata = statx_fd(fd)
            if (
                not stat.S_ISDIR(metadata.mode)
                or metadata.device != identity.st_dev
                or metadata.inode != identity.st_ino
                or metadata.mount_id != identity.mount_id
            ):
                raise MutationRecoveryError("Recovery root fd no coincide con lease.")
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _inspect_locked(
        self,
        identity: WorkspaceIdentity,
        root_fd: int,
        journal_fd: int,
        *,
        attempt_public_id: str | None,
    ) -> MutationRecoveryPlan:
        vault_id = self._mutation_vault_id()
        try:
            raw_blockade = self._read_blockade(journal_fd)
        except MutationRecoveryError:
            return self._manual_plan(
                identity,
                _BlockadeObservation(MutationBlockadeState.MALFORMED, None, None),
                "blockade_invalid",
                attempt_public_id,
            )
        blockade = self._classify_blockade_shape(raw_blockade, vault_id=vault_id)
        implicated = attempt_public_id
        if implicated is None and blockade.value is not None:
            value = blockade.value.get("attempt_public_id")
            implicated = value if isinstance(value, str) else None
        if implicated is None:
            return self._manual_plan(identity, blockade, "attempt_not_identified")
        if not _bounded_text(implicated, 128):
            return self._manual_plan(identity, blockade, "attempt_id_invalid")

        attempt = self._attempt_row(implicated)
        if attempt is not None and not _attempt_matches_workspace(attempt, identity):
            return self._manual_plan(
                identity, blockade, "attempt_workspace_mismatch", implicated
            )
        try:
            manifest = self._read_manifest(
                journal_fd,
                implicated,
                expected_identity=attempt if attempt is not None else blockade.value,
                attempt=attempt,
                trusted_mount_id=identity.mount_id,
            )
        except MutationRecoveryError:
            return self._manual_plan(identity, blockade, "manifest_invalid", implicated)
        blockade = self._correlate_blockade(
            blockade,
            identity=identity,
            vault_id=vault_id,
            attempt=attempt,
            manifest=manifest,
        )
        if attempt is None:
            if (
                blockade.state is MutationBlockadeState.EXACT_INITIAL
                and blockade.value is not None
                and not self._preclaim_lineage_matches(blockade.value, identity)
            ):
                blockade = _BlockadeObservation(
                    MutationBlockadeState.MISMATCHED,
                    blockade.value,
                    blockade.raw_sha256,
                )
            return self._preclaim_plan(identity, implicated, blockade, manifest)

        if manifest is None:
            return self._manual_plan(identity, blockade, "manifest_missing", implicated)

        result = self._result_row(int(attempt["id"]))
        proposal_items, attempt_files = self._file_rows(attempt)
        complete_coverage = _has_complete_item_coverage(proposal_items, attempt_files)
        try:
            files = self._observe_files(root_fd, journal_fd, attempt, attempt_files)
        except (LinuxFilesystemError, MutationRecoveryError, OSError):
            return self._manual_plan(
                identity, blockade, "attempt_artifacts_missing", implicated
            )
        prepublication_targets: tuple[str, ...] | None = None
        if str(attempt["state"]) in {"claimed", "preparing", "prepared"}:
            prepublication_targets = self._check_prepublication_targets(
                root_fd, identity, proposal_items
            )
        return self._decide(
            identity,
            attempt,
            result,
            blockade,
            manifest,
            files,
            complete_coverage=complete_coverage,
            prepublication_targets=prepublication_targets,
        )

    def _preclaim_lineage_matches(self, value: dict[str, Any], identity: WorkspaceIdentity) -> bool:
        with self.repository.database.connect() as connection:
            row = connection.execute(
                """SELECT proposal.public_id, proposal.proposal_sha256,
                          proposal.step_id, proposal.actor, proposal.workspace_root,
                          run.public_id AS run_public_id, binding.gate_id
                   FROM assistant_autonomy_mutation_proposals AS proposal
                   JOIN assistant_autonomy_runs AS run ON run.id=proposal.run_id
                   JOIN assistant_autonomy_mutation_gate_bindings AS binding
                     ON binding.proposal_id=proposal.id
                   WHERE proposal.public_id=? AND binding.gate_id=?""",
                (value.get("proposal_public_id"), value.get("gate_id")),
            ).fetchone()
        return bool(
            row is not None
            and row["proposal_sha256"] == value.get("proposal_sha256")
            and row["step_id"] == value.get("step_id")
            and row["run_public_id"] == value.get("run_public_id")
            and row["workspace_root"] == identity.canonical_root
            and hashlib.sha256(str(row["actor"]).encode()).hexdigest()
            == value.get("actor_sha256")
            and hashlib.sha256(identity.canonical_root.encode()).hexdigest()
            == value.get("canonical_workspace_root_sha256")
        )

    def _mutation_vault_id(self) -> str:
        with self.repository.database.connect() as connection:
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key='mutation_vault_id'"
            ).fetchone()
            if row is None or not _bounded_text(str(row[0]), 128):
                raise MutationRecoveryError("mutation_vault_id ausente o inválido.")
            return str(row[0])

    def _attempt_row(self, public_id: str) -> dict[str, Any] | None:
        with self.repository.database.connect() as connection:
            row = connection.execute(
                """SELECT attempt.*, run.public_id AS run_public_id
                   FROM assistant_autonomy_mutation_attempts AS attempt
                   JOIN assistant_autonomy_runs AS run ON run.id=attempt.run_id
                   WHERE attempt.public_id=?""",
                (public_id,),
            ).fetchone()
            return None if row is None else dict(row)

    def _result_row(self, attempt_id: int) -> dict[str, Any] | None:
        with self.repository.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_autonomy_mutation_results WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            return None if row is None else dict(row)

    @staticmethod
    def _read_blockade(journal_fd: int) -> bytes | None:
        try:
            fd = openat2(journal_fd, BLOCKADE_NAME, os.O_RDONLY | os.O_CLOEXEC)
        except LinuxFilesystemError as exc:
            if _is_enoent(exc):
                return None
            raise MutationRecoveryError("Blockade no puede inspeccionarse.") from exc
        try:
            metadata = validate_metadata(fd, directory=False)
            if (
                metadata.uid != os.geteuid()
                or metadata.nlink != 1
                or stat.S_IMODE(metadata.mode) != 0o600
            ):
                raise MutationRecoveryError("Blockade metadata no confiable.")
            return _read_bounded(fd, _MAX_BLOCKADE_BYTES)
        finally:
            os.close(fd)

    @staticmethod
    def _classify_blockade_shape(raw: bytes | None, *, vault_id: str) -> _BlockadeObservation:
        if raw is None:
            return _BlockadeObservation(MutationBlockadeState.ABSENT, None, None)
        digest = hashlib.sha256(raw).hexdigest()
        try:
            value = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _BlockadeObservation(MutationBlockadeState.MALFORMED, None, digest)
        if not isinstance(value, dict) or _canonical(value) != raw:
            return _BlockadeObservation(MutationBlockadeState.MALFORMED, None, digest)
        if value.get("format_version") != _FORMAT_VERSION:
            return _BlockadeObservation(MutationBlockadeState.MALFORMED, value, digest)
        if value.get("mutation_vault_id") != vault_id:
            return _BlockadeObservation(MutationBlockadeState.FOREIGN_VAULT, value, digest)
        domain = value.get("domain")
        if domain == _EMERGENCY_BLOCKADE_DOMAIN:
            if (
                frozenset(value) != _EMERGENCY_BLOCKADE_KEYS
                or value.get("generation") != "emergency"
            ):
                return _BlockadeObservation(
                    MutationBlockadeState.MALFORMED, value, digest
                )
            return _BlockadeObservation(MutationBlockadeState.EMERGENCY_RECOVERY, value, digest)
        if domain != _BLOCKADE_DOMAIN:
            return _BlockadeObservation(MutationBlockadeState.MALFORMED, value, digest)
        state = (
            MutationBlockadeState.EXACT_REMOVAL_READY
            if value.get("removal_ready") is True
            else MutationBlockadeState.EXACT_INITIAL
        )
        return _BlockadeObservation(state, value, digest)

    def _read_manifest(
        self,
        journal_fd: int,
        attempt_id: str,
        *,
        expected_identity: dict[str, Any] | None,
        attempt: dict[str, Any] | None,
        trusted_mount_id: int,
    ) -> MutationManifestSnapshot | None:
        try:
            attempt_fd = openat2(
                journal_fd, attempt_id, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
            )
        except LinuxFilesystemError as exc:
            if _is_enoent(exc):
                return None
            raise MutationRecoveryError("Attempt journal no puede abrirse.") from exc
        try:
            directory = validate_metadata(attempt_fd, directory=True)
            if (
                directory.uid != os.geteuid()
                or stat.S_IMODE(directory.mode) != 0o700
                or directory.mount_id != trusted_mount_id
            ):
                raise MutationRecoveryError("Attempt journal metadata no confiable.")
            fd = openat2(attempt_fd, "manifest.jsonl", os.O_RDONLY | os.O_CLOEXEC)
            try:
                metadata = validate_metadata(fd, directory=False)
                if (
                    metadata.uid != os.geteuid()
                    or metadata.nlink != 1
                    or stat.S_IMODE(metadata.mode) != 0o600
                    or metadata.mount_id != trusted_mount_id
                ):
                    raise MutationRecoveryError("Manifest metadata no confiable.")
                raw = _read_bounded(fd, _MAX_MANIFEST_BYTES)
            finally:
                os.close(fd)
        except LinuxFilesystemError as exc:
            if _is_enoent(exc):
                return None
            raise MutationRecoveryError("Manifest no puede inspeccionarse.") from exc
        finally:
            os.close(attempt_fd)
        return self._validate_manifest(raw, expected_identity=expected_identity, attempt=attempt)

    @staticmethod
    def _validate_manifest(
        raw: bytes,
        *,
        expected_identity: dict[str, Any] | None,
        attempt: dict[str, Any] | None,
    ) -> MutationManifestSnapshot:
        if not raw or not raw.endswith(b"\n"):
            raise MutationRecoveryError("Manifest no está newline-terminated.")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise MutationRecoveryError("Manifest no es UTF-8 estricto.") from exc
        hashes: list[str] = []
        states: list[str] = []
        invariant: dict[str, Any] | None = None
        outcome: str | None = None
        outcome_sequence: int | None = None
        outcome_sha256: str | None = None
        identity_keys = (
            "attempt_public_id",
            "proposal_public_id",
            "proposal_sha256",
            "gate_id",
            "run_public_id",
            "step_id",
            "workspace_st_dev",
            "workspace_st_ino",
            "workspace_mount_id",
        )
        for sequence, line in enumerate(text.splitlines()):
            encoded = line.encode("utf-8")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MutationRecoveryError("Manifest contiene JSON malformado.") from exc
            if not isinstance(record, dict) or _canonical(record) != encoded:
                raise MutationRecoveryError("Manifest contiene JSON no canónico.")
            claimed = record.get("record_sha256")
            material = dict(record)
            material.pop("record_sha256", None)
            actual = hashlib.sha256(_MANIFEST_DOMAIN_BYTES + _canonical(material)).hexdigest()
            previous = "0" * 64 if sequence == 0 else hashes[-1]
            if (
                record.get("manifest_domain") != _MANIFEST_DOMAIN
                or record.get("format_version") != _FORMAT_VERSION
                or record.get("sequence") != sequence
                or record.get("previous_record_sha256") != previous
                or claimed != actual
            ):
                raise MutationRecoveryError("Manifest hash chain inválida.")
            current = {key: record.get(key) for key in identity_keys}
            if invariant is None:
                invariant = current
            elif current != invariant:
                raise MutationRecoveryError("Manifest identity cambió entre records.")
            hashes.append(actual)
            state = record.get("attempt_state")
            if not isinstance(state, str):
                raise MutationRecoveryError("Manifest attempt_state inválido.")
            states.append(state)
            if state in _OUTCOME_STATES:
                if outcome is not None:
                    raise MutationRecoveryError("Manifest contiene múltiples outcomes.")
                outcome = str(state)
                outcome_sequence = sequence
                outcome_sha256 = actual
        assert invariant is not None
        if not _manifest_identity_matches(invariant, expected_identity):
            raise MutationRecoveryError("Manifest identity no coincide.")
        db_sequence: int | None = None
        db_sha: str | None = None
        pointer = "not_applicable"
        if attempt is not None:
            if hashes[0] != attempt["initial_manifest_sha256"]:
                raise MutationRecoveryError("Initial manifest hash no coincide con DB.")
            db_sequence = int(attempt["manifest_sequence"])
            db_sha = str(attempt["manifest_tail_sha256"])
            if db_sequence < 0 or db_sequence >= len(hashes) or hashes[db_sequence] != db_sha:
                raise MutationRecoveryError("DB manifest pointer no pertenece a chain.")
            pointer = "exact_tail" if db_sequence == len(hashes) - 1 else "valid_prefix"
        return MutationManifestSnapshot(
            sequence=len(hashes) - 1,
            tail_sha256=hashes[-1],
            record_hashes=tuple(hashes),
            record_states=tuple(states),
            outcome_record=outcome,
            outcome_sequence=outcome_sequence,
            outcome_sha256=outcome_sha256,
            db_sequence=db_sequence,
            db_sha256=db_sha,
            db_pointer=pointer,
        )

    @staticmethod
    def _correlate_blockade(
        blockade: _BlockadeObservation,
        *,
        identity: WorkspaceIdentity,
        vault_id: str,
        attempt: dict[str, Any] | None,
        manifest: MutationManifestSnapshot | None,
    ) -> _BlockadeObservation:
        if blockade.state in {
            MutationBlockadeState.ABSENT,
            MutationBlockadeState.MALFORMED,
            MutationBlockadeState.FOREIGN_VAULT,
        }:
            return blockade
        value = blockade.value
        assert value is not None
        expected = attempt or value
        expected_attempt = expected.get("public_id", value.get("attempt_public_id"))
        exact_workspace = (
            value.get("workspace_st_dev") == identity.st_dev
            and value.get("workspace_st_ino") == identity.st_ino
            and value.get("workspace_mount_id") == identity.mount_id
        )
        exact_common = (
            value.get("mutation_vault_id") == vault_id
            and value.get("attempt_public_id") == expected_attempt
            and value.get("proposal_public_id")
            == expected.get("proposal_public_id", value.get("proposal_public_id"))
            and value.get("proposal_sha256")
            == expected.get("proposal_sha256", value.get("proposal_sha256"))
            and value.get("gate_id") == expected.get("gate_id", value.get("gate_id"))
        )
        if not exact_workspace or not exact_common:
            return _BlockadeObservation(
                MutationBlockadeState.MISMATCHED, value, blockade.raw_sha256
            )
        if blockade.state is MutationBlockadeState.EMERGENCY_RECOVERY:
            emergency_exact = bool(
                attempt is not None
                and manifest is not None
                and value.get("canonical_workspace_root_sha256")
                == hashlib.sha256(identity.canonical_root.encode()).hexdigest()
                and value.get("initial_blockade_sha256")
                == attempt["initial_blockade_sha256"]
                and value.get("manifest_sequence") == manifest.sequence
                and value.get("manifest_tail_sha256") == manifest.tail_sha256
                and value.get("run_public_id") == attempt["run_public_id"]
                and value.get("step_id") == attempt["step_id"]
                and _bounded_text(value.get("recovery_code"), 80)
            )
            if not emergency_exact:
                return _BlockadeObservation(
                    MutationBlockadeState.MISMATCHED, value, blockade.raw_sha256
                )
        if (
            attempt is not None
            and blockade.state is MutationBlockadeState.EXACT_INITIAL
            and blockade.raw_sha256 != attempt["initial_blockade_sha256"]
        ):
            return _BlockadeObservation(
                MutationBlockadeState.MISMATCHED, value, blockade.raw_sha256
            )
        if attempt is None and (
            manifest is None
            or value.get("initial_manifest_sequence") != 0
            or value.get("initial_manifest_sha256") != manifest.record_hashes[0]
        ):
            return _BlockadeObservation(
                MutationBlockadeState.MISMATCHED, value, blockade.raw_sha256
            )
        return blockade

    def _file_rows(
        self, attempt: dict[str, Any]
    ) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
        with self.repository.database.connect() as connection:
            proposal_items = connection.execute(
                "SELECT * FROM assistant_autonomy_mutation_items "
                "WHERE proposal_id=? ORDER BY ordinal",
                (int(attempt["proposal_id"]),),
            ).fetchall()
            attempt_files = connection.execute(
                "SELECT * FROM assistant_autonomy_mutation_attempt_files "
                "WHERE attempt_id=? ORDER BY ordinal",
                (int(attempt["id"]),),
            ).fetchall()
        return (
            tuple(dict(row) for row in proposal_items),
            tuple(dict(row) for row in attempt_files),
        )

    def _observe_files(
        self,
        root_fd: int,
        journal_fd: int,
        attempt: dict[str, Any],
        rows: tuple[dict[str, Any], ...],
    ) -> tuple[MutationRecoveryFileObservation, ...]:
        attempt_fd = openat2(
            journal_fd,
            str(attempt["public_id"]),
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        )
        try:
            directory = validate_metadata(attempt_fd, directory=True)
            if directory.mount_id != int(attempt["workspace_mount_id"]):
                raise MutationRecoveryError("Attempt journal cambió de mount.")
            return tuple(self._observe_file(root_fd, attempt_fd, row) for row in rows)
        finally:
            os.close(attempt_fd)

    @staticmethod
    def _check_prepublication_targets(
        root_fd: int,
        identity: WorkspaceIdentity,
        proposal_items: tuple[dict[str, Any], ...],
    ) -> tuple[str, ...]:
        observations: list[str] = []
        for item in proposal_items:
            parts = str(item["relative_path"]).split("/")
            parent_path = "." if len(parts) == 1 else "/".join(parts[:-1])
            try:
                parent_fd = openat2(
                    root_fd,
                    parent_path,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
                )
                try:
                    parent = validate_metadata(parent_fd, directory=True)
                    if parent.mount_id != identity.mount_id:
                        observations.append("ambiguous")
                        continue
                    target = _observe_optional(parent_fd, parts[-1])
                finally:
                    os.close(parent_fd)
            except (LinuxFilesystemError, MutationRecoveryError, OSError):
                observations.append("ambiguous")
                continue
            if item["operation"] == "create":
                observations.append("intact" if target is None else "stale")
                continue
            if target is None:
                observations.append("stale")
                continue
            trusted = (
                target.metadata.mount_id == identity.mount_id
                and target.metadata.uid == os.geteuid()
                and target.metadata.nlink == 1
                and not target.metadata.mode & (stat.S_ISUID | stat.S_ISGID)
            )
            if not trusted:
                observations.append("ambiguous")
                continue
            exact = (
                target.size == int(item["original_size"])
                and target.sha256 == item["original_sha256"]
            )
            observations.append("intact" if exact else "stale")
        return tuple(observations)

    def _observe_file(
        self, root_fd: int, attempt_fd: int, row: dict[str, Any]
    ) -> MutationRecoveryFileObservation:
        path = str(row["relative_path"])
        parts = path.split("/")
        parent_path = "." if len(parts) == 1 else "/".join(parts[:-1])
        try:
            parent_fd = openat2(root_fd, parent_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                parent = validate_metadata(parent_fd, directory=True)
                if (
                    parent.device != int(row["parent_st_dev"])
                    or parent.inode != int(row["parent_st_ino"])
                    or parent.mount_id != int(row["parent_mount_id"])
                ):
                    return _file_observation(
                        row, MutationRecoveryPhysicalState.AMBIGUOUS, "parent_mismatch"
                    )
                target = _observe_optional(parent_fd, parts[-1])
            finally:
                os.close(parent_fd)
            stage = _observe_optional(attempt_fd, str(row["artifact_name"]))
            witness = _observe_optional(attempt_fd, str(row["witness_name"]))
            physical = _classify_physical(row, target=target, stage=stage, witness=witness)
            return _file_observation(row, physical)
        except (LinuxFilesystemError, MutationRecoveryError, OSError):
            return _file_observation(
                row, MutationRecoveryPhysicalState.AMBIGUOUS, "filesystem_error"
            )

    def _preclaim_plan(
        self,
        identity: WorkspaceIdentity,
        attempt_id: str,
        blockade: _BlockadeObservation,
        manifest: MutationManifestSnapshot | None,
    ) -> MutationRecoveryPlan:
        if manifest is None:
            return self._manual_plan(identity, blockade, "manifest_missing", attempt_id)
        exact_preclaim = (
            manifest.sequence == 0
            and manifest.record_states == ("preclaim",)
            and manifest.outcome_record is None
        )
        if not exact_preclaim:
            disposition = MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
        elif blockade.state is MutationBlockadeState.ABSENT:
            disposition = MutationRecoveryDisposition.PRECLAIM_ORPHAN_UNBLOCKED
        elif blockade.state is MutationBlockadeState.EXACT_INITIAL:
            disposition = MutationRecoveryDisposition.BLOCKED_PRECLAIM_ORPHAN
        else:
            disposition = MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
        value = blockade.value or {}
        return MutationRecoveryPlan(
            disposition=disposition,
            workspace=identity,
            attempt_public_id=attempt_id,
            proposal_public_id=_optional_text(value.get("proposal_public_id")),
            proposal_sha256=_optional_text(value.get("proposal_sha256")),
            gate_id=_optional_text(value.get("gate_id")),
            run_public_id=_optional_text(value.get("run_public_id")),
            step_id=_optional_text(value.get("step_id")),
            attempt_state=None,
            result_public_id=None,
            result_outcome=None,
            blockade_state=blockade.state,
            manifest=manifest,
            files=(),
            outcome_manifest_without_result=manifest.outcome_record,
            outcome_sequence=manifest.outcome_sequence,
            outcome_sha256=manifest.outcome_sha256,
            recovery_code=(
                None
                if disposition is not MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
                else "preclaim_mismatch"
            ),
        )

    def _decide(
        self,
        identity: WorkspaceIdentity,
        attempt: dict[str, Any],
        result: dict[str, Any] | None,
        blockade: _BlockadeObservation,
        manifest: MutationManifestSnapshot | None,
        files: tuple[MutationRecoveryFileObservation, ...],
        *,
        complete_coverage: bool,
        prepublication_targets: tuple[str, ...] | None,
    ) -> MutationRecoveryPlan:
        state = str(attempt["state"])
        invalid_blockade = blockade.state in {
            MutationBlockadeState.MALFORMED,
            MutationBlockadeState.FOREIGN_VAULT,
            MutationBlockadeState.MISMATCHED,
        }
        physical = [item.physical_state for item in files]
        if state == "manual_intervention_required":
            disposition = MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
        elif result is not None:
            result_evidence_valid = _result_evidence_matches(attempt, result, blockade, manifest)
            cleanup_ready = _has_exact_cleanup_ready(manifest, result)
            coverage_required = result["outcome"] in {"filesystem_succeeded", "rolled_back"}
            if (
                invalid_blockade
                or not result_evidence_valid
                or (coverage_required and not complete_coverage)
            ):
                disposition = MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
            elif state in _TERMINAL_STATES:
                disposition = (
                    MutationRecoveryDisposition.ALREADY_TERMINAL
                    if blockade.state is MutationBlockadeState.ABSENT and cleanup_ready
                    else MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
                )
            elif blockade.state is MutationBlockadeState.ABSENT:
                disposition = (
                    MutationRecoveryDisposition.TERMINALIZATION_REQUIRED
                    if cleanup_ready
                    else MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
                )
            else:
                disposition = MutationRecoveryDisposition.CLEANUP_REQUIRED
        elif (
            invalid_blockade
            or blockade.state is MutationBlockadeState.EXACT_REMOVAL_READY
        ):
            disposition = MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
        elif blockade.state is MutationBlockadeState.ABSENT:
            disposition = MutationRecoveryDisposition.EMERGENCY_BLOCKADE_REQUIRED
        elif manifest is not None and manifest.outcome_record is not None:
            coverage_required = manifest.outcome_record in {
                "filesystem_succeeded",
                "rolled_back",
            }
            disposition = (
                MutationRecoveryDisposition.ADOPT_DURABLE_OUTCOME
                if (complete_coverage or not coverage_required)
                and _outcome_is_adoptable(attempt, manifest, files)
                else MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
            )
        else:
            all_published = bool(physical) and all(
                item is MutationRecoveryPhysicalState.EXACT_PUBLISHED for item in physical
            )
            exact = all(item is not MutationRecoveryPhysicalState.AMBIGUOUS for item in physical)
            if state in {"claimed", "preparing", "prepared"}:
                assert prepublication_targets is not None
                publication_contradiction = any(
                    item
                    in {
                        MutationRecoveryPhysicalState.EXACT_PUBLISHED,
                        MutationRecoveryPhysicalState.EXACT_ROLLED_BACK,
                    }
                    for item in physical
                )
                if publication_contradiction or "ambiguous" in prepublication_targets:
                    disposition = MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
                elif "stale" in prepublication_targets:
                    disposition = MutationRecoveryDisposition.STALE
                elif prepublication_targets and all(
                    item == "intact" for item in prepublication_targets
                ):
                    disposition = MutationRecoveryDisposition.FAILED_BEFORE_PUBLICATION
                else:
                    disposition = MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
            elif state == "publishing":
                if not complete_coverage:
                    disposition = MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
                elif all_published:
                    disposition = MutationRecoveryDisposition.COMPLETE_FILESYSTEM_SUCCESS
                elif exact and all(
                    item
                    in {
                        MutationRecoveryPhysicalState.EXACT_PUBLISHED,
                        MutationRecoveryPhysicalState.EXACT_UNPUBLISHED,
                    }
                    for item in physical
                ):
                    disposition = MutationRecoveryDisposition.ROLLBACK_REQUIRED
                else:
                    disposition = MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
            elif state == "recovery_required":
                disposition = (
                    MutationRecoveryDisposition.ROLLBACK_REQUIRED
                    if complete_coverage and exact
                    else MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
                )
            elif state == "filesystem_applied":
                disposition = (
                    MutationRecoveryDisposition.COMPLETE_FILESYSTEM_SUCCESS
                    if complete_coverage and all_published
                    else MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
                )
            else:
                disposition = MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED
        return MutationRecoveryPlan(
            disposition=disposition,
            workspace=identity,
            attempt_public_id=str(attempt["public_id"]),
            proposal_public_id=str(attempt["proposal_public_id"]),
            proposal_sha256=str(attempt["proposal_sha256"]),
            gate_id=str(attempt["gate_id"]),
            run_public_id=str(attempt["run_public_id"]),
            step_id=str(attempt["step_id"]),
            attempt_state=state,
            result_public_id=None if result is None else str(result["public_id"]),
            result_outcome=None if result is None else str(result["outcome"]),
            blockade_state=blockade.state,
            manifest=manifest,
            files=files,
            outcome_manifest_without_result=(
                None if result is not None or manifest is None else manifest.outcome_record
            ),
            outcome_sequence=None if manifest is None else manifest.outcome_sequence,
            outcome_sha256=None if manifest is None else manifest.outcome_sha256,
            recovery_code=None,
        )

    @staticmethod
    def _manual_plan(
        identity: WorkspaceIdentity,
        blockade: _BlockadeObservation,
        code: str,
        attempt_id: str | None = None,
    ) -> MutationRecoveryPlan:
        return MutationRecoveryPlan(
            disposition=MutationRecoveryDisposition.MANUAL_INTERVENTION_REQUIRED,
            workspace=identity,
            attempt_public_id=attempt_id,
            proposal_public_id=None,
            proposal_sha256=None,
            gate_id=None,
            run_public_id=None,
            step_id=None,
            attempt_state=None,
            result_public_id=None,
            result_outcome=None,
            blockade_state=blockade.state,
            manifest=None,
            files=(),
            outcome_manifest_without_result=None,
            outcome_sequence=None,
            outcome_sha256=None,
            recovery_code=code,
        )


def _classify_physical(
    row: dict[str, Any],
    *,
    target: _ObjectObservation | None,
    stage: _ObjectObservation | None,
    witness: _ObjectObservation | None,
) -> MutationRecoveryPhysicalState:
    expected_mount = int(row["parent_mount_id"])
    if any(
        value is not None and value.metadata.mount_id != expected_mount
        for value in (target, stage, witness)
    ):
        return MutationRecoveryPhysicalState.AMBIGUOUS
    post_inode = _db_inode(row, "stage")
    post_sha = str(row["expected_postimage_sha256"])
    post_size = int(row["expected_postimage_size"])
    post_witness = _matches(witness, post_inode, post_size, post_sha)
    target_post = _matches(target, post_inode, post_size, post_sha)
    stage_post = _matches(stage, post_inode, post_size, post_sha)
    operation = str(row["operation"])
    db_state = str(row["state"])
    if operation == "create":
        if target is None and stage_post and post_witness:
            return MutationRecoveryPhysicalState.EXACT_UNPUBLISHED
        if target_post and stage is None and post_witness:
            return MutationRecoveryPhysicalState.EXACT_PUBLISHED
        if target is None and post_witness and db_state in {"rollback_intent", "rolled_back"}:
            return MutationRecoveryPhysicalState.EXACT_ROLLED_BACK
        return MutationRecoveryPhysicalState.AMBIGUOUS

    pre_inode = _db_inode(row, "preimage")
    pre_sha = str(row["expected_preimage_sha256"])
    pre_size = int(row["expected_preimage_size"])
    target_pre = _matches_original(target, row, pre_inode, pre_size, pre_sha)
    stage_pre = _matches_original(stage, row, pre_inode, pre_size, pre_sha)
    if target_post and stage_pre and post_witness:
        return MutationRecoveryPhysicalState.EXACT_PUBLISHED
    if target_pre and stage_post and post_witness:
        if db_state in {"rollback_intent", "rolled_back"}:
            return MutationRecoveryPhysicalState.EXACT_ROLLED_BACK
        return MutationRecoveryPhysicalState.EXACT_UNPUBLISHED
    return MutationRecoveryPhysicalState.AMBIGUOUS


def _attempt_matches_workspace(
    attempt: dict[str, Any], identity: WorkspaceIdentity
) -> bool:
    return bool(
        attempt.get("workspace_root") == identity.canonical_root
        and int(attempt["workspace_st_dev"]) == identity.st_dev
        and int(attempt["workspace_st_ino"]) == identity.st_ino
        and int(attempt["workspace_mount_id"]) == identity.mount_id
    )


def _has_complete_item_coverage(
    proposal_items: tuple[dict[str, Any], ...],
    attempt_files: tuple[dict[str, Any], ...],
) -> bool:
    if not proposal_items or len(proposal_items) != len(attempt_files):
        return False
    expected_ordinals = {int(item["ordinal"]) for item in proposal_items}
    observed_ordinals = {int(row["ordinal"]) for row in attempt_files}
    if (
        len(expected_ordinals) != len(proposal_items)
        or len(observed_ordinals) != len(attempt_files)
        or expected_ordinals != observed_ordinals
    ):
        return False
    expected_by_ordinal = {int(item["ordinal"]): item for item in proposal_items}
    for row in attempt_files:
        item = expected_by_ordinal[int(row["ordinal"])]
        operation = str(item["operation"])
        preimage_fields = (
            row["preimage_st_dev"],
            row["preimage_st_ino"],
            row["preimage_uid"],
            row["preimage_gid"],
            row["preimage_mode"],
            row["preimage_nlink"],
        )
        exact = (
            int(row["proposal_item_id"]) == int(item["id"])
            and row["relative_path"] == item["relative_path"]
            and row["operation"] == operation
            and row["expected_postimage_sha256"] == item["proposed_sha256"]
            and int(row["expected_postimage_size"]) == int(item["proposed_size"])
            and row["expected_preimage_sha256"] == item["original_sha256"]
            and row["expected_preimage_size"] == item["original_size"]
        )
        if not exact:
            return False
        if operation == "create":
            if int(item["original_exists"]) != 0 or any(
                value is not None for value in preimage_fields
            ):
                return False
        elif (
            operation != "replace"
            or int(item["original_exists"]) != 1
            or any(value is None for value in preimage_fields)
            or int(row["preimage_nlink"]) != 1
        ):
            return False
    return True


def _outcome_is_adoptable(
    attempt: dict[str, Any],
    manifest: MutationManifestSnapshot,
    files: tuple[MutationRecoveryFileObservation, ...],
) -> bool:
    outcome = manifest.outcome_record
    outcome_sequence = manifest.outcome_sequence
    outcome_sha256 = manifest.outcome_sha256
    if (
        outcome is None
        or outcome_sequence is None
        or outcome_sha256 is None
        or outcome_sequence != manifest.sequence
        or manifest.record_hashes[outcome_sequence] != outcome_sha256
        or manifest.db_sequence is None
        or manifest.db_sha256 is None
    ):
        return False
    if manifest.db_sequence == outcome_sequence:
        if manifest.db_sha256 != outcome_sha256:
            return False
    elif manifest.db_sequence == outcome_sequence - 1:
        if manifest.record_hashes[manifest.db_sequence] != manifest.db_sha256:
            return False
    else:
        return False

    state = str(attempt["state"])
    physical = tuple(item.physical_state for item in files)
    if outcome == "filesystem_succeeded":
        return state == "filesystem_applied" and bool(physical) and all(
            item is MutationRecoveryPhysicalState.EXACT_PUBLISHED for item in physical
        )
    if outcome == "rolled_back":
        if state != "recovery_required" or not physical:
            return False
        if any(
            item
            not in {
                MutationRecoveryPhysicalState.EXACT_ROLLED_BACK,
                MutationRecoveryPhysicalState.EXACT_UNPUBLISHED,
            }
            for item in physical
        ):
            return False
        return all(
            item.physical_state is MutationRecoveryPhysicalState.EXACT_ROLLED_BACK
            for item in files
            if item.db_state in {"published", "rollback_intent", "rolled_back"}
        )
    if outcome in {"expired", "stale", "failed_before_publication"}:
        return bool(
            state in {"claimed", "preparing", "prepared"}
            and attempt.get("publication_started_at") is None
            and all(
                item
                not in {
                    MutationRecoveryPhysicalState.EXACT_PUBLISHED,
                    MutationRecoveryPhysicalState.EXACT_ROLLED_BACK,
                }
                for item in physical
            )
        )
    return False


def _has_exact_cleanup_ready(
    manifest: MutationManifestSnapshot,
    result: dict[str, Any],
) -> bool:
    outcome_sequence = int(result["final_manifest_sequence"])
    cleanup_records = tuple(
        index for index, state in enumerate(manifest.record_states) if state == "cleanup_ready"
    )
    return bool(
        cleanup_records == (outcome_sequence + 1,)
        and manifest.sequence == outcome_sequence + 1
        and manifest.db_pointer == "exact_tail"
        and manifest.db_sequence == manifest.sequence
        and manifest.db_sha256 == manifest.tail_sha256
        and manifest.outcome_sequence == outcome_sequence
        and manifest.outcome_sha256 == result["final_manifest_sha256"]
    )


def _observe_optional(directory_fd: int, name: str) -> _ObjectObservation | None:
    try:
        fd = openat2(directory_fd, name, os.O_RDONLY | os.O_CLOEXEC)
    except LinuxFilesystemError as exc:
        if _is_enoent(exc):
            return None
        raise
    try:
        metadata = validate_metadata(fd, directory=False)
        size, digest = _hash_fd(fd)
        return _ObjectObservation(metadata, size, digest)
    finally:
        os.close(fd)


def _matches(
    value: _ObjectObservation | None,
    inode: tuple[int, int] | None,
    size: int,
    digest: str,
) -> bool:
    return (
        value is not None
        and inode is not None
        and (value.metadata.device, value.metadata.inode) == inode
        and value.size == size
        and value.sha256 == digest
    )


def _matches_original(
    value: _ObjectObservation | None,
    row: dict[str, Any],
    inode: tuple[int, int] | None,
    size: int,
    digest: str,
) -> bool:
    return (
        _matches(value, inode, size, digest)
        and value is not None
        and value.metadata.uid == int(row["preimage_uid"])
        and value.metadata.gid == int(row["preimage_gid"])
        and value.metadata.mode == int(row["preimage_mode"])
        and value.metadata.nlink == int(row["preimage_nlink"])
    )


def _db_inode(row: dict[str, Any], prefix: str) -> tuple[int, int] | None:
    device = row.get(f"{prefix}_st_dev")
    inode = row.get(f"{prefix}_st_ino")
    if device is None or inode is None:
        return None
    return int(device), int(inode)


def _file_observation(
    row: dict[str, Any],
    state: MutationRecoveryPhysicalState,
    code: str | None = None,
) -> MutationRecoveryFileObservation:
    return MutationRecoveryFileObservation(
        int(row["ordinal"]),
        str(row["relative_path"]),
        str(row["operation"]),
        str(row["state"]),
        state,
        code,
    )


def _manifest_identity_matches(invariant: dict[str, Any], expected: dict[str, Any] | None) -> bool:
    if expected is None:
        return True
    mappings = {
        "attempt_public_id": "public_id",
        "proposal_public_id": "proposal_public_id",
        "proposal_sha256": "proposal_sha256",
        "gate_id": "gate_id",
        "run_public_id": "run_public_id",
        "step_id": "step_id",
        "workspace_st_dev": "workspace_st_dev",
        "workspace_st_ino": "workspace_st_ino",
        "workspace_mount_id": "workspace_mount_id",
    }
    for manifest_key, expected_key in mappings.items():
        expected_value = expected.get(expected_key, expected.get(manifest_key))
        if expected_value is not None and invariant.get(manifest_key) != expected_value:
            return False
    return True


def _result_evidence_matches(
    attempt: dict[str, Any],
    result: dict[str, Any],
    blockade: _BlockadeObservation,
    manifest: MutationManifestSnapshot | None,
) -> bool:
    if manifest is None:
        return False
    final_sequence = int(result["final_manifest_sequence"])
    if (
        final_sequence < 0
        or final_sequence >= len(manifest.record_hashes)
        or manifest.record_hashes[final_sequence] != result["final_manifest_sha256"]
        or manifest.outcome_record != result["outcome"]
        or manifest.outcome_sequence != final_sequence
        or manifest.outcome_sha256 != result["final_manifest_sha256"]
    ):
        return False
    state = str(attempt["state"])
    if state in _TERMINAL_STATES:
        expected = "filesystem_succeeded" if state == "succeeded" else state
        if result["outcome"] != expected:
            return False
    if blockade.state is MutationBlockadeState.EXACT_REMOVAL_READY:
        value = blockade.value
        if value is None:
            return False
        return bool(
            value.get("attempt_public_id") == attempt["public_id"]
            and value.get("result_public_id") == result["public_id"]
            and value.get("result_outcome") == result["outcome"]
            and value.get("result_final_manifest_sequence") == final_sequence
            and value.get("result_final_manifest_sha256") == result["final_manifest_sha256"]
            and value.get("cleanup_manifest_sequence") == manifest.sequence
            and value.get("cleanup_manifest_tail_sha256") == manifest.tail_sha256
            and value.get("removal_ready") is True
        )
    return True


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _read_bounded(fd: int, maximum: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(65_536, maximum + 1 - total))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > maximum:
            raise MutationRecoveryError("Recovery artifact excede límite.")
        chunks.append(chunk)


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


def _is_enoent(exc: LinuxFilesystemError) -> bool:
    cause = exc.__cause__
    return isinstance(cause, OSError) and cause.errno == errno.ENOENT


def _bounded_text(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and "\x00" not in value
        and len(value) <= maximum
    )


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None
