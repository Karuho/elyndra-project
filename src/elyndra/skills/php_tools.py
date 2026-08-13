from __future__ import annotations

import json
import os
import re
import shutil
import stat
from pathlib import Path
from typing import Any

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult
from elyndra.skills.process import ProcessResult, run_controlled_process
from elyndra.skills.tool_resolution import resolve_project_tool

_PHP_EXTENSIONS = {".php", ".phtml", ".inc"}
_PHPSTAN_CONFIG_NAMES = ("phpstan.neon", "phpstan.neon.dist", "phpstan.dist.neon")
_PHPUNIT_CONFIG_NAMES = ("phpunit.xml", "phpunit.xml.dist")
_LEVEL_PATTERN = re.compile(r"^(?:[0-9]|10|max)$", re.IGNORECASE)
_PROJECT_MARKERS = (
    "composer.json",
    *_PHPSTAN_CONFIG_NAMES,
    *_PHPUNIT_CONFIG_NAMES,
)

class PhpSyntaxValidateSkill:
    name = "php.syntax_validate"
    description = "Ejecuta php -l sobre un archivo PHP legible, sin usar shell."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        path = _resolve_param_path(params, "path")
        php = shutil.which("php")
        tool = str(Path(php).resolve()) if php else "php (no disponible)"
        return _approval_details(
            skill_name=self.name,
            tool=tool,
            resolved_path=path,
            project_root=None,
            authorization_scope="single_file",
            authorization_source="explicit_approval",
            timeout_seconds=context.config.php_tool_timeout_seconds,
            action=[php or "php", "-l", str(path)],
            risk_detail=(
                "Se concede acceso únicamente a este archivo durante esta ejecución. "
                "No se autoriza su carpeta completa."
            ),
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        path = _resolve_param_path(params, "path")
        validation_error = _validate_php_file(path)
        authorization = _single_file_authorization(context, path, params)
        if validation_error:
            return _failure(validation_error, path=path, authorization=authorization)
        php = shutil.which("php")
        if php is None:
            return _unavailable("php", path, authorization=authorization)

        result = _run(context, [php, "-l", str(path)], cwd=path.parent)
        if result.timed_out:
            return _timeout_result(self.name, path, result, authorization=authorization)
        ok = result.returncode == 0
        heading = "Sintaxis PHP válida." if ok else "PHP encontró errores de sintaxis."
        return _tool_result(
            heading,
            self.name,
            path,
            result,
            ok=ok,
            authorization=authorization,
            tool_path=Path(php).resolve(),
        )


class ComposerValidateSkill:
    name = "composer.validate"
    description = (
        "Valida composer.json y composer.lock sin scripts, plugins ni acceso de red solicitado."
    )
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        requested = _resolve_param_path(params, "path")
        root = _discover_project_root(requested)
        effective, profile, timeout_seconds, _ = _effective_profile(
            context, root, self.name, params
        )
        authorization = context.authorization.project(root)
        scope = authorization.scope.value
        composer_file = _find_composer_file(requested, root) or (root / "composer.json")
        command_prefix = _composer_command(root)
        command = _composer_argv(command_prefix or ["composer"], composer_file, effective)
        tool = (
            " ".join(command_prefix)
            if command_prefix is not None
            else "composer (no disponible)"
        )
        return _approval_details(
            skill_name=self.name,
            tool=tool,
            resolved_path=requested,
            project_root=root,
            authorization_scope=scope,
            authorization_source=authorization.source,
            timeout_seconds=timeout_seconds,
            action=command,
            risk_detail=(
                "Composer no instalará dependencias ni ejecutará scripts o plugins. "
                + _project_scope_explanation(scope)
            ),
            profile=profile,
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        requested = _resolve_existing_path(params, "path")
        root = _discover_project_root(requested)
        effective, profile, timeout_seconds, max_output_chars = _effective_profile(
            context, root, self.name, params
        )
        authorization = _authorize_project(
            context, root, params, timeout_seconds=timeout_seconds, profile=profile
        )
        composer_file = _find_composer_file(requested, root)
        if composer_file is None:
            return _failure(
                "No se encontró composer.json en la ruta o sus padres.",
                path=requested,
                authorization=authorization,
            )
        command_prefix = _composer_command(root)
        if command_prefix is None:
            return _unavailable(
                "composer o composer.phar",
                composer_file,
                authorization=authorization,
            )

        command = _composer_argv(command_prefix, composer_file, effective)
        result = _run(
            context,
            command,
            cwd=root,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )
        if result.timed_out:
            return _timeout_result(
                self.name,
                composer_file,
                result,
                authorization=authorization,
            )
        ok = result.returncode == 0
        heading = (
            "Composer validó el proyecto correctamente."
            if ok
            else "Composer encontró errores o advertencias bloqueantes."
        )
        return _tool_result(
            heading,
            self.name,
            composer_file,
            result,
            ok=ok,
            authorization=authorization,
            tool_path=Path(command_prefix[0]).resolve(),
        )


class PhpStanAnalyseSkill:
    name = "phpstan.analyse"
    description = "Ejecuta análisis estático PHPStan sobre un proyecto autorizado."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        target = _resolve_param_path(params, "path")
        root = _discover_project_root(target)
        effective, profile, timeout_seconds, _ = _effective_profile(
            context, root, self.name, params
        )
        authorization = context.authorization.project(root)
        scope = authorization.scope.value
        tool_resolution = resolve_project_tool(root, "phpstan")
        binary = tool_resolution.path
        command = _phpstan_argv(str(binary or "phpstan"), target, root, effective)
        tool = str(binary) if binary else "phpstan (no disponible)"
        return _approval_details(
            skill_name=self.name,
            tool=tool,
            resolved_path=target,
            project_root=root,
            authorization_scope=scope,
            authorization_source=authorization.source,
            timeout_seconds=timeout_seconds,
            action=command,
            risk_detail=(
                "PHPStan puede cargar autoload, configuración y extensiones del proyecto. "
                + _project_scope_explanation(scope)
            ),
            profile=profile,
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params, "path")
        root = _discover_project_root(target)
        effective, profile, timeout_seconds, max_output_chars = _effective_profile(
            context, root, self.name, params
        )
        authorization = _authorize_project(
            context, root, params, timeout_seconds=timeout_seconds, profile=profile
        )
        tool_resolution = resolve_project_tool(root, "phpstan")
        binary = tool_resolution.path
        if binary is None:
            return _unavailable(
                "vendor/bin/phpstan o phpstan",
                target,
                authorization=authorization,
            )

        try:
            command = _phpstan_argv(str(binary), target, root, effective)
        except ValueError as exc:
            return _failure(str(exc), path=target, authorization=authorization)

        result = _run(
            context,
            command,
            cwd=root,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )
        if result.timed_out:
            return _timeout_result(
                self.name,
                target,
                result,
                authorization=authorization,
            )
        ok = result.returncode == 0
        issue_count = _phpstan_issue_count(result.stdout)
        if ok:
            heading = "PHPStan no encontró errores."
        elif issue_count is not None:
            heading = f"PHPStan encontró {issue_count} problema(s)."
        else:
            heading = "PHPStan finalizó con errores."
        extra = {"issue_count": issue_count} if issue_count is not None else {}
        return _tool_result(
            heading,
            self.name,
            target,
            result,
            ok=ok,
            authorization=authorization,
            tool_path=Path(binary).resolve(),
            extra=extra,
        )


class PhpUnitRunSkill:
    name = "phpunit.run"
    description = "Ejecuta PHPUnit en un proyecto autorizado con salida y tiempo limitados."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        target = _resolve_param_path(params, "path")
        root = _discover_project_root(target)
        effective, profile, timeout_seconds, _ = _effective_profile(
            context, root, self.name, params
        )
        authorization = context.authorization.project(root)
        scope = authorization.scope.value
        tool_resolution = resolve_project_tool(root, "phpunit")
        binary = tool_resolution.path
        command = _phpunit_argv(str(binary or "phpunit"), target, root, effective)
        tool = str(binary) if binary else "phpunit (no disponible)"
        return _approval_details(
            skill_name=self.name,
            tool=tool,
            resolved_path=target,
            project_root=root,
            authorization_scope=scope,
            authorization_source=authorization.source,
            timeout_seconds=timeout_seconds,
            action=command,
            risk_detail=(
                "PHPUnit ejecutará código del proyecto y sus pruebas. "
                + _project_scope_explanation(scope)
            ),
            profile=profile,
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params, "path")
        root = _discover_project_root(target)
        effective, profile, timeout_seconds, max_output_chars = _effective_profile(
            context, root, self.name, params
        )
        authorization = _authorize_project(
            context, root, params, timeout_seconds=timeout_seconds, profile=profile
        )
        tool_resolution = resolve_project_tool(root, "phpunit")
        binary = tool_resolution.path
        if binary is None:
            return _unavailable(
                "vendor/bin/phpunit o phpunit",
                target,
                authorization=authorization,
            )

        command = _phpunit_argv(str(binary), target, root, effective)

        result = _run(
            context,
            command,
            cwd=root,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )
        if result.timed_out:
            return _timeout_result(
                self.name,
                target,
                result,
                authorization=authorization,
            )
        ok = result.returncode == 0
        heading = "Todas las pruebas PHPUnit pasaron." if ok else "PHPUnit reportó fallos."
        return _tool_result(
            heading,
            self.name,
            target,
            result,
            ok=ok,
            authorization=authorization,
            tool_path=Path(binary).resolve(),
        )


def php_tool_capabilities() -> dict[str, bool]:
    return {
        "php": bool(shutil.which("php")),
        "composer_global": bool(shutil.which("composer")),
        "phpstan_global": bool(shutil.which("phpstan")),
        "phpunit_global": bool(shutil.which("phpunit")),
        "project_local_detection": True,
    }


def _run(
    context: SkillContext,
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int | None = None,
    max_output_chars: int | None = None,
) -> ProcessResult:
    return run_controlled_process(
        command,
        cwd=cwd,
        timeout_seconds=(
            timeout_seconds or context.config.php_tool_timeout_seconds
        ),
        max_output_chars=(
            max_output_chars or context.config.php_tool_max_output_chars
        ),
    )


def _composer_argv(
    command_prefix: list[str],
    composer_file: Path,
    params: dict[str, Any],
) -> list[str]:
    command = [
        *command_prefix,
        "--no-plugins",
        "--no-scripts",
        "--no-interaction",
        "validate",
        "--no-check-publish",
    ]
    if params.get("strict") is True:
        command.append("--strict")
    command.append(str(composer_file.resolve(strict=False)))
    return command


def _phpstan_argv(
    binary: str,
    target: Path,
    root: Path,
    params: dict[str, Any],
) -> list[str]:
    command = [
        binary,
        "analyse",
        "--no-progress",
        "--no-ansi",
        "--error-format=prettyJson",
    ]
    config = _optional_config(params, root, _PHPSTAN_CONFIG_NAMES)
    if config is not None:
        command.extend(("--configuration", str(config)))
    level = str(params.get("level", "")).strip()
    if level:
        if _LEVEL_PATTERN.fullmatch(level) is None:
            raise ValueError("El nivel PHPStan debe ser 0–10 o max.")
        command.extend(("--level", level.casefold()))
    command.append(str(target))
    return command


def _phpunit_argv(
    binary: str,
    target: Path,
    root: Path,
    params: dict[str, Any],
) -> list[str]:
    command = [binary, "--colors=never", "--do-not-cache-result"]
    config = _optional_config(params, root, _PHPUNIT_CONFIG_NAMES)
    if config is not None:
        command.extend(("--configuration", str(config)))
    testsuite = _safe_option_value(params.get("testsuite"), "testsuite")
    if testsuite:
        command.extend(("--testsuite", testsuite))
    filter_value = _safe_option_value(params.get("filter"), "filter")
    if filter_value:
        command.extend(("--filter", filter_value))
    if target != root or config is None:
        command.append(str(target))
    return command


def _resolve_param_path(params: dict[str, Any], key: str) -> Path:
    raw = str(params.get(key, "")).strip()
    if not raw:
        raise ValueError(f"Falta el parámetro {key}.")
    return Path(raw).expanduser().resolve(strict=False)


def _resolve_existing_path(params: dict[str, Any], key: str) -> Path:
    path = _resolve_param_path(params, key)
    if not path.exists():
        raise ValueError(f"La ruta no existe: {path}")
    mode = path.stat().st_mode
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise ValueError(f"La ruta no es un archivo o directorio regular: {path}")
    return path


def _validate_php_file(path: Path) -> str | None:
    if not path.exists():
        return "El archivo PHP no existe."
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        return f"No se pudo inspeccionar el archivo PHP: {exc}"
    if not stat.S_ISREG(mode):
        return (
            "La ruta debe ser un archivo regular; no se admiten directorios "
            "ni recursos especiales."
        )
    if path.suffix.casefold() not in _PHP_EXTENSIONS:
        return "La skill solo admite archivos .php, .phtml o .inc."
    if not _has_read_access(path):
        return "El usuario efectivo no tiene permiso de lectura sobre el archivo PHP."
    return None


def _has_read_access(path: Path) -> bool:
    try:
        return os.access(path, os.R_OK, effective_ids=True)
    except (NotImplementedError, TypeError):
        return os.access(path, os.R_OK)


def _single_file_authorization(
    context: SkillContext,
    path: Path,
    params: dict[str, Any],
) -> dict[str, Any]:
    decision = context.authorization.single_file(
        path,
        source=str(params.get("authorization_source") or "explicit_approval"),
    )
    return {
        **decision.as_data(),
        "timeout_seconds": context.config.php_tool_timeout_seconds,
    }


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
        "timeout_seconds": (
            timeout_seconds or context.config.php_tool_timeout_seconds
        ),
        "project_profile_id": profile.get("id") if profile else None,
        "project_profile_applied": profile is not None,
    }


def _project_scope_explanation(scope: str) -> str:
    if scope == "project_persistent":
        return "El proyecto tiene autorización persistente configurada o explícita."
    return (
        "El proyecto está fuera de las raíces persistentes; la aprobación concede "
        "acceso únicamente a este proyecto y únicamente durante esta ejecución."
    )


def _approval_details(
    *,
    skill_name: str,
    tool: str,
    resolved_path: Path,
    project_root: Path | None,
    authorization_scope: str,
    authorization_source: str,
    timeout_seconds: int,
    action: list[str],
    risk_detail: str,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    action_text = " ".join(action)
    lines = [
        f"Skill: {skill_name}",
        f"Herramienta: {tool}",
        f"Ruta resuelta: {resolved_path}",
    ]
    if project_root is not None:
        lines.append(f"Proyecto: {project_root}")
    lines.extend(
        (
            f"Alcance de autorización: {authorization_scope}",
            f"Origen de autorización: {authorization_source}",
            f"Riesgo: medio. {risk_detail}",
            f"Timeout: {timeout_seconds} segundos",
            f"Acción exacta: {action_text}",
        )
    )
    return {
        "approval_summary": "\n".join(lines),
        "authorization_scope": authorization_scope,
        "authorization_source": authorization_source,
        "resolved_path": str(resolved_path),
        "project_root": str(project_root) if project_root is not None else None,
        "tool": tool,
        "timeout_seconds": timeout_seconds,
        "action_argv": action,
        "project_profile_id": profile.get("id") if profile else None,
        "project_profile_applied": profile is not None,
    }


def _effective_profile(
    context: SkillContext,
    root: Path,
    skill_name: str,
    params: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None, int, int]:
    settings = context.php_profiles.effective_settings(
        root,
        default_timeout_seconds=context.config.php_tool_timeout_seconds,
        default_max_output_chars=context.config.php_tool_max_output_chars,
    )
    profile = settings["profile"]
    effective = dict(params)
    if profile is not None:
        if skill_name == "composer.validate" and "strict" not in effective:
            effective["strict"] = bool(profile.get("composer_strict"))
        if skill_name == "phpstan.analyse":
            if not str(effective.get("config", "")).strip() and profile.get(
                "phpstan_config"
            ):
                effective["config"] = str(root / str(profile["phpstan_config"]))
            if not str(effective.get("level", "")).strip() and profile.get(
                "phpstan_level"
            ):
                effective["level"] = str(profile["phpstan_level"])
        if skill_name == "phpunit.run":
            if not str(effective.get("config", "")).strip() and profile.get(
                "phpunit_config"
            ):
                effective["config"] = str(root / str(profile["phpunit_config"]))
            if not str(effective.get("testsuite", "")).strip() and profile.get(
                "phpunit_testsuite"
            ):
                effective["testsuite"] = str(profile["phpunit_testsuite"])
    return (
        effective,
        profile,
        int(settings["timeout_seconds"]),
        int(settings["max_output_chars"]),
    )


def _optional_config(
    params: dict[str, Any],
    root: Path,
    default_names: tuple[str, ...],
) -> Path | None:
    raw = str(params.get("config", "")).strip()
    if raw:
        config = Path(raw).expanduser().resolve(strict=False)
        _ensure_inside_project(config, root)
        if not config.is_file():
            raise ValueError(f"La configuración no es un archivo: {config}")
        return config
    for name in default_names:
        candidate = root / name
        if candidate.is_file():
            return candidate.resolve()
    return None


def _find_composer_file(path: Path, root: Path) -> Path | None:
    if path.is_file() and path.name.casefold() == "composer.json":
        return path
    candidate = root / "composer.json"
    return candidate.resolve() if candidate.is_file() else None


def _discover_project_root(path: Path) -> Path:
    start = path if path.is_dir() else path.parent
    start = start.resolve(strict=False)
    current = start
    while True:
        if any((current / marker).is_file() for marker in _PROJECT_MARKERS):
            return current
        if current.parent == current:
            return start
        current = current.parent


def _ensure_inside_project(path: Path, root: Path) -> Path:
    resolved = path.resolve(strict=False)
    project = root.resolve(strict=False)
    if resolved != project and project not in resolved.parents:
        raise PermissionError(
            f"La ruta {resolved} está fuera del proyecto autorizado {project}."
        )
    return resolved


def _composer_command(root: Path) -> list[str] | None:
    local_phar = _ensure_inside_project(root / "composer.phar", root)
    php = shutil.which("php")
    if local_phar.is_file() and php:
        return [php, str(local_phar)]
    composer = shutil.which("composer")
    return [composer] if composer else None


def _safe_option_value(value: Any, name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    if len(clean) > 300 or any(ord(char) < 32 for char in clean):
        raise ValueError(f"El valor de {name} no es válido.")
    return clean


def _phpstan_issue_count(stdout: str) -> int | None:
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    totals = payload.get("totals") if isinstance(payload, dict) else None
    if not isinstance(totals, dict):
        return None
    values = [totals.get("file_errors", 0), totals.get("errors", 0)]
    try:
        return sum(int(value or 0) for value in values)
    except (TypeError, ValueError):
        return None


def _tool_result(
    heading: str,
    skill_name: str,
    path: Path,
    result: ProcessResult,
    *,
    ok: bool,
    authorization: dict[str, Any],
    tool_path: Path,
    extra: dict[str, Any] | None = None,
) -> SkillResult:
    output = result.output
    message = (
        f"{heading}\n\n"
        f"- Skill: `{skill_name}`\n"
        f"- Ruta: `{path}`\n"
        f"- Autorización: `{authorization['authorization_scope']}`\n"
        f"- Exit code: `{result.returncode}`\n"
        f"- Duración: `{result.duration_ms} ms`"
    )
    if output:
        message += f"\n\n```text\n{output}\n```"
    data: dict[str, Any] = {
        "engine": "local-skill",
        "generated": False,
        "skill": skill_name,
        "path": str(path),
        "command": list(result.command),
        "cwd": result.cwd,
        "tool_path": str(tool_path),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "duration_ms": result.duration_ms,
        "timed_out": result.timed_out,
        "timeout_seconds": authorization.get("timeout_seconds"),
        "shell": False,
        "network_isolation": "environment-only; no kernel namespace",
        **authorization,
    }
    if extra:
        data.update(extra)
    return SkillResult(ok, message, data)


def _timeout_result(
    skill_name: str,
    path: Path,
    result: ProcessResult,
    *,
    authorization: dict[str, Any],
) -> SkillResult:
    tool_path = Path(result.command[0]).resolve(strict=False)
    response = _tool_result(
        "La ejecución superó el tiempo permitido y el proceso fue terminado.",
        skill_name,
        path,
        result,
        ok=False,
        authorization=authorization,
        tool_path=tool_path,
    )
    return response


def _failure(
    message: str,
    *,
    path: Path,
    authorization: dict[str, Any] | None = None,
) -> SkillResult:
    data: dict[str, Any] = {
        "path": str(path),
        "resolved_path": str(path),
        "engine": "local-skill",
    }
    if authorization:
        data.update(authorization)
    return SkillResult(False, message, data)


def _unavailable(
    tool: str,
    path: Path,
    *,
    authorization: dict[str, Any],
) -> SkillResult:
    return SkillResult(
        False,
        f"No se encontró la herramienta requerida: {tool}.",
        {
            "path": str(path),
            "resolved_path": str(path),
            "tool": tool,
            "engine": "local-skill",
            **authorization,
        },
    )
