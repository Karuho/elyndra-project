"""Workspace authority policies for model-driven project mutation."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class MutationWorkspacePolicy(Protocol):
    """Authority boundary evaluated before model mutation source is disclosed."""

    def require_allowed(self, workspace_root: Path | str) -> None:
        """Raise when model-driven mutation is forbidden for ``workspace_root``."""


class PublicProjectMutationPolicy:
    """Allow project mutation only when it does not overlap the running product."""

    def __init__(self, protected_product_roots: tuple[Path | str, ...]) -> None:
        if not protected_product_roots:
            raise ValueError("La política pública requiere al menos una raíz protegida.")
        self._protected_product_roots = tuple(
            _canonical_directory(root, label="raíz de producto protegida")
            for root in protected_product_roots
        )

    @property
    def protected_product_roots(self) -> tuple[Path, ...]:
        """Return the immutable canonical roots protected by this policy."""

        return self._protected_product_roots

    def require_allowed(self, workspace_root: Path | str) -> None:
        workspace = _canonical_directory(workspace_root, label="workspace")
        for protected in self._protected_product_roots:
            if (
                workspace == protected
                or workspace in protected.parents
                or protected in workspace.parents
            ):
                raise PermissionError(
                    "El workspace de mutación se superpone con el producto Elyndra activo."
                )


def _canonical_directory(root: Path | str, *, label: str) -> Path:
    if not isinstance(root, (Path, str)):
        raise TypeError(f"La {label} debe ser una ruta.")
    requested = Path(root).expanduser()
    try:
        canonical = requested.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"No se pudo resolver la {label}: {requested}") from exc
    if not canonical.is_dir():
        raise ValueError(f"La {label} debe ser una carpeta: {canonical}")
    return canonical
