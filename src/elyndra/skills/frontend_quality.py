from __future__ import annotations

import json
import stat
import time
from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.process import run_controlled_process
from elyndra.skills.tool_resolution import resolve_project_tool

_PROJECT_MARKERS = (
    "package.json",
    "tsconfig.json",
    "angular.json",
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.ts",
    "webpack.config.js",
)
_FRAMEWORK_DEPENDENCIES = {
    "@angular/core": "angular",
    "astro": "astro",
    "next": "next",
    "nuxt": "nuxt",
    "react": "react",
    "svelte": "svelte",
    "vite": "vite",
    "vue": "vue",
}
_LOCKFILES = (
    ("npm", "package-lock.json"),
    ("pnpm", "pnpm-lock.yaml"),
    ("yarn", "yarn.lock"),
    ("bun", "bun.lockb"),
)
_ESLINT_CONFIGS = (
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    "eslint.config.ts",
    ".eslintrc",
    ".eslintrc.json",
    ".eslintrc.js",
    ".eslintrc.cjs",
)
_STYLELINT_CONFIGS = (
    "stylelint.config.js",
    "stylelint.config.mjs",
    "stylelint.config.cjs",
    ".stylelintrc",
    ".stylelintrc.json",
    ".stylelintrc.js",
    ".stylelintrc.cjs",
)
_FRAMEWORK_PRESETS = {
    "auto",
    "generic",
    "angular",
    "vite",
    "react",
    "vue",
    "svelte",
    "astro",
    "next",
    "nuxt",
}
_VITE_CONFIGS = (
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.cjs",
    "vite.config.ts",
    "vite.config.mts",
    "vite.config.cts",
)


class EslintLintSkill:
    name = "eslint.lint"
    description = "Ejecuta ESLint local o global sin npm, npx ni shell."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _project_root(params)
        settings = _settings(context, root, params)
        decision = context.authorization.project(root)
        tool = resolve_project_tool(root, "eslint")
        argv = _eslint_argv(root, tool.path or Path("eslint"), settings)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            argv,
            "ESLint puede cargar configuración y plugins locales del proyecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _existing_project_root(params)
        settings = _settings(context, root, params)
        authorization = _authorize(context, root, params, settings)
        tool = resolve_project_tool(root, "eslint")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "eslint", authorization)
        argv = _eslint_argv(root, tool.path, settings)
        return _run_tool(
            self.name,
            "ESLint",
            root,
            tool.path,
            tool.source,
            argv,
            settings,
            authorization,
        )


class StylelintLintSkill:
    name = "stylelint.lint"
    description = "Ejecuta Stylelint local o global sin npm, npx ni shell."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _project_root(params)
        settings = _settings(context, root, params)
        decision = context.authorization.project(root)
        tool = resolve_project_tool(root, "stylelint")
        argv = _stylelint_argv(root, tool.path or Path("stylelint"), settings)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            argv,
            "Stylelint puede cargar configuración y plugins locales del proyecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _existing_project_root(params)
        settings = _settings(context, root, params)
        authorization = _authorize(context, root, params, settings)
        tool = resolve_project_tool(root, "stylelint")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "stylelint", authorization)
        argv = _stylelint_argv(root, tool.path, settings)
        return _run_tool(
            self.name,
            "Stylelint",
            root,
            tool.path,
            tool.source,
            argv,
            settings,
            authorization,
        )


class FrameworkValidateSkill:
    name = "web.framework_validate"
    description = "Comprueba configuración Angular, Vite y frontend sin ejecutar código."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _project_root(params)
        settings = _settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["inspect-frontend-config", str(root)],
            "Solo se leen manifiestos y archivos JSON acotados; no se ejecuta código.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _existing_project_root(params)
        settings = _settings(context, root, params)
        authorization = _authorize(context, root, params, settings)
        started = time.perf_counter()
        report = inspect_frontend_project(root, framework_preset=settings["framework_preset"])
        duration_ms = round((time.perf_counter() - started) * 1000)
        errors = report["errors"]
        warnings = report["warnings"]
        ok = not errors
        lines = [
            "Configuración frontend válida." if ok else "Configuración frontend con problemas.",
            "",
            f"- Proyecto: `{root}`",
            f"- Framework principal: `{report['primary_framework']}`",
            f"- Gestor de paquetes: `{report['package_manager'] or '-'}`",
            f"- Workspaces: `{report['workspace_count']}`",
            f"- Errores: `{len(errors)}`",
            f"- Advertencias: `{len(warnings)}`",
            f"- Duración: `{duration_ms} ms`",
        ]
        if errors:
            lines.extend(("", "Errores:", *(f"- {item}" for item in errors[:50])))
        if warnings:
            lines.extend(("", "Advertencias:", *(f"- {item}" for item in warnings[:50])))
        return SkillResult(
            ok,
            "\n".join(lines),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "framework_report": report,
                "duration_ms": duration_ms,
                "returncode": 0 if ok else 1,
                "timed_out": False,
                "stdout_truncated": len(errors) > 50 or len(warnings) > 50,
                "stderr_truncated": False,
                "shell": False,
                **authorization,
            },
        )


def inspect_frontend_project(
    root: Path,
    *,
    framework_preset: str = "auto",
) -> dict[str, Any]:
    if framework_preset not in _FRAMEWORK_PRESETS:
        allowed = ", ".join(sorted(_FRAMEWORK_PRESETS))
        raise ValueError(f"framework_preset debe ser uno de: {allowed}.")
    package = _read_json(root / "package.json", max_bytes=2_000_000)
    dependencies = _dependency_names(package)
    detected = {
        framework
        for dependency, framework in _FRAMEWORK_DEPENDENCIES.items()
        if dependency in dependencies
    }
    if (root / "angular.json").is_file():
        detected.add("angular")
    if any((root / name).is_file() for name in _VITE_CONFIGS):
        detected.add("vite")
    locks = [manager for manager, filename in _LOCKFILES if (root / filename).is_file()]
    scripts = _mapping_keys(package.get("scripts"))
    workspace_names = _workspace_names(package)
    angular = _angular_details(root)
    errors: list[str] = []
    warnings: list[str] = []

    if len(locks) > 1:
        warnings.append("Se detectaron varios lockfiles: " + ", ".join(locks) + ".")
    if "angular" in detected:
        if not (root / "angular.json").is_file():
            errors.append("Angular está declarado, pero falta angular.json.")
        elif angular["invalid_json"]:
            errors.append("angular.json no contiene JSON válido.")
        elif angular["project_count"] == 0:
            warnings.append("angular.json no declara proyectos.")
        if "@angular/core" not in dependencies:
            warnings.append("angular.json existe, pero @angular/core no aparece en package.json.")
    if "vite" in detected:
        if not any((root / name).is_file() for name in _VITE_CONFIGS):
            warnings.append("Vite está declarado, pero no se encontró vite.config.*.")
        if "vite" not in dependencies:
            warnings.append("vite.config.* existe, pero vite no aparece en package.json.")
    if (
        any(path.suffix.casefold() in {".ts", ".tsx"} for path in root.glob("*"))
        and not (root / "tsconfig.json").is_file()
    ):
        warnings.append("Hay TypeScript en la raíz, pero falta tsconfig.json.")
    if framework_preset not in {"auto", "generic"} and framework_preset not in detected:
        errors.append(
            f"El perfil exige {framework_preset}, pero no se detectó ese framework."
        )
    if package and not locks:
        warnings.append("package.json existe, pero no se detectó lockfile.")
    if package and not scripts:
        warnings.append("package.json no declara scripts.")

    primary = _primary_framework(detected, framework_preset)
    eslint = resolve_project_tool(root, "eslint")
    stylelint = resolve_project_tool(root, "stylelint")
    return {
        "framework_preset": framework_preset,
        "detected_frameworks": sorted(detected),
        "primary_framework": primary,
        "package_manager": locks[0] if len(locks) == 1 else "",
        "lockfiles": locks,
        "scripts": scripts,
        "workspaces": workspace_names,
        "workspace_count": len(workspace_names),
        "angular": angular,
        "vite_config": _first_existing(root, _VITE_CONFIGS),
        "eslint_config": _first_existing(root, _ESLINT_CONFIGS),
        "stylelint_config": _first_existing(root, _STYLELINT_CONFIGS),
        "tools": {
            "eslint": str(eslint.path or ""),
            "eslint_source": eslint.source,
            "stylelint": str(stylelint.path or ""),
            "stylelint_source": stylelint.source,
        },
        "errors": errors,
        "warnings": warnings,
    }


def _settings(
    context: SkillContext,
    root: Path,
    params: dict[str, Any],
) -> dict[str, Any]:
    effective = context.web_profiles.effective_settings(
        root,
        default_timeout_seconds=context.config.php_tool_timeout_seconds,
        default_max_output_chars=context.config.php_tool_max_output_chars,
    )
    profile = effective["profile"] or {}
    return {
        "profile": effective["profile"],
        "timeout_seconds": int(effective["timeout_seconds"]),
        "max_output_chars": int(effective["max_output_chars"]),
        "framework_preset": _text_setting(params, profile, "framework_preset", "auto"),
        "eslint_config": _config_setting(root, params, profile, "eslint_config"),
        "stylelint_config": _config_setting(root, params, profile, "stylelint_config"),
    }


def _text_setting(
    params: dict[str, Any],
    profile: dict[str, Any],
    name: str,
    default: str,
) -> str:
    if name in params and params[name] is not None:
        return str(params[name]).strip().casefold()
    return str(profile.get(name) or default).strip().casefold()


def _config_setting(
    root: Path,
    params: dict[str, Any],
    profile: dict[str, Any],
    name: str,
) -> str:
    raw = str(params.get(name) if name in params else profile.get(name, "") or "").strip()
    if not raw:
        return ""
    candidate = Path(raw)
    resolved = (
        candidate.resolve(strict=False)
        if candidate.is_absolute()
        else (root / candidate).resolve(strict=False)
    )
    if resolved != root and root not in resolved.parents:
        raise ValueError("La configuración debe permanecer dentro del proyecto.")
    if not resolved.is_file():
        raise ValueError(f"Configuración no encontrada: {resolved}")
    return resolved.relative_to(root).as_posix()


def _eslint_argv(root: Path, tool: Path, settings: dict[str, Any]) -> list[str]:
    argv = [str(tool), ".", "--no-color", "--format", "stylish"]
    config = settings["eslint_config"]
    if config:
        argv.extend(("--config", config))
    return argv


def _stylelint_argv(root: Path, tool: Path, settings: dict[str, Any]) -> list[str]:
    del root
    argv = [str(tool), "**/*.css", "--formatter", "string", "--no-color"]
    config = settings["stylelint_config"]
    if config:
        argv.extend(("--config", config))
    return argv


def _run_tool(
    skill_name: str,
    label: str,
    root: Path,
    tool_path: Path,
    tool_source: str,
    argv: list[str],
    settings: dict[str, Any],
    authorization: dict[str, Any],
) -> SkillResult:
    result = run_controlled_process(
        argv,
        cwd=root,
        timeout_seconds=settings["timeout_seconds"],
        max_output_chars=settings["max_output_chars"],
    )
    ok = result.returncode == 0 and not result.timed_out
    heading = f"{label} correcto." if ok else f"{label} encontró problemas."
    message = "\n".join(
        (
            heading,
            "",
            f"- Proyecto: `{root}`",
            f"- Herramienta: `{tool_path}`",
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
            "tool_path": str(tool_path),
            "tool_source": tool_source,
            "command_argv": argv,
            "returncode": result.returncode,
            "duration_ms": result.duration_ms,
            "timed_out": result.timed_out,
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
            "shell": False,
            **authorization,
        },
    )


def _authorize(
    context: SkillContext,
    root: Path,
    params: dict[str, Any],
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
    profile = settings.get("profile")
    return {
        **decision.as_data(),
        "timeout_seconds": settings["timeout_seconds"],
        "project_profile_id": profile.get("id") if profile else None,
        "project_profile_applied": profile is not None,
    }


def _project_root(params: dict[str, Any]) -> Path:
    raw = str(params.get("path") or "").strip()
    if not raw:
        raise ValueError("Falta el parámetro path.")
    path = Path(raw).expanduser().resolve(strict=False)
    start = path if path.is_dir() else path.parent
    current = start
    while True:
        if any((current / marker).is_file() for marker in _PROJECT_MARKERS):
            return current
        if current.parent == current:
            return start
        current = current.parent


def _existing_project_root(params: dict[str, Any]) -> Path:
    root = _project_root(params).resolve(strict=True)
    mode = root.stat().st_mode
    if not stat.S_ISDIR(mode):
        raise ValueError(f"El proyecto debe ser una carpeta regular: {root}")
    return root


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
            **authorization,
        },
    )


def _read_json(path: Path, *, max_bytes: int) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > max_bytes:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _dependency_names(package: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        value = package.get(key)
        if isinstance(value, dict):
            names.update(str(item) for item in value)
    return names


def _mapping_keys(value: Any) -> list[str]:
    return sorted(str(item) for item in value) if isinstance(value, dict) else []


def _workspace_names(package: dict[str, Any]) -> list[str]:
    value = package.get("workspaces")
    if isinstance(value, list):
        return [str(item) for item in value[:100] if str(item).strip()]
    if isinstance(value, dict) and isinstance(value.get("packages"), list):
        return [str(item) for item in value["packages"][:100] if str(item).strip()]
    return []


def _angular_details(root: Path) -> dict[str, Any]:
    path = root / "angular.json"
    if not path.is_file():
        return {"present": False, "invalid_json": False, "project_count": 0, "projects": []}
    payload = _read_json(path, max_bytes=2_000_000)
    if not payload:
        return {"present": True, "invalid_json": True, "project_count": 0, "projects": []}
    projects = payload.get("projects") if isinstance(payload.get("projects"), dict) else {}
    names = sorted(str(name) for name in projects)[:100]
    return {
        "present": True,
        "invalid_json": False,
        "project_count": len(projects),
        "projects": names,
        "default_project": str(payload.get("defaultProject") or ""),
    }


def _first_existing(root: Path, names: tuple[str, ...]) -> str:
    for name in names:
        if (root / name).is_file():
            return name
    return ""


def _primary_framework(detected: set[str], preset: str) -> str:
    if preset not in {"auto", "generic"}:
        return preset
    priority = ("angular", "next", "nuxt", "astro", "vue", "svelte", "react", "vite")
    for item in priority:
        if item in detected:
            return item
    return "generic"
