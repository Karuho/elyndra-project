from pathlib import Path

import pytest

from elyndra.skills.path_safety import PathNotAllowed, ensure_allowed


def test_path_inside_root_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    child = root / "project" / "file.py"
    child.parent.mkdir(parents=True)
    child.write_text("print('ok')", encoding="utf-8")
    assert ensure_allowed(child, (root,)) == child.resolve()


def test_path_outside_root_is_denied(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    outside = tmp_path / "outside.txt"
    root.mkdir()
    outside.write_text("no", encoding="utf-8")
    with pytest.raises(PathNotAllowed):
        ensure_allowed(outside, (root,))
