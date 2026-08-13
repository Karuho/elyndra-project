from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from elyndra.db import Database

_SECRET_FRAGMENTS = ("password", "passwd", "secret", "token", "api_key", "private_key")


class AuditRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def record(
        self,
        *,
        actor: str,
        action: str,
        outcome: str,
        target: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> int:
        safe_details = _redact(details or {})
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO audit_events(created_at, actor, action, target, outcome, details_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    actor,
                    action,
                    target,
                    outcome,
                    json.dumps(safe_details, ensure_ascii=False, sort_keys=True),
                ),
            )
            return int(cursor.lastrowid)

    def list_recent(
        self,
        limit: int = 20,
        *,
        action: str | None = None,
        target: str | None = None,
        outcome: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if action:
            clauses.append("action = ?")
            values.append(action)
        if target:
            clauses.append("target = ?")
            values.append(target)
        if outcome:
            clauses.append("outcome = ?")
            values.append(outcome)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM audit_events{where} ORDER BY id DESC LIMIT ?",
                tuple(values),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, event_id: int) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM audit_events WHERE id = ?", (event_id,)
            ).fetchone()
        return dict(row) if row is not None else None


def _redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(fragment in lowered for fragment in _SECRET_FRAGMENTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value
