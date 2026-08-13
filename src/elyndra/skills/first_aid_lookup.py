from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from elyndra.first_aid import FirstAidLibrary
from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult


@dataclass(slots=True)
class FirstAidLookupSkill:
    name: str = "first_aid.lookup"
    description: str = (
        "Consulta tarjetas locales de primeros auxilios para acciones inmediatas mientras "
        "llega ayuda."
    )
    risk: RiskLevel = RiskLevel.LOW

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        topic = str(params.get("topic", "")).strip()
        query = str(params.get("query", "")).strip()
        language = str(params.get("language", "es")).strip() or "es"
        locale = str(params.get("locale", "")).strip() or None
        library = FirstAidLibrary(context.structured_packs)
        selected = (
            library.topic(topic, language=language)
            if topic
            else library.lookup(query, language=language, locale=locale)
        )
        if selected is None:
            return SkillResult(
                False,
                "No se encontró una tarjeta local para esa situación.",
                {"found": False},
            )
        message, data = library.render_topic(selected, language=language)
        return SkillResult(
            True,
            message,
            {
                "engine": "local-first-aid",
                "generated": False,
                "network_access": False,
                "model_used": False,
            }
            | data,
        )
