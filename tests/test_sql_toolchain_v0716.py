from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from elyndra.application import ElyndraApplication
from elyndra.cli import build_parser
from elyndra.paths import ElyndraPaths
from elyndra.router import DeterministicRouter
from elyndra.web.server import ElyndraWebService


def _sql_project(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "queries").mkdir()
    (root / "queries" / "list_users.sql").write_text(
        "SELECT id, name FROM users WHERE active = 1;\n",
        encoding="utf-8",
    )
    migrations = root / "migrations"
    migrations.mkdir()
    (migrations / "001_create_users.sql").write_text(
        "BEGIN;\n"
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, active INTEGER);\n"
        "COMMIT;\n",
        encoding="utf-8",
    )
    database = root / "app.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, active INTEGER)"
        )
        connection.execute("CREATE INDEX idx_users_active ON users(active)")
        connection.execute(
            "INSERT INTO users(name, active) VALUES ('secret-row', 1)"
        )
    return root


def test_sql_router_clarifies_missing_path_and_routes_project() -> None:
    router = DeterministicRouter()

    missing = router.route("sql verify")
    verify = router.route("verifica proyecto sql /tmp/database")
    inspect = router.route("inspecciona proyecto sqlite /tmp/database")

    assert missing.kind == "clarification"
    assert missing.params["intended_skill"] == "sql.verify_project"
    assert verify.skill_name == "sql.verify_project"
    assert inspect.skill_name == "sql.project_inspect"


def test_sql_cli_exposes_controlled_workflows() -> None:
    parser = build_parser()

    verify = parser.parse_args(
        ["sqldev", "verify", "/tmp/sql", "--approve", "--allow-root-once"]
    )
    plan = parser.parse_args(
        [
            "sqldev",
            "plan",
            "/tmp/app.sqlite",
            "--query",
            "SELECT 1",
            "--approve",
        ]
    )
    profile = parser.parse_args(
        ["project", "sql-profile-set", "/tmp/sql", "--approve"]
    )

    assert verify.sqldev_command == "verify"
    assert verify.allow_root_once is True
    assert plan.sqldev_command == "plan"
    assert profile.project_command == "sql-profile-set"


def test_sql_inspect_detects_queries_migrations_and_sqlite(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project(Path.home() / "Proyectos" / "sql-inspect")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "sql.project_inspect",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    inventory = result.data["inventory"]
    assert inventory["sql_files"] == 2
    assert inventory["migration_files"] == 1
    assert inventory["sqlite_databases"] == 1
    assert inventory["read_only_default"] is True


def test_sql_external_project_requires_one_time_authorization(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project(Path.home() / "Escritorio" / "sql-external")
    app = ElyndraApplication.load(isolated_home)

    denied = app.execute_skill(
        "sql.project_inspect",
        {"path": str(project)},
        approved=True,
    )
    allowed = app.execute_skill(
        "sql.project_inspect",
        {"path": str(project), "allow_root_once": True},
        approved=True,
    )

    assert denied.ok is False
    assert "--allow-root-once" in denied.message
    assert allowed.ok is True
    assert allowed.data["authorization_scope"] == "project_once"


def test_sql_static_accepts_reads_and_rejects_mutations(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project(Path.home() / "Proyectos" / "sql-static")
    unsafe = project / "queries" / "unsafe.sql"
    unsafe.write_text("UPDATE users SET active = 0;\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    failed = app.execute_skill(
        "sql.static_validate",
        {"path": str(project)},
        approved=True,
    )
    allowed = app.execute_skill(
        "sql.static_validate",
        {"path": str(project), "allow_mutating_sql": True},
        approved=True,
    )

    assert failed.ok is False
    assert any("modifica esquema o datos" in item for item in failed.data["errors"])
    assert allowed.ok is True


def test_sql_static_rejects_mutating_common_table_expression(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project(Path.home() / "Proyectos" / "sql-cte-write")
    path = project / "queries" / "cte_write.sql"
    path.write_text(
        "WITH removed AS (DELETE FROM users RETURNING id) SELECT id FROM removed;\n",
        encoding="utf-8",
    )
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "sql.static_validate",
        {"path": str(path)},
        approved=True,
    )

    assert result.ok is False
    assert result.data["reports"][0]["statement_counts"]["mutating"] == 1


def test_sql_static_rejects_sensitive_operations_even_when_mutations_allowed(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project(Path.home() / "Proyectos" / "sql-sensitive")
    path = project / "queries" / "sensitive.sql"
    path.write_text("ATTACH DATABASE '/tmp/other.db' AS other;\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "sql.static_validate",
        {"path": str(path), "allow_mutating_sql": True},
        approved=True,
    )

    assert result.ok is False
    assert any("ATTACH DATABASE" in item for item in result.data["errors"])


def test_sql_migrations_reject_duplicates_and_destructive_statements(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project(Path.home() / "Proyectos" / "sql-migrations")
    migrations = project / "migrations"
    (migrations / "001_duplicate.sql").write_text(
        "DROP TABLE users;\n",
        encoding="utf-8",
    )
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "sql.migration_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is False
    errors = result.data["report"]["errors"]
    assert any("Versión duplicada" in item for item in errors)
    assert any("destructiva no autorizada" in item for item in errors)


def test_sql_migration_invalid_utf8_is_reported_without_crashing(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project(Path.home() / "Proyectos" / "sql-utf8")
    (project / "migrations" / "002_invalid.sql").write_bytes(b"\xff\xfe")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "sql.migration_validate",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is False
    assert any("UTF-8" in item for item in result.data["report"]["errors"])


def test_sqlite_schema_inspection_reads_metadata_not_rows(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project(Path.home() / "Proyectos" / "sqlite-schema")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "sqlite.schema_inspect",
        {"path": str(project / "app.sqlite")},
        approved=True,
    )

    assert result.ok is True
    database = result.data["databases"][0]
    assert database["tables"] == 1
    assert database["columns"] == 3
    assert database["rows_read"] == 0
    assert database["mode"] == "read-only"
    assert "secret-row" not in str(result.data)


def test_sqlite_query_plan_accepts_select_and_rejects_writes(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project(Path.home() / "Proyectos" / "sqlite-plan")
    database = project / "app.sqlite"
    app = ElyndraApplication.load(isolated_home)

    select = app.execute_skill(
        "sqlite.query_plan",
        {
            "database": str(database),
            "query": "SELECT id FROM users WHERE active = 1",
        },
        approved=True,
    )
    update = app.execute_skill(
        "sqlite.query_plan",
        {"database": str(database), "query": "UPDATE users SET active = 0"},
        approved=True,
    )
    multiple = app.execute_skill(
        "sqlite.query_plan",
        {"database": str(database), "query": "SELECT 1; SELECT 2"},
        approved=True,
    )

    assert select.ok is True
    assert select.data["plan"]
    assert update.ok is False
    assert multiple.ok is False


def test_sql_verify_persists_history_and_skips_missing_stages(
    isolated_home: ElyndraPaths,
) -> None:
    project = Path.home() / "Proyectos" / "sql-history"
    project.mkdir(parents=True)
    (project / "query.sql").write_text("SELECT 1;\n", encoding="utf-8")
    app = ElyndraApplication.load(isolated_home)

    result = app.execute_skill(
        "sql.verify_project",
        {"path": str(project)},
        approved=True,
    )

    assert result.ok is True
    run = result.data["verification_run"]
    assert run["toolchain"] == "sql"
    assert run["status"] == "passed"
    stages = {item["name"]: item for item in result.data["stages"]}
    assert stages["inspect"]["status"] == "passed"
    assert stages["static"]["status"] == "passed"
    assert stages["migrations"]["status"] == "skipped"
    assert stages["schema"]["status"] == "skipped"


def test_sql_profile_and_control_center(isolated_home: ElyndraPaths) -> None:
    project = _sql_project(Path.home() / "Proyectos" / "sql-profile")
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    profile = service.save_sql_profile(
        {
            "project_root": str(project),
            "dialect": "sqlite",
            "schema_enabled": False,
            "max_sql_files": 321,
        }
    )
    overview = service.control_overview()
    projects = service.control_projects()

    assert profile["dialect"] == "sqlite"
    assert profile["schema_enabled"] is False
    assert profile["max_sql_files"] == 321
    assert overview["sql_profiles"] == 1
    assert "sql_verifications" in overview
    assert projects["sql_profiles"][0]["project_root"] == str(project)
    assert len(app.skills.list_all()) == 102
    with app.database.connect() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert version == "51"


def test_sql_profile_rejects_unsafe_exclusion(isolated_home: ElyndraPaths) -> None:
    project = _sql_project(Path.home() / "Proyectos" / "sql-profile-invalid")
    app = ElyndraApplication.load(isolated_home)

    with pytest.raises(ValueError, match="rutas relativas seguras"):
        app.sql_profiles.save(
            project,
            actor=app.identity.system_user,
            exclude_paths=["../outside"],
        )


def test_sql_knowledge_package_is_valid(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    package = Path(__file__).parents[1] / "knowledge-packs" / "sql-databases-modern-basic"

    inspected = app.alexandria_packages.inspect(package)

    assert inspected["package_id"] == "programming.sql-databases.modern-basic"
    assert inspected["domain"] == "programming/sql-databases"
    assert inspected["source_count"] == 1


def test_sql_source_contains_no_execution_or_write_commands() -> None:
    source = (
        Path(__file__).parents[1] / "src" / "elyndra" / "skills" / "sql_project.py"
    ).read_text(encoding="utf-8")

    forbidden = (
        "shell=True",
        "executescript(",
        "PRAGMA journal_mode",
        "VACUUM INTO",
        "subprocess",
    )
    assert not any(item in source for item in forbidden)
    assert "mode=ro" in source
    assert "PRAGMA query_only = ON" in source
