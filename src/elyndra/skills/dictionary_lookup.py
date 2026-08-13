from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from elyndra.dictionary import LocalDictionary
from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult


@dataclass(slots=True)
class DictionaryLookupSkill:
    name: str = "dictionary.lookup"
    description: str = (
        "Consulta el lexicón multilingüe local inicial sin usar red ni modelo."
    )
    risk: RiskLevel = RiskLevel.LOW

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        term = str(params.get("term", "")).strip()
        if not term:
            return SkillResult(False, "Falta el parámetro term.", {})
        language = str(params.get("language", "")).strip() or None
        output_language = str(params.get("output_language", "es")).strip() or "es"
        dialect = str(params.get("dialect", "")).strip() or None
        dictionary = LocalDictionary(context.structured_packs)
        message, data = dictionary.render_lookup(
            term,
            language=language,
            output_language=output_language,
            dialect=dialect,
        )
        return SkillResult(
            True,
            message,
            {
                "engine": "local-dictionary",
                "generated": False,
                "network_access": False,
                "model_used": False,
            }
            | data,
        )
