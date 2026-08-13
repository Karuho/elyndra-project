from __future__ import annotations

from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult


class KnowledgeImportSkill:
    name = "knowledge.import"
    description = "Importa un archivo de texto autorizado a la base local con hash y fragmentos."
    risk = RiskLevel.MEDIUM

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        path_value = str(params.get("path", "")).strip()
        if not path_value:
            return SkillResult(False, "Falta la ruta del archivo a importar.", {})
        try:
            imported = context.knowledge.import_file(
                Path(path_value),
                title=str(params["title"]) if params.get("title") else None,
                project=str(params["project"]) if params.get("project") else None,
                force=bool(params.get("force", False)),
            )
        except (OSError, PermissionError, ValueError) as exc:
            return SkillResult(False, str(exc), {})

        status = imported["status"]
        if status == "unchanged":
            message = (
                f"El documento ya estaba actualizado: {imported['title']} "
                f"(ID {imported['document_id']}, {imported['chunks']} fragmentos)."
            )
        else:
            verb = "Importado" if status == "imported" else "Actualizado"
            message = (
                f"{verb}: {imported['title']} como documento #{imported['document_id']} "
                f"con {imported['chunks']} fragmentos."
            )
        return SkillResult(True, message, imported)
