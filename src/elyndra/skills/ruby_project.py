from __future__ import annotations

import os
import re
import stat
import time
from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.process import ProcessResult, run_controlled_process
from elyndra.skills.tool_resolution import ToolResolution, resolve_project_tool

_RUBY_EXTENSIONS = {".rb", ".rake"}
_PROJECT_MARKERS = (
    "Gemfile",
    "Gemfile.lock",
    "Rakefile",
    ".rubocop.yml",
    ".rspec",
)
_DEFAULT_EXCLUDES = {
    ".bundle",
    ".git",
    ".idea",
    ".vscode",
    "coverage",
    "log",
    "node_modules",
    "tmp",
    "vendor",
}
_GEM_RE = re.compile(r"^\s*gem\s+[\"']([^\"']+)[\"']", re.MULTILINE)
_REMOTE_SOURCE_RE = re.compile(r"^\s*source\s+[\"']https?://", re.MULTILINE)
_DYNAMIC_GEM_RE = re.compile(r"\bgem\s+[\"'][^\"']+[\"']\s*,.*\b(?:git|github|path):")


class RubyProjectInspectSkill:
    name = "ruby.project_inspect"
    description = "Inspecciona un proyecto Ruby sin ejecutar Bundler ni código."
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
            context.config.ruby_tool_timeout_seconds,
            ["inspect-ruby-project", str(root)],
            "Solo se leen nombres, manifiestos y metadatos acotados.",
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


class RubyDescriptorValidateSkill:
    name = "ruby.descriptor_validate"
    description = "Valida Gemfile y gemspecs sin evaluarlos como Ruby."
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
            context.config.ruby_tool_timeout_seconds,
            ["validate-ruby-descriptors", str(root)],
            "No se evalúan Gemfile, gemspecs, Rakefile ni código del proyecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        report = _validate_descriptors(root)
        ok = not report["errors"]
        lines = [
            "Descriptores Ruby válidos." if ok else "Descriptores Ruby con errores.",
            "",
            f"- Proyecto: `{root}`",
            f"- Gemfile: `{'sí' if report['gemfile'] else 'no'}`",
            f"- Gemspecs: `{report['gemspec_count']}`",
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


class RubyBundleCheckSkill:
    name = "ruby.bundle_check"
    description = "Ejecuta bundle check sin instalar ni actualizar gemas."
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
            ["bundle", "check"],
            "Bundler evalúa el Gemfile, pero no instala ni actualiza dependencias.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        if not (root / "Gemfile").is_file():
            return _skipped_result(
                self.name,
                root,
                "No existe Gemfile; bundle check fue omitido.",
                authorization,
            )
        tool = _resolve_ruby_tool(root, "bundle")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "bundle", authorization)
        argv = [str(tool.path), "check"]
        result = run_controlled_process(
            argv,
            cwd=root,
            timeout_seconds=settings["timeout_seconds"],
            max_output_chars=settings["max_output_chars"],
            environment={
                "BUNDLE_FROZEN": "true",
                "BUNDLE_DISABLE_LOCAL_BRANCH_CHECK": "true",
            },
        )
        return _process_result(
            self.name,
            root,
            tool,
            argv,
            result,
            authorization,
            success="Dependencias Ruby disponibles según bundle check.",
            failure="bundle check encontró dependencias ausentes o problemas.",
        )


class RubySyntaxCheckSkill:
    name = "ruby.syntax_check"
    description = "Ejecuta ruby -c sobre archivos Ruby sin cargar el proyecto."
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
            ["ruby", "-c", "<archivo.rb>"],
            "ruby -c comprueba sintaxis sin ejecutar el cuerpo del archivo.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        files, truncated = _collect_ruby_files(
            target,
            root=root,
            exclude_paths=settings["exclude_paths"],
            max_files=settings["max_ruby_files"],
        )
        if truncated:
            return _file_limit_result(self.name, root, settings, authorization)
        if not files:
            return _skipped_result(
                self.name,
                root,
                "No se encontraron archivos Ruby para comprobar.",
                authorization,
            )
        tool = resolve_project_tool(root, "ruby")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "ruby", authorization)
        started = time.perf_counter()
        results: list[tuple[Path, ProcessResult]] = []
        for path in files:
            elapsed = time.perf_counter() - started
            remaining = max(1, settings["timeout_seconds"] - int(elapsed))
            argv = [str(tool.path), "-c", str(path)]
            result = run_controlled_process(
                argv,
                cwd=root,
                timeout_seconds=remaining,
                max_output_chars=min(settings["max_output_chars"], 3000),
            )
            results.append((path, result))
            if (
                result.returncode != 0 or result.timed_out
            ) and (settings["fail_fast"] or result.timed_out):
                break
        failures = [item for item in results if item[1].returncode != 0 or item[1].timed_out]
        duration_ms = round((time.perf_counter() - started) * 1000)
        lines = [
            "Sintaxis Ruby correcta." if not failures else "Ruby encontró errores de sintaxis.",
            "",
            f"- Proyecto: `{root}`",
            f"- Archivos examinados: `{len(results)}`",
            f"- Fallos: `{len(failures)}`",
            f"- Timeout: `{'sí' if any(item[1].timed_out for item in results) else 'no'}`",
            f"- Duración: `{duration_ms} ms`",
        ]
        for path, result in failures[:20]:
            detail = result.output.strip() or f"exit code {result.returncode}"
            lines.extend(("", f"`{path.relative_to(root)}`", detail))
        return SkillResult(
            not failures,
            "\n".join(lines),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "tool_path": str(tool.path),
                "tool_source": tool.source,
                "files_examined": len(results),
                "failure_count": len(failures),
                "returncode": failures[0][1].returncode if failures else 0,
                "duration_ms": duration_ms,
                "timed_out": any(item[1].timed_out for item in results),
                "shell": False,
                **authorization,
            },
        )


class RubocopCheckSkill:
    name = "rubocop.check"
    description = "Ejecuta RuboCop local o global sin aplicar correcciones."
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
            ["rubocop", "--format", "simple", "--force-exclusion", "--cache", "false"],
            "RuboCop analiza el proyecto; no aplica autocorrecciones.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        tool = _resolve_ruby_tool(root, "rubocop")
        if tool.path is None:
            return _tool_unavailable(self.name, root, "rubocop", authorization)
        argv = [
            str(tool.path),
            "--format",
            "simple",
            "--force-exclusion",
            "--cache",
            "false",
        ]
        result = run_controlled_process(
            argv,
            cwd=root,
            timeout_seconds=settings["timeout_seconds"],
            max_output_chars=settings["max_output_chars"],
        )
        return _process_result(
            self.name,
            root,
            tool,
            argv,
            result,
            authorization,
            success="RuboCop finalizó correctamente.",
            failure="RuboCop encontró problemas.",
        )


class RubyTestSkill:
    name = "ruby.test_project"
    description = "Ejecuta RSpec o Minitest con argumentos fijos."
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
            [settings["test_framework"], "<tests>"],
            "Los tests ejecutan código del proyecto y requieren aprobación explícita.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        framework = _test_framework(root, settings["test_framework"])
        if framework is None:
            return _skipped_result(
                self.name,
                root,
                "No se detectaron tests RSpec ni Minitest.",
                authorization,
            )
        if framework == "rspec":
            tool = _resolve_ruby_tool(root, "rspec")
            if tool.path is not None:
                argv = [str(tool.path), "--format", "progress"]
            else:
                bundle = _resolve_ruby_tool(root, "bundle")
                if bundle.path is None:
                    return _tool_unavailable(self.name, root, "rspec", authorization)
                tool = bundle
                argv = [str(bundle.path), "exec", "rspec", "--format", "progress"]
        else:
            tool = resolve_project_tool(root, "ruby")
            if tool.path is None:
                return _tool_unavailable(self.name, root, "ruby", authorization)
            runner = (
                'Dir["test/**/*_test.rb"].sort.each '
                "{ |file| require File.expand_path(file) }"
            )
            argv = [str(tool.path), "-Itest", "-e", runner]
        result = run_controlled_process(
            argv,
            cwd=root,
            timeout_seconds=settings["timeout_seconds"],
            max_output_chars=settings["max_output_chars"],
            environment={"BUNDLE_FROZEN": "true"},
        )
        return _process_result(
            self.name,
            root,
            tool,
            argv,
            result,
            authorization,
            success=f"Tests {framework} finalizaron correctamente.",
            failure=f"Tests {framework} encontraron problemas.",
            extra={"test_framework": framework},
        )


class RubyVerifyProjectSkill:
    name = "ruby.verify_project"
    description = "Ejecuta la verificación Ruby completa y guarda historial comparable."
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
            ["ruby-verify", *_enabled_stage_names(settings), str(root)],
            "Bundler, RuboCop y tests pueden evaluar código del proyecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        started = time.perf_counter()
        profile = settings.get("profile") or {}
        run_id = context.verification_runs.start(
            toolchain="ruby",
            project_root=root,
            actor=context.actor,
            profile_id=profile.get("id"),
            plan={
                "stages": _enabled_stage_names(settings),
                "test_framework": settings["test_framework"],
                "require_tools": settings["require_tools"],
                "fail_fast": settings["fail_fast"],
            },
        )
        stages: list[dict[str, Any]] = []
        stage_specs = (
            ("inspect", True, RubyProjectInspectSkill()),
            ("descriptor", settings["descriptor_enabled"], RubyDescriptorValidateSkill()),
            ("bundle", settings["bundle_enabled"], RubyBundleCheckSkill()),
            ("syntax", settings["syntax_enabled"], RubySyntaxCheckSkill()),
            ("rubocop", settings["rubocop_enabled"], RubocopCheckSkill()),
            ("tests", settings["tests_enabled"], RubyTestSkill()),
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
            "passed": "Verificación Ruby correcta.",
            "partial": "Verificación Ruby parcial.",
            "failed": "Verificación Ruby fallida.",
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


def _pipeline_settings(
    context: SkillContext,
    root: Path,
    params: dict[str, Any],
) -> dict[str, Any]:
    effective = context.ruby_profiles.effective_settings(
        root,
        default_timeout_seconds=context.config.ruby_tool_timeout_seconds,
        default_max_output_chars=context.config.ruby_tool_max_output_chars,
    )
    profile = effective["profile"] or {}
    return {
        "profile": effective["profile"],
        "timeout_seconds": int(effective["timeout_seconds"]),
        "max_output_chars": int(effective["max_output_chars"]),
        "max_ruby_files": _bounded_files(
            params.get("max_files"),
            int(effective["max_ruby_files"]),
        ),
        "exclude_paths": list(effective["exclude_paths"]),
        "descriptor_enabled": _setting(params, profile, "descriptor_enabled", True),
        "bundle_enabled": _setting(params, profile, "bundle_enabled", True),
        "syntax_enabled": _setting(params, profile, "syntax_enabled", True),
        "rubocop_enabled": _setting(params, profile, "rubocop_enabled", True),
        "tests_enabled": _setting(params, profile, "tests_enabled", True),
        "test_framework": _choice_setting(
            params.get("test_framework"),
            profile.get("test_framework", "auto"),
            {"auto", "rspec", "minitest"},
            "test_framework",
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
        if list(current.glob("*.gemspec")):
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


def _collect_ruby_files(
    target: Path,
    *,
    root: Path,
    exclude_paths: list[str],
    max_files: int,
) -> tuple[list[Path], bool]:
    if target.is_file():
        return ([target] if target.suffix.casefold() in _RUBY_EXTENSIONS else []), False
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
            if candidate.suffix.casefold() not in _RUBY_EXTENSIONS:
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
    files, truncated = _collect_ruby_files(
        root,
        root=root,
        exclude_paths=settings["exclude_paths"],
        max_files=settings["max_ruby_files"],
    )
    gemfile = _read_text(root / "Gemfile", limit=300_000)
    gems = sorted(set(_GEM_RE.findall(gemfile)), key=str.casefold)
    test_files = [
        path
        for path in files
        if "spec" in path.parts or "test" in path.parts or path.name.endswith("_test.rb")
    ]
    frameworks: list[str] = []
    if "rails" in gems or (root / "config" / "application.rb").is_file():
        frameworks.append("Rails")
    if "sinatra" in gems:
        frameworks.append("Sinatra")
    if "hanami" in gems:
        frameworks.append("Hanami")
    if "rspec" in gems or "rspec-core" in gems or (root / "spec").is_dir():
        frameworks.append("RSpec")
    if (root / "test").is_dir():
        frameworks.append("Minitest")
    return {
        "project_root": str(root),
        "ruby_files": len(files),
        "test_files": len(test_files),
        "truncated": truncated,
        "gemfile": bool(gemfile),
        "lockfile": (root / "Gemfile.lock").is_file(),
        "gemspecs": sorted(path.name for path in root.glob("*.gemspec")),
        "gems": gems[:100],
        "frameworks": frameworks,
        "test_framework": _test_framework(root, settings["test_framework"]),
        "tools": {
            name: _resolve_ruby_tool(root, name).path is not None
            for name in ("ruby", "bundle", "rubocop", "rspec")
        },
    }


def _validate_descriptors(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    gemfile_path = root / "Gemfile"
    gemfile = _read_text(gemfile_path, limit=300_000)
    if gemfile_path.is_file() and not gemfile:
        errors.append("Gemfile está vacío o no puede leerse como UTF-8.")
    if gemfile:
        if gemfile.count("(") != gemfile.count(")"):
            warnings.append("Gemfile contiene paréntesis desbalanceados.")
        if _REMOTE_SOURCE_RE.search(gemfile):
            warnings.append("Gemfile declara una fuente remota; bundle check no instala gemas.")
        if _DYNAMIC_GEM_RE.search(gemfile):
            warnings.append(
                "Gemfile contiene dependencias git/path que pueden ejecutar código local."
            )
    gemspecs = sorted(root.glob("*.gemspec"))
    for path in gemspecs:
        if not _read_text(path, limit=300_000):
            errors.append(f"{path.name} está vacío o no puede leerse como UTF-8.")
    if (root / "Gemfile.lock").is_file() and not gemfile_path.is_file():
        warnings.append("Gemfile.lock existe, pero falta Gemfile.")
    return {
        "gemfile": gemfile_path.is_file(),
        "lockfile": (root / "Gemfile.lock").is_file(),
        "gemspec_count": len(gemspecs),
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


def _test_framework(root: Path, configured: str) -> str | None:
    if configured != "auto":
        return configured
    gemfile = _read_text(root / "Gemfile", limit=300_000)
    gems = set(_GEM_RE.findall(gemfile))
    if (root / "spec").is_dir() or {"rspec", "rspec-core", "rspec-rails"} & gems:
        return "rspec"
    if (root / "test").is_dir():
        return "minitest"
    return None


def _format_inventory(inventory: dict[str, Any], authorization: dict[str, Any]) -> str:
    frameworks = ", ".join(inventory["frameworks"]) or "no detectado"
    test_framework = inventory["test_framework"] or "no detectado"
    return "\n".join(
        (
            "Inspección Ruby completada sin ejecutar código.",
            "",
            f"- Proyecto: `{inventory['project_root']}`",
            f"- Archivos Ruby: `{inventory['ruby_files']}`",
            f"- Tests detectados: `{inventory['test_files']}`",
            f"- Gemfile: `{'sí' if inventory['gemfile'] else 'no'}`",
            f"- Gemfile.lock: `{'sí' if inventory['lockfile'] else 'no'}`",
            f"- Gemspecs: `{len(inventory['gemspecs'])}`",
            f"- Frameworks: `{frameworks}`",
            f"- Tests: `{test_framework}`",
            f"- Autorización: `{authorization['authorization_scope']}`",
        )
    )


def _resolve_ruby_tool(root: Path, name: str) -> ToolResolution:
    candidate = (root / "bin" / name).resolve(strict=False)
    if (
        name in {"bundle", "rubocop", "rspec"}
        and candidate.is_file()
        and os.access(candidate, os.X_OK)
    ):
        return ToolResolution(candidate, "project_local")
    return resolve_project_tool(root, name)


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
        f"El proyecto supera el límite de {settings['max_ruby_files']} archivos Ruby.",
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
        ("bundle", "bundle_enabled"),
        ("syntax", "syntax_enabled"),
        ("rubocop", "rubocop_enabled"),
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
