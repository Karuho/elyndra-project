from __future__ import annotations

import getpass
import hashlib
import shutil
from pathlib import Path

import pytest

from elyndra.config import write_default_config
from elyndra.paths import ElyndraPaths


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ElyndraPaths:
    short_id = hashlib.sha256(str(tmp_path).encode("utf-8")).hexdigest()[:6]
    test_root = (
        Path.cwd()
        / "build"
        / "test-runs"
        / "v089-online-integration-fix"
        / short_id
    )
    if test_root.exists():
        shutil.rmtree(test_root)
    monkeypatch.setenv("HOME", str(test_root / "h"))
    monkeypatch.setenv("ELYNDRA_HOME", str(test_root / "r"))
    paths = ElyndraPaths.from_environment()
    write_default_config(paths, owner_name="Test Owner", system_user=getpass.getuser())
    projects = Path.home() / "Proyectos"
    projects.mkdir(parents=True, exist_ok=True)
    return paths
