from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.db import Database


class MemoryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def add(
        self,
        content: str,
        *,
        kind: str = "fact",
        project: str | None = None,
        source: str = "owner",
        confidence: float = 1.0,
    ) -> int:
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("El recuerdo no puede estar vacío.")
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO memories(
                    kind, content, project, source, confidence, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (kind.strip() or "fact", clean_content, project, source, confidence, now, now),
            )
            return int(cursor.lastrowid)

    def get(self, memory_id: int) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_active(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memories
                WHERE status = 'active'
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        clean_query = query.strip()
        if not clean_query:
            return []
        with self.database.connect() as connection:
            try:
                fts_enabled = connection.execute(
                    "SELECT value FROM schema_meta WHERE key = 'fts5'"
                ).fetchone()
                if fts_enabled and fts_enabled[0] == "enabled":
                    rows = connection.execute(
                        """
                        SELECT m.*, bm25(memory_fts) AS rank
                        FROM memory_fts
                        JOIN memories m ON m.id = memory_fts.rowid
                        WHERE memory_fts MATCH ? AND m.status = 'active'
                        ORDER BY rank
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
                SELECT * FROM memories
                WHERE status = 'active'
                  AND (content LIKE ? OR kind LIKE ? OR COALESCE(project, '') LIKE ?)
                ORDER BY id DESC
                LIMIT ?
                """,
                (like, like, like, max(1, limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def update(
        self,
        memory_id: int,
        *,
        content: str,
        kind: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("El recuerdo no puede estar vacío.")
        current = self.get(memory_id)
        if current is None or current["status"] != "active":
            raise ValueError("Recuerdo activo no encontrado.")
        clean_kind = (kind or str(current["kind"])).strip() or "fact"
        clean_project = project if project is not None else current["project"]
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE memories
                SET content = ?, kind = ?, project = ?, updated_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (clean_content, clean_kind, clean_project, now, memory_id),
            )
        updated = self.get(memory_id)
        assert updated is not None
        return updated

    def forget(self, memory_id: int) -> bool:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memories SET status = 'deleted', updated_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (now, memory_id),
            )
            return cursor.rowcount > 0


def _fts_query(query: str) -> str:
    words = [word.replace('"', "") for word in query.split() if word.replace('"', "")]
    return " AND ".join(f'"{word}"*' for word in words)


class ProjectRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def add(self, name: str, path: Path) -> int:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("El nombre del proyecto no puede estar vacío.")
        resolved = path.expanduser().resolve()
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO projects(name, path, created_at) VALUES (?, ?, ?)",
                (clean_name, str(resolved), now),
            )
            return int(cursor.lastrowid)

    def get(self, name: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE name = ? COLLATE NOCASE", (name.strip(),)
            ).fetchone()
        return dict(row) if row else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM projects ORDER BY name").fetchall()
        return [dict(row) for row in rows]
