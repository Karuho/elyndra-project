from __future__ import annotations

import os
import re
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.process import ProcessResult, run_controlled_process
from elyndra.skills.tool_resolution import ToolResolution, resolve_project_tool

_DART_EXTENSION = ".dart"
_PROJECT_MARKERS = (
    "pubspec.yaml",
    "pubspec.lock",
    "analysis_options.yaml",
    ".metadata",
)
_DEFAULT_EXCLUDES = {
    ".dart_tool",
    ".git",
    ".idea",
    ".vscode",
    "android/.gradle",
    "build",
    "coverage",
    "ios/Pods",
    "linux/flutter/ephemeral",
    "macos/Pods",
    "test/fixtures",
    "web/.dart_tool",
    "windows/flutter/ephemeral",
}
_PACKAGE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_FRAMEWORK_HINTS = {
    "flutter": "Flutter",
    "flutter_bloc": "BLoC",
    "flutter_riverpod": "Riverpod",
    "provider": "Provider",
    "get": "GetX",
    "shelf": "Shelf",
    "dart_frog": "Dart Frog",
    "aqueduct": "Aqueduct",
    "drift": "Drift",
    "firebase_core": "Firebase",
    "dio": "Dio",
}


class DartProjectInspectSkill:
    name = "dart.project_inspect"
    description = "Inspecciona un proyecto Dart o Flutter sin ejecutar herramientas."
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
            context.config.dart_tool_timeout_seconds,
            ["inspect-dart-project", str(root)],
            "Solo se leen rutas, YAML y metadatos acotados; no se ejecuta Dart ni Flutter.",
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


class DartDescriptorValidateSkill:
    name = "dart.descriptor_validate"
    description = "Valida pubspec.yaml y analysis_options.yaml sin ejecutar pub."
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
            context.config.dart_tool_timeout_seconds,
            ["validate-dart-descriptors", str(root)],
            "Los YAML se interpretan como datos; no se ejecutan pub get ni scripts.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        report = _validate_descriptors(root, settings)
        ok = not report["errors"]
        lines = [
            "Descriptores Dart válidos." if ok else "Descriptores Dart con errores.",
            "",
            f"- Proyecto: `{root}`",
            f"- pubspec.yaml: `{'sí' if report['pubspec'] else 'no'}`",
            f"- analysis_options.yaml: `{'sí' if report['analysis_options'] else 'no'}`",
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


class DartFormatCheckSkill:
    name = "dart.format_check"
    description = "Comprueba dart format sin modificar archivos."
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
                "dart",
                "format",
                "--output=none",
                "--set-exit-if-changed",
                "<archivo.dart>",
            ],
            "dart format solo comprueba diferencias; no reescribe el proyecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        files, truncated = _collect_dart_files(
            target,
            root=root,
            exclude_paths=settings["exclude_paths"],
            max_files=settings["max_dart_files"],
        )
        if truncated:
            return _file_limit_result(self.name, root, settings, authorization)
        if not files:
            return _skipped_result(
                self.name,
                root,
                "No se encontraron archivos Dart para comprobar formato.",
                authorization,
            )
        tool = resolve_project_tool(root, "dart")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "dart", authorization)
        started = time.perf_counter()
        results: list[tuple[Path, ProcessResult]] = []
        for path in files:
            elapsed = time.perf_counter() - started
            remaining = max(1, settings["timeout_seconds"] - int(elapsed))
            argv = [
                str(tool.path),
                "format",
                "--output=none",
                "--set-exit-if-changed",
                str(path),
            ]
            result = run_controlled_process(
                argv,
                cwd=root,
                timeout_seconds=remaining,
                max_output_chars=min(settings["max_output_chars"], 4000),
                environment=_dart_environment(),
            )
            results.append((path, result))
            failed = result.returncode != 0 or result.timed_out
            if failed and (settings["fail_fast"] or result.timed_out):
                break
        failures = [
            item
            for item in results
            if item[1].returncode != 0 or item[1].timed_out
        ]
        duration_ms = round((time.perf_counter() - started) * 1000)
        lines = [
            "Formato Dart correcto." if not failures else "dart format encontró diferencias.",
            "",
            f"- Proyecto: `{root}`",
            f"- Archivos examinados: `{len(results)}`",
            f"- Diferencias o fallos: `{len(failures)}`",
            f"- Timeout: `{'sí' if any(item[1].timed_out for item in results) else 'no'}`",
            f"- Duración: `{duration_ms} ms`",
        ]
        for path, result in failures[:20]:
            detail = result.output.strip() or f"exit code {result.returncode}"
            lines.extend(("", f"`{path.relative_to(root)}`", _bounded_text(detail, 3000)))
        ok = not failures
        return SkillResult(
            ok,
            "\n".join(lines),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "tool_path": str(tool.path),
                "tool_source": tool.source,
                "files_examined": len(results),
                "failures": len(failures),
                "duration_ms": duration_ms,
                "timed_out": any(item[1].timed_out for item in results),
                "modifies_files": False,
                "shell": False,
                "stage_status": "passed" if ok else "failed",
                **authorization,
            },
        )


class DartAnalyzeSkill:
    name = "dart.analyze"
    description = "Ejecuta dart analyze o flutter analyze con argumentos fijos."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        inventory = _inspect_project(root, settings)
        runner = _analysis_runner(inventory)
        argv = (
            ["flutter", "analyze", "--no-pub"]
            if runner == "flutter"
            else ["dart", "analyze", "--fatal-infos"]
        )
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            argv,
            "El analizador carga el paquete, pero no ejecuta pub get ni corrige archivos.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root, settings, authorization = _execution_context(context, params)
        inventory = _inspect_project(root, settings)
        if inventory["dart_files"] == 0:
            return _skipped_result(
                self.name,
                root,
                "No se encontraron archivos Dart para analizar.",
                authorization,
            )
        runner = _analysis_runner(inventory)
        tool = resolve_project_tool(root, runner)
        if tool.path is None:
            return _tool_unavailable(self.name, root, runner, authorization)
        argv = (
            [str(tool.path), "analyze", "--no-pub"]
            if runner == "flutter"
            else [str(tool.path), "analyze", "--fatal-infos"]
        )
        return _run_dart_command(
            self.name,
            root,
            tool,
            argv,
            settings,
            authorization,
            success="Análisis Dart/Flutter finalizó correctamente.",
            failure="El análisis Dart/Flutter encontró problemas.",
            extra={"runner": runner},
        )


class DartTestSkill:
    name = "dart.test_project"
    description = "Ejecuta dart test con argumentos fijos y sin pub get."
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
            ["dart", "test", "--reporter", "compact"],
            "dart test ejecuta código del proyecto; no ejecuta pub get.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root, settings, authorization = _execution_context(context, params)
        inventory = _inspect_project(root, settings)
        if inventory["test_files"] == 0:
            return _skipped_result(
                self.name,
                root,
                "No se detectaron tests Dart.",
                authorization,
            )
        tool = resolve_project_tool(root, "dart")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "dart", authorization)
        argv = [str(tool.path), "test", "--reporter", "compact"]
        return _run_dart_command(
            self.name,
            root,
            tool,
            argv,
            settings,
            authorization,
            success="Tests Dart finalizaron correctamente.",
            failure="Tests Dart encontraron problemas.",
            extra={"runner": "dart", "executes_project_code": True},
        )


class FlutterTestSkill:
    name = "flutter.test_project"
    description = "Ejecuta flutter test --no-pub con argumentos fijos."
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
            ["flutter", "test", "--no-pub", "--reporter", "compact"],
            "flutter test ejecuta código del proyecto; --no-pub evita resolver paquetes.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root, settings, authorization = _execution_context(context, params)
        inventory = _inspect_project(root, settings)
        if not inventory["is_flutter"]:
            return _skipped_result(
                self.name,
                root,
                "El proyecto no fue identificado como Flutter.",
                authorization,
            )
        if inventory["test_files"] == 0:
            return _skipped_result(
                self.name,
                root,
                "No se detectaron tests Flutter.",
                authorization,
            )
        tool = resolve_project_tool(root, "flutter")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "flutter", authorization)
        argv = [str(tool.path), "test", "--no-pub", "--reporter", "compact"]
        return _run_dart_command(
            self.name,
            root,
            tool,
            argv,
            settings,
            authorization,
            success="Tests Flutter finalizaron correctamente.",
            failure="Tests Flutter encontraron problemas.",
            extra={"runner": "flutter", "executes_project_code": True},
        )


class DartVerifyProjectSkill:
    name = "dart.verify_project"
    description = "Ejecuta la verificación Dart/Flutter y guarda historial comparable."
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
            ["dart-verify", *_enabled_stage_names(settings), str(root)],
            "Analyze carga el paquete y los tests ejecutan código; no se resuelven dependencias.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        inventory = _inspect_project(root, settings)
        test_runner = _resolved_test_runner(settings["test_runner"], inventory)
        started = time.perf_counter()
        profile = settings.get("profile") or {}
        run_id = context.verification_runs.start(
            toolchain="dart",
            project_root=root,
            actor=context.actor,
            profile_id=profile.get("id"),
            plan={
                "stages": _enabled_stage_names(settings),
                "test_runner": test_runner,
                "require_tools": settings["require_tools"],
                "fail_fast": settings["fail_fast"],
                "automatic_pub_get": False,
                "proxy_environment_restricted": True,
            },
        )
        test_skill: DartTestSkill | FlutterTestSkill
        test_skill = FlutterTestSkill() if test_runner == "flutter" else DartTestSkill()
        stage_specs = (
            ("inspect", True, DartProjectInspectSkill()),
            ("descriptor", settings["descriptor_enabled"], DartDescriptorValidateSkill()),
            ("format", settings["format_enabled"], DartFormatCheckSkill()),
            ("analyze", settings["analyze_enabled"], DartAnalyzeSkill()),
            ("tests", settings["tests_enabled"], test_skill),
        )
        stages: list[dict[str, Any]] = []
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
        summary = {
            "stages": stages,
            "authorization": authorization,
            "test_runner": test_runner,
        }
        run = context.verification_runs.finish(
            run_id,
            status=status,
            duration_ms=duration_ms,
            summary=summary,
        )
        heading = {
            "passed": "Verificación Dart/Flutter correcta.",
            "partial": "Verificación Dart/Flutter parcial.",
            "failed": "Verificación Dart/Flutter fallida.",
        }[status]
        lines = [
            heading,
            "",
            f"- Proyecto: `{root}`",
            f"- Ejecución: `{run_id}`",
            f"- Estado: `{status}`",
            f"- Runner de tests: `{test_runner}`",
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
                "test_runner": test_runner,
                "duration_ms": duration_ms,
                "shell": False,
                **authorization,
            },
        )


def _execution_context(
    context: SkillContext,
    params: dict[str, Any],
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root = _discover_project_root(_resolve_existing_path(params))
    settings = _pipeline_settings(context, root, params)
    authorization = _authorize_project(context, root, params, settings=settings)
    return root, settings, authorization


def _pipeline_settings(
    context: SkillContext,
    root: Path,
    params: dict[str, Any],
) -> dict[str, Any]:
    effective = context.dart_profiles.effective_settings(
        root,
        default_timeout_seconds=context.config.dart_tool_timeout_seconds,
        default_max_output_chars=context.config.dart_tool_max_output_chars,
    )
    profile = effective["profile"] or {}
    return {
        "profile": effective["profile"],
        "timeout_seconds": int(effective["timeout_seconds"]),
        "max_output_chars": int(effective["max_output_chars"]),
        "max_dart_files": _bounded_files(
            params.get("max_files"),
            int(effective["max_dart_files"]),
        ),
        "exclude_paths": list(effective["exclude_paths"]),
        "descriptor_enabled": _setting(params, profile, "descriptor_enabled", True),
        "format_enabled": _setting(params, profile, "format_enabled", True),
        "analyze_enabled": _setting(params, profile, "analyze_enabled", True),
        "tests_enabled": _setting(params, profile, "tests_enabled", True),
        "test_runner": _choice_setting(
            params.get("test_runner"),
            profile.get("test_runner", "auto"),
            {"auto", "dart", "flutter"},
            "test_runner",
        ),
        "fail_fast": _setting(params, profile, "fail_fast", False),
        "require_tools": _setting(params, profile, "require_tools", False),
    }


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
        if any((current / marker).exists() for marker in _PROJECT_MARKERS):
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


def _collect_dart_files(
    target: Path,
    *,
    root: Path,
    exclude_paths: list[str],
    max_files: int,
) -> tuple[list[Path], bool]:
    if target.is_file():
        return ([target] if target.suffix.casefold() == _DART_EXTENSION else []), False
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
            if candidate.suffix.casefold() != _DART_EXTENSION:
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


def _inspect_project(root: Path, settings: dict[str, Any]) -> dict[str, Any]:
    files, truncated = _collect_dart_files(
        root,
        root=root,
        exclude_paths=settings["exclude_paths"],
        max_files=settings["max_dart_files"],
    )
    pubspec, pubspec_error = _load_yaml(root / "pubspec.yaml", limit=500_000)
    mapping = pubspec if isinstance(pubspec, dict) else {}
    dependencies = _dependency_mapping(mapping.get("dependencies"))
    dev_dependencies = _dependency_mapping(mapping.get("dev_dependencies"))
    all_dependencies = {**dependencies, **dev_dependencies}
    environment = mapping.get("environment")
    sdk_constraint = ""
    if isinstance(environment, dict):
        sdk_constraint = str(environment.get("sdk") or "").strip()
    is_flutter = _is_flutter_project(root, dependencies)
    tests = [path for path in files if _is_test_file(path, root)]
    frameworks = [
        label
        for dependency, label in _FRAMEWORK_HINTS.items()
        if dependency in all_dependencies
    ]
    return {
        "project_root": str(root),
        "dart_files": len(files),
        "test_files": len(tests),
        "truncated": truncated,
        "pubspec": (root / "pubspec.yaml").is_file(),
        "pubspec_lock": (root / "pubspec.lock").is_file(),
        "analysis_options": (root / "analysis_options.yaml").is_file(),
        "package_config": (root / ".dart_tool" / "package_config.json").is_file(),
        "package_name": str(mapping.get("name") or "").strip(),
        "sdk_constraint": sdk_constraint,
        "dependencies": sorted(dependencies, key=str.casefold)[:100],
        "dev_dependencies": sorted(dev_dependencies, key=str.casefold)[:100],
        "is_flutter": is_flutter,
        "project_type": "flutter" if is_flutter else "dart",
        "frameworks": frameworks,
        "pubspec_error": pubspec_error,
        "tools": {
            name: resolve_project_tool(root, name).path is not None
            for name in ("dart", "flutter")
        },
    }


def _validate_descriptors(
    root: Path,
    settings: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    pubspec_path = root / "pubspec.yaml"
    analysis_path = root / "analysis_options.yaml"
    files, _ = _collect_dart_files(
        root,
        root=root,
        exclude_paths=settings["exclude_paths"],
        max_files=settings["max_dart_files"],
    )
    pubspec, pubspec_error = _load_yaml(pubspec_path, limit=500_000)
    analysis, analysis_error = _load_yaml(analysis_path, limit=300_000)
    if pubspec_error:
        errors.append(f"pubspec.yaml no es YAML UTF-8 válido: {pubspec_error}")
    if analysis_error:
        errors.append(
            f"analysis_options.yaml no es YAML UTF-8 válido: {analysis_error}"
        )
    if pubspec_path.is_file() and not isinstance(pubspec, dict):
        errors.append("pubspec.yaml debe contener un objeto YAML en la raíz.")
    if analysis_path.is_file() and analysis is not None and not isinstance(analysis, dict):
        errors.append("analysis_options.yaml debe contener un objeto YAML en la raíz.")
    mapping = pubspec if isinstance(pubspec, dict) else {}
    name = str(mapping.get("name") or "").strip()
    if mapping and not name:
        errors.append("pubspec.yaml no declara name.")
    elif name and _PACKAGE_NAME_RE.fullmatch(name) is None:
        errors.append("El nombre del paquete Dart no cumple el formato snake_case.")
    environment = mapping.get("environment")
    if (
        mapping
        and (
            not isinstance(environment, dict)
            or not str(environment.get("sdk") or "").strip()
        )
    ):
        warnings.append("pubspec.yaml no declara environment.sdk.")
    for key in ("dependencies", "dev_dependencies", "dependency_overrides"):
        value = mapping.get(key)
        if value is not None and not isinstance(value, dict):
            errors.append(f"{key} debe ser un objeto YAML.")
    for section in ("dependencies", "dev_dependencies", "dependency_overrides"):
        value = mapping.get(section)
        if not isinstance(value, dict):
            continue
        for dependency, specification in value.items():
            if not isinstance(specification, dict):
                continue
            local_path = specification.get("path")
            if isinstance(local_path, str) and local_path.strip():
                resolved = (root / local_path).resolve(strict=False)
                if resolved != root and root not in resolved.parents:
                    warnings.append(
                        f"La dependencia local {dependency} sale del proyecto: {local_path}"
                    )
            if "git" in specification:
                warnings.append(
                    f"La dependencia {dependency} usa git; Elyndra no la descargará."
                )
            if "hosted" in specification:
                warnings.append(
                    f"La dependencia {dependency} usa hosted; Elyndra no la resolverá."
                )
    if (root / "pubspec.lock").is_file() and not pubspec_path.is_file():
        warnings.append("pubspec.lock existe, pero falta pubspec.yaml.")
    if files and not pubspec_path.is_file():
        warnings.append("Hay archivos Dart, pero falta pubspec.yaml.")
    if not files and not pubspec_path.is_file():
        warnings.append("No se detectó un proyecto Dart o Flutter.")
    return {
        "pubspec": pubspec_path.is_file(),
        "pubspec_lock": (root / "pubspec.lock").is_file(),
        "analysis_options": analysis_path.is_file(),
        "package_name": name,
        "errors": errors,
        "warnings": warnings,
    }


def _load_yaml(path: Path, *, limit: int) -> tuple[Any, str]:
    if not path.is_file():
        return None, ""
    try:
        raw = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        return None, str(exc)
    if len(raw) > limit:
        return None, f"el archivo supera el límite de {limit} caracteres"
    try:
        return yaml.safe_load(raw), ""
    except yaml.YAMLError as exc:
        return None, str(exc).splitlines()[0]


def _dependency_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _is_flutter_project(root: Path, dependencies: dict[str, Any]) -> bool:
    flutter = dependencies.get("flutter")
    if isinstance(flutter, dict) and str(flutter.get("sdk") or "").casefold() == "flutter":
        return True
    return (
        (root / ".metadata").is_file()
        or (root / "lib" / "main.dart").is_file()
        and any((root / platform).is_dir() for platform in ("android", "ios", "web"))
    )


def _is_test_file(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return (
        path.name.endswith("_test.dart")
        or relative.parts[:1] in {("test",), ("integration_test",)}
    )


def _format_inventory(inventory: dict[str, Any], authorization: dict[str, Any]) -> str:
    frameworks = ", ".join(inventory["frameworks"]) or "no detectado"
    return "\n".join(
        (
            "Inspección Dart/Flutter completada sin ejecutar código.",
            "",
            f"- Proyecto: `{inventory['project_root']}`",
            f"- Archivos Dart: `{inventory['dart_files']}`",
            f"- Tests detectados: `{inventory['test_files']}`",
            f"- pubspec.yaml: `{'sí' if inventory['pubspec'] else 'no'}`",
            f"- pubspec.lock: `{'sí' if inventory['pubspec_lock'] else 'no'}`",
            f"- Tipo: `{inventory['project_type']}`",
            f"- Paquete: `{inventory['package_name'] or '-'}`",
            f"- SDK: `{inventory['sdk_constraint'] or '-'}`",
            f"- Frameworks: `{frameworks}`",
            f"- Autorización: `{authorization['authorization_scope']}`",
        )
    )


def _analysis_runner(inventory: dict[str, Any]) -> str:
    return "flutter" if inventory.get("is_flutter") else "dart"


def _resolved_test_runner(
    selected: str,
    inventory: dict[str, Any],
) -> str:
    if selected == "auto":
        return "flutter" if inventory.get("is_flutter") else "dart"
    return selected


def _run_dart_command(
    skill_name: str,
    root: Path,
    tool: ToolResolution,
    argv: list[str],
    settings: dict[str, Any],
    authorization: dict[str, Any],
    *,
    success: str,
    failure: str,
    extra: dict[str, Any] | None = None,
) -> SkillResult:
    with tempfile.TemporaryDirectory(prefix="elyndra-dart-") as temp_dir:
        environment = _dart_environment(Path(temp_dir))
        result = run_controlled_process(
            argv,
            cwd=root,
            timeout_seconds=settings["timeout_seconds"],
            max_output_chars=settings["max_output_chars"],
            environment=environment,
        )
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
            "automatic_pub_get": False,
            "automatic_installation": False,
            "proxy_environment_restricted": True,
            "temporary_directory": True,
            **(extra or {}),
        },
    )


def _dart_environment(temp_root: Path | None = None) -> dict[str, str]:
    environment = {
        "CI": "true",
        "DART_SUPPRESS_ANALYTICS": "true",
        "FLUTTER_SUPPRESS_ANALYTICS": "true",
        "PUB_ENVIRONMENT": "elyndra",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    if temp_root is not None:
        cache = temp_root / "cache"
        temp = temp_root / "tmp"
        cache.mkdir(parents=True, exist_ok=True)
        temp.mkdir(parents=True, exist_ok=True)
        environment.update(
            {
                "XDG_CACHE_HOME": str(cache),
                "TMPDIR": str(temp),
            }
        )
    return environment


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
            "shell": False,
            "stage_status": "passed" if ok else "failed",
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
    return SkillResult(
        False,
        f"El proyecto supera el límite de {settings['max_dart_files']} archivos Dart.",
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "stage_status": "failed",
            "file_limit_exceeded": True,
            "shell": False,
            **authorization,
        },
    )


def _approval_details(
    skill_name: str,
    root: Path,
    scope: str,
    source: str,
    timeout: int,
    argv: list[str],
    risk: str,
) -> dict[str, Any]:
    return {
        "skill_name": skill_name,
        "tool": argv[0],
        "project_root": str(root),
        "resolved_path": str(root),
        "authorization_scope": scope,
        "authorization_source": source,
        "risk_detail": risk,
        "timeout_seconds": timeout,
        "action_argv": argv,
        "approval_summary": (
            f"Skill: {skill_name}\nProyecto: {root}\n"
            f"Autorización: {scope}\nRiesgo: {risk}\n"
            f"Timeout: {timeout}s\nAcción: {' '.join(argv)}"
        ),
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
    value = profile.get(name, default)
    return value is True


def _choice_setting(value: Any, current: Any, allowed: set[str], field: str) -> str:
    selected = str(current if value is None else value).strip().casefold()
    if selected not in allowed:
        raise ValueError(f"{field} debe ser uno de: {', '.join(sorted(allowed))}.")
    return selected


def _bounded_files(value: Any, default: int) -> int:
    selected = default if value is None else int(value)
    if not 1 <= selected <= 20_000:
        raise ValueError("max_files debe estar entre 1 y 20000.")
    return selected


def _enabled_stage_names(settings: dict[str, Any]) -> list[str]:
    names = ["inspect"]
    for stage, key in (
        ("descriptor", "descriptor_enabled"),
        ("format", "format_enabled"),
        ("analyze", "analyze_enabled"),
        ("tests", "tests_enabled"),
    ):
        if settings[key]:
            names.append(stage)
    return names


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


def _bounded_text(value: str, limit: int) -> str:
    clean = value.strip()
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _is_excluded(path: Path, excluded: set[Path]) -> bool:
    return any(path == item or item in path.parents for item in excluded)
