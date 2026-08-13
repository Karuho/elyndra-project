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
  <artifactId>example-kotlin</artifactId>
  <version>1.0.0</version>
  <dependencies>
    <dependency>
      <groupId>io.ktor</groupId>
      <artifactId>ktor-server-core-jvm</artifactId>
      <version>3.0.0</version>
    </dependency>
  </dependencies>
</project>
""",
        encoding="utf-8",
    )
    source = root / "src" / "main" / "kotlin" / "example" / "App.kt"
    source.parent.mkdir(parents=True)
    source.write_text(
        "package example\nobject App { fun value(): Int = 1 }\n",
        encoding="utf-8",
    )
    test = root / "src" / "test" / "kotlin" / "example" / "AppTest.kt"
    test.parent.mkdir(parents=True)
    test.write_text("package example\nclass AppTest\n", encoding="utf-8")
    (root / "notes.kts").write_text("println(\"no ejecutar\")\n", encoding="utf-8")
    return root


def test_kotlin_router_clarifies_missing_path_and_routes_project() -> None:
    router = DeterministicRouter()

    missing = router.route("kotlin verify")
    verify = router.route("verifica proyecto Kotlin /tmp/app")
    inspect = router.route("inspecciona proyecto Kotlin /tmp/app")

    assert missing.kind == "clarification"
    assert missing.params["intended_skill"] == "kotlin.verify_project"
    assert verify.skill_name == "kotlin.verify_project"
    assert inspect.skill_name == "kotlin.project_inspect"


def test_kotlin_inspect_reports_metadata_without_executing_wrapper(
    isolated_home: ElyndraPaths,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "kotlin-inspect")
    wrapper = project / "mvnw"
    wrapper.write_text("#!/bin/sh\ntouch wrapper-ran\n", encoding="utf-8")
    wrapper.chmod(0o755)
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "kotlin.project_inspect",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    inventory = result.data["inventory"]
    assert inventory["artifact"] == "example-kotlin"
    assert inventory["build_tool"] == "maven"
    assert inventory["kotlin_files"] == 2
    assert inventory["kotlin_scripts"] == 1
    assert inventory["test_files"] == 1
    assert inventory["frameworks"] == ["Ktor"]
    assert inventory["wrappers_detected"] == ["mvnw"]
    assert inventory["wrappers_executed"] is False
    assert not (project / "wrapper-ran").exists()


def test_kotlin_external_project_requires_one_time_authorization(
    isolated_home: ElyndraPaths,
) -> None:
    project = _maven_project(Path.home() / "Escritorio" / "kotlin-external")
    app = ElyndraApplication.load(isolated_home)

    denied = app.execute_skill(
        "kotlin.project_inspect",
        {"path": str(project)},
        approved=True,
    )
    allowed = app.execute_skill(
        "kotlin.project_inspect",
        {"path": str(project), "allow_root_once": True},
        approved=True,
    )

    assert denied.ok is False
    assert "--allow-root-once" in denied.message
    assert allowed.ok is True
    assert allowed.data["authorization_scope"] == "project_once"


def test_kotlin_descriptor_validation_accepts_maven_project(
    isolated_home: ElyndraPaths,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "kotlin-descriptor")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "kotlin.descriptor_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["report"]["errors"] == []
    assert result.data["report"]["build_tool"] == "maven"


def test_kotlin_descriptor_validation_rejects_invalid_pom(
    isolated_home: ElyndraPaths,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "kotlin-invalid-pom")
    (project / "pom.xml").write_text("<project>", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "kotlin.descriptor_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is False
    assert "XML válido" in result.message


def test_kotlinc_uses_fixed_argv_and_excludes_kotlin_scripts(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "kotlin-kotlinc")
    log = Path.home() / "kotlinc.log"
    _executable(
        project / ".venv" / "bin" / "kotlinc",
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" > '{log}'\n"
        "for arg in \"$@\"; do\n"
        "  case \"$arg\" in\n"
        f"    @*) /bin/cat \"${{arg#@}}\" >> '{log}' ;;\n"
        "  esac\n"
        "done\n"
        "exit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "kotlin.kotlinc_compile",
        {"path": str(project), "jvm_target": 17},
        approved=True,
    )

    assert result.ok is True
    assert result.data["tool_source"] == "project_local"
    assert result.data["shell"] is False
    command = log.read_text(encoding="utf-8")
    assert "-jvm-target 17" in command
    assert "App.kt" in command
    assert "AppTest.kt" in command
    assert "notes.kts" not in command
    assert "-P" not in command
    assert "-Xplugin" not in command


def test_kotlin_maven_build_is_offline_and_does_not_use_wrapper(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "kotlin-maven")
    log = Path.home() / "maven.log"
    _executable(
        project / ".venv" / "bin" / "mvn",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n",
    )
    _executable(project / "mvnw", "#!/bin/sh\nexit 77\n")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "kotlin.build_project",
        {"path": str(project), "build_tool": "maven"},
        approved=True,
    )

    assert result.ok is True
    assert log.read_text(encoding="utf-8").strip() == (
        "--offline --batch-mode --no-transfer-progress compile"
    )
    assert "mvnw" not in json.dumps(result.data)


def test_kotlin_gradle_tests_use_fixed_offline_arguments(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = Path.home() / "Proyectos" / "kotlin-gradle"
    project.mkdir(parents=True)
    (project / "build.gradle.kts").write_text(
        'plugins { kotlin("jvm") version "2.0.0" }\n',
        encoding="utf-8",
    )
    source = project / "src" / "main" / "kotlin" / "App.kt"
    source.parent.mkdir(parents=True)
    source.write_text("object App\n", encoding="utf-8")
    log = Path.home() / "gradle.log"
    _executable(
        project / ".venv" / "bin" / "gradle",
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n",
    )
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "kotlin.test_project",
        {"path": str(project), "build_tool": "gradle"},
        approved=True,
    )

    assert result.ok is True
    assert log.read_text(encoding="utf-8").strip() == (
        "--offline --no-daemon --console=plain test"
    )


def test_kotlin_profile_persists_safe_settings(isolated_home: ElyndraPaths) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "kotlin-profile")
    app = ElyndraApplication.load(isolated_home)

    profile = app.kotlin_profiles.save(
        project,
        actor=app.identity.system_user,
        build_tool="maven",
        jvm_target=21,
        fail_fast=True,
        require_tools=True,
        max_kotlin_files=123,
        exclude_paths=["target", "generated"],
    )

    assert profile["build_tool"] == "maven"
    assert profile["jvm_target"] == 21
    assert profile["fail_fast"] is True
    assert profile["require_tools"] is True
    assert profile["max_kotlin_files"] == 123
    assert profile["exclude_paths"] == ["target", "generated"]


def test_kotlin_profile_rejects_absolute_exclusion(
    isolated_home: ElyndraPaths,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "kotlin-bad-profile")
    app = ElyndraApplication.load(isolated_home)

    with pytest.raises(ValueError, match="relativas"):
        app.kotlin_profiles.save(
            project,
            actor=app.identity.system_user,
            exclude_paths=[str(Path.home())],
        )


def test_kotlin_verify_uses_managed_build_and_persists_partial_history(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "kotlin-history")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "kotlin.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    run = result.data["verification_run"]
    assert run["toolchain"] == "kotlin"
    assert run["status"] == "partial"
    stages = {item["name"]: item for item in result.data["stages"]}
    assert stages["inspect"]["status"] == "passed"
    assert stages["descriptor"]["status"] == "passed"
    assert stages["kotlinc"]["status"] == "skipped"
    assert "classpath" in stages["kotlinc"]["message"]
    assert stages["build"]["status"] == "unavailable"


def test_kotlin_control_center_schema_and_registry(
    isolated_home: ElyndraPaths,
) -> None:
    project = _maven_project(Path.home() / "Proyectos" / "kotlin-control")
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    profile = service.save_kotlin_profile(
        {
            "project_root": str(project),
            "build_tool": "maven",
            "jvm_target": 17,
        }
    )
    overview = service.control_overview()
    projects = service.control_projects()

    assert profile["jvm_target"] == 17
    assert overview["kotlin_profiles"] == 1
    assert "kotlin_verifications" in overview
    assert projects["kotlin_profiles"][0]["build_tool"] == "maven"
    assert len(app.skills.list_all()) == 102
    with app.database.connect() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert version == "50"


def test_kotlin_knowledge_package_and_security_contract(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    root = Path(__file__).parents[1]
    package = root / "knowledge-packs" / "kotlin-modern-basic"

    inspected = app.alexandria_packages.inspect(package)
    source = (root / "src" / "elyndra" / "skills" / "kotlin_project.py").read_text(
        encoding="utf-8"
    )

    assert inspected["package_id"] == "programming.kotlin.modern-basic"
    assert inspected["domain"] == "programming/kotlin"
    assert inspected["source_count"] == 1
    assert "shell=True" not in source
    assert "mvnw" not in source.split("_execute_build_stage", 1)[1].split(
        "def _build_argv", 1
    )[0]
    assert "gradlew" not in source.split("_execute_build_stage", 1)[1].split(
        "def _build_argv", 1
    )[0]
