from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.process import run_controlled_process
from elyndra.skills.tool_resolution import resolve_project_tool

_PYTHON_EXTENSIONS = {".py", ".pyi"}
_PROJECT_MARKERS = (
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "requirements.txt",
    "Pipfile",
    "poetry.lock",
    "uv.lock",
)
_DEFAULT_EXCLUDES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "build",
    "dist",
}
_FRAMEWORK_DEPENDENCIES = {
    "django": "Django",
    "fastapi": "FastAPI",
    "flask": "Flask",
    "pydantic": "Pydantic",
    "pytest": "Pytest",
    "sqlalchemy": "SQLAlchemy",
    "typer": "Typer",
}


class PythonProjectInspectSkill:
    name = "python.project_inspect"
    description = "Inspecciona metadatos, estructura y herramientas Python sin ejecutar código."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        target = _resolve_path(params)
        root = _discover_project_root(target)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            context.config.python_tool_timeout_seconds,
            ["inspect-python-project", str(root)],
            "Solo se leen nombres y metadatos acotados; no se importa el proyecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        inventory = _inspect_project(root, settings)
        return SkillResult(
            True,
            _format_inventory(inventory, authorization),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "inventory": inventory,
                **authorization,
            },
        )


class PyProjectValidateSkill:
    name = "python.pyproject_validate"
    description = (
        "Valida pyproject.toml de forma determinista sin instalar ni construir "
        "el proyecto."
    )
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        target = _resolve_path(params)
        root = _discover_project_root(target)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            context.config.python_tool_timeout_seconds,
            ["validate-pyproject", str(root / "pyproject.toml")],
            "Se analiza TOML local; no se invoca el backend de build.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        pyproject = root / "pyproject.toml"
        if not pyproject.is_file():
            return SkillResult(
                True,
                "No existe pyproject.toml; etapa omitida.",
                {
                    "engine": "local-skill",
                    "generated": False,
                    "skill": self.name,
                    "project_root": str(root),
                    "stage_status": "skipped",
                    "issues": [],
                    **authorization,
                },
            )
        report = _validate_pyproject(pyproject)
        ok = not report["errors"]
        heading = "pyproject.toml válido." if ok else "pyproject.toml contiene errores."
        lines = [
            heading,
            "",
            f"- Proyecto: `{root}`",
            f"- Errores: `{len(report['errors'])}`",
            f"- Advertencias: `{len(report['warnings'])}`",
        ]
        for label, items in (("Errores", report["errors"]), ("Advertencias", report["warnings"])):
            if items:
                lines.extend(("", f"{label}:"))
                lines.extend(f"- {item}" for item in items)
        return SkillResult(
            ok,
            "\n".join(lines),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "report": report,
                "stage_status": "passed" if ok else "failed",
                **authorization,
            },
        )


class PythonCompileProjectSkill:
    name = "python.compile_project"
    description = "Compila sintácticamente archivos Python sin importar ni ejecutar el proyecto."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        target = _resolve_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            [sys.executable, "<compilación interna>", "<archivo.py>"],
            "Se usa compile() sin importar módulos ni escribir bytecode.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        files, truncated = _collect_python_files(
            target,
            root=root,
            exclude_paths=settings["exclude_paths"],
            max_files=settings["max_python_files"],
        )
        if truncated:
            return _file_limit_result(self.name, root, settings, authorization)
        started = time.perf_counter()
        issues: list[dict[str, Any]] = []
        deadline = started + settings["timeout_seconds"]
        timed_out = False
        for source in files:
            if time.perf_counter() >= deadline:
                timed_out = True
                break
            try:
                compile(source.read_bytes(), str(source), "exec", dont_inherit=True)
            except (OSError, SyntaxError, ValueError) as exc:
                issues.append(
                    {
                        "path": source.relative_to(root).as_posix(),
                        "error": _bounded_text(str(exc), 500),
                    }
                )
                if len(issues) >= 100:
                    break
        duration_ms = round((time.perf_counter() - started) * 1000)
        ok = not issues and not timed_out
        heading = (
            "Compilación Python correcta."
            if ok
            else "La compilación Python encontró problemas."
        )
        lines = [
            heading,
            "",
            f"- Proyecto: `{root}`",
            f"- Archivos examinados: `{len(files)}`",
            f"- Fallos: `{len(issues)}`",
            f"- Timeout: `{'sí' if timed_out else 'no'}`",
            f"- Duración: `{duration_ms} ms`",
        ]
        if issues:
            lines.extend(("", "Problemas:"))
            lines.extend(f"- `{item['path']}`: {item['error']}" for item in issues[:20])
        return SkillResult(
            ok,
            "\n".join(lines),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "scanned_files": len(files),
                "failed_files": len(issues),
                "issues": issues,
                "duration_ms": duration_ms,
                "timed_out": timed_out,
                "shell": False,
                **authorization,
            },
        )


class RuffCheckSkill:
    name = "ruff.check"
    description = "Ejecuta Ruff sin aplicar fixes y con configuración acotada al proyecto."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root, settings, decision = _tool_approval_context(context, params)
        tool = resolve_project_tool(root, "ruff")
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            _ruff_argv(root, tool.path, settings),
            "Ruff analiza archivos; no aplica cambios ni descarga dependencias.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _run_python_tool(context, params, skill_name=self.name, tool_name="ruff")


class MypyCheckSkill:
    name = "mypy.check"
    description = "Ejecuta mypy con caché temporal y configuración dentro del proyecto."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root, settings, decision = _tool_approval_context(context, params)
        tool = resolve_project_tool(root, "mypy")
        action = _mypy_argv(root, tool.path, settings, cache_dir="<temporal>")
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            action,
            "mypy puede cargar plugins declarados por el proyecto; requiere aprobación explícita.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _run_python_tool(context, params, skill_name=self.name, tool_name="mypy")


class PytestRunSkill:
    name = "pytest.run"
    description = "Ejecuta Pytest del proyecto con argumentos fijos y sin caché persistente."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root, settings, decision = _tool_approval_context(context, params)
        tool = resolve_project_tool(root, "pytest")
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            _pytest_argv(root, tool.path, settings),
            "Pytest ejecuta código de pruebas y del proyecto dentro de la raíz autorizada.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _run_python_tool(context, params, skill_name=self.name, tool_name="pytest")


class PythonVerifyProjectSkill:
    name = "python.verify_project"
    description = "Encadena pyproject, compilación, Ruff, mypy y Pytest con historial auditable."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        target = _resolve_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["verify-python-project", *_planned_stages(settings), str(root)],
            "Puede ejecutar analizadores y pruebas; cada etapa usa argumentos fijos y auditados.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        started = time.perf_counter()
        run_id = context.verification_runs.start(
            toolchain="python",
            project_root=root,
            actor=context.actor,
            profile_id=(settings["profile"] or {}).get("id"),
            plan={"stages": _planned_stages(settings), "settings": _public_settings(settings)},
        )
        stages: list[dict[str, Any]] = []
        inventory_result = PythonProjectInspectSkill().execute(context, params)
        inventory = inventory_result.data.get("inventory", {})
        stages.append(_stage_from_result("inspect", inventory_result))
        stage_specs = (
            ("pyproject", "pyproject_enabled", PyProjectValidateSkill()),
            ("compile", "compile_enabled", PythonCompileProjectSkill()),
            ("ruff", "ruff_enabled", RuffCheckSkill()),
            ("mypy", "mypy_enabled", MypyCheckSkill()),
            ("pytest", "pytest_enabled", PytestRunSkill()),
        )
        for stage_name, setting_name, skill in stage_specs:
            if not settings[setting_name]:
                stages.append({"name": stage_name, "status": "skipped", "reason": "desactivado"})
                continue
            if stage_name == "pytest" and int(inventory.get("test_files", 0)) == 0:
                stages.append(
                    {"name": "pytest", "status": "skipped", "reason": "no se detectaron tests"}
                )
                continue
            result = skill.execute(context, params)
            stage = _stage_from_result(stage_name, result)
            if settings["require_tools"] and stage["status"] == "unavailable":
                stage["status"] = "failed"
                stage["reason"] = f"herramienta obligatoria ausente: {stage_name}"
            stages.append(stage)
            if settings["fail_fast"] and stage["status"] == "failed":
                break
        status = _verification_status(stages)
        duration_ms = round((time.perf_counter() - started) * 1000)
        summary = {
            "stages": stages,
            "counts": _stage_counts(stages),
            "inventory": inventory,
            "settings": _public_settings(settings),
        }
        context.verification_runs.finish(
            run_id,
            status=status,
            duration_ms=duration_ms,
            summary=summary,
        )
        ok = status != "failed"
        return SkillResult(
            ok,
            _format_verification(root, run_id, status, duration_ms, stages),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "verification_run_id": run_id,
                "verification_status": status,
                "duration_ms": duration_ms,
                "stages": stages,
                "summary": summary,
                **authorization,
            },
        )


def _tool_approval_context(
    context: SkillContext,
    params: dict[str, Any],
) -> tuple[Path, dict[str, Any], Any]:
    target = _resolve_path(params)
    root = _discover_project_root(target)
    settings = _pipeline_settings(context, root, params)
    return root, settings, context.authorization.project(root)


def _run_python_tool(
    context: SkillContext,
    params: dict[str, Any],
    *,
    skill_name: str,
    tool_name: str,
) -> SkillResult:
    target = _resolve_existing_path(params)
    root = _discover_project_root(target)
    settings = _pipeline_settings(context, root, params)
    authorization = _authorize_project(context, root, params, settings=settings)
    tool = resolve_project_tool(root, tool_name)
    if tool.path is None:
        return _tool_unavailable(skill_name, root, tool_name, authorization)
    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        if tool_name == "ruff":
            argv = _ruff_argv(root, tool.path, settings)
        elif tool_name == "mypy":
            temporary = tempfile.TemporaryDirectory(prefix="elyndra-mypy-")
            argv = _mypy_argv(root, tool.path, settings, cache_dir=temporary.name)
        else:
            argv = _pytest_argv(root, tool.path, settings)
        result = run_controlled_process(
            argv,
            cwd=root,
            timeout_seconds=settings["timeout_seconds"],
            max_output_chars=settings["max_output_chars"],
        )
    finally:
        if temporary is not None:
            temporary.cleanup()
    ok = result.returncode == 0 and not result.timed_out
    labels = {"ruff": "Ruff", "mypy": "mypy", "pytest": "Pytest"}
    heading = (
        f"{labels[tool_name]} finalizó correctamente."
        if ok
        else f"{labels[tool_name]} encontró problemas."
    )
    message = "\n".join(
        (
            heading,
            "",
            f"- Proyecto: `{root}`",
            f"- Herramienta: `{tool.path}`",
            f"- Exit code: `{result.returncode}`",
            f"- Timeout: `{'sí' if result.timed_out else 'no'}`",
            f"- Duración: `{result.duration_ms} ms`",
            "",
            result.output.strip() or "Sin salida.",
        )
    )
    return SkillResult(
        ok,
        message,
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "tool_path": str(tool.path),
            "tool_source": tool.source,
            "command_argv": argv,
            "cwd": result.cwd,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
            "timed_out": result.timed_out,
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
            "shell": False,
            **authorization,
        },
    )


def _ruff_argv(root: Path, tool: Path | None, settings: dict[str, Any]) -> list[str]:
    argv = [str(tool or "ruff"), "check", ".", "--no-cache", "--output-format", "concise"]
    config = _validated_config(root, settings.get("ruff_config", ""), label="Ruff")
    if config:
        argv.extend(("--config", config))
    return argv


def _mypy_argv(
    root: Path,
    tool: Path | None,
    settings: dict[str, Any],
    *,
    cache_dir: str,
) -> list[str]:
    argv = [
        str(tool or "mypy"),
        ".",
        "--no-color-output",
        "--no-pretty",
        "--show-error-codes",
        "--cache-dir",
        cache_dir,
    ]
    config = _validated_config(root, settings.get("mypy_config", ""), label="mypy")
    if config:
        argv.extend(("--config-file", config))
    return argv


def _pytest_argv(root: Path, tool: Path | None, settings: dict[str, Any]) -> list[str]:
    argv = [str(tool or "pytest"), "-q", "-p", "no:cacheprovider"]
    target = _validated_target(root, settings.get("pytest_path", ""))
    if target:
        argv.append(target)
    if settings.get("fail_fast"):
        argv.extend(("--maxfail", "1"))
    return argv


def _validated_config(root: Path, raw: Any, *, label: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    candidate = Path(value)
    resolved = (
        candidate.resolve(strict=False)
        if candidate.is_absolute()
        else (root / candidate).resolve(strict=False)
    )
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"La configuración {label} debe permanecer dentro del proyecto.")
    if not resolved.is_file():
        raise ValueError(f"Configuración {label} no encontrada: {resolved}")
    return resolved.relative_to(root).as_posix()


def _validated_target(root: Path, raw: Any) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    candidate = Path(value)
    resolved = (
        candidate.resolve(strict=False)
        if candidate.is_absolute()
        else (root / candidate).resolve(strict=False)
    )
    if resolved != root and root not in resolved.parents:
        raise ValueError("La ruta de Pytest debe permanecer dentro del proyecto.")
    if not resolved.exists():
        raise ValueError(f"Ruta de Pytest no encontrada: {resolved}")
    return resolved.relative_to(root).as_posix() or "."


def _resolve_path(params: dict[str, Any]) -> Path:
    raw = str(params.get("path", "")).strip()
    if not raw:
        raise ValueError("Falta el parámetro path.")
    return Path(raw).expanduser().resolve(strict=False)


def _resolve_existing_path(params: dict[str, Any]) -> Path:
    path = _resolve_path(params)
    if not path.exists():
        raise ValueError(f"La ruta no existe: {path}")
    mode = path.stat().st_mode
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise ValueError(f"La ruta no es un archivo o directorio regular: {path}")
    return path


def _discover_project_root(path: Path) -> Path:
    start = path if path.is_dir() else path.parent
    current = start.resolve(strict=False)
    while True:
        if any((current / marker).is_file() for marker in _PROJECT_MARKERS):
            return current
        if current.parent == current:
            return start.resolve(strict=False)
        current = current.parent


def _authorize_project(
    context: SkillContext,
    root: Path,
    params: dict[str, Any],
    *,
    settings: dict[str, Any],
) -> dict[str, Any]:
    decision = context.authorization.project(
        root,
        allow_once=params.get("allow_root_once") is True,
        source=str(params.get("authorization_source") or "explicit_approval"),
    )
    if not decision.allowed:
        raise PermissionError(
            f"{decision.reason} Autorízalo solo para esta ejecución con --allow-root-once."
        )
    profile = settings.get("profile") or {}
    return {
        **decision.as_data(),
        "timeout_seconds": settings["timeout_seconds"],
        "project_profile_id": profile.get("id"),
        "project_profile_applied": bool(profile),
    }


def _pipeline_settings(
    context: SkillContext,
    root: Path,
    params: dict[str, Any],
) -> dict[str, Any]:
    effective = context.python_profiles.effective_settings(
        root,
        default_timeout_seconds=context.config.python_tool_timeout_seconds,
        default_max_output_chars=context.config.python_tool_max_output_chars,
    )
    profile = effective["profile"] or {}
    return {
        "profile": effective["profile"],
        "timeout_seconds": int(effective["timeout_seconds"]),
        "max_output_chars": int(effective["max_output_chars"]),
        "max_python_files": _bounded_files(
            params.get("max_files"),
            int(effective["max_python_files"]),
        ),
        "exclude_paths": list(effective["exclude_paths"]),
        "pyproject_enabled": _setting(params, profile, "pyproject_enabled", True),
        "compile_enabled": _setting(params, profile, "compile_enabled", True),
        "ruff_enabled": _setting(params, profile, "ruff_enabled", True),
        "mypy_enabled": _setting(params, profile, "mypy_enabled", True),
        "pytest_enabled": _setting(params, profile, "pytest_enabled", True),
        "ruff_config": str(
            params.get("ruff_config")
            if params.get("ruff_config") is not None
            else profile.get("ruff_config", "")
        ).strip(),
        "mypy_config": str(
            params.get("mypy_config")
            if params.get("mypy_config") is not None
            else profile.get("mypy_config", "")
        ).strip(),
        "pytest_path": str(
            params.get("pytest_path")
            if params.get("pytest_path") is not None
            else profile.get("pytest_path", "")
        ).strip(),
        "fail_fast": _setting(params, profile, "fail_fast", False),
        "require_tools": _setting(params, profile, "require_tools", False),
    }


def _bounded_files(value: Any, default: int) -> int:
    resolved = default if value is None else int(value)
    if not 1 <= resolved <= 20_000:
        raise ValueError("max_files debe estar entre 1 y 20000.")
    return resolved


def _setting(
    params: dict[str, Any],
    profile: dict[str, Any],
    name: str,
    default: bool,
) -> bool:
    if name in params:
        return params[name] is True
    if name in profile:
        return bool(profile[name])
    return default


def _collect_python_files(
    target: Path,
    *,
    root: Path,
    exclude_paths: list[str],
    max_files: int,
) -> tuple[list[Path], bool]:
    if target.is_file():
        return ([target] if target.suffix.casefold() in _PYTHON_EXTENSIONS else []), False
    excluded = {
        (root / relative).resolve(strict=False)
        for relative in (*_DEFAULT_EXCLUDES, *exclude_paths)
    }
    files: list[Path] = []
    for current, directories, filenames in os.walk(target, followlinks=False):
        current_path = Path(current).resolve(strict=False)
        directories[:] = [
            name
            for name in directories
            if not _is_excluded((current_path / name).resolve(strict=False), excluded)
        ]
        for filename in sorted(filenames):
            candidate = (current_path / filename).resolve(strict=False)
            if candidate != root and root not in candidate.parents:
                continue
            if candidate.suffix.casefold() not in _PYTHON_EXTENSIONS:
                continue
            if _is_excluded(candidate, excluded):
                continue
            try:
                mode = candidate.stat().st_mode
            except OSError:
                continue
            if not stat.S_ISREG(mode):
                continue
            files.append(candidate)
            if len(files) > max_files:
                return files, True
    files.sort(key=lambda item: item.relative_to(root).as_posix().casefold())
    return files, False


def _is_excluded(path: Path, excluded: set[Path]) -> bool:
    return any(path == item or item in path.parents for item in excluded)


def _inspect_project(root: Path, settings: dict[str, Any]) -> dict[str, Any]:
    files, truncated = _collect_python_files(
        root,
        root=root,
        exclude_paths=settings["exclude_paths"],
        max_files=settings["max_python_files"],
    )
    pyproject = _read_pyproject(root / "pyproject.toml")
    dependencies = set(pyproject.get("dependencies", []))
    optional = pyproject.get("optional_dependencies", {})
    for values in optional.values():
        dependencies.update(values)
    normalized_dependencies = {_dependency_name(value) for value in dependencies}
    frameworks = sorted(
        label
        for package, label in _FRAMEWORK_DEPENDENCIES.items()
        if package in normalized_dependencies
    )
    tools = {
        name: _tool_data(resolve_project_tool(root, name))
        for name in ("ruff", "mypy", "pytest")
    }
    test_files = sum(
        1
        for path in files
        if path.name.startswith("test_")
        or path.name.endswith("_test.py")
        or "tests" in path.relative_to(root).parts
    )
    return {
        "project_root": str(root),
        "python_files": len(files),
        "test_files": test_files,
        "scan_truncated": truncated,
        "pyproject": bool(pyproject),
        "project_name": str(pyproject.get("name") or ""),
        "requires_python": str(pyproject.get("requires_python") or ""),
        "build_backend": str(pyproject.get("build_backend") or ""),
        "dependencies_count": len(pyproject.get("dependencies", [])),
        "optional_dependency_groups": sorted(pyproject.get("optional_dependencies", {})),
        "script_names": sorted(pyproject.get("scripts", [])),
        "frameworks": frameworks,
        "src_layout": (root / "src").is_dir(),
        "requirements_files": sorted(path.name for path in root.glob("requirements*.txt")),
        "setup_cfg": (root / "setup.cfg").is_file(),
        "setup_py": (root / "setup.py").is_file(),
        "tox_ini": (root / "tox.ini").is_file(),
        "noxfile": (root / "noxfile.py").is_file(),
        "tools": tools,
    }


def _read_pyproject(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > 1_000_000:
        return {}
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    project = payload.get("project") if isinstance(payload.get("project"), dict) else {}
    build = payload.get("build-system") if isinstance(payload.get("build-system"), dict) else {}
    optional = project.get("optional-dependencies", {})
    optional_dependencies = {
        str(name): [str(value) for value in values]
        for name, values in optional.items()
        if isinstance(values, list)
    } if isinstance(optional, dict) else {}
    scripts = project.get("scripts", {})
    return {
        "name": str(project.get("name") or ""),
        "requires_python": str(project.get("requires-python") or ""),
        "dependencies": [str(value) for value in project.get("dependencies", [])]
        if isinstance(project.get("dependencies"), list)
        else [],
        "optional_dependencies": optional_dependencies,
        "scripts": sorted(str(name) for name in scripts) if isinstance(scripts, dict) else [],
        "build_backend": str(build.get("build-backend") or ""),
    }


def _validate_pyproject(path: Path) -> dict[str, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if path.stat().st_size > 1_000_000:
        return {"errors": ["pyproject.toml supera 1 MiB."], "warnings": []}
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        return {"errors": [f"TOML inválido: {exc}"], "warnings": []}
    project = payload.get("project")
    build = payload.get("build-system")
    if project is None:
        warnings.append("Falta la tabla [project].")
    elif not isinstance(project, dict):
        errors.append("[project] debe ser una tabla TOML.")
    else:
        if not str(project.get("name") or "").strip():
            warnings.append("[project].name no está definido.")
        dependencies = project.get("dependencies", [])
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) for item in dependencies
        ):
            errors.append("[project].dependencies debe ser una lista de cadenas.")
        scripts = project.get("scripts", {})
        if not isinstance(scripts, dict):
            errors.append("[project].scripts debe ser una tabla.")
    if build is not None:
        if not isinstance(build, dict):
            errors.append("[build-system] debe ser una tabla TOML.")
        else:
            requires = build.get("requires", [])
            if not isinstance(requires, list) or not all(
                isinstance(item, str) for item in requires
            ):
                errors.append("[build-system].requires debe ser una lista de cadenas.")
            if requires and not str(build.get("build-backend") or "").strip():
                warnings.append("[build-system] declara requires sin build-backend.")
    return {"errors": errors, "warnings": warnings}


def _dependency_name(value: str) -> str:
    clean = value.strip().casefold()
    for separator in ("[", " ", "<", ">", "=", "!", "~", ";"):
        clean = clean.split(separator, 1)[0]
    return clean.replace("_", "-")


def _tool_data(resolution: Any) -> dict[str, Any]:
    return {
        "available": resolution.path is not None,
        "path": str(resolution.path) if resolution.path is not None else "",
        "source": resolution.source,
    }


def _approval_details(
    skill_name: str,
    root: Path,
    scope: str,
    source: str,
    timeout_seconds: int,
    action: list[str],
    risk_detail: str,
) -> dict[str, Any]:
    summary = "\n".join(
        (
            f"Skill: {skill_name}",
            f"Proyecto: {root}",
            f"Alcance de autorización: {scope}",
            f"Origen de autorización: {source}",
            f"Riesgo: medio. {risk_detail}",
            f"Timeout: {timeout_seconds} segundos",
            f"Acción exacta: {' '.join(action)}",
        )
    )
    return {
        "approval_summary": summary,
        "project_root": str(root),
        "resolved_path": str(root),
        "authorization_scope": scope,
        "authorization_source": source,
        "timeout_seconds": timeout_seconds,
        "action_argv": action,
    }


def _tool_unavailable(
    skill_name: str,
    root: Path,
    tool: str,
    authorization: dict[str, Any],
) -> SkillResult:
    return SkillResult(
        False,
        f"No se encontró la herramienta requerida: {tool}.",
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "tool": tool,
            "stage_status": "unavailable",
            **authorization,
        },
    )


def _file_limit_result(
    skill_name: str,
    root: Path,
    settings: dict[str, Any],
    authorization: dict[str, Any],
) -> SkillResult:
    return SkillResult(
        False,
        (
            "El proyecto supera el límite Python configurado "
            f"({settings['max_python_files']} archivos)."
        ),
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "scan_truncated": True,
            **authorization,
        },
    )


def _stage_from_result(name: str, result: SkillResult) -> dict[str, Any]:
    explicit = str(result.data.get("stage_status") or "")
    unavailable = result.message.startswith("No se encontró la herramienta requerida:")
    status = explicit or ("passed" if result.ok else ("unavailable" if unavailable else "failed"))
    return {
        "name": name,
        "status": status,
        "returncode": result.data.get("returncode"),
        "duration_ms": int(result.data.get("duration_ms") or 0),
        "timed_out": bool(result.data.get("timed_out", False)),
        "scanned_files": result.data.get("scanned_files"),
        "failed_files": result.data.get("failed_files"),
        "reason": result.message.splitlines()[0],
    }


def _verification_status(stages: list[dict[str, Any]]) -> str:
    if any(item["status"] == "failed" for item in stages):
        return "failed"
    if any(item["status"] == "unavailable" for item in stages):
        return "partial"
    return "passed"


def _stage_counts(stages: list[dict[str, Any]]) -> dict[str, int]:
    statuses = ("passed", "failed", "skipped", "unavailable")
    return {status: sum(item["status"] == status for item in stages) for status in statuses}


def _planned_stages(settings: dict[str, Any]) -> list[str]:
    stages = ["inspect"]
    for key, name in (
        ("pyproject_enabled", "pyproject"),
        ("compile_enabled", "compile"),
        ("ruff_enabled", "ruff"),
        ("mypy_enabled", "mypy"),
        ("pytest_enabled", "pytest"),
    ):
        if settings[key]:
            stages.append(name)
    return stages


def _public_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in settings.items() if key != "profile"}


def _format_inventory(
    inventory: dict[str, Any],
    authorization: dict[str, Any],
) -> str:
    frameworks = ", ".join(inventory["frameworks"]) or "no detectado"
    return "\n".join(
        (
            "Inspección Python completada sin ejecutar código.",
            "",
            f"- Proyecto: `{inventory['project_root']}`",
            f"- Archivos Python: `{inventory['python_files']}`",
            f"- Tests detectados: `{inventory['test_files']}`",
            f"- pyproject.toml: `{'sí' if inventory['pyproject'] else 'no'}`",
            f"- Paquete: `{inventory['project_name'] or '-'}`",
            f"- Python requerido: `{inventory['requires_python'] or '-'}`",
            f"- Backend de build: `{inventory['build_backend'] or '-'}`",
            f"- Frameworks/librerías: `{frameworks}`",
            f"- Layout src/: `{'sí' if inventory['src_layout'] else 'no'}`",
            f"- Autorización: `{authorization['authorization_scope']}`",
        )
    )


def _format_verification(
    root: Path,
    run_id: str,
    status: str,
    duration_ms: int,
    stages: list[dict[str, Any]],
) -> str:
    labels = {"passed": "correcta", "partial": "parcial", "failed": "con errores"}
    lines = [
        f"Verificación Python {labels[status]}.",
        "",
        f"- Proyecto: `{root}`",
        f"- Ejecución: `{run_id}`",
        f"- Estado: `{status}`",
        f"- Duración: `{duration_ms} ms`",
        "",
        "Etapas:",
    ]
    for stage in stages:
        reason = f" — {stage['reason']}" if stage.get("reason") else ""
        lines.append(f"- {stage['name']}: `{stage['status']}`{reason}")
    return "\n".join(lines)


def _bounded_text(value: str, limit: int) -> str:
    clean = value.replace("\x00", "").strip()
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"
