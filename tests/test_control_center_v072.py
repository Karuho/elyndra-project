from __future__ import annotations

from pathlib import Path

import pytest

from elyndra.application import ElyndraApplication
from elyndra.cli import build_parser
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService


def _executable(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_php_profile_crud_and_relative_config(isolated_home: ElyndraPaths) -> None:
    project = Path.home() / "Proyectos" / "profile-app"
    project.mkdir(parents=True)
    config = project / "phpstan.neon"
    config.write_text("parameters: {}\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    saved = app.php_profiles.save(
        project,
        actor=app.identity.system_user,
        phpstan_config=config,
        phpstan_level="max",
        composer_strict=True,
        timeout_seconds=45,
        max_output_chars=5_000,
    )

    assert saved["project_root"] == str(project.resolve())
    assert saved["phpstan_config"] == "phpstan.neon"
    assert saved["phpstan_level"] == "max"
    assert saved["composer_strict"] is True
    assert app.php_profiles.list_all() == [saved]
    assert app.php_profiles.delete(project) is True
    assert app.php_profiles.get(project) is None


def test_php_profile_rejects_config_outside_project(
    isolated_home: ElyndraPaths,
) -> None:
    project = Path.home() / "Proyectos" / "safe-profile"
    project.mkdir(parents=True)
    outside = Path.home() / "outside.neon"
    outside.write_text("parameters: {}\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    with pytest.raises(ValueError, match="dentro del proyecto"):
        app.php_profiles.save(
            project,
            actor=app.identity.system_user,
            phpstan_config=outside,
        )


def test_phpstan_plan_and_execution_apply_stored_profile(
    isolated_home: ElyndraPaths,
) -> None:
    project = Path.home() / "Proyectos" / "phpstan-profile"
    project.mkdir(parents=True)
    (project / "composer.json").write_text("{}", encoding="utf-8")
    config = project / "phpstan.neon"
    config.write_text("parameters: {}\n", encoding="utf-8")
    argv_log = Path.home() / "phpstan-profile-argv.log"
    binary = _executable(
        project / "vendor" / "bin" / "phpstan",
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {argv_log}\n"
        "echo '{\"totals\":{\"errors\":0,\"file_errors\":0}}'\n"
        "exit 0\n",
    )
    app = ElyndraApplication.load(isolated_home)
    profile = app.php_profiles.save(
        project,
        actor=app.identity.system_user,
        phpstan_config="phpstan.neon",
        phpstan_level="max",
        timeout_seconds=30,
        max_output_chars=2_000,
    )

    plan = app.plan_skill("phpstan.analyse", {"path": str(project)})
    result = app.execute_skill(
        "phpstan.analyse", {"path": str(project)}, approved=True
    )

    assert plan.ok is True
    assert plan.data["project_profile_applied"] is True
    assert plan.data["project_profile_id"] == profile["id"]
    assert plan.data["timeout_seconds"] == 30
    assert str(config.resolve()) in plan.data["action_argv"]
    assert "max" in plan.data["action_argv"]
    assert result.ok is True
    assert result.data["command"][0] == str(binary.resolve())
    assert result.data["project_profile_applied"] is True
    assert result.data["timeout_seconds"] == 30
    argv = argv_log.read_text(encoding="utf-8").splitlines()
    assert "--configuration" in argv
    assert str(config.resolve()) in argv
    assert "--level" in argv
    assert "max" in argv


def test_explicit_phpstan_params_override_profile(
    isolated_home: ElyndraPaths,
) -> None:
    project = Path.home() / "Proyectos" / "override-profile"
    project.mkdir(parents=True)
    (project / "composer.json").write_text("{}", encoding="utf-8")
    default_config = project / "phpstan.neon"
    explicit_config = project / "phpstan.dist.neon"
    default_config.write_text("parameters: {}\n", encoding="utf-8")
    explicit_config.write_text("parameters: {}\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)
    app.php_profiles.save(
        project,
        actor=app.identity.system_user,
        phpstan_config=default_config,
        phpstan_level="max",
    )

    plan = app.plan_skill(
        "phpstan.analyse",
        {
            "path": str(project),
            "config": str(explicit_config),
            "level": "3",
        },
    )

    assert str(explicit_config.resolve()) in plan.data["action_argv"]
    assert str(default_config.resolve()) not in plan.data["action_argv"]
    assert "3" in plan.data["action_argv"]
    assert "max" not in plan.data["action_argv"]


def test_control_service_manages_trust_profiles_and_audit(
    isolated_home: ElyndraPaths,
) -> None:
    project = Path.home() / "Escritorio" / "web-control"
    project.mkdir(parents=True)
    (project / "composer.json").write_text("{}", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    trusted = service.trust_project(str(project))
    profile = service.save_php_profile(
        {
            "project_root": str(project),
            "phpstan_level": "8",
            "composer_strict": True,
            "timeout_seconds": 60,
            "max_output_chars": 4_000,
        }
    )
    projects = service.control_projects()
    audit = service.control_audit(query="web.project", limit=50)

    assert trusted["path"] == str(project.resolve())
    assert profile["phpstan_level"] == "8"
    assert projects["trusted_projects"][0]["profile"]["id"] == profile["id"]
    assert {item["action"] for item in audit} >= {
        "web.project.trust",
        "web.project.php_profile.save",
    }
    assert service.delete_php_profile(str(project)) is True
    assert service.untrust_project(str(project)) is True


def test_control_rejects_missing_project_paths(
    isolated_home: ElyndraPaths,
) -> None:
    service = ElyndraWebService(ElyndraApplication.load(isolated_home))

    with pytest.raises(ValueError, match="Falta la ruta"):
        service.trust_project("   ")
    with pytest.raises(ValueError, match="Falta la raíz"):
        service.save_php_profile({})


def test_control_profile_requires_persistent_authorization(
    isolated_home: ElyndraPaths,
) -> None:
    project = Path.home() / "Escritorio" / "untrusted-profile"
    project.mkdir(parents=True)
    service = ElyndraWebService(ElyndraApplication.load(isolated_home))

    with pytest.raises(PermissionError, match="requiere una raíz configurada"):
        service.save_php_profile({"project_root": str(project)})


def test_cli_exposes_php_profile_commands() -> None:
    parser = build_parser()
    profiles = parser.parse_args(["project", "profiles"])
    show = parser.parse_args(["project", "profile-show", "/tmp/app"])
    save = parser.parse_args(
        [
            "project",
            "profile-set",
            "/tmp/app",
            "--phpstan-level",
            "max",
            "--timeout",
            "60",
            "--approve",
        ]
    )
    delete = parser.parse_args(
        ["project", "profile-delete", "/tmp/app", "--approve"]
    )

    assert profiles.project_command == "profiles"
    assert show.project_command == "profile-show"
    assert save.project_command == "profile-set"
    assert save.phpstan_level == "max"
    assert save.timeout == 60
    assert delete.project_command == "profile-delete"


def test_control_page_assets_are_present() -> None:
    root = Path(__file__).parents[1] / "src" / "elyndra" / "web" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")

    assert 'id="open-control"' in html
    assert 'id="control"' in html
    assert "/api/control/trusted-projects" in script
    assert "/api/control/php-profiles" in script
    assert "/api/control/audit" in script
