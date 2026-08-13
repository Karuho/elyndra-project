from __future__ import annotations

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


def _go_project(root: Path, *, formatted: bool = True, tests: bool = True) -> Path:
    root.mkdir(parents=True)
    (root / "go.mod").write_text(
        "module example.local/demo\n\ngo 1.23\n",
        encoding="utf-8",
    )
    body = "package demo\n\nfunc Add(a, b int) int { return a + b }\n"
    if not formatted:
        body = "package demo\nfunc Add(a,b int)int{return a+b}\n"
    (root / "demo.go").write_text(body, encoding="utf-8")
    if tests:
        (root / "demo_test.go").write_text(
            "package demo\n\nimport \"testing\"\n\n"
            "func TestAdd(t *testing.T) {\n"
            "\tif Add(1, 2) != 3 { t.Fatal(\"bad sum\") }\n"
            "}\n",
            encoding="utf-8",
        )
    return root


def test_go_router_clarifies_missing_path_and_routes_project() -> None:
    router = DeterministicRouter()

    missing = router.route("go verify")
    verify = router.route("verifica proyecto Go /tmp/go-demo")
    inspect = router.route("inspecciona proyecto Golang /tmp/go-demo")

    assert missing.kind == "clarification"
    assert missing.params["intended_skill"] == "go.verify_project"
    assert verify.skill_name == "go.verify_project"
    assert inspect.skill_name == "go.project_inspect"


def test_go_inspect_reports_module_without_execution(
    isolated_home: ElyndraPaths,
) -> None:
    project = _go_project(Path.home() / "Proyectos" / "go-inspect")
    marker = project / "executed"
    _executable(project / ".venv" / "bin" / "go", f"#!/bin/sh\ntouch '{marker}'\n")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "go.project_inspect",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    inventory = result.data["inventory"]
    assert inventory["go_files"] == 2
    assert inventory["test_files"] == 1
    assert inventory["module"] == "example.local/demo"
    assert inventory["go_version"] == "1.23"
    assert not marker.exists()


def test_go_external_project_requires_one_time_authorization(
    isolated_home: ElyndraPaths,
) -> None:
    project = _go_project(Path.home() / "Escritorio" / "go-external")
    app = ElyndraApplication.load(isolated_home)

    denied = app.execute_skill(
        "go.project_inspect",
        {"path": str(project)},
        approved=True,
    )
    allowed = app.execute_skill(
        "go.project_inspect",
        {"path": str(project), "allow_root_once": True},
        approved=True,
    )

    assert denied.ok is False
    assert "--allow-root-once" in denied.message
    assert allowed.ok is True
    assert allowed.data["authorization_scope"] == "project_once"


def test_go_module_validation_is_deterministic_and_does_not_run_go(
    isolated_home: ElyndraPaths,
) -> None:
    project = _go_project(Path.home() / "Proyectos" / "go-module")
    marker = project / "go-ran"
    _executable(project / ".venv" / "bin" / "go", f"#!/bin/sh\ntouch '{marker}'\n")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "go.module_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["report"]["errors"] == []
    assert not marker.exists()


def test_gofmt_uses_diff_mode_without_writing_files(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _go_project(Path.home() / "Proyectos" / "go-fmt", formatted=False)
    source = project / "demo.go"
    before = source.read_text(encoding="utf-8")
    log = Path.home() / "gofmt.log"
    _executable(
        project / ".venv" / "bin" / "gofmt",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nprintf 'diff found\\n'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "gofmt.check",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is False
    command = log.read_text(encoding="utf-8")
    assert command.startswith("-d ")
    assert "-w" not in command
    assert source.read_text(encoding="utf-8") == before
    assert result.data["shell"] is False


def test_go_commands_use_fixed_arguments_and_offline_environment(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _go_project(Path.home() / "Proyectos" / "go-fixed")
    log = Path.home() / "go.log"
    _executable(
        project / ".venv" / "bin" / "go",
        "#!/bin/sh\n"
        f"printf '%s|%s|%s|%s|%s\\n' \"$*\" \"$GOPROXY\" \"$GOSUMDB\" "
        f"\"$GOTOOLCHAIN\" \"$GOFLAGS\" >> '{log}'\n"
        "exit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    vet = app.execute_skill("go.vet", {"path": str(project)}, approved=True)
    build = app.execute_skill("go.build_project", {"path": str(project)}, approved=True)
    tests = app.execute_skill(
        "go.test_project",
        {"path": str(project), "test_mode": "short"},
        approved=True,
    )

    assert vet.ok and build.ok and tests.ok
    commands = log.read_text(encoding="utf-8")
    assert "vet ./...|off|off|local|-mod=readonly -buildvcs=false" in commands
    assert "build ./...|off|off|local|-mod=readonly -buildvcs=false" in commands
    assert "test -short -count=1 ./...|off|off|local|-mod=readonly -buildvcs=false" in commands
    forbidden = (" get ", " install ", " generate ", " mod tidy")
    assert not any(item in f" {commands} " for item in forbidden)


def test_go_verify_persists_partial_history_without_tools(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _go_project(Path.home() / "Proyectos" / "go-history")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "go.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    run = result.data["verification_run"]
    assert run["toolchain"] == "go"
    assert run["status"] == "partial"
    stages = {item["name"]: item for item in result.data["stages"]}
    assert stages["inspect"]["status"] == "passed"
    assert stages["module"]["status"] == "passed"
    assert stages["fmt"]["status"] == "unavailable"
    assert stages["vet"]["status"] == "unavailable"


def test_go_verify_passes_with_controlled_fake_tools(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _go_project(Path.home() / "Proyectos" / "go-verify")
    _executable(project / ".venv" / "bin" / "go", "#!/bin/sh\nexit 0\n")
    _executable(project / ".venv" / "bin" / "gofmt", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "go.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["verification_run"]["status"] == "passed"
    assert all(item["status"] == "passed" for item in result.data["stages"])


def test_go_profile_and_control_center(isolated_home: ElyndraPaths) -> None:
    project = _go_project(Path.home() / "Proyectos" / "go-profile")
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    profile = service.save_go_profile(
        {
            "project_root": str(project),
            "vet_enabled": False,
            "test_mode": "short",
            "max_go_files": 222,
        }
    )
    overview = service.control_overview()
    projects = service.control_projects()

    assert profile["vet_enabled"] is False
    assert profile["test_mode"] == "short"
    assert profile["max_go_files"] == 222
    assert overview["go_profiles"] == 1
    assert "go_verifications" in overview
    assert projects["go_profiles"][0]["project_root"] == str(project)
    with app.database.connect() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert version == "50"


def test_go_knowledge_package_is_valid(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    package = Path(__file__).parents[1] / "knowledge-packs" / "go-modern-basic"

    inspected = app.alexandria_packages.inspect(package)

    assert inspected["package_id"] == "programming.go.modern-basic"
    assert inspected["domain"] == "programming/go"
    assert inspected["source_count"] == 1


def test_go_source_contains_no_install_or_generate_commands() -> None:
    source = (
        Path(__file__).parents[1] / "src" / "elyndra" / "skills" / "go_project.py"
    ).read_text(encoding="utf-8")

    forbidden_argv = (
        '[str(tool.path), "get"',
        '[str(tool.path), "install"',
        '[str(tool.path), "generate"',
        '"mod", "tidy"',
    )
    assert not any(item in source for item in forbidden_argv)
