from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from elyndra.policy.authorization import (
    AuthorizationDecision,
    AuthorizationScope,
)


@dataclass(frozen=True, slots=True)
class WorkspaceScope:
    """Exact canonical filesystem root available to one autonomy run."""

    root: Path

    @classmethod
    def from_root(cls, root: Path | str) -> WorkspaceScope:
        requested = Path(root).expanduser()

        try:
            resolved = requested.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError(
                f"El workspace debe existir: {requested}"
            ) from exc

        if not resolved.is_dir():
            raise ValueError(
                f"El workspace debe ser una carpeta: {resolved}"
            )

        home = Path.home().resolve(strict=False)
        filesystem_root = Path(resolved.anchor)

        if resolved in (filesystem_root, home):
            raise ValueError(
                "El workspace es demasiado amplio para autonomía."
            )

        if resolved.parent == filesystem_root:
            raise ValueError(
                "No se permite una raíz de primer nivel como workspace autónomo."
            )

        if resolved in home.parents:
            raise ValueError(
                "El workspace no puede contener todo el HOME del usuario."
            )

        return cls(root=resolved)

    @classmethod
    def from_authorization(
        cls,
        decision: AuthorizationDecision,
    ) -> WorkspaceScope:
        if not decision.allowed:
            raise PermissionError(
                "La autorización existente no permite el proyecto."
            )

        if decision.scope not in {
            AuthorizationScope.PROJECT_ONCE,
            AuthorizationScope.PROJECT_PERSISTENT,
        }:
            raise PermissionError(
                "La autonomía requiere autorización de proyecto, "
                "no autorización de archivo individual."
            )

        if decision.project_root is None:
            raise PermissionError(
                "La autorización no define una raíz de proyecto."
            )

        return cls.from_root(decision.project_root)

    def resolve(
        self,
        path: Path | str,
        *,
        must_exist: bool = False,
    ) -> Path:
        candidate = Path(path).expanduser()

        if not candidate.is_absolute():
            candidate = self.root / candidate

        try:
            resolved = candidate.resolve(strict=must_exist)
        except FileNotFoundError as exc:
            raise ValueError(
                f"La ruta requerida no existe: {candidate}"
            ) from exc

        if resolved != self.root and self.root not in resolved.parents:
            raise PermissionError(
                f"La ruta escapa del workspace autorizado: {resolved}"
            )

        return resolved

    def contains(self, path: Path | str) -> bool:
        try:
            self.resolve(path)
        except (PermissionError, ValueError, OSError):
            return False
        return True
