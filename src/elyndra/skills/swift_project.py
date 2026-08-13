from __future__ import annotations

import os
import re
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.process import ProcessResult, run_controlled_process
from elyndra.skills.tool_resolution import ToolResolution, resolve_project_tool

_SWIFT_EXTENSION = ".swift"
_PROJECT_MARKERS = (
    "Package.swift",
    "Package.resolved",
)
_DEFAULT_EXCLUDES = {
    ".build",
    ".git",
    ".idea",
    ".swiftpm",
    ".vscode",
    "DerivedData",
    "Packages",
    "build",
    "vendor",
}
_FRAMEWORK_HINTS = {
    "import SwiftUI": "SwiftUI",
    "import Vapor": "Vapor",
    "import NIO": "SwiftNIO",
    "import XCTest": "XCTest",
    "import Testing": "Swift Testing",
    "import ArgumentParser": "Swift Argument Parser",
    "import Foundation": "Foundation",
}
_TOOLS_VERSION = re.compile(
    r"^\s*//\s*swift-tools-version\s*:\s*([^\s]+)",
    re.MULTILINE,
)
_PACKAGE_NAME = re.compile(r"\bname\s*:\s*\"([^\"]+)\"")
_DEPENDENCY = re.compile(r"\.package\s*\((.*?)\)", re.DOTALL)
_TARGET = re.compile(r"\.(?:target|executableTarget|testTarget|macro)\s*\(")
_PRODUCT = re.compile(r"\.(?:library|executable|plugin)\s*\(")


class SwiftProjectInspectSkill:
    name = "swift.project_inspect"
    description = "Inspecciona un proyecto Swift sin ejecutar Package.swift ni plugins."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            context.config.swift_tool_timeout_seconds,
            ["inspect-swift-project", str(root)],
            "Solo se leen rutas y metadatos acotados; Package.swift no se ejecuta.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
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
                "stage_status": "passed",
                "shell": False,
                **authorization,
            },
        )


class SwiftManifestValidateSkill:
    name = "swift.manifest_validate"
    description = "Valida Package.swift como texto, sin evaluar el manifiesto."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            context.config.swift_tool_timeout_seconds,
            ["validate-swift-manifest", str(root / "Package.swift")],
            "Package.swift se analiza como UTF-8; no se invoca SwiftPM.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        report = _validate_manifest(root)
        ok = not report["errors"]
        lines = [
            "Manifiesto Swift válido." if ok else "Manifiesto Swift con errores.",
            "",
            f"- Proyecto: `{root}`",
            f"- Package.swift: `{'sí' if report['manifest_exists'] else 'no'}`",
            f"- Errores: `{len(report['errors'])}`",
            f"- Advertencias: `{len(report['warnings'])}`",
        ]
        for label, items in (
            ("Errores", report["errors"]),
            ("Advertencias", report["warnings"]),
        ):
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
                "shell": False,
                **authorization,
            },
        )


class SwiftSyntaxCheckSkill:
    name = "swift.syntax_check"
    description = "Comprueba sintaxis con swiftc -parse, archivo por archivo."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["swiftc", "-parse", "<archivo.swift>"],
            "El compilador solo analiza sintaxis; no enlaza ni ejecuta el proyecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        files, truncated = _collect_swift_files(
            target,
            root=root,
            exclude_paths=settings["exclude_paths"],
            max_files=settings["max_swift_files"],
        )
        if truncated:
            return _file_limit_result(self.name, root, settings, authorization)
        if not files:
            return _skipped_result(
                self.name,
                root,
                "No se encontraron archivos Swift para comprobar.",
                authorization,
            )
        tool = resolve_project_tool(root, "swiftc")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "swiftc", authorization)
        return _run_per_file_tool(
            self.name,
            root,
            tool,
            files,
            settings,
            authorization,
            command=lambda path: [str(tool.path), "-parse", str(path)],
            success="Sintaxis Swift correcta.",
            failure="swiftc encontró problemas de sintaxis.",
        )


class SwiftFormatCheckSkill:
    name = "swift.format_check"
    description = "Comprueba formato con swift-format lint sin reescribir archivos."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["swift-format", "lint", "--strict", "<archivo.swift>"],
            "Solo se informa formato; no se usa swift-format format ni modo in-place.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        files, truncated = _collect_swift_files(
            target,
            root=root,
            exclude_paths=settings["exclude_paths"],
            max_files=settings["max_swift_files"],
        )
        if truncated:
            return _file_limit_result(self.name, root, settings, authorization)
        if not files:
            return _skipped_result(
                self.name,
                root,
                "No se encontraron archivos Swift para revisar formato.",
                authorization,
            )
        tool = resolve_project_tool(root, "swift-format")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "swift-format", authorization)
        return _run_per_file_tool(
            self.name,
            root,
            tool,
            files,
            settings,
            authorization,
            command=lambda path: [str(tool.path), "lint", "--strict", str(path)],
            success="Formato Swift correcto.",
            failure="swift-format encontró diferencias o problemas.",
        )


class SwiftBuildSkill:
    name = "swift.build_project"
    description = "Compila un paquete SwiftPM sin resolución automática de dependencias."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            _swiftpm_argv("build", root, settings, scratch="<temporal>"),
            "SwiftPM evalúa Package.swift y puede ejecutar plugins del proyecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _execute_swiftpm_stage(context, params, skill_name=self.name, tests=False)


class SwiftTestSkill:
    name = "swift.test_project"
    description = "Ejecuta tests SwiftPM con resolución automática desactivada."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            _swiftpm_argv("test", root, settings, scratch="<temporal>"),
            "Los tests, el manifiesto y los plugins pueden ejecutar código del proyecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _execute_swiftpm_stage(context, params, skill_name=self.name, tests=True)


class SwiftVerifyProjectSkill:
    name = "swift.verify_project"
    description = "Ejecuta la verificación Swift completa y guarda historial comparable."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["swift-verify", *_enabled_stage_names(settings), str(root)],
            "Build y tests SwiftPM pueden ejecutar manifiestos, plugins y tests.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        started = time.perf_counter()
        profile = settings.get("profile") or {}
        plan = {
            "stages": _enabled_stage_names(settings),
            "configuration": settings["configuration"],
            "require_tools": settings["require_tools"],
            "fail_fast": settings["fail_fast"],
        }
        run_id = context.verification_runs.start(
            toolchain="swift",
            project_root=root,
            actor=context.actor,
            profile_id=profile.get("id"),
            plan=plan,
        )
        stages: list[dict[str, Any]] = []
        stage_specs = (
            ("inspect", True, SwiftProjectInspectSkill()),
            ("manifest", settings["manifest_enabled"], SwiftManifestValidateSkill()),
            ("syntax", settings["syntax_enabled"], SwiftSyntaxCheckSkill()),
            ("format", settings["format_enabled"], SwiftFormatCheckSkill()),
            ("build", settings["build_enabled"], SwiftBuildSkill()),
            ("tests", settings["tests_enabled"], SwiftTestSkill()),
        )
        for stage_name, enabled, skill in stage_specs:
            if not enabled:
                stages.append(
                    {
                        "name": stage_name,
                        "status": "skipped",
                        "message": "Etapa desactivada por configuración.",
                    }
                )
                continue
            result = skill.execute(context, dict(params))
            status = _stage_status(result)
            if status == "unavailable" and settings["require_tools"]:
                status = "failed"
            stages.append(
                {
                    "name": stage_name,
                    "status": status,
                    "message": _bounded_text(result.message.splitlines()[0], 240),
                    "duration_ms": result.data.get("duration_ms"),
                    "returncode": result.data.get("returncode"),
                }
            )
            if settings["fail_fast"] and status == "failed":
                break
        status = _overall_status(stages)
        duration_ms = round((time.perf_counter() - started) * 1000)
        run = context.verification_runs.finish(
            run_id,
            status=status,
            duration_ms=duration_ms,
            summary={"stages": stages, "authorization": authorization},
        )
        heading = {
            "passed": "Verificación Swift correcta.",
            "partial": "Verificación Swift parcial.",
            "failed": "Verificación Swift fallida.",
        }[status]
        lines = [
            heading,
            "",
            f"- Proyecto: `{root}`",
            f"- Ejecución: `{run_id}`",
            f"- Estado: `{status}`",
            f"- Duración: `{duration_ms} ms`",
            "",
            "Etapas:",
        ]
        for stage in stages:
            suffix = f" — {stage['message']}" if stage.get("message") else ""
            lines.append(f"- {stage['name']}: `{stage['status']}`{suffix}")
        return SkillResult(
            status != "failed",
            "\n".join(lines),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "verification_run": run,
                "stages": stages,
                "duration_ms": duration_ms,
                "shell": False,
                **authorization,
            },
        )


def _execute_swiftpm_stage(
    context: SkillContext,
    params: dict[str, Any],
    *,
    skill_name: str,
    tests: bool,
) -> SkillResult:
    root = _discover_project_root(_resolve_existing_path(params))
    settings = _pipeline_settings(context, root, params)
    authorization = _authorize_project(context, root, params, settings=settings)
    manifest = root / "Package.swift"
    if not manifest.is_file():
        return _skipped_result(
            skill_name,
            root,
            "No existe Package.swift; la etapa SwiftPM fue omitida.",
            authorization,
        )
    report = _validate_manifest(root)
    if report["remote_dependencies"] and not (root / "Package.resolved").is_file():
        return _lockfile_unavailable_result(skill_name, root, authorization)
    tool = resolve_project_tool(root, "swift")
    if tool.path is None:
        return _tool_unavailable(skill_name, root, "swift", authorization)
    subcommand = "test" if tests else "build"
    with tempfile.TemporaryDirectory(prefix="elyndra-swift-") as temp_dir:
        temp = Path(temp_dir)
        scratch = temp / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        argv = [
            str(tool.path),
            *_swiftpm_argv(subcommand, root, settings, scratch=str(scratch))[1:],
        ]
        result = run_controlled_process(
            argv,
            cwd=root,
            timeout_seconds=settings["timeout_seconds"],
            max_output_chars=settings["max_output_chars"],
            environment=_swift_environment(temp),
        )
    label = "Tests" if tests else "Build"
    return _process_result(
        skill_name,
        root,
        tool,
        argv,
        result,
        authorization,
        success=f"{label} Swift finalizó correctamente.",
        failure=f"{label} Swift encontró problemas.",
        extra={
            "configuration": settings["configuration"],
            "automatic_resolution": False,
            "network_isolation": False,
            "proxy_environment_restricted": True,
            "artifacts_external": True,
            "manifest_executed": True,
            "plugins_may_execute": report["plugins_detected"],
            "sandboxed": False,
        },
    )


def _swiftpm_argv(
    subcommand: str,
    root: Path,
    settings: dict[str, Any],
    *,
    scratch: str,
) -> list[str]:
    return [
        "swift",
        subcommand,
        "--package-path",
        str(root),
        "--disable-automatic-resolution",
        "--scratch-path",
        scratch,
        "--configuration",
        settings["configuration"],
    ]


def _swift_environment(temp: Path) -> dict[str, str]:
    home = temp / "home"
    cache = temp / "cache"
    modules = temp / "module-cache"
    tmp = temp / "tmp"
    for path in (home, cache, modules, tmp):
        path.mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(home),
        "XDG_CACHE_HOME": str(cache),
        "TMPDIR": str(tmp),
        "CLANG_MODULE_CACHE_PATH": str(modules),
        "SWIFTPM_MODULECACHE_OVERRIDE": str(modules),
        "GIT_TERMINAL_PROMPT": "0",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "localhost,127.0.0.1",
    }


def _run_per_file_tool(
    skill_name: str,
    root: Path,
    tool: ToolResolution,
    files: list[Path],
    settings: dict[str, Any],
    authorization: dict[str, Any],
    *,
    command: Any,
    success: str,
    failure: str,
) -> SkillResult:
    started = time.perf_counter()
    failures: list[dict[str, Any]] = []
    duration_ms = 0
    timed_out = False
    last_argv: list[str] = []
    for path in files:
        elapsed = time.perf_counter() - started
        remaining = settings["timeout_seconds"] - int(elapsed)
        if remaining <= 0:
            timed_out = True
            break
        argv = command(path)
        last_argv = argv
        result = run_controlled_process(
            argv,
            cwd=root,
            timeout_seconds=remaining,
            max_output_chars=settings["max_output_chars"],
        )
        duration_ms += result.duration_ms
        if result.timed_out:
            timed_out = True
        if result.returncode != 0 or result.timed_out:
            failures.append(
                {
                    "path": str(path.relative_to(root)),
                    "returncode": result.returncode,
                    "output": _bounded_text(result.output, 1000),
                }
            )
            if settings["fail_fast"] or len(failures) >= 25:
                break
    ok = not failures and not timed_out
    lines = [
        success if ok else failure,
        "",
        f"- Proyecto: `{root}`",
        f"- Herramienta: `{tool.path}`",
        f"- Archivos examinados: `{len(files)}`",
        f"- Fallos: `{len(failures)}`",
        f"- Timeout: `{'sí' if timed_out else 'no'}`",
        f"- Duración: `{duration_ms} ms`",
    ]
    for item in failures[:10]:
        lines.extend(("", f"- `{item['path']}`: {item['output'] or 'falló'}"))
    return SkillResult(
        ok,
        "\n".join(lines),
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "tool_path": str(tool.path),
            "tool_source": tool.source,
            "command_argv": last_argv,
            "files_examined": len(files),
            "failures": failures,
            "duration_ms": duration_ms,
            "timed_out": timed_out,
            "returncode": 0 if ok else 1,
            "stage_status": "passed" if ok else "failed",
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
        if list(current.glob("*.xcodeproj")) or list(current.glob("*.xcworkspace")):
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
    effective = context.swift_profiles.effective_settings(
        root,
        default_timeout_seconds=context.config.swift_tool_timeout_seconds,
        default_max_output_chars=context.config.swift_tool_max_output_chars,
    )
    profile = effective["profile"] or {}
    return {
        "profile": effective["profile"],
        "timeout_seconds": int(effective["timeout_seconds"]),
        "max_output_chars": int(effective["max_output_chars"]),
        "max_swift_files": _bounded_files(
            params.get("max_files"),
            int(effective["max_swift_files"]),
        ),
        "exclude_paths": list(effective["exclude_paths"]),
        "manifest_enabled": _setting(params, profile, "manifest_enabled", True),
        "syntax_enabled": _setting(params, profile, "syntax_enabled", True),
        "format_enabled": _setting(params, profile, "format_enabled", True),
        "build_enabled": _setting(params, profile, "build_enabled", True),
        "tests_enabled": _setting(params, profile, "tests_enabled", True),
        "configuration": _configuration(
            params.get("configuration"),
            profile.get("configuration", "debug"),
        ),
        "fail_fast": _setting(params, profile, "fail_fast", False),
        "require_tools": _setting(params, profile, "require_tools", False),
    }


def _setting(
    params: dict[str, Any],
    profile: dict[str, Any],
    name: str,
    default: bool,
) -> bool:
    if name in params:
        value = params[name]
        if not isinstance(value, bool):
            raise ValueError(f"{name} debe ser booleano.")
        return value
    return bool(profile.get(name, default))


def _configuration(value: Any, current: Any) -> str:
    selected = str(current if value is None else value).strip().casefold()
    if selected not in {"debug", "release"}:
        raise ValueError("configuration debe ser debug o release.")
    return selected


def _bounded_files(value: Any, default: int) -> int:
    selected = default if value is None else int(value)
    if not 1 <= selected <= 20_000:
        raise ValueError("max_files debe estar entre 1 y 20000.")
    return selected


def _collect_swift_files(
    target: Path,
    *,
    root: Path,
    exclude_paths: list[str],
    max_files: int,
) -> tuple[list[Path], bool]:
    if target.is_file():
        valid = target.suffix.casefold() == _SWIFT_EXTENSION
        return ([target] if valid else []), False
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
            if candidate.suffix.casefold() != _SWIFT_EXTENSION:
                continue
            if candidate.name == "Package.swift" or _is_excluded(candidate, excluded):
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
    files, truncated = _collect_swift_files(
        root,
        root=root,
        exclude_paths=settings["exclude_paths"],
        max_files=settings["max_swift_files"],
    )
    report = _validate_manifest(root)
    frameworks = _frameworks(files)
    test_count = sum(
        1
        for path in files
        if "Tests" in path.relative_to(root).parts
        or path.name.endswith(("Tests.swift", "Test.swift"))
    )
    xcode_projects = sorted(path.name for path in root.glob("*.xcodeproj"))
    xcode_workspaces = sorted(path.name for path in root.glob("*.xcworkspace"))
    return {
        "project_root": str(root),
        "swift_files": len(files),
        "test_files": test_count,
        "truncated": truncated,
        "manifest": report["manifest_exists"],
        "resolved": (root / "Package.resolved").is_file(),
        "package_name": report["package_name"],
        "tools_version": report["tools_version"],
        "dependencies": report["dependencies"],
        "remote_dependencies": report["remote_dependencies"],
        "local_dependencies": report["local_dependencies"],
        "targets": report["targets"],
        "products": report["products"],
        "plugins_detected": report["plugins_detected"],
        "frameworks": frameworks,
        "xcode_projects": xcode_projects,
        "xcode_workspaces": xcode_workspaces,
        "tools": {
            name: resolve_project_tool(root, name).path is not None
            for name in ("swift", "swiftc", "swift-format")
        },
        "manifest_executed": False,
        "automatic_resolution": False,
    }


def _frameworks(files: list[Path]) -> list[str]:
    found: set[str] = set()
    for path in files[:500]:
        try:
            text = path.read_text(encoding="utf-8", errors="strict")[:100_000]
        except (OSError, UnicodeError):
            continue
        for token, label in _FRAMEWORK_HINTS.items():
            if token in text:
                found.add(label)
    return sorted(found)


def _validate_manifest(root: Path) -> dict[str, Any]:
    manifest = root / "Package.swift"
    errors: list[str] = []
    warnings: list[str] = []
    if not manifest.is_file():
        return {
            "manifest_exists": False,
            "package_name": "",
            "tools_version": "",
            "dependencies": 0,
            "remote_dependencies": 0,
            "local_dependencies": 0,
            "targets": 0,
            "products": 0,
            "plugins_detected": False,
            "errors": [],
            "warnings": [],
        }
    try:
        text = manifest.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        errors.append("Package.swift no puede leerse como UTF-8.")
        text = ""
    tools_match = _TOOLS_VERSION.search(text)
    package_match = _PACKAGE_NAME.search(text)
    dependencies = _DEPENDENCY.findall(text)
    remote = sum(1 for item in dependencies if "url:" in item)
    local = sum(1 for item in dependencies if "path:" in item)
    if text and tools_match is None:
        errors.append("Package.swift no declara // swift-tools-version:.")
    if text and "Package(" not in text:
        errors.append("Package.swift no contiene una declaración Package(...).")
    if text and package_match is None:
        warnings.append("No se pudo extraer el nombre del paquete sin evaluar el manifiesto.")
    delimiter_error = _delimiter_error(text)
    if delimiter_error:
        errors.append(delimiter_error)
    plugins = ".plugin(" in text or ".macro(" in text or "CompilerPlugin" in text
    if plugins:
        warnings.append("Se detectaron plugins o macros; build/tests pueden ejecutar código.")
    if ".unsafeFlags(" in text:
        warnings.append("Package.swift declara unsafeFlags; revísalos antes de compilar.")
    if remote and not (root / "Package.resolved").is_file():
        warnings.append(
            "Hay dependencias remotas y falta Package.resolved; build/tests se bloquearán."
        )
    if list(root.glob("*.xcodeproj")) and not manifest.is_file():
        warnings.append("Proyecto Xcode detectado; esta versión ejecuta solo SwiftPM.")
    return {
        "manifest_exists": True,
        "package_name": package_match.group(1) if package_match else "",
        "tools_version": tools_match.group(1) if tools_match else "",
        "dependencies": len(dependencies),
        "remote_dependencies": remote,
        "local_dependencies": local,
        "targets": len(_TARGET.findall(text)),
        "products": len(_PRODUCT.findall(text)),
        "plugins_detected": plugins,
        "errors": errors,
        "warnings": warnings,
    }


def _delimiter_error(text: str) -> str:
    pairs = {"(": ")", "[": "]", "{": "}"}
    closing = {value: key for key, value in pairs.items()}
    stack: list[str] = []
    quote = ""
    escaped = False
    line_comment = False
    block_depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_depth:
            if char == "/" and next_char == "*":
                block_depth += 1
                index += 2
                continue
            if char == "*" and next_char == "/":
                block_depth -= 1
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
        if char == "/" and next_char == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            block_depth = 1
            index += 2
            continue
        if char in {'"', "'"}:
            quote = char
        elif char in pairs:
            stack.append(char)
        elif char in closing and (not stack or stack.pop() != closing[char]):
            return f"Delimitador inesperado en Package.swift: {char}."
        index += 1
    if quote:
        return "Package.swift contiene una cadena sin cerrar."
    if block_depth:
        return "Package.swift contiene un comentario de bloque sin cerrar."
    if stack:
        return f"Package.swift contiene un delimitador sin cerrar: {stack[-1]}."
    return ""


def _format_inventory(inventory: dict[str, Any], authorization: dict[str, Any]) -> str:
    frameworks = ", ".join(inventory["frameworks"]) or "no detectado"
    xcode = len(inventory["xcode_projects"]) + len(inventory["xcode_workspaces"])
    return "\n".join(
        (
            "Inspección Swift completada sin ejecutar código.",
            "",
            f"- Proyecto: `{inventory['project_root']}`",
            f"- Archivos Swift: `{inventory['swift_files']}`",
            f"- Tests detectados: `{inventory['test_files']}`",
            f"- Package.swift: `{'sí' if inventory['manifest'] else 'no'}`",
            f"- Package.resolved: `{'sí' if inventory['resolved'] else 'no'}`",
            f"- Paquete: `{inventory['package_name'] or '-'}`",
            f"- Swift tools: `{inventory['tools_version'] or '-'}`",
            f"- Dependencias: `{inventory['dependencies']}`",
            f"- Proyectos/workspaces Xcode: `{xcode}`",
            f"- Frameworks/librerías: `{frameworks}`",
            f"- Autorización: `{authorization['authorization_scope']}`",
        )
    )


def _process_result(
    skill_name: str,
    root: Path,
    tool: ToolResolution,
    argv: list[str],
    result: ProcessResult,
    authorization: dict[str, Any],
    *,
    success: str,
    failure: str,
    extra: dict[str, Any] | None = None,
) -> SkillResult:
    ok = result.returncode == 0 and not result.timed_out
    lines = [
        success if ok else failure,
        "",
        f"- Proyecto: `{root}`",
        f"- Herramienta: `{tool.path}`",
        f"- Exit code: `{result.returncode}`",
        f"- Timeout: `{'sí' if result.timed_out else 'no'}`",
        f"- Duración: `{result.duration_ms} ms`",
    ]
    if result.output.strip():
        lines.extend(("", result.output.strip()))
    return SkillResult(
        ok,
        "\n".join(lines),
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
            "stage_status": "passed" if ok else "failed",
            "shell": False,
            **(extra or {}),
            **authorization,
        },
    )


def _tool_unavailable(
    skill_name: str,
    root: Path,
    tool_name: str,
    authorization: dict[str, Any],
) -> SkillResult:
    return SkillResult(
        False,
        f"No se encontró la herramienta requerida: {tool_name}.",
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "tool_name": tool_name,
            "tool_unavailable": True,
            "stage_status": "unavailable",
            "shell": False,
            **authorization,
        },
    )


def _lockfile_unavailable_result(
    skill_name: str,
    root: Path,
    authorization: dict[str, Any],
) -> SkillResult:
    return SkillResult(
        False,
        (
            "Package.resolved no existe para dependencias remotas; la etapa no se "
            "ejecutó para evitar resolución o cambios automáticos."
        ),
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "lockfile_missing": True,
            "stage_status": "unavailable",
            "shell": False,
            **authorization,
        },
    )


def _skipped_result(
    skill_name: str,
    root: Path,
    message: str,
    authorization: dict[str, Any],
) -> SkillResult:
    return SkillResult(
        True,
        message,
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "stage_status": "skipped",
            "shell": False,
            **authorization,
        },
    )


def _file_limit_result(
    skill_name: str,
    root: Path,
    settings: dict[str, Any],
    authorization: dict[str, Any],
) -> SkillResult:
    limit = settings["max_swift_files"]
    return SkillResult(
        False,
        f"El proyecto supera el límite de {limit} archivos Swift.",
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "file_limit_exceeded": True,
            "stage_status": "failed",
            "shell": False,
            **authorization,
        },
    )


def _approval_details(
    skill_name: str,
    root: Path,
    scope: str,
    source: str,
    timeout_seconds: int,
    argv: list[str],
    risk_note: str,
) -> dict[str, Any]:
    return {
        "approval_summary": "\n".join(
            (
                f"Skill: {skill_name}",
                f"Proyecto: {root}",
                f"Ruta resuelta: {root}",
                f"Alcance de autorización: {scope}",
                f"Origen de autorización: {source}",
                f"Riesgo: medio. {risk_note}",
                f"Timeout: {timeout_seconds} segundos",
                f"Acción exacta: {' '.join(argv)}",
            )
        ),
        "resolved_path": str(root),
        "project_root": str(root),
        "authorization_scope": scope,
        "authorization_source": source,
        "timeout_seconds": timeout_seconds,
        "command_argv": argv,
        "risk_note": risk_note,
    }


def _stage_status(result: SkillResult) -> str:
    explicit = str(result.data.get("stage_status", "")).strip()
    if explicit in {"passed", "failed", "unavailable", "skipped"}:
        return explicit
    return "passed" if result.ok else "failed"


def _overall_status(stages: list[dict[str, Any]]) -> str:
    statuses = {str(item["status"]) for item in stages}
    if "failed" in statuses:
        return "failed"
    if "unavailable" in statuses:
        return "partial"
    return "passed"


def _enabled_stage_names(settings: dict[str, Any]) -> list[str]:
    names = ["inspect"]
    for stage, key in (
        ("manifest", "manifest_enabled"),
        ("syntax", "syntax_enabled"),
        ("format", "format_enabled"),
        ("build", "build_enabled"),
        ("tests", "tests_enabled"),
    ):
        if settings[key]:
            names.append(stage)
    return names


def _bounded_text(text: str, limit: int) -> str:
    clean = text.strip()
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"
