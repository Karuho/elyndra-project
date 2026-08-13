from __future__ import annotations

from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.path_safety import ensure_allowed
from elyndra.skills.text_safety import iter_project_files, looks_textual


class ProjectSearchTextSkill:
    name = "project.search_text"
    description = "Busca texto dentro de archivos legibles de un proyecto registrado."
    risk = RiskLevel.LOW

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        name = str(params.get("name", "")).strip()
        query = str(params.get("query", "")).strip()
        if not name or not query:
            return SkillResult(False, "Indica el proyecto y el texto a buscar.", {})
        project = context.projects.get(name)
        if project is None:
            return SkillResult(False, f"Proyecto no registrado: {name}", {})
        root = ensure_allowed(Path(project["path"]), context.config.allowed_roots)
        if not root.is_dir():
            return SkillResult(False, f"La carpeta no existe: {root}", {})

        max_results = min(
            max(1, int(params.get("max_results", context.config.max_search_results))),
            context.config.max_search_results,
        )
        needle = query.casefold()
        results: list[dict[str, Any]] = []
        scanned = 0

        try:
            for candidate in iter_project_files(root):
                if not candidate.is_file() or not looks_textual(candidate):
                    continue
                scanned += 1
                if scanned > context.config.project_scan_max_files:
                    break
                try:
                    max_bytes = context.config.knowledge_max_file_size_mb * 1024**2
                    if candidate.stat().st_size > max_bytes:
                        continue
                    lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                for line_number, line in enumerate(lines, start=1):
                    if needle in line.casefold():
                        results.append(
                            {
                                "path": str(candidate),
                                "relative_path": str(candidate.relative_to(root)),
                                "line": line_number,
                                "text": line.strip()[:300],
                            }
                        )
                        if len(results) >= max_results:
                            break
                if len(results) >= max_results:
                    break
        except PermissionError as exc:
            return SkillResult(False, f"Permiso denegado durante la búsqueda: {exc}", {})

        lines = [
            f"{item['relative_path']}:{item['line']}: {item['text']}" for item in results
        ]
        message = (
            f"Encontré {len(results)} coincidencia(s) de {query!r} en el proyecto {name}."
        )
        if lines:
            message += "\n" + "\n".join(lines)
        return SkillResult(
            True,
            message,
            {
                "name": name,
                "query": query,
                "results": results,
                "files_scanned": scanned,
            },
        )
