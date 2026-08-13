from __future__ import annotations

import os
import re
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from elyndra.policy import RiskLevel
from elyndra.skills.base import SkillContext, SkillResult

_SQL_SUFFIX = ".sql"
_DATABASE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
_PROJECT_MARKERS = (
    "migrations",
    "migration",
    "database",
    "db",
    "prisma",
    "alembic.ini",
    "flyway.conf",
    "liquibase.properties",
)
_DEFAULT_EXCLUDES = {
    ".git",
    ".idea",
    ".vscode",
    "backups",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "tmp",
    "vendor",
}
_DIALECTS = {"auto", "generic", "sqlite", "mysql", "mariadb", "postgresql"}
_MUTATING_KEYWORDS = {
    "ALTER",
    "ANALYZE",
    "CALL",
    "CLUSTER",
    "COMMENT",
    "COPY",
    "CREATE",
    "DELETE",
    "DO",
    "DROP",
    "EXEC",
    "EXECUTE",
    "GRANT",
    "INSERT",
    "LOAD",
    "MERGE",
    "REFRESH",
    "REINDEX",
    "REPLACE",
    "REVOKE",
    "TRUNCATE",
    "UPDATE",
    "VACUUM",
}
_DESTRUCTIVE_PATTERNS = (
    (re.compile(r"\bDROP\s+(?:DATABASE|SCHEMA|TABLE|VIEW|INDEX|TRIGGER)\b", re.I), "DROP"),
    (re.compile(r"\bTRUNCATE\b", re.I), "TRUNCATE"),
    (re.compile(r"\bDELETE\s+FROM\b(?![\s\S]*\bWHERE\b)", re.I), "DELETE sin WHERE"),
    (re.compile(r"\bUPDATE\b[\s\S]*\bSET\b(?![\s\S]*\bWHERE\b)", re.I), "UPDATE sin WHERE"),
    (re.compile(r"\bALTER\s+TABLE\b[\s\S]*\bDROP\s+(?:COLUMN|CONSTRAINT)\b", re.I), "ALTER DROP"),
)
_DANGEROUS_PATTERNS = (
    (re.compile(r"\bPRAGMA\s+writable_schema\b", re.I), "PRAGMA writable_schema"),
    (re.compile(r"\bATTACH\s+(?:DATABASE\s+)?", re.I), "ATTACH DATABASE"),
    (re.compile(r"\bDETACH\s+(?:DATABASE\s+)?", re.I), "DETACH DATABASE"),
    (re.compile(r"\bload_extension\s*\(", re.I), "load_extension"),
    (re.compile(r"\bINTO\s+OUTFILE\b", re.I), "INTO OUTFILE"),
    (re.compile(r"\bCOPY\b[\s\S]*\bPROGRAM\b", re.I), "COPY PROGRAM"),
    (
        re.compile(r"^\s*(?:CALL|EXEC(?:UTE)?|DO)\b", re.I),
        "ejecución procedural",
    ),
)
_MIGRATION_DIR_NAMES = {"migration", "migrations", "migrate"}
_MIGRATION_VERSION_PATTERNS = (
    re.compile(r"^[vV](\d+(?:[._-]\d+)*)__"),
    re.compile(r"^(\d+)(?:[_-].*)?\.sql$", re.I),
)
_SQLITE_HEADER = b"SQLite format 3\x00"


class SqlProjectInspectSkill:
    name = "sql.project_inspect"
    description = "Inspecciona SQL, migraciones y bases SQLite sin ejecutar consultas."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            context.config.sql_tool_timeout_seconds,
            ["inspect-sql-project", str(root)],
            "Solo se leen nombres, SQL y cabeceras SQLite; no se ejecutan consultas.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        inventory = _inspect_project(root, settings)
        return SkillResult(
            True,
            _format_inventory(inventory, authorization),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "inventory": inventory,
                "stage_status": "passed",
                "shell": False,
                **authorization,
            },
        )


class SqlStaticValidateSkill:
    name = "sql.static_validate"
    description = "Valida estructura SQL y bloquea mutaciones fuera de migraciones."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["validate-sql-static", settings["dialect"], str(root)],
            "El análisis es estático; DDL/DML fuera de migraciones se rechaza por defecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        started = time.perf_counter()
        files, truncated = _collect_sql_files(
            target,
            root=root,
            exclude_paths=settings["exclude_paths"],
            max_files=settings["max_sql_files"],
        )
        if truncated:
            return _file_limit_result(self.name, root, settings, authorization)
        if not files:
            return _skipped_result(
                self.name,
                root,
                "No se encontraron archivos SQL para validar.",
                authorization,
            )
        reports: list[dict[str, Any]] = []
        errors: list[str] = []
        warnings: list[str] = []
        statement_counts: dict[str, int] = {}
        for path in files:
            report = _validate_sql_file(
                path,
                root=root,
                dialect=settings["dialect"],
                allow_mutating=settings["allow_mutating_sql"] or _is_migration(path, root),
            )
            reports.append(report)
            for item in report["errors"]:
                errors.append(f"{report['relative_path']}: {item}")
            for item in report["warnings"]:
                warnings.append(f"{report['relative_path']}: {item}")
            for category, count in report["statement_counts"].items():
                statement_counts[category] = statement_counts.get(category, 0) + count
            if settings["fail_fast"] and report["errors"]:
                break
        duration_ms = round((time.perf_counter() - started) * 1000)
        ok = not errors
        message = _format_static_report(
            root,
            files=len(reports),
            errors=errors,
            warnings=warnings,
            statement_counts=statement_counts,
            duration_ms=duration_ms,
        )
        return SkillResult(
            ok,
            message,
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "reports": reports,
                "errors": errors[:100],
                "warnings": warnings[:100],
                "statement_counts": statement_counts,
                "duration_ms": duration_ms,
                "stage_status": "passed" if ok else "failed",
                "shell": False,
                **authorization,
            },
        )


class SqlMigrationValidateSkill:
    name = "sql.migration_validate"
    description = "Valida orden, versiones y operaciones destructivas de migraciones SQL."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["validate-sql-migrations", settings["dialect"], str(root)],
            "No se aplican migraciones; las operaciones destructivas se rechazan por defecto.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        started = time.perf_counter()
        files, truncated = _collect_sql_files(
            root,
            root=root,
            exclude_paths=settings["exclude_paths"],
            max_files=settings["max_sql_files"],
        )
        if truncated:
            return _file_limit_result(self.name, root, settings, authorization)
        migrations = [path for path in files if _is_migration(path, root)]
        if not migrations:
            return _skipped_result(
                self.name,
                root,
                "No se detectaron archivos de migración SQL.",
                authorization,
            )
        report = _validate_migrations(
            migrations,
            root=root,
            dialect=settings["dialect"],
            allow_destructive=settings["allow_destructive_migrations"],
        )
        duration_ms = round((time.perf_counter() - started) * 1000)
        ok = not report["errors"]
        lines = [
            "Migraciones SQL válidas." if ok else "Migraciones SQL con errores.",
            "",
            f"- Proyecto: `{root}`",
            f"- Migraciones: `{len(migrations)}`",
            f"- Versionadas: `{report['versioned']}`",
            f"- Destructivas: `{report['destructive_count']}`",
            f"- Errores: `{len(report['errors'])}`",
            f"- Advertencias: `{len(report['warnings'])}`",
            f"- Duración: `{duration_ms} ms`",
        ]
        _append_diagnostics(lines, report["errors"], report["warnings"])
        return SkillResult(
            ok,
            "\n".join(lines),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "report": report,
                "duration_ms": duration_ms,
                "stage_status": "passed" if ok else "failed",
                "shell": False,
                **authorization,
            },
        )


class SqliteSchemaInspectSkill:
    name = "sqlite.schema_inspect"
    description = "Inspecciona esquemas SQLite en modo solo lectura sin leer filas."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["inspect-sqlite-schema", "mode=ro", str(root)],
            "Solo se consulta sqlite_master y metadatos PRAGMA; no se leen filas de usuario.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        target = _resolve_existing_path(params)
        root = _discover_project_root(target)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        files, truncated = _collect_database_files(
            target,
            root=root,
            exclude_paths=settings["exclude_paths"],
            max_files=settings["max_database_files"],
        )
        if truncated:
            return SkillResult(
                False,
                "Se superó el límite de bases SQLite configurado.",
                {
                    "engine": "local-skill",
                    "generated": False,
                    "skill": self.name,
                    "project_root": str(root),
                    "stage_status": "failed",
                    "shell": False,
                    **authorization,
                },
            )
        if not files:
            return _skipped_result(
                self.name,
                root,
                "No se detectaron bases SQLite para inspeccionar.",
                authorization,
            )
        started = time.perf_counter()
        databases: list[dict[str, Any]] = []
        errors: list[str] = []
        for path in files:
            try:
                databases.append(_inspect_sqlite_database(path, root=root))
            except (OSError, sqlite3.Error, ValueError) as exc:
                errors.append(f"{path.relative_to(root).as_posix()}: {exc}")
        duration_ms = round((time.perf_counter() - started) * 1000)
        ok = not errors
        totals = {
            key: sum(int(item.get(key, 0)) for item in databases)
            for key in ("tables", "views", "indexes", "triggers", "columns")
        }
        lines = [
            "Esquemas SQLite inspeccionados." if ok else "Inspección SQLite con errores.",
            "",
            f"- Proyecto: `{root}`",
            f"- Bases: `{len(databases)}`",
            f"- Tablas: `{totals['tables']}`",
            f"- Vistas: `{totals['views']}`",
            f"- Índices: `{totals['indexes']}`",
            f"- Triggers: `{totals['triggers']}`",
            f"- Columnas: `{totals['columns']}`",
            f"- Errores: `{len(errors)}`",
            f"- Duración: `{duration_ms} ms`",
        ]
        if errors:
            lines.extend(("", "Errores:"))
            lines.extend(f"- {item}" for item in errors[:20])
        return SkillResult(
            ok,
            "\n".join(lines),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "databases": databases,
                "totals": totals,
                "errors": errors,
                "duration_ms": duration_ms,
                "stage_status": "passed" if ok else "failed",
                "shell": False,
                **authorization,
            },
        )


class SqliteQueryPlanSkill:
    name = "sqlite.query_plan"
    description = "Genera EXPLAIN QUERY PLAN para una consulta de solo lectura."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        database = _resolve_database_path(params)
        root = _discover_project_root(database)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            context.config.sql_tool_timeout_seconds,
            ["sqlite", "EXPLAIN QUERY PLAN", str(database)],
            "Solo se acepta una consulta SELECT/CTE y la base se abre con mode=ro.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        database = _resolve_database_path(params)
        root = _discover_project_root(database)
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        query = _resolve_query(params, root=root)
        validation = _validate_sql_text(
            query,
            dialect="sqlite",
            allow_mutating=False,
        )
        if validation["errors"]:
            return SkillResult(
                False,
                "La consulta no es apta para EXPLAIN QUERY PLAN: "
                + "; ".join(validation["errors"]),
                {
                    "engine": "local-skill",
                    "generated": False,
                    "skill": self.name,
                    "project_root": str(root),
                    "database": str(database),
                    "validation": validation,
                    "stage_status": "failed",
                    "shell": False,
                    **authorization,
                },
            )
        statements = validation["statements"]
        if len(statements) != 1 or statements[0]["category"] != "read":
            raise ValueError("EXPLAIN requiere exactamente una consulta SELECT o WITH.")
        started = time.perf_counter()
        with _open_sqlite_readonly(database) as connection:
            rows = connection.execute("EXPLAIN QUERY PLAN " + query).fetchall()
        duration_ms = round((time.perf_counter() - started) * 1000)
        plan = [
            {
                "id": int(row[0]),
                "parent": int(row[1]),
                "detail": _bounded_text(str(row[3]), 500),
            }
            for row in rows[:200]
        ]
        lines = [
            "Plan SQLite generado en modo solo lectura.",
            "",
            f"- Base: `{database}`",
            f"- Operaciones: `{len(plan)}`",
            f"- Duración: `{duration_ms} ms`",
            "",
            "Plan:",
        ]
        lines.extend(f"- {item['detail']}" for item in plan)
        return SkillResult(
            True,
            "\n".join(lines),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "database": str(database),
                "plan": plan,
                "duration_ms": duration_ms,
                "stage_status": "passed",
                "shell": False,
                **authorization,
            },
        )


class SqlVerifyProjectSkill:
    name = "sql.verify_project"
    description = "Ejecuta la verificación SQL segura y guarda historial comparable."
    risk = RiskLevel.MEDIUM

    def approval_details(
        self,
        context: SkillContext,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        root = _discover_project_root(_resolve_path(params))
        settings = _pipeline_settings(context, root, params)
        decision = context.authorization.project(root)
        return _approval_details(
            self.name,
            root,
            decision.scope.value,
            decision.source,
            settings["timeout_seconds"],
            ["sql-verify", *_enabled_stage_names(settings), str(root)],
            "No se aplican migraciones ni DDL/DML; SQLite se abre en modo solo lectura.",
        )

    def execute(self, context: SkillContext, params: dict[str, Any]) -> SkillResult:
        root = _discover_project_root(_resolve_existing_path(params))
        settings = _pipeline_settings(context, root, params)
        authorization = _authorize_project(context, root, params, settings=settings)
        started = time.perf_counter()
        profile = settings.get("profile") or {}
        run_id = context.verification_runs.start(
            toolchain="sql",
            project_root=root,
            actor=context.actor,
            profile_id=profile.get("id"),
            plan={
                "stages": _enabled_stage_names(settings),
                "dialect": settings["dialect"],
                "allow_mutating_sql": settings["allow_mutating_sql"],
                "allow_destructive_migrations": settings[
                    "allow_destructive_migrations"
                ],
                "database_mode": "read-only",
                "fail_fast": settings["fail_fast"],
            },
        )
        stage_specs = (
            ("inspect", True, SqlProjectInspectSkill()),
            ("static", settings["static_enabled"], SqlStaticValidateSkill()),
            (
                "migrations",
                settings["migrations_enabled"],
                SqlMigrationValidateSkill(),
            ),
            ("schema", settings["schema_enabled"], SqliteSchemaInspectSkill()),
        )
        stages: list[dict[str, Any]] = []
        for stage_name, enabled, skill in stage_specs:
            if not enabled:
                stages.append(
                    {
                        "name": stage_name,
                        "status": "skipped",
                        "message": "Etapa desactivada por configuración.",
                    }
                )
                continue
            result = skill.execute(context, dict(params))
            status = _stage_status(result)
            stages.append(
                {
                    "name": stage_name,
                    "status": status,
                    "message": _bounded_text(result.message.splitlines()[0], 240),
                    "duration_ms": result.data.get("duration_ms"),
                }
            )
            if settings["fail_fast"] and status == "failed":
                break
        status = _overall_status(stages)
        duration_ms = round((time.perf_counter() - started) * 1000)
        summary = {
            "stages": stages,
            "authorization": authorization,
            "dialect": settings["dialect"],
            "database_mode": "read-only",
        }
        run = context.verification_runs.finish(
            run_id,
            status=status,
            duration_ms=duration_ms,
            summary=summary,
        )
        heading = {
            "passed": "Verificación SQL correcta.",
            "partial": "Verificación SQL parcial.",
            "failed": "Verificación SQL fallida.",
        }[status]
        lines = [
            heading,
            "",
            f"- Proyecto: `{root}`",
            f"- Ejecución: `{run_id}`",
            f"- Estado: `{status}`",
            f"- Dialecto: `{settings['dialect']}`",
            "- SQLite: `solo lectura`",
            f"- Duración: `{duration_ms} ms`",
            "",
            "Etapas:",
        ]
        for stage in stages:
            suffix = f" — {stage['message']}" if stage.get("message") else ""
            lines.append(f"- {stage['name']}: `{stage['status']}`{suffix}")
        return SkillResult(
            status != "failed",
            "\n".join(lines),
            {
                "engine": "local-skill",
                "generated": False,
                "skill": self.name,
                "project_root": str(root),
                "verification_run": run,
                "stages": stages,
                "duration_ms": duration_ms,
                "shell": False,
                **authorization,
            },
        )


def _pipeline_settings(
    context: SkillContext,
    root: Path,
    params: dict[str, Any],
) -> dict[str, Any]:
    effective = context.sql_profiles.effective_settings(
        root,
        default_timeout_seconds=context.config.sql_tool_timeout_seconds,
        default_max_output_chars=context.config.sql_tool_max_output_chars,
    )
    profile = effective["profile"] or {}
    return {
        "profile": effective["profile"],
        "timeout_seconds": int(effective["timeout_seconds"]),
        "max_output_chars": int(effective["max_output_chars"]),
        "max_sql_files": _bounded_files(
            params.get("max_files"),
            int(effective["max_sql_files"]),
        ),
        "max_database_files": _bounded_database_files(
            params.get("max_database_files"),
            int(effective["max_database_files"]),
        ),
        "exclude_paths": list(effective["exclude_paths"]),
        "static_enabled": _setting(params, profile, "static_enabled", True),
        "migrations_enabled": _setting(
            params,
            profile,
            "migrations_enabled",
            True,
        ),
        "schema_enabled": _setting(params, profile, "schema_enabled", True),
        "dialect": _choice_setting(
            params.get("dialect"),
            profile.get("dialect", "auto"),
            _DIALECTS,
            "dialect",
        ),
        "allow_mutating_sql": _setting(
            params,
            profile,
            "allow_mutating_sql",
            False,
        ),
        "allow_destructive_migrations": _setting(
            params,
            profile,
            "allow_destructive_migrations",
            False,
        ),
        "fail_fast": _setting(params, profile, "fail_fast", False),
    }


def _resolve_path(params: dict[str, Any]) -> Path:
    raw = str(params.get("path", "")).strip()
    if not raw:
        raise ValueError("Falta el parámetro path.")
    return Path(raw).expanduser().resolve(strict=False)


def _resolve_existing_path(params: dict[str, Any]) -> Path:
    path = _resolve_path(params)
    if not path.exists():
        raise ValueError(f"La ruta no existe: {path}")
    mode = path.stat().st_mode
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise ValueError(f"La ruta no es un archivo o directorio regular: {path}")
    return path


def _resolve_database_path(params: dict[str, Any]) -> Path:
    raw = str(params.get("database", "")).strip()
    if not raw:
        raise ValueError("Falta el parámetro database.")
    path = Path(raw).expanduser().resolve(strict=True)
    if not path.is_file() or path.is_symlink():
        raise ValueError("La base SQLite debe ser un archivo regular, no un symlink.")
    if not _is_sqlite_file(path):
        raise ValueError(f"El archivo no contiene una base SQLite válida: {path}")
    return path


def _resolve_query(params: dict[str, Any], *, root: Path) -> str:
    direct = str(params.get("query", "")).strip()
    query_file = str(params.get("query_file", "")).strip()
    if bool(direct) == bool(query_file):
        raise ValueError("Indica exactamente uno de: query o query_file.")
    if direct:
        if len(direct) > 100_000:
            raise ValueError("La consulta supera 100000 caracteres.")
        return direct
    path = Path(query_file).expanduser().resolve(strict=True)
    if path != root and root not in path.parents:
        raise PermissionError("El archivo de consulta debe estar dentro del proyecto.")
    if path.suffix.casefold() != ".sql" or not path.is_file() or path.is_symlink():
        raise ValueError("query_file debe ser un archivo .sql regular dentro del proyecto.")
    if path.stat().st_size > 1_000_000:
        raise ValueError("El archivo de consulta supera 1 MB.")
    return path.read_text(encoding="utf-8")


def _discover_project_root(path: Path) -> Path:
    start = path if path.is_dir() else path.parent
    current = start.resolve(strict=False)
    while True:
        if any((current / marker).exists() for marker in _PROJECT_MARKERS):
            return current
        if current.parent == current:
            return start.resolve(strict=False)
        current = current.parent


def _authorize_project(
    context: SkillContext,
    root: Path,
    params: dict[str, Any],
    *,
    settings: dict[str, Any],
) -> dict[str, Any]:
    decision = context.authorization.project(
        root,
        allow_once=params.get("allow_root_once") is True,
        source=str(params.get("authorization_source") or "explicit_approval"),
    )
    if not decision.allowed:
        raise PermissionError(
            f"{decision.reason} Autorízalo solo para esta ejecución con --allow-root-once."
        )
    profile = settings.get("profile") or {}
    return {
        **decision.as_data(),
        "timeout_seconds": settings["timeout_seconds"],
        "project_profile_id": profile.get("id"),
        "project_profile_applied": bool(profile),
    }


def _collect_sql_files(
    target: Path,
    *,
    root: Path,
    exclude_paths: list[str],
    max_files: int,
) -> tuple[list[Path], bool]:
    if target.is_file():
        return ([target] if target.suffix.casefold() == _SQL_SUFFIX else []), False
    return _walk_files(
        target,
        root=root,
        exclude_paths=exclude_paths,
        max_files=max_files,
        predicate=lambda path: path.suffix.casefold() == _SQL_SUFFIX,
    )


def _collect_database_files(
    target: Path,
    *,
    root: Path,
    exclude_paths: list[str],
    max_files: int,
) -> tuple[list[Path], bool]:
    if target.is_file():
        return ([target] if _is_sqlite_file(target) else []), False
    return _walk_files(
        target,
        root=root,
        exclude_paths=exclude_paths,
        max_files=max_files,
        predicate=_is_sqlite_file,
    )


def _walk_files(
    target: Path,
    *,
    root: Path,
    exclude_paths: list[str],
    max_files: int,
    predicate: Any,
) -> tuple[list[Path], bool]:
    excluded = {
        (root / relative).resolve(strict=False)
        for relative in (*_DEFAULT_EXCLUDES, *exclude_paths)
    }
    files: list[Path] = []
    for current, directories, filenames in os.walk(target, followlinks=False):
        current_path = Path(current).resolve(strict=False)
        directories[:] = [
            name
            for name in directories
            if not _is_excluded((current_path / name).resolve(strict=False), excluded)
        ]
        for filename in sorted(filenames):
            raw_candidate = current_path / filename
            if raw_candidate.is_symlink():
                continue
            candidate = raw_candidate.resolve(strict=False)
            if candidate != root and root not in candidate.parents:
                continue
            if _is_excluded(candidate, excluded):
                continue
            try:
                mode = candidate.stat().st_mode
            except OSError:
                continue
            if not stat.S_ISREG(mode):
                continue
            try:
                matched = bool(predicate(candidate))
            except OSError:
                matched = False
            if not matched:
                continue
            files.append(candidate)
            if len(files) > max_files:
                return files, True
    files.sort(key=lambda item: item.relative_to(root).as_posix().casefold())
    return files, False


def _inspect_project(root: Path, settings: dict[str, Any]) -> dict[str, Any]:
    sql_files, sql_truncated = _collect_sql_files(
        root,
        root=root,
        exclude_paths=settings["exclude_paths"],
        max_files=settings["max_sql_files"],
    )
    database_files, database_truncated = _collect_database_files(
        root,
        root=root,
        exclude_paths=settings["exclude_paths"],
        max_files=settings["max_database_files"],
    )
    migrations = [path for path in sql_files if _is_migration(path, root)]
    dialects: dict[str, int] = {}
    for path in sql_files[:500]:
        dialect = _detect_dialect_from_path(path, root=root)
        dialects[dialect] = dialects.get(dialect, 0) + 1
    detected = _dominant_dialect(dialects)
    markers = _framework_markers(root)
    return {
        "project_root": str(root),
        "sql_files": len(sql_files),
        "migration_files": len(migrations),
        "sqlite_databases": len(database_files),
        "sql_truncated": sql_truncated,
        "database_truncated": database_truncated,
        "dialect": settings["dialect"] if settings["dialect"] != "auto" else detected,
        "dialect_counts": dialects,
        "frameworks": markers,
        "migration_directories": sorted(
            {
                str(path.parent.relative_to(root).as_posix())
                for path in migrations
            },
            key=str.casefold,
        )[:50],
        "databases": [
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "wal_present": path.with_name(path.name + "-wal").exists(),
            }
            for path in database_files[:50]
        ],
        "read_only_default": not settings["allow_mutating_sql"],
        "destructive_migrations_allowed": settings[
            "allow_destructive_migrations"
        ],
    }


def _validate_sql_file(
    path: Path,
    *,
    root: Path,
    dialect: str,
    allow_mutating: bool,
) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    if path.stat().st_size > 1_000_000:
        return {
            "relative_path": relative,
            "errors": ["El archivo supera 1 MB."],
            "warnings": [],
            "statements": [],
            "statement_counts": {},
        }
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {
            "relative_path": relative,
            "errors": ["El archivo no es UTF-8 válido."],
            "warnings": [],
            "statements": [],
            "statement_counts": {},
        }
    report = _validate_sql_text(
        text,
        dialect=_resolve_dialect(dialect, path=path, root=root),
        allow_mutating=allow_mutating,
    )
    return {"relative_path": relative, **report}


def _validate_sql_text(
    text: str,
    *,
    dialect: str,
    allow_mutating: bool,
) -> dict[str, Any]:
    cleaned, scan_errors = _clean_sql(text)
    statements = _split_statements(cleaned)
    errors = list(scan_errors)
    warnings: list[str] = []
    details: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for index, statement in enumerate(statements, start=1):
        normalized = statement.strip()
        if not normalized:
            continue
        keyword = _leading_keyword(normalized)
        category = _statement_category(keyword)
        counts[category] = counts.get(category, 0) + 1
        destructive = _matching_labels(normalized, _DESTRUCTIVE_PATTERNS)
        dangerous = _matching_labels(normalized, _DANGEROUS_PATTERNS)
        statement_errors: list[str] = []
        statement_warnings: list[str] = []
        if category == "mutating" and not allow_mutating:
            statement_errors.append(
                f"sentencia {index}: {keyword or 'SQL'} modifica esquema o datos"
            )
        if dangerous:
            statement_errors.append(
                f"sentencia {index}: operación sensible: {', '.join(dangerous)}"
            )
        if destructive:
            statement_warnings.append(
                f"sentencia {index}: operación destructiva: {', '.join(destructive)}"
            )
        if re.search(r"\bSELECT\s+\*\b", normalized, re.I):
            statement_warnings.append(f"sentencia {index}: SELECT * detectado")
        if dialect == "sqlite" and re.search(r"\bSERIAL\b", normalized, re.I):
            statement_warnings.append(
                f"sentencia {index}: SERIAL no es un tipo nativo de SQLite"
            )
        errors.extend(statement_errors)
        warnings.extend(statement_warnings)
        details.append(
            {
                "index": index,
                "keyword": keyword,
                "category": category,
                "destructive": destructive,
                "dangerous": dangerous,
            }
        )
    if text.strip() and not details and not errors:
        warnings.append("El archivo no contiene sentencias SQL terminadas o reconocibles.")
    return {
        "dialect": dialect,
        "errors": errors,
        "warnings": warnings,
        "statements": details,
        "statement_counts": counts,
    }


def _clean_sql(text: str) -> tuple[str, list[str]]:
    result = list(text)
    errors: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        following = text[index + 1] if index + 1 < length else ""
        if char == "-" and following == "-":
            end = text.find("\n", index + 2)
            end = length if end < 0 else end
            _blank_range(result, index, end)
            index = end
            continue
        if char == "#":
            end = text.find("\n", index + 1)
            end = length if end < 0 else end
            _blank_range(result, index, end)
            index = end
            continue
        if char == "/" and following == "*":
            end = text.find("*/", index + 2)
            if end < 0:
                errors.append("Comentario /* sin cierre.")
                _blank_range(result, index, length)
                break
            _blank_range(result, index, end + 2)
            index = end + 2
            continue
        if char in {"'", '"', "`"}:
            end, closed = _quoted_end(text, index, char)
            if not closed:
                errors.append(f"Cadena o identificador {char} sin cierre.")
                _blank_range(result, index, length)
                break
            _blank_range(result, index, end)
            index = end
            continue
        if char == "[":
            end = text.find("]", index + 1)
            if end < 0:
                errors.append("Identificador [ sin cierre ].")
                _blank_range(result, index, length)
                break
            _blank_range(result, index, end + 1)
            index = end + 1
            continue
        if char == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", text[index:])
            if match:
                marker = match.group(0)
                end = text.find(marker, index + len(marker))
                if end < 0:
                    errors.append(f"Bloque {marker} sin cierre.")
                    _blank_range(result, index, length)
                    break
                final = end + len(marker)
                _blank_range(result, index, final)
                index = final
                continue
        index += 1
    balance = 0
    for char in result:
        if char == "(":
            balance += 1
        elif char == ")":
            balance -= 1
            if balance < 0:
                errors.append("Paréntesis de cierre sin apertura.")
                balance = 0
    if balance:
        errors.append(f"Paréntesis sin cerrar: {balance}.")
    return "".join(result), errors


def _quoted_end(text: str, start: int, quote_char: str) -> tuple[int, bool]:
    index = start + 1
    while index < len(text):
        if text[index] == quote_char:
            if index + 1 < len(text) and text[index + 1] == quote_char:
                index += 2
                continue
            return index + 1, True
        if text[index] == "\\" and index + 1 < len(text):
            index += 2
            continue
        index += 1
    return len(text), False


def _blank_range(result: list[str], start: int, end: int) -> None:
    for index in range(start, min(end, len(result))):
        if result[index] not in {"\n", "\r"}:
            result[index] = " "


def _split_statements(cleaned: str) -> list[str]:
    statements: list[str] = []
    start = 0
    for index, char in enumerate(cleaned):
        if char == ";":
            statements.append(cleaned[start:index])
            start = index + 1
    tail = cleaned[start:]
    if tail.strip():
        statements.append(tail)
    return statements


def _leading_keyword(statement: str) -> str:
    match = re.match(r"\s*([A-Za-z]+)", statement)
    if not match:
        return ""
    keyword = match.group(1).upper()
    if keyword != "WITH":
        return keyword
    candidates = re.findall(
        r"\b(SELECT|INSERT|UPDATE|DELETE|MERGE)\b",
        statement,
        re.I,
    )
    mutating = [
        candidate.upper()
        for candidate in candidates
        if candidate.upper() in {"INSERT", "UPDATE", "DELETE", "MERGE"}
    ]
    if mutating:
        return mutating[0]
    return candidates[-1].upper() if candidates else "WITH"


def _statement_category(keyword: str) -> str:
    if keyword in {"SELECT", "WITH", "EXPLAIN", "SHOW", "DESCRIBE"}:
        return "read"
    if keyword in _MUTATING_KEYWORDS:
        return "mutating"
    if keyword in {"BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"}:
        return "transaction"
    if keyword in {"SET", "USE", "PRAGMA"}:
        return "control"
    return "other"


def _validate_migrations(
    migrations: list[Path],
    *,
    root: Path,
    dialect: str,
    allow_destructive: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    versions: dict[str, str] = {}
    destructive_count = 0
    versioned = 0
    files: list[dict[str, Any]] = []
    for path in migrations:
        relative = path.relative_to(root).as_posix()
        version = _migration_version(path.name)
        if version:
            versioned += 1
            if version in versions:
                errors.append(
                    f"Versión duplicada {version}: {versions[version]} y {relative}."
                )
            else:
                versions[version] = relative
        else:
            warnings.append(f"Migración sin versión reconocible: {relative}.")
        report = _validate_sql_file(
            path,
            root=root,
            dialect=dialect,
            allow_mutating=True,
        )
        for item in report["errors"]:
            errors.append(f"{relative}: {item}")
        destructive = [
            label
            for statement in report["statements"]
            for label in statement["destructive"]
        ]
        destructive_count += len(destructive)
        if destructive and not allow_destructive:
            errors.append(
                f"{relative}: operación destructiva no autorizada: "
                f"{', '.join(sorted(set(destructive)))}."
            )
        elif destructive:
            warnings.append(
                f"{relative}: contiene operaciones destructivas autorizadas."
            )
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            content = ""
        statement_total = sum(report["statement_counts"].values())
        if statement_total > 1 and not re.search(r"\bBEGIN\b", content, re.I):
            warnings.append(f"{relative}: múltiples sentencias sin BEGIN explícito.")
        if not content.strip() and not report["errors"]:
            errors.append(f"{relative}: migración vacía.")
        files.append(
            {
                "path": relative,
                "version": version,
                "destructive": sorted(set(destructive)),
                "statement_counts": report["statement_counts"],
            }
        )
    return {
        "files": files,
        "versioned": versioned,
        "destructive_count": destructive_count,
        "errors": errors,
        "warnings": warnings,
    }


def _inspect_sqlite_database(path: Path, *, root: Path) -> dict[str, Any]:
    if not _is_sqlite_file(path):
        raise ValueError("Cabecera SQLite inválida.")
    with _open_sqlite_readonly(path) as connection:
        rows = connection.execute(
            "SELECT type, name, tbl_name FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        objects = [
            {"type": str(row[0]), "name": str(row[1]), "table": str(row[2])}
            for row in rows
        ]
        tables = [item["name"] for item in objects if item["type"] == "table"]
        table_details: list[dict[str, Any]] = []
        columns_total = 0
        for table in tables[:200]:
            columns = connection.execute(
                'SELECT name, type, "notnull", dflt_value, pk '
                "FROM pragma_table_info(?) ORDER BY cid",
                (table,),
            ).fetchall()
            indexes = connection.execute(
                'SELECT name, "unique", origin, partial '
                "FROM pragma_index_list(?) ORDER BY seq",
                (table,),
            ).fetchall()
            foreign_keys = connection.execute(
                "SELECT \"table\", \"from\", \"to\", on_update, on_delete "
                "FROM pragma_foreign_key_list(?) ORDER BY id, seq",
                (table,),
            ).fetchall()
            columns_total += len(columns)
            table_details.append(
                {
                    "name": table,
                    "columns": [
                        {
                            "name": str(row[0]),
                            "type": str(row[1]),
                            "not_null": bool(row[2]),
                            "primary_key": bool(row[4]),
                        }
                        for row in columns
                    ],
                    "indexes": [
                        {"name": str(row[0]), "unique": bool(row[1])}
                        for row in indexes
                    ],
                    "foreign_keys": [
                        {
                            "table": str(row[0]),
                            "from": str(row[1]),
                            "to": str(row[2]),
                            "on_update": str(row[3]),
                            "on_delete": str(row[4]),
                        }
                        for row in foreign_keys
                    ],
                }
            )
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "tables": len(tables),
        "views": sum(item["type"] == "view" for item in objects),
        "indexes": sum(item["type"] == "index" for item in objects),
        "triggers": sum(item["type"] == "trigger" for item in objects),
        "columns": columns_total,
        "objects": objects[:500],
        "table_details": table_details,
        "rows_read": 0,
        "mode": "read-only",
    }


def _open_sqlite_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.set_authorizer(_sqlite_authorizer)
    return connection


def _sqlite_authorizer(
    action: int,
    arg1: str | None,
    arg2: str | None,
    database: str | None,
    source: str | None,
) -> int:
    del database, source
    denied = {
        value
        for name in (
            "SQLITE_ALTER_TABLE",
            "SQLITE_ANALYZE",
            "SQLITE_ATTACH",
            "SQLITE_CREATE_INDEX",
            "SQLITE_CREATE_TABLE",
            "SQLITE_CREATE_TEMP_INDEX",
            "SQLITE_CREATE_TEMP_TABLE",
            "SQLITE_CREATE_TEMP_TRIGGER",
            "SQLITE_CREATE_TEMP_VIEW",
            "SQLITE_CREATE_TRIGGER",
            "SQLITE_CREATE_VIEW",
            "SQLITE_DELETE",
            "SQLITE_DETACH",
            "SQLITE_DROP_INDEX",
            "SQLITE_DROP_TABLE",
            "SQLITE_DROP_TEMP_INDEX",
            "SQLITE_DROP_TEMP_TABLE",
            "SQLITE_DROP_TEMP_TRIGGER",
            "SQLITE_DROP_TEMP_VIEW",
            "SQLITE_DROP_TRIGGER",
            "SQLITE_DROP_VIEW",
            "SQLITE_INSERT",
            "SQLITE_REINDEX",
            "SQLITE_TRANSACTION",
            "SQLITE_UPDATE",
        )
        if (value := getattr(sqlite3, name, None)) is not None
    }
    if action in denied:
        return sqlite3.SQLITE_DENY
    if action == getattr(sqlite3, "SQLITE_FUNCTION", -1):
        function_name = str(arg2 or arg1 or "").casefold()
        if function_name == "load_extension":
            return sqlite3.SQLITE_DENY
    if action == getattr(sqlite3, "SQLITE_PRAGMA", -1):
        allowed_pragmas = {
            "foreign_key_list",
            "index_list",
            "table_info",
            "query_only",
        }
        return (
            sqlite3.SQLITE_OK
            if str(arg1 or "").casefold() in allowed_pragmas
            else sqlite3.SQLITE_DENY
        )
    return sqlite3.SQLITE_OK


def _is_sqlite_file(path: Path) -> bool:
    if path.suffix.casefold() not in _DATABASE_SUFFIXES:
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER
    except OSError:
        return False


def _is_migration(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    parts = {part.casefold() for part in relative.parts[:-1]}
    if parts & _MIGRATION_DIR_NAMES:
        return True
    name = path.name.casefold()
    return bool(
        re.match(r"^[vu]\d+(?:[._-]\d+)*__.+\.sql$", name)
        or re.match(r"^\d+[_-].+\.sql$", name)
        or name.endswith(".up.sql")
        or name.endswith(".down.sql")
    )


def _migration_version(filename: str) -> str:
    for pattern in _MIGRATION_VERSION_PATTERNS:
        match = pattern.match(filename)
        if match:
            return match.group(1).replace("_", ".").replace("-", ".")
    return ""


def _detect_dialect_from_path(path: Path, *, root: Path) -> str:
    relative = path.relative_to(root).as_posix().casefold()
    name = path.name.casefold()
    if "postgres" in relative or "pgsql" in relative:
        return "postgresql"
    if "mariadb" in relative:
        return "mariadb"
    if "mysql" in relative:
        return "mysql"
    if "sqlite" in relative or name.endswith(".sqlite.sql"):
        return "sqlite"
    try:
        text = path.read_text(encoding="utf-8")[:80_000]
    except (OSError, UnicodeDecodeError):
        return "generic"
    hints = {
        "postgresql": (r"\bSERIAL\b", r"\bRETURNING\b", r"\bPLPGSQL\b"),
        "mysql": (r"\bAUTO_INCREMENT\b", r"\bENGINE\s*=", r"`[^`]+`"),
        "sqlite": (r"\bWITHOUT\s+ROWID\b", r"\bPRAGMA\b", r"\bAUTOINCREMENT\b"),
    }
    scores = {
        dialect: sum(bool(re.search(pattern, text, re.I)) for pattern in patterns)
        for dialect, patterns in hints.items()
    }
    winner = max(scores, key=scores.get)
    return winner if scores[winner] else "generic"


def _dominant_dialect(counts: dict[str, int]) -> str:
    meaningful = {key: value for key, value in counts.items() if key != "generic"}
    if not meaningful:
        return "generic"
    return max(meaningful, key=meaningful.get)


def _resolve_dialect(dialect: str, *, path: Path, root: Path) -> str:
    return _detect_dialect_from_path(path, root=root) if dialect == "auto" else dialect


def _framework_markers(root: Path) -> list[str]:
    markers = {
        "Alembic": (root / "alembic.ini").exists() or (root / "alembic").is_dir(),
        "Flyway": (root / "flyway.conf").exists(),
        "Liquibase": (root / "liquibase.properties").exists(),
        "Prisma": (root / "prisma" / "schema.prisma").exists(),
        "Django": (root / "manage.py").exists(),
        "Laravel": (root / "artisan").exists(),
        "Rails": (root / "db" / "migrate").is_dir() and (root / "Gemfile").exists(),
    }
    return [name for name, present in markers.items() if present]


def _matching_labels(
    statement: str,
    patterns: tuple[tuple[re.Pattern[str], str], ...],
) -> list[str]:
    return [label for pattern, label in patterns if pattern.search(statement)]


def _is_excluded(path: Path, excluded: set[Path]) -> bool:
    return any(path == item or item in path.parents for item in excluded)


def _setting(
    params: dict[str, Any],
    profile: dict[str, Any],
    key: str,
    default: bool,
) -> bool:
    value = params.get(key)
    if value is not None:
        return value is True
    if key in profile:
        return bool(profile[key])
    return default


def _choice_setting(value: Any, current: Any, allowed: set[str], field: str) -> str:
    selected = str(value if value not in (None, "") else current).strip().casefold()
    if selected not in allowed:
        raise ValueError(f"{field} debe ser uno de: {', '.join(sorted(allowed))}.")
    return selected


def _bounded_files(value: Any, default: int) -> int:
    resolved = default if value in (None, "") else int(value)
    if not 1 <= resolved <= 20_000:
        raise ValueError("max_files debe estar entre 1 y 20000.")
    return resolved


def _bounded_database_files(value: Any, default: int) -> int:
    resolved = default if value in (None, "") else int(value)
    if not 1 <= resolved <= 200:
        raise ValueError("max_database_files debe estar entre 1 y 200.")
    return resolved


def _stage_status(result: SkillResult) -> str:
    status = str(result.data.get("stage_status") or "")
    if status in {"passed", "failed", "unavailable", "skipped"}:
        return status
    return "passed" if result.ok else "failed"


def _overall_status(stages: list[dict[str, Any]]) -> str:
    statuses = {str(stage["status"]) for stage in stages}
    if "failed" in statuses:
        return "failed"
    if "unavailable" in statuses:
        return "partial"
    return "passed"


def _enabled_stage_names(settings: dict[str, Any]) -> list[str]:
    stages = ["inspect"]
    for name, key in (
        ("static", "static_enabled"),
        ("migrations", "migrations_enabled"),
        ("schema", "schema_enabled"),
    ):
        if settings[key]:
            stages.append(name)
    return stages


def _approval_details(
    skill_name: str,
    root: Path,
    scope: str,
    source: str,
    timeout: int,
    argv: list[str],
    warning: str,
) -> dict[str, Any]:
    return {
        "approval_summary": f"{skill_name} sobre {root}",
        "tool": argv[0],
        "project_root": str(root),
        "authorization_scope": scope,
        "authorization_source": source,
        "timeout_seconds": timeout,
        "argv": argv,
        "risk_note": warning,
        "shell": False,
    }


def _file_limit_result(
    skill_name: str,
    root: Path,
    settings: dict[str, Any],
    authorization: dict[str, Any],
) -> SkillResult:
    return SkillResult(
        False,
        f"Se superó el límite de {settings['max_sql_files']} archivos SQL.",
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "stage_status": "failed",
            "shell": False,
            **authorization,
        },
    )


def _skipped_result(
    skill_name: str,
    root: Path,
    message: str,
    authorization: dict[str, Any],
) -> SkillResult:
    return SkillResult(
        True,
        message,
        {
            "engine": "local-skill",
            "generated": False,
            "skill": skill_name,
            "project_root": str(root),
            "stage_status": "skipped",
            "shell": False,
            **authorization,
        },
    )


def _format_inventory(
    inventory: dict[str, Any],
    authorization: dict[str, Any],
) -> str:
    frameworks = ", ".join(inventory["frameworks"]) or "no detectado"
    return "\n".join(
        (
            "Inspección SQL completada sin ejecutar consultas.",
            "",
            f"- Proyecto: `{inventory['project_root']}`",
            f"- Archivos SQL: `{inventory['sql_files']}`",
            f"- Migraciones: `{inventory['migration_files']}`",
            f"- Bases SQLite: `{inventory['sqlite_databases']}`",
            f"- Dialecto: `{inventory['dialect']}`",
            f"- Frameworks: `{frameworks}`",
            f"- DDL/DML fuera de migraciones: "
            f"`{'permitido' if not inventory['read_only_default'] else 'bloqueado'}`",
            f"- Autorización: `{authorization['authorization_scope']}`",
        )
    )


def _format_static_report(
    root: Path,
    *,
    files: int,
    errors: list[str],
    warnings: list[str],
    statement_counts: dict[str, int],
    duration_ms: int,
) -> str:
    lines = [
        "SQL estático válido." if not errors else "SQL estático con errores.",
        "",
        f"- Proyecto: `{root}`",
        f"- Archivos examinados: `{files}`",
        f"- Lectura: `{statement_counts.get('read', 0)}`",
        f"- Mutaciones: `{statement_counts.get('mutating', 0)}`",
        f"- Errores: `{len(errors)}`",
        f"- Advertencias: `{len(warnings)}`",
        f"- Duración: `{duration_ms} ms`",
    ]
    _append_diagnostics(lines, errors, warnings)
    return "\n".join(lines)


def _append_diagnostics(
    lines: list[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    if errors:
        lines.extend(("", "Errores:"))
        lines.extend(f"- {item}" for item in errors[:20])
    if warnings:
        lines.extend(("", "Advertencias:"))
        lines.extend(f"- {item}" for item in warnings[:20])


def _bounded_text(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"
