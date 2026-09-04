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


def _dart_project(
    root: Path,
    *,
    flutter: bool = False,
    outside_path: str | None = None,
) -> Path:
    root.mkdir(parents=True)
    dependencies = "  collection: ^1.18.0\n"
    if flutter:
        dependencies += "  flutter:\n    sdk: flutter\n  provider: ^6.1.0\n"
    if outside_path:
        dependencies += f"  local_demo:\n    path: {outside_path}\n"
    (root / "pubspec.yaml").write_text(
        "name: example_app\n"
        "version: 1.0.0\n"
        "environment:\n"
        "  sdk: '>=3.3.0 <4.0.0'\n"
        "dependencies:\n"
        f"{dependencies}"
        "dev_dependencies:\n"
        "  test: ^1.25.0\n",
        encoding="utf-8",
    )
    source = root / "lib" / "main.dart"
    source.parent.mkdir(parents=True)
    source.write_text("void main() { print('ok'); }\n", encoding="utf-8")
    test = root / "test" / "main_test.dart"
    test.parent.mkdir(parents=True)
    test.write_text("void main() {}\n", encoding="utf-8")
    if flutter:
        (root / ".metadata").write_text("version: 1.0.0\n", encoding="utf-8")
        (root / "android").mkdir()
    return root


def test_dart_router_clarifies_missing_path_and_routes_project() -> None:
    router = DeterministicRouter()

    missing = router.route("dart verify")
    flutter_missing = router.route("flutter verify")
    verify = router.route("verifica proyecto flutter /tmp/app")
    inspect = router.route("inspecciona proyecto dart /tmp/app")

    assert missing.kind == "clarification"
    assert flutter_missing.params["intended_skill"] == "dart.verify_project"
    assert verify.skill_name == "dart.verify_project"
    assert inspect.skill_name == "dart.project_inspect"


def test_dart_inspect_reads_pubspec_without_executing_tools(
    isolated_home: ElyndraPaths,
) -> None:
    project = _dart_project(Path.home() / "Proyectos" / "dart-inspect", flutter=True)
    marker = project / "executed"
    (project / "tool.dart").write_text(
        f"void main() {{ File('{marker}').writeAsStringSync('x'); }}\n",
        encoding="utf-8",
    )
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dart.project_inspect",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    inventory = result.data["inventory"]
    assert inventory["dart_files"] == 3
    assert inventory["test_files"] == 1
    assert inventory["pubspec"] is True
    assert inventory["package_name"] == "example_app"
    assert inventory["project_type"] == "flutter"
    assert "Flutter" in inventory["frameworks"]
    assert not marker.exists()


def test_dart_external_project_requires_one_time_authorization(
    isolated_home: ElyndraPaths,
) -> None:
    project = _dart_project(Path.home() / "Escritorio" / "dart-external")
    app = ElyndraApplication.load(isolated_home)

    denied = app.execute_skill(
        "dart.project_inspect",
        {"path": str(project)},
        approved=True,
    )
    allowed = app.execute_skill(
        "dart.project_inspect",
        {"path": str(project), "allow_root_once": True},
        approved=True,
    )

    assert denied.ok is False
    assert "--allow-root-once" in denied.message
    assert allowed.ok is True
    assert allowed.data["authorization_scope"] == "project_once"


def test_dart_descriptor_warns_about_external_path_dependency(
    isolated_home: ElyndraPaths,
) -> None:
    project = _dart_project(
        Path.home() / "Proyectos" / "dart-descriptor",
        outside_path="../../outside",
    )
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dart.descriptor_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert any(
        "sale del proyecto" in item for item in result.data["report"]["warnings"]
    )


def test_dart_descriptor_rejects_invalid_yaml(isolated_home: ElyndraPaths) -> None:
    project = _dart_project(Path.home() / "Proyectos" / "dart-invalid")
    (project / "pubspec.yaml").write_text("name: [broken\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dart.descriptor_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is False
    assert result.data["stage_status"] == "failed"
    assert result.data["report"]["errors"]


def test_dart_format_uses_no_output_and_never_rewrites(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _dart_project(Path.home() / "Proyectos" / "dart-format")
    source = project / "lib" / "main.dart"
    before = source.read_text(encoding="utf-8")
    log = Path.home() / "dart-format.log"
    _executable(
        project / ".venv" / "bin" / "dart",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dart.format_check",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    commands = log.read_text(encoding="utf-8")
    assert commands.count("format --output=none --set-exit-if-changed") == 2
    assert source.read_text(encoding="utf-8") == before
    assert result.data["modifies_files"] is False
    assert result.data["shell"] is False


def test_flutter_analyze_uses_no_pub_and_restricted_proxy(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _dart_project(Path.home() / "Proyectos" / "flutter-analyze", flutter=True)
    log = Path.home() / "flutter-analyze.log"
    _executable(
        project / ".venv" / "bin" / "flutter",
        f"#!/bin/sh\nprintf '%s|%s\\n' \"$*\" \"$HTTPS_PROXY\" > '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dart.analyze",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    command, proxy = log.read_text(encoding="utf-8").strip().split("|")
    assert command == "analyze --no-pub"
    assert proxy == "http://127.0.0.1:9"
    assert result.data["runner"] == "flutter"


def test_dart_tests_use_fixed_arguments(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _dart_project(Path.home() / "Proyectos" / "dart-tests")
    log = Path.home() / "dart-tests.log"
    _executable(
        project / ".venv" / "bin" / "dart",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dart.test_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert log.read_text(encoding="utf-8").strip() == "test --reporter compact"
    assert result.data["executes_project_code"] is True


def test_flutter_tests_use_no_pub(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _dart_project(Path.home() / "Proyectos" / "flutter-tests", flutter=True)
    log = Path.home() / "flutter-tests.log"
    _executable(
        project / ".venv" / "bin" / "flutter",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "flutter.test_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert log.read_text(encoding="utf-8").strip() == (
        "test --no-pub --reporter compact"
    )


def test_dart_verify_persists_partial_history_without_tools(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _dart_project(Path.home() / "Proyectos" / "dart-history")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dart.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    run = result.data["verification_run"]
    assert run["toolchain"] == "dart"
    assert run["status"] == "partial"
    stages = {item["name"]: item for item in result.data["stages"]}
    assert stages["inspect"]["status"] == "passed"
    assert stages["descriptor"]["status"] == "passed"
    assert stages["format"]["status"] == "unavailable"
    assert stages["analyze"]["status"] == "unavailable"
    assert stages["tests"]["status"] == "unavailable"


def test_dart_verify_passes_with_controlled_fake_tool(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _dart_project(Path.home() / "Proyectos" / "dart-verify")
    _executable(project / ".venv" / "bin" / "dart", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dart.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["verification_run"]["status"] == "passed"
    assert all(item["status"] == "passed" for item in result.data["stages"])


def test_dart_profile_and_control_center(isolated_home: ElyndraPaths) -> None:
    project = _dart_project(Path.home() / "Proyectos" / "dart-profile", flutter=True)
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    profile = service.save_dart_profile(
        {
            "project_root": str(project),
            "format_enabled": False,
            "test_runner": "flutter",
            "max_dart_files": 222,
        }
    )
    overview = service.control_overview()
    projects = service.control_projects()

    assert profile["format_enabled"] is False
    assert profile["test_runner"] == "flutter"
    assert profile["max_dart_files"] == 222
    assert ".dart_tool" in profile["exclude_paths"]
    assert overview["dart_profiles"] == 1
    assert "dart_verifications" in overview
    assert projects["dart_profiles"][0]["project_root"] == str(project)
    assert len(app.skills.list_all()) == 102
    with app.database.connect() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert version == "51"


def test_dart_profile_rejects_unsafe_exclusion(isolated_home: ElyndraPaths) -> None:
    project = _dart_project(Path.home() / "Proyectos" / "dart-profile-invalid")
    app = ElyndraApplication.load(isolated_home)

    with pytest.raises(ValueError, match="rutas relativas seguras"):
        app.dart_profiles.save(
            project,
            actor=app.identity.system_user,
            exclude_paths=["../outside"],
        )


def test_dart_knowledge_package_is_valid(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    package = Path(__file__).parents[1] / "knowledge-packs" / "dart-flutter-modern-basic"

    inspected = app.alexandria_packages.inspect(package)

    assert inspected["package_id"] == "programming.dart-flutter.modern-basic"
    assert inspected["domain"] == "programming/dart-flutter"
    assert inspected["source_count"] == 1


def test_dart_source_contains_no_pub_or_writer_commands() -> None:
    source = (
        Path(__file__).parents[1] / "src" / "elyndra" / "skills" / "dart_project.py"
    ).read_text(encoding="utf-8")

    forbidden_argv = (
        '[str(tool.path), "pub", "get"',
        '[str(tool.path), "pub", "upgrade"',
        '[str(tool.path), "run"',
        '[str(tool.path), "build"',
        '"--output=write"',
        "shell=True",
    )
    assert not any(item in source for item in forbidden_argv)
