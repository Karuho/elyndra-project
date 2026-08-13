from __future__ import annotations

import shutil
import subprocess
from collections import Counter
from contextlib import suppress
from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.path_safety import ensure_allowed
from elyndra.skills.text_safety import iter_project_files


class ProjectInspectSkill:
    name = "project.inspect"
    description = "Resume estructura, lenguajes, tamaño y estado Git de un proyecto registrado."
    risk = RiskLevel.LOW

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        name = str(params.get("name", "")).strip()
        project = context.projects.get(name)
        if project is None:
            return SkillResult(False, f"Proyecto no registrado: {name}", {})
        root = ensure_allowed(Path(project["path"]), context.config.allowed_roots)
        if not root.is_dir():
            return SkillResult(False, f"La carpeta no existe: {root}", {})

        suffixes: Counter[str] = Counter()
        top_level: list[str] = []
        total_size = 0
        file_count = 0
        truncated = False

        try:
            top_level = sorted(item.name for item in root.iterdir() if item.name != ".git")[:50]
            for candidate in iter_project_files(root):
                if not candidate.is_file():
                    continue
                file_count += 1
                if file_count > context.config.project_scan_max_files:
                    truncated = True
                    file_count -= 1
                    break
                with suppress(OSError):
                    total_size += candidate.stat().st_size
                label = candidate.suffix.casefold() or f"[{candidate.name}]"
                suffixes[label] += 1
        except PermissionError as exc:
            return SkillResult(False, f"Permiso denegado al inspeccionar: {exc}", {})

        git = _git_status(root, context.config.command_timeout_seconds)
        common = suffixes.most_common(12)
        language_text = ", ".join(f"{suffix}={count}" for suffix, count in common) or "sin archivos"
        top_text = ", ".join(top_level) or "vacío"
        message = (
            f"Proyecto {name}: {file_count} archivo(s), {total_size / 1024**2:.2f} MB.\n"
            f"Tipos principales: {language_text}.\n"
            f"Nivel superior: {top_text}.\n"
            f"Git: {git['summary']}."
        )
        if truncated:
            message += f"\nEscaneo limitado a {context.config.project_scan_max_files} archivos."

        return SkillResult(
            True,
            message,
            {
                "name": name,
                "path": str(root),
                "file_count": file_count,
                "size_bytes": total_size,
                "extensions": dict(common),
                "top_level": top_level,
                "git": git,
                "truncated": truncated,
            },
        )


def _git_status(root: Path, timeout: int) -> dict[str, Any]:
    if not (root / ".git").exists() or shutil.which("git") is None:
        return {"is_repository": False, "summary": "no es repositorio Git"}
    try:
        branch = subprocess.run(
            ["git", "-C", str(root), "branch", "--show-current"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"is_repository": True, "summary": "consulta Git agotó el tiempo"}

    branch_name = branch.stdout.strip() or "HEAD separado"
    changes = [line for line in status.stdout.splitlines() if line.strip()]
    summary = f"rama {branch_name}, {'limpio' if not changes else f'{len(changes)} cambio(s)'}"
    return {
        "is_repository": True,
        "branch": branch_name,
        "changes": len(changes),
        "summary": summary,
    }
