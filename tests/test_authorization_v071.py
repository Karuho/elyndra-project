from __future__ import annotations

import os
from pathlib import Path

import pytest

from elyndra.application import ElyndraApplication
from elyndra.cli import build_parser
from elyndra.paths import ElyndraPaths


def _executable(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_trusted_project_grants_persistent_authorization(
    isolated_home: ElyndraPaths, monkeypatch
) -> None:
    project = Path.home() / "Escritorio" / "trusted-app"
    project.mkdir(parents=True)
    (project / "composer.json").write_text("{}", encoding="utf-8")
    tools = Path.home() / "trusted-tools"
    _executable(tools / "composer", "#!/bin/sh\necho valid\nexit 0\n")
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ.get('PATH', '')}")
    app = ElyndraApplication.load(isolated_home)

    app.trusted_projects.trust(project, actor=app.identity.system_user)
    result = app.execute_skill(
        "composer.validate", {"path": str(project)}, approved=True
    )

    assert result.ok is True
    assert result.data["authorization_scope"] == "project_persistent"
    assert result.data["authorization_source"] == "trusted_project"
    assert result.data["authorization_expires_after_execution"] is False


def test_revoked_project_requires_one_time_authorization(
    isolated_home: ElyndraPaths,
) -> None:
    project = Path.home() / "Escritorio" / "revoked-app"
    project.mkdir(parents=True)
    (project / "composer.json").write_text("{}", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)
    app.trusted_projects.trust(project, actor=app.identity.system_user)

    assert app.trusted_projects.untrust(project) is True
    result = app.execute_skill(
        "composer.validate", {"path": str(project)}, approved=True
    )

    assert result.ok is False
    assert "--allow-root-once" in result.message


def test_trust_rejects_home_and_files(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    with pytest.raises(ValueError, match="demasiado amplia|contenga todo HOME"):
        app.trusted_projects.trust(Path.home(), actor=app.identity.system_user)
    file_path = Path.home() / "not-a-project"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="carpeta existente"):
        app.trusted_projects.trust(file_path, actor=app.identity.system_user)


def test_skill_plan_does_not_execute(
    isolated_home: ElyndraPaths, monkeypatch
) -> None:
    tools = Path.home() / "plan-tools"
    execution_log = Path.home() / "plan.log"
    _executable(
        tools / "php",
        f"#!/bin/sh\necho run >> {execution_log}\necho ok\nexit 0\n",
    )
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ.get('PATH', '')}")
    source = Path.home() / "Escritorio" / "plan.php"
    source.parent.mkdir()
    source.write_text("<?php\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    plan = app.plan_skill("php.syntax_validate", {"path": str(source)})

    assert plan.ok is True
    assert plan.data["execution_performed"] is False
    assert plan.data["authorization_scope"] == "single_file"
    assert plan.data["action_argv"][-2:] == ["-l", str(source.resolve())]
    assert not execution_log.exists()


def test_skill_inspect_and_cli_subcommands() -> None:
    parser = build_parser()
    plan = parser.parse_args(
        ["skill", "plan", "php.syntax_validate", "--params", '{"path":"/tmp/a.php"}']
    )
    inspect = parser.parse_args(["skill", "inspect", "phpunit.run"])
    trust = parser.parse_args(["project", "trust", "/tmp/app", "--approve"])
    audit = parser.parse_args(["audit", "list", "--action", "skill.execute"])

    assert plan.skill_command == "plan"
    assert inspect.skill_command == "inspect"
    assert trust.project_command == "trust"
    assert audit.audit_command == "list"


def test_audit_filters_and_show(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    event_id = app.audit.record(
        actor=app.identity.system_user,
        action="test.authorization",
        target="project",
        outcome="success",
        details={"token": "do-not-store"},
    )

    rows = app.audit.list_recent(action="test.authorization")
    event = app.audit.get(event_id)

    assert [row["id"] for row in rows] == [event_id]
    assert event is not None
    assert "do-not-store" not in event["details_json"]
    assert "[REDACTED]" in event["details_json"]


def test_project_tool_resolution_prefers_local_binary(
    isolated_home: ElyndraPaths, monkeypatch
) -> None:
    project = Path.home() / "Proyectos" / "local-tool"
    project.mkdir(parents=True)
    (project / "composer.json").write_text("{}", encoding="utf-8")
    local = _executable(
        project / "vendor" / "bin" / "phpstan",
        "#!/bin/sh\necho '{\"totals\":{\"errors\":0,\"file_errors\":0}}'\nexit 0\n",
    )
    global_tools = Path.home() / "global-tools"
    _executable(global_tools / "phpstan", "#!/bin/sh\nexit 99\n")
    monkeypatch.setenv(
        "PATH", f"{global_tools}{os.pathsep}{os.environ.get('PATH', '')}"
    )
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "phpstan.analyse", {"path": str(project)}, approved=True
    )

    assert result.ok is True
    assert result.data["command"][0] == str(local.resolve())
