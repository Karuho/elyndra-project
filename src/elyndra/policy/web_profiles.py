from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.db import Database

_MIN_TIMEOUT_SECONDS = 5
_MAX_TIMEOUT_SECONDS = 600
_MIN_OUTPUT_CHARS = 1_000
_MAX_OUTPUT_CHARS = 50_000
_MIN_FILES = 1
_MAX_FILES = 20_000
_DEFAULT_EXCLUDES = (".git", "node_modules", "vendor", "dist", "build")
_FRAMEWORK_PRESETS = {
    "auto",
    "generic",
    "angular",
    "vite",
    "react",
    "vue",
    "svelte",
    "astro",
    "next",
    "nuxt",
}


class WebProjectProfileRepository:
    """Persist safe defaults for controlled frontend verification."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save(
        self,
        project_root: Path,
        *,
        actor: str,
        html_enabled: bool | None = None,
        css_enabled: bool | None = None,
        javascript_enabled: bool | None = None,
        typescript_enabled: bool | None = None,
        eslint_enabled: bool | None = None,
        stylelint_enabled: bool | None = None,
        framework_checks_enabled: bool | None = None,
        framework_preset: str | None = None,
        eslint_config: str | None = None,
        stylelint_config: str | None = None,
        fail_fast: bool | None = None,
        require_tools: bool | None = None,
        max_files: int | None = None,
        exclude_paths: list[str] | tuple[str, ...] | str | None = None,
        timeout_seconds: int | None = None,
        max_output_chars: int | None = None,
    ) -> dict[str, Any]:
        root = _validate_project_root(project_root)
        current = self.get(root) or {}
        now = datetime.now(UTC).isoformat()
        values = {
            "html_enabled": _bool_value(html_enabled, current.get("html_enabled", True)),
            "css_enabled": _bool_value(css_enabled, current.get("css_enabled", True)),
            "javascript_enabled": _bool_value(
                javascript_enabled,
                current.get("javascript_enabled", True),
            ),
            "typescript_enabled": _bool_value(
                typescript_enabled,
                current.get("typescript_enabled", True),
            ),
            "eslint_enabled": _bool_value(
                eslint_enabled,
                current.get("eslint_enabled", True),
            ),
            "stylelint_enabled": _bool_value(
                stylelint_enabled,
                current.get("stylelint_enabled", True),
            ),
            "framework_checks_enabled": _bool_value(
                framework_checks_enabled,
                current.get("framework_checks_enabled", True),
            ),
            "framework_preset": _framework_preset(
                framework_preset,
                current.get("framework_preset", "auto"),
            ),
            "eslint_config": _config_path(
                root,
                eslint_config,
                current.get("eslint_config", ""),
                label="ESLint",
            ),
            "stylelint_config": _config_path(
                root,
                stylelint_config,
                current.get("stylelint_config", ""),
                label="Stylelint",
            ),
            "fail_fast": _bool_value(fail_fast, current.get("fail_fast", False)),
            "require_tools": _bool_value(
                require_tools,
                current.get("require_tools", False),
            ),
            "max_files": _bounded_int(
                max_files,
                current.get("max_files", 3000),
                minimum=_MIN_FILES,
                maximum=_MAX_FILES,
                field="max_files",
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
                INSERT INTO web_project_profiles(
                    project_root,
                    html_enabled,
                    css_enabled,
                    javascript_enabled,
                    typescript_enabled,
                    eslint_enabled,
                    stylelint_enabled,
                    framework_checks_enabled,
                    framework_preset,
                    eslint_config,
                    stylelint_config,
                    fail_fast,
                    require_tools,
                    max_files,
                    exclude_paths_json,
                    timeout_seconds,
                    max_output_chars,
                    actor,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_root) DO UPDATE SET
                    html_enabled=excluded.html_enabled,
                    css_enabled=excluded.css_enabled,
                    javascript_enabled=excluded.javascript_enabled,
                    typescript_enabled=excluded.typescript_enabled,
                    eslint_enabled=excluded.eslint_enabled,
                    stylelint_enabled=excluded.stylelint_enabled,
                    framework_checks_enabled=excluded.framework_checks_enabled,
                    framework_preset=excluded.framework_preset,
                    eslint_config=excluded.eslint_config,
                    stylelint_config=excluded.stylelint_config,
                    fail_fast=excluded.fail_fast,
                    require_tools=excluded.require_tools,
                    max_files=excluded.max_files,
                    exclude_paths_json=excluded.exclude_paths_json,
                    timeout_seconds=excluded.timeout_seconds,
                    max_output_chars=excluded.max_output_chars,
                    actor=excluded.actor,
                    updated_at=excluded.updated_at
                """,
                (
                    str(root),
                    int(values["html_enabled"]),
                    int(values["css_enabled"]),
                    int(values["javascript_enabled"]),
                    int(values["typescript_enabled"]),
                    int(values["eslint_enabled"]),
                    int(values["stylelint_enabled"]),
                    int(values["framework_checks_enabled"]),
                    values["framework_preset"],
                    values["eslint_config"],
                    values["stylelint_config"],
                    int(values["fail_fast"]),
                    int(values["require_tools"]),
                    values["max_files"],
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
            raise RuntimeError("No se pudo recuperar el perfil web guardado.")
        return profile

    def get(self, project_root: Path) -> dict[str, Any] | None:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM web_project_profiles WHERE project_root = ?",
                (str(root),),
            ).fetchone()
        return _public_profile(dict(row)) if row is not None else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM web_project_profiles ORDER BY project_root COLLATE NOCASE"
            ).fetchall()
        return [_public_profile(dict(row)) for row in rows]

    def delete(self, project_root: Path) -> bool:
        root = project_root.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM web_project_profiles WHERE project_root = ?",
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
            "max_files": int(profile.get("max_files", 3000) if profile else 3000),
            "exclude_paths": list(
                profile.get("exclude_paths", _DEFAULT_EXCLUDES)
                if profile
                else _DEFAULT_EXCLUDES
            ),
        }


def _validate_project_root(project_root: Path) -> Path:
    root = project_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"El perfil web requiere una carpeta existente: {root}")
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
            raise ValueError("Las exclusiones web deben ser rutas relativas al proyecto.")
        resolved = (root / candidate).resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise ValueError("Las exclusiones web deben permanecer dentro del proyecto.")
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
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=False)
    else:
        resolved = (root / candidate).resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"La configuración {label} debe permanecer dentro del proyecto.")
    if not resolved.is_file():
        raise ValueError(f"Configuración {label} no encontrada: {resolved}")
    return resolved.relative_to(root).as_posix()


def _framework_preset(value: str | None, current: Any) -> str:
    resolved = str(current or "auto") if value is None else str(value or "auto")
    resolved = resolved.strip().casefold()
    if resolved not in _FRAMEWORK_PRESETS:
        allowed = ", ".join(sorted(_FRAMEWORK_PRESETS))
        raise ValueError(f"framework_preset debe ser uno de: {allowed}.")
    return resolved


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
        "html_enabled",
        "css_enabled",
        "javascript_enabled",
        "typescript_enabled",
        "eslint_enabled",
        "stylelint_enabled",
        "framework_checks_enabled",
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
