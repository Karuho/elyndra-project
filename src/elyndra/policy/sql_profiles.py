from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.db import Database

_MIN_TIMEOUT_SECONDS = 5
_MAX_TIMEOUT_SECONDS = 900
_MIN_OUTPUT_CHARS = 1_000
_MAX_OUTPUT_CHARS = 50_000
_MIN_FILES = 1
_MAX_FILES = 20_000
_MIN_DATABASE_FILES = 1
_MAX_DATABASE_FILES = 200
_ALLOWED_DIALECTS = {
    "auto",
    "generic",
    "sqlite",
    "mysql",
    "mariadb",
    "postgresql",
}
_DEFAULT_EXCLUDES = (
    ".git",
    ".idea",
    ".vscode",
    "backups",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "tmp",
    "vendor",
)


class SqlProjectProfileRepository:
    """Persist safe defaults for controlled SQL and database verification."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save(
        self,
        project_root: Path,
        *,
        actor: str,
        static_enabled: bool | None = None,
        migrations_enabled: bool | None = None,
        schema_enabled: bool | None = None,
        dialect: str | None = None,
        allow_mutating_sql: bool | None = None,
        allow_destructive_migrations: bool | None = None,
        fail_fast: bool | None = None,
        max_sql_files: int | None = None,
        max_database_files: int | None = None,
        exclude_paths: list[str] | tuple[str, ...] | str | None = None,
        timeout_seconds: int | None = None,
        max_output_chars: int | None = None,
    ) -> dict[str, Any]:
        root = _validate_project_root(project_root)
        current = self.get(root) or {}
        now = datetime.now(UTC).isoformat()
        values = {
            "static_enabled": _bool_value(
                static_enabled,
                current.get("static_enabled", True),
            ),
            "migrations_enabled": _bool_value(
                migrations_enabled,
                current.get("migrations_enabled", True),
            ),
            "schema_enabled": _bool_value(
                schema_enabled,
                current.get("schema_enabled", True),
            ),
            "dialect": _choice(
                dialect,
                current.get("dialect", "auto"),
                _ALLOWED_DIALECTS,
                "dialect",
            ),
            "allow_mutating_sql": _bool_value(
                allow_mutating_sql,
                current.get("allow_mutating_sql", False),
            ),
            "allow_destructive_migrations": _bool_value(
                allow_destructive_migrations,
                current.get("allow_destructive_migrations", False),
            ),
            "fail_fast": _bool_value(
                fail_fast,
                current.get("fail_fast", False),
            ),
            "max_sql_files": _bounded_int(
                max_sql_files,
                current.get("max_sql_files", 3000),
                minimum=_MIN_FILES,
                maximum=_MAX_FILES,
                field="max_sql_files",
            ),
            "max_database_files": _bounded_int(
                max_database_files,
                current.get("max_database_files", 20),
                minimum=_MIN_DATABASE_FILES,
                maximum=_MAX_DATABASE_FILES,
                field="max_database_files",
            ),
            "exclude_paths_json": json.dumps(
                _exclude_paths(
                    root,
                    exclude_paths,
                    current.get("exclude_paths", _DEFAULT_EXCLUDES),
                ),
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
                INSERT INTO sql_project_profiles(
                    project_root,
                    static_enabled,
                    migrations_enabled,
                    schema_enabled,
                    dialect,
                    allow_mutating_sql,
                    allow_destructive_migrations,
                    fail_fast,
                    max_sql_files,
                    max_database_files,
                    exclude_paths_json,
                    timeout_seconds,
                    max_output_chars,
                    actor,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_root) DO UPDATE SET
                    static_enabled=excluded.static_enabled,
                    migrations_enabled=excluded.migrations_enabled,
                    schema_enabled=excluded.schema_enabled,
                    dialect=excluded.dialect,
                    allow_mutating_sql=excluded.allow_mutating_sql,
                    allow_destructive_migrations=excluded.allow_destructive_migrations,
                    fail_fast=excluded.fail_fast,
                    max_sql_files=excluded.max_sql_files,
                    max_database_files=excluded.max_database_files,
                    exclude_paths_json=excluded.exclude_paths_json,
                    timeout_seconds=excluded.timeout_seconds,
                    max_output_chars=excluded.max_output_chars,
                    actor=excluded.actor,
                    updated_at=excluded.updated_at
                """,
                (
                    str(root),
                    int(values["static_enabled"]),
                    int(values["migrations_enabled"]),
                    int(values["schema_enabled"]),
                    values["dialect"],
                    int(values["allow_mutating_sql"]),
                    int(values["allow_destructive_migrations"]),
                    int(values["fail_fast"]),
                    values["max_sql_files"],
                    values["max_database_files"],
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
            raise RuntimeError("No se pudo recuperar el perfil SQL guardado.")
        return profile

    def get(self, project_root: Path) -> dict[str, Any] | None:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sql_project_profiles WHERE project_root = ?",
                (str(root),),
            ).fetchone()
        return _public_profile(dict(row)) if row is not None else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sql_project_profiles "
                "ORDER BY project_root COLLATE NOCASE"
            ).fetchall()
        return [_public_profile(dict(row)) for row in rows]

    def delete(self, project_root: Path) -> bool:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM sql_project_profiles WHERE project_root = ?",
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
            "max_sql_files": int(
                profile.get("max_sql_files", 3000) if profile else 3000
            ),
            "max_database_files": int(
                profile.get("max_database_files", 20) if profile else 20
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
        raise ValueError(f"El perfil SQL requiere una carpeta existente: {root}")
    return root


def _choice(value: str | None, current: Any, allowed: set[str], field: str) -> str:
    selected = str(value if value is not None else current).strip().casefold()
    if selected not in allowed:
        options = ", ".join(sorted(allowed))
        raise ValueError(f"{field} debe ser uno de: {options}.")
    return selected


def _exclude_paths(
    root: Path,
    value: list[str] | tuple[str, ...] | str | None,
    current: Any,
) -> list[str]:
    if value is None:
        values = list(current) if isinstance(current, list) else list(_DEFAULT_EXCLUDES)
    elif isinstance(value, str):
        values = [item.strip() for item in value.split(",") if item.strip()]
    else:
        values = [str(item).strip() for item in value if str(item).strip()]
    result: list[str] = []
    for item in values:
        relative = Path(item)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Las exclusiones SQL deben ser rutas relativas seguras.")
        resolved = (root / relative).resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise ValueError("Una exclusión SQL sale del proyecto.")
        normalized = relative.as_posix().strip("/")
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _bounded_int(
    value: int | None,
    current: Any,
    *,
    minimum: int,
    maximum: int,
    field: str,
) -> int | None:
    selected = current if value is None else value
    if selected in (None, ""):
        return None
    resolved = int(selected)
    if not minimum <= resolved <= maximum:
        raise ValueError(f"{field} debe estar entre {minimum} y {maximum}.")
    return resolved


def _bool_value(value: bool | None, current: Any) -> bool:
    return bool(current) if value is None else value is True


def _public_profile(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["exclude_paths"] = json.loads(str(item.pop("exclude_paths_json", "[]")))
    for key in (
        "static_enabled",
        "migrations_enabled",
        "schema_enabled",
        "allow_mutating_sql",
        "allow_destructive_migrations",
        "fail_fast",
    ):
        item[key] = bool(item[key])
    return item
