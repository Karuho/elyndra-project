from __future__ import annotations

from pathlib import Path

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths


def _register_project(app: ElyndraApplication) -> Path:
    root = Path.home() / "Proyectos" / "demo"
    root.mkdir(parents=True)
    (root / "app.py").write_text(
        "class DemoService:\n    pass\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Demo\n", encoding="utf-8")
    app.projects.add("demo", root)
    return root


def test_project_inspection(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    root = _register_project(app)

    result = app.execute_skill("project.inspect", {"name": "demo"})
    assert result.ok is True
    assert result.data["path"] == str(root.resolve())
    assert result.data["file_count"] == 2
    assert result.data["extensions"][".py"] == 1


def test_project_text_search(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    _register_project(app)

    result = app.execute_skill(
        "project.search_text",
        {"name": "demo", "query": "DemoService"},
    )
    assert result.ok is True
    assert len(result.data["results"]) == 1
    assert result.data["results"][0]["relative_path"] == "app.py"


def test_file_read_with_line_numbers(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    root = _register_project(app)

    result = app.execute_skill(
        "file.read",
        {"path": str(root / "app.py"), "start_line": 1, "end_line": 2},
    )
    assert result.ok is True
    assert "1 | class DemoService:" in result.message
    assert "2 |     pass" in result.message
