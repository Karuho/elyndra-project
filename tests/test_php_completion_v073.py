from __future__ import annotations

import os
from pathlib import Path

import pytest

from elyndra.application import ElyndraApplication
from elyndra.cli import build_parser
from elyndra.paths import ElyndraPaths
from elyndra.router import DeterministicRouter
from elyndra.web.server import ElyndraWebService


def _executable(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _prepend_path(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    current = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{current}")


def _php_project(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "composer.json").write_text(
        """{
  "name": "test/example",
  "require": {"php": "^8.2", "laravel/framework": "^11"},
  "require-dev": {"phpstan/phpstan": "^2", "phpunit/phpunit": "^11"},
  "autoload": {"psr-4": {"App\\\\": "src/"}},
  "scripts": {"test": "phpunit", "analyse": "phpstan analyse"}
}
""",
        encoding="utf-8",
    )
    source = root / "src" / "Example.php"
    source.parent.mkdir()
    source.write_text("<?php\nfinal class Example {}\n", encoding="utf-8")
    tests = root / "tests" / "ExampleTest.php"
    tests.parent.mkdir()
    tests.write_text("<?php\nfinal class ExampleTest {}\n", encoding="utf-8")
    return root


def test_php_project_inspect_reports_metadata_without_script_values(
    isolated_home: ElyndraPaths,
) -> None:
    project = _php_project(Path.home() / "Proyectos" / "inspect-app")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "php.project_inspect",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    inventory = result.data["inventory"]
    assert inventory["package_name"] == "test/example"
    assert inventory["php_constraint"] == "^8.2"
    assert inventory["frameworks"] == ["Laravel"]
    assert inventory["composer_scripts"] == ["analyse", "test"]
    assert "phpunit" not in str(inventory["composer_scripts"])
    assert inventory["php_files"] == 2
    assert result.data["authorization_scope"] == "project_persistent"


def test_php_project_inspect_external_requires_one_time_authorization(
    isolated_home: ElyndraPaths,
) -> None:
    project = _php_project(Path.home() / "Escritorio" / "inspect-external")
    app = ElyndraApplication.load(isolated_home)

    denied = app.execute_skill(
        "php.project_inspect",
        {"path": str(project)},
        approved=True,
    )
    allowed = app.execute_skill(
        "php.project_inspect",
        {"path": str(project), "allow_root_once": True},
        approved=True,
    )

    assert denied.ok is False
    assert "--allow-root-once" in denied.message
    assert allowed.ok is True
    assert allowed.data["authorization_scope"] == "project_once"


def test_php_syntax_scan_checks_project_and_excludes_vendor(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = Path.home() / "tools"
    log = Path.home() / "php-scan.log"
    _executable(
        tools / "php",
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$2\" >> \"{log}\"\n"
        "echo ok\n"
        "exit 0\n",
    )
    _prepend_path(monkeypatch, tools)
    project = _php_project(Path.home() / "Proyectos" / "syntax-app")
    vendor = project / "vendor" / "ignored.php"
    vendor.parent.mkdir()
    vendor.write_text("<?php\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "php.syntax_scan",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["scanned_files"] == 2
    scanned = log.read_text(encoding="utf-8")
    assert "Example.php" in scanned
    assert "ignored.php" not in scanned
    assert result.data["shell"] is False


def test_php_syntax_scan_enforces_file_limit(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = Path.home() / "tools-limit"
    _executable(tools / "php", "#!/bin/sh\nexit 0\n")
    _prepend_path(monkeypatch, tools)
    project = _php_project(Path.home() / "Proyectos" / "limited-app")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "php.syntax_scan",
        {"path": str(project), "max_files": 1},
        approved=True,
    )

    assert result.ok is False
    assert result.data["scan_truncated"] is True
    assert "límite de archivos PHP" in result.message


def test_php_profile_persists_pipeline_settings_and_exclusions(
    isolated_home: ElyndraPaths,
) -> None:
    project = _php_project(Path.home() / "Proyectos" / "profile-073")
    (project / "var" / "cache").mkdir(parents=True)
    app = ElyndraApplication.load(isolated_home)

    saved = app.php_profiles.save(
        project,
        actor=app.identity.system_user,
        composer_enabled=False,
        syntax_scan_enabled=True,
        phpstan_enabled=False,
        phpunit_enabled=True,
        fail_fast=True,
        require_tools=True,
        max_php_files=321,
        exclude_paths=["vendor", "var/cache"],
    )

    assert saved["composer_enabled"] is False
    assert saved["syntax_scan_enabled"] is True
    assert saved["phpstan_enabled"] is False
    assert saved["phpunit_enabled"] is True
    assert saved["fail_fast"] is True
    assert saved["require_tools"] is True
    assert saved["max_php_files"] == 321
    assert saved["exclude_paths"] == ["vendor", "var/cache"]


def test_php_profile_rejects_exclusion_outside_project(
    isolated_home: ElyndraPaths,
) -> None:
    project = _php_project(Path.home() / "Proyectos" / "profile-exclude")
    app = ElyndraApplication.load(isolated_home)

    with pytest.raises(ValueError, match="dentro del proyecto"):
        app.php_profiles.save(
            project,
            actor=app.identity.system_user,
            exclude_paths=["../../outside"],
        )


def test_php_verify_project_runs_complete_pipeline_and_persists_history(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = Path.home() / "pipeline-tools"
    _executable(tools / "php", "#!/bin/sh\necho syntax-ok\nexit 0\n")
    _executable(tools / "composer", "#!/bin/sh\necho composer-ok\nexit 0\n")
    _prepend_path(monkeypatch, tools)
    project = _php_project(Path.home() / "Proyectos" / "pipeline-app")
    _executable(
        project / "vendor" / "bin" / "phpstan",
        "#!/bin/sh\necho '{\"totals\":{\"errors\":0,\"file_errors\":0}}'\nexit 0\n",
    )
    _executable(
        project / "vendor" / "bin" / "phpunit",
        "#!/bin/sh\necho 'OK (2 tests, 2 assertions)'\nexit 0\n",
    )
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "php.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["verification_status"] == "passed"
    assert [item["status"] for item in result.data["stages"]] == [
        "passed",
        "passed",
        "passed",
        "passed",
        "passed",
    ]
    run = app.verification_runs.get(result.data["verification_run_id"])
    assert run is not None
    assert run["status"] == "passed"
    assert len(run["summary"]["stages"]) == 5


def test_php_verify_project_is_partial_when_optional_tools_are_missing(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "")
    project = _php_project(Path.home() / "Proyectos" / "partial-app")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "php.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["verification_status"] == "partial"
    statuses = {item["name"]: item["status"] for item in result.data["stages"]}
    assert statuses["syntax"] == "unavailable"
    assert statuses["phpstan"] == "unavailable"
    assert statuses["phpunit"] == "unavailable"


def test_php_verify_project_can_require_all_tools(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "")
    project = _php_project(Path.home() / "Proyectos" / "required-tools")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "php.verify_project",
        {"path": str(project), "require_tools": True, "fail_fast": True},
        approved=True,
    )

    assert result.ok is False
    assert result.data["verification_status"] == "failed"
    assert result.data["stages"][1]["status"] == "failed"
    assert len(result.data["stages"]) == 2


def test_php_verification_compare_reports_stage_changes(
    isolated_home: ElyndraPaths,
) -> None:
    project = _php_project(Path.home() / "Proyectos" / "compare-app")
    app = ElyndraApplication.load(isolated_home)
    first = app.verification_runs.start(
        toolchain="php",
        project_root=project,
        actor=app.identity.system_user,
        profile_id=None,
        plan={"stages": ["syntax"]},
    )
    app.verification_runs.finish(
        first,
        status="failed",
        duration_ms=20,
        summary={"stages": [{"name": "syntax", "status": "failed"}]},
    )
    second = app.verification_runs.start(
        toolchain="php",
        project_root=project,
        actor=app.identity.system_user,
        profile_id=None,
        plan={"stages": ["syntax"]},
    )
    app.verification_runs.finish(
        second,
        status="passed",
        duration_ms=10,
        summary={"stages": [{"name": "syntax", "status": "passed"}]},
    )

    comparison = app.verification_runs.compare(first, second)

    assert comparison["status_changed"] is True
    assert comparison["duration_delta_ms"] == -10
    assert comparison["stage_changes"] == [
        {
            "name": "syntax",
            "before": "failed",
            "after": "passed",
            "changed": True,
        }
    ]


def test_router_and_cli_expose_php_completion_workflows() -> None:
    router = DeterministicRouter()
    parser = build_parser()

    verify_route = router.route("verifica el proyecto PHP /tmp/store")
    inspect_route = router.route("inspecciona el proyecto PHP /tmp/store")
    syntax_route = router.route("valida toda la sintaxis PHP del proyecto /tmp/store")
    verify_args = parser.parse_args(
        [
            "php",
            "verify",
            "/tmp/store",
            "--no-phpunit",
            "--fail-fast",
            "--approve",
        ]
    )
    history_args = parser.parse_args(["php", "history", "/tmp/store"])

    assert verify_route.skill_name == "php.verify_project"
    assert inspect_route.skill_name == "php.project_inspect"
    assert syntax_route.skill_name == "php.syntax_scan"
    assert verify_args.php_command == "verify"
    assert verify_args.phpunit is False
    assert verify_args.fail_fast is True
    assert history_args.php_command == "history"


def test_control_service_exposes_php_verification_history(
    isolated_home: ElyndraPaths,
) -> None:
    project = _php_project(Path.home() / "Proyectos" / "control-history")
    app = ElyndraApplication.load(isolated_home)
    run_id = app.verification_runs.start(
        toolchain="php",
        project_root=project,
        actor=app.identity.system_user,
        profile_id=None,
        plan={},
    )
    app.verification_runs.finish(
        run_id,
        status="passed",
        duration_ms=5,
        summary={"stages": []},
    )
    service = ElyndraWebService(app)

    overview = service.control_overview()
    items = service.control_php_verifications(project_root=str(project))

    assert overview["php_verifications"] == 1
    assert items[0]["public_id"] == run_id


def test_control_assets_include_php_verification_history() -> None:
    root = Path(__file__).parents[1] / "src" / "elyndra" / "web" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")

    assert 'id="control-php-verifications"' in html
    assert "/api/control/php-verifications" in script
    assert 'id="profile-max-php-files"' in html
    assert 'id="profile-require-tools"' in html


def test_php_syntax_scan_rejects_invalid_explicit_file_limit(
    isolated_home: ElyndraPaths,
) -> None:
    project = _php_project(Path.home() / "Proyectos" / "invalid-limit")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "php.syntax_scan",
        {"path": str(project), "max_files": 0},
        approved=True,
    )

    assert result.ok is False
    assert "max_files debe estar entre 1 y 20000" in result.message


def test_php_syntax_scan_does_not_follow_file_symlink_outside_project(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = Path.home() / "tools-symlink"
    log = Path.home() / "php-symlink.log"
    _executable(
        tools / "php",
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$2\" >> \"{log}\"\n"
        "exit 0\n",
    )
    _prepend_path(monkeypatch, tools)
    project = _php_project(Path.home() / "Proyectos" / "symlink-app")
    outside = Path.home() / "outside.php"
    outside.write_text("<?php\n", encoding="utf-8")
    (project / "outside-link.php").symlink_to(outside)
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "php.syntax_scan",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    assert result.data["scanned_files"] == 2
    assert str(outside) not in log.read_text(encoding="utf-8")


def test_php_verify_project_disabled_optional_stages_can_pass(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = Path.home() / "pipeline-minimal-tools"
    _executable(tools / "php", "#!/bin/sh\nexit 0\n")
    _prepend_path(monkeypatch, tools)
    project = _php_project(Path.home() / "Proyectos" / "minimal-pipeline")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "php.verify_project",
        {
            "path": str(project),
            "composer_enabled": False,
            "phpstan_enabled": False,
            "phpunit_enabled": False,
        },
        approved=True,
    )

    assert result.ok is True
    assert result.data["verification_status"] == "passed"
    statuses = {item["name"]: item["status"] for item in result.data["stages"]}
    assert statuses == {
        "inspect": "passed",
        "composer": "skipped",
        "syntax": "passed",
        "phpstan": "skipped",
        "phpunit": "skipped",
    }


def test_php_verification_compare_rejects_different_projects(
    isolated_home: ElyndraPaths,
) -> None:
    first_project = _php_project(Path.home() / "Proyectos" / "compare-first")
    second_project = _php_project(Path.home() / "Proyectos" / "compare-second")
    app = ElyndraApplication.load(isolated_home)
    first = app.verification_runs.start(
        toolchain="php",
        project_root=first_project,
        actor=app.identity.system_user,
        profile_id=None,
        plan={},
    )
    second = app.verification_runs.start(
        toolchain="php",
        project_root=second_project,
        actor=app.identity.system_user,
        profile_id=None,
        plan={},
    )

    with pytest.raises(ValueError, match="mismo proyecto"):
        app.verification_runs.compare(first, second)


def test_control_profile_rejects_non_boolean_stage_values(
    isolated_home: ElyndraPaths,
) -> None:
    project = _php_project(Path.home() / "Proyectos" / "web-bool-profile")
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    with pytest.raises(ValueError, match="composer_enabled debe ser booleano"):
        service.save_php_profile(
            {
                "project_root": str(project),
                "composer_enabled": "false",
            }
        )
