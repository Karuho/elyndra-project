from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class Capability(StrEnum):
    WORKSPACE_READ = "workspace.read"
    WORKSPACE_WRITE = "workspace.write"
    PROCESS_EXEC = "process.exec"

    NETWORK_MODEL = "network.model"
    NETWORK_ARTIFACT = "network.artifact"

    GIT_BRANCH = "git.branch"
    GIT_COMMIT = "git.commit"
    GIT_PUSH = "git.push"
    GIT_TAG = "git.tag"

    GITHUB_ISSUE = "github.issue"
    GITHUB_PR = "github.pr"
    GITHUB_MERGE = "github.merge"
    GITHUB_RELEASE = "github.release"

    SELF_MODIFY = "self.modify"
    BACKGROUND_RUN = "background.run"


_MAX_STEPS = 500
_MAX_RETRIES = 10
_MAX_COMMANDS = 2_000
_MAX_RUNTIME_SECONDS = 86_400


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    """Ephemeral bounded authority for one autonomy run."""

    capabilities: frozenset[Capability]
    expires_at: datetime
    issued_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    max_steps: int = 40
    max_retries: int = 2
    max_commands: int = 80
    max_runtime_seconds: int = 3_600
    allowed_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized: set[Capability] = set()
        for capability in self.capabilities:
            try:
                normalized.add(Capability(capability))
            except ValueError as exc:
                raise ValueError(
                    f"Capability no registrada: {capability!r}"
                ) from exc

        object.__setattr__(self, "capabilities", frozenset(normalized))

        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")

        if self.expires_at <= self.issued_at:
            raise ValueError("El grant debe expirar después de su emisión.")

        _bounded_positive(self.max_steps, _MAX_STEPS, "max_steps")
        _bounded_positive(self.max_retries, _MAX_RETRIES, "max_retries", allow_zero=True)
        _bounded_positive(self.max_commands, _MAX_COMMANDS, "max_commands")
        _bounded_positive(
            self.max_runtime_seconds,
            _MAX_RUNTIME_SECONDS,
            "max_runtime_seconds",
        )

        normalized_hosts: list[str] = []
        for host in self.allowed_hosts:
            clean = host.strip().casefold()
            if not clean:
                raise ValueError("Un host permitido no puede estar vacío.")
            if any(token in clean for token in ("://", "/", "*")):
                raise ValueError(
                    "Los hosts permitidos deben ser nombres o direcciones exactas, "
                    "sin esquema, path ni wildcard."
                )
            if clean not in normalized_hosts:
                normalized_hosts.append(clean)

        object.__setattr__(self, "allowed_hosts", tuple(normalized_hosts))

    def is_expired(self, *, at: datetime | None = None) -> bool:
        current = at or datetime.now(UTC)
        _require_aware(current, "at")
        return current >= self.expires_at

    def allows(
        self,
        capability: Capability | str,
        *,
        at: datetime | None = None,
    ) -> bool:
        if self.is_expired(at=at):
            return False
        try:
            required = Capability(capability)
        except ValueError:
            return False
        return required in self.capabilities

    def require(
        self,
        capability: Capability | str,
        *,
        at: datetime | None = None,
    ) -> Capability:
        try:
            required = Capability(capability)
        except ValueError as exc:
            raise PermissionError(
                f"Capability no registrada: {capability!r}"
            ) from exc

        if not self.allows(required, at=at):
            raise PermissionError(
                f"Capability no concedida o expirada: {required.value}"
            )
        return required

    def allows_host(self, host: str) -> bool:
        clean = host.strip().casefold()
        return bool(clean) and clean in self.allowed_hosts


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} debe incluir zona horaria.")


def _bounded_positive(
    value: int,
    maximum: int,
    label: str,
    *,
    allow_zero: bool = False,
) -> None:
    minimum = 0 if allow_zero else 1
    if not minimum <= int(value) <= maximum:
        raise ValueError(
            f"{label} debe estar entre {minimum} y {maximum}."
        )
