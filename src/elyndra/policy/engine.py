from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason: str


class PolicyEngine:
    def evaluate(self, risk: RiskLevel, *, approved: bool = False) -> PolicyDecision:
        if risk is RiskLevel.LOW:
            return PolicyDecision(True, "Acción local de bajo riesgo.")
        if risk is RiskLevel.MEDIUM:
            if approved:
                return PolicyDecision(True, "Acción de riesgo medio aprobada por el propietario.")
            return PolicyDecision(False, "Esta acción requiere --approve.")
        if risk is RiskLevel.HIGH:
            return PolicyDecision(
                False,
                "Las acciones de alto riesgo están bloqueadas en Elyndra 0.2.",
            )
        return PolicyDecision(False, "Acción bloqueada por política.")
