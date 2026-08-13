from __future__ import annotations

import json
import re
import stat
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.process import run_controlled_process
from elyndra.skills.tool_resolution import ToolResolution, resolve_project_tool

_SOURCE_EXTENSIONS = {".cs", ".fs", ".vb"}
_PROJECT_EXTENSIONS = {".csproj", ".fsproj", ".vbproj"}
_SOLUTION_EXTENSIONS = {".sln", ".slnx"}
_PROJECT_MARKERS = (
    "global.json",
    "Directory.Build.props",
    "Directory.Build.targets",
    "Directory.Packages.props",
)
_DEFAULT_EXCLUDES = {
    ".git",
    ".idea",
    ".vscode",
    "artifacts",
    "bin",
    "obj",
    "packages",
    "TestResults",
}
_FRAMEWORK_HINTS = {
    "Microsoft.NET.Sdk.Web": "ASP.NET Core",
    "Microsoft.AspNetCore": "ASP.NET Core",
    "Microsoft.EntityFrameworkCore": "Entity Framework Core",
    "Microsoft.NET.Test.Sdk": ".NET Test SDK",
    "xunit": "xUnit",
    "NUnit": "NUnit",
    "MSTest": "MSTest",
    "Microsoft.Maui": ".NET MAUI",
    "Microsoft.AspNetCore.Components": "Blazor",
    "Avalonia": "Avalonia",
    "Serilog": "Serilog",
}


class DotnetProjectInspectSkill:
    name = "dotnet.project_inspect"
    description = "Inspecciona un proyecto C#/.NET sin ejecutar MSBuild ni código."
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
            context.config.dotnet_tool_timeout_seconds,
            ["inspect-dotnet-project", str(root)],
            "Solo se leen nombres y metadatos acotados; no se ejecuta MSBuild.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        inventory = _inspect_project(root, settings, requested_target=target)
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


class DotnetDescriptorValidateSkill:
    name = "dotnet.descriptor_validate"
    description = "Valida soluciones y archivos MSBuild sin ejecutar targets o tareas."
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
            context.config.dotnet_tool_timeout_seconds,
            ["validate-dotnet-descriptors", str(root)],
            "Se analiza XML/JSON como datos; no se ejecutan targets MSBuild.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        report = _validate_descriptors(root)
        ok = not report["errors"]
        lines = [
            "Descriptores .NET válidos." if ok else "Descriptores .NET con errores.",
            "",
            f"- Proyecto: `{root}`",
            f"- Proyectos MSBuild: `{report['project_count']}`",
            f"- Soluciones: `{report['solution_count']}`",
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


class DotnetFormatCheckSkill:
    name = "dotnet.format_check"
    description = "Comprueba dotnet format sin restaurar paquetes ni modificar fuentes."
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
            ["dotnet", "format", "<proyecto-o-solución>", "--verify-no-changes", "--no-restore"],
            "Puede cargar analizadores ya presentes, pero no aplica cambios ni restaura paquetes.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _execute_dotnet_stage(context, params, skill_name=self.name, action="format")


class DotnetBuildSkill:
    name = "dotnet.build_project"
    description = "Compila .NET sin restore y dirige artefactos fuera del proyecto."
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
            [
                "dotnet",
                "build",
                "<proyecto-o-solución>",
                "--no-restore",
                "--artifacts-path",
                "<temporal>",
            ],
            "MSBuild puede ejecutar targets y tareas del proyecto; no se permite restore.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _execute_dotnet_stage(context, params, skill_name=self.name, action="build")


class DotnetTestSkill:
    name = "dotnet.test_project"
    description = "Ejecuta tests .NET sin restore y con artefactos temporales externos."
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
            [
                "dotnet",
                "test",
                "<proyecto-o-solución>",
                "--no-restore",
                "--artifacts-path",
                "<temporal>",
            ],
            "Los tests y targets MSBuild ejecutan código del proyecto; no se permite restore.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        return _execute_dotnet_stage(context, params, skill_name=self.name, action="test")


class DotnetVerifyProjectSkill:
    name = "dotnet.verify_project"
    description = "Ejecuta la verificación C#/.NET completa y guarda historial comparable."
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
            ["dotnet-verify", *_enabled_stage_names(settings), str(root)],
            "Build/tests pueden ejecutar targets y código; todas las etapas usan argumentos fijos.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        profile = settings.get("profile") or {}
        plan = {
            "stages": _enabled_stage_names(settings),
            "configuration": settings["configuration"],
            "require_tools": settings["require_tools"],
            "fail_fast": settings["fail_fast"],
        }
        run_id = context.verification_runs.start(
            toolchain="dotnet",
            project_root=root,
            profile_id=profile.get("id"),
            actor=context.actor,
            plan=plan,
        )
        started = time.perf_counter()
        stages: list[dict[str, Any]] = []
        inspect_result = DotnetProjectInspectSkill().execute(context, params)
        stages.append(
            {
                "name": "inspect",
                "status": _stage_status(inspect_result),
                "message": _bounded_text(inspect_result.message.splitlines()[0], 240),
            }
        )
        stage_specs = (
            ("descriptor", settings["descriptor_enabled"], DotnetDescriptorValidateSkill()),
            ("format", settings["format_enabled"], DotnetFormatCheckSkill()),
            ("build", settings["build_enabled"], DotnetBuildSkill()),
            ("tests", settings["tests_enabled"], DotnetTestSkill()),
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
            "passed": "Verificación .NET correcta.",
            "partial": "Verificación .NET parcial.",
            "failed": "Verificación .NET fallida.",
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
                **authorization,
            },
        )


def _execute_dotnet_stage(
    context: SkillContext,
    params: dict[str, Any],
    *,
    skill_name: str,
    action: str,
) -> SkillResult:
    requested = _resolve_existing_path(params)
    root = _discover_project_root(requested)
    settings = _pipeline_settings(context, root, params)
    authorization = _authorize_project(context, root, params, settings=settings)
    target, target_error = _select_target(root, requested)
    if target is None:
        return _skipped_result(skill_name, root, target_error, authorization)
    tool = resolve_project_tool(root, "dotnet")
    if tool.path is None:
        return _tool_unavailable(skill_name, root, "dotnet", authorization)
    if action in {"build", "test"}:
        sdk_major = _dotnet_sdk_major(tool, root)
        if sdk_major is None:
            return _tool_unavailable(
                skill_name,
                root,
                "dotnet SDK con versión detectable",
                authorization,
            )
        if sdk_major < 8:
            return _unavailable_result(
                skill_name,
                root,
                (
                    "Se requiere .NET SDK 8 o superior para enviar todos los "
                    "artefactos fuera del proyecto."
                ),
                authorization,
            )
    with tempfile.TemporaryDirectory(prefix="elyndra-dotnet-") as temp_dir:
        temp = Path(temp_dir)
        environment = _dotnet_environment(temp)
        if action == "format":
            argv = [
                str(tool.path),
                "format",
                str(target),
                "--verify-no-changes",
                "--no-restore",
                "--verbosity",
                "minimal",
            ]
        else:
            argv = [
                str(tool.path),
                action,
                str(target),
                "--configuration",
                settings["configuration"],
                "--no-restore",
                "--nologo",
                "--verbosity",
                "minimal",
                "--disable-build-servers",
                "--artifacts-path",
                str(temp / "artifacts"),
            ]
            if action == "test":
                argv.extend(("--logger", "console;verbosity=minimal"))
        result = run_controlled_process(
            argv,
            cwd=root,
            timeout_seconds=settings["timeout_seconds"],
            max_output_chars=settings["max_output_chars"],
            environment=environment,
        )
    if action == "format" and result.returncode != 0 and _format_unavailable(result.output):
        return _unavailable_result(
            skill_name,
            root,
            "dotnet format no está disponible en el SDK detectado.",
            authorization,
        )
    labels = {
        "format": ("Formato .NET correcto.", "dotnet format encontró cambios o problemas."),
        "build": ("Build .NET finalizó correctamente.", "Build .NET encontró problemas."),
        "test": ("Tests .NET finalizaron correctamente.", "Tests .NET encontraron problemas."),
    }
    success, failure = labels[action]
    return _process_result(
        skill_name,
        root,
        tool,
        argv,
        result,
        authorization,
        success=success,
        failure=failure,
        extra={
            "target": str(target),
            "configuration": settings["configuration"],
            "network_isolation": False,
            "proxy_environment_restricted": True,
            "restore_executed": False,
            "artifacts_external": action in {"build", "test"},
        },
    )


def _dotnet_environment(temp: Path) -> dict[str, str]:
    home = temp / "home"
    home.mkdir(parents=True, exist_ok=True)
    (temp / "tmp").mkdir(parents=True, exist_ok=True)
    return {
        "DOTNET_CLI_HOME": str(home),
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
        "DOTNET_NOLOGO": "1",
        "DOTNET_CLI_DO_NOT_USE_MSBUILD_SERVER": "1",
        "MSBUILDDISABLENODEREUSE": "1",
        "NUGET_XMLDOC_MODE": "skip",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost",
        "TMPDIR": str(temp / "tmp"),
    }


def _dotnet_sdk_major(tool: ToolResolution, root: Path) -> int | None:
    if tool.path is None:
        return None
    with tempfile.TemporaryDirectory(prefix="elyndra-dotnet-version-") as temp_dir:
        result = run_controlled_process(
            [str(tool.path), "--version"],
            cwd=root,
            timeout_seconds=10,
            max_output_chars=1000,
            environment=_dotnet_environment(Path(temp_dir)),
        )
    if result.returncode != 0:
        return None
    match = re.match(r"\s*(\d+)", result.stdout)
    return int(match.group(1)) if match else None


def _format_unavailable(output: str) -> bool:
    normalized = output.casefold()
    return any(
        token in normalized
        for token in (
            "could not execute because the specified command or file was not found",
            "no executable found matching command",
            "dotnet-format does not exist",
        )
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
        if _has_dotnet_marker(current):
            return current
        if current.parent == current:
            return start.resolve(strict=False)
        current = current.parent


def _has_dotnet_marker(path: Path) -> bool:
    if any((path / marker).is_file() for marker in _PROJECT_MARKERS):
        return True
    return any(
        item.is_file()
        and (item.suffix.casefold() in _PROJECT_EXTENSIONS | _SOLUTION_EXTENSIONS)
        for item in path.iterdir()
    )


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
    effective = context.dotnet_profiles.effective_settings(
        root,
        default_timeout_seconds=context.config.dotnet_tool_timeout_seconds,
        default_max_output_chars=context.config.dotnet_tool_max_output_chars,
    )
    profile = effective["profile"] or {}
    configuration = str(
        params.get("configuration")
        if params.get("configuration") is not None
        else profile.get("configuration", "Debug")
    )
    if configuration not in {"Debug", "Release"}:
        raise ValueError("configuration debe ser Debug o Release.")
    return {
        "profile": effective["profile"],
        "timeout_seconds": int(effective["timeout_seconds"]),
        "max_output_chars": int(effective["max_output_chars"]),
        "max_dotnet_files": _bounded_files(
            params.get("max_files"), int(effective["max_dotnet_files"])
        ),
        "exclude_paths": list(effective["exclude_paths"]),
        "descriptor_enabled": _setting(params, profile, "descriptor_enabled", True),
        "format_enabled": _setting(params, profile, "format_enabled", True),
        "build_enabled": _setting(params, profile, "build_enabled", True),
        "tests_enabled": _setting(params, profile, "tests_enabled", True),
        "configuration": configuration,
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
        return params[name] is True
    if name in profile:
        return bool(profile[name])
    return default


def _bounded_files(value: Any, default: int) -> int:
    resolved = default if value is None else int(value)
    if not 1 <= resolved <= 20_000:
        raise ValueError("max_files debe estar entre 1 y 20000.")
    return resolved


def _collect_source_files(
    root: Path,
    settings: dict[str, Any],
) -> tuple[list[Path], bool]:
    excluded = {(root / value).resolve(strict=False) for value in settings["exclude_paths"]}
    files: list[Path] = []
    for path in root.rglob("*"):
        if len(files) >= settings["max_dotnet_files"]:
            return files, True
        if path.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve(strict=False)
        if _is_excluded(resolved, excluded):
            continue
        if resolved.suffix.casefold() in _SOURCE_EXTENSIONS:
            files.append(resolved)
    return sorted(files), False


def _is_excluded(path: Path, excluded: set[Path]) -> bool:
    return any(path == item or item in path.parents for item in excluded)


def _inspect_project(
    root: Path,
    settings: dict[str, Any],
    *,
    requested_target: Path,
) -> dict[str, Any]:
    source_files, truncated = _collect_source_files(root, settings)
    projects = sorted(
        path
        for path in root.rglob("*")
        if (
            path.is_file()
            and not path.is_symlink()
            and path.suffix.casefold() in _PROJECT_EXTENSIONS
        )
    )
    solutions = sorted(
        path
        for path in root.rglob("*")
        if (
            path.is_file()
            and not path.is_symlink()
            and path.suffix.casefold() in _SOLUTION_EXTENSIONS
        )
    )
    project_data = [_read_project(path) for path in projects[:100]]
    target, target_error = _select_target(root, requested_target)
    frameworks = sorted(
        {
            framework
            for item in project_data
            for framework in item.get("frameworks", [])
        }
    )
    package_references = sorted(
        {
            package
            for item in project_data
            for package in item.get("package_references", [])
        }
    )
    languages = sorted(
        {
            {".cs": "C#", ".fs": "F#", ".vb": "Visual Basic"}[path.suffix.casefold()]
            for path in source_files
        }
    )
    return {
        "dotnet_files": len(source_files),
        "csharp_files": sum(path.suffix.casefold() == ".cs" for path in source_files),
        "fsharp_files": sum(path.suffix.casefold() == ".fs" for path in source_files),
        "visual_basic_files": sum(path.suffix.casefold() == ".vb" for path in source_files),
        "test_files": sum(_is_test_file(path) for path in source_files),
        "project_files": len(projects),
        "solutions": len(solutions),
        "languages": languages,
        "target_frameworks": sorted(
            {
                framework
                for item in project_data
                for framework in item.get("target_frameworks", [])
            }
        ),
        "frameworks": frameworks,
        "package_reference_count": len(package_references),
        "project_reference_count": sum(
            int(item.get("project_reference_count", 0)) for item in project_data
        ),
        "selected_target": str(target) if target is not None else None,
        "target_error": target_error,
        "global_json": (root / "global.json").is_file(),
        "directory_build_props": (root / "Directory.Build.props").is_file(),
        "directory_build_targets": (root / "Directory.Build.targets").is_file(),
        "directory_packages_props": (root / "Directory.Packages.props").is_file(),
        "nuget_config": any((root / name).is_file() for name in ("NuGet.config", "nuget.config")),
        "local_tool_manifest": (root / ".config" / "dotnet-tools.json").is_file(),
        "files_truncated": truncated,
        "dotnet_available": resolve_project_tool(root, "dotnet").path is not None,
        "restore_executed": False,
    }


def _read_project(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "target_frameworks": [],
        "package_references": [],
        "project_reference_count": 0,
        "frameworks": [],
    }
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError, UnicodeError):
        return result
    root = tree.getroot()
    values: list[str] = []
    packages: list[str] = []
    references = 0
    sdk = str(root.attrib.get("Sdk", ""))
    for element in root.iter():
        name = _xml_local_name(element.tag)
        if name in {"TargetFramework", "TargetFrameworks"} and element.text:
            values.extend(item.strip() for item in element.text.split(";") if item.strip())
        elif name == "PackageReference":
            include = str(
                element.attrib.get("Include") or element.attrib.get("Update") or ""
            ).strip()
            if include:
                packages.append(include)
        elif name == "ProjectReference":
            references += 1
    hints = [sdk, *packages]
    frameworks = sorted(
        {
            label
            for hint in hints
            for token, label in _FRAMEWORK_HINTS.items()
            if token.casefold() in hint.casefold()
        }
    )
    result.update(
        {
            "target_frameworks": sorted(set(values)),
            "package_references": sorted(set(packages)),
            "project_reference_count": references,
            "frameworks": frameworks,
        }
    )
    return result


def _validate_descriptors(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    projects = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in _PROJECT_EXTENSIONS
    )
    solutions = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in _SOLUTION_EXTENSIONS
    )
    xml_files = [
        *projects,
        *(
            root / name
            for name in _PROJECT_MARKERS
            if name.endswith((".props", ".targets")) and (root / name).is_file()
        ),
    ]
    for path in xml_files:
        try:
            tree = ET.parse(path)
        except (ET.ParseError, OSError, UnicodeError) as exc:
            errors.append(f"{path.relative_to(root)} no contiene XML válido: {exc}")
            continue
        if path.suffix.casefold() in _PROJECT_EXTENSIONS:
            document = tree.getroot()
            if _xml_local_name(document.tag) != "Project":
                errors.append(f"{path.relative_to(root)} no tiene raíz Project.")
            data = _read_project(path)
            if not data["target_frameworks"]:
                warnings.append(f"{path.relative_to(root)} no declara TargetFramework(s).")
    global_json = root / "global.json"
    if global_json.is_file():
        try:
            payload = json.loads(global_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeError) as exc:
            errors.append(f"global.json no contiene JSON válido: {exc}")
        else:
            if not isinstance(payload, dict):
                errors.append("global.json debe contener un objeto JSON.")
            elif "sdk" in payload and not isinstance(payload["sdk"], dict):
                errors.append("global.json.sdk debe ser un objeto.")
    for solution in solutions:
        try:
            text = solution.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{solution.relative_to(root)} no es UTF-8 legible: {exc}")
            continue
        if (
            solution.suffix.casefold() == ".sln"
            and "Microsoft Visual Studio Solution File" not in text[:300]
        ):
            warnings.append(
                f"{solution.relative_to(root)} no contiene el encabezado habitual de .sln."
            )
    if (root / "Directory.Build.targets").is_file():
        warnings.append("Directory.Build.targets puede ejecutar tareas durante build y tests.")
    if (root / ".config" / "dotnet-tools.json").is_file():
        warnings.append(
            "Existe un manifiesto de herramientas locales; "
            "Elyndra no ejecuta dotnet tool restore."
        )
    if not projects and not solutions:
        warnings.append("No se encontraron proyectos ni soluciones .NET.")
    return {
        "errors": errors,
        "warnings": warnings,
        "project_count": len(projects),
        "solution_count": len(solutions),
    }


def _select_target(root: Path, requested: Path) -> tuple[Path | None, str]:
    if (
        requested.is_file()
        and requested.suffix.casefold() in _PROJECT_EXTENSIONS | _SOLUTION_EXTENSIONS
    ):
        return requested, ""
    top_solutions = sorted(
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.casefold() in _SOLUTION_EXTENSIONS
    )
    if len(top_solutions) == 1:
        return top_solutions[0], ""
    if len(top_solutions) > 1:
        return None, "Hay varias soluciones en la raíz; indica una ruta .sln o .slnx concreta."
    top_projects = sorted(
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.casefold() in _PROJECT_EXTENSIONS
    )
    if len(top_projects) == 1:
        return top_projects[0], ""
    if len(top_projects) > 1:
        return None, "Hay varios proyectos en la raíz; indica un archivo *proj concreto."
    nested_projects = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in _PROJECT_EXTENSIONS
    )
    if len(nested_projects) == 1:
        return nested_projects[0], ""
    return None, "No se detectó un proyecto o solución .NET único para ejecutar."


def _is_test_file(path: Path) -> bool:
    parts = [part.casefold() for part in path.parts]
    return (
        any(part in {"test", "tests"} or part.endswith((".test", ".tests")) for part in parts)
        or path.stem.casefold().endswith(("test", "tests"))
    )


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _format_inventory(inventory: dict[str, Any], authorization: dict[str, Any]) -> str:
    languages = ", ".join(inventory["languages"]) or "no detectado"
    frameworks = ", ".join(inventory["frameworks"]) or "no detectado"
    target = inventory["selected_target"] or "-"
    return "\n".join(
        (
            "Inspección .NET completada sin ejecutar código.",
            "",
            f"- Archivos .NET: `{inventory['dotnet_files']}`",
            (
                f"- C#: `{inventory['csharp_files']}`; "
                f"F#: `{inventory['fsharp_files']}`; "
                f"VB: `{inventory['visual_basic_files']}`"
            ),
            f"- Tests detectados: `{inventory['test_files']}`",
            f"- Proyectos: `{inventory['project_files']}`; soluciones: `{inventory['solutions']}`",
            f"- Lenguajes: `{languages}`",
            f"- Frameworks: `{frameworks}`",
            f"- Target seleccionado: `{target}`",
            f"- Autorización: `{authorization['authorization_scope']}`",
        )
    )


def _process_result(
    skill_name: str,
    root: Path,
    tool: ToolResolution,
    argv: list[str],
    result: Any,
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
    return _unavailable_result(
        skill_name,
        root,
        f"No se encontró la herramienta requerida: {tool_name}.",
        authorization,
    )


def _unavailable_result(
    skill_name: str,
    root: Path,
    message: str,
    authorization: dict[str, Any],
) -> SkillResult:
    return SkillResult(
        False,
        message,
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "tool_unavailable": True,
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


def _stage_status(result: SkillResult) -> str:
    explicit = str(result.data.get("stage_status", ""))
    if explicit in {"passed", "failed", "unavailable", "skipped"}:
        return explicit
    if result.data.get("tool_unavailable"):
        return "unavailable"
    return "passed" if result.ok else "failed"


def _overall_status(stages: list[dict[str, Any]]) -> str:
    statuses = {str(stage.get("status")) for stage in stages}
    if "failed" in statuses:
        return "failed"
    if "unavailable" in statuses:
        return "partial"
    return "passed"


def _enabled_stage_names(settings: dict[str, Any]) -> list[str]:
    names = ["inspect"]
    for name, key in (
        ("descriptor", "descriptor_enabled"),
        ("format", "format_enabled"),
        ("build", "build_enabled"),
        ("tests", "tests_enabled"),
    ):
        if settings[key]:
            names.append(name)
    return names


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


def _bounded_text(value: str, limit: int) -> str:
    clean = value.strip()
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"
