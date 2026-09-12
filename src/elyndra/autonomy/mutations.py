"""Immutable mutation proposal commitments for bounded autonomy."""

from __future__ import annotations

import base64
import hashlib
import json
import posixpath
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

MUTATION_PROPOSAL_DOMAIN = "elyndra.autonomy.mutation-proposal.v1"
MUTATION_PROPOSAL_FORMAT = "v1"
MAX_MUTATION_ITEMS = 3
MAX_PROPOSED_BYTES_PER_ITEM = 65_536
MAX_TOTAL_PROPOSED_BYTES = 131_072
MAX_ORIGINAL_BYTES = 262_144
MAX_RELATIVE_PATH_BYTES = 512
MAX_PATH_COMPONENT_BYTES = 255
MAX_PROPOSAL_LIFETIME = timedelta(minutes=30)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STEP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
_PROTECTED_PARTS = frozenset(
    {
        ".circleci",
        ".devcontainer",
        ".elyndra-mutation-journal",
        ".git",
        ".github",
        ".gitlab",
        ".hg",
        ".svn",
        ".venv",
        ".vscode",
        "__pycache__",
        "node_modules",
        "target",
        "venv",
    }
)
_PROTECTED_NAMES = frozenset(
    {
        ".env",
        ".npmrc",
        ".pypirc",
        "authorized_keys",
        "credentials",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
        "known_hosts",
        "secrets",
        "secrets.json",
    }
)
_PROTECTED_SUFFIXES = frozenset({".key", ".p12", ".pem", ".pfx"})


class MutationOperation(StrEnum):
    """Complete-content mutation operations supported by Phase 9A."""

    CREATE = "create"
    REPLACE = "replace"


@dataclass(frozen=True, slots=True)
class MutationItem:
    """One immutable complete-content mutation candidate."""

    relative_path: str
    operation: MutationOperation
    original_exists: bool
    original_sha256: str | None
    original_size: int | None
    proposed_content: bytes | str
    proposed_sha256: str = field(init=False)
    proposed_size: int = field(init=False)

    def __post_init__(self) -> None:
        relative_path = _validated_relative_path(self.relative_path)
        try:
            operation = MutationOperation(self.operation)
        except ValueError as exc:
            raise ValueError("Operación de mutación inválida.") from exc

        if not isinstance(self.original_exists, bool):
            raise TypeError("original_exists debe ser booleano.")

        if operation is MutationOperation.CREATE:
            if self.original_exists:
                raise ValueError("CREATE requiere original_exists=False.")
            if self.original_sha256 is not None or self.original_size is not None:
                raise ValueError("CREATE prohíbe preimagen SHA-256 y tamaño.")
        else:
            if not self.original_exists:
                raise ValueError("REPLACE requiere original_exists=True.")
            _require_sha256(self.original_sha256, "original_sha256")
            if isinstance(self.original_size, bool) or not isinstance(
                self.original_size, int
            ):
                raise TypeError("original_size debe ser un entero.")
            if not 0 <= self.original_size <= MAX_ORIGINAL_BYTES:
                raise ValueError(
                    f"original_size debe estar entre 0 y {MAX_ORIGINAL_BYTES}."
                )

        content = _content_bytes(self.proposed_content)
        if len(content) > MAX_PROPOSED_BYTES_PER_ITEM:
            raise ValueError(
                "proposed_content supera el límite de "
                f"{MAX_PROPOSED_BYTES_PER_ITEM} bytes."
            )

        object.__setattr__(self, "relative_path", relative_path)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "proposed_content", content)
        object.__setattr__(self, "proposed_sha256", hashlib.sha256(content).hexdigest())
        object.__setattr__(self, "proposed_size", len(content))

    def canonical_data(self) -> dict[str, object]:
        """Return the exact JSON-compatible item commitment."""

        return {
            "operation": self.operation.value,
            "original_exists": self.original_exists,
            "original_sha256": self.original_sha256,
            "original_size": self.original_size,
            "proposed_content_base64": base64.b64encode(
                self.proposed_content
            ).decode("ascii"),
            "proposed_sha256": self.proposed_sha256,
            "proposed_size": self.proposed_size,
            "relative_path": self.relative_path,
        }


@dataclass(frozen=True, slots=True)
class MutationProposal:
    """An immutable, non-authoritative mutation proposal commitment."""

    run_id: str
    step_id: str
    actor: str
    workspace_root: str
    items: tuple[MutationItem, ...]
    created_at: datetime
    expires_at: datetime
    format_version: str = MUTATION_PROPOSAL_FORMAT
    proposal_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        run_id = _required_exact(self.run_id, "run_id", 128)
        step_id = _required_exact(self.step_id, "step_id", 64)
        if not _STEP_ID_RE.fullmatch(step_id):
            raise ValueError("step_id no tiene el formato canónico esperado.")
        actor = _required_exact(self.actor, "actor", 200)
        workspace_root = _validated_workspace_root(self.workspace_root)
        if self.format_version != MUTATION_PROPOSAL_FORMAT:
            raise ValueError("Versión de formato de propuesta no soportada.")

        created_at = _utc_datetime(self.created_at, "created_at")
        expires_at = _utc_datetime(self.expires_at, "expires_at")
        lifetime = expires_at - created_at
        if lifetime <= timedelta(0):
            raise ValueError("expires_at debe ser posterior a created_at.")
        if lifetime > MAX_PROPOSAL_LIFETIME:
            raise ValueError("La propuesta no puede vivir más de 30 minutos.")

        items = tuple(self.items)
        if not 1 <= len(items) <= MAX_MUTATION_ITEMS:
            raise ValueError(
                f"La propuesta requiere entre 1 y {MAX_MUTATION_ITEMS} items."
            )
        if any(not isinstance(item, MutationItem) for item in items):
            raise TypeError("Todos los items deben ser MutationItem.")

        paths = [item.relative_path for item in items]
        if len(paths) != len(set(paths)):
            raise ValueError("La propuesta contiene rutas duplicadas.")
        folded = [unicodedata.normalize("NFC", path.casefold()) for path in paths]
        if len(folded) != len(set(folded)):
            raise ValueError("La propuesta contiene rutas que colisionan por casefold.")

        ordered = tuple(sorted(items, key=lambda item: item.relative_path.encode("utf-8")))
        total_size = sum(item.proposed_size for item in ordered)
        if total_size > MAX_TOTAL_PROPOSED_BYTES:
            raise ValueError(
                "La propuesta supera el límite total de "
                f"{MAX_TOTAL_PROPOSED_BYTES} bytes."
            )

        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "step_id", step_id)
        object.__setattr__(self, "actor", actor)
        object.__setattr__(self, "workspace_root", workspace_root)
        object.__setattr__(self, "items", ordered)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "proposal_sha256", _proposal_sha256(self.canonical_data()))

    def canonical_data(self) -> dict[str, object]:
        """Return all and only the authoritative proposal fields."""

        return {
            "actor": self.actor,
            "created_at": _datetime_text(self.created_at),
            "expires_at": _datetime_text(self.expires_at),
            "format_version": self.format_version,
            "items": [item.canonical_data() for item in self.items],
            "run_id": self.run_id,
            "step_id": self.step_id,
            "workspace_root": self.workspace_root,
        }

    def canonical_json_bytes(self) -> bytes:
        """Serialize the exact canonical proposal payload."""

        return _canonical_json_bytes(self.canonical_data())


@dataclass(frozen=True, slots=True)
class PersistedMutationProposal:
    """Vault lookup identity paired with its immutable proposal."""

    public_id: str
    request_key: str
    proposal: MutationProposal

    def __post_init__(self) -> None:
        _required_exact(self.public_id, "public_id", 128)
        _required_exact(self.request_key, "request_key", 128)
        if not isinstance(self.proposal, MutationProposal):
            raise TypeError("proposal debe ser MutationProposal.")


@dataclass(frozen=True, slots=True)
class MutationReviewRecord:
    """Bounded public metadata for one exact owner mutation review."""

    proposal_id: str
    proposal_sha256: str
    gate_id: str
    run_id: str
    step_id: str
    actor: str
    gate_status: str
    created_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None

    def __post_init__(self) -> None:
        _required_exact(self.proposal_id, "proposal_id", 128)
        _require_sha256(self.proposal_sha256, "proposal_sha256")
        _required_exact(self.gate_id, "gate_id", 128)
        _required_exact(self.run_id, "run_id", 128)
        step_id = _required_exact(self.step_id, "step_id", 64)
        if not _STEP_ID_RE.fullmatch(step_id):
            raise ValueError("step_id no tiene el formato canónico esperado.")
        _required_exact(self.actor, "actor", 200)
        if self.gate_status not in {"pending", "approved", "rejected", "cancelled"}:
            raise ValueError("gate_status inválido.")
        _utc_datetime(self.created_at, "created_at")
        if self.resolved_at is None:
            if self.gate_status != "pending" or self.resolved_by is not None:
                raise ValueError("Review pendiente con resolución inconsistente.")
        else:
            _utc_datetime(self.resolved_at, "resolved_at")
            if self.gate_status == "pending" or self.resolved_by is None:
                raise ValueError("Review resuelta con metadata inconsistente.")
            _required_exact(self.resolved_by, "resolved_by", 200)


def _validated_relative_path(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("relative_path debe ser texto.")
    if not value or "\x00" in value or "\\" in value:
        raise ValueError("Ruta relativa vacía o con caracteres prohibidos.")
    if value.startswith("/") or value.startswith("//"):
        raise ValueError("La ruta debe ser relativa y POSIX.")
    if value.endswith("/") or "//" in value:
        raise ValueError("La ruta no puede tener separadores vacíos.")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError("La ruta debe suministrarse ya normalizada en NFC.")

    encoded = value.encode("utf-8")
    if len(encoded) > MAX_RELATIVE_PATH_BYTES:
        raise ValueError(
            f"La ruta supera {MAX_RELATIVE_PATH_BYTES} bytes UTF-8."
        )
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("La ruta contiene un componente no permitido.")
    if any(len(part.encode("utf-8")) > MAX_PATH_COMPONENT_BYTES for part in parts):
        raise ValueError(
            f"Un componente de ruta supera {MAX_PATH_COMPONENT_BYTES} bytes UTF-8."
        )

    folded_parts = {part.casefold() for part in parts}
    if folded_parts & _PROTECTED_PARTS:
        raise ValueError("La ruta pertenece a un namespace protegido.")
    name = parts[-1].casefold()
    if name in _PROTECTED_NAMES or name.startswith(".env."):
        raise ValueError("La ruta identifica material local protegido.")
    if any(name.endswith(suffix) for suffix in _PROTECTED_SUFFIXES):
        raise ValueError("La ruta identifica material secreto protegido.")
    return value


def _validated_workspace_root(value: str) -> str:
    value = _required_exact(value, "workspace_root", 4_096)
    if "\x00" in value or "\\" in value or not value.startswith("/"):
        raise ValueError("workspace_root debe ser una ruta POSIX absoluta canónica.")
    if value.startswith("//") or (value != "/" and value.endswith("/")):
        raise ValueError("workspace_root no tiene forma POSIX canónica.")
    if posixpath.normpath(value) != value:
        raise ValueError("workspace_root no tiene forma POSIX canónica.")
    return value


def _content_bytes(value: bytes | str) -> bytes:
    if isinstance(value, str):
        content = value.encode("utf-8", errors="strict")
    elif isinstance(value, bytes):
        content = value
    else:
        raise TypeError("proposed_content debe ser bytes o str.")
    try:
        content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("proposed_content debe ser UTF-8 estricto.") from exc
    if b"\x00" in content:
        raise ValueError("proposed_content no puede contener NUL.")
    _reject_secret_material(content.decode("utf-8", errors="strict"))
    return content


def _reject_secret_material(content: str) -> None:
    if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
        raise ValueError("proposed_content contiene material secreto no permitido.")


def _required_exact(value: str, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} debe ser texto.")
    if not value or value != value.strip() or "\x00" in value:
        raise ValueError(f"{label} debe ser texto canónico no vacío.")
    if len(value) > maximum:
        raise ValueError(f"{label} supera {maximum} caracteres.")
    return value


def _require_sha256(value: str | None, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} debe ser SHA-256 hexadecimal minúsculo.")
    return value


def _utc_datetime(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{label} debe ser datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} debe incluir zona horaria UTC.")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} debe estar expresado en UTC.")
    return value.astimezone(UTC)


def _datetime_text(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _proposal_sha256(value: object) -> str:
    material = (
        MUTATION_PROPOSAL_DOMAIN.encode("utf-8")
        + b"\0"
        + _canonical_json_bytes(value)
    )
    return hashlib.sha256(material).hexdigest()
