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
_DEFAULT_EXCLUDES = (
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "build",
    "dist",
)


class PythonProjectProfileRepository:
    """Persist safe defaults for controlled Python verification."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save(
        self,
        project_root: Path,
        *,
        actor: str,
        pyproject_enabled: bool | None = None,
        compile_enabled: bool | None = None,
        ruff_enabled: bool | None = None,
        mypy_enabled: bool | None = None,
        pytest_enabled: bool | None = None,
        ruff_config: str | None = None,
        mypy_config: str | None = None,
        pytest_path: str | None = None,
        fail_fast: bool | None = None,
        require_tools: bool | None = None,
        max_python_files: int | None = None,
        exclude_paths: list[str] | tuple[str, ...] | str | None = None,
        timeout_seconds: int | None = None,
        max_output_chars: int | None = None,
    ) -> dict[str, Any]:
        root = _validate_project_root(project_root)
        current = self.get(root) or {}
        now = datetime.now(UTC).isoformat()
        values = {
            "pyproject_enabled": _bool_value(
                pyproject_enabled,
                current.get("pyproject_enabled", True),
            ),
            "compile_enabled": _bool_value(
                compile_enabled,
                current.get("compile_enabled", True),
            ),
            "ruff_enabled": _bool_value(
                ruff_enabled,
                current.get("ruff_enabled", True),
            ),
            "mypy_enabled": _bool_value(
                mypy_enabled,
                current.get("mypy_enabled", True),
            ),
            "pytest_enabled": _bool_value(
                pytest_enabled,
                current.get("pytest_enabled", True),
            ),
            "ruff_config": _config_path(
                root,
                ruff_config,
                current.get("ruff_config", ""),
                label="Ruff",
            ),
            "mypy_config": _config_path(
                root,
                mypy_config,
                current.get("mypy_config", ""),
                label="mypy",
            ),
            "pytest_path": _target_path(
                root,
                pytest_path,
                current.get("pytest_path", ""),
            ),
            "fail_fast": _bool_value(fail_fast, current.get("fail_fast", False)),
            "require_tools": _bool_value(
                require_tools,
                current.get("require_tools", False),
            ),
            "max_python_files": _bounded_int(
                max_python_files,
                current.get("max_python_files", 3000),
                minimum=_MIN_FILES,
                maximum=_MAX_FILES,
                field="max_python_files",
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
                INSERT INTO python_project_profiles(
                    project_root,
                    pyproject_enabled,
                    compile_enabled,
                    ruff_enabled,
                    mypy_enabled,
                    pytest_enabled,
                    ruff_config,
                    mypy_config,
                    pytest_path,
                    fail_fast,
                    require_tools,
                    max_python_files,
                    exclude_paths_json,
                    timeout_seconds,
                    max_output_chars,
                    actor,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_root) DO UPDATE SET
                    pyproject_enabled=excluded.pyproject_enabled,
                    compile_enabled=excluded.compile_enabled,
                    ruff_enabled=excluded.ruff_enabled,
                    mypy_enabled=excluded.mypy_enabled,
                    pytest_enabled=excluded.pytest_enabled,
                    ruff_config=excluded.ruff_config,
                    mypy_config=excluded.mypy_config,
                    pytest_path=excluded.pytest_path,
                    fail_fast=excluded.fail_fast,
                    require_tools=excluded.require_tools,
                    max_python_files=excluded.max_python_files,
                    exclude_paths_json=excluded.exclude_paths_json,
                    timeout_seconds=excluded.timeout_seconds,
                    max_output_chars=excluded.max_output_chars,
                    actor=excluded.actor,
                    updated_at=excluded.updated_at
                """,
                (
                    str(root),
                    int(values["pyproject_enabled"]),
                    int(values["compile_enabled"]),
                    int(values["ruff_enabled"]),
                    int(values["mypy_enabled"]),
                    int(values["pytest_enabled"]),
                    values["ruff_config"],
                    values["mypy_config"],
                    values["pytest_path"],
                    int(values["fail_fast"]),
                    int(values["require_tools"]),
                    values["max_python_files"],
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
            raise RuntimeError("No se pudo recuperar el perfil Python guardado.")
        return profile

    def get(self, project_root: Path) -> dict[str, Any] | None:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM python_project_profiles WHERE project_root = ?",
                (str(root),),
            ).fetchone()
        return _public_profile(dict(row)) if row is not None else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM python_project_profiles ORDER BY project_root COLLATE NOCASE"
            ).fetchall()
        return [_public_profile(dict(row)) for row in rows]

    def delete(self, project_root: Path) -> bool:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM python_project_profiles WHERE project_root = ?",
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
            "max_python_files": int(
                profile.get("max_python_files", 3000) if profile else 3000
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
        raise ValueError(f"El perfil Python requiere una carpeta existente: {root}")
    return root


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
            raise ValueError("Las exclusiones Python deben ser rutas relativas al proyecto.")
        resolved = (root / candidate).resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise ValueError("Las exclusiones Python deben permanecer dentro del proyecto.")
        relative = resolved.relative_to(root).as_posix()
        if relative and relative != "." and relative not in clean:
            clean.append(relative)
    return clean


def _config_path(root: Path, value: str | None, current: Any, *, label: str) -> str:
    raw = str(current or "") if value is None else str(value or "")
    raw = raw.strip()
    if not raw:
        return ""
    candidate = Path(raw)
    resolved = (
        candidate.resolve(strict=False)
        if candidate.is_absolute()
        else (root / candidate).resolve(strict=False)
    )
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"La configuración {label} debe permanecer dentro del proyecto.")
    if not resolved.is_file():
        raise ValueError(f"Configuración {label} no encontrada: {resolved}")
    return resolved.relative_to(root).as_posix()


def _target_path(root: Path, value: str | None, current: Any) -> str:
    raw = str(current or "") if value is None else str(value or "")
    raw = raw.strip()
    if not raw:
        return ""
    candidate = Path(raw)
    resolved = (
        candidate.resolve(strict=False)
        if candidate.is_absolute()
        else (root / candidate).resolve(strict=False)
    )
    if resolved != root and root not in resolved.parents:
        raise ValueError("La ruta de Pytest debe permanecer dentro del proyecto.")
    if not resolved.exists():
        raise ValueError(f"Ruta de Pytest no encontrada: {resolved}")
    return resolved.relative_to(root).as_posix() or "."


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


def _bool_value(value: bool | None, current: Any) -> bool:
    return bool(current) if value is None else value is True


def _public_profile(item: dict[str, Any]) -> dict[str, Any]:
    for name in (
        "pyproject_enabled",
        "compile_enabled",
        "ruff_enabled",
        "mypy_enabled",
        "pytest_enabled",
        "fail_fast",
        "require_tools",
    ):
        item[name] = bool(item.get(name))
    try:
        excluded = json.loads(str(item.pop("exclude_paths_json", "[]")))
    except json.JSONDecodeError:
        excluded = []
    item["exclude_paths"] = excluded if isinstance(excluded, list) else []
    return item
