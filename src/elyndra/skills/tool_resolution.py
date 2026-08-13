from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ToolResolution:
    path: Path | None
    source: str


def resolve_project_tool(project_root: Path, name: str) -> ToolResolution:
    local_candidates = (
        project_root / "vendor" / "bin" / name,
        project_root / "node_modules" / ".bin" / name,
        project_root / ".venv" / "bin" / name,
        project_root / "venv" / "bin" / name,
    )
    for candidate in local_candidates:
        resolved = candidate.resolve(strict=False)
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return ToolResolution(resolved, "project_local")
    global_binary = shutil.which(name)
    if global_binary:
        # Preserve the invoked filename for multi-call drivers such as Swift.
        # Resolving a `swift`/`swiftc` symlink to `swift-driver` changes argv[0]
        # and makes the compiler reject an otherwise valid invocation.
        return ToolResolution(Path(global_binary).absolute(), "global_path")
    return ToolResolution(None, "unavailable")
