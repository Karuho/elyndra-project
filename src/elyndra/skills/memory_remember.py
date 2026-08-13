from __future__ import annotations

from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult


class MemoryRememberSkill:
    name = "memory.remember"
    description = "Guarda un recuerdo explícito del propietario en SQLite."
    risk = RiskLevel.LOW

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        content = str(params.get("content", "")).strip()
        if not content:
            return SkillResult(False, "Falta el contenido del recuerdo.", {})
        memory_id = context.memories.add(
            content,
            kind=str(params.get("kind", "fact")),
            project=str(params["project"]) if params.get("project") else None,
            source="owner",
            confidence=1.0,
        )
        return SkillResult(
            True,
            f"Recuerdo guardado con ID {memory_id}.",
            {"memory_id": memory_id, "kind": str(params.get("kind", "fact"))},
        )
