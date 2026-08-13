from __future__ import annotations

from pathlib import Path


class PathNotAllowed(PermissionError):
    pass


def ensure_allowed(path: Path, allowed_roots: tuple[Path, ...]) -> Path:
    resolved = path.expanduser().resolve()
    for root in allowed_roots:
        allowed = root.expanduser().resolve()
        if resolved == allowed or allowed in resolved.parents:
            return resolved
    roots = ", ".join(str(root) for root in allowed_roots)
    raise PathNotAllowed(f"Ruta fuera de las raíces permitidas: {resolved}. Permitidas: {roots}")
