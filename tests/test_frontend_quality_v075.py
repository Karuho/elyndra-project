from __future__ import annotations

import json
from pathlib import Path

import pytest

from elyndra.application import ElyndraApplication
from elyndra.cli import build_parser
from elyndra.paths import ElyndraPaths
from elyndra.router import DeterministicRouter
from elyndra.skills.frontend_quality import inspect_frontend_project


def _project(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "index.html").write_text("<main>Hola</main>\n", encoding="utf-8")
    (root / "app.css").write_text("body { color: #222; }\n", encoding="utf-8")
    (root / "app.js").write_text("const value = 1;\n", encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "frontend-demo",
                "scripts": {"dev": "vite", "build": "vite build"},
                "dependencies": {"react": "1"},
                "devDependencies": {"vite": "1", "eslint": "1", "stylelint": "1"},
                "workspaces": ["packages/*"],
            }
        ),
        encoding="utf-8",
    )
    (root / "package-lock.json").write_text("{}", encoding="utf-8")
    (root / "vite.config.js").write_text("export default {};\n", encoding="utf-8")
    return root


def _executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def test_frontend_cli_exposes_quality_commands_and_profile_flags() -> None:
    parser = build_parser()

    eslint = parser.parse_args(
        ["webdev", "eslint", "/tmp/site", "--config", "eslint.config.js", "--approve"]
    )
    stylelint = parser.parse_args(
        ["webdev", "stylelint", "/tmp/site", "--approve"]
    )
    framework = parser.parse_args(
        ["webdev", "framework", "/tmp/site", "--framework-preset", "vite", "--approve"]
    )
    profile = parser.parse_args(
        [
            "project",
            "web-profile-set",
            "/tmp/site",
            "--eslint",
            "--stylelint",
            "--framework-checks",
            "--framework-preset",
            "react",
            "--approve",
        ]
    )

    assert eslint.webdev_command == "eslint"
    assert eslint.eslint_config == "eslint.config.js"
    assert stylelint.webdev_command == "stylelint"
    assert framework.framework_preset == "vite"
    assert profile.framework_preset == "react"
    assert profile.eslint is True


def test_frontend_router_routes_eslint_stylelint_and_framework() -> None:
    router = DeterministicRouter()

    eslint = router.route("ejecuta ESLint en /tmp/site")
    stylelint = router.route("revisa Stylelint en /tmp/site")
    framework = router.route("valida la configuración Vite en /tmp/site")

    assert eslint.skill_name == "eslint.lint"
    assert stylelint.skill_name == "stylelint.lint"
    assert framework.skill_name == "web.framework_validate"


def test_frontend_inventory_detects_vite_react_workspace_and_npm(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path / "frontend-inventory")

    report = inspect_frontend_project(root)

    assert report["primary_framework"] == "react"
    assert report["detected_frameworks"] == ["react", "vite"]
    assert report["package_manager"] == "npm"
    assert report["workspaces"] == ["packages/*"]
    assert report["vite_config"] == "vite.config.js"


def test_framework_validation_rejects_required_preset_mismatch(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project(Path.home() / "Proyectos" / "frontend-mismatch")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "web.framework_validate",
        {"path": str(root), "framework_preset": "angular"},
        approved=True,
    )

    assert result.ok is False
    assert "no se detectó" in result.message
    assert result.data["shell"] is False


def test_angular_inspection_reports_projects_and_invalid_lock_mix(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project(Path.home() / "Proyectos" / "angular-workspace")
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    package["dependencies"]["@angular/core"] = "1"
    (root / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (root / "angular.json").write_text(
        json.dumps({"projects": {"app": {}, "admin": {}}, "defaultProject": "app"}),
        encoding="utf-8",
    )
    (root / "yarn.lock").write_text("", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "web.framework_validate",
        {"path": str(root)},
        approved=True,
    )

    report = result.data["framework_report"]
    assert result.ok is True
    assert report["angular"]["project_count"] == 2
    assert report["angular"]["projects"] == ["admin", "app"]
    assert any("varios lockfiles" in item for item in report["warnings"])


def test_eslint_prefers_project_local_binary_and_uses_fixed_argv(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project(Path.home() / "Proyectos" / "eslint-local")
    log = Path.home() / "eslint.log"
    local = root / "node_modules" / ".bin" / "eslint"
    _executable(local, f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill("eslint.lint", {"path": str(root)}, approved=True)

    assert result.ok is True
    assert result.data["tool_source"] == "project_local"
    assert result.data["shell"] is False
    assert log.read_text(encoding="utf-8").strip() == ". --no-color --format stylish"


def test_stylelint_uses_validated_project_config(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project(Path.home() / "Proyectos" / "stylelint-local")
    config = root / "stylelint.config.js"
    config.write_text("export default {};\n", encoding="utf-8")
    log = Path.home() / "stylelint.log"
    local = root / "node_modules" / ".bin" / "stylelint"
    _executable(local, f"#!/bin/sh\nprintf '%s\\n' \"$*\" > '{log}'\nexit 0\n")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "stylelint.lint",
        {"path": str(root), "stylelint_config": "stylelint.config.js"},
        approved=True,
    )

    assert result.ok is True
    assert result.data["tool_source"] == "project_local"
    assert "--config stylelint.config.js" in log.read_text(encoding="utf-8")


def test_frontend_config_rejects_path_outside_project(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project(Path.home() / "Proyectos" / "frontend-config-safe")
    outside = Path.home() / "outside-eslint.js"
    outside.write_text("export default {};\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "eslint.lint",
        {"path": str(root), "eslint_config": str(outside)},
        approved=True,
    )

    assert result.ok is False
    assert "permanecer dentro del proyecto" in result.message


def test_web_profile_persists_quality_and_framework_settings(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project(Path.home() / "Proyectos" / "frontend-profile")
    eslint_config = root / "eslint.config.js"
    stylelint_config = root / "stylelint.config.js"
    eslint_config.write_text("export default [];\n", encoding="utf-8")
    stylelint_config.write_text("export default {};\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    profile = app.web_profiles.save(
        root,
        actor=app.identity.system_user,
        eslint_enabled=True,
        stylelint_enabled=False,
        framework_checks_enabled=True,
        framework_preset="vite",
        eslint_config="eslint.config.js",
        stylelint_config="stylelint.config.js",
    )

    assert profile["eslint_enabled"] is True
    assert profile["stylelint_enabled"] is False
    assert profile["framework_checks_enabled"] is True
    assert profile["framework_preset"] == "vite"
    assert profile["eslint_config"] == "eslint.config.js"


def test_web_verify_includes_framework_eslint_and_stylelint_stages(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project(Path.home() / "Proyectos" / "frontend-pipeline")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "web.verify_project",
        {"path": str(root), "javascript_enabled": False, "typescript_enabled": False},
        approved=True,
    )

    statuses = {item["name"]: item["status"] for item in result.data["stages"]}
    assert statuses["framework"] == "passed"
    assert statuses["eslint"] == "unavailable"
    assert statuses["stylelint"] == "unavailable"
    assert result.data["verification_status"] == "partial"


def test_web_verify_require_tools_fails_when_linter_is_missing(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project(Path.home() / "Proyectos" / "frontend-required")
    monkeypatch.setenv("PATH", "")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "web.verify_project",
        {
            "path": str(root),
            "javascript_enabled": False,
            "typescript_enabled": False,
            "stylelint_enabled": False,
            "require_tools": True,
        },
        approved=True,
    )

    assert result.ok is False
    assert result.data["verification_status"] == "failed"
    statuses = {item["name"]: item["status"] for item in result.data["stages"]}
    assert statuses["eslint"] == "failed"


def test_web_profile_migration_adds_quality_columns(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)

    with app.database.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(web_project_profiles)")
        }
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]

    assert {"eslint_enabled", "stylelint_enabled", "framework_preset"} <= columns
    assert version == "50"


def test_existing_v074_database_is_migrated_without_recreating_profiles(
    isolated_home: ElyndraPaths,
) -> None:
    import sqlite3

    database_path = isolated_home.database_file
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', '12');
            CREATE TABLE web_project_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_root TEXT NOT NULL UNIQUE,
                html_enabled INTEGER NOT NULL DEFAULT 1,
                css_enabled INTEGER NOT NULL DEFAULT 1,
                javascript_enabled INTEGER NOT NULL DEFAULT 1,
                typescript_enabled INTEGER NOT NULL DEFAULT 1,
                fail_fast INTEGER NOT NULL DEFAULT 0,
                require_tools INTEGER NOT NULL DEFAULT 0,
                max_files INTEGER NOT NULL DEFAULT 3000,
                exclude_paths_json TEXT NOT NULL DEFAULT '[]',
                timeout_seconds INTEGER,
                max_output_chars INTEGER,
                actor TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO web_project_profiles(
                project_root, actor, created_at, updated_at
            ) VALUES('/tmp/legacy-web', 'karuho', 'before', 'before');
            """
        )

    app = ElyndraApplication.load(isolated_home)
    profile = app.web_profiles.get(Path("/tmp/legacy-web"))

    assert profile is not None
    assert profile["eslint_enabled"] is True
    assert profile["stylelint_enabled"] is True
    assert profile["framework_preset"] == "auto"
    with app.database.connect() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert version == "50"
