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
_ALLOWED_COMPILERS = {"auto", "gcc", "clang"}
_ALLOWED_C_STANDARDS = {"c11", "c17", "c23"}
_ALLOWED_CPP_STANDARDS = {"c++17", "c++20", "c++23"}
_DEFAULT_EXCLUDES = (
    ".git",
    ".idea",
    ".vscode",
    "build",
    "cmake-build-debug",
    "cmake-build-release",
    "dist",
    "out",
    "vendor",
)


class NativeProjectProfileRepository:
    """Persist safe defaults for controlled C and C++ verification."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save(
        self,
        project_root: Path,
        *,
        actor: str,
        descriptor_enabled: bool | None = None,
        c_syntax_enabled: bool | None = None,
        cpp_syntax_enabled: bool | None = None,
        static_enabled: bool | None = None,
        build_enabled: bool | None = None,
        tests_enabled: bool | None = None,
        compiler: str | None = None,
        c_standard: str | None = None,
        cpp_standard: str | None = None,
        fail_fast: bool | None = None,
        require_tools: bool | None = None,
        max_native_files: int | None = None,
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
            "c_syntax_enabled": _bool_value(
                c_syntax_enabled,
                current.get("c_syntax_enabled", True),
            ),
            "cpp_syntax_enabled": _bool_value(
                cpp_syntax_enabled,
                current.get("cpp_syntax_enabled", True),
            ),
            "static_enabled": _bool_value(
                static_enabled,
                current.get("static_enabled", True),
            ),
            "build_enabled": _bool_value(
                build_enabled,
                current.get("build_enabled", True),
            ),
            "tests_enabled": _bool_value(
                tests_enabled,
                current.get("tests_enabled", True),
            ),
            "compiler": _choice(
                compiler,
                current.get("compiler", "auto"),
                _ALLOWED_COMPILERS,
                "compiler",
            ),
            "c_standard": _choice(
                c_standard,
                current.get("c_standard", "c17"),
                _ALLOWED_C_STANDARDS,
                "c_standard",
            ),
            "cpp_standard": _choice(
                cpp_standard,
                current.get("cpp_standard", "c++20"),
                _ALLOWED_CPP_STANDARDS,
                "cpp_standard",
            ),
            "fail_fast": _bool_value(fail_fast, current.get("fail_fast", False)),
            "require_tools": _bool_value(
                require_tools,
                current.get("require_tools", False),
            ),
            "max_native_files": _bounded_int(
                max_native_files,
                current.get("max_native_files", 3000),
                minimum=_MIN_FILES,
                maximum=_MAX_FILES,
                field="max_native_files",
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
                INSERT INTO native_project_profiles(
                    project_root,
                    descriptor_enabled,
                    c_syntax_enabled,
                    cpp_syntax_enabled,
                    static_enabled,
                    build_enabled,
                    tests_enabled,
                    compiler,
                    c_standard,
                    cpp_standard,
                    fail_fast,
                    require_tools,
                    max_native_files,
                    exclude_paths_json,
                    timeout_seconds,
                    max_output_chars,
                    actor,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_root) DO UPDATE SET
                    descriptor_enabled=excluded.descriptor_enabled,
                    c_syntax_enabled=excluded.c_syntax_enabled,
                    cpp_syntax_enabled=excluded.cpp_syntax_enabled,
                    static_enabled=excluded.static_enabled,
                    build_enabled=excluded.build_enabled,
                    tests_enabled=excluded.tests_enabled,
                    compiler=excluded.compiler,
                    c_standard=excluded.c_standard,
                    cpp_standard=excluded.cpp_standard,
                    fail_fast=excluded.fail_fast,
                    require_tools=excluded.require_tools,
                    max_native_files=excluded.max_native_files,
                    exclude_paths_json=excluded.exclude_paths_json,
                    timeout_seconds=excluded.timeout_seconds,
                    max_output_chars=excluded.max_output_chars,
                    actor=excluded.actor,
                    updated_at=excluded.updated_at
                """,
                (
                    str(root),
                    int(values["descriptor_enabled"]),
                    int(values["c_syntax_enabled"]),
                    int(values["cpp_syntax_enabled"]),
                    int(values["static_enabled"]),
                    int(values["build_enabled"]),
                    int(values["tests_enabled"]),
                    values["compiler"],
                    values["c_standard"],
                    values["cpp_standard"],
                    int(values["fail_fast"]),
                    int(values["require_tools"]),
                    values["max_native_files"],
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
            raise RuntimeError("No se pudo recuperar el perfil C/C++ guardado.")
        return profile

    def get(self, project_root: Path) -> dict[str, Any] | None:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM native_project_profiles WHERE project_root = ?",
                (str(root),),
            ).fetchone()
        return _public_profile(dict(row)) if row is not None else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM native_project_profiles "
                "ORDER BY project_root COLLATE NOCASE"
            ).fetchall()
        return [_public_profile(dict(row)) for row in rows]

    def delete(self, project_root: Path) -> bool:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM native_project_profiles WHERE project_root = ?",
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
            "max_native_files": int(
                profile.get("max_native_files", 3000) if profile else 3000
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
        raise ValueError(f"El perfil C/C++ requiere una carpeta existente: {root}")
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
            raise ValueError("Las exclusiones C/C++ deben ser rutas relativas seguras.")
        resolved = (root / relative).resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise ValueError("Una exclusión C/C++ sale del proyecto.")
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
        "c_syntax_enabled",
        "cpp_syntax_enabled",
        "static_enabled",
        "build_enabled",
        "tests_enabled",
        "fail_fast",
        "require_tools",
    ):
        item[key] = bool(item[key])
    return item
