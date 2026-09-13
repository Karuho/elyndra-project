"""Cross-process workspace coordination outside autonomous workspaces."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import secrets
import stat
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Protocol

from elyndra.autonomy.linux_fs import (
    LinuxFilesystemError,
    openat2,
    statx_fd,
    validate_metadata,
)

JOURNAL_NAME = ".elyndra-mutation-journal"
BLOCKADE_NAME = "blockade.json"
_MAX_BLOCKADE_BYTES = 16_384
_LEASE_DOMAIN = b"elyndra.workspace-lease.v1\0"
_FACTORY = object()
_TEST_COORDINATOR_FACTORY = object()


class WorkspaceCoordinationError(PermissionError):
    """Trusted workspace coordination could not be established."""


class WorkspaceLeaseTimeout(WorkspaceCoordinationError):
    """The bounded lease acquisition window elapsed."""


class WorkspaceBlockedError(WorkspaceCoordinationError):
    """A durable mutation blockade forbids process execution."""


class _Cancellation(Protocol):
    @property
    def cancelled(self) -> bool: ...

    def require_active(self) -> None: ...


@dataclass(frozen=True, slots=True)
class WorkspaceIdentity:
    canonical_root: str
    st_dev: int
    st_ino: int
    mount_id: int


class WorkspaceLeaseMode(StrEnum):
    SHARED = "shared"
    EXCLUSIVE = "exclusive"


class WorkspaceLeaseReceipt:
    """Opaque process-local proof backed by a currently live flock."""

    __slots__ = ("_lease", "_token")

    def __init__(self, lease: WorkspaceLease, token: bytes, factory: object) -> None:
        if factory is not _FACTORY:
            raise TypeError("WorkspaceLeaseReceipt no puede construirse públicamente.")
        self._lease = lease
        self._token = token

    @property
    def identity(self) -> WorkspaceIdentity:
        return self._lease.identity

    @property
    def mode(self) -> WorkspaceLeaseMode:
        return self._lease.mode

    @property
    def journal_mask_path(self) -> Path:
        return self._lease.mask_path

    def require_live(
        self,
        *,
        mode: WorkspaceLeaseMode,
        identity: WorkspaceIdentity | None = None,
    ) -> None:
        self._lease._require_receipt(self._token, mode=mode, identity=identity)

    def require_workspace(self, root: Path | str) -> None:
        current = self._lease._coordinator.identity(root)
        self.require_live(mode=self.mode, identity=current)


class WorkspaceLease:
    """An acquired flock whose descriptor lifetime is the lease lifetime."""

    __slots__ = (
        "_coordinator",
        "_fd",
        "_token",
        "identity",
        "mask_path",
        "mode",
        "receipt",
    )

    def __init__(
        self,
        *,
        fd: int,
        identity: WorkspaceIdentity,
        mode: WorkspaceLeaseMode,
        mask_path: Path,
        coordinator: WorkspaceLeaseCoordinator,
        factory: object,
    ) -> None:
        if factory is not _FACTORY:
            raise TypeError("WorkspaceLease no puede construirse públicamente.")
        self._fd = fd
        self._token = secrets.token_bytes(32)
        self.identity = identity
        self.mode = mode
        self.mask_path = mask_path
        coordinator._live[self._token] = self
        self._coordinator = coordinator
        self.receipt = WorkspaceLeaseReceipt(self, self._token, _FACTORY)

    @property
    def closed(self) -> bool:
        return self._fd < 0

    def _require_receipt(
        self,
        token: bytes,
        *,
        mode: WorkspaceLeaseMode,
        identity: WorkspaceIdentity | None,
    ) -> None:
        if (
            not secrets.compare_digest(token, self._token)
            or self.closed
            or self._coordinator._live.get(self._token) is not self
        ):
            raise WorkspaceCoordinationError("Receipt de workspace no está vivo.")
        if self.mode is not mode:
            raise WorkspaceCoordinationError("Modo de lease incompatible.")
        if identity is not None and self.identity != identity:
            raise WorkspaceCoordinationError("WorkspaceIdentity incompatible.")
        try:
            fcntl.fcntl(self._fd, fcntl.F_GETFD)
        except OSError as exc:
            raise WorkspaceCoordinationError("Descriptor de lease cerrado.") from exc

    def close(self) -> None:
        if getattr(self, "_fd", -1) >= 0:
            fd, self._fd = self._fd, -1
            self._coordinator._live.pop(self._token, None)
            os.close(fd)

    def __enter__(self) -> WorkspaceLease:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()


class WorkspaceExecutionSession:
    """Live shared lease and verified empty journal mask for one execution."""

    __slots__ = ("_lease",)

    def __init__(self, lease: WorkspaceLease) -> None:
        if lease.mode is not WorkspaceLeaseMode.SHARED:
            raise WorkspaceCoordinationError("Execution session requiere lease shared.")
        self._lease = lease

    @property
    def receipt(self) -> WorkspaceLeaseReceipt:
        return self._lease.receipt

    @property
    def identity(self) -> WorkspaceIdentity:
        return self._lease.identity

    @property
    def journal_mask_path(self) -> Path:
        return self._lease.mask_path

    def close(self) -> None:
        self._lease.close()

    def require_live(self, identity: WorkspaceIdentity | None = None) -> None:
        self.receipt.require_live(mode=WorkspaceLeaseMode.SHARED, identity=identity)

    def __enter__(self) -> WorkspaceExecutionSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class WorkspaceLeaseCoordinator:
    """Resolve trusted runtime storage and coordinate physical workspaces."""

    def __init__(
        self,
        *,
        _runtime_root: Path | None = None,
        _factory: object | None = None,
    ) -> None:
        injected = _factory is _TEST_COORDINATOR_FACTORY
        if (_runtime_root is None) != (not injected):
            raise TypeError("Runtime root alternativo reservado para tests internos.")
        root = _runtime_root if injected else _production_runtime_root()
        if root is None:
            raise TypeError("Runtime root de test requerido.")
        self.runtime_root = _verify_runtime_root(root, verify_ancestors=not injected)
        self._live: dict[bytes, WorkspaceLease] = {}
        self._elyndra_dir = _ensure_private_directory(self.runtime_root, "elyndra")
        self._lease_dir = _ensure_private_directory(self._elyndra_dir, "workspace-leases")
        self.empty_mask_path = _ensure_private_directory(self._elyndra_dir, "empty-journal-mask")
        for path in (self._elyndra_dir, self._lease_dir, self.empty_mask_path):
            _validate_private_directory(path)

    @classmethod
    def _for_test(cls, runtime_root: Path) -> WorkspaceLeaseCoordinator:
        """Build an explicitly test-only coordinator outside production lookup."""
        return cls(_runtime_root=runtime_root, _factory=_TEST_COORDINATOR_FACTORY)

    def identity(self, root: Path | str) -> WorkspaceIdentity:
        supplied = Path(root)
        try:
            canonical = supplied.resolve(strict=True)
        except OSError as exc:
            raise WorkspaceCoordinationError("Workspace no puede resolverse.") from exc
        fd = os.open(canonical, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            metadata = statx_fd(fd)
        finally:
            os.close(fd)
        if not stat.S_ISDIR(metadata.mode):
            raise WorkspaceCoordinationError("Workspace no es directorio.")
        return WorkspaceIdentity(
            canonical_root=str(canonical),
            st_dev=metadata.device,
            st_ino=metadata.inode,
            mount_id=metadata.mount_id,
        )

    def acquire(
        self,
        identity: WorkspaceIdentity,
        mode: WorkspaceLeaseMode,
        *,
        cancellation: _Cancellation | None = None,
        timeout_seconds: float = 10.0,
        poll_seconds: float = 0.02,
    ) -> WorkspaceLease:
        if timeout_seconds < 0 or poll_seconds <= 0:
            raise ValueError("Timeout/poll de lease inválido.")
        current = self.identity(identity.canonical_root)
        if current != identity:
            raise WorkspaceCoordinationError("WorkspaceIdentity cambió antes del lease.")
        key = _lease_key(identity)
        directory_fd = os.open(
            self._lease_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        )
        try:
            try:
                fd = os.open(
                    key + ".lock",
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_CLOEXEC
                    | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory_fd,
                )
                os.fchmod(fd, 0o600)
            except FileExistsError:
                fd = os.open(
                    key + ".lock",
                    os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
        finally:
            os.close(directory_fd)
        try:
            metadata = validate_metadata(fd, directory=False)
            if (
                metadata.uid != os.geteuid()
                or metadata.nlink != 1
                or stat.S_IMODE(metadata.mode) != 0o600
            ):
                raise WorkspaceCoordinationError("Lock file no es confiable.")
            operation = fcntl.LOCK_SH if mode is WorkspaceLeaseMode.SHARED else fcntl.LOCK_EX
            deadline = time.monotonic() + timeout_seconds
            while True:
                if cancellation is not None:
                    cancellation.require_active()
                try:
                    fcntl.flock(fd, operation | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise WorkspaceLeaseTimeout("Timeout adquiriendo workspace lease.") from exc
                    time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
            return WorkspaceLease(
                fd=fd,
                identity=identity,
                mode=mode,
                mask_path=self.empty_mask_path,
                coordinator=self,
                factory=_FACTORY,
            )
        except BaseException:
            os.close(fd)
            raise

    def execution_session(
        self,
        root: Path | str,
        *,
        cancellation: _Cancellation | None = None,
        timeout_seconds: float = 10.0,
    ) -> WorkspaceExecutionSession:
        identity = self.identity(root)
        self._bootstrap_journal(
            identity, cancellation=cancellation, timeout_seconds=timeout_seconds
        )
        lease = self.acquire(
            identity,
            WorkspaceLeaseMode.SHARED,
            cancellation=cancellation,
            timeout_seconds=timeout_seconds,
        )
        try:
            if self.identity(root) != identity:
                raise WorkspaceCoordinationError("Workspace cambió tras adquirir lease.")
            self._validate_journal_and_blockade(identity)
            return WorkspaceExecutionSession(lease)
        except BaseException:
            lease.close()
            raise

    def _bootstrap_journal(
        self,
        identity: WorkspaceIdentity,
        *,
        cancellation: _Cancellation | None,
        timeout_seconds: float,
    ) -> None:
        root_fd = _open_workspace(identity)
        try:
            try:
                journal_fd = openat2(
                    root_fd, JOURNAL_NAME, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
                )
            except LinuxFilesystemError as exc:
                if not _is_missing(exc):
                    raise WorkspaceCoordinationError("Journal inseguro.") from exc
            else:
                os.close(journal_fd)
                self._validate_journal(identity)
                return
        finally:
            os.close(root_fd)
        with self.acquire(
            identity,
            WorkspaceLeaseMode.EXCLUSIVE,
            cancellation=cancellation,
            timeout_seconds=timeout_seconds,
        ):
            root_fd = _open_workspace(identity)
            try:
                try:
                    os.mkdir(JOURNAL_NAME, 0o700, dir_fd=root_fd)
                    os.chmod(
                        JOURNAL_NAME,
                        0o700,
                        dir_fd=root_fd,
                        follow_symlinks=False,
                    )
                    os.fsync(root_fd)
                except FileExistsError:
                    pass
            finally:
                os.close(root_fd)
            self._validate_journal(identity)

    def _validate_journal(self, identity: WorkspaceIdentity) -> None:
        root_fd = _open_workspace(identity)
        try:
            fd = openat2(root_fd, JOURNAL_NAME, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                metadata = validate_metadata(fd, directory=True)
                if (
                    metadata.uid != os.geteuid()
                    or stat.S_IMODE(metadata.mode) != 0o700
                    or metadata.mount_id != identity.mount_id
                ):
                    raise WorkspaceCoordinationError("Journal no cumple ownership/mode/mount.")
            finally:
                os.close(fd)
        finally:
            os.close(root_fd)

    def _validate_journal_and_blockade(self, identity: WorkspaceIdentity) -> None:
        self._validate_journal(identity)
        root_fd = _open_workspace(identity)
        try:
            journal_fd = openat2(root_fd, JOURNAL_NAME, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                try:
                    blockade_fd = openat2(journal_fd, BLOCKADE_NAME, os.O_RDONLY | os.O_CLOEXEC)
                except LinuxFilesystemError as exc:
                    if _is_missing(exc):
                        return
                    raise WorkspaceBlockedError("Blockade ilegible o ambiguo.") from exc
                try:
                    validate_metadata(blockade_fd, directory=False)
                    payload = os.read(blockade_fd, _MAX_BLOCKADE_BYTES + 1)
                    if len(payload) > _MAX_BLOCKADE_BYTES:
                        raise WorkspaceBlockedError("Blockade excede límite.")
                    try:
                        value = json.loads(payload.decode("utf-8", errors="strict"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise WorkspaceBlockedError("Blockade malformado.") from exc
                    if not isinstance(value, dict) or value.get("format_version") != "v1":
                        raise WorkspaceBlockedError("Blockade no soportado.")
                    raise WorkspaceBlockedError("Workspace bloqueado por mutación durable.")
                finally:
                    os.close(blockade_fd)
            finally:
                os.close(journal_fd)
        finally:
            os.close(root_fd)


def _production_runtime_root() -> Path:
    configured = os.environ.get("XDG_RUNTIME_DIR")
    if configured is not None:
        if not configured:
            raise WorkspaceCoordinationError("XDG_RUNTIME_DIR configurado pero vacío.")
        return Path(configured)
    fallback = Path("/run/user") / str(os.geteuid())
    if not fallback.exists():
        raise WorkspaceCoordinationError("No existe un runtime root confiable.")
    return fallback


def _verify_runtime_root(path: Path, *, verify_ancestors: bool) -> Path:
    if not path.is_absolute():
        raise WorkspaceCoordinationError("Runtime root debe ser absoluto.")
    parts = path.parts[1:]
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for index, component in enumerate(parts):
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=fd,
            )
            os.close(fd)
            fd = child
            metadata = os.fstat(fd)
            if verify_ancestors and stat.S_IMODE(metadata.st_mode) & 0o022:
                raise WorkspaceCoordinationError("Ancestro runtime escribible no confiable.")
            if index == len(parts) - 1 and (
                metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise WorkspaceCoordinationError("Runtime root requiere owner actual y modo 0700.")
        resolved = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if resolved != path:
            raise WorkspaceCoordinationError("Runtime root no es canónico.")
        return resolved
    except OSError as exc:
        raise WorkspaceCoordinationError("Runtime root no es confiable.") from exc
    finally:
        os.close(fd)


def _ensure_private_directory(parent: Path, name: str) -> Path:
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        created = False
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            pass
        if created:
            os.chmod(name, 0o700, dir_fd=parent_fd, follow_symlinks=False)
        fd = os.open(
            name, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=parent_fd
        )
        try:
            metadata = os.fstat(fd)
            if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
                raise WorkspaceCoordinationError("Directorio runtime Elyndra inseguro.")
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)
    return parent / name


def _validate_private_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = validate_metadata(fd, directory=True)
        if metadata.uid != os.geteuid() or stat.S_IMODE(metadata.mode) != 0o700:
            raise WorkspaceCoordinationError("Directorio privado inseguro.")
    finally:
        os.close(fd)


def _lease_key(identity: WorkspaceIdentity) -> str:
    payload = (
        _LEASE_DOMAIN
        + identity.canonical_root.encode("utf-8", errors="strict")
        + b"\0"
        + str(identity.st_dev).encode("ascii")
        + b"\0"
        + str(identity.st_ino).encode("ascii")
    )
    return hashlib.sha256(payload).hexdigest()


def _open_workspace(identity: WorkspaceIdentity) -> int:
    fd = os.open(
        identity.canonical_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    metadata = statx_fd(fd)
    current = (metadata.device, metadata.inode, metadata.mount_id)
    expected = (identity.st_dev, identity.st_ino, identity.mount_id)
    if current != expected:
        os.close(fd)
        raise WorkspaceCoordinationError("WorkspaceIdentity cambió.")
    return fd


def _is_missing(exc: LinuxFilesystemError) -> bool:
    cause = exc.__cause__
    return isinstance(cause, OSError) and cause.errno == errno.ENOENT
