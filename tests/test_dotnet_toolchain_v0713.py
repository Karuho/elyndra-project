from __future__ import annotations

import json
import threading
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths
from elyndra.router import DeterministicRouter
from elyndra.web.server import (
    ElyndraWebService,
    _handler_factory,
    _LocalThreadingHTTPServer,
)


def _executable(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _dotnet_project(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "Example.csproj").write_text(
        """<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.EntityFrameworkCore" Version="8.0.0" />
    <PackageReference Include="xunit" Version="2.6.0" />
  </ItemGroup>
</Project>
""",
        encoding="utf-8",
    )
    source = root / "src" / "Program.cs"
    source.parent.mkdir(parents=True)
    source.write_text("namespace Example; public static class Program { }\n", encoding="utf-8")
    test = root / "tests" / "ProgramTests.cs"
    test.parent.mkdir(parents=True)
    test.write_text("namespace Example.Tests; public class ProgramTests { }\n", encoding="utf-8")
    (root / "Example.sln").write_text(
        "Microsoft Visual Studio Solution File, Format Version 12.00\n",
        encoding="utf-8",
    )
    return root


def _fake_dotnet(project: Path, log: Path, *, version: str = "8.0.100") -> Path:
    return _executable(
        project / ".venv" / "bin" / "dotnet",
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then\n"
        f"  printf '%s\\n' '{version}'\n"
        "  exit 0\n"
        "fi\n"
        f"printf '%s\\n' \"$*\" >> '{log}'\n"
        f"printf 'HOME=%s\\n' \"$DOTNET_CLI_HOME\" >> '{log}'\n"
        f"printf 'PROXY=%s\\n' \"$HTTPS_PROXY\" >> '{log}'\n"
        "exit 0\n",
    )


def test_dotnet_router_clarifies_missing_path_and_routes_project() -> None:
    router = DeterministicRouter()

    missing = router.route("dotnet verify")
    verify = router.route("verifica proyecto C# /tmp/app")
    inspect = router.route("inspecciona proyecto .NET /tmp/app")

    assert missing.kind == "clarification"
    assert missing.params["intended_skill"] == "dotnet.verify_project"
    assert verify.skill_name == "dotnet.verify_project"
    assert inspect.skill_name == "dotnet.project_inspect"


def test_dotnet_inspect_reports_metadata_without_executing_msbuild(
    isolated_home: ElyndraPaths,
) -> None:
    project = _dotnet_project(Path.home() / "Proyectos" / "dotnet-inspect")
    (project / "Directory.Build.targets").write_text(
        '<Project><Target Name="Danger"><Exec Command="touch should-not-run" /></Target></Project>',
        encoding="utf-8",
    )
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dotnet.project_inspect",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    inventory = result.data["inventory"]
    assert inventory["csharp_files"] == 2
    assert inventory["test_files"] == 1
    assert inventory["project_files"] == 1
    assert inventory["solutions"] == 1
    assert inventory["target_frameworks"] == ["net8.0"]
    assert inventory["frameworks"] == ["ASP.NET Core", "Entity Framework Core", "xUnit"]
    assert inventory["directory_build_targets"] is True
    assert inventory["restore_executed"] is False
    assert not (project / "should-not-run").exists()


def test_dotnet_external_project_requires_one_time_authorization(
    isolated_home: ElyndraPaths,
) -> None:
    project = _dotnet_project(Path.home() / "Escritorio" / "dotnet-external")
    app = ElyndraApplication.load(isolated_home)

    denied = app.execute_skill(
        "dotnet.project_inspect",
        {"path": str(project)},
        approved=True,
    )
    allowed = app.execute_skill(
        "dotnet.project_inspect",
        {"path": str(project), "allow_root_once": True},
        approved=True,
    )

    assert denied.ok is False
    assert "--allow-root-once" in denied.message
    assert allowed.ok is True
    assert allowed.data["authorization_scope"] == "project_once"


def test_dotnet_descriptor_validation_accepts_project_and_warns_about_targets(
    isolated_home: ElyndraPaths,
) -> None:
    project = _dotnet_project(Path.home() / "Proyectos" / "dotnet-descriptor")
    (project / "Directory.Build.targets").write_text("<Project />\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dotnet.descriptor_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["report"]["errors"] == []
    assert any("Directory.Build.targets" in item for item in result.data["report"]["warnings"])


def test_dotnet_descriptor_validation_rejects_invalid_project_xml(
    isolated_home: ElyndraPaths,
) -> None:
    project = _dotnet_project(Path.home() / "Proyectos" / "dotnet-invalid")
    (project / "Example.csproj").write_text("<Project>", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dotnet.descriptor_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is False
    assert "XML válido" in result.message


def test_dotnet_format_uses_verify_no_changes_and_no_restore(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _dotnet_project(Path.home() / "Proyectos" / "dotnet-format")
    log = Path.home() / "dotnet-format.log"
    _fake_dotnet(project, log)
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dotnet.format_check",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    command = log.read_text(encoding="utf-8")
    assert "format" in command
    assert "--verify-no-changes" in command
    assert "--no-restore" in command
    assert "--include" not in command
    assert result.data["restore_executed"] is False


def test_dotnet_build_uses_no_restore_external_artifacts_and_blocked_proxy(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _dotnet_project(Path.home() / "Proyectos" / "dotnet-build")
    log = Path.home() / "dotnet-build.log"
    _fake_dotnet(project, log)
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dotnet.build_project",
        {"path": str(project), "configuration": "Release"},
        approved=True,
    )

    assert result.ok is True
    command = log.read_text(encoding="utf-8")
    assert "build" in command
    assert "--configuration Release" in command
    assert "--no-restore" in command
    assert "--artifacts-path" in command
    assert "--disable-build-servers" in command
    assert "PROXY=http://127.0.0.1:9" in command
    assert not (project / "bin").exists()
    assert not (project / "obj").exists()
    assert not (project / "artifacts").exists()
    assert result.data["artifacts_external"] is True


def test_dotnet_test_uses_fixed_arguments_and_never_restores(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _dotnet_project(Path.home() / "Proyectos" / "dotnet-test")
    log = Path.home() / "dotnet-test.log"
    _fake_dotnet(project, log)
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dotnet.test_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    command = log.read_text(encoding="utf-8")
    assert "test" in command
    assert "--logger console;verbosity=minimal" in command
    assert "--no-restore" in command
    assert "restore" not in result.data["command_argv"]
    assert "run" not in result.data["command_argv"]
    assert "publish" not in result.data["command_argv"]


def test_dotnet_sdk_before_eight_is_unavailable_for_external_artifacts(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _dotnet_project(Path.home() / "Proyectos" / "dotnet-old-sdk")
    log = Path.home() / "dotnet-old.log"
    _fake_dotnet(project, log, version="7.0.410")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dotnet.build_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is False
    assert result.data["stage_status"] == "unavailable"
    assert ".NET SDK 8" in result.message


def test_dotnet_ambiguous_root_requires_explicit_target(
    isolated_home: ElyndraPaths,
) -> None:
    project = _dotnet_project(Path.home() / "Proyectos" / "dotnet-ambiguous")
    (project / "Other.sln").write_text(
        "Microsoft Visual Studio Solution File, Format Version 12.00\n",
        encoding="utf-8",
    )
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dotnet.build_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["stage_status"] == "skipped"
    assert "varias soluciones" in result.message


def test_dotnet_profile_persists_safe_settings(isolated_home: ElyndraPaths) -> None:
    project = _dotnet_project(Path.home() / "Proyectos" / "dotnet-profile")
    app = ElyndraApplication.load(isolated_home)

    profile = app.dotnet_profiles.save(
        project,
        actor=app.identity.system_user,
        configuration="Release",
        format_enabled=False,
        fail_fast=True,
        require_tools=True,
        max_dotnet_files=123,
        exclude_paths=["bin", "generated"],
    )

    assert profile["configuration"] == "Release"
    assert profile["format_enabled"] is False
    assert profile["fail_fast"] is True
    assert profile["require_tools"] is True
    assert profile["max_dotnet_files"] == 123
    assert profile["exclude_paths"] == ["bin", "generated"]


def test_dotnet_profile_rejects_absolute_exclusion(
    isolated_home: ElyndraPaths,
) -> None:
    project = _dotnet_project(Path.home() / "Proyectos" / "dotnet-bad-profile")
    app = ElyndraApplication.load(isolated_home)

    with pytest.raises(ValueError, match="relativas"):
        app.dotnet_profiles.save(
            project,
            actor=app.identity.system_user,
            exclude_paths=[str(Path.home())],
        )


def test_dotnet_verify_persists_partial_history_without_tool(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _dotnet_project(Path.home() / "Proyectos" / "dotnet-history")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "dotnet.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    run = result.data["verification_run"]
    assert run["toolchain"] == "dotnet"
    assert run["status"] == "partial"
    stages = {item["name"]: item for item in result.data["stages"]}
    assert stages["inspect"]["status"] == "passed"
    assert stages["descriptor"]["status"] == "passed"
    assert stages["format"]["status"] == "unavailable"
    assert stages["build"]["status"] == "unavailable"


def test_dotnet_control_center_schema_registry_and_security_contract(
    isolated_home: ElyndraPaths,
) -> None:
    project = _dotnet_project(Path.home() / "Proyectos" / "dotnet-control")
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    profile = service.save_dotnet_profile(
        {"project_root": str(project), "configuration": "Release"}
    )
    overview = service.control_overview()
    projects = service.control_projects()
    source_path = (
        Path(__file__).parents[1] / "src" / "elyndra" / "skills" / "dotnet_project.py"
    )
    source = source_path.read_text(encoding="utf-8")

    assert profile["configuration"] == "Release"
    assert overview["dotnet_profiles"] == 1
    assert "dotnet_verifications" in overview
    assert projects["dotnet_profiles"][0]["configuration"] == "Release"
    assert len(app.skills.list_all()) == 102
    assert app.config.dotnet_tool_timeout_seconds == 300
    assert app.config.dotnet_tool_max_output_chars == 12000
    assert "shell=True" not in source
    for forbidden in ('"restore",', '"run",', '"publish",', '"tool", "restore"'):
        assert forbidden not in source
    with app.database.connect() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert version == "50"


def test_dotnet_knowledge_package_manifest(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    root = Path(__file__).parents[1]
    package = root / "knowledge-packs" / "dotnet-modern-basic"

    inspected = app.alexandria_packages.inspect(package)

    assert inspected["package_id"] == "programming.dotnet.modern-basic"
    assert inspected["domain"] == "programming/dotnet"
    assert inspected["source_count"] == 1


def test_dotnet_control_http_endpoints(
    isolated_home: ElyndraPaths,
) -> None:
    project = _dotnet_project(Path.home() / "Proyectos" / "dotnet-http")
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    token = "dotnet-control-token"
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0),
        _handler_factory(service, token),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    try:
        save = Request(
            f"{base}/api/control/dotnet-profiles",
            data=json.dumps(
                {"project_root": str(project), "configuration": "Release"}
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Elyndra-Token": token,
            },
            method="POST",
        )
        with urlopen(save, timeout=3) as response:
            saved = json.load(response)
        assert response.status == HTTPStatus.OK
        assert saved["item"]["configuration"] == "Release"

        with urlopen(
            f"{base}/api/control/dotnet-verifications", timeout=3
        ) as response:
            verifications = json.load(response)
        assert response.status == HTTPStatus.OK
        assert verifications == {"items": []}

        delete = Request(
            f"{base}/api/control/dotnet-profiles?path={quote(str(project))}",
            headers={"X-Elyndra-Token": token},
            method="DELETE",
        )
        with urlopen(delete, timeout=3) as response:
            removed = json.load(response)
        assert response.status == HTTPStatus.OK
        assert removed == {"removed": str(project)}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()
