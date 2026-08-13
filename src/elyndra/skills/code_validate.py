from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from elyndra.documents import DOCUMENT_EXTENSIONS, process_document
from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.path_safety import ensure_allowed


class CodeValidateSkill:
    name = "code.validate"
    description = (
        "Valida sintaxis y estructura mediante parsers locales y linters explícitos."
    )
    risk = RiskLevel.LOW

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        if not params.get("path"):
            return SkillResult(False, "Falta la ruta del archivo.", {})
        path = ensure_allowed(Path(str(params["path"])), context.config.allowed_roots)
        if not path.is_file():
            return SkillResult(False, f"No es un archivo: {path}", {"path": str(path)})

        suffix = path.suffix.lower()
        if suffix in {
            ".json",
            ".toml",
            ".xml",
            ".yaml",
            ".yml",
            ".csv",
            ".php",
            *DOCUMENT_EXTENSIONS,
        }:
            return self._validate_document(path)

        command = self._command_for(path)
        if command is None:
            return SkillResult(
                False,
                f"Formato no soportado en 0.1: {suffix or '(sin extensión)' }.",
                {"path": str(path)},
            )
        if shutil.which(command[0]) is None and Path(command[0]).name == command[0]:
            return SkillResult(False, f"No está instalado el validador: {command[0]}", {})

        try:
            completed = subprocess.run(
                command,
                shell=False,
                capture_output=True,
                text=True,
                timeout=context.config.command_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return SkillResult(False, "La validación superó el tiempo permitido.", {})

        output = (completed.stdout + completed.stderr).strip()
        ok = completed.returncode == 0
        return SkillResult(
            ok,
            "Sintaxis válida." if ok else "La validación encontró errores.",
            {
                "path": str(path),
                "returncode": completed.returncode,
                "output": output[-4000:],
                "command": command,
            },
        )

    @staticmethod
    def _command_for(path: Path) -> list[str] | None:
        suffix = path.suffix.lower()
        if suffix == ".py":
            return [sys.executable, "-m", "py_compile", str(path)]
        if suffix in {".js", ".mjs", ".cjs"}:
            return ["node", "--check", str(path)]
        return None

    @staticmethod
    def _validate_document(path: Path) -> SkillResult:
        try:
            result = process_document(path.name, path.read_bytes())
        except (OSError, ValueError) as exc:
            return SkillResult(
                False,
                f"No se pudo procesar el archivo: {exc}",
                {"path": str(path)},
            )
        status = result.validation_status
        messages = result.diagnostics.get("messages", [])
        detail = " ".join(str(value) for value in messages)
        labels = {
            "valid": "Validación determinista correcta.",
            "invalid": "La validación encontró errores.",
            "partial": "Solo fue posible una validación parcial.",
            "unavailable": "El validador requerido no está disponible.",
            "not_checked": "El archivo fue leído, pero no existe un validador activo.",
        }
        return SkillResult(
            status == "valid",
            labels.get(status, "No se pudo determinar el estado de validación."),
            {
                "path": str(path),
                "validation_status": status,
                "extraction_status": result.extraction_status,
                "processor": result.processor,
                "diagnostics": result.diagnostics,
                "detail": detail,
            },
        )
