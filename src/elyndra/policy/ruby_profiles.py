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
_ALLOWED_TEST_FRAMEWORKS = {"auto", "rspec", "minitest"}
_DEFAULT_EXCLUDES = (
    ".bundle",
    ".git",
    ".idea",
    ".vscode",
    "coverage",
    "log",
    "node_modules",
    "tmp",
    "vendor",
)


class RubyProjectProfileRepository:
    """Persist safe defaults for controlled Ruby verification."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save(
        self,
        project_root: Path,
        *,
        actor: str,
        descriptor_enabled: bool | None = None,
        bundle_enabled: bool | None = None,
        syntax_enabled: bool | None = None,
        rubocop_enabled: bool | None = None,
        tests_enabled: bool | None = None,
        test_framework: str | None = None,
        fail_fast: bool | None = None,
        require_tools: bool | None = None,
        max_ruby_files: int | None = None,
        exclude_paths: list[str] | tuple[str, ...] | str | None = None,
        timeout_seconds: int | None = None,
        max_output_chars: int | None = None,
    ) -> dict[str, Any]:
        root = _validate_project_root(project_root)
        current = self.get(root) or {}
        now = datetime.now(UTC).isoformat()
        values = {
            "descriptor_enabled": _bool_value(
                descriptor_enabled,
                current.get("descriptor_enabled", True),
            ),
            "bundle_enabled": _bool_value(
                bundle_enabled,
                current.get("bundle_enabled", True),
            ),
            "syntax_enabled": _bool_value(
                syntax_enabled,
                current.get("syntax_enabled", True),
            ),
            "rubocop_enabled": _bool_value(
                rubocop_enabled,
                current.get("rubocop_enabled", True),
            ),
            "tests_enabled": _bool_value(
                tests_enabled,
                current.get("tests_enabled", True),
            ),
            "test_framework": _choice(
                test_framework,
                current.get("test_framework", "auto"),
                _ALLOWED_TEST_FRAMEWORKS,
                "test_framework",
            ),
            "fail_fast": _bool_value(fail_fast, current.get("fail_fast", False)),
            "require_tools": _bool_value(
                require_tools,
                current.get("require_tools", False),
            ),
            "max_ruby_files": _bounded_int(
                max_ruby_files,
                current.get("max_ruby_files", 3000),
                minimum=_MIN_FILES,
                maximum=_MAX_FILES,
                field="max_ruby_files",
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
                INSERT INTO ruby_project_profiles(
                    project_root,
                    descriptor_enabled,
                    bundle_enabled,
                    syntax_enabled,
                    rubocop_enabled,
                    tests_enabled,
                    test_framework,
                    fail_fast,
                    require_tools,
                    max_ruby_files,
                    exclude_paths_json,
                    timeout_seconds,
                    max_output_chars,
                    actor,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_root) DO UPDATE SET
                    descriptor_enabled=excluded.descriptor_enabled,
                    bundle_enabled=excluded.bundle_enabled,
                    syntax_enabled=excluded.syntax_enabled,
                    rubocop_enabled=excluded.rubocop_enabled,
                    tests_enabled=excluded.tests_enabled,
                    test_framework=excluded.test_framework,
                    fail_fast=excluded.fail_fast,
                    require_tools=excluded.require_tools,
                    max_ruby_files=excluded.max_ruby_files,
                    exclude_paths_json=excluded.exclude_paths_json,
                    timeout_seconds=excluded.timeout_seconds,
                    max_output_chars=excluded.max_output_chars,
                    actor=excluded.actor,
                    updated_at=excluded.updated_at
                """,
                (
                    str(root),
                    int(values["descriptor_enabled"]),
                    int(values["bundle_enabled"]),
                    int(values["syntax_enabled"]),
                    int(values["rubocop_enabled"]),
                    int(values["tests_enabled"]),
                    values["test_framework"],
                    int(values["fail_fast"]),
                    int(values["require_tools"]),
                    values["max_ruby_files"],
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
            raise RuntimeError("No se pudo recuperar el perfil Ruby guardado.")
        return profile

    def get(self, project_root: Path) -> dict[str, Any] | None:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM ruby_project_profiles WHERE project_root = ?",
                (str(root),),
            ).fetchone()
        return _public_profile(dict(row)) if row is not None else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ruby_project_profiles "
                "ORDER BY project_root COLLATE NOCASE"
            ).fetchall()
        return [_public_profile(dict(row)) for row in rows]

    def delete(self, project_root: Path) -> bool:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM ruby_project_profiles WHERE project_root = ?",
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
            "max_ruby_files": int(
                profile.get("max_ruby_files", 3000) if profile else 3000
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
        raise ValueError(f"El perfil Ruby requiere una carpeta existente: {root}")
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
            raise ValueError("Las exclusiones Ruby deben ser rutas relativas seguras.")
        resolved = (root / relative).resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise ValueError("Una exclusión Ruby sale del proyecto.")
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
        "descriptor_enabled",
        "bundle_enabled",
        "syntax_enabled",
        "rubocop_enabled",
        "tests_enabled",
        "fail_fast",
        "require_tools",
    ):
        item[key] = bool(item[key])
    return item
