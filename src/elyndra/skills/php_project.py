from __future__ import annotations

import json
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.php_tools import (
    ComposerValidateSkill,
    PhpStanAnalyseSkill,
    PhpUnitRunSkill,
)
from elyndra.skills.process import run_controlled_process
from elyndra.skills.tool_resolution import resolve_project_tool

_PHP_EXTENSIONS = {".php", ".phtml", ".inc"}
_PROJECT_MARKERS = (
    "composer.json",
    "phpstan.neon",
    "phpstan.neon.dist",
    "phpstan.dist.neon",
    "phpunit.xml",
    "phpunit.xml.dist",
)
_DEFAULT_EXCLUDES = {".git", "vendor", "node_modules"}
_FRAMEWORK_PACKAGES = {
    "laravel/framework": "Laravel",
    "symfony/framework-bundle": "Symfony",
    "cakephp/cakephp": "CakePHP",
    "codeigniter4/framework": "CodeIgniter",
    "yiisoft/yii2": "Yii",
    "slim/slim": "Slim",
    "wordpress/wordpress": "WordPress",
    "drupal/core": "Drupal",
}


class PhpProjectInspectSkill:
    name = "php.project_inspect"
    description = (
        "Inspecciona estructura, Composer, herramientas y configuración PHP sin ejecutar código."
    )
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        requested = _resolve_path(params)
        root = _discover_project_root(requested)
        decision = context.authorization.project(root)
        return _project_approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            context.config.php_tool_timeout_seconds,
            ["inspect-php-project", str(root)],
            "Solo se leen metadatos y nombres; no se ejecuta código del proyecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        requested = _resolve_existing_path(params)
        root = _discover_project_root(requested)
        authorization = _authorize_project(context, root, params)
        settings = _pipeline_settings(context, root, params)
        inventory = _inspect_project(root, settings)
        message = _format_inventory(inventory, authorization)
        return SkillResult(
            True,
            message,
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "inventory": inventory,
                **authorization,
            },
        )


class PhpProjectSyntaxScanSkill:
    name = "php.syntax_scan"
    description = "Ejecuta php -l sobre todos los archivos PHP permitidos de un proyecto."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        requested = _resolve_path(params)
        root = _discover_project_root(requested)
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        php = shutil.which("php")
        return _project_approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            [php or "php", "-l", f"<hasta {settings['max_php_files']} archivos>"],
            "Cada archivo se valida por argv, sin shell y sin copiar su contenido.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        requested = _resolve_existing_path(params)
        root = _discover_project_root(requested)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(
            context,
            root,
            params,
            timeout_seconds=settings["timeout_seconds"],
            profile=settings["profile"],
        )
        php = shutil.which("php")
        if php is None:
            return SkillResult(
                False,
                "No se encontró la herramienta requerida: php.",
                {
                    "engine": "local-skill",
                    "generated": False,
                    "skill": self.name,
                    "project_root": str(root),
                    "tool": "php",
                    **authorization,
                },
            )
        files, truncated = _collect_php_files(
            root,
            exclude_paths=settings["exclude_paths"],
            max_files=settings["max_php_files"],
        )
        if truncated:
            return SkillResult(
                False,
                (
                    "El proyecto supera el límite de archivos PHP configurado "
                    f"({settings['max_php_files']}). Ajusta max_php_files en su perfil."
                ),
                {
                    "engine": "local-skill",
                    "generated": False,
                    "skill": self.name,
                    "project_root": str(root),
                    "files_found": len(files),
                    "scan_truncated": True,
                    **authorization,
                },
            )

        started = time.perf_counter()
        failures: list[dict[str, Any]] = []
        timed_out = False
        output_truncated = False
        scanned = 0
        deadline = started + settings["timeout_seconds"]
        for source in files:
            remaining = int(deadline - time.perf_counter())
            if remaining <= 0:
                timed_out = True
                break
            result = run_controlled_process(
                [php, "-l", str(source)],
                cwd=root,
                timeout_seconds=max(1, remaining),
                max_output_chars=min(settings["max_output_chars"], 2_000),
            )
            scanned += 1
            output_truncated = (
                output_truncated
                or result.stdout_truncated
                or result.stderr_truncated
            )
            if result.timed_out:
                timed_out = True
                failures.append(
                    {
                        "path": str(source.relative_to(root)),
                        "returncode": result.returncode,
                        "timed_out": True,
                        "output": result.output[-1_000:],
                    }
                )
                break
            if result.returncode != 0:
                failures.append(
                    {
                        "path": str(source.relative_to(root)),
                        "returncode": result.returncode,
                        "timed_out": False,
                        "output": result.output[-1_000:],
                    }
                )
                if len(failures) >= 100:
                    output_truncated = True
                    break
        duration_ms = round((time.perf_counter() - started) * 1000)
        ok = not failures and not timed_out and scanned == len(files)
        heading = (
            f"Sintaxis PHP válida en {scanned} archivo(s)."
            if ok
            else f"La validación PHP encontró problemas en {len(failures)} archivo(s)."
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
        if failures:
            lines.extend(("", "Problemas:"))
            lines.extend(f"- `{item['path']}`" for item in failures[:20])
        return SkillResult(
            ok,
            "\n".join(lines),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "tool_path": str(Path(php).resolve()),
                "command_template": [php, "-l", "<archivo.php>"],
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


class PhpVerifyProjectSkill:
    name = "php.verify_project"
    description = (
        "Ejecuta un flujo PHP determinista: inspección, Composer, sintaxis, PHPStan y PHPUnit."
    )
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        requested = _resolve_path(params)
        root = _discover_project_root(requested)
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        stages = _planned_stages(settings)
        summary = _project_approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["php-verify-project", str(root)],
            "El flujo ejecuta solo herramientas PHP conocidas mediante argv validado.",
        )
        summary["verification_stages"] = stages
        summary["approval_summary"] += "\nEtapas: " + ", ".join(stages)
        summary["project_profile_id"] = (
            settings["profile"].get("id") if settings["profile"] else None
        )
        return summary

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        requested = _resolve_existing_path(params)
        root = _discover_project_root(requested)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(
            context,
            root,
            params,
            timeout_seconds=settings["timeout_seconds"],
            profile=settings["profile"],
        )
        plan = {
            "stages": _planned_stages(settings),
            "settings": _public_pipeline_settings(settings),
            "authorization_scope": authorization["authorization_scope"],
        }
        run_id = context.verification_runs.start(
            toolchain="php",
            project_root=root,
            actor=context.actor,
            profile_id=settings["profile"].get("id") if settings["profile"] else None,
            plan=plan,
        )
        started = time.perf_counter()
        stage_results: list[dict[str, Any]] = []
        try:
            inventory = _inspect_project(root, settings)
            stage_results.append(
                {
                    "name": "inspect",
                    "status": "passed",
                    "duration_ms": 0,
                    "php_files": inventory["php_files"],
                    "frameworks": inventory["frameworks"],
                }
            )
            tasks = _pipeline_tasks(context, root, params, settings, inventory)
            for name, enabled, runner, unavailable_reason in tasks:
                if not enabled:
                    stage_results.append(
                        {"name": name, "status": "skipped", "reason": "disabled"}
                    )
                    continue
                if unavailable_reason:
                    status = "failed" if settings["require_tools"] else "unavailable"
                    stage_results.append(
                        {"name": name, "status": status, "reason": unavailable_reason}
                    )
                    if status == "failed" and settings["fail_fast"]:
                        break
                    continue
                result = runner()
                stage = _stage_from_result(name, result)
                stage_results.append(stage)
                if stage["status"] == "failed" and settings["fail_fast"]:
                    break
            status = _verification_status(stage_results)
            duration_ms = round((time.perf_counter() - started) * 1000)
            summary = {
                "stages": stage_results,
                "counts": _stage_counts(stage_results),
                "project_inventory": inventory,
            }
            context.verification_runs.finish(
                run_id,
                status=status,
                duration_ms=duration_ms,
                summary=summary,
            )
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000)
            context.verification_runs.finish(
                run_id,
                status="failed",
                duration_ms=duration_ms,
                summary={
                    "stages": stage_results,
                    "error": str(exc),
                },
            )
            raise

        message = _format_verification(root, run_id, status, duration_ms, stage_results)
        return SkillResult(
            status != "failed",
            message,
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "verification_run_id": run_id,
                "verification_status": status,
                "project_root": str(root),
                "duration_ms": duration_ms,
                "stages": stage_results,
                "profile_applied": settings["profile"] is not None,
                "project_profile_id": (
                    settings["profile"].get("id") if settings["profile"] else None
                ),
                **authorization,
            },
        )


def _pipeline_tasks(
    context: SkillContext,
    root: Path,
    params: dict[str, Any],
    settings: dict[str, Any],
    inventory: dict[str, Any],
) -> list[tuple[str, bool, Any, str]]:
    shared = dict(params)
    shared["path"] = str(root)
    shared["allow_root_once"] = authorization_once = (
        params.get("allow_root_once") is True
    )
    if authorization_once:
        shared["authorization_source"] = str(
            params.get("authorization_source") or "pipeline_project_once"
        )

    composer_missing = "" if inventory["composer_json"] else "composer.json no existe"
    composer_tool_missing = "" if _composer_available(root) else "Composer no disponible"
    composer_reason = composer_missing or composer_tool_missing
    php_reason = "" if shutil.which("php") else "PHP no disponible"
    phpstan_reason = (
        "" if resolve_project_tool(root, "phpstan").path else "PHPStan no disponible"
    )
    phpunit_reason = (
        "" if resolve_project_tool(root, "phpunit").path else "PHPUnit no disponible"
    )
    return [
        (
            "composer",
            settings["composer_enabled"],
            lambda: ComposerValidateSkill().execute(context, shared),
            composer_reason,
        ),
        (
            "syntax",
            settings["syntax_scan_enabled"],
            lambda: PhpProjectSyntaxScanSkill().execute(context, shared),
            php_reason,
        ),
        (
            "phpstan",
            settings["phpstan_enabled"],
            lambda: PhpStanAnalyseSkill().execute(context, shared),
            phpstan_reason,
        ),
        (
            "phpunit",
            settings["phpunit_enabled"],
            lambda: PhpUnitRunSkill().execute(context, shared),
            phpunit_reason,
        ),
    ]


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
    timeout_seconds: int | None = None,
    profile: dict[str, Any] | None = None,
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
        "timeout_seconds": timeout_seconds or context.config.php_tool_timeout_seconds,
        "project_profile_id": profile.get("id") if profile else None,
        "project_profile_applied": profile is not None,
    }


def _pipeline_settings(
    context: SkillContext,
    root: Path,
    params: dict[str, Any],
) -> dict[str, Any]:
    effective = context.php_profiles.effective_settings(
        root,
        default_timeout_seconds=context.config.php_tool_timeout_seconds,
        default_max_output_chars=context.config.php_tool_max_output_chars,
    )
    profile = effective["profile"] or {}
    return {
        "profile": effective["profile"],
        "timeout_seconds": int(effective["timeout_seconds"]),
        "max_output_chars": int(effective["max_output_chars"]),
        "max_php_files": _max_php_files(
            params.get("max_files"),
            int(effective["max_php_files"]),
        ),
        "exclude_paths": list(effective["exclude_paths"]),
        "composer_enabled": _setting(params, profile, "composer_enabled", True),
        "syntax_scan_enabled": _setting(params, profile, "syntax_scan_enabled", True),
        "phpstan_enabled": _setting(params, profile, "phpstan_enabled", True),
        "phpunit_enabled": _setting(params, profile, "phpunit_enabled", True),
        "fail_fast": _setting(params, profile, "fail_fast", False),
        "require_tools": _setting(params, profile, "require_tools", False),
    }


def _max_php_files(value: Any, default: int) -> int:
    resolved = default if value is None else int(value)
    if not 1 <= resolved <= 20_000:
        raise ValueError("max_files debe estar entre 1 y 20000.")
    return resolved


def _setting(
    params: dict[str, Any], profile: dict[str, Any], name: str, default: bool
) -> bool:
    if name in params:
        return params[name] is True
    if name in profile:
        return bool(profile[name])
    return default


def _public_pipeline_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in settings.items()
        if key != "profile"
    }


def _collect_php_files(
    root: Path,
    *,
    exclude_paths: list[str],
    max_files: int,
) -> tuple[list[Path], bool]:
    excluded = {
        (root / relative).resolve(strict=False)
        for relative in (*_DEFAULT_EXCLUDES, *exclude_paths)
    }
    files: list[Path] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
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
            if candidate.suffix.casefold() not in _PHP_EXTENSIONS:
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
    files, truncated = _collect_php_files(
        root,
        exclude_paths=settings["exclude_paths"],
        max_files=settings["max_php_files"],
    )
    composer = _read_composer(root / "composer.json")
    package_names = set(composer["require"]) | set(composer["require_dev"])
    frameworks = sorted(
        name for package, name in _FRAMEWORK_PACKAGES.items() if package in package_names
    )
    phpstan = resolve_project_tool(root, "phpstan")
    phpunit = resolve_project_tool(root, "phpunit")
    return {
        "project_root": str(root),
        "php_files": len(files),
        "file_scan_truncated": truncated,
        "composer_json": (root / "composer.json").is_file(),
        "composer_lock": (root / "composer.lock").is_file(),
        "package_name": composer["name"],
        "php_constraint": composer["php_constraint"],
        "require_count": len(composer["require"]),
        "require_dev_count": len(composer["require_dev"]),
        "autoload_namespaces": composer["autoload_namespaces"],
        "composer_scripts": composer["scripts"],
        "frameworks": frameworks,
        "configs": {
            "phpstan": _first_existing(
                root,
                ("phpstan.neon", "phpstan.neon.dist", "phpstan.dist.neon"),
            ),
            "phpunit": _first_existing(root, ("phpunit.xml", "phpunit.xml.dist")),
        },
        "tools": {
            "php": shutil.which("php") or "",
            "composer": _composer_available(root),
            "phpstan": str(phpstan.path or ""),
            "phpunit": str(phpunit.path or ""),
        },
    }


def _read_composer(path: Path) -> dict[str, Any]:
    empty = {
        "name": "",
        "php_constraint": "",
        "require": [],
        "require_dev": [],
        "autoload_namespaces": [],
        "scripts": [],
    }
    if not path.is_file() or path.stat().st_size > 2_000_000:
        return empty
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return empty
    if not isinstance(payload, dict):
        return empty
    require = payload.get("require") if isinstance(payload.get("require"), dict) else {}
    require_dev = (
        payload.get("require-dev")
        if isinstance(payload.get("require-dev"), dict)
        else {}
    )
    autoload = payload.get("autoload") if isinstance(payload.get("autoload"), dict) else {}
    psr4 = autoload.get("psr-4") if isinstance(autoload.get("psr-4"), dict) else {}
    scripts = payload.get("scripts") if isinstance(payload.get("scripts"), dict) else {}
    return {
        "name": str(payload.get("name") or ""),
        "php_constraint": str(require.get("php") or ""),
        "require": sorted(str(name) for name in require if name != "php"),
        "require_dev": sorted(str(name) for name in require_dev),
        "autoload_namespaces": sorted(str(name) for name in psr4),
        "scripts": sorted(str(name) for name in scripts),
    }


def _first_existing(root: Path, names: tuple[str, ...]) -> str:
    for name in names:
        if (root / name).is_file():
            return name
    return ""


def _composer_available(root: Path) -> str:
    local = root / "composer.phar"
    php = shutil.which("php")
    if local.is_file() and php:
        return f"{php} {local}"
    return shutil.which("composer") or ""


def _planned_stages(settings: dict[str, Any]) -> list[str]:
    stages = ["inspect"]
    for key, name in (
        ("composer_enabled", "composer"),
        ("syntax_scan_enabled", "syntax"),
        ("phpstan_enabled", "phpstan"),
        ("phpunit_enabled", "phpunit"),
    ):
        if settings[key]:
            stages.append(name)
    return stages


def _project_approval_details(
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


def _stage_from_result(name: str, result: SkillResult) -> dict[str, Any]:
    unavailable = result.message.startswith("No se encontró la herramienta requerida:")
    status = "passed" if result.ok else ("unavailable" if unavailable else "failed")
    return {
        "name": name,
        "status": status,
        "returncode": result.data.get("returncode"),
        "duration_ms": int(result.data.get("duration_ms") or 0),
        "timed_out": bool(result.data.get("timed_out", False)),
        "issue_count": result.data.get("issue_count"),
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


def _format_inventory(
    inventory: dict[str, Any], authorization: dict[str, Any]
) -> str:
    frameworks = ", ".join(inventory["frameworks"]) or "no detectado"
    return "\n".join(
        (
            "Inspección PHP completada sin ejecutar código.",
            "",
            f"- Proyecto: `{inventory['project_root']}`",
            f"- Archivos PHP: `{inventory['php_files']}`",
            f"- Composer: `{'sí' if inventory['composer_json'] else 'no'}`",
            f"- Paquete: `{inventory['package_name'] or '-'}`",
            f"- Restricción PHP: `{inventory['php_constraint'] or '-'}`",
            f"- Frameworks: `{frameworks}`",
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
    labels = {
        "passed": "correcta",
        "partial": "parcial",
        "failed": "con errores",
    }
    lines = [
        f"Verificación PHP {labels[status]}.",
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
