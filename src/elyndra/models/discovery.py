from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

_SKIP_DIRECTORIES = {
    ".git",
    ".venv",
    "node_modules",
    "vendor",
    "target",
    "build",
    "dist",
    "__pycache__",
    "Trash",
}


@dataclass(frozen=True, slots=True)
class RuntimeCandidate:
    kind: str
    path: str
    version: str


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    path: str
    size_bytes: int
    size_mb: float


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    runtimes: tuple[RuntimeCandidate, ...]
    models: tuple[ModelCandidate, ...]
    running_processes: tuple[str, ...]
    scanned_roots: tuple[str, ...]
    scanned_files: int
    truncated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "runtimes": [asdict(item) for item in self.runtimes],
            "models": [asdict(item) for item in self.models],
            "running_processes": list(self.running_processes),
            "scanned_roots": list(self.scanned_roots),
            "scanned_files": self.scanned_files,
            "truncated": self.truncated,
        }


def default_search_roots() -> tuple[Path, ...]:
    home = Path.home()
    candidates = (
        home / "Proyectos",
        home / "Descargas",
        home / ".cache" / "llama.cpp",
        home / ".cache" / "huggingface",
        home / ".local" / "share",
        home / ".ollama",
        Path("/opt"),
        Path("/usr/local"),
    )
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved.exists() and resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return tuple(unique)


def discover_local_models(
    roots: tuple[Path, ...] | None = None,
    *,
    max_files: int = 50000,
) -> DiscoveryReport:
    selected_roots = roots or default_search_roots()
    models, runtime_files, scanned_files, truncated = _discover_files(
        selected_roots, max_files=max_files
    )
    runtimes = _discover_runtimes(runtime_files)
    processes = _discover_running_processes()
    return DiscoveryReport(
        runtimes=tuple(runtimes),
        models=tuple(models),
        running_processes=tuple(processes),
        scanned_roots=tuple(str(root) for root in selected_roots),
        scanned_files=scanned_files,
        truncated=truncated,
    )


def _discover_runtimes(extra_paths: tuple[Path, ...] = ()) -> list[RuntimeCandidate]:
    found: dict[str, RuntimeCandidate] = {}
    for name in ("llama-cli", "llama-server", "ollama"):
        resolved = shutil.which(name)
        if not resolved:
            continue
        path = str(Path(resolved).resolve())
        found[path] = RuntimeCandidate(name, path, _read_version(Path(path)))

    for cmdline in _iter_proc_cmdlines():
        if not cmdline:
            continue
        executable = Path(cmdline[0])
        name = _runtime_kind(executable)
        if name is None:
            continue
        try:
            path = str(executable.resolve())
        except OSError:
            path = str(executable)
        found.setdefault(path, RuntimeCandidate(name, path, _read_version(Path(path))))

    for executable in extra_paths:
        name = _runtime_kind(executable)
        if name is None:
            continue
        path = str(executable.resolve())
        found.setdefault(path, RuntimeCandidate(name, path, _read_version(executable)))
    return sorted(found.values(), key=lambda item: (item.kind, item.path))


def _read_version(binary: Path) -> str:
    try:
        completed = subprocess.run(
            [str(binary), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "desconocida"
    output = (completed.stdout or completed.stderr).strip().splitlines()
    return output[0][:240] if output else "desconocida"


def _discover_running_processes() -> list[str]:
    matches: list[str] = []
    for cmdline in _iter_proc_cmdlines():
        rendered = " ".join(cmdline)
        lowered = rendered.casefold()
        if any(term in lowered for term in ("llama", "ollama", "nomad")):
            matches.append(rendered[:1000])
    return sorted(set(matches))


def _iter_proc_cmdlines() -> list[list[str]]:
    proc = Path("/proc")
    results: list[list[str]] = []
    if not proc.exists():
        return results
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        parts = [part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part]
        if parts:
            results.append(parts)
    return results


def _discover_files(
    roots: tuple[Path, ...],
    *,
    max_files: int,
) -> tuple[list[ModelCandidate], tuple[Path, ...], int, bool]:
    models: dict[Path, ModelCandidate] = {}
    runtimes: set[Path] = set()
    scanned_files = 0
    truncated = False
    for root in roots:
        if not root.exists():
            continue
        for current, directories, filenames in os.walk(root):
            directories[:] = [name for name in directories if name not in _SKIP_DIRECTORIES]
            current_path = Path(current)
            for filename in filenames:
                scanned_files += 1
                if scanned_files > max_files:
                    truncated = True
                    break
                candidate = current_path / filename
                lowered = filename.casefold()
                if lowered.endswith(".gguf"):
                    try:
                        resolved = candidate.resolve()
                        size = resolved.stat().st_size
                    except OSError:
                        continue
                    models[resolved] = ModelCandidate(
                        path=str(resolved),
                        size_bytes=size,
                        size_mb=round(size / 1024**2, 2),
                    )
                    continue
                if _runtime_kind(candidate) is None:
                    continue
                try:
                    mode = candidate.stat().st_mode
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if mode & 0o111:
                    runtimes.add(resolved)
            if truncated:
                break
        if truncated:
            break
    ordered = sorted(models.values(), key=lambda item: (item.size_bytes, item.path))
    return ordered, tuple(sorted(runtimes)), scanned_files, truncated


def _runtime_kind(path: Path) -> str | None:
    lowered = path.name.casefold()
    if lowered in {"llama-cli", "llama-server", "ollama"}:
        return lowered
    if lowered == "main" and "llama" in str(path.parent).casefold():
        return "llama-cli-legacy"
    if lowered == "server" and "llama" in str(path.parent).casefold():
        return "llama-server-legacy"
    return None
