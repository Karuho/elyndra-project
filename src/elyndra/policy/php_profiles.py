from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.db import Database

_LEVELS = {str(value) for value in range(11)} | {"max"}
_MIN_TIMEOUT_SECONDS = 5
_MAX_TIMEOUT_SECONDS = 600
_MIN_OUTPUT_CHARS = 1_000
_MAX_OUTPUT_CHARS = 50_000
_MIN_PHP_FILES = 1
_MAX_PHP_FILES = 20_000
_DEFAULT_EXCLUDES = (".git", "vendor", "node_modules")


class PhpProjectProfileRepository:
    """Persist safe, non-executable defaults for one PHP project."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save(
        self,
        project_root: Path,
        *,
        actor: str,
        phpstan_config: str | Path | None = None,
        phpstan_level: str | None = None,
        phpunit_config: str | Path | None = None,
        phpunit_testsuite: str | None = None,
        composer_strict: bool | None = None,
        composer_enabled: bool | None = None,
        syntax_scan_enabled: bool | None = None,
        phpstan_enabled: bool | None = None,
        phpunit_enabled: bool | None = None,
        fail_fast: bool | None = None,
        require_tools: bool | None = None,
        max_php_files: int | None = None,
        exclude_paths: list[str] | tuple[str, ...] | str | None = None,
        timeout_seconds: int | None = None,
        max_output_chars: int | None = None,
    ) -> dict[str, Any]:
        root = _validate_project_root(project_root)
        current = self.get(root) or {}
        now = datetime.now(UTC).isoformat()
        values = {
            "phpstan_config": _profile_path(
                root,
                phpstan_config,
                current.get("phpstan_config", ""),
                field="phpstan_config",
            ),
            "phpstan_level": _phpstan_level(
                phpstan_level,
                current.get("phpstan_level", ""),
            ),
            "phpunit_config": _profile_path(
                root,
                phpunit_config,
                current.get("phpunit_config", ""),
                field="phpunit_config",
            ),
            "phpunit_testsuite": _safe_text(
                phpunit_testsuite,
                current.get("phpunit_testsuite", ""),
                field="phpunit_testsuite",
            ),
            "composer_strict": _bool_value(
                composer_strict, current.get("composer_strict", False)
            ),
            "composer_enabled": _bool_value(
                composer_enabled, current.get("composer_enabled", True)
            ),
            "syntax_scan_enabled": _bool_value(
                syntax_scan_enabled, current.get("syntax_scan_enabled", True)
            ),
            "phpstan_enabled": _bool_value(
                phpstan_enabled, current.get("phpstan_enabled", True)
            ),
            "phpunit_enabled": _bool_value(
                phpunit_enabled, current.get("phpunit_enabled", True)
            ),
            "fail_fast": _bool_value(fail_fast, current.get("fail_fast", False)),
            "require_tools": _bool_value(
                require_tools, current.get("require_tools", False)
            ),
            "max_php_files": _bounded_int(
                max_php_files,
                current.get("max_php_files", 2000),
                minimum=_MIN_PHP_FILES,
                maximum=_MAX_PHP_FILES,
                field="max_php_files",
            ),
            "exclude_paths_json": json.dumps(
                _exclude_paths(root, exclude_paths, current.get("exclude_paths", [])),
                ensure_ascii=False,
            ),
            "timeout_seconds": _bounded_int(
                timeout_seconds,
                current.get("timeout_seconds"),
                minimum=_MIN_TIMEOUT_SECONDS,
                maximum=_MAX_TIMEOUT_SECONDS,
                field="timeout_seconds",
            ),
            "max_output_chars": _bounded_int(
                max_output_chars,
                current.get("max_output_chars"),
                minimum=_MIN_OUTPUT_CHARS,
                maximum=_MAX_OUTPUT_CHARS,
                field="max_output_chars",
            ),
        }
        created_at = str(current.get("created_at") or now)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO php_project_profiles(
                    project_root,
                    phpstan_config,
                    phpstan_level,
                    phpunit_config,
                    phpunit_testsuite,
                    composer_strict,
                    composer_enabled,
                    syntax_scan_enabled,
                    phpstan_enabled,
                    phpunit_enabled,
                    fail_fast,
                    require_tools,
                    max_php_files,
                    exclude_paths_json,
                    timeout_seconds,
                    max_output_chars,
                    actor,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_root) DO UPDATE SET
                    phpstan_config=excluded.phpstan_config,
                    phpstan_level=excluded.phpstan_level,
                    phpunit_config=excluded.phpunit_config,
                    phpunit_testsuite=excluded.phpunit_testsuite,
                    composer_strict=excluded.composer_strict,
                    composer_enabled=excluded.composer_enabled,
                    syntax_scan_enabled=excluded.syntax_scan_enabled,
                    phpstan_enabled=excluded.phpstan_enabled,
                    phpunit_enabled=excluded.phpunit_enabled,
                    fail_fast=excluded.fail_fast,
                    require_tools=excluded.require_tools,
                    max_php_files=excluded.max_php_files,
                    exclude_paths_json=excluded.exclude_paths_json,
                    timeout_seconds=excluded.timeout_seconds,
                    max_output_chars=excluded.max_output_chars,
                    actor=excluded.actor,
                    updated_at=excluded.updated_at
                """,
                (
                    str(root),
                    values["phpstan_config"],
                    values["phpstan_level"],
                    values["phpunit_config"],
                    values["phpunit_testsuite"],
                    int(values["composer_strict"]),
                    int(values["composer_enabled"]),
                    int(values["syntax_scan_enabled"]),
                    int(values["phpstan_enabled"]),
                    int(values["phpunit_enabled"]),
                    int(values["fail_fast"]),
                    int(values["require_tools"]),
                    values["max_php_files"],
                    values["exclude_paths_json"],
                    values["timeout_seconds"],
                    values["max_output_chars"],
                    actor,
                    created_at,
                    now,
                ),
            )
        profile = self.get(root)
        if profile is None:
            raise RuntimeError("No se pudo recuperar el perfil PHP guardado.")
        return profile

    def get(self, project_root: Path) -> dict[str, Any] | None:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM php_project_profiles WHERE project_root = ?",
                (str(root),),
            ).fetchone()
        return _public_profile(dict(row)) if row is not None else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM php_project_profiles ORDER BY project_root COLLATE NOCASE"
            ).fetchall()
        return [_public_profile(dict(row)) for row in rows]

    def delete(self, project_root: Path) -> bool:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM php_project_profiles WHERE project_root = ?",
                (str(root),),
            )
            return cursor.rowcount > 0

    def effective_settings(
        self,
        project_root: Path,
        *,
        default_timeout_seconds: int,
        default_max_output_chars: int,
    ) -> dict[str, Any]:
        profile = self.get(project_root)
        return {
            "profile": profile,
            "timeout_seconds": int(
                profile.get("timeout_seconds")
                if profile and profile.get("timeout_seconds") is not None
                else default_timeout_seconds
            ),
            "max_output_chars": int(
                profile.get("max_output_chars")
                if profile and profile.get("max_output_chars") is not None
                else default_max_output_chars
            ),
            "max_php_files": int(
                profile.get("max_php_files", 2000) if profile else 2000
            ),
            "exclude_paths": list(
                profile.get("exclude_paths", _DEFAULT_EXCLUDES)
                if profile
                else _DEFAULT_EXCLUDES
            ),
        }


def _validate_project_root(project_root: Path) -> Path:
    root = project_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"El perfil PHP requiere una carpeta existente: {root}")
    return root


def _profile_path(
    root: Path,
    value: str | Path | None,
    current: Any,
    *,
    field: str,
) -> str:
    if value is None:
        return str(current or "")
    clean = str(value).strip()
    if not clean:
        return ""
    candidate = Path(clean).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"{field} debe permanecer dentro del proyecto {root}.")
    if not resolved.is_file():
        raise ValueError(f"{field} no es un archivo existente: {resolved}")
    return resolved.relative_to(root).as_posix()


def _phpstan_level(value: str | None, current: Any) -> str:
    if value is None:
        return str(current or "")
    clean = value.strip().casefold()
    if not clean:
        return ""
    if clean not in _LEVELS:
        raise ValueError("phpstan_level debe ser 0–10, max o vacío.")
    return clean


def _safe_text(value: str | None, current: Any, *, field: str) -> str:
    if value is None:
        return str(current or "")
    clean = value.strip()
    if len(clean) > 300 or any(ord(char) < 32 for char in clean):
        raise ValueError(f"{field} contiene caracteres no permitidos.")
    return clean


def _bool_value(value: bool | None, current: Any) -> bool:
    return bool(current) if value is None else bool(value)


def _bounded_int(
    value: int | None,
    current: Any,
    *,
    minimum: int,
    maximum: int,
    field: str,
) -> int | None:
    if value is None:
        return int(current) if current is not None else None
    resolved = int(value)
    if not minimum <= resolved <= maximum:
        raise ValueError(f"{field} debe estar entre {minimum} y {maximum}.")
    return resolved


def _exclude_paths(
    root: Path,
    value: list[str] | tuple[str, ...] | str | None,
    current: Any,
) -> list[str]:
    if value is None:
        items = list(current or _DEFAULT_EXCLUDES)
    elif isinstance(value, str):
        items = [part.strip() for part in value.split(",") if part.strip()]
    else:
        items = [str(part).strip() for part in value if str(part).strip()]
    normalized: list[str] = []
    for item in items:
        candidate = (root / item).resolve(strict=False)
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"La exclusión debe permanecer dentro del proyecto: {item}")
        relative = candidate.relative_to(root).as_posix()
        if relative in {"", "."}:
            raise ValueError("No se puede excluir la raíz completa del proyecto.")
        if relative not in normalized:
            normalized.append(relative)
    if len(normalized) > 50:
        raise ValueError("El perfil PHP admite como máximo 50 rutas excluidas.")
    return normalized


def _public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    for field in (
        "composer_strict",
        "composer_enabled",
        "syntax_scan_enabled",
        "phpstan_enabled",
        "phpunit_enabled",
        "fail_fast",
        "require_tools",
    ):
        profile[field] = bool(profile.get(field, 0))
    try:
        excludes = json.loads(str(profile.pop("exclude_paths_json", "[]")))
    except json.JSONDecodeError:
        excludes = []
    profile["exclude_paths"] = excludes if isinstance(excludes, list) else []
    return profile
