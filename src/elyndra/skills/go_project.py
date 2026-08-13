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

_GO_EXTENSION = ".go"
_PROJECT_MARKERS = ("go.mod", "go.work", "go.sum")
_DEFAULT_EXCLUDES = {
    ".git",
    ".idea",
    ".vscode",
    "bin",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "testdata",
    "vendor",
}
_MODULE_RE = re.compile(r"(?m)^\s*module\s+([^\s]+)\s*$")
_GO_VERSION_RE = re.compile(r"(?m)^\s*go\s+([0-9]+(?:\.[0-9]+){1,2})\s*$")
_TOOLCHAIN_RE = re.compile(r"(?m)^\s*toolchain\s+([^\s]+)\s*$")
_REQUIRE_LINE_RE = re.compile(r"(?m)^\s*require\s+([^\s()]+)\s+([^\s()]+)\s*$")
_REPLACE_LOCAL_RE = re.compile(r"(?m)^\s*replace\s+.+?=>\s+([^\s]+)\s*$")


class GoProjectInspectSkill:
    name = "go.project_inspect"
    description = "Inspecciona un proyecto Go sin ejecutar herramientas ni código."
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
            context.config.go_tool_timeout_seconds,
            ["inspect-go-project", str(root)],
            "Solo se leen nombres, go.mod, go.work y metadatos acotados.",
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
                **authorization,
            },
        )


class GoModuleValidateSkill:
    name = "go.module_validate"
    description = "Valida go.mod y go.work sin ejecutar el comando go."
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
            context.config.go_tool_timeout_seconds,
            ["validate-go-module", str(root)],
            "No se ejecutan go mod, go get, go work ni código del proyecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        report = _validate_descriptors(root)
        ok = not report["errors"]
        lines = [
            "Módulo Go válido." if ok else "Módulo Go con errores.",
            "",
            f"- Proyecto: `{root}`",
            f"- go.mod: `{'sí' if report['go_mod'] else 'no'}`",
            f"- go.work: `{'sí' if report['go_work'] else 'no'}`",
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


class GofmtCheckSkill:
    name = "gofmt.check"
    description = "Comprueba formato Go con gofmt -d sin modificar archivos."
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
            ["gofmt", "-d", "<archivo.go>"],
            "gofmt genera un diff en memoria; no usa -w ni modifica archivos.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        files, truncated = _collect_go_files(
            target,
            root=root,
            exclude_paths=settings["exclude_paths"],
            max_files=settings["max_go_files"],
        )
        if truncated:
            return _file_limit_result(self.name, root, settings, authorization)
        if not files:
            return _skipped_result(
                self.name,
                root,
                "No se encontraron archivos Go para comprobar.",
                authorization,
            )
        tool = resolve_project_tool(root, "gofmt")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "gofmt", authorization)
        started = time.perf_counter()
        results: list[tuple[Path, ProcessResult, bool]] = []
        for path in files:
            elapsed = time.perf_counter() - started
            remaining = max(1, settings["timeout_seconds"] - int(elapsed))
            argv = [str(tool.path), "-d", str(path)]
            result = run_controlled_process(
                argv,
                cwd=root,
                timeout_seconds=remaining,
                max_output_chars=min(settings["max_output_chars"], 4000),
            )
            changed = bool(result.stdout.strip())
            results.append((path, result, changed))
            failed = result.returncode != 0 or result.timed_out or changed
            if failed and (settings["fail_fast"] or result.timed_out):
                break
        failures = [
            item
            for item in results
            if item[1].returncode != 0 or item[1].timed_out or item[2]
        ]
        duration_ms = round((time.perf_counter() - started) * 1000)
        lines = [
            "Formato Go correcto." if not failures else "gofmt encontró diferencias.",
            "",
            f"- Proyecto: `{root}`",
            f"- Archivos examinados: `{len(results)}`",
            f"- Diferencias o fallos: `{len(failures)}`",
            f"- Timeout: `{'sí' if any(item[1].timed_out for item in results) else 'no'}`",
            f"- Duración: `{duration_ms} ms`",
        ]
        for path, result, changed in failures[:20]:
            detail = result.output.strip()
            if changed and not detail:
                detail = "El archivo no coincide con el formato de gofmt."
            if not detail:
                detail = f"exit code {result.returncode}"
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
                "shell": False,
                "stage_status": "passed" if ok else "failed",
                **authorization,
            },
        )


class GoVetSkill:
    name = "go.vet"
    description = "Ejecuta go vet ./... sin red ni modificaciones automáticas."
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
            ["go", "vet", "./..."],
            "go vet carga paquetes del proyecto; la red queda desactivada.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root, settings, authorization = _execution_context(context, params)
        if not _has_go_files(root, settings):
            return _skipped_result(
                self.name,
                root,
                "No se encontraron archivos Go; go vet fue omitido.",
                authorization,
            )
        tool = resolve_project_tool(root, "go")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "go", authorization)
        return _run_go_command(
            self.name,
            root,
            tool,
            [str(tool.path), "vet", "./..."],
            settings,
            authorization,
            success="go vet finalizó correctamente.",
            failure="go vet encontró problemas.",
        )


class GoBuildSkill:
    name = "go.build_project"
    description = "Compila paquetes Go sin escribir binarios en el proyecto."
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
            ["go", "build", "./..."],
            "La compilación usa caché temporal, modo readonly y red desactivada.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root, settings, authorization = _execution_context(context, params)
        if not _has_go_files(root, settings):
            return _skipped_result(
                self.name,
                root,
                "No se encontraron archivos Go; go build fue omitido.",
                authorization,
            )
        tool = resolve_project_tool(root, "go")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "go", authorization)
        return _run_go_command(
            self.name,
            root,
            tool,
            [str(tool.path), "build", "./..."],
            settings,
            authorization,
            success="Build Go finalizó correctamente.",
            failure="Build Go encontró problemas.",
        )


class GoTestSkill:
    name = "go.test_project"
    description = "Ejecuta go test con red desactivada y argumentos fijos."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        argv = ["go", "test"]
        if settings["test_mode"] == "short":
            argv.append("-short")
        argv.extend(("-count=1", "./..."))
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            argv,
            "go test ejecuta código del proyecto; la red queda desactivada.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root, settings, authorization = _execution_context(context, params)
        files, _ = _collect_go_files(
            root,
            root=root,
            exclude_paths=settings["exclude_paths"],
            max_files=settings["max_go_files"],
        )
        if not any(path.name.endswith("_test.go") for path in files):
            return _skipped_result(
                self.name,
                root,
                "No se detectaron tests Go; go test fue omitido.",
                authorization,
            )
        tool = resolve_project_tool(root, "go")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "go", authorization)
        argv = [str(tool.path), "test"]
        if settings["test_mode"] == "short":
            argv.append("-short")
        argv.extend(("-count=1", "./..."))
        return _run_go_command(
            self.name,
            root,
            tool,
            argv,
            settings,
            authorization,
            success="Tests Go finalizaron correctamente.",
            failure="Tests Go encontraron problemas.",
            extra={"test_mode": settings["test_mode"]},
        )


class GoVerifyProjectSkill:
    name = "go.verify_project"
    description = "Ejecuta la verificación Go completa y guarda historial comparable."
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
            ["go-verify", *_enabled_stage_names(settings), str(root)],
            "go vet, build y test cargan paquetes; test ejecuta código del proyecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        started = time.perf_counter()
        profile = settings.get("profile") or {}
        run_id = context.verification_runs.start(
            toolchain="go",
            project_root=root,
            actor=context.actor,
            profile_id=profile.get("id"),
            plan={
                "stages": _enabled_stage_names(settings),
                "test_mode": settings["test_mode"],
                "require_tools": settings["require_tools"],
                "fail_fast": settings["fail_fast"],
                "network_allowed": False,
            },
        )
        stages: list[dict[str, Any]] = []
        stage_specs = (
            ("inspect", True, GoProjectInspectSkill()),
            ("module", settings["module_enabled"], GoModuleValidateSkill()),
            ("fmt", settings["fmt_enabled"], GofmtCheckSkill()),
            ("vet", settings["vet_enabled"], GoVetSkill()),
            ("build", settings["build_enabled"], GoBuildSkill()),
            ("tests", settings["tests_enabled"], GoTestSkill()),
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
        summary = {"stages": stages, "authorization": authorization}
        run = context.verification_runs.finish(
            run_id,
            status=status,
            duration_ms=duration_ms,
            summary=summary,
        )
        heading = {
            "passed": "Verificación Go correcta.",
            "partial": "Verificación Go parcial.",
            "failed": "Verificación Go fallida.",
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
    effective = context.go_profiles.effective_settings(
        root,
        default_timeout_seconds=context.config.go_tool_timeout_seconds,
        default_max_output_chars=context.config.go_tool_max_output_chars,
    )
    profile = effective["profile"] or {}
    return {
        "profile": effective["profile"],
        "timeout_seconds": int(effective["timeout_seconds"]),
        "max_output_chars": int(effective["max_output_chars"]),
        "max_go_files": _bounded_files(
            params.get("max_files"),
            int(effective["max_go_files"]),
        ),
        "exclude_paths": list(effective["exclude_paths"]),
        "module_enabled": _setting(params, profile, "module_enabled", True),
        "fmt_enabled": _setting(params, profile, "fmt_enabled", True),
        "vet_enabled": _setting(params, profile, "vet_enabled", True),
        "build_enabled": _setting(params, profile, "build_enabled", True),
        "tests_enabled": _setting(params, profile, "tests_enabled", True),
        "test_mode": _choice_setting(
            params.get("test_mode"),
            profile.get("test_mode", "auto"),
            {"auto", "short", "full"},
            "test_mode",
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


def _collect_go_files(
    target: Path,
    *,
    root: Path,
    exclude_paths: list[str],
    max_files: int,
) -> tuple[list[Path], bool]:
    if target.is_file():
        return ([target] if target.suffix.casefold() == _GO_EXTENSION else []), False
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
            if candidate.suffix.casefold() != _GO_EXTENSION:
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


def _has_go_files(root: Path, settings: dict[str, Any]) -> bool:
    files, _ = _collect_go_files(
        root,
        root=root,
        exclude_paths=settings["exclude_paths"],
        max_files=settings["max_go_files"],
    )
    return bool(files)


def _inspect_project(root: Path, settings: dict[str, Any]) -> dict[str, Any]:
    files, truncated = _collect_go_files(
        root,
        root=root,
        exclude_paths=settings["exclude_paths"],
        max_files=settings["max_go_files"],
    )
    go_mod = _read_text(root / "go.mod", limit=400_000)
    go_work = _read_text(root / "go.work", limit=400_000)
    module = _first_match(_MODULE_RE, go_mod)
    go_version = _first_match(_GO_VERSION_RE, go_mod or go_work)
    toolchain = _first_match(_TOOLCHAIN_RE, go_mod or go_work)
    requires = sorted(
        {match.group(1) for match in _REQUIRE_LINE_RE.finditer(go_mod)},
        key=str.casefold,
    )
    test_files = [path for path in files if path.name.endswith("_test.go")]
    commands = [path for path in files if "cmd" in path.parts and path.name == "main.go"]
    frameworks: list[str] = []
    module_text = go_mod.casefold()
    for needle, name in (
        ("github.com/gin-gonic/gin", "Gin"),
        ("github.com/labstack/echo", "Echo"),
        ("github.com/gofiber/fiber", "Fiber"),
        ("google.golang.org/grpc", "gRPC"),
        ("github.com/spf13/cobra", "Cobra"),
    ):
        if needle in module_text:
            frameworks.append(name)
    return {
        "project_root": str(root),
        "go_files": len(files),
        "test_files": len(test_files),
        "command_packages": len(commands),
        "truncated": truncated,
        "go_mod": bool(go_mod),
        "go_sum": (root / "go.sum").is_file(),
        "go_work": bool(go_work),
        "module": module,
        "go_version": go_version,
        "toolchain": toolchain,
        "requires": requires[:100],
        "frameworks": frameworks,
        "tools": {
            name: resolve_project_tool(root, name).path is not None
            for name in ("go", "gofmt")
        },
    }


def _validate_descriptors(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    go_mod_path = root / "go.mod"
    go_work_path = root / "go.work"
    go_mod = _read_text(go_mod_path, limit=400_000)
    go_work = _read_text(go_work_path, limit=400_000)
    if go_mod_path.is_file() and not go_mod:
        errors.append("go.mod está vacío o no puede leerse como UTF-8.")
    if go_work_path.is_file() and not go_work:
        errors.append("go.work está vacío o no puede leerse como UTF-8.")
    if go_mod:
        if _MODULE_RE.search(go_mod) is None:
            errors.append("go.mod no declara una directiva module válida.")
        if _GO_VERSION_RE.search(go_mod) is None:
            warnings.append("go.mod no declara una versión go explícita.")
        for match in _REPLACE_LOCAL_RE.finditer(go_mod):
            target = match.group(1).strip('"')
            if target.startswith(("./", "../")):
                resolved = (root / target).resolve(strict=False)
                if resolved != root and root not in resolved.parents:
                    warnings.append(
                        f"replace local sale del proyecto autorizado: {target}"
                    )
    if go_work and _GO_VERSION_RE.search(go_work) is None:
        warnings.append("go.work no declara una versión go explícita.")
    if (root / "go.sum").is_file() and not go_mod_path.is_file():
        warnings.append("go.sum existe, pero falta go.mod.")
    if not go_mod_path.is_file() and not go_work_path.is_file():
        warnings.append("No existe go.mod ni go.work; se tratará como proyecto Go directo.")
    return {
        "go_mod": go_mod_path.is_file(),
        "go_sum": (root / "go.sum").is_file(),
        "go_work": go_work_path.is_file(),
        "module": _first_match(_MODULE_RE, go_mod),
        "go_version": _first_match(_GO_VERSION_RE, go_mod or go_work),
        "errors": errors,
        "warnings": warnings,
    }


def _read_text(path: Path, *, limit: int) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="strict")[:limit]
    except (OSError, UnicodeError):
        return ""


def _first_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _format_inventory(inventory: dict[str, Any], authorization: dict[str, Any]) -> str:
    frameworks = ", ".join(inventory["frameworks"]) or "no detectado"
    return "\n".join(
        (
            "Inspección Go completada sin ejecutar código.",
            "",
            f"- Proyecto: `{inventory['project_root']}`",
            f"- Archivos Go: `{inventory['go_files']}`",
            f"- Tests detectados: `{inventory['test_files']}`",
            f"- Paquetes main en cmd/: `{inventory['command_packages']}`",
            f"- go.mod: `{'sí' if inventory['go_mod'] else 'no'}`",
            f"- go.work: `{'sí' if inventory['go_work'] else 'no'}`",
            f"- Módulo: `{inventory['module'] or '-'}`",
            f"- Versión Go: `{inventory['go_version'] or '-'}`",
            f"- Frameworks: `{frameworks}`",
            f"- Autorización: `{authorization['authorization_scope']}`",
        )
    )


def _run_go_command(
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
    with tempfile.TemporaryDirectory(prefix="elyndra-go-") as temp_dir:
        environment = _go_environment(root, Path(temp_dir))
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
            "network_allowed": False,
            "go_mod_readonly": True,
            "temporary_cache": True,
            **(extra or {}),
        },
    )


def _go_environment(root: Path, temp_root: Path) -> dict[str, str]:
    flags = "-mod=readonly -buildvcs=false"
    environment = {
        "GOCACHE": str(temp_root / "cache"),
        "GOTMPDIR": str(temp_root / "tmp"),
        "GOPROXY": "off",
        "GOSUMDB": "off",
        "GOTOOLCHAIN": "local",
        "GOFLAGS": flags,
    }
    (temp_root / "cache").mkdir(parents=True, exist_ok=True)
    (temp_root / "tmp").mkdir(parents=True, exist_ok=True)
    if not (root / "go.mod").is_file() and not (root / "go.work").is_file():
        environment["GO111MODULE"] = "off"
        environment["GOFLAGS"] = "-buildvcs=false"
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
        f"El proyecto supera el límite de {settings['max_go_files']} archivos Go.",
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
        ("module", "module_enabled"),
        ("fmt", "fmt_enabled"),
        ("vet", "vet_enabled"),
        ("build", "build_enabled"),
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
