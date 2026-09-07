from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

_MAX_EXECUTABLE_CHARS = 4_096
_MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
_MAX_ARG_CHARS = 4_096
_MAX_ARGV_ITEMS = 128
_MAX_ARGV_UTF8_BYTES = 32_768
_MAX_CWD_CHARS = 1_024
_MAX_COMMAND_TIMEOUT_SECONDS = 900
_MAX_OUTPUT_BYTES = 1_048_576
_DEFAULT_OUTPUT_BYTES = 262_144

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_COMMAND_SPEC_KEYS = frozenset(
    {
        "executable",
        "argv",
        "cwd",
        "sandbox_profile",
        "environment_profile",
        "stdin_policy",
        "timeout_seconds",
        "stdout_limit_bytes",
        "stderr_limit_bytes",
    }
)


class CommandSandboxProfile(StrEnum):
    """Isolation contract. Phase 6 defines it but executes nothing."""

    WORKSPACE_RW_NO_NETWORK_V1 = "workspace_rw_no_network_v1"


class CommandEnvironmentProfile(StrEnum):
    """Deterministic environment contract without arbitrary persisted secrets."""

    MINIMAL_V1 = "minimal_v1"


class CommandStdinPolicy(StrEnum):
    DEVNULL = "devnull"


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """
    Frozen argv-only command declaration.

    This object does not execute anything and performs no executable lookup.
    """

    executable: str
    argv: tuple[str, ...]
    cwd: str = "."
    sandbox_profile: CommandSandboxProfile = (
        CommandSandboxProfile.WORKSPACE_RW_NO_NETWORK_V1
    )
    environment_profile: CommandEnvironmentProfile = (
        CommandEnvironmentProfile.MINIMAL_V1
    )
    stdin_policy: CommandStdinPolicy = CommandStdinPolicy.DEVNULL
    timeout_seconds: int = 120
    stdout_limit_bytes: int = _DEFAULT_OUTPUT_BYTES
    stderr_limit_bytes: int = _DEFAULT_OUTPUT_BYTES

    def __post_init__(self) -> None:
        executable = _exact_absolute_path(
            self.executable,
            label="executable",
            maximum=_MAX_EXECUTABLE_CHARS,
        )

        if isinstance(self.argv, (str, bytes)):
            raise TypeError("argv debe ser una secuencia de argumentos, no texto.")

        argv = tuple(self.argv)
        if not argv:
            raise ValueError("argv requiere al menos argv[0].")
        if len(argv) > _MAX_ARGV_ITEMS:
            raise ValueError(
                f"argv supera el máximo de {_MAX_ARGV_ITEMS} argumentos."
            )

        total_bytes = 0
        normalized_argv: list[str] = []

        for index, value in enumerate(argv):
            if not isinstance(value, str):
                raise TypeError(f"argv[{index}] debe ser texto.")
            if "\x00" in value:
                raise ValueError(f"argv[{index}] contiene NUL.")
            if len(value) > _MAX_ARG_CHARS:
                raise ValueError(
                    f"argv[{index}] supera {_MAX_ARG_CHARS} caracteres."
                )

            total_bytes += len(value.encode("utf-8"))
            normalized_argv.append(value)

        if total_bytes > _MAX_ARGV_UTF8_BYTES:
            raise ValueError(
                f"argv supera {_MAX_ARGV_UTF8_BYTES} bytes UTF-8."
            )

        normalized_tuple = tuple(normalized_argv)
        if normalized_tuple[0] != executable:
            raise ValueError(
                "argv[0] debe coincidir exactamente con executable."
            )

        cwd = _exact_relative_cwd(self.cwd)

        try:
            sandbox_profile = CommandSandboxProfile(self.sandbox_profile)
            environment_profile = CommandEnvironmentProfile(
                self.environment_profile
            )
            stdin_policy = CommandStdinPolicy(self.stdin_policy)
        except ValueError as exc:
            raise ValueError("Perfil de comando no reconocido.") from exc

        timeout_seconds = _bounded_exact_int(
            self.timeout_seconds,
            label="timeout_seconds",
            minimum=1,
            maximum=_MAX_COMMAND_TIMEOUT_SECONDS,
        )
        stdout_limit_bytes = _bounded_exact_int(
            self.stdout_limit_bytes,
            label="stdout_limit_bytes",
            minimum=1,
            maximum=_MAX_OUTPUT_BYTES,
        )
        stderr_limit_bytes = _bounded_exact_int(
            self.stderr_limit_bytes,
            label="stderr_limit_bytes",
            minimum=1,
            maximum=_MAX_OUTPUT_BYTES,
        )

        object.__setattr__(self, "executable", executable)
        object.__setattr__(self, "argv", normalized_tuple)
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "sandbox_profile", sandbox_profile)
        object.__setattr__(
            self,
            "environment_profile",
            environment_profile,
        )
        object.__setattr__(self, "stdin_policy", stdin_policy)
        object.__setattr__(self, "timeout_seconds", timeout_seconds)
        object.__setattr__(
            self,
            "stdout_limit_bytes",
            stdout_limit_bytes,
        )
        object.__setattr__(
            self,
            "stderr_limit_bytes",
            stderr_limit_bytes,
        )

    def to_data(self) -> dict[str, Any]:
        return {
            "executable": self.executable,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "sandbox_profile": self.sandbox_profile.value,
            "environment_profile": self.environment_profile.value,
            "stdin_policy": self.stdin_policy.value,
            "timeout_seconds": self.timeout_seconds,
            "stdout_limit_bytes": self.stdout_limit_bytes,
            "stderr_limit_bytes": self.stderr_limit_bytes,
        }

    @classmethod
    def from_data(cls, raw: object) -> CommandSpec:
        if not isinstance(raw, dict):
            raise TypeError("CommandSpec persistido debe ser un objeto.")

        if frozenset(raw) != _COMMAND_SPEC_KEYS:
            raise ValueError(
                "CommandSpec persistido tiene campos faltantes o inesperados."
            )

        argv_raw = raw["argv"]
        if not isinstance(argv_raw, list):
            raise TypeError("CommandSpec.argv persistido debe ser una lista.")

        return cls(
            executable=_json_string(
                raw["executable"],
                "CommandSpec.executable",
                allow_empty=False,
            ),
            argv=tuple(
                _json_string(
                    value,
                    f"CommandSpec.argv[{index}]",
                    allow_empty=True,
                )
                for index, value in enumerate(argv_raw)
            ),
            cwd=_json_string(
                raw["cwd"],
                "CommandSpec.cwd",
                allow_empty=False,
            ),
            sandbox_profile=CommandSandboxProfile(
                _json_string(
                    raw["sandbox_profile"],
                    "CommandSpec.sandbox_profile",
                    allow_empty=False,
                )
            ),
            environment_profile=CommandEnvironmentProfile(
                _json_string(
                    raw["environment_profile"],
                    "CommandSpec.environment_profile",
                    allow_empty=False,
                )
            ),
            stdin_policy=CommandStdinPolicy(
                _json_string(
                    raw["stdin_policy"],
                    "CommandSpec.stdin_policy",
                    allow_empty=False,
                )
            ),
            timeout_seconds=_json_int(
                raw["timeout_seconds"],
                "CommandSpec.timeout_seconds",
            ),
            stdout_limit_bytes=_json_int(
                raw["stdout_limit_bytes"],
                "CommandSpec.stdout_limit_bytes",
            ),
            stderr_limit_bytes=_json_int(
                raw["stderr_limit_bytes"],
                "CommandSpec.stderr_limit_bytes",
            ),
        )


@dataclass(frozen=True, slots=True)
class ExecutableIdentity:
    canonical_path: str
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    sha256: str

    @classmethod
    def capture(cls, executable: str | Path) -> ExecutableIdentity:
        requested = Path(executable)

        try:
            resolved = requested.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise PermissionError(
                f"El ejecutable requerido no existe: {requested}"
            ) from exc

        if str(resolved) != str(requested):
            raise PermissionError(
                "El ejecutable debe usar su ruta canónica exacta; "
                f"recibido={requested}, canónico={resolved}."
            )

        try:
            with resolved.open("rb", buffering=0) as handle:
                before = os.fstat(handle.fileno())
                _require_safe_executable_metadata(
                    before,
                    resolved,
                )

                if handle.read(4) != b"\x7fELF":
                    raise PermissionError(
                        "Phase 6 solo admite ejecutables ELF directos; "
                        "scripts/shebang quedan denegados."
                    )

                handle.seek(0)
                digest = hashlib.sha256()

                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)

                after = os.fstat(handle.fileno())
        except PermissionError:
            raise
        except OSError as exc:
            raise PermissionError(
                f"No se pudo capturar el ejecutable: {resolved}"
            ) from exc

        if _stat_identity(before) != _stat_identity(after):
            raise PermissionError(
                "El ejecutable cambió mientras se capturaba su identidad."
            )

        try:
            path_after = resolved.stat()
        except OSError as exc:
            raise PermissionError(
                "El ejecutable desapareció después de capturar su identidad."
            ) from exc

        if _stat_identity(after) != _stat_identity(path_after):
            raise PermissionError(
                "La ruta del ejecutable cambió durante la captura."
            )

        if not os.access(resolved, os.X_OK):
            raise PermissionError(
                f"El archivo no es ejecutable por el usuario actual: {resolved}"
            )

        return cls(
            canonical_path=str(resolved),
            device=after.st_dev,
            inode=after.st_ino,
            mode=after.st_mode,
            size=after.st_size,
            mtime_ns=after.st_mtime_ns,
            ctime_ns=after.st_ctime_ns,
            sha256=digest.hexdigest(),
        )

    def to_data(self) -> dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "device": self.device,
            "inode": self.inode,
            "mode": self.mode,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "ctime_ns": self.ctime_ns,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class CommandSnapshot:
    """
    Frozen identity of one CommandSpec at one filesystem instant.

    Revalidation detects changes but still does not execute anything.
    """

    spec: CommandSpec
    resolved_cwd: str
    executable: ExecutableIdentity
    command_sha256: str

    @classmethod
    def capture(
        cls,
        spec: CommandSpec,
        *,
        resolved_cwd: str | Path,
    ) -> CommandSnapshot:
        if not isinstance(spec, CommandSpec):
            raise TypeError("spec debe ser CommandSpec.")

        cwd = _canonical_existing_directory(resolved_cwd)
        executable = ExecutableIdentity.capture(spec.executable)

        command_sha256 = _command_sha256(
            spec,
            resolved_cwd=cwd,
            executable=executable,
        )

        return cls(
            spec=spec,
            resolved_cwd=str(cwd),
            executable=executable,
            command_sha256=command_sha256,
        )

    def revalidate(self) -> CommandSnapshot:
        current = CommandSnapshot.capture(
            self.spec,
            resolved_cwd=self.resolved_cwd,
        )

        if current.command_sha256 != self.command_sha256:
            raise PermissionError(
                "CommandSpec o identidad filesystem cambió "
                "desde la preparación original."
            )

        return current


def _command_sha256(
    spec: CommandSpec,
    *,
    resolved_cwd: Path,
    executable: ExecutableIdentity,
) -> str:
    encoded = json.dumps(
        {
            "contract_version": 1,
            "spec": spec.to_data(),
            "resolved_cwd": str(resolved_cwd),
            "executable_identity": executable.to_data(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def _require_safe_executable_metadata(
    metadata: os.stat_result,
    executable: Path,
) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise PermissionError(
            f"El ejecutable debe ser un archivo regular: {executable}"
        )

    if metadata.st_mode & (stat.S_ISUID | stat.S_ISGID):
        raise PermissionError(
            "No se permiten ejecutables setuid/setgid en autonomía."
        )

    if metadata.st_size <= 0:
        raise PermissionError("El ejecutable está vacío.")

    if metadata.st_size > _MAX_EXECUTABLE_BYTES:
        raise PermissionError(
            "El ejecutable supera el tamaño máximo permitido "
            f"de {_MAX_EXECUTABLE_BYTES} bytes."
        )


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _canonical_existing_directory(value: str | Path) -> Path:
    requested = Path(value)

    try:
        resolved = requested.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError(
            f"El cwd requerido no existe: {requested}"
        ) from exc

    if not resolved.is_dir():
        raise ValueError(f"cwd debe ser una carpeta: {resolved}")

    return resolved


def _exact_absolute_path(
    value: object,
    *,
    label: str,
    maximum: int,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} debe ser texto.")
    if not value:
        raise ValueError(f"{label} no puede estar vacío.")
    if "\x00" in value:
        raise ValueError(f"{label} contiene NUL.")
    if len(value) > maximum:
        raise ValueError(
            f"{label} supera el máximo de {maximum} caracteres."
        )

    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} debe ser una ruta absoluta.")

    if value.startswith("//") or os.path.normpath(value) != value:
        raise ValueError(
            f"{label} debe estar normalizado lexicalmente."
        )

    return value


def _exact_relative_cwd(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("cwd debe ser texto.")
    if not value:
        raise ValueError("cwd no puede estar vacío.")
    if "\x00" in value:
        raise ValueError("cwd contiene NUL.")
    if len(value) > _MAX_CWD_CHARS:
        raise ValueError(
            f"cwd supera {_MAX_CWD_CHARS} caracteres."
        )

    path = Path(value)
    if path.is_absolute():
        raise ValueError("cwd debe ser relativo al WorkspaceScope.")

    if ".." in path.parts:
        raise ValueError("cwd no puede contener segmentos '..'.")

    if os.path.normpath(value) != value:
        raise ValueError("cwd debe estar normalizado lexicalmente.")

    return value


def _bounded_exact_int(
    value: object,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} debe ser un entero.")

    if not minimum <= value <= maximum:
        raise ValueError(
            f"{label} debe estar entre {minimum} y {maximum}."
        )

    return value


def _json_string(
    value: object,
    label: str,
    *,
    allow_empty: bool,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} debe ser texto.")
    if not allow_empty and not value:
        raise ValueError(f"{label} no puede estar vacío.")
    return value


def _json_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} debe ser un entero.")
    return value


def valid_command_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))