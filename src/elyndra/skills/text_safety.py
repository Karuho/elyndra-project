from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

_SKIP_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "target",
    "build",
    "dist",
    "coverage",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}

_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".rst",
    ".py",
    ".php",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".kt",
    ".kts",
    ".css",
    ".scss",
    ".html",
    ".htm",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".xml",
    ".sql",
    ".sh",
    ".ini",
    ".conf",
    ".properties",
    ".env.example",
}


def should_skip(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(part in _SKIP_DIRECTORIES for part in relative.parts)


def iter_project_files(root: Path) -> Iterator[Path]:
    for current, directories, filenames in os.walk(root):
        directories[:] = [
            name
            for name in directories
            if name not in _SKIP_DIRECTORIES and not name.startswith(".elyndra")
        ]
        current_path = Path(current)
        for filename in filenames:
            yield current_path / filename


def looks_textual(path: Path, *, max_probe_bytes: int = 8192) -> bool:
    if path.name in {"Dockerfile", "Makefile", "Procfile", "LICENSE"}:
        return True
    if path.suffix.casefold() in _TEXT_EXTENSIONS:
        return True
    try:
        probe = path.read_bytes()[:max_probe_bytes]
    except OSError:
        return False
    if not probe:
        return True
    if b"\x00" in probe:
        return False
    printable = sum(byte in b"\n\r\t\f\b" or 32 <= byte <= 126 or byte >= 128 for byte in probe)
    return printable / len(probe) >= 0.85
