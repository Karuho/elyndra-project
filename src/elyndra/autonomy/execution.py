from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from elyndra.autonomy.capabilities import Capability, CapabilityGrant
from elyndra.autonomy.models import RunPlan, RunStep
from elyndra.autonomy.scope import WorkspaceScope

_PATH_CAPABILITIES = frozenset(
    {
        Capability.WORKSPACE_READ,
        Capability.WORKSPACE_WRITE,
        Capability.PROCESS_EXEC,
        Capability.GIT_BRANCH,
        Capability.GIT_COMMIT,
        Capability.GIT_PUSH,
        Capability.GIT_TAG,
        Capability.SELF_MODIFY,
    }
)

_EXISTING_TARGET_CAPABILITIES = frozenset(
    {
        Capability.WORKSPACE_READ,
        Capability.PROCESS_EXEC,
        Capability.GIT_BRANCH,
        Capability.GIT_COMMIT,
        Capability.GIT_PUSH,
        Capability.GIT_TAG,
    }
)

_NETWORK_CAPABILITIES = frozenset(
    {
        Capability.NETWORK_MODEL,
        Capability.NETWORK_ARTIFACT,
    }
)


class ExecutionOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DENIED = "denied"


class ExecutionDenied(PermissionError):
    """The prepared operation is outside the frozen authority envelope."""


class ExecutionCancelled(RuntimeError):
    """The local cancellation token stopped preparation or execution."""


@dataclass(frozen=True, slots=True)
class ExecutionBudgetSnapshot:
    max_commands: int
    max_retries: int
    max_runtime_seconds: int
    commands_reserved: int
    retries_reserved: int
    runtime_seconds_reserved: int

    @property
    def commands_remaining(self) -> int:
        return self.max_commands - self.commands_reserved

    @property
    def retries_remaining(self) -> int:
        return self.max_retries - self.retries_reserved

    @property
    def runtime_seconds_remaining(self) -> int:
        return self.max_runtime_seconds - self.runtime_seconds_reserved


@dataclass(slots=True)
class ExecutionBudget:
    max_commands: int
    max_retries: int
    max_runtime_seconds: int
    commands_reserved: int = 0
    retries_reserved: int = 0
    runtime_seconds_reserved: int = 0

    def __post_init__(self) -> None:
        values = {
            "max_commands": self.max_commands,
            "max_retries": self.max_retries,
            "max_runtime_seconds": self.max_runtime_seconds,
            "commands_reserved": self.commands_reserved,
            "retries_reserved": self.retries_reserved,
            "runtime_seconds_reserved": self.runtime_seconds_reserved,
        }

        normalized: dict[str, int] = {}
        for label, value in values.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{label} debe ser un entero.")
            normalized[label] = value

        if normalized["max_commands"] < 1:
            raise ValueError("max_commands debe ser al menos 1.")
        if normalized["max_retries"] < 0:
            raise ValueError("max_retries no puede ser negativo.")
        if normalized["max_runtime_seconds"] < 1:
            raise ValueError("max_runtime_seconds debe ser al menos 1.")

        if not 0 <= normalized["commands_reserved"] <= normalized["max_commands"]:
            raise ValueError(
                "commands_reserved debe estar entre 0 y max_commands."
            )

        if not 0 <= normalized["retries_reserved"] <= normalized["max_retries"]:
            raise ValueError(
                "retries_reserved debe estar entre 0 y max_retries."
            )

        if not (
            0
            <= normalized["runtime_seconds_reserved"]
            <= normalized["max_runtime_seconds"]
        ):
            raise ValueError(
                "runtime_seconds_reserved debe estar entre 0 "
                "y max_runtime_seconds."
            )

    @classmethod
    def from_grant(cls, grant: CapabilityGrant) -> ExecutionBudget:
        return cls(
            max_commands=grant.max_commands,
            max_retries=grant.max_retries,
            max_runtime_seconds=grant.max_runtime_seconds,
        )

    def snapshot(self) -> ExecutionBudgetSnapshot:
        return ExecutionBudgetSnapshot(
            max_commands=self.max_commands,
            max_retries=self.max_retries,
            max_runtime_seconds=self.max_runtime_seconds,
            commands_reserved=self.commands_reserved,
            retries_reserved=self.retries_reserved,
            runtime_seconds_reserved=self.runtime_seconds_reserved,
        )

    def require_within(self, grant: CapabilityGrant) -> None:
        if self.max_commands > grant.max_commands:
            raise ValueError("El budget no puede ampliar max_commands del grant.")
        if self.max_retries > grant.max_retries:
            raise ValueError("El budget no puede ampliar max_retries del grant.")
        if self.max_runtime_seconds > grant.max_runtime_seconds:
            raise ValueError(
                "El budget no puede ampliar max_runtime_seconds del grant."
            )

    def reserve(
        self,
        *,
        runtime_seconds: int = 0,
        retry: bool = False,
    ) -> ExecutionBudgetSnapshot:
        if isinstance(runtime_seconds, bool) or not isinstance(
            runtime_seconds,
            int,
        ):
            raise TypeError("runtime_seconds debe ser un entero.")

        runtime = runtime_seconds
        if runtime < 0:
            raise ValueError("runtime_seconds no puede ser negativo.")

        next_commands = self.commands_reserved + 1
        next_retries = self.retries_reserved + (1 if retry else 0)
        next_runtime = self.runtime_seconds_reserved + runtime

        if next_commands > self.max_commands:
            raise ExecutionDenied("Se agotó max_commands del CapabilityGrant.")

        if next_retries > self.max_retries:
            raise ExecutionDenied("Se agotó max_retries del CapabilityGrant.")

        if next_runtime > self.max_runtime_seconds:
            raise ExecutionDenied(
                "La reserva excede max_runtime_seconds del CapabilityGrant."
            )

        self.commands_reserved = next_commands
        self.retries_reserved = next_retries
        self.runtime_seconds_reserved = next_runtime

        return self.snapshot()


@dataclass(slots=True)
class CancellationToken:
    _cancelled: bool = False
    _reason: str = ""

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def reason(self) -> str:
        return self._reason

    def cancel(self, reason: str = "Cancelado por el propietario.") -> None:
        if self._cancelled:
            return

        clean = reason.strip()
        if not clean:
            raise ValueError("La razón de cancelación no puede estar vacía.")
        if len(clean) > 2_000:
            raise ValueError("La razón de cancelación supera 2000 caracteres.")

        self._cancelled = True
        self._reason = clean

    def require_active(self) -> None:
        if self._cancelled:
            raise ExecutionCancelled(self._reason)


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    run_id: str
    step_id: str
    capability: Capability
    action: str
    target: str
    requires_human_gate: bool
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        run_id = _required(self.run_id, "run_id", 128)
        step_id = _required(self.step_id, "step_id", 64)
        action = _required(self.action, "action", 160)
        request_id = _required(self.request_id, "request_id", 128)

        try:
            capability = Capability(self.capability)
        except ValueError as exc:
            raise ValueError("Capability de ejecución inválida.") from exc

        target = self.target.strip()
        if len(target) > 2_000:
            raise ValueError("target supera 2000 caracteres.")

        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "step_id", step_id)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "capability", capability)
        object.__setattr__(self, "target", target)


@dataclass(frozen=True, slots=True)
class PreparedExecution:
    request: ExecutionRequest
    resolved_target: str | None
    budget: ExecutionBudgetSnapshot
    prepared_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.prepared_at.tzinfo is None or self.prepared_at.utcoffset() is None:
            raise ValueError("prepared_at debe incluir zona horaria.")


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    request_id: str
    outcome: ExecutionOutcome
    summary: str
    exit_code: int | None = None
    duration_ms: int = 0
    error_code: str = ""

    def __post_init__(self) -> None:
        request_id = _required(self.request_id, "request_id", 128)
        summary = _required(self.summary, "summary", 2_000)

        try:
            outcome = ExecutionOutcome(self.outcome)
        except ValueError as exc:
            raise ValueError("Resultado de ejecución inválido.") from exc

        duration = int(self.duration_ms)
        if duration < 0:
            raise ValueError("duration_ms no puede ser negativo.")

        error_code = self.error_code.strip()
        if len(error_code) > 80:
            raise ValueError("error_code supera 80 caracteres.")

        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "duration_ms", duration)
        object.__setattr__(self, "error_code", error_code)


class Executor(Protocol):
    """Future executor interface. Phase 3 ships no implementation."""

    def execute(
        self,
        prepared: PreparedExecution,
        *,
        cancellation: CancellationToken,
    ) -> ExecutionResult:
        ...


class ExecutionContract:
    """Pure authority/budget gate between a frozen plan and a future executor."""

    def __init__(
        self,
        *,
        run_id: str,
        plan: RunPlan,
        workspace: WorkspaceScope,
        grant: CapabilityGrant,
        approved_step_ids: frozenset[str] = frozenset(),
        budget: ExecutionBudget | None = None,
        cancellation: CancellationToken | None = None,
    ) -> None:
        self.run_id = _required(run_id, "run_id", 128)
        self.plan = plan
        self.workspace = workspace
        self.grant = grant
        self.budget = budget or ExecutionBudget.from_grant(grant)
        self.cancellation = cancellation or CancellationToken()

        self.budget.require_within(grant)

        steps = {step.step_id: step for step in plan.steps}
        approved = frozenset(item.strip().casefold() for item in approved_step_ids)

        unknown = approved - steps.keys()
        if unknown:
            rendered = ", ".join(sorted(unknown))
            raise ValueError(
                f"approved_step_ids contiene steps ajenos al plan: {rendered}"
            )

        self._steps = steps
        self._approved_step_ids = approved

    def prepare(
        self,
        step_id: str,
        *,
        runtime_seconds: int = 0,
        retry: bool = False,
    ) -> PreparedExecution:
        self.cancellation.require_active()

        clean_step_id = _required(step_id, "step_id", 64).casefold()
        step = self._steps.get(clean_step_id)
        if step is None:
            raise ExecutionDenied(
                f"El step no pertenece al plan congelado: {clean_step_id}"
            )

        self.grant.require(step.capability, at=_utcnow())

        if (
            step.requires_human_gate
            and step.step_id not in self._approved_step_ids
        ):
            raise ExecutionDenied(
                f"El step {step.step_id} requiere HumanGate aprobado."
            )

        resolved_target = self._resolve_target(step)

        budget = self.budget.reserve(
            runtime_seconds=runtime_seconds,
            retry=retry,
        )

        request = ExecutionRequest(
            run_id=self.run_id,
            step_id=step.step_id,
            capability=step.capability,
            action=step.action,
            target=step.target,
            requires_human_gate=step.requires_human_gate,
        )

        return PreparedExecution(
            request=request,
            resolved_target=resolved_target,
            budget=budget,
            prepared_at=_utcnow(),
        )

    def _resolve_target(self, step: RunStep) -> str | None:
        if step.capability in _PATH_CAPABILITIES:
            target = step.target or "."
            resolved = self.workspace.resolve(
                target,
                must_exist=step.capability in _EXISTING_TARGET_CAPABILITIES,
            )
            return str(resolved)

        if step.capability in _NETWORK_CAPABILITIES:
            host = step.target.strip().casefold()
            if not host:
                raise ExecutionDenied(
                    f"{step.capability.value} requiere un host exacto."
                )
            if not self.grant.allows_host(host):
                raise ExecutionDenied(
                    f"Host fuera del allowlist del CapabilityGrant: {host}"
                )
            return host

        return None


def _required(value: str, label: str, maximum: int) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{label} no puede estar vacío.")
    if len(clean) > maximum:
        raise ValueError(
            f"{label} supera el máximo de {maximum} caracteres."
        )
    return clean


def _utcnow() -> datetime:
    return datetime.now(UTC)
