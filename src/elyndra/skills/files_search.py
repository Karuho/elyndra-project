from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.path_safety import ensure_allowed


class FilesSearchSkill:
    name = "files.search"
    description = "Busca nombres de archivos dentro de una raíz autorizada."
    risk = RiskLevel.LOW

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        pattern = str(params.get("pattern", "*")).strip() or "*"
        root_value = params.get("root") or str(context.config.allowed_roots[0])
        root = ensure_allowed(Path(str(root_value)), context.config.allowed_roots)
        if not root.exists() or not root.is_dir():
            return SkillResult(False, f"La carpeta no existe: {root}", {"root": str(root)})

        include_hidden = bool(params.get("include_hidden", False))
        max_results = min(
            max(1, int(params.get("max_results", context.config.max_search_results))),
            context.config.max_search_results,
        )
        results: list[str] = []
        try:
            for candidate in root.rglob("*"):
                relative = candidate.relative_to(root)
                if not include_hidden and any(part.startswith(".") for part in relative.parts):
                    continue
                if fnmatch.fnmatch(candidate.name.casefold(), pattern.casefold()):
                    results.append(str(candidate))
                    if len(results) >= max_results:
                        break
        except PermissionError as exc:
            return SkillResult(False, f"Permiso denegado durante la búsqueda: {exc}", {})

        message = f"Encontré {len(results)} resultado(s) para {pattern!r} en {root}."
        return SkillResult(
            True,
            message,
            {"root": str(root), "pattern": pattern, "results": results},
        )
