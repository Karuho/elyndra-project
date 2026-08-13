from __future__ import annotations

from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.path_safety import ensure_allowed
from elyndra.skills.text_safety import looks_textual


class FileReadSkill:
    name = "file.read"
    description = "Lee un rango limitado de líneas de un archivo de texto autorizado."
    risk = RiskLevel.LOW

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        path_value = str(params.get("path", "")).strip()
        if not path_value:
            return SkillResult(False, "Falta la ruta del archivo.", {})
        path = ensure_allowed(Path(path_value), context.config.allowed_roots)
        if not path.is_file():
            return SkillResult(False, f"No es un archivo: {path}", {"path": str(path)})
        if not looks_textual(path):
            return SkillResult(
                False,
                "El archivo parece binario y no se mostrará.",
                {"path": str(path)},
            )

        start_line = max(1, int(params.get("start_line", 1)))
        default_end = start_line + context.config.file_read_max_lines - 1
        requested_end = int(params.get("end_line", default_end))
        end_line = max(start_line, requested_end)
        end_line = min(end_line, start_line + context.config.file_read_max_lines - 1)

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return SkillResult(False, f"No pude leer el archivo: {exc}", {"path": str(path)})

        selected = lines[start_line - 1 : end_line]
        if not selected and start_line > len(lines):
            return SkillResult(
                False,
                f"El archivo tiene {len(lines)} líneas; el rango comienza en {start_line}.",
                {"path": str(path), "line_count": len(lines)},
            )

        numbered = [
            f"{number:>5} | {line}"
            for number, line in enumerate(selected, start=start_line)
        ]
        message = "\n".join(numbered) if numbered else "El archivo está vacío."
        return SkillResult(
            True,
            message,
            {
                "path": str(path),
                "start_line": start_line,
                "end_line": start_line + max(0, len(selected) - 1),
                "line_count": len(lines),
            },
        )
