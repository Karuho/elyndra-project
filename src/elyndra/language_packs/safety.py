from __future__ import annotations

import hashlib
from pathlib import Path


def regular_file(path: Path, *, max_bytes: int) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ValueError("La fuente no puede ser un enlace simbólico.")
    resolved = expanded.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("La fuente debe ser un archivo regular.")
    if resolved.stat().st_size > max_bytes:
        raise ValueError("La fuente supera el límite de tamaño permitido.")
    return resolved


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(source_id: str, external_id: str) -> str:
    return hashlib.sha256(f"{source_id}\0{external_id}".encode()).hexdigest()
