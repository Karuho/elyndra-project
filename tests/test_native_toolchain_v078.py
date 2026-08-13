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


def _cmake_project(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "CMakeLists.txt").write_text(
        """cmake_minimum_required(VERSION 3.16)
project(example LANGUAGES C CXX)
enable_testing()
add_executable(example src/main.cpp src/helper.c)
add_test(NAME example-runs COMMAND example)
""",
        encoding="utf-8",
    )
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text(
        "int helper(void); int main() { return helper(); }\n",
        encoding="utf-8",
    )
    (root / "src" / "helper.c").write_text(
        "int helper(void) { return 0; }\n",
        encoding="utf-8",
    )
    (root / "include").mkdir()
    (root / "include" / "example.hpp").write_text("#pragma once\n", encoding="utf-8")
    return root


def _direct_c_project(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    return root


def test_native_router_clarifies_missing_path_and_routes_project() -> None:
    router = DeterministicRouter()

    missing = router.route("c++ verify")
    verify = router.route("verifica proyecto C++ /tmp/native")
    inspect = router.route("inspecciona proyecto C /tmp/native")

    assert missing.kind == "clarification"
    assert missing.params["intended_skill"] == "native.verify_project"
    assert verify.skill_name == "native.verify_project"
    assert inspect.skill_name == "native.project_inspect"


def test_native_inspect_reports_languages_without_execution(
    isolated_home: ElyndraPaths,
) -> None:
    project = _cmake_project(Path.home() / "Proyectos" / "native-inspect")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "native.project_inspect",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    inventory = result.data["inventory"]
    assert inventory["c_files"] == 1
    assert inventory["cpp_files"] == 1
    assert inventory["headers"] == 1
    assert inventory["build_tool"] == "cmake"
    assert inventory["languages"] == ["C", "C++"]


def test_native_external_project_requires_one_time_authorization(
    isolated_home: ElyndraPaths,
) -> None:
    project = _direct_c_project(Path.home() / "Escritorio" / "native-external")
    app = ElyndraApplication.load(isolated_home)

    denied = app.execute_skill(
        "native.project_inspect",
        {"path": str(project)},
        approved=True,
    )
    allowed = app.execute_skill(
        "native.project_inspect",
        {"path": str(project), "allow_root_once": True},
        approved=True,
    )

    assert denied.ok is False
    assert "--allow-root-once" in denied.message
    assert allowed.ok is True
    assert allowed.data["authorization_scope"] == "project_once"


def test_native_descriptor_reports_sensitive_cmake_without_executing_it(
    isolated_home: ElyndraPaths,
) -> None:
    project = _cmake_project(Path.home() / "Proyectos" / "native-sensitive")
    with (project / "CMakeLists.txt").open("a", encoding="utf-8") as handle:
        handle.write('execute_process(COMMAND echo "not executed")\n')
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "native.descriptor_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert "execute_process" in result.data["report"]["dangerous_cmake_features"]
    assert "efectos externos" in result.message


def test_c_syntax_uses_fixed_arguments_and_never_uses_shell(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _direct_c_project(Path.home() / "Proyectos" / "native-c-syntax")
    log = Path.home() / "gcc.log"
    _executable(
        project / ".venv" / "bin" / "gcc",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "native.c_syntax_check",
        {"path": str(project), "c_standard": "c17"},
        approved=True,
    )

    assert result.ok is True
    assert result.data["tool_source"] == "project_local"
    assert result.data["shell"] is False
    command = log.read_text(encoding="utf-8")
    assert "-fsyntax-only" in command
    assert "-std=c17" in command
    assert "@" in command


def test_cmake_build_uses_temp_directory_and_fixed_arguments(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _cmake_project(Path.home() / "Proyectos" / "native-cmake")
    log = Path.home() / "cmake.log"
    _executable(
        project / ".venv" / "bin" / "cmake",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "native.build_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["shell"] is False
    commands = log.read_text(encoding="utf-8")
    assert "-S" in commands
    assert "-B" in commands
    assert "FETCHCONTENT_FULLY_DISCONNECTED=ON" in commands
    assert "--build" in commands
    assert str(project / "build") not in commands


def test_native_verify_skips_raw_syntax_for_cmake_and_passes_with_tools(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _cmake_project(Path.home() / "Proyectos" / "native-verify")
    for name in ("cmake", "ctest", "cppcheck"):
        _executable(project / ".venv" / "bin" / name, "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "native.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["verification_run"]["status"] == "passed"
    stages = {item["name"]: item for item in result.data["stages"]}
    assert stages["c_syntax"]["status"] == "skipped"
    assert stages["cpp_syntax"]["status"] == "skipped"
    assert "CMake administra" in stages["cpp_syntax"]["message"]
    assert stages["build"]["status"] == "passed"
    assert stages["tests"]["status"] == "passed"


def test_native_profile_persists_safe_settings(isolated_home: ElyndraPaths) -> None:
    project = _cmake_project(Path.home() / "Proyectos" / "native-profile")
    app = ElyndraApplication.load(isolated_home)

    profile = app.native_profiles.save(
        project,
        actor=app.identity.system_user,
        compiler="clang",
        c_standard="c17",
        cpp_standard="c++23",
        fail_fast=True,
        require_tools=True,
        max_native_files=321,
        exclude_paths=["build", "generated"],
    )

    assert profile["compiler"] == "clang"
    assert profile["cpp_standard"] == "c++23"
    assert profile["fail_fast"] is True
    assert profile["require_tools"] is True
    assert profile["max_native_files"] == 321
    assert profile["exclude_paths"] == ["build", "generated"]


def test_control_center_exposes_native_profiles_and_runs(
    isolated_home: ElyndraPaths,
) -> None:
    project = _cmake_project(Path.home() / "Proyectos" / "native-control")
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    profile = service.save_native_profile(
        {
            "project_root": str(project),
            "compiler": "gcc",
            "cpp_standard": "c++20",
        }
    )

    overview = service.control_overview()
    projects = service.control_projects()

    assert profile["compiler"] == "gcc"
    assert overview["native_profiles"] == 1
    assert "native_verifications" in overview
    assert projects["native_profiles"][0]["cpp_standard"] == "c++20"


def test_native_knowledge_package_is_valid(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    package = Path(__file__).parents[1] / "knowledge-packs" / "c-cpp-modern-basic"

    inspected = app.alexandria_packages.inspect(package)

    assert inspected["package_id"] == "programming.c-cpp.modern-basic"
    assert inspected["domain"] == "programming/c-cpp"
    assert inspected["source_count"] == 1


def test_java_verify_skips_raw_javac_for_managed_maven_project(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = Path.home() / "Proyectos" / "minecraft-plugin"
    source = project / "src" / "main" / "java" / "example" / "Plugin.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "package example; import org.bukkit.plugin.java.JavaPlugin; "
        "public final class Plugin extends JavaPlugin {}\n",
        encoding="utf-8",
    )
    (project / "pom.xml").write_text(
        """<project xmlns="http://maven.apache.org/POM/4.0.0">
<modelVersion>4.0.0</modelVersion><groupId>example</groupId>
<artifactId>plugin</artifactId><version>1.0.0</version></project>
""",
        encoding="utf-8",
    )
    _executable(project / ".venv" / "bin" / "javac", "#!/bin/sh\nexit 1\n")
    _executable(project / ".venv" / "bin" / "mvn", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "java.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["verification_run"]["status"] == "passed"
    stages = {item["name"]: item for item in result.data["stages"]}
    assert stages["javac"]["status"] == "skipped"
    assert "classpath" in stages["javac"]["message"]
    assert stages["build"]["status"] == "passed"
    assert stages["tests"]["status"] == "passed"
