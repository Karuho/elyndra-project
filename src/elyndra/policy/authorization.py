from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from elyndra.db import Database


class AuthorizationScope(StrEnum):
    SINGLE_FILE = "single_file"
    PROJECT_ONCE = "project_once"
    PROJECT_PERSISTENT = "project_persistent"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    scope: AuthorizationScope
    source: str
    resolved_path: Path
    project_root: Path | None
    reason: str
    expires_after_execution: bool = False

    def as_data(self) -> dict[str, Any]:
        data = asdict(self)
        data["scope"] = self.scope.value
        data["resolved_path"] = str(self.resolved_path)
        data["project_root"] = (
            str(self.project_root) if self.project_root is not None else None
        )
        return {
            "authorization_scope": data.pop("scope"),
            "authorization_source": data.pop("source"),
            "authorization_reason": data.pop("reason"),
            "authorization_expires_after_execution": data.pop(
                "expires_after_execution"
            ),
            **data,
        }


class TrustedProjectRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def trust(self, path: Path, *, actor: str) -> dict[str, Any]:
        resolved = validate_trusted_project_path(path)
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO trusted_project_roots(path, actor, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET actor=excluded.actor, updated_at=excluded.updated_at
                """,
                (str(resolved), actor, now, now),
            )
        return self.inspect(resolved)

    def untrust(self, path: Path) -> bool:
        resolved = path.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM trusted_project_roots WHERE path = ?", (str(resolved),)
            )
            return cursor.rowcount > 0

    def list_all(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM trusted_project_roots ORDER BY path COLLATE NOCASE"
            ).fetchall()
        return [dict(row) for row in rows]

    def inspect(self, path: Path) -> dict[str, Any]:
        resolved = path.expanduser().resolve(strict=False)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM trusted_project_roots WHERE path = ?", (str(resolved),)
            ).fetchone()
        return {
            "path": str(resolved),
            "trusted": row is not None,
            "record": dict(row) if row is not None else None,
        }

    def matching_root(self, path: Path) -> Path | None:
        resolved = path.expanduser().resolve(strict=False)
        candidates = [Path(item["path"]) for item in self.list_all()]
        matches = [
            root
            for root in candidates
            if resolved == root or root in resolved.parents
        ]
        return max(matches, key=lambda item: len(item.parts), default=None)


class AuthorizationPolicy:
    def __init__(
        self,
        allowed_roots: tuple[Path, ...],
        trusted_projects: TrustedProjectRepository,
    ) -> None:
        self.allowed_roots = tuple(
            root.expanduser().resolve(strict=False) for root in allowed_roots
        )
        self.trusted_projects = trusted_projects

    def single_file(
        self,
        path: Path,
        *,
        source: str = "explicit_approval",
    ) -> AuthorizationDecision:
        resolved = path.expanduser().resolve(strict=False)
        return AuthorizationDecision(
            allowed=True,
            scope=AuthorizationScope.SINGLE_FILE,
            source=source,
            resolved_path=resolved,
            project_root=None,
            reason="Acceso limitado a un único archivo durante esta ejecución.",
            expires_after_execution=True,
        )

    def project(
        self,
        root: Path,
        *,
        allow_once: bool = False,
        source: str | None = None,
    ) -> AuthorizationDecision:
        resolved = root.expanduser().resolve(strict=False)
        configured = _matching_root(resolved, self.allowed_roots)
        if configured is not None:
            return AuthorizationDecision(
                allowed=True,
                scope=AuthorizationScope.PROJECT_PERSISTENT,
                source="configured_root",
                resolved_path=resolved,
                project_root=resolved,
                reason=f"Proyecto cubierto por la raíz configurada {configured}.",
            )
        trusted = self.trusted_projects.matching_root(resolved)
        if trusted is not None:
            return AuthorizationDecision(
                allowed=True,
                scope=AuthorizationScope.PROJECT_PERSISTENT,
                source="trusted_project",
                resolved_path=resolved,
                project_root=resolved,
                reason=f"Proyecto confiable registrado explícitamente: {trusted}.",
            )
        if allow_once:
            return AuthorizationDecision(
                allowed=True,
                scope=AuthorizationScope.PROJECT_ONCE,
                source=source or "explicit_approval",
                resolved_path=resolved,
                project_root=resolved,
                reason="Acceso concedido solo a este proyecto y solo para esta ejecución.",
                expires_after_execution=True,
            )
        return AuthorizationDecision(
            allowed=False,
            scope=AuthorizationScope.PROJECT_ONCE,
            source="explicit_approval_required",
            resolved_path=resolved,
            project_root=resolved,
            reason=(
                "El proyecto está fuera de las raíces persistentes y no está registrado "
                "como confiable. Requiere autorización puntual explícita."
            ),
            expires_after_execution=True,
        )


def validate_trusted_project_path(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"La ruta confiable debe ser una carpeta existente: {resolved}")
    home = Path.home().resolve(strict=False)
    filesystem_root = Path(resolved.anchor)
    if resolved in (filesystem_root, home) or resolved.parent == filesystem_root:
        raise ValueError(
            "La ruta es demasiado amplia para registrarla como proyecto confiable."
        )
    if resolved in home.parents:
        raise ValueError(
            "No se puede registrar como proyecto confiable una raíz que contenga todo HOME."
        )
    return resolved


def _matching_root(path: Path, roots: tuple[Path, ...]) -> Path | None:
    matches = [root for root in roots if path == root or root in path.parents]
    return max(matches, key=lambda item: len(item.parts), default=None)
