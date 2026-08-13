from __future__ import annotations

import json
import os
import shutil
import stat
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.frontend_quality import (
    EslintLintSkill,
    FrameworkValidateSkill,
    StylelintLintSkill,
    inspect_frontend_project,
)
from elyndra.skills.process import run_controlled_process
from elyndra.skills.tool_resolution import resolve_project_tool

_HTML_EXTENSIONS = {".html", ".htm"}
_CSS_EXTENSIONS = {".css"}
_JAVASCRIPT_EXTENSIONS = {".js", ".mjs", ".cjs"}
_TYPESCRIPT_EXTENSIONS = {".ts", ".tsx", ".mts", ".cts"}
_WEB_EXTENSIONS = (
    _HTML_EXTENSIONS | _CSS_EXTENSIONS | _JAVASCRIPT_EXTENSIONS | _TYPESCRIPT_EXTENSIONS
)
_PROJECT_MARKERS = (
    "package.json",
    "tsconfig.json",
    "angular.json",
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.cjs",
    "vite.config.ts",
    "vite.config.mts",
    "vite.config.cts",
    "webpack.config.js",
)
_DEFAULT_EXCLUDES = {".git", "node_modules", "vendor", "dist", "build"}
_FRAMEWORK_PACKAGES = {
    "@angular/core": "Angular",
    "astro": "Astro",
    "next": "Next.js",
    "nuxt": "Nuxt",
    "react": "React",
    "svelte": "Svelte",
    "vite": "Vite",
    "vue": "Vue",
}
_VOID_HTML_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_OPTIONAL_HTML_END_TAGS = {
    "body",
    "colgroup",
    "dd",
    "dt",
    "head",
    "html",
    "li",
    "option",
    "p",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
}


class WebProjectInspectSkill:
    name = "web.project_inspect"
    description = "Inspecciona un proyecto HTML, CSS, JavaScript o TypeScript sin ejecutar código."
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
            context.config.php_tool_timeout_seconds,
            ["inspect-web-project", str(root)],
            "Solo se leen nombres y metadatos acotados; no se ejecuta código.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(
            context,
            root,
            params,
            timeout_seconds=settings["timeout_seconds"],
            profile=settings["profile"],
        )
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


class HtmlValidateSkill:
    name = "html.validate"
    description = "Comprueba estructura HTML básica sin ejecutar scripts ni cargar recursos."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        return _scan_approval_details(context, params, self.name, "HTML")

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _run_internal_scan(
            context,
            params,
            skill_name=self.name,
            label="HTML",
            extensions=_HTML_EXTENSIONS,
            validator=_validate_html_text,
        )


class CssValidateSkill:
    name = "css.validate"
    description = "Comprueba comentarios, cadenas y delimitadores CSS sin ejecutar código."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        return _scan_approval_details(context, params, self.name, "CSS")

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _run_internal_scan(
            context,
            params,
            skill_name=self.name,
            label="CSS",
            extensions=_CSS_EXTENSIONS,
            validator=_validate_css_text,
        )


class JavaScriptSyntaxSkill:
    name = "javascript.syntax_validate"
    description = "Ejecuta node --check sobre JavaScript sin ejecutar el programa."
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
        node = shutil.which("node") or "node"
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            [node, "--check", "<archivo.js>"],
            "Node analiza sintaxis; Elyndra no ejecuta el programa ni instala dependencias.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(
            context,
            root,
            params,
            timeout_seconds=settings["timeout_seconds"],
            profile=settings["profile"],
        )
        node = shutil.which("node")
        if node is None:
            return _tool_unavailable(self.name, root, "node", authorization)
        files, truncated = _collect_files(
            target,
            root=root,
            extensions=_JAVASCRIPT_EXTENSIONS,
            exclude_paths=settings["exclude_paths"],
            max_files=settings["max_files"],
        )
        if truncated:
            return _file_limit_result(self.name, root, settings, authorization)
        return _run_file_commands(
            skill_name=self.name,
            label="JavaScript",
            root=root,
            files=files,
            command_builder=lambda source: [node, "--check", str(source)],
            settings=settings,
            authorization=authorization,
            tool_path=Path(node).resolve(),
        )


class TypeScriptCheckSkill:
    name = "typescript.check"
    description = "Ejecuta tsc --noEmit con binario local prioritario y argumentos validados."
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
        tool = resolve_project_tool(root, "tsc")
        action = [str(tool.path or "tsc"), "--noEmit", "--pretty", "false"]
        config = _typescript_config(root, params)
        if config:
            action.extend(("--project", config))
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            action,
            "TypeScript puede cargar configuración del proyecto; no ejecuta scripts npm.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(
            context,
            root,
            params,
            timeout_seconds=settings["timeout_seconds"],
            profile=settings["profile"],
        )
        tool = resolve_project_tool(root, "tsc")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "tsc", authorization)
        argv = [str(tool.path), "--noEmit", "--pretty", "false"]
        config = _typescript_config(root, params)
        if config:
            argv.extend(("--project", config))
        result = run_controlled_process(
            argv,
            cwd=root,
            timeout_seconds=settings["timeout_seconds"],
            max_output_chars=settings["max_output_chars"],
        )
        ok = result.returncode == 0 and not result.timed_out
        heading = "TypeScript válido." if ok else "TypeScript encontró problemas."
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
                "skill": self.name,
                "project_root": str(root),
                "tool_path": str(tool.path),
                "tool_source": tool.source,
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


class WebVerifyProjectSkill:
    name = "web.verify_project"
    description = "Verifica HTML, CSS, JavaScript y TypeScript con un flujo determinista."
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
        stages = _planned_stages(settings)
        details = _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["web-verify-project", str(root)],
            "Usa validadores internos, node --check, tsc --noEmit, ESLint y Stylelint.",
        )
        details["verification_stages"] = stages
        details["approval_summary"] += "\nEtapas: " + ", ".join(stages)
        details["project_profile_id"] = (
            settings["profile"].get("id") if settings["profile"] else None
        )
        return details

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(
            context,
            root,
            params,
            timeout_seconds=settings["timeout_seconds"],
            profile=settings["profile"],
        )
        inventory = _inspect_project(root, settings)
        plan = {
            "stages": _planned_stages(settings),
            "settings": _public_settings(settings),
            "authorization_scope": authorization["authorization_scope"],
        }
        run_id = context.verification_runs.start(
            toolchain="web",
            project_root=root,
            actor=context.actor,
            profile_id=settings["profile"].get("id") if settings["profile"] else None,
            plan=plan,
        )
        started = time.perf_counter()
        stages: list[dict[str, Any]] = [
            {
                "name": "inspect",
                "status": "passed",
                "duration_ms": 0,
                "files": inventory["total_files"],
                "frameworks": inventory["frameworks"],
            }
        ]
        shared = dict(params)
        shared["path"] = str(root)
        if params.get("allow_root_once") is True:
            shared["allow_root_once"] = True
            shared["authorization_source"] = str(
                params.get("authorization_source") or "pipeline_project_once"
            )
        tasks = (
            ("framework", settings["framework_checks_enabled"], FrameworkValidateSkill()),
            ("html", settings["html_enabled"], HtmlValidateSkill()),
            ("css", settings["css_enabled"], CssValidateSkill()),
            ("javascript", settings["javascript_enabled"], JavaScriptSyntaxSkill()),
            ("typescript", settings["typescript_enabled"], TypeScriptCheckSkill()),
            ("eslint", settings["eslint_enabled"], EslintLintSkill()),
            ("stylelint", settings["stylelint_enabled"], StylelintLintSkill()),
        )
        try:
            for name, enabled, skill in tasks:
                if not enabled:
                    stages.append({"name": name, "status": "skipped", "reason": "disabled"})
                    continue
                if name == "javascript" and not shutil.which("node"):
                    status = "failed" if settings["require_tools"] else "unavailable"
                    stages.append({"name": name, "status": status, "reason": "Node no disponible"})
                elif name == "typescript" and resolve_project_tool(root, "tsc").path is None:
                    status = "failed" if settings["require_tools"] else "unavailable"
                    stages.append(
                        {
                            "name": name,
                            "status": status,
                            "reason": "TypeScript no disponible",
                        }
                    )
                elif (
                    name in {"eslint", "stylelint"}
                    and resolve_project_tool(root, name).path is None
                ):
                    status = "failed" if settings["require_tools"] else "unavailable"
                    stages.append(
                        {
                            "name": name,
                            "status": status,
                            "reason": f"{name} no disponible",
                        }
                    )
                else:
                    result = skill.execute(context, shared)
                    stages.append(_stage_from_result(name, result))
                if stages[-1]["status"] == "failed" and settings["fail_fast"]:
                    break
            status = _verification_status(stages)
            duration_ms = round((time.perf_counter() - started) * 1000)
            context.verification_runs.finish(
                run_id,
                status=status,
                duration_ms=duration_ms,
                summary={
                    "stages": stages,
                    "counts": _stage_counts(stages),
                    "project_inventory": inventory,
                },
            )
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000)
            context.verification_runs.finish(
                run_id,
                status="failed",
                duration_ms=duration_ms,
                summary={"stages": stages, "error": str(exc)},
            )
            raise
        return SkillResult(
            status != "failed",
            _format_verification(root, run_id, status, duration_ms, stages),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "verification_run_id": run_id,
                "verification_status": status,
                "project_root": str(root),
                "duration_ms": duration_ms,
                "stages": stages,
                "project_profile_id": (
                    settings["profile"].get("id") if settings["profile"] else None
                ),
                **authorization,
            },
        )


def _scan_approval_details(
    context: SkillContext,
    params: dict[str, Any],
    skill_name: str,
    label: str,
) -> dict[str, Any]:
    target = _resolve_path(params)
    root = _discover_project_root(target)
    settings = _pipeline_settings(context, root, params)
    decision = context.authorization.project(root)
    return _approval_details(
        skill_name,
        root,
        decision.scope.value,
        decision.source,
        settings["timeout_seconds"],
        [f"validate-{label.casefold()}", str(target)],
        f"La comprobación {label} es local y no ejecuta scripts.",
    )


def _run_internal_scan(
    context: SkillContext,
    params: dict[str, Any],
    *,
    skill_name: str,
    label: str,
    extensions: set[str],
    validator: Any,
) -> SkillResult:
    target = _resolve_existing_path(params)
    root = _discover_project_root(target)
    settings = _pipeline_settings(context, root, params)
    authorization = _authorize_project(
        context,
        root,
        params,
        timeout_seconds=settings["timeout_seconds"],
        profile=settings["profile"],
    )
    files, truncated = _collect_files(
        target,
        root=root,
        extensions=extensions,
        exclude_paths=settings["exclude_paths"],
        max_files=settings["max_files"],
    )
    if truncated:
        return _file_limit_result(skill_name, root, settings, authorization)
    started = time.perf_counter()
    failures: list[dict[str, Any]] = []
    for source in files:
        try:
            if source.stat().st_size > 4_000_000:
                issues = ["archivo superior al límite local de 4 MB"]
            else:
                issues = validator(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            issues = [f"no se pudo leer como UTF-8: {exc}"]
        if issues:
            failures.append(
                {
                    "path": source.relative_to(root).as_posix(),
                    "issues": issues[:20],
                }
            )
            if len(failures) >= 100:
                break
    duration_ms = round((time.perf_counter() - started) * 1000)
    ok = not failures
    heading = (
        f"{label} válido en {len(files)} archivo(s)."
        if ok
        else f"{label} encontró problemas en {len(failures)} archivo(s)."
    )
    lines = [
        heading,
        "",
        f"- Proyecto: `{root}`",
        f"- Archivos examinados: `{len(files)}`",
        f"- Fallos: `{len(failures)}`",
        f"- Duración: `{duration_ms} ms`",
    ]
    if failures:
        lines.extend(("", "Problemas:"))
        for item in failures[:20]:
            lines.append(f"- `{item['path']}`: {item['issues'][0]}")
    return SkillResult(
        ok,
        "\n".join(lines),
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "files_found": len(files),
            "scanned_files": len(files),
            "failed_files": len(failures),
            "failures": failures,
            "duration_ms": duration_ms,
            "timed_out": False,
            "stdout_truncated": len(failures) >= 100,
            "stderr_truncated": False,
            "shell": False,
            **authorization,
        },
    )


def _run_file_commands(
    *,
    skill_name: str,
    label: str,
    root: Path,
    files: list[Path],
    command_builder: Any,
    settings: dict[str, Any],
    authorization: dict[str, Any],
    tool_path: Path,
) -> SkillResult:
    started = time.perf_counter()
    deadline = started + settings["timeout_seconds"]
    failures: list[dict[str, Any]] = []
    scanned = 0
    timed_out = False
    output_truncated = False
    for source in files:
        remaining = int(deadline - time.perf_counter())
        if remaining <= 0:
            timed_out = True
            break
        result = run_controlled_process(
            command_builder(source),
            cwd=root,
            timeout_seconds=max(1, remaining),
            max_output_chars=min(settings["max_output_chars"], 2_000),
        )
        scanned += 1
        output_truncated = (
            output_truncated or result.stdout_truncated or result.stderr_truncated
        )
        if result.returncode != 0 or result.timed_out:
            failures.append(
                {
                    "path": source.relative_to(root).as_posix(),
                    "returncode": result.returncode,
                    "timed_out": result.timed_out,
                    "output": result.output[-1_000:],
                }
            )
            if result.timed_out or len(failures) >= 100:
                timed_out = result.timed_out
                break
    duration_ms = round((time.perf_counter() - started) * 1000)
    ok = not failures and not timed_out and scanned == len(files)
    heading = (
        f"Sintaxis {label} válida en {scanned} archivo(s)."
        if ok
        else f"{label} encontró problemas en {len(failures)} archivo(s)."
    )
    lines = [
        heading,
        "",
        f"- Proyecto: `{root}`",
        f"- Archivos encontrados: `{len(files)}`",
        f"- Archivos examinados: `{scanned}`",
        f"- Fallos: `{len(failures)}`",
        f"- Timeout: `{'sí' if timed_out else 'no'}`",
        f"- Duración: `{duration_ms} ms`",
    ]
    return SkillResult(
        ok,
        "\n".join(lines),
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "tool_path": str(tool_path),
            "files_found": len(files),
            "scanned_files": scanned,
            "failed_files": len(failures),
            "failures": failures,
            "duration_ms": duration_ms,
            "timed_out": timed_out,
            "stdout_truncated": output_truncated,
            "stderr_truncated": output_truncated,
            "shell": False,
            **authorization,
        },
    )


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
    timeout_seconds: int,
    profile: dict[str, Any] | None,
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
    return {
        **decision.as_data(),
        "timeout_seconds": timeout_seconds,
        "project_profile_id": profile.get("id") if profile else None,
        "project_profile_applied": profile is not None,
    }


def _pipeline_settings(
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
        "max_files": _bounded_files(params.get("max_files"), int(effective["max_files"])),
        "exclude_paths": list(effective["exclude_paths"]),
        "html_enabled": _setting(params, profile, "html_enabled", True),
        "css_enabled": _setting(params, profile, "css_enabled", True),
        "javascript_enabled": _setting(params, profile, "javascript_enabled", True),
        "typescript_enabled": _setting(params, profile, "typescript_enabled", True),
        "eslint_enabled": _setting(params, profile, "eslint_enabled", True),
        "stylelint_enabled": _setting(params, profile, "stylelint_enabled", True),
        "framework_checks_enabled": _setting(
            params, profile, "framework_checks_enabled", True
        ),
        "framework_preset": str(
            params.get("framework_preset")
            if params.get("framework_preset") is not None
            else profile.get("framework_preset", "auto")
        ).strip().casefold(),
        "eslint_config": str(
            params.get("eslint_config")
            if params.get("eslint_config") is not None
            else profile.get("eslint_config", "")
        ).strip(),
        "stylelint_config": str(
            params.get("stylelint_config")
            if params.get("stylelint_config") is not None
            else profile.get("stylelint_config", "")
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


def _collect_files(
    target: Path,
    *,
    root: Path,
    extensions: set[str],
    exclude_paths: list[str],
    max_files: int,
) -> tuple[list[Path], bool]:
    if target.is_file():
        return ([target] if target.suffix.casefold() in extensions else []), False
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
            if candidate.suffix.casefold() not in extensions:
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
    counts: dict[str, int] = {}
    truncated = False
    for label, extensions in (
        ("html", _HTML_EXTENSIONS),
        ("css", _CSS_EXTENSIONS),
        ("javascript", _JAVASCRIPT_EXTENSIONS),
        ("typescript", _TYPESCRIPT_EXTENSIONS),
    ):
        files, limited = _collect_files(
            root,
            root=root,
            extensions=extensions,
            exclude_paths=settings["exclude_paths"],
            max_files=settings["max_files"],
        )
        counts[label] = len(files)
        truncated = truncated or limited
    package = _read_package_json(root / "package.json")
    dependencies = set(package["dependencies"]) | set(package["dev_dependencies"])
    frameworks = sorted(
        label for dependency, label in _FRAMEWORK_PACKAGES.items() if dependency in dependencies
    )
    tsc = resolve_project_tool(root, "tsc")
    frontend = inspect_frontend_project(
        root, framework_preset=settings.get("framework_preset", "auto")
    )
    return {
        "project_root": str(root),
        "file_counts": counts,
        "total_files": sum(counts.values()),
        "file_scan_truncated": truncated,
        "package_json": (root / "package.json").is_file(),
        "package_lock": _first_existing(
            root,
            ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb"),
        ),
        "package_name": package["name"],
        "package_type": package["type"],
        "scripts": package["scripts"],
        "dependency_count": len(package["dependencies"]),
        "dev_dependency_count": len(package["dev_dependencies"]),
        "frameworks": frameworks,
        "frontend": frontend,
        "package_manager": frontend["package_manager"],
        "workspace_count": frontend["workspace_count"],
        "primary_framework": frontend["primary_framework"],
        "configs": {
            "typescript": _first_existing(root, ("tsconfig.json",)),
            "angular": _first_existing(root, ("angular.json",)),
            "eslint": _first_existing(
                root,
                ("eslint.config.js", "eslint.config.mjs", ".eslintrc.json"),
            ),
            "stylelint": _first_existing(
                root,
                ("stylelint.config.js", ".stylelintrc", ".stylelintrc.json"),
            ),
        },
        "tools": {
            "node": shutil.which("node") or "",
            "tsc": str(tsc.path or ""),
            "tsc_source": tsc.source,
            "eslint": frontend["tools"]["eslint"],
            "eslint_source": frontend["tools"]["eslint_source"],
            "stylelint": frontend["tools"]["stylelint"],
            "stylelint_source": frontend["tools"]["stylelint_source"],
        },
    }


def _read_package_json(path: Path) -> dict[str, Any]:
    empty = {
        "name": "",
        "type": "",
        "scripts": [],
        "dependencies": [],
        "dev_dependencies": [],
    }
    if not path.is_file() or path.stat().st_size > 2_000_000:
        return empty
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return empty
    if not isinstance(payload, dict):
        return empty
    scripts = payload.get("scripts") if isinstance(payload.get("scripts"), dict) else {}
    dependencies = (
        payload.get("dependencies")
        if isinstance(payload.get("dependencies"), dict)
        else {}
    )
    dev_dependencies = (
        payload.get("devDependencies")
        if isinstance(payload.get("devDependencies"), dict)
        else {}
    )
    return {
        "name": str(payload.get("name") or ""),
        "type": str(payload.get("type") or ""),
        "scripts": sorted(str(name) for name in scripts),
        "dependencies": sorted(str(name) for name in dependencies),
        "dev_dependencies": sorted(str(name) for name in dev_dependencies),
    }


def _first_existing(root: Path, names: tuple[str, ...]) -> str:
    for name in names:
        if (root / name).is_file():
            return name
    return ""


def _typescript_config(root: Path, params: dict[str, Any]) -> str:
    raw = str(params.get("config") or "").strip()
    if not raw:
        return "tsconfig.json" if (root / "tsconfig.json").is_file() else ""
    candidate_path = Path(raw)
    candidate = (
        candidate_path.resolve(strict=False)
        if candidate_path.is_absolute()
        else (root / candidate_path).resolve(strict=False)
    )
    if candidate != root and root not in candidate.parents:
        raise ValueError("La configuración TypeScript debe permanecer dentro del proyecto.")
    if not candidate.is_file():
        raise ValueError(f"Configuración TypeScript no encontrada: {candidate}")
    return candidate.relative_to(root).as_posix()


class _StructuralHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.issues: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        clean = tag.casefold()
        if clean in _VOID_HTML_TAGS:
            return
        if clean in _OPTIONAL_HTML_END_TAGS and self.stack and self.stack[-1][0] == clean:
            self.stack.pop()
        self.stack.append((clean, self.getpos()[0]))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag, attrs

    def handle_endtag(self, tag: str) -> None:
        clean = tag.casefold()
        indexes = [index for index, item in enumerate(self.stack) if item[0] == clean]
        if not indexes:
            if clean not in _OPTIONAL_HTML_END_TAGS:
                self.issues.append(f"cierre </{clean}> sin apertura en línea {self.getpos()[0]}")
            return
        index = indexes[-1]
        dangling = self.stack[index + 1 :]
        for name, line in dangling:
            if name not in _OPTIONAL_HTML_END_TAGS:
                self.issues.append(f"<{name}> abierto en línea {line} antes de </{clean}>")
        del self.stack[index:]

    def error(self, message: str) -> None:
        self.issues.append(message)


def _validate_html_text(text: str) -> list[str]:
    parser = _StructuralHtmlParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        parser.issues.append(f"HTML no procesable: {exc}")
    for tag, line in parser.stack:
        if tag not in _OPTIONAL_HTML_END_TAGS:
            parser.issues.append(f"<{tag}> abierto en línea {line} sin cierre")
    return parser.issues[:100]


def _validate_css_text(text: str) -> list[str]:
    issues: list[str] = []
    stack: list[tuple[str, int]] = []
    quote = ""
    escaped = False
    in_comment = False
    line = 1
    index = 0
    pairs = {"}": "{", ")": "(", "]": "["}
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if char == "\n":
            line += 1
        if in_comment:
            if char == "*" and next_char == "/":
                in_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char == "/" and next_char == "*":
            in_comment = True
            index += 2
            continue
        if char in {'"', "'"}:
            quote = char
        elif char in "{([":
            stack.append((char, line))
        elif char in "})]":
            if not stack or stack[-1][0] != pairs[char]:
                issues.append(f"delimitador {char} inesperado en línea {line}")
            else:
                stack.pop()
        index += 1
    if in_comment:
        issues.append("comentario CSS sin cierre")
    if quote:
        issues.append("cadena CSS sin cierre")
    for opener, opened_line in reversed(stack):
        issues.append(f"delimitador {opener} abierto en línea {opened_line} sin cierre")
    return issues[:100]


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


def _file_limit_result(
    skill_name: str,
    root: Path,
    settings: dict[str, Any],
    authorization: dict[str, Any],
) -> SkillResult:
    return SkillResult(
        False,
        (
            "El proyecto supera el límite web configurado "
            f"({settings['max_files']} archivos). Ajusta max_files en su perfil."
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
    unavailable = result.message.startswith("No se encontró la herramienta requerida:")
    status = "passed" if result.ok else ("unavailable" if unavailable else "failed")
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
    return {
        status: sum(item["status"] == status for item in stages) for status in statuses
    }


def _planned_stages(settings: dict[str, Any]) -> list[str]:
    stages = ["inspect"]
    for key, name in (
        ("framework_checks_enabled", "framework"),
        ("html_enabled", "html"),
        ("css_enabled", "css"),
        ("javascript_enabled", "javascript"),
        ("typescript_enabled", "typescript"),
        ("eslint_enabled", "eslint"),
        ("stylelint_enabled", "stylelint"),
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
    counts = inventory["file_counts"]
    return "\n".join(
        (
            "Inspección web completada sin ejecutar código.",
            "",
            f"- Proyecto: `{inventory['project_root']}`",
            f"- HTML: `{counts['html']}`",
            f"- CSS: `{counts['css']}`",
            f"- JavaScript: `{counts['javascript']}`",
            f"- TypeScript: `{counts['typescript']}`",
            f"- package.json: `{'sí' if inventory['package_json'] else 'no'}`",
            f"- Paquete: `{inventory['package_name'] or '-'}`",
            f"- Frameworks: `{frameworks}`",
            f"- Framework principal: `{inventory['primary_framework']}`",
            f"- Gestor de paquetes: `{inventory['package_manager'] or '-'}`",
            f"- Workspaces: `{inventory['workspace_count']}`",
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
        f"Verificación web {labels[status]}.",
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
