from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.db import Database


class VerificationRunRepository:
    """Persist bounded, non-secret verification summaries for any toolchain."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def start(
        self,
        *,
        toolchain: str,
        project_root: Path,
        actor: str,
        profile_id: int | None,
        plan: dict[str, Any],
    ) -> str:
        public_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO verification_runs(
                    public_id,
                    toolchain,
                    project_root,
                    profile_id,
                    status,
                    actor,
                    plan_json,
                    summary_json,
                    started_at,
                    completed_at,
                    duration_ms
                )
                VALUES (?, ?, ?, ?, 'running', ?, ?, '{}', ?, NULL, NULL)
                """,
                (
                    public_id,
                    toolchain,
                    str(project_root.resolve(strict=False)),
                    profile_id,
                    actor,
                    json.dumps(plan, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
        return public_id

    def finish(
        self,
        public_id: str,
        *,
        status: str,
        duration_ms: int,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE verification_runs
                SET status = ?, summary_json = ?, completed_at = ?, duration_ms = ?
                WHERE public_id = ?
                """,
                (
                    status,
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                    now,
                    int(duration_ms),
                    public_id,
                ),
            )
        item = self.get(public_id)
        if item is None:
            raise RuntimeError("No se pudo recuperar la ejecución de verificación.")
        return item

    def get(self, public_id: str) -> dict[str, Any] | None:
        clean = public_id.strip()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM verification_runs WHERE public_id = ?",
                (clean,),
            ).fetchone()
        return _public_run(dict(row)) if row is not None else None

    def list_recent(
        self,
        *,
        toolchain: str | None = None,
        project_root: Path | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if toolchain:
            clauses.append("toolchain = ?")
            values.append(toolchain)
        if project_root is not None:
            clauses.append("project_root = ?")
            values.append(str(project_root.expanduser().resolve(strict=False)))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 200)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM verification_runs
                {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                tuple(values),
            ).fetchall()
        return [_public_run(dict(row)) for row in rows]

    def count(self, *, toolchain: str | None = None) -> int:
        if toolchain:
            query = "SELECT COUNT(*) FROM verification_runs WHERE toolchain = ?"
            values: tuple[Any, ...] = (toolchain,)
        else:
            query = "SELECT COUNT(*) FROM verification_runs"
            values = ()
        with self.database.connect() as connection:
            return int(connection.execute(query, values).fetchone()[0])

    def compare(self, first_id: str, second_id: str) -> dict[str, Any]:
        first = self.get(first_id)
        second = self.get(second_id)
        if first is None or second is None:
            raise ValueError("No se encontraron ambas verificaciones solicitadas.")
        if first["toolchain"] != second["toolchain"]:
            raise ValueError("Solo se pueden comparar verificaciones del mismo toolchain.")
        if first["project_root"] != second["project_root"]:
            raise ValueError("Solo se pueden comparar verificaciones del mismo proyecto.")
        return {
            "toolchain": first["toolchain"],
            "first": first,
            "second": second,
            "status_changed": first["status"] != second["status"],
            "duration_delta_ms": _optional_delta(
                first.get("duration_ms"), second.get("duration_ms")
            ),
            "stage_changes": _stage_changes(
                first.get("summary", {}).get("stages", []),
                second.get("summary", {}).get("stages", []),
            ),
        }


def _public_run(item: dict[str, Any]) -> dict[str, Any]:
    item["plan"] = _json_object(item.pop("plan_json", "{}"))
    item["summary"] = _json_object(item.pop("summary_json", "{}"))
    return item


def _json_object(value: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _optional_delta(first: Any, second: Any) -> int | None:
    if first is None or second is None:
        return None
    return int(second) - int(first)


def _stage_changes(
    first_stages: list[dict[str, Any]],
    second_stages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    first_by_name = {
        str(item.get("name")): item for item in first_stages if item.get("name")
    }
    second_by_name = {
        str(item.get("name")): item for item in second_stages if item.get("name")
    }
    changes: list[dict[str, Any]] = []
    for name in sorted(set(first_by_name) | set(second_by_name)):
        before = first_by_name.get(name, {})
        after = second_by_name.get(name, {})
        changes.append(
            {
                "name": name,
                "before": before.get("status", "missing"),
                "after": after.get("status", "missing"),
                "changed": before.get("status") != after.get("status"),
            }
        )
    return changes
