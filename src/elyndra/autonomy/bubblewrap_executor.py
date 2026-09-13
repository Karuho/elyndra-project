from __future__ import annotations

import fcntl
import hashlib
import os
import re
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from elyndra.autonomy.capabilities import Capability
from elyndra.autonomy.commands import (
    CommandEnvironmentProfile,
    CommandSandboxProfile,
    CommandSnapshot,
    CommandStdinPolicy,
)
from elyndra.autonomy.execution import (
    CancellationToken,
    ExecutionDenied,
    ExecutionOutcome,
    ExecutionResult,
    PreparedExecution,
)
from elyndra.autonomy.repository import AutonomyRepository
from elyndra.autonomy.scope import WorkspaceScope
from elyndra.autonomy.workspace_lease import WorkspaceLeaseMode

_SANDBOX_EXECUTABLE = "/run/elyndra/executable"
_ANSI_ESCAPE = re.compile(
    rb"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
)

_HOST_ENVIRONMENT = {
    "PATH": "/usr/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "NO_COLOR": "1",
    "TERM": "dumb",
}

_SANDBOX_ENVIRONMENT = {
    "PATH": "/usr/bin",
    "HOME": "/tmp",
    "TMPDIR": "/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "NO_COLOR": "1",
    "TERM": "dumb",
    "PYTHONNOUSERSITE": "1",
    "PIP_NO_INDEX": "1",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "COMPOSER_NO_INTERACTION": "1",
    "COMPOSER_DISABLE_NETWORK": "1",
}


@dataclass(slots=True)
class _TailCollector:
    stream: BinaryIO
    limit: int
    data: bytearray
    truncated: bool = False
    error: BaseException | None = None

    @classmethod
    def create(
        cls,
        stream: BinaryIO,
        limit: int,
    ) -> _TailCollector:
        return cls(
            stream=stream,
            limit=limit,
            data=bytearray(),
        )

    def collect(self) -> None:
        try:
            while True:
                chunk = self.stream.read(64 * 1024)
                if not chunk:
                    break

                if len(chunk) >= self.limit:
                    self.data[:] = chunk[-self.limit :]
                    self.truncated = True
                    continue

                self.data.extend(chunk)

                overflow = len(self.data) - self.limit
                if overflow > 0:
                    del self.data[:overflow]
                    self.truncated = True
        except BaseException as exc:
            self.error = exc

    def text(self) -> str:
        clean = _ANSI_ESCAPE.sub(b"", bytes(self.data))
        clean = clean.replace(b"\x00", b"")
        return _sanitized_utf8_tail(clean, self.limit)


class BubblewrapExecutor:
    """
    Execute one already-authorized PROCESS_EXEC request in Bubblewrap.

    The executor cannot create authority. It requires:
    - an existing durable reservation;
    - an exact persisted CommandSnapshot;
    - a still-running/granted autonomy run;
    - a sealed copy of the executable;
    - Bubblewrap isolation with no host network.
    """

    def __init__(
        self,
        repository: AutonomyRepository,
        *,
        actor: str,
    ) -> None:
        if not isinstance(
            repository,
            AutonomyRepository,
        ):
            raise TypeError(
                "repository debe ser AutonomyRepository."
            )

        clean_actor = actor.strip()
        if not clean_actor:
            raise ValueError("actor no puede estar vacío.")
        if len(clean_actor) > 200:
            raise ValueError("actor supera 200 caracteres.")

        self.repository = repository
        self.actor = clean_actor
        self.bwrap_path = _trusted_bwrap_path()

    def execute(
        self,
        prepared: PreparedExecution,
        *,
        cancellation: CancellationToken,
    ) -> ExecutionResult:
        if not isinstance(prepared, PreparedExecution):
            raise TypeError("prepared debe ser PreparedExecution.")
        session = prepared.workspace_session
        try:
            session.require_live()
            return self._execute_with_session(prepared, cancellation=cancellation)
        finally:
            session.close()

    def _execute_with_session(
        self,
        prepared: PreparedExecution,
        *,
        cancellation: CancellationToken,
    ) -> ExecutionResult:
        if not isinstance(
            prepared,
            PreparedExecution,
        ):
            raise TypeError(
                "prepared debe ser PreparedExecution."
            )

        if not isinstance(
            cancellation,
            CancellationToken,
        ):
            raise TypeError(
                "cancellation debe ser CancellationToken."
            )

        cancellation.require_active()
        lease_receipt = prepared.workspace_session.receipt
        lease_receipt.require_live(mode=WorkspaceLeaseMode.SHARED)

        request = prepared.request

        if request.capability is not Capability.PROCESS_EXEC:
            raise ExecutionDenied(
                "BubblewrapExecutor solo acepta process.exec."
            )

        snapshot = prepared.command_snapshot

        if not isinstance(
            snapshot,
            CommandSnapshot,
        ):
            raise ExecutionDenied(
                "Falta CommandSnapshot autorizado."
            )

        spec = snapshot.spec

        if (
            spec.sandbox_profile
            is not
            CommandSandboxProfile.WORKSPACE_RW_NO_NETWORK_V1
        ):
            raise ExecutionDenied(
                "Sandbox profile no soportado."
            )

        if (
            spec.environment_profile
            is not CommandEnvironmentProfile.MINIMAL_V1
        ):
            raise ExecutionDenied(
                "Environment profile no soportado."
            )

        if (
            spec.stdin_policy
            is not CommandStdinPolicy.DEVNULL
        ):
            raise ExecutionDenied(
                "stdin policy no soportada."
            )

        if (
            prepared.reserved_runtime_seconds
            != spec.timeout_seconds
        ):
            raise ExecutionDenied(
                "La reserva runtime no coincide "
                "con CommandSpec.timeout_seconds."
            )

        cancellation.require_active()

        # Primero exige que la reserva YA exista.
        self.repository.verify_execution_reservation(
            request,
            actor=self.actor,
            runtime_seconds=prepared.reserved_runtime_seconds,
            retry=prepared.retry,
        )

        cancellation.require_active()

        item = self.repository.get(request.run_id)
        if item is None:
            raise ExecutionDenied(
                "AutonomyRun desapareció antes del launch."
            )

        if item.get("actor") != self.actor:
            raise ExecutionDenied(
                "Actor inconsistente antes del launch."
            )

        try:
            workspace = WorkspaceScope.from_root(
                str(item["workspace_root"])
            )
            lease_receipt.require_workspace(workspace.root)
            current_cwd = workspace.resolve(
                spec.cwd,
                must_exist=True,
            )
        except (
            KeyError,
            OSError,
            PermissionError,
            ValueError,
        ) as exc:
            raise ExecutionDenied(
                "Workspace/cwd dejó de ser válido "
                "antes del launch."
            ) from exc

        if str(current_cwd) != snapshot.resolved_cwd:
            raise ExecutionDenied(
                "cwd cambió desde CommandSnapshot."
            )

        try:
            snapshot.revalidate()
        except (
            OSError,
            PermissionError,
            ValueError,
        ) as exc:
            raise ExecutionDenied(
                "CommandSnapshot cambió antes del launch."
            ) from exc

        cancellation.require_active()

        with _sealed_executable(snapshot) as executable_fd:
            cancellation.require_active()

            argv = self._build_bwrap_argv(
                snapshot=snapshot,
                workspace=workspace,
                executable_fd=executable_fd,
                journal_mask=lease_receipt.journal_mask_path,
            )

            cancellation.require_active()

            # Última frontera durable antes de producir efectos:
            # un request_id puede iniciar como máximo un proceso.
            receipt = self.repository._claim_execution_launch(
                request,
                actor=self.actor,
                runtime_seconds=prepared.reserved_runtime_seconds,
                retry=prepared.retry,
                workspace_lease_receipt=lease_receipt,
            )

            if cancellation.cancelled:
                result = ExecutionResult(
                    request_id=request.request_id,
                    outcome=ExecutionOutcome.CANCELLED,
                    summary=(
                        "Proceso sandboxed cancelado "
                        "antes del launch."
                    ),
                    exit_code=None,
                    duration_ms=0,
                    error_code="cancelled",
                )
            else:
                result = self._run(
                    argv,
                    prepared=prepared,
                    cancellation=cancellation,
                    pass_fd=executable_fd,
                )

            self.repository._record_execution_result(
                request,
                result,
                actor=self.actor,
                receipt=receipt,
            )

            return result

    def _build_bwrap_argv(
        self,
        *,
        snapshot: CommandSnapshot,
        workspace: WorkspaceScope,
        executable_fd: int,
        journal_mask: Path,
    ) -> tuple[str, ...]:
        spec = snapshot.spec

        argv: list[str] = [
            self.bwrap_path,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--clearenv",
            "--cap-drop",
            "ALL",
            "--ro-bind",
            "/usr",
            "/usr",
        ]

        for system_path in (
            "/lib",
            "/lib64",
        ):
            if Path(system_path).exists():
                argv.extend(
                    (
                        "--ro-bind",
                        system_path,
                        system_path,
                    )
                )

        argv.extend(
            (
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--dir",
                "/run",
                "--dir",
                "/run/elyndra",
                "--perms",
                "0555",
                "--ro-bind-data",
                str(executable_fd),
                _SANDBOX_EXECUTABLE,
                "--bind",
                str(workspace.root),
                str(workspace.root),
                "--ro-bind",
                str(journal_mask),
                str(workspace.root / ".elyndra-mutation-journal"),
            )
        )

        for key, value in _SANDBOX_ENVIRONMENT.items():
            argv.extend(
                (
                    "--setenv",
                    key,
                    value,
                )
            )

        argv.extend(
            (
                "--chdir",
                snapshot.resolved_cwd,
                "--argv0",
                spec.argv[0],
                "--",
                _SANDBOX_EXECUTABLE,
                *spec.argv[1:],
            )
        )

        return tuple(argv)

    def _run(
        self,
        argv: tuple[str, ...],
        *,
        prepared: PreparedExecution,
        cancellation: CancellationToken,
        pass_fd: int,
    ) -> ExecutionResult:
        spec = prepared.command_snapshot
        if spec is None:
            raise ExecutionDenied(
                "Falta CommandSnapshot."
            )

        command = spec.spec
        started = time.perf_counter()

        try:
            process = subprocess.Popen(  # noqa: S603
                argv,
                env=dict(_HOST_ENVIRONMENT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
                pass_fds=(pass_fd,),
            )
        except OSError as exc:
            return ExecutionResult(
                request_id=prepared.request.request_id,
                outcome=ExecutionOutcome.FAILED,
                summary=(
                    "No se pudo iniciar Bubblewrap."
                ),
                exit_code=None,
                duration_ms=round(
                    (
                        time.perf_counter()
                        - started
                    )
                    * 1000
                ),
                error_code="sandbox_launch_failed",
                stderr=_sanitized_utf8_tail(
                    str(exc).encode(
                        "utf-8",
                        errors="replace",
                    ),
                    command.stderr_limit_bytes,
                ),
            )

        assert process.stdout is not None
        assert process.stderr is not None

        stdout_collector = _TailCollector.create(
            process.stdout,
            command.stdout_limit_bytes,
        )
        stderr_collector = _TailCollector.create(
            process.stderr,
            command.stderr_limit_bytes,
        )

        stdout_thread = threading.Thread(
            target=stdout_collector.collect,
            name="elyndra-bwrap-stdout",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=stderr_collector.collect,
            name="elyndra-bwrap-stderr",
            daemon=True,
        )

        stdout_thread.start()
        stderr_thread.start()

        deadline = (
            time.monotonic()
            + command.timeout_seconds
        )

        timed_out = False
        cancelled = False

        while process.poll() is None:
            if cancellation.cancelled:
                cancelled = True
                _terminate_process(process)
                break

            if time.monotonic() >= deadline:
                timed_out = True
                _terminate_process(process)
                break

            time.sleep(0.02)

        if process.poll() is None:
            _terminate_process(process)

        try:
            returncode = process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            _terminate_process(
                process,
                force=True,
            )
            returncode = process.wait(timeout=1)

        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)

        if stdout_thread.is_alive():
            raise RuntimeError(
                "stdout collector no terminó."
            )

        if stderr_thread.is_alive():
            raise RuntimeError(
                "stderr collector no terminó."
            )

        if stdout_collector.error is not None:
            raise RuntimeError(
                "Falló stdout collector."
            ) from stdout_collector.error

        if stderr_collector.error is not None:
            raise RuntimeError(
                "Falló stderr collector."
            ) from stderr_collector.error

        duration_ms = round(
            (
                time.perf_counter()
                - started
            )
            * 1000
        )

        stdout = stdout_collector.text()
        stderr = stderr_collector.text()

        if cancelled:
            outcome = ExecutionOutcome.CANCELLED
            error_code = "cancelled"
            summary = (
                "Proceso sandboxed cancelado."
            )
        elif timed_out:
            outcome = ExecutionOutcome.FAILED
            error_code = "process_timeout"
            summary = (
                "Proceso sandboxed excedió timeout."
            )
        elif returncode == 0:
            outcome = ExecutionOutcome.SUCCEEDED
            error_code = ""
            summary = (
                "Proceso sandboxed completado."
            )
        else:
            outcome = ExecutionOutcome.FAILED
            error_code = "process_exit_nonzero"
            summary = (
                "Proceso sandboxed terminó "
                "con exit code no cero."
            )

        return ExecutionResult(
            request_id=prepared.request.request_id,
            outcome=outcome,
            summary=summary,
            exit_code=returncode,
            duration_ms=duration_ms,
            error_code=error_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            stdout_truncated=(
                stdout_collector.truncated
            ),
            stderr_truncated=(
                stderr_collector.truncated
            ),
        )


def _sanitized_utf8_tail(
    value: bytes,
    limit: int,
) -> str:
    sanitized = value.decode(
        "utf-8",
        errors="replace",
    ).strip()
    encoded = sanitized.encode("utf-8")

    if len(encoded) <= limit:
        return sanitized

    clipped = encoded[-limit:]
    while clipped and clipped[0] & 0xC0 == 0x80:
        clipped = clipped[1:]
    return clipped.decode("utf-8")


def _trusted_bwrap_path() -> str:
    requested = Path("/usr/bin/bwrap")

    if requested.is_symlink():
        raise RuntimeError(
            "Bubblewrap no puede ser un symlink."
        )

    try:
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(
            "Bubblewrap no está disponible."
        ) from exc

    if str(resolved) != str(requested):
        raise RuntimeError(
            "Bubblewrap debe usar /usr/bin/bwrap exacto."
        )

    try:
        metadata = resolved.stat()
    except OSError as exc:
        raise RuntimeError(
            "No se pudo inspeccionar Bubblewrap."
        ) from exc

    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(
            "Bubblewrap debe ser archivo regular."
        )

    if metadata.st_uid != 0:
        raise RuntimeError(
            "Bubblewrap debe pertenecer a root."
        )

    if metadata.st_mode & (
        stat.S_ISUID
        | stat.S_ISGID
        | stat.S_IWGRP
        | stat.S_IWOTH
    ):
        raise RuntimeError(
            "Bubblewrap tiene permisos inseguros."
        )

    if not os.access(resolved, os.X_OK):
        raise RuntimeError(
            "Bubblewrap no es ejecutable."
        )

    return str(resolved)


@contextmanager
def _sealed_executable(
    snapshot: CommandSnapshot,
) -> Iterator[int]:
    executable = snapshot.executable

    flags = os.O_RDONLY | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)

    source_fd = -1
    memfd = -1

    try:
        source_fd = os.open(
            executable.canonical_path,
            flags,
        )

        before = os.fstat(source_fd)

        if not _metadata_matches(
            before,
            snapshot,
        ):
            raise ExecutionDenied(
                "Identidad del ejecutable cambió "
                "antes del open estable."
            )

        required_os = (
            "memfd_create",
            "MFD_ALLOW_SEALING",
        )
        required_fcntl = (
            "F_ADD_SEALS",
            "F_GET_SEALS",
            "F_SEAL_SEAL",
            "F_SEAL_SHRINK",
            "F_SEAL_GROW",
            "F_SEAL_WRITE",
        )

        if not all(
            hasattr(os, name)
            for name in required_os
        ):
            raise ExecutionDenied(
                "Host sin memfd sealing."
            )

        if not all(
            hasattr(fcntl, name)
            for name in required_fcntl
        ):
            raise ExecutionDenied(
                "Host sin fcntl sealing."
            )

        flags_memfd = (
            os.MFD_ALLOW_SEALING
            | getattr(os, "MFD_CLOEXEC", 0)
        )

        memfd = os.memfd_create(
            "elyndra-autonomy-executable",
            flags=flags_memfd,
        )

        digest = hashlib.sha256()

        while True:
            chunk = os.read(
                source_fd,
                1024 * 1024,
            )
            if not chunk:
                break

            digest.update(chunk)
            _write_all(memfd, chunk)

        after = os.fstat(source_fd)

        if _stat_identity(before) != _stat_identity(after):
            raise ExecutionDenied(
                "Ejecutable cambió durante "
                "la copia estable."
            )

        if (
            digest.hexdigest()
            != executable.sha256
        ):
            raise ExecutionDenied(
                "SHA-256 del ejecutable cambió."
            )

        os.lseek(
            memfd,
            0,
            os.SEEK_SET,
        )

        seals = (
            fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_WRITE
            | fcntl.F_SEAL_SEAL
        )

        fcntl.fcntl(
            memfd,
            fcntl.F_ADD_SEALS,
            seals,
        )

        actual_seals = fcntl.fcntl(
            memfd,
            fcntl.F_GET_SEALS,
        )

        if actual_seals != seals:
            raise ExecutionDenied(
                "memfd no quedó completamente sellado."
            )

        yield memfd

    finally:
        if source_fd >= 0:
            os.close(source_fd)

        if memfd >= 0:
            os.close(memfd)


def _metadata_matches(
    metadata: os.stat_result,
    snapshot: CommandSnapshot,
) -> bool:
    executable = snapshot.executable

    return (
        metadata.st_dev == executable.device
        and metadata.st_ino == executable.inode
        and metadata.st_mode == executable.mode
        and metadata.st_size == executable.size
        and metadata.st_mtime_ns
        == executable.mtime_ns
        and metadata.st_ctime_ns
        == executable.ctime_ns
    )


def _stat_identity(
    metadata: os.stat_result,
) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _write_all(
    fd: int,
    data: bytes,
) -> None:
    view = memoryview(data)

    while view:
        written = os.write(fd, view)

        if written <= 0:
            raise OSError(
                "No se pudo escribir sealed memfd."
            )

        view = view[written:]


def _terminate_process(
    process: subprocess.Popen[bytes],
    *,
    force: bool = False,
) -> None:
    signal_to_send = (
        signal.SIGKILL
        if force
        else signal.SIGTERM
    )

    try:
        os.killpg(
            process.pid,
            signal_to_send,
        )
    except ProcessLookupError:
        return

    if force:
        return

    try:
        process.wait(timeout=0.5)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(
            process.pid,
            signal.SIGKILL,
        )
    except ProcessLookupError:
        return
