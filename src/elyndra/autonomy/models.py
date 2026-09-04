from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from elyndra.autonomy.capabilities import Capability, CapabilityGrant
from elyndra.autonomy.scope import WorkspaceScope

_STEP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_PLAN_STEPS = 500
_MAX_OBJECTIVE_CHARS = 4_000
_MAX_ACTION_CHARS = 160
_MAX_TARGET_CHARS = 2_000
_MAX_REASON_CHARS = 2_000


class AutonomyRunStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class HumanGateKind(StrEnum):
    APPROVAL = "approval"
    REVIEW = "review"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


class HumanGateStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RunStep:
    step_id: str
    capability: Capability
    action: str
    target: str = ""
    requires_human_gate: bool = False

    def __post_init__(self) -> None:
        clean_id = self.step_id.strip().casefold()
        if not _STEP_ID_RE.fullmatch(clean_id):
            raise ValueError(
                "step_id debe usar solo a-z, 0-9, punto, guion o guion bajo "
                "y tener entre 1 y 64 caracteres."
            )

        try:
            capability = Capability(self.capability)
        except ValueError as exc:
            raise ValueError(
                f"Capability no registrada en step {clean_id!r}."
            ) from exc

        action = _required(self.action, "action", _MAX_ACTION_CHARS)
        target = self.target.strip()
        if len(target) > _MAX_TARGET_CHARS:
            raise ValueError(
                f"target supera {_MAX_TARGET_CHARS} caracteres."
            )

        object.__setattr__(self, "step_id", clean_id)
        object.__setattr__(self, "capability", capability)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "target", target)


@dataclass(frozen=True, slots=True)
class RunPlan:
    objective: str
    steps: tuple[RunStep, ...]

    def __post_init__(self) -> None:
        objective = _required(
            self.objective,
            "objective",
            _MAX_OBJECTIVE_CHARS,
        )
        steps = tuple(self.steps)

        if not steps:
            raise ValueError("Un plan autónomo requiere al menos un step.")

        if len(steps) > _MAX_PLAN_STEPS:
            raise ValueError(
                f"El plan supera el límite de {_MAX_PLAN_STEPS} steps."
            )

        identifiers = [step.step_id for step in steps]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Los step_id del plan deben ser únicos.")

        object.__setattr__(self, "objective", objective)
        object.__setattr__(self, "steps", steps)

    @property
    def required_capabilities(self) -> frozenset[Capability]:
        return frozenset(step.capability for step in self.steps)


@dataclass(frozen=True, slots=True)
class HumanGate:
    run_id: str
    reason: str
    kind: HumanGateKind = HumanGateKind.APPROVAL
    gate_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: HumanGateStatus = HumanGateStatus.PENDING

    def __post_init__(self) -> None:
        run_id = _required(self.run_id, "run_id", 128)
        gate_id = _required(self.gate_id, "gate_id", 128)
        reason = _required(self.reason, "reason", _MAX_REASON_CHARS)

        try:
            kind = HumanGateKind(self.kind)
            status = HumanGateStatus(self.status)
        except ValueError as exc:
            raise ValueError("Tipo o estado de HumanGate inválido.") from exc

        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "gate_id", gate_id)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "status", status)


@dataclass(frozen=True, slots=True)
class AutonomyRun:
    actor: str
    workspace: WorkspaceScope
    grant: CapabilityGrant
    plan: RunPlan
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: AutonomyRunStatus = AutonomyRunStatus.PLANNED
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        actor = _required(self.actor, "actor", 200)
        run_id = _required(self.run_id, "run_id", 128)

        try:
            status = AutonomyRunStatus(self.status)
        except ValueError as exc:
            raise ValueError("Estado de autonomía inválido.") from exc

        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at debe incluir zona horaria.")

        if self.grant.is_expired(at=self.created_at):
            raise PermissionError(
                "No se puede crear un AutonomyRun con un grant expirado."
            )

        if len(self.plan.steps) > self.grant.max_steps:
            raise PermissionError(
                "El plan supera max_steps del CapabilityGrant."
            )

        missing = (
            self.plan.required_capabilities
            - self.grant.capabilities
        )
        if missing:
            rendered = ", ".join(
                sorted(capability.value for capability in missing)
            )
            raise PermissionError(
                f"El plan requiere capabilities no concedidas: {rendered}"
            )

        object.__setattr__(self, "actor", actor)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "status", status)


def _required(value: str, label: str, maximum: int) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{label} no puede estar vacío.")
    if len(clean) > maximum:
        raise ValueError(
            f"{label} supera el máximo de {maximum} caracteres."
        )
    return clean
