from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from elyndra.db import Database

_SESSION_STATUSES = {"active", "completed", "needs_attention", "closed"}


class DevelopmentSessionRepository:
    """Group reviewed changes, validations and repairs into one supervised timeline."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def start(
        self,
        *,
        root_change_proposal_id: str,
        project_root: str,
        objective: str,
        actor: str,
        chat_id: str | None = None,
    ) -> str:
        existing = self.find_by_change(root_change_proposal_id)
        if existing is not None:
            public_id = str(existing["public_id"])
            if chat_id:
                self.focus(chat_id, public_id, actor=actor)
            return public_id
        public_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_development_sessions(
                    public_id, root_change_proposal_id, current_change_proposal_id,
                    current_validation_cycle_id, chat_id, project_root, objective,
                    status, actor, created_at, updated_at, closed_at
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, 'active', ?, ?, ?, NULL)
                """,
                (
                    public_id,
                    root_change_proposal_id.strip(),
                    root_change_proposal_id.strip(),
                    chat_id,
                    project_root,
                    objective.strip()[:4000],
                    actor,
                    now,
                    now,
                ),
            )
            self._insert_event(
                connection,
                session_id=public_id,
                event_type="change_proposed",
                entity_type="change_proposal",
                entity_id=root_change_proposal_id,
                status="proposed",
                summary="Propuesta inicial creada y pendiente de revisión.",
                payload={},
                created_at=now,
            )
            if chat_id:
                self._focus_connection(
                    connection,
                    chat_id=chat_id,
                    session_id=public_id,
                    actor=actor,
                    selected_at=now,
                )
        return public_id

    def get(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_development_sessions WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
            if row is None:
                return None
            events = connection.execute(
                """
                SELECT event_type, entity_type, entity_id, status, summary,
                       payload_json, created_at
                FROM assistant_development_session_events
                WHERE session_id = ? ORDER BY id ASC
                """,
                (public_id.strip(),),
            ).fetchall()
        item = dict(row)
        item["events"] = [_public_event(dict(event)) for event in events]
        return item

    def list_recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM assistant_development_sessions
                ORDER BY id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_for_chat(
        self,
        chat_id: str,
        *,
        include_closed: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        condition = "" if include_closed else "AND status != 'closed'"
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM assistant_development_sessions
                WHERE chat_id = ? {condition}
                ORDER BY id DESC LIMIT ?
                """,
                (chat_id.strip(), max(1, min(int(limit), 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def focus(
        self,
        chat_id: str,
        public_id: str,
        *,
        actor: str,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT public_id FROM assistant_development_sessions
                WHERE public_id = ? AND actor = ?
                """,
                (public_id.strip(), actor),
            ).fetchone()
            if row is None:
                raise ValueError("La sesión no existe para el propietario actual.")
            self._focus_connection(
                connection,
                chat_id=chat_id,
                session_id=public_id,
                actor=actor,
                selected_at=now,
            )
        item = self.get(public_id)
        if item is None:
            raise RuntimeError("No se pudo recuperar la sesión enfocada.")
        return item

    def focused_for_chat(
        self,
        chat_id: str,
        *,
        actor: str,
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT s.* FROM assistant_chat_session_focus AS f
                JOIN assistant_development_sessions AS s
                  ON s.public_id = f.development_session_id
                WHERE f.chat_id = ? AND f.actor = ? AND s.actor = ?
                LIMIT 1
                """,
                (chat_id.strip(), actor, actor),
            ).fetchone()
        return dict(row) if row is not None else None

    def resolve_for_chat(
        self,
        chat_id: str,
        *,
        actor: str,
    ) -> dict[str, Any] | None:
        focused = self.focused_for_chat(chat_id, actor=actor)
        if focused is not None and focused.get("status") != "closed":
            return self.get(str(focused["public_id"]))
        if focused is not None:
            self.clear_focus(chat_id, actor=actor)
        recent = self.list_for_chat(chat_id, include_closed=False, limit=1)
        if not recent:
            return None
        return self.focus(
            chat_id,
            str(recent[0]["public_id"]),
            actor=actor,
        )

    def clear_focus(self, chat_id: str, *, actor: str) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM assistant_chat_session_focus WHERE chat_id = ? AND actor = ?",
                (chat_id.strip(), actor),
            )
        return cursor.rowcount == 1

    def count(self, *, status: str | None = None) -> int:
        with self.database.connect() as connection:
            if status is None:
                row = connection.execute(
                    "SELECT COUNT(*) FROM assistant_development_sessions"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) FROM assistant_development_sessions WHERE status = ?",
                    (status,),
                ).fetchone()
        return int(row[0])

    def find_by_change(self, proposal_id: str) -> dict[str, Any] | None:
        return self._find_by_entity("change_proposal", proposal_id)

    def find_by_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        return self._find_by_entity("validation_cycle", cycle_id)

    def record_change(
        self,
        proposal_id: str,
        *,
        outcome: str,
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        session = self.find_by_change(proposal_id)
        if session is None:
            return None
        status = {
            "applied": "active",
            "rejected": "needs_attention",
            "stale": "needs_attention",
            "failed": "needs_attention",
        }.get(outcome, "active")
        event_type = f"change_{outcome}"
        return self._record(
            str(session["public_id"]),
            event_type=event_type,
            entity_type="change_proposal",
            entity_id=proposal_id,
            entity_status=outcome,
            summary=summary,
            payload=payload or {},
            session_status=status,
            current_change_id=proposal_id,
        )

    def record_validation_proposed(self, cycle: dict[str, Any]) -> dict[str, Any] | None:
        source_id = str(cycle.get("source_change_proposal_id", ""))
        session = self.find_by_change(source_id)
        if session is None:
            return None
        cycle_id = str(cycle.get("public_id", ""))
        return self._record(
            str(session["public_id"]),
            event_type="validation_proposed",
            entity_type="validation_cycle",
            entity_id=cycle_id,
            entity_status="validation_proposed",
            summary="Plan de validación congelado y pendiente de aprobación.",
            payload={"plan": cycle.get("plan", {})},
            session_status="active",
            current_cycle_id=cycle_id,
        )

    def record_validation_completed(self, cycle: dict[str, Any]) -> dict[str, Any] | None:
        cycle_id = str(cycle.get("public_id", ""))
        session = self.find_by_cycle(cycle_id)
        if session is None:
            return None
        cycle_status = str(cycle.get("status", "validation_failed"))
        session_status = (
            "completed" if cycle_status == "validation_passed" else "needs_attention"
        )
        return self._record(
            str(session["public_id"]),
            event_type=cycle_status,
            entity_type="validation_cycle",
            entity_id=cycle_id,
            entity_status=cycle_status,
            summary=(
                "La validación terminó correctamente."
                if cycle_status == "validation_passed"
                else "La validación terminó con fallos o resultados parciales."
            ),
            payload={"validation_result": cycle.get("validation_result", {})},
            session_status=session_status,
            current_cycle_id=cycle_id,
        )

    def record_repair_proposed(
        self,
        *,
        cycle_id: str,
        repair_proposal_id: str,
    ) -> dict[str, Any] | None:
        session = self.find_by_cycle(cycle_id)
        if session is None:
            return None
        return self._record(
            str(session["public_id"]),
            event_type="repair_proposed",
            entity_type="change_proposal",
            entity_id=repair_proposal_id,
            entity_status="proposed",
            summary="Nueva reparación propuesta; requiere revisión y aprobación separadas.",
            payload={"validation_cycle_id": cycle_id},
            session_status="active",
            current_change_id=repair_proposal_id,
            current_cycle_id=cycle_id,
        )

    def close(self, public_id: str, *, actor: str) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE assistant_development_sessions
                SET status = 'closed', updated_at = ?, closed_at = ?
                WHERE public_id = ? AND actor = ? AND status != 'closed'
                """,
                (now, now, public_id.strip(), actor),
            )
            if cursor.rowcount != 1:
                raise ValueError("La sesión no existe o ya está cerrada.")
            self._insert_event(
                connection,
                session_id=public_id,
                event_type="session_closed",
                entity_type="session",
                entity_id=public_id,
                status="closed",
                summary="Sesión cerrada explícitamente por el propietario.",
                payload={},
                created_at=now,
            )
            connection.execute(
                "DELETE FROM assistant_chat_session_focus "
                "WHERE development_session_id = ? AND actor = ?",
                (public_id.strip(), actor),
            )
        item = self.get(public_id)
        if item is None:
            raise RuntimeError("No se pudo recuperar la sesión cerrada.")
        return item

    def _find_by_entity(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT s.* FROM assistant_development_sessions AS s
                JOIN assistant_development_session_events AS e
                  ON e.session_id = s.public_id
                WHERE e.entity_type = ? AND e.entity_id = ?
                ORDER BY e.id DESC LIMIT 1
                """,
                (entity_type, entity_id.strip()),
            ).fetchone()
        return dict(row) if row is not None else None

    def _record(
        self,
        session_id: str,
        *,
        event_type: str,
        entity_type: str,
        entity_id: str,
        entity_status: str,
        summary: str,
        payload: dict[str, Any],
        session_status: str,
        current_change_id: str | None = None,
        current_cycle_id: str | None = None,
    ) -> dict[str, Any]:
        if session_status not in _SESSION_STATUSES:
            raise ValueError("Estado de sesión inválido.")
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            self._insert_event(
                connection,
                session_id=session_id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                status=entity_status,
                summary=summary,
                payload=payload,
                created_at=now,
            )
            connection.execute(
                """
                UPDATE assistant_development_sessions
                SET status = ?,
                    current_change_proposal_id = COALESCE(?, current_change_proposal_id),
                    current_validation_cycle_id = COALESCE(?, current_validation_cycle_id),
                    updated_at = ?, closed_at = NULL
                WHERE public_id = ? AND status != 'closed'
                """,
                (
                    session_status,
                    current_change_id,
                    current_cycle_id,
                    now,
                    session_id,
                ),
            )
        item = self.get(session_id)
        if item is None:
            raise RuntimeError("No se pudo recuperar la sesión actualizada.")
        return item

    @staticmethod
    def _focus_connection(
        connection: Any,
        *,
        chat_id: str,
        session_id: str,
        actor: str,
        selected_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO assistant_chat_session_focus(
                chat_id, development_session_id, actor, selected_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                development_session_id = excluded.development_session_id,
                actor = excluded.actor,
                selected_at = excluded.selected_at,
                updated_at = excluded.updated_at
            """,
            (
                chat_id.strip(),
                session_id.strip(),
                actor,
                selected_at,
                selected_at,
            ),
        )

    @staticmethod
    def _insert_event(
        connection: Any,
        *,
        session_id: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        status: str,
        summary: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO assistant_development_session_events(
                session_id, event_type, entity_type, entity_id, status,
                summary, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id.strip(),
                event_type,
                entity_type,
                entity_id.strip(),
                status,
                summary[:1000],
                _encoded_payload(payload),
                created_at,
            ),
        )


def _encoded_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(encoded) <= 18_000:
        return encoded
    return json.dumps(
        {"truncated": True, "preview": encoded[:17_000]},
        ensure_ascii=False,
        sort_keys=True,
    )


def _public_event(item: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(str(item.pop("payload_json", "{}")))
    except json.JSONDecodeError:
        payload = {}
    item["payload"] = payload if isinstance(payload, dict) else {}
    return item
