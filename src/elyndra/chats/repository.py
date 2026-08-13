from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from elyndra.db import Database

_TRANSCRIPT_MODES = {"summary", "full"}
_MAX_SUMMARY_CHARS = 2400
_MAX_SUMMARY_LINES = 10


class ChatRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        title: str | None = None,
        project: str | None = None,
        transcript_mode: str = "summary",
    ) -> dict[str, Any]:
        mode = _validate_transcript_mode(transcript_mode)
        now = _now()
        public_id = f"chat_{uuid.uuid4().hex[:12]}"
        clean_title = _clean_title(title) or "Nuevo chat"
        clean_project = project.strip() if project and project.strip() else None
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO chats(
                    public_id, title, project, status, transcript_mode, turn_count,
                    created_at, updated_at, last_opened_at
                ) VALUES (?, ?, ?, 'active', ?, 0, ?, ?, ?)
                """,
                (public_id, clean_title, clean_project, mode, now, now, now),
            )
            chat_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO chat_summaries(chat_id, summary, updated_at)
                VALUES (?, '', ?)
                """,
                (chat_id, now),
            )
            self._refresh_fts(connection, chat_id)
        result = self.get(public_id)
        assert result is not None
        return result

    def get(self, identifier: str | int) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = self._find_row(connection, identifier)
            if row is None:
                return None
            summary_row = connection.execute(
                "SELECT summary FROM chat_summaries WHERE chat_id = ?",
                (int(row["id"]),),
            ).fetchone()
        result = dict(row)
        result["summary"] = str(summary_row["summary"]) if summary_row else ""
        return result

    def list_active(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.*, COALESCE(s.summary, '') AS summary
                FROM chats c
                LEFT JOIN chat_summaries s ON s.chat_id = c.id
                WHERE c.status = 'active'
                ORDER BY c.pinned DESC, c.updated_at DESC, c.id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_pinned(self, limit: int = 5) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.*, COALESCE(s.summary, '') AS summary
                FROM chats c
                LEFT JOIN chat_summaries s ON s.chat_id = c.id
                WHERE c.status = 'active' AND c.pinned = 1
                ORDER BY c.updated_at DESC, c.id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_pinned(self) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM chats WHERE status = 'active' AND pinned = 1"
            ).fetchone()
        return int(row["total"]) if row else 0

    def list_archived(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.*, COALESCE(s.summary, '') AS summary
                FROM chats c
                LEFT JOIN chat_summaries s ON s.chat_id = c.id
                WHERE c.status = 'archived'
                ORDER BY c.updated_at DESC, c.id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def touch(self, identifier: str | int) -> dict[str, Any]:
        now = _now()
        with self.database.connect() as connection:
            row = self._find_row(connection, identifier)
            if row is None:
                raise ValueError(f"Chat no encontrado: {identifier}")
            connection.execute(
                "UPDATE chats SET last_opened_at = ?, updated_at = ? WHERE id = ?",
                (now, now, int(row["id"])),
            )
        result = self.get(identifier)
        assert result is not None
        return result

    def append_turn(
        self,
        identifier: str | int,
        *,
        user_text: str,
        assistant_text: str,
    ) -> dict[str, Any]:
        clean_user = _single_line(user_text)
        clean_assistant = _single_line(assistant_text)
        if not clean_user or not clean_assistant:
            raise ValueError("El turno de chat no puede estar vacío.")
        now = _now()
        with self.database.connect() as connection:
            row = self._find_row(connection, identifier)
            if row is None:
                raise ValueError(f"Chat no encontrado: {identifier}")
            chat_id = int(row["id"])
            turn_index = int(row["turn_count"]) + 1
            if str(row["transcript_mode"]) == "full":
                connection.execute(
                    """
                    INSERT INTO chat_turns(
                        chat_id, turn_index, user_text, assistant_text, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (chat_id, turn_index, user_text.strip(), assistant_text.strip(), now),
                )
            summary_row = connection.execute(
                "SELECT summary FROM chat_summaries WHERE chat_id = ?", (chat_id,)
            ).fetchone()
            current_summary = str(summary_row["summary"]) if summary_row else ""
            summary = _updated_summary(
                current_summary,
                turn_index=turn_index,
                user_text=clean_user,
                assistant_text=clean_assistant,
            )
            connection.execute(
                """
                INSERT INTO chat_summaries(chat_id, summary, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    summary = excluded.summary,
                    updated_at = excluded.updated_at
                """,
                (chat_id, summary, now),
            )
            connection.execute(
                """
                UPDATE chats
                SET turn_count = ?, updated_at = ?, last_opened_at = ?
                WHERE id = ?
                """,
                (turn_index, now, now, chat_id),
            )
            self._refresh_fts(connection, chat_id)
        result = self.get(identifier)
        assert result is not None
        return result

    def summary(self, identifier: str | int) -> str:
        with self.database.connect() as connection:
            row = self._find_any_row(connection, identifier)
            if row is None:
                raise ValueError(f"Chat no encontrado: {identifier}")
            summary_row = connection.execute(
                "SELECT summary FROM chat_summaries WHERE chat_id = ?",
                (int(row["id"]),),
            ).fetchone()
        return str(summary_row["summary"]) if summary_row else ""

    def recent_turns(self, identifier: str | int, limit: int = 3) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            row = self._find_any_row(connection, identifier)
            if row is None:
                raise ValueError(f"Chat no encontrado: {identifier}")
            rows = connection.execute(
                """
                SELECT turn_index, user_text, assistant_text, created_at
                FROM chat_turns
                WHERE chat_id = ?
                ORDER BY turn_index DESC
                LIMIT ?
                """,
                (int(row["id"]), max(1, limit)),
            ).fetchall()
        return [dict(item) for item in reversed(rows)]

    def rename(self, identifier: str | int, title: str) -> dict[str, Any]:
        clean_title = _clean_title(title)
        if not clean_title:
            raise ValueError("El título del chat no puede estar vacío.")
        now = _now()
        with self.database.connect() as connection:
            row = self._find_row(connection, identifier)
            if row is None:
                raise ValueError(f"Chat no encontrado: {identifier}")
            chat_id = int(row["id"])
            connection.execute(
                "UPDATE chats SET title = ?, updated_at = ? WHERE id = ?",
                (clean_title, now, chat_id),
            )
            self._refresh_fts(connection, chat_id)
        result = self.get(identifier)
        assert result is not None
        return result

    def set_transcript_mode(
        self, identifier: str | int, transcript_mode: str
    ) -> dict[str, Any]:
        mode = _validate_transcript_mode(transcript_mode)
        now = _now()
        with self.database.connect() as connection:
            row = self._find_row(connection, identifier)
            if row is None:
                raise ValueError(f"Chat no encontrado: {identifier}")
            connection.execute(
                "UPDATE chats SET transcript_mode = ?, updated_at = ? WHERE id = ?",
                (mode, now, int(row["id"])),
            )
        result = self.get(identifier)
        assert result is not None
        return result

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        clean_query = query.strip()
        if not clean_query:
            return []
        with self.database.connect() as connection:
            try:
                fts_enabled = connection.execute(
                    "SELECT value FROM schema_meta WHERE key = 'chat_fts5'"
                ).fetchone()
                if fts_enabled and fts_enabled[0] == "enabled":
                    rows = connection.execute(
                        """
                        SELECT c.*, COALESCE(s.summary, '') AS summary,
                               bm25(chat_fts) AS rank
                        FROM chat_fts
                        JOIN chats c ON c.id = CAST(chat_fts.chat_id AS INTEGER)
                        LEFT JOIN chat_summaries s ON s.chat_id = c.id
                        WHERE chat_fts MATCH ? AND c.status = 'active'
                        ORDER BY c.pinned DESC, rank, c.updated_at DESC
                        LIMIT ?
                        """,
                        (_fts_query(clean_query), max(1, limit)),
                    ).fetchall()
                    return [dict(row) for row in rows]
            except sqlite3.OperationalError:
                pass

            like = f"%{clean_query}%"
            rows = connection.execute(
                """
                SELECT c.*, COALESCE(s.summary, '') AS summary
                FROM chats c
                LEFT JOIN chat_summaries s ON s.chat_id = c.id
                WHERE c.status = 'active'
                  AND (
                    c.public_id LIKE ? OR c.title LIKE ? OR
                    COALESCE(c.project, '') LIKE ? OR COALESCE(s.summary, '') LIKE ?
                  )
                ORDER BY c.pinned DESC, c.updated_at DESC
                LIMIT ?
                """,
                (like, like, like, like, max(1, limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_pinned(self, identifier: str | int, pinned: bool) -> dict[str, Any]:
        now = _now()
        with self.database.connect() as connection:
            row = self._find_row(connection, identifier)
            if row is None:
                raise ValueError(f"Chat no encontrado: {identifier}")
            chat_id = int(row["id"])
            connection.execute(
                "UPDATE chats SET pinned = ?, updated_at = ? WHERE id = ?",
                (1 if pinned else 0, now, chat_id),
            )
        result = self.get(identifier)
        assert result is not None
        return result

    def archive(self, identifier: str | int) -> dict[str, Any]:
        now = _now()
        with self.database.connect() as connection:
            row = self._find_row(connection, identifier)
            if row is None:
                raise ValueError(f"Chat no encontrado: {identifier}")
            chat_id = int(row["id"])
            connection.execute(
                """
                UPDATE chats
                SET status = 'archived', pinned = 0, updated_at = ?
                WHERE id = ?
                """,
                (now, chat_id),
            )
            self._delete_fts(connection, chat_id)
        result = self.get_any(identifier)
        assert result is not None
        return result

    def restore(self, identifier: str | int) -> dict[str, Any]:
        now = _now()
        with self.database.connect() as connection:
            row = self._find_any_row(connection, identifier)
            if row is None or str(row["status"]) != "archived":
                raise ValueError(f"Chat archivado no encontrado: {identifier}")
            chat_id = int(row["id"])
            connection.execute(
                "UPDATE chats SET status = 'active', updated_at = ? WHERE id = ?",
                (now, chat_id),
            )
            self._refresh_fts(connection, chat_id)
        result = self.get(identifier)
        assert result is not None
        return result

    def deletion_manifest(self, identifier: str | int) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = self._find_any_row(connection, identifier)
            if row is None:
                raise ValueError(f"Chat no encontrado: {identifier}")
            archives = connection.execute(
                "SELECT path FROM chat_archives WHERE chat_id = ?",
                (int(row["id"]),),
            ).fetchall()
            attachments = connection.execute(
                "SELECT stored_path FROM chat_attachments WHERE chat_id = ?",
                (int(row["id"]),),
            ).fetchall()
        return {
            "public_id": str(row["public_id"]),
            "title": str(row["title"]),
            "archive_paths": [str(item["path"]) for item in archives],
            "attachment_paths": [str(item["stored_path"]) for item in attachments],
        }

    def hard_delete(self, identifier: str | int) -> dict[str, Any]:
        manifest = self.deletion_manifest(identifier)
        with self.database.connect() as connection:
            row = self._find_any_row(connection, identifier)
            assert row is not None
            chat_id = int(row["id"])
            self._delete_fts(connection, chat_id)
            connection.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        return manifest

    def get_any(self, identifier: str | int) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = self._find_any_row(connection, identifier)
            if row is None:
                return None
            summary_row = connection.execute(
                "SELECT summary FROM chat_summaries WHERE chat_id = ?",
                (int(row["id"]),),
            ).fetchone()
        result = dict(row)
        result["summary"] = str(summary_row["summary"]) if summary_row else ""
        return result

    def forget(self, identifier: str | int) -> bool:
        now = _now()
        with self.database.connect() as connection:
            row = self._find_row(connection, identifier)
            if row is None:
                return False
            chat_id = int(row["id"])
            cursor = connection.execute(
                """
                UPDATE chats SET status = 'deleted', updated_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (now, chat_id),
            )
            if cursor.rowcount:
                self._delete_fts(connection, chat_id)
            return cursor.rowcount > 0

    @staticmethod
    def _find_any_row(
        connection: sqlite3.Connection, identifier: str | int
    ) -> sqlite3.Row | None:
        clean = str(identifier).strip()
        if not clean:
            return None
        if clean.isdigit():
            return connection.execute(
                "SELECT * FROM chats WHERE id = ? AND status != 'deleted'",
                (int(clean),),
            ).fetchone()
        return connection.execute(
            "SELECT * FROM chats WHERE public_id = ? AND status != 'deleted'",
            (clean,),
        ).fetchone()

    @staticmethod
    def _find_row(
        connection: sqlite3.Connection, identifier: str | int
    ) -> sqlite3.Row | None:
        clean = str(identifier).strip()
        if not clean:
            return None
        if clean.isdigit():
            return connection.execute(
                "SELECT * FROM chats WHERE id = ? AND status = 'active'", (int(clean),)
            ).fetchone()
        return connection.execute(
            "SELECT * FROM chats WHERE public_id = ? AND status = 'active'", (clean,)
        ).fetchone()

    @staticmethod
    def _delete_fts(connection: sqlite3.Connection, chat_id: int) -> None:
        try:
            connection.execute("DELETE FROM chat_fts WHERE chat_id = ?", (str(chat_id),))
        except sqlite3.OperationalError:
            return

    def _refresh_fts(self, connection: sqlite3.Connection, chat_id: int) -> None:
        try:
            self._delete_fts(connection, chat_id)
            row = connection.execute(
                """
                SELECT c.id, c.public_id, c.title, COALESCE(c.project, '') AS project,
                       COALESCE(s.summary, '') || ' ' ||
                       COALESCE(ms.topics_json, '') || ' ' ||
                       COALESCE(ms.decisions_json, '') || ' ' ||
                       COALESCE(ms.pending_json, '') || ' ' ||
                       COALESCE(ms.outcomes_json, '') AS summary
                FROM chats c
                LEFT JOIN chat_summaries s ON s.chat_id = c.id
                LEFT JOIN chat_memory_state ms ON ms.chat_id = c.id
                WHERE c.id = ? AND c.status = 'active'
                """,
                (chat_id,),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                """
                INSERT INTO chat_fts(chat_id, public_id, title, project, summary)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(row["id"]),
                    row["public_id"],
                    row["title"],
                    row["project"],
                    row["summary"],
                ),
            )
        except sqlite3.OperationalError:
            return


def _validate_transcript_mode(value: str) -> str:
    clean = value.strip().casefold()
    if clean not in _TRANSCRIPT_MODES:
        raise ValueError("El modo de transcripción debe ser 'summary' o 'full'.")
    return clean


def _clean_title(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.strip().split())[:120]


def _single_line(value: str) -> str:
    return " ".join(value.strip().split())


def _updated_summary(
    current: str,
    *,
    turn_index: int,
    user_text: str,
    assistant_text: str,
) -> str:
    line = (
        f"{turn_index}. Usuario: {_truncate(user_text, 180)} | "
        f"Asistente: {_truncate(assistant_text, 260)}"
    )
    lines = [item for item in current.splitlines() if item.strip()]
    lines.append(line)
    lines = lines[-_MAX_SUMMARY_LINES:]
    while lines and len("\n".join(lines)) > _MAX_SUMMARY_CHARS:
        lines.pop(0)
    return "\n".join(lines)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


def _fts_query(query: str) -> str:
    words = [word.replace('"', "") for word in query.split() if word.replace('"', "")]
    return " OR ".join(f'"{word}"*' for word in words)


def _now() -> str:
    return datetime.now(UTC).isoformat()
