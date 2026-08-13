from __future__ import annotations

import json
from pathlib import Path

import pytest

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths
from elyndra.router import DeterministicRouter
from elyndra.skills.tool_resolution import resolve_project_tool
from elyndra.web.server import ElyndraWebService


def _executable(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _swift_project(
    root: Path,
    *,
    remote_dependency: bool = False,
    resolved: bool = True,
    plugins: bool = False,
) -> Path:
    root.mkdir(parents=True)
    dependency = ""
    target_dependencies = ""
    if remote_dependency:
        dependency = (
            '    dependencies: [.package(url: "https://example.invalid/demo.git", '
            'from: "1.0.0")],\n'
        )
        target_dependencies = 'dependencies: [.product(name: "Demo", package: "demo")])'
    else:
        target_dependencies = "dependencies: [])"
    plugin_target = (
        '        .plugin(name: "BuildPlugin", capability: .buildTool()),\n'
        if plugins
        else ""
    )
    (root / "Package.swift").write_text(
        "// swift-tools-version: 5.9\n"
        "import PackageDescription\n\n"
        "let package = Package(\n"
        '    name: "Example",\n'
        '    products: [.library(name: "Example", targets: ["Example"])],\n'
        f"{dependency}"
        "    targets: [\n"
        f'        .target(name: "Example", {target_dependencies},\n'
        '        .testTarget(name: "ExampleTests", dependencies: ["Example"]),\n'
        f"{plugin_target}"
        "    ]\n"
        ")\n",
        encoding="utf-8",
    )
    source = root / "Sources" / "Example" / "Example.swift"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import Foundation\n\npublic func answer() -> Int { 42 }\n",
        encoding="utf-8",
    )
    test = root / "Tests" / "ExampleTests" / "ExampleTests.swift"
    test.parent.mkdir(parents=True)
    test.write_text(
        "import XCTest\n@testable import Example\nfinal class ExampleTests: XCTestCase {}\n",
        encoding="utf-8",
    )
    if remote_dependency and resolved:
        (root / "Package.resolved").write_text(
            json.dumps({"version": 2, "pins": []}),
            encoding="utf-8",
        )
    return root


def test_global_tool_resolution_preserves_swift_symlink_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    binary_dir = tmp_path / "bin"
    driver = _executable(binary_dir / "swift-driver", "#!/bin/sh\nexit 0\n")
    (binary_dir / "swift").symlink_to(driver.name)
    monkeypatch.setenv("PATH", str(binary_dir))

    resolution = resolve_project_tool(project, "swift")

    assert resolution.path == binary_dir / "swift"
    assert resolution.source == "global_path"


def test_swift_router_clarifies_missing_path_and_routes_project() -> None:
    router = DeterministicRouter()

    missing = router.route("swift verify")
    verify = router.route("verifica proyecto swift /tmp/app")
    inspect = router.route("inspecciona proyecto swift /tmp/app")

    assert missing.kind == "clarification"
    assert missing.params["intended_skill"] == "swift.verify_project"
    assert verify.skill_name == "swift.verify_project"
    assert inspect.skill_name == "swift.project_inspect"


def test_swift_inspect_reads_metadata_without_executing_manifest(
    isolated_home: ElyndraPaths,
) -> None:
    project = _swift_project(
        Path.home() / "Proyectos" / "swift-inspect",
        remote_dependency=True,
        plugins=True,
    )
    marker = project / "manifest-executed"
    with (project / "Package.swift").open("a", encoding="utf-8") as handle:
        handle.write(f'\n// FileManager.default.createFile(atPath: "{marker}", contents: nil)\n')
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "swift.project_inspect",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    inventory = result.data["inventory"]
    assert inventory["swift_files"] == 2
    assert inventory["test_files"] == 1
    assert inventory["manifest"] is True
    assert inventory["package_name"] == "Example"
    assert inventory["tools_version"] == "5.9"
    assert inventory["remote_dependencies"] == 1
    assert inventory["plugins_detected"] is True
    assert inventory["manifest_executed"] is False
    assert not marker.exists()


def test_swift_external_project_requires_one_time_authorization(
    isolated_home: ElyndraPaths,
) -> None:
    project = _swift_project(Path.home() / "Escritorio" / "swift-external")
    app = ElyndraApplication.load(isolated_home)

    denied = app.execute_skill(
        "swift.project_inspect",
        {"path": str(project)},
        approved=True,
    )
    allowed = app.execute_skill(
        "swift.project_inspect",
        {"path": str(project), "allow_root_once": True},
        approved=True,
    )

    assert denied.ok is False
    assert "--allow-root-once" in denied.message
    assert allowed.ok is True
    assert allowed.data["authorization_scope"] == "project_once"


def test_swift_manifest_validation_warns_about_plugins_and_remote_dependencies(
    isolated_home: ElyndraPaths,
) -> None:
    project = _swift_project(
        Path.home() / "Proyectos" / "swift-manifest",
        remote_dependency=True,
        resolved=False,
        plugins=True,
    )
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "swift.manifest_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    warnings = result.data["report"]["warnings"]
    assert any("plugins" in item for item in warnings)
    assert any("Package.resolved" in item for item in warnings)


def test_swift_manifest_validation_rejects_unbalanced_manifest(
    isolated_home: ElyndraPaths,
) -> None:
    project = _swift_project(Path.home() / "Proyectos" / "swift-invalid")
    (project / "Package.swift").write_text(
        "// swift-tools-version: 5.9\nlet package = Package(name: \"Broken\"\n",
        encoding="utf-8",
    )
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "swift.manifest_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is False
    assert result.data["stage_status"] == "failed"
    assert any("delimitador" in item.casefold() for item in result.data["report"]["errors"])


def test_swift_syntax_uses_parse_without_generating_artifacts(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _swift_project(Path.home() / "Proyectos" / "swift-syntax")
    log = Path.home() / "swiftc.log"
    _executable(
        project / ".venv" / "bin" / "swiftc",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "swift.syntax_check",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    commands = log.read_text(encoding="utf-8")
    assert commands.count("-parse") == 2
    assert "-emit" not in commands
    assert not list(project.rglob("*.o"))
    assert not list(project.rglob("*.swiftmodule"))
    assert result.data["shell"] is False


def test_swift_format_uses_lint_strict_without_rewriting(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _swift_project(Path.home() / "Proyectos" / "swift-format")
    source = project / "Sources" / "Example" / "Example.swift"
    before = source.read_text(encoding="utf-8")
    log = Path.home() / "swift-format.log"
    _executable(
        project / ".venv" / "bin" / "swift-format",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "swift.format_check",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    commands = log.read_text(encoding="utf-8")
    assert commands.count("lint --strict") == 2
    assert not any(line.startswith("format ") for line in commands.splitlines())
    assert "--in-place" not in commands
    assert source.read_text(encoding="utf-8") == before


def test_swift_build_uses_fixed_resolution_and_external_scratch(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _swift_project(Path.home() / "Proyectos" / "swift-build")
    log = Path.home() / "swift-build.log"
    _executable(
        project / ".venv" / "bin" / "swift",
        "#!/bin/sh\n"
        f"printf '%s|%s|%s|%s\\n' \"$*\" \"$HOME\" \"$HTTPS_PROXY\" "
        f"\"$SWIFTPM_MODULECACHE_OVERRIDE\" > '{log}'\n"
        "exit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "swift.build_project",
        {"path": str(project), "configuration": "release"},
        approved=True,
    )

    assert result.ok is True
    command, home, proxy, module_cache = log.read_text(encoding="utf-8").strip().split("|")
    assert command.startswith("build --package-path ")
    assert "--disable-automatic-resolution" in command
    assert "--scratch-path" in command
    assert "--configuration release" in command
    assert proxy == "http://127.0.0.1:9"
    assert not Path(home).exists()
    assert not Path(module_cache).exists()
    assert not (project / ".build").exists()
    assert result.data["artifacts_external"] is True
    assert result.data["sandboxed"] is False


def test_swift_tests_use_fixed_swiftpm_arguments(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _swift_project(Path.home() / "Proyectos" / "swift-tests")
    log = Path.home() / "swift-tests.log"
    _executable(
        project / ".venv" / "bin" / "swift",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "swift.test_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    command = log.read_text(encoding="utf-8")
    assert command.startswith("test --package-path ")
    assert "--disable-automatic-resolution" in command
    forbidden = (" update ", " resolve ", " package ", " run ")
    assert not any(item in f" {command} " for item in forbidden)


def test_swiftpm_stages_block_remote_dependencies_without_lockfile(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _swift_project(
        Path.home() / "Proyectos" / "swift-no-lock",
        remote_dependency=True,
        resolved=False,
    )
    marker = project / "swift-ran"
    _executable(
        project / ".venv" / "bin" / "swift",
        f"#!/bin/sh\ntouch '{marker}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "swift.build_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is False
    assert result.data["stage_status"] == "unavailable"
    assert "Package.resolved no existe" in result.message
    assert not marker.exists()


def test_swift_verify_persists_partial_history_without_tools(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _swift_project(Path.home() / "Proyectos" / "swift-history")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "swift.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    run = result.data["verification_run"]
    assert run["toolchain"] == "swift"
    assert run["status"] == "partial"
    stages = {item["name"]: item for item in result.data["stages"]}
    assert stages["inspect"]["status"] == "passed"
    assert stages["manifest"]["status"] == "passed"
    assert stages["syntax"]["status"] == "unavailable"
    assert stages["format"]["status"] == "unavailable"
    assert stages["build"]["status"] == "unavailable"


def test_swift_verify_passes_with_controlled_fake_tools(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _swift_project(Path.home() / "Proyectos" / "swift-verify")
    for tool in ("swift", "swiftc", "swift-format"):
        _executable(project / ".venv" / "bin" / tool, "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "swift.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["verification_run"]["status"] == "passed"
    assert all(item["status"] == "passed" for item in result.data["stages"])


def test_swift_profile_and_control_center(isolated_home: ElyndraPaths) -> None:
    project = _swift_project(Path.home() / "Proyectos" / "swift-profile")
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    profile = service.save_swift_profile(
        {
            "project_root": str(project),
            "format_enabled": False,
            "configuration": "release",
            "max_swift_files": 222,
        }
    )
    overview = service.control_overview()
    projects = service.control_projects()

    assert profile["format_enabled"] is False
    assert profile["configuration"] == "release"
    assert profile["max_swift_files"] == 222
    assert ".build" in profile["exclude_paths"]
    assert overview["swift_profiles"] == 1
    assert "swift_verifications" in overview
    assert projects["swift_profiles"][0]["project_root"] == str(project)
    assert len(app.skills.list_all()) == 102
    with app.database.connect() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert version == "50"


def test_swift_profile_rejects_unsafe_exclusion(isolated_home: ElyndraPaths) -> None:
    project = _swift_project(Path.home() / "Proyectos" / "swift-profile-invalid")
    app = ElyndraApplication.load(isolated_home)

    with pytest.raises(ValueError, match="rutas relativas seguras"):
        app.swift_profiles.save(
            project,
            actor=app.identity.system_user,
            exclude_paths=["../outside"],
        )


def test_swift_knowledge_package_is_valid(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    package = Path(__file__).parents[1] / "knowledge-packs" / "swift-modern-basic"

    inspected = app.alexandria_packages.inspect(package)

    assert inspected["package_id"] == "programming.swift.modern-basic"
    assert inspected["domain"] == "programming/swift"
    assert inspected["source_count"] == 1


def test_swift_source_contains_no_update_resolve_or_writer_commands() -> None:
    source = (
        Path(__file__).parents[1] / "src" / "elyndra" / "skills" / "swift_project.py"
    ).read_text(encoding="utf-8")

    forbidden_argv = (
        '[str(tool.path), "package", "update"',
        '[str(tool.path), "package", "resolve"',
        '[str(tool.path), "run"',
        '"--in-place"',
        '"format", str(path)',
        "shell=True",
    )
    assert not any(item in source for item in forbidden_argv)
