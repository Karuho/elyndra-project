from __future__ import annotations

import json
from pathlib import Path

import pytest

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths
from elyndra.router import DeterministicRouter
from elyndra.web.server import ElyndraWebService


def _executable(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _python_project(root: Path, *, with_tests: bool = True) -> Path:
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        """[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "example-python"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.100", "pydantic>=2"]

[project.optional-dependencies]
dev = ["pytest>=8", "ruff>=0.9", "mypy>=1"]

[project.scripts]
example = "example.cli:main"
""",
        encoding="utf-8",
    )
    package = root / "src" / "example" / "__init__.py"
    package.parent.mkdir(parents=True)
    package.write_text("VALUE: int = 1\n", encoding="utf-8")
    if with_tests:
        test = root / "tests" / "test_example.py"
        test.parent.mkdir()
        test.write_text("def test_value():\n    assert 1 == 1\n", encoding="utf-8")
    return root


def test_python_router_clarifies_missing_path_and_routes_tools() -> None:
    router = DeterministicRouter()

    missing = router.route("python verify")
    verify = router.route("verifica proyecto Python /tmp/app")
    ruff = router.route("ejecuta Ruff en /tmp/app")
    pytest_route = router.route("ejecuta Pytest en /tmp/app")

    assert missing.kind == "clarification"
    assert missing.params["intended_skill"] == "python.verify_project"
    assert verify.skill_name == "python.verify_project"
    assert ruff.skill_name == "ruff.check"
    assert pytest_route.skill_name == "pytest.run"


def test_python_inspect_reports_metadata_without_script_values(
    isolated_home: ElyndraPaths,
) -> None:
    project = _python_project(Path.home() / "Proyectos" / "inspect-python")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "python.project_inspect",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    inventory = result.data["inventory"]
    assert inventory["project_name"] == "example-python"
    assert inventory["requires_python"] == ">=3.11"
    assert inventory["script_names"] == ["example"]
    assert "example.cli:main" not in json.dumps(inventory)
    assert inventory["frameworks"] == ["FastAPI", "Pydantic", "Pytest"]
    assert inventory["python_files"] == 2


def test_python_external_project_requires_one_time_authorization(
    isolated_home: ElyndraPaths,
) -> None:
    project = _python_project(Path.home() / "Escritorio" / "python-external")
    app = ElyndraApplication.load(isolated_home)

    denied = app.execute_skill(
        "python.project_inspect",
        {"path": str(project)},
        approved=True,
    )
    allowed = app.execute_skill(
        "python.project_inspect",
        {"path": str(project), "allow_root_once": True},
        approved=True,
    )

    assert denied.ok is False
    assert "--allow-root-once" in denied.message
    assert allowed.ok is True
    assert allowed.data["authorization_scope"] == "project_once"


def test_pyproject_validation_is_deterministic(isolated_home: ElyndraPaths) -> None:
    project = _python_project(Path.home() / "Proyectos" / "pyproject-valid")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "python.pyproject_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["stage_status"] == "passed"
    assert result.data["report"]["errors"] == []


def test_pyproject_validation_rejects_invalid_toml(isolated_home: ElyndraPaths) -> None:
    project = _python_project(Path.home() / "Proyectos" / "pyproject-invalid")
    (project / "pyproject.toml").write_text("[project\nname = 1\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "python.pyproject_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is False
    assert "TOML inválido" in result.message


def test_python_compile_detects_syntax_and_excludes_virtualenv(
    isolated_home: ElyndraPaths,
) -> None:
    project = _python_project(Path.home() / "Proyectos" / "compile-python")
    broken = project / "src" / "example" / "broken.py"
    broken.write_text("def broken(:\n    pass\n", encoding="utf-8")
    ignored = project / ".venv" / "lib" / "ignored.py"
    ignored.parent.mkdir(parents=True)
    ignored.write_text("def ignored(:\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "python.compile_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is False
    assert result.data["failed_files"] == 1
    assert result.data["scanned_files"] == 3
    assert "broken.py" in result.message
    assert "ignored.py" not in result.message
    assert result.data["shell"] is False


def test_ruff_prefers_project_local_binary_and_uses_fixed_argv(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _python_project(Path.home() / "Proyectos" / "ruff-local")
    log = Path.home() / "ruff.log"
    _executable(
        project / ".venv" / "bin" / "ruff",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill("ruff.check", {"path": str(project)}, approved=True)

    assert result.ok is True
    assert result.data["tool_source"] == "project_local"
    assert result.data["shell"] is False
    assert log.read_text(encoding="utf-8").strip() == (
        "check . --no-cache --output-format concise"
    )


def test_mypy_rejects_config_outside_project(isolated_home: ElyndraPaths) -> None:
    project = _python_project(Path.home() / "Proyectos" / "mypy-safe")
    outside = Path.home() / "mypy.ini"
    outside.write_text("[mypy]\n", encoding="utf-8")
    _executable(project / ".venv" / "bin" / "mypy", "#!/bin/sh\nexit 0\n")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "mypy.check",
        {"path": str(project), "mypy_config": str(outside)},
        approved=True,
    )

    assert result.ok is False
    assert "permanecer dentro del proyecto" in result.message


def test_pytest_uses_project_local_binary_without_cache(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _python_project(Path.home() / "Proyectos" / "pytest-local")
    log = Path.home() / "pytest.log"
    _executable(
        project / ".venv" / "bin" / "pytest",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "pytest.run",
        {"path": str(project), "pytest_path": "tests"},
        approved=True,
    )

    assert result.ok is True
    assert result.data["tool_source"] == "project_local"
    assert log.read_text(encoding="utf-8").strip() == "-q -p no:cacheprovider tests"


def test_python_profile_persists_safe_settings(isolated_home: ElyndraPaths) -> None:
    project = _python_project(Path.home() / "Proyectos" / "python-profile")
    (project / "mypy.ini").write_text("[mypy]\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    profile = app.python_profiles.save(
        project,
        actor=app.identity.system_user,
        ruff_enabled=False,
        mypy_config="mypy.ini",
        pytest_path="tests",
        fail_fast=True,
        require_tools=True,
        max_python_files=123,
        exclude_paths=[".venv", "generated"],
    )

    assert profile["ruff_enabled"] is False
    assert profile["mypy_config"] == "mypy.ini"
    assert profile["pytest_path"] == "tests"
    assert profile["fail_fast"] is True
    assert profile["require_tools"] is True
    assert profile["max_python_files"] == 123
    assert profile["exclude_paths"] == [".venv", "generated"]


def test_python_verify_runs_pipeline_and_persists_history(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _python_project(Path.home() / "Proyectos" / "python-pipeline")
    for name in ("ruff", "mypy", "pytest"):
        _executable(project / ".venv" / "bin" / name, "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "python.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["verification_status"] == "passed"
    assert [stage["status"] for stage in result.data["stages"]] == [
        "passed",
        "passed",
        "passed",
        "passed",
        "passed",
        "passed",
    ]
    run = app.verification_runs.get(result.data["verification_run_id"])
    assert run is not None
    assert run["toolchain"] == "python"
    assert run["status"] == "passed"


def test_python_verify_is_partial_when_optional_tools_are_missing(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _python_project(Path.home() / "Proyectos" / "python-partial")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "python.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["verification_status"] == "partial"
    statuses = {stage["name"]: stage["status"] for stage in result.data["stages"]}
    assert statuses["compile"] == "passed"
    assert statuses["ruff"] == "unavailable"
    assert statuses["mypy"] == "unavailable"
    assert statuses["pytest"] == "unavailable"


def test_python_verify_can_require_tools(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _python_project(Path.home() / "Proyectos" / "python-required")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "python.verify_project",
        {
            "path": str(project),
            "require_tools": True,
            "fail_fast": True,
        },
        approved=True,
    )

    assert result.ok is False
    assert result.data["verification_status"] == "failed"
    assert result.data["stages"][-1]["name"] == "ruff"


def test_python_control_center_and_schema(isolated_home: ElyndraPaths) -> None:
    project = _python_project(Path.home() / "Proyectos" / "python-control")
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    profile = service.save_python_profile(
        {
            "project_root": str(project),
            "ruff_enabled": False,
            "max_python_files": 99,
        }
    )
    overview = service.control_overview()
    projects = service.control_projects()

    assert profile["ruff_enabled"] is False
    assert overview["python_profiles"] == 1
    assert projects["python_profiles"][0]["project_root"] == str(project)
    with app.database.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(python_project_profiles)")
        }
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert {"ruff_enabled", "mypy_config", "pytest_path"} <= columns
    assert version == "50"


def test_shipped_python_package_is_valid(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    package = Path(__file__).resolve().parents[1] / "knowledge-packs" / "python-modern-basic"

    report = app.alexandria_packages.inspect(package)

    assert report["package_id"] == "programming.python.modern-basic"
    assert report["domain"] == "programming/python"
    assert report["tier"] == "optional"
    assert report["source_count"] == 1
