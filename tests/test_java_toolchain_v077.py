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


def _maven_project(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "pom.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>cl.elyndra</groupId>
  <artifactId>example-java</artifactId>
  <version>1.0.0</version>
  <dependencies>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>5.11.0</version>
    </dependency>
  </dependencies>
</project>
""",
        encoding="utf-8",
    )
    source = root / "src" / "main" / "java" / "example" / "App.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "package example; public final class App { public static int value() { return 1; } }\n",
        encoding="utf-8",
    )
    test = root / "src" / "test" / "java" / "example" / "AppTest.java"
    test.parent.mkdir(parents=True)
    test.write_text(
        "package example; public final class AppTest { }\n",
        encoding="utf-8",
    )
    return root


def test_java_router_clarifies_missing_path_and_routes_project() -> None:
    router = DeterministicRouter()

    missing = router.route("java verify")
    verify = router.route("verifica proyecto Java /tmp/app")
    inspect = router.route("inspecciona proyecto Java /tmp/app")

    assert missing.kind == "clarification"
    assert missing.params["intended_skill"] == "java.verify_project"
    assert verify.skill_name == "java.verify_project"
    assert inspect.skill_name == "java.project_inspect"


def test_java_inspect_reports_metadata_without_executing_wrapper(
    isolated_home: ElyndraPaths,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "java-inspect")
    wrapper = project / "mvnw"
    wrapper.write_text("#!/bin/sh\ntouch wrapper-ran\n", encoding="utf-8")
    wrapper.chmod(0o755)
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "java.project_inspect",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    inventory = result.data["inventory"]
    assert inventory["artifact"] == "example-java"
    assert inventory["build_tool"] == "maven"
    assert inventory["java_files"] == 2
    assert inventory["test_files"] == 1
    assert inventory["wrappers_detected"] == ["mvnw"]
    assert inventory["wrappers_executed"] is False
    assert not (project / "wrapper-ran").exists()


def test_java_external_project_requires_one_time_authorization(
    isolated_home: ElyndraPaths,
) -> None:
    project = _maven_project(Path.home() / "Escritorio" / "java-external")
    app = ElyndraApplication.load(isolated_home)

    denied = app.execute_skill(
        "java.project_inspect",
        {"path": str(project)},
        approved=True,
    )
    allowed = app.execute_skill(
        "java.project_inspect",
        {"path": str(project), "allow_root_once": True},
        approved=True,
    )

    assert denied.ok is False
    assert "--allow-root-once" in denied.message
    assert allowed.ok is True
    assert allowed.data["authorization_scope"] == "project_once"


def test_java_descriptor_validation_accepts_maven_project(
    isolated_home: ElyndraPaths,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "java-descriptor")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "java.descriptor_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["report"]["errors"] == []
    assert result.data["report"]["build_tool"] == "maven"


def test_java_descriptor_validation_rejects_invalid_pom(
    isolated_home: ElyndraPaths,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "java-invalid-pom")
    (project / "pom.xml").write_text("<project>", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "java.descriptor_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is False
    assert "XML válido" in result.message


def test_javac_uses_fixed_argv_and_never_executes_annotations(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "java-javac")
    log = Path.home() / "javac.log"
    _executable(
        project / ".venv" / "bin" / "javac",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "java.javac_compile",
        {"path": str(project), "java_release": 17},
        approved=True,
    )

    assert result.ok is True
    assert result.data["tool_source"] == "project_local"
    assert result.data["shell"] is False
    command = log.read_text(encoding="utf-8")
    assert "-proc:none" in command
    assert "--release 17" in command
    assert "@" in command


def test_maven_build_is_offline_and_does_not_use_wrapper(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "java-maven")
    log = Path.home() / "maven.log"
    _executable(
        project / ".venv" / "bin" / "mvn",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n",
    )
    _executable(
        project / "mvnw",
        "#!/bin/sh\nexit 77\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "java.build_project",
        {"path": str(project), "build_tool": "maven"},
        approved=True,
    )

    assert result.ok is True
    assert result.data["shell"] is False
    assert log.read_text(encoding="utf-8").strip() == (
        "--offline --batch-mode --no-transfer-progress compile"
    )
    assert "mvnw" not in json.dumps(result.data)


def test_gradle_tests_use_fixed_offline_arguments(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = Path.home() / "Proyectos" / "java-gradle"
    project.mkdir(parents=True)
    (project / "build.gradle.kts").write_text("plugins { java }\n", encoding="utf-8")
    source = project / "src" / "main" / "java" / "App.java"
    source.parent.mkdir(parents=True)
    source.write_text("final class App {}\n", encoding="utf-8")
    log = Path.home() / "gradle.log"
    _executable(
        project / ".venv" / "bin" / "gradle",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "java.test_project",
        {"path": str(project), "build_tool": "gradle"},
        approved=True,
    )

    assert result.ok is True
    assert log.read_text(encoding="utf-8").strip() == (
        "--offline --no-daemon --console=plain test"
    )


def test_java_profile_persists_safe_settings(isolated_home: ElyndraPaths) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "java-profile")
    app = ElyndraApplication.load(isolated_home)

    profile = app.java_profiles.save(
        project,
        actor=app.identity.system_user,
        build_tool="maven",
        java_release=21,
        fail_fast=True,
        require_tools=True,
        max_java_files=123,
        exclude_paths=["target", "generated"],
    )

    assert profile["build_tool"] == "maven"
    assert profile["java_release"] == 21
    assert profile["fail_fast"] is True
    assert profile["require_tools"] is True
    assert profile["max_java_files"] == 123
    assert profile["exclude_paths"] == ["target", "generated"]


def test_java_profile_rejects_absolute_exclusion(isolated_home: ElyndraPaths) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "java-bad-profile")
    app = ElyndraApplication.load(isolated_home)

    with pytest.raises(ValueError, match="relativas"):
        app.java_profiles.save(
            project,
            actor=app.identity.system_user,
            exclude_paths=[str(Path.home())],
        )


def test_java_verify_persists_partial_history_without_tools(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "java-history")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "java.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    run = result.data["verification_run"]
    assert run["toolchain"] == "java"
    assert run["status"] == "partial"
    saved = app.verification_runs.get(run["public_id"])
    assert saved is not None
    assert saved["summary"]["stages"][0]["name"] == "inspect"


def test_java_verify_can_require_missing_tools(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "java-required")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "java.verify_project",
        {"path": str(project), "require_tools": True},
        approved=True,
    )

    assert result.ok is False
    assert result.data["verification_run"]["status"] == "failed"


def test_control_center_exposes_java_profiles_and_runs(
    isolated_home: ElyndraPaths,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "java-control")
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    profile = service.save_java_profile(
        {
            "project_root": str(project),
            "build_tool": "maven",
            "java_release": 17,
        }
    )

    overview = service.control_overview()
    projects = service.control_projects()

    assert profile["build_tool"] == "maven"
    assert overview["java_profiles"] == 1
    assert "java_verifications" in overview
    assert projects["java_profiles"][0]["java_release"] == 17


def test_java_knowledge_package_is_valid(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    package = Path(__file__).parents[1] / "knowledge-packs" / "java-modern-basic"

    inspected = app.alexandria_packages.inspect(package)

    assert inspected["package_id"] == "programming.java.modern-basic"
    assert inspected["domain"] == "programming/java"
    assert inspected["source_count"] == 1
