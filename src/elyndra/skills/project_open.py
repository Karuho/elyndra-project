from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.path_safety import ensure_allowed


class ProjectOpenSkill:
    name = "project.open"
    description = "Abre un proyecto autorizado en VS Code o xdg-open."
    risk = RiskLevel.MEDIUM

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        name = str(params.get("name", "")).strip()
        path_value = params.get("path")
        if name:
            project = context.projects.get(name)
            if project is None:
                return SkillResult(False, f"Proyecto no registrado: {name}", {})
            path_value = project["path"]
        if not path_value:
            return SkillResult(False, "Indica name o path.", {})

        path = ensure_allowed(Path(str(path_value)), context.config.allowed_roots)
        if not path.is_dir():
            return SkillResult(False, f"La carpeta del proyecto no existe: {path}", {})

        launcher = shutil.which("code") or shutil.which("xdg-open")
        if launcher is None:
            return SkillResult(False, "No encontré 'code' ni 'xdg-open'.", {})

        subprocess.Popen(
            [launcher, str(path)],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return SkillResult(True, f"Proyecto abierto: {path}", {"path": str(path)})
