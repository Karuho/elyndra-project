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
_ALLOWED_FEATURE_MODES = {"default", "all"}
_DEFAULT_EXCLUDES = (
    ".git",
    ".idea",
    ".vscode",
    "bin",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "testdata",
    "vendor",
)


class RustProjectProfileRepository:
    """Persist safe defaults for controlled Rust verification."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save(
        self,
        project_root: Path,
        *,
        actor: str,
        manifest_enabled: bool | None = None,
        fmt_enabled: bool | None = None,
        check_enabled: bool | None = None,
        clippy_enabled: bool | None = None,
        tests_enabled: bool | None = None,
        feature_mode: str | None = None,
        fail_fast: bool | None = None,
        require_tools: bool | None = None,
        max_rust_files: int | None = None,
        exclude_paths: list[str] | tuple[str, ...] | str | None = None,
        timeout_seconds: int | None = None,
        max_output_chars: int | None = None,
    ) -> dict[str, Any]:
        root = _validate_project_root(project_root)
        current = self.get(root) or {}
        now = datetime.now(UTC).isoformat()
        values = {
            "manifest_enabled": _bool_value(
                manifest_enabled,
                current.get("manifest_enabled", True),
            ),
            "fmt_enabled": _bool_value(fmt_enabled, current.get("fmt_enabled", True)),
            "check_enabled": _bool_value(check_enabled, current.get("check_enabled", True)),
            "clippy_enabled": _bool_value(
                clippy_enabled,
                current.get("clippy_enabled", True),
            ),
            "tests_enabled": _bool_value(
                tests_enabled,
                current.get("tests_enabled", True),
            ),
            "feature_mode": _choice(
                feature_mode,
                current.get("feature_mode", "default"),
                _ALLOWED_FEATURE_MODES,
                "feature_mode",
            ),
            "fail_fast": _bool_value(fail_fast, current.get("fail_fast", False)),
            "require_tools": _bool_value(
                require_tools,
                current.get("require_tools", False),
            ),
            "max_rust_files": _bounded_int(
                max_rust_files,
                current.get("max_rust_files", 3000),
                minimum=_MIN_FILES,
                maximum=_MAX_FILES,
                field="max_rust_files",
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
                INSERT INTO rust_project_profiles(
                    project_root,
                    manifest_enabled,
                    fmt_enabled,
                    check_enabled,
                    clippy_enabled,
                    tests_enabled,
                    feature_mode,
                    fail_fast,
                    require_tools,
                    max_rust_files,
                    exclude_paths_json,
                    timeout_seconds,
                    max_output_chars,
                    actor,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_root) DO UPDATE SET
                    manifest_enabled=excluded.manifest_enabled,
                    fmt_enabled=excluded.fmt_enabled,
                    check_enabled=excluded.check_enabled,
                    clippy_enabled=excluded.clippy_enabled,
                    tests_enabled=excluded.tests_enabled,
                    feature_mode=excluded.feature_mode,
                    fail_fast=excluded.fail_fast,
                    require_tools=excluded.require_tools,
                    max_rust_files=excluded.max_rust_files,
                    exclude_paths_json=excluded.exclude_paths_json,
                    timeout_seconds=excluded.timeout_seconds,
                    max_output_chars=excluded.max_output_chars,
                    actor=excluded.actor,
                    updated_at=excluded.updated_at
                """,
                (
                    str(root),
                    int(values["manifest_enabled"]),
                    int(values["fmt_enabled"]),
                    int(values["check_enabled"]),
                    int(values["clippy_enabled"]),
                    int(values["tests_enabled"]),
                    values["feature_mode"],
                    int(values["fail_fast"]),
                    int(values["require_tools"]),
                    values["max_rust_files"],
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
            raise RuntimeError("No se pudo recuperar el perfil Rust guardado.")
        return profile

    def get(self, project_root: Path) -> dict[str, Any] | None:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM rust_project_profiles WHERE project_root = ?",
                (str(root),),
            ).fetchone()
        return _public_profile(dict(row)) if row is not None else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM rust_project_profiles "
                "ORDER BY project_root COLLATE NOCASE"
            ).fetchall()
        return [_public_profile(dict(row)) for row in rows]

    def delete(self, project_root: Path) -> bool:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM rust_project_profiles WHERE project_root = ?",
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
            "max_rust_files": int(
                profile.get("max_rust_files", 3000) if profile else 3000
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
        raise ValueError(f"El perfil Rust requiere una carpeta existente: {root}")
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
            raise ValueError("Las exclusiones Rust deben ser rutas relativas seguras.")
        resolved = (root / relative).resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise ValueError("Una exclusión Rust sale del proyecto.")
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
        "manifest_enabled",
        "fmt_enabled",
        "check_enabled",
        "clippy_enabled",
        "tests_enabled",
        "fail_fast",
        "require_tools",
    ):
        item[key] = bool(item[key])
    return item
