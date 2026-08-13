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
_ALLOWED_BUILD_TOOLS = {"auto", "javac", "maven", "gradle"}
_DEFAULT_EXCLUDES = (
    ".git",
    ".gradle",
    ".idea",
    "build",
    "out",
    "target",
)


class JavaProjectProfileRepository:
    """Persist safe defaults for controlled Java/JVM verification."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save(
        self,
        project_root: Path,
        *,
        actor: str,
        descriptor_enabled: bool | None = None,
        javac_enabled: bool | None = None,
        build_enabled: bool | None = None,
        tests_enabled: bool | None = None,
        build_tool: str | None = None,
        java_release: int | None = None,
        fail_fast: bool | None = None,
        require_tools: bool | None = None,
        max_java_files: int | None = None,
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
            "javac_enabled": _bool_value(
                javac_enabled,
                current.get("javac_enabled", True),
            ),
            "build_enabled": _bool_value(
                build_enabled,
                current.get("build_enabled", True),
            ),
            "tests_enabled": _bool_value(
                tests_enabled,
                current.get("tests_enabled", True),
            ),
            "build_tool": _build_tool(build_tool, current.get("build_tool", "auto")),
            "java_release": _optional_release(
                java_release,
                current.get("java_release"),
            ),
            "fail_fast": _bool_value(fail_fast, current.get("fail_fast", False)),
            "require_tools": _bool_value(
                require_tools,
                current.get("require_tools", False),
            ),
            "max_java_files": _bounded_int(
                max_java_files,
                current.get("max_java_files", 3000),
                minimum=_MIN_FILES,
                maximum=_MAX_FILES,
                field="max_java_files",
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
                INSERT INTO java_project_profiles(
                    project_root,
                    descriptor_enabled,
                    javac_enabled,
                    build_enabled,
                    tests_enabled,
                    build_tool,
                    java_release,
                    fail_fast,
                    require_tools,
                    max_java_files,
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
                    javac_enabled=excluded.javac_enabled,
                    build_enabled=excluded.build_enabled,
                    tests_enabled=excluded.tests_enabled,
                    build_tool=excluded.build_tool,
                    java_release=excluded.java_release,
                    fail_fast=excluded.fail_fast,
                    require_tools=excluded.require_tools,
                    max_java_files=excluded.max_java_files,
                    exclude_paths_json=excluded.exclude_paths_json,
                    timeout_seconds=excluded.timeout_seconds,
                    max_output_chars=excluded.max_output_chars,
                    actor=excluded.actor,
                    updated_at=excluded.updated_at
                """,
                (
                    str(root),
                    int(values["descriptor_enabled"]),
                    int(values["javac_enabled"]),
                    int(values["build_enabled"]),
                    int(values["tests_enabled"]),
                    values["build_tool"],
                    values["java_release"],
                    int(values["fail_fast"]),
                    int(values["require_tools"]),
                    values["max_java_files"],
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
            raise RuntimeError("No se pudo recuperar el perfil Java guardado.")
        return profile

    def get(self, project_root: Path) -> dict[str, Any] | None:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM java_project_profiles WHERE project_root = ?",
                (str(root),),
            ).fetchone()
        return _public_profile(dict(row)) if row is not None else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM java_project_profiles ORDER BY project_root COLLATE NOCASE"
            ).fetchall()
        return [_public_profile(dict(row)) for row in rows]

    def delete(self, project_root: Path) -> bool:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM java_project_profiles WHERE project_root = ?",
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
            "max_java_files": int(
                profile.get("max_java_files", 3000) if profile else 3000
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
        raise ValueError(f"El perfil Java requiere una carpeta existente: {root}")
    return root


def _build_tool(value: str | None, current: Any) -> str:
    selected = str(value if value is not None else current or "auto").strip().casefold()
    if selected not in _ALLOWED_BUILD_TOOLS:
        raise ValueError("build_tool debe ser auto, javac, maven o gradle.")
    return selected


def _optional_release(value: int | None, current: Any) -> int | None:
    selected = current if value is None else value
    if selected in (None, ""):
        return None
    release = int(selected)
    if not 8 <= release <= 99:
        raise ValueError("java_release debe estar entre 8 y 99.")
    return release


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
    clean: list[str] = []
    for item in values:
        candidate = Path(item)
        if candidate.is_absolute():
            raise ValueError("Las exclusiones Java deben ser relativas al proyecto.")
        resolved = (root / candidate).resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise ValueError("Una exclusión Java sale fuera del proyecto.")
        normalized = candidate.as_posix().strip("/")
        if normalized and normalized not in clean:
            clean.append(normalized)
    return clean or list(_DEFAULT_EXCLUDES)


def _bounded_int(
    value: int | None,
    current: Any,
    *,
    minimum: int,
    maximum: int,
    field: str,
) -> int | None:
    selected = current if value is None else value
    if selected is None:
        return None
    number = int(selected)
    if not minimum <= number <= maximum:
        raise ValueError(f"{field} debe estar entre {minimum} y {maximum}.")
    return number


def _bool_value(value: bool | None, current: Any) -> bool:
    return bool(current) if value is None else value is True


def _public_profile(item: dict[str, Any]) -> dict[str, Any]:
    for field in (
        "descriptor_enabled",
        "javac_enabled",
        "build_enabled",
        "tests_enabled",
        "fail_fast",
        "require_tools",
    ):
        item[field] = bool(item[field])
    try:
        excluded = json.loads(str(item.pop("exclude_paths_json", "[]")))
    except json.JSONDecodeError:
        excluded = []
    item["exclude_paths"] = excluded if isinstance(excluded, list) else []
    return item
