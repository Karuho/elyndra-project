from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.config import AppConfig
from elyndra.db import Database
from elyndra.path_safety import ensure_allowed


class KnowledgeRepository:
    def __init__(self, database: Database, config: AppConfig) -> None:
        self.database = database
        self.config = config

    def import_file(
        self,
        path: Path,
        *,
        title: str | None = None,
        project: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        resolved = ensure_allowed(path, self.config.allowed_roots)
        if not resolved.is_file():
            raise ValueError(f"No es un archivo: {resolved}")

        suffix = resolved.suffix.casefold()
        if suffix not in self.config.knowledge_allowed_extensions:
            allowed = ", ".join(self.config.knowledge_allowed_extensions)
            raise ValueError(
                f"Extensión no permitida para conocimiento: {suffix}. "
                f"Permitidas: {allowed}"
            )

        max_bytes = self.config.knowledge_max_file_size_mb * 1024 * 1024
        size_bytes = resolved.stat().st_size
        if size_bytes > max_bytes:
            raise ValueError(
                f"El archivo pesa {size_bytes / 1024**2:.2f} MB; máximo permitido: "
                f"{self.config.knowledge_max_file_size_mb} MB."
            )

        raw = resolved.read_bytes()
        if b"\x00" in raw[:8192]:
            raise ValueError("El archivo parece binario y no se importará como texto.")
        digest = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip():
            raise ValueError("El archivo no contiene texto importable.")

        clean_title = (title or resolved.name).strip() or resolved.name
        clean_project = project.strip() if project and project.strip() else None
        chunks = _chunk_text(
            text,
            chunk_size=self.config.knowledge_chunk_size_chars,
            overlap=self.config.knowledge_chunk_overlap_chars,
        )
        now = datetime.now(UTC).isoformat()

        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM documents WHERE source_path = ?",
                (str(resolved),),
            ).fetchone()
            if (
                existing
                and not force
                and existing["sha256"] == digest
                and existing["status"] == "active"
            ):
                count = connection.execute(
                    "SELECT COUNT(*) FROM document_chunks WHERE document_id = ?",
                    (existing["id"],),
                ).fetchone()[0]
                return {
                    "document_id": int(existing["id"]),
                    "status": "unchanged",
                    "title": existing["title"],
                    "source_path": str(resolved),
                    "sha256": digest,
                    "size_bytes": size_bytes,
                    "chunks": int(count),
                }

            if existing:
                document_id = int(existing["id"])
                connection.execute(
                    """
                    UPDATE documents
                    SET title = ?, source_type = ?, project = ?, sha256 = ?, size_bytes = ?,
                        status = 'active', updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        clean_title,
                        suffix.lstrip(".") or "text",
                        clean_project,
                        digest,
                        size_bytes,
                        now,
                        document_id,
                    ),
                )
                connection.execute(
                    "DELETE FROM document_chunks WHERE document_id = ?",
                    (document_id,),
                )
                if _knowledge_fts_enabled(connection):
                    connection.execute(
                        "DELETE FROM document_fts WHERE document_id = ?",
                        (document_id,),
                    )
                import_status = "updated"
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO documents(
                        title, source_path, source_type, project, sha256, size_bytes,
                        status, imported_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        clean_title,
                        str(resolved),
                        suffix.lstrip(".") or "text",
                        clean_project,
                        digest,
                        size_bytes,
                        now,
                        now,
                    ),
                )
                document_id = int(cursor.lastrowid)
                import_status = "imported"

            fts_enabled = _knowledge_fts_enabled(connection)
            for index, chunk in enumerate(chunks):
                cursor = connection.execute(
                    """
                    INSERT INTO document_chunks(
                        document_id, chunk_index, content, char_start, char_end
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        index,
                        chunk["content"],
                        chunk["char_start"],
                        chunk["char_end"],
                    ),
                )
                chunk_id = int(cursor.lastrowid)
                if fts_enabled:
                    connection.execute(
                        """
                        INSERT INTO document_fts(chunk_id, document_id, title, project, content)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            chunk_id,
                            document_id,
                            clean_title,
                            clean_project or "",
                            chunk["content"],
                        ),
                    )

        return {
            "document_id": document_id,
            "status": import_status,
            "title": clean_title,
            "source_path": str(resolved),
            "sha256": digest,
            "size_bytes": size_bytes,
            "chunks": len(chunks),
        }

    def list_active(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT d.*,
                       (SELECT COUNT(*) FROM document_chunks c WHERE c.document_id = d.id) AS chunks
                FROM documents d
                WHERE d.status = 'active'
                ORDER BY d.updated_at DESC, d.id DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, document_id: int) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
        return dict(row) if row else None

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        clean_query = query.strip()
        if not clean_query:
            return []
        safe_limit = max(1, min(limit, self.config.max_search_results))

        with self.database.connect() as connection:
            if _knowledge_fts_enabled(connection):
                try:
                    rows = connection.execute(
                        """
                        SELECT
                            d.id AS document_id,
                            c.id AS chunk_id,
                            d.title,
                            d.source_path,
                            d.project,
                            c.chunk_index,
                            c.content,
                            bm25(document_fts) AS rank
                        FROM document_fts
                        JOIN document_chunks c ON c.id = CAST(document_fts.chunk_id AS INTEGER)
                        JOIN documents d ON d.id = c.document_id
                        WHERE document_fts MATCH ? AND d.status = 'active'
                        ORDER BY rank, d.id DESC, c.chunk_index
                        LIMIT ?
                        """,
                        (_fts_query(clean_query), safe_limit),
                    ).fetchall()
                    return [_result_row(row, clean_query) for row in rows]
                except sqlite3.OperationalError:
                    pass

            like = f"%{clean_query}%"
            rows = connection.execute(
                """
                SELECT
                    d.id AS document_id,
                    c.id AS chunk_id,
                    d.title,
                    d.source_path,
                    d.project,
                    c.chunk_index,
                    c.content,
                    0.0 AS rank
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE d.status = 'active'
                  AND (c.content LIKE ? OR d.title LIKE ? OR COALESCE(d.project, '') LIKE ?)
                ORDER BY d.id DESC, c.chunk_index
                LIMIT ?
                """,
                (like, like, like, safe_limit),
            ).fetchall()
        return [_result_row(row, clean_query) for row in rows]

    def forget(self, document_id: int) -> bool:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE documents SET status = 'deleted', updated_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (now, document_id),
            )
            if cursor.rowcount and _knowledge_fts_enabled(connection):
                connection.execute("DELETE FROM document_fts WHERE document_id = ?", (document_id,))
            return cursor.rowcount > 0


def _knowledge_fts_enabled(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key = 'knowledge_fts5'"
    ).fetchone()
    return bool(row and row[0] == "enabled")


def _fts_query(query: str) -> str:
    words = [word.replace('"', "") for word in query.split() if word.replace('"', "")]
    return " AND ".join(f'"{word}"*' for word in words)


def _result_row(row: sqlite3.Row, query: str) -> dict[str, Any]:
    item = dict(row)
    content = str(item["content"]).strip()
    item["excerpt"] = _focused_excerpt(content, query)
    return item


def _focused_excerpt(content: str, query: str, *, max_chars: int = 600) -> str:
    if len(content) <= max_chars:
        return content

    folded = content.casefold()
    terms = [term.casefold() for term in query.split() if term.strip()]
    positions = [folded.find(term) for term in terms]
    matches = [position for position in positions if position >= 0]
    center = min(matches) if matches else 0

    start = max(0, center - max_chars // 3)
    end = min(len(content), start + max_chars)
    if end - start < max_chars:
        start = max(0, end - max_chars)

    if start > 0:
        next_space = content.find(" ", start, min(end, start + 80))
        if next_space >= 0:
            start = next_space + 1
    if end < len(content):
        previous_space = content.rfind(" ", max(start, end - 80), end)
        if previous_space > start:
            end = previous_space

    excerpt = content[start:end].strip()
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(content):
        excerpt += "..."
    return excerpt


def _chunk_text(text: str, *, chunk_size: int, overlap: int) -> list[dict[str, Any]]:
    normalized = text.strip()
    if not normalized:
        return []

    chunks: list[dict[str, Any]] = []
    start = 0
    total = len(normalized)
    while start < total:
        target_end = min(total, start + chunk_size)
        end = target_end
        if target_end < total:
            search_floor = start + max(200, chunk_size // 2)
            candidates = [
                normalized.rfind("\n\n", search_floor, target_end),
                normalized.rfind("\n", search_floor, target_end),
                normalized.rfind(". ", search_floor, target_end),
                normalized.rfind(" ", search_floor, target_end),
            ]
            best = max(candidates)
            if best > start:
                end = best + (2 if normalized[best : best + 2] in {"\n\n", ". "} else 1)

        content = normalized[start:end].strip()
        if content:
            content_start = start
            left_trim = len(normalized[start:end]) - len(normalized[start:end].lstrip())
            content_start += left_trim
            chunks.append(
                {
                    "content": content,
                    "char_start": content_start,
                    "char_end": content_start + len(content),
                }
            )

        if end >= total:
            break
        next_start = max(start + 1, end - overlap)
        if next_start > 0:
            boundary = normalized.find(" ", next_start, min(total, next_start + 80))
            if boundary >= 0:
                next_start = boundary + 1
        start = next_start

    return chunks
