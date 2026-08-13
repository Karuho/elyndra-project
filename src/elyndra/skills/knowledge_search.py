from __future__ import annotations

from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult


class KnowledgeSearchSkill:
    name = "knowledge.search"
    description = "Busca información en documentos importados y devuelve procedencia local."
    risk = RiskLevel.LOW

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        query = str(params.get("query", "")).strip()
        if not query:
            return SkillResult(False, "Falta el texto a buscar.", {})
        results = context.knowledge.search(query, int(params.get("limit", 10)))
        if not results:
            return SkillResult(True, "No encontré conocimiento local relacionado.", {"results": []})
        lines = []
        for item in results:
            citation = f"[doc#{item['document_id']} fragmento#{item['chunk_index']}]"
            project = f" · proyecto {item['project']}" if item.get("project") else ""
            lines.append(f"{citation} {item['title']}{project}\n{item['excerpt']}")
        return SkillResult(
            True,
            "Resultados de conocimiento local:\n\n" + "\n\n".join(lines),
            {"results": results, "query": query},
        )
