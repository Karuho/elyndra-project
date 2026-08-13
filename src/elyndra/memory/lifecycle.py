from __future__ import annotations

import gzip
import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.db import Database
from elyndra.memory.repository import MemoryRepository

_MAX_STATE_ITEMS = 12
_MAX_RECENT_ITEMS = 5
_MAX_ITEM_CHARS = 320

_DECISION_MARKERS = (
    "decidimos",
    "hemos decidido",
    "quedamos en",
    "se usara",
    "se usará",
    "usaremos",
    "vamos a usar",
    "la decision es",
    "la decisión es",
)
_PENDING_MARKERS = (
    "falta ",
    "queda pendiente",
    "pendiente",
    "hay que ",
    "tenemos que ",
    "necesito ",
    "necesitamos ",
    "vamos a implementar",
    "despues debemos",
    "después debemos",
)
_OUTCOME_MARKERS = (
    "funciono",
    "funcionó",
    "quedo listo",
    "quedó listo",
    "quedo corregido",
    "quedó corregido",
    "se resolvio",
    "se resolvió",
    "pasaron las pruebas",
    "all checks passed",
    "tests passed",
)
_PROBLEM_MARKERS = (
    "error",
    "fallo",
    "falló",
    "falla",
    "no funciona",
    "problema",
    "bug",
    "exception",
    "traceback",
)
_TOPIC_MARKERS = (
    "estamos trabajando en",
    "estamos viendo",
    "hablamos de",
    "el tema es",
    "respecto a",
    "sobre ",
)
_EXPLICIT_MEMORY_MARKERS = (
    "recuerda que",
    "quiero que recuerdes",
    "ten en cuenta que",
    "anota que",
    "no olvides que",
)
_PREFERENCE_MARKERS = (
    "prefiero ",
    "me gusta ",
    "no me gusta ",
    "me encanta ",
    "odio ",
    "suelo usar ",
    "siempre uso ",
)
_RULE_MARKERS = (
    "quiero que siempre",
    "debes siempre",
    "no debes nunca",
    "para mi es importante que",
    "para mí es importante que",
)
_ROUTINE_MARKERS = (
    "cada dia ",
    "cada día ",
    "cada mañana ",
    "cada noche ",
    "todos los dias ",
    "todos los días ",
    "todas las semanas ",
    "siempre a las ",
)
_TOKEN = re.compile(r"[\wÀ-ÖØ-öø-ÿĀ-ž]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class TurnConsolidation:
    topics: tuple[str, ...]
    episodes_created: tuple[int, ...]
    proposals_created: tuple[int, ...]
    summary: str


class MemoryLifecycleRepository:
    """Persist compact chat state, episodes and reviewable semantic proposals."""

    def __init__(self, database: Database, memories: MemoryRepository) -> None:
        self.database = database
        self.memories = memories

    def consolidate_turn(
        self,
        identifier: str | int,
        *,
        turn_index: int,
        user_text: str,
        assistant_text: str,
    ) -> TurnConsolidation:
        clean_user = _clean_text(user_text, _MAX_ITEM_CHARS)
        clean_assistant = _clean_text(assistant_text, _MAX_ITEM_CHARS)
        now = _now()
        with self.database.connect() as connection:
            chat = _find_chat(connection, identifier)
            if chat is None:
                raise ValueError(f"Chat no encontrado: {identifier}")
            chat_id = int(chat["id"])
            project = str(chat["project"]) if chat["project"] else None
            state = self._load_state(connection, chat_id)

            topics = _merge_items(state["topics"], _extract_topics(clean_user))
            decisions = list(state["decisions"])
            pending = list(state["pending"])
            outcomes = list(state["outcomes"])
            recent = list(state["recent"])

            episode_ids: list[int] = []
            for kind, content, importance in _extract_episodes(clean_user):
                episode_id = self._insert_episode(
                    connection,
                    chat_id=chat_id,
                    project=project,
                    kind=kind,
                    content=content,
                    turn_index=turn_index,
                    importance=importance,
                    now=now,
                )
                if episode_id is not None:
                    episode_ids.append(episode_id)
                if kind == "decision":
                    decisions = _merge_items(decisions, (content,))
                elif kind == "pending":
                    pending = _merge_items(pending, (content,))
                elif kind == "outcome":
                    outcomes = _merge_items(outcomes, (content,))
                elif kind == "problem":
                    pending = _merge_items(pending, (f"Resolver: {content}",))

            proposal_ids: list[int] = []
            for kind, content, reason, confidence in _extract_proposals(clean_user):
                proposal_id = self._insert_proposal(
                    connection,
                    chat_id=chat_id,
                    project=project,
                    kind=kind,
                    content=content,
                    reason=reason,
                    confidence=confidence,
                    now=now,
                )
                if proposal_id is not None:
                    proposal_ids.append(proposal_id)

            recent = [
                item
                for item in recent
                if not isinstance(item, dict) or int(item.get("turn", -1)) != turn_index
            ]
            recent.append(
                {
                    "turn": turn_index,
                    "user": clean_user,
                    "assistant": clean_assistant,
                }
            )
            recent = recent[-_MAX_RECENT_ITEMS:]
            self._save_state(
                connection,
                chat_id=chat_id,
                topics=topics,
                decisions=decisions,
                pending=pending,
                outcomes=outcomes,
                recent=recent,
                now=now,
            )
            summary = _render_state(
                topics=topics,
                decisions=decisions,
                pending=pending,
                outcomes=outcomes,
                recent=recent,
            )
            self._refresh_chat_fts(connection, chat_id, summary)
        return TurnConsolidation(
            topics=tuple(topics),
            episodes_created=tuple(episode_ids),
            proposals_created=tuple(proposal_ids),
            summary=summary,
        )

    def summary_data(self, identifier: str | int) -> dict[str, Any]:
        with self.database.connect() as connection:
            chat = _find_chat(connection, identifier)
            if chat is None:
                raise ValueError(f"Chat no encontrado: {identifier}")
            state = self._load_state(connection, int(chat["id"]))
        return {
            "chat_id": chat["public_id"],
            "topics": list(state["topics"]),
            "decisions": list(state["decisions"]),
            "pending": list(state["pending"]),
            "outcomes": list(state["outcomes"]),
            "recent": list(state["recent"]),
        }

    def render_summary(self, identifier: str | int) -> str:
        data = self.summary_data(identifier)
        rendered = _render_state(
            topics=data["topics"],
            decisions=data["decisions"],
            pending=data["pending"],
            outcomes=data["outcomes"],
            recent=data["recent"],
        )
        if rendered:
            return rendered
        with self.database.connect() as connection:
            chat = _find_chat(connection, identifier)
            assert chat is not None
            row = connection.execute(
                "SELECT summary FROM chat_summaries WHERE chat_id = ?",
                (int(chat["id"]),),
            ).fetchone()
        return str(row["summary"]) if row else ""

    def list_episodes(
        self,
        *,
        chat: str | int | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["e.status = 'active'"]
        params: list[Any] = []
        if chat is not None:
            clean_chat = str(chat).strip()
            if clean_chat.isdigit():
                clauses.append("c.id = ?")
                params.append(int(clean_chat))
            else:
                clauses.append("c.public_id = ?")
                params.append(clean_chat)
        if kind:
            clauses.append("e.kind = ?")
            params.append(kind.strip().casefold())
        params.append(max(1, limit))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT e.*, c.public_id AS chat_public_id, c.title AS chat_title
                FROM chat_episodes e
                JOIN chats c ON c.id = e.chat_id
                WHERE {' AND '.join(clauses)} AND c.status = 'active'
                ORDER BY e.importance DESC, e.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def search_episodes(
        self,
        query: str,
        *,
        chat: str | int | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        clean_query = query.strip()
        if not clean_query:
            return []
        with self.database.connect() as connection:
            chat_id: int | None = None
            if chat is not None:
                row = _find_chat(connection, chat)
                if row is None:
                    return []
                chat_id = int(row["id"])
            try:
                fts = connection.execute(
                    "SELECT value FROM schema_meta WHERE key = 'episode_fts5'"
                ).fetchone()
                if fts and fts[0] == "enabled":
                    clauses = ["e.status = 'active'", "c.status = 'active'"]
                    params: list[Any] = [_fts_query(clean_query)]
                    if chat_id is not None:
                        clauses.append("e.chat_id = ?")
                        params.append(chat_id)
                    params.append(max(1, limit))
                    rows = connection.execute(
                        f"""
                        SELECT e.*, c.public_id AS chat_public_id,
                               c.title AS chat_title, bm25(episode_fts) AS rank
                        FROM episode_fts
                        JOIN chat_episodes e
                          ON e.id = CAST(episode_fts.episode_id AS INTEGER)
                        JOIN chats c ON c.id = e.chat_id
                        WHERE episode_fts MATCH ? AND {' AND '.join(clauses)}
                        ORDER BY rank, e.importance DESC, e.id DESC
                        LIMIT ?
                        """,
                        params,
                    ).fetchall()
                    return [dict(row) for row in rows]
            except sqlite3.OperationalError:
                pass

            like = f"%{clean_query}%"
            clauses = ["e.status = 'active'", "c.status = 'active'"]
            params = [like, like, like]
            if chat_id is not None:
                clauses.append("e.chat_id = ?")
                params.append(chat_id)
            params.append(max(1, limit))
            rows = connection.execute(
                f"""
                SELECT e.*, c.public_id AS chat_public_id, c.title AS chat_title
                FROM chat_episodes e
                JOIN chats c ON c.id = e.chat_id
                WHERE (e.content LIKE ? OR e.kind LIKE ? OR COALESCE(e.project, '') LIKE ?)
                  AND {' AND '.join(clauses)}
                ORDER BY e.importance DESC, e.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_proposals(
        self,
        *,
        status: str = "pending",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clean_status = status.strip().casefold()
        if clean_status not in {"pending", "approved", "rejected", "all"}:
            raise ValueError("Estado inválido para propuestas de memoria.")
        with self.database.connect() as connection:
            if clean_status == "all":
                rows = connection.execute(
                    """
                    SELECT p.*, c.public_id AS chat_public_id, c.title AS chat_title
                    FROM memory_proposals p
                    LEFT JOIN chats c ON c.id = p.chat_id
                    ORDER BY p.id DESC LIMIT ?
                    """,
                    (max(1, limit),),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT p.*, c.public_id AS chat_public_id, c.title AS chat_title
                    FROM memory_proposals p
                    LEFT JOIN chats c ON c.id = p.chat_id
                    WHERE p.status = ?
                    ORDER BY p.id DESC LIMIT ?
                    """,
                    (clean_status, max(1, limit)),
                ).fetchall()
        return [dict(row) for row in rows]

    def edit_proposal(self, proposal_id: int, content: str) -> dict[str, Any]:
        clean = _clean_text(content, 1000)
        if not clean:
            raise ValueError("La propuesta no puede quedar vacía.")
        now = _now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_proposals
                SET content = ?, content_hash = ?, updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (clean, _fingerprint(clean), now, proposal_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("Propuesta pendiente no encontrada.")
            row = connection.execute(
                "SELECT * FROM memory_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        assert row is not None
        return dict(row)

    def approve_proposal(self, proposal_id: int) -> dict[str, Any]:
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_proposals WHERE id = ? AND status = 'pending'",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Propuesta pendiente no encontrada.")
        memory_id = self.memories.add(
            str(row["content"]),
            kind=str(row["kind"]),
            project=str(row["project"]) if row["project"] else None,
            source="reviewed-chat-proposal",
            confidence=float(row["confidence"]),
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE memory_proposals
                SET status = 'approved', memory_id = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (memory_id, now, now, proposal_id),
            )
            if str(row["kind"]) == "preference":
                connection.execute(
                    """
                    INSERT OR IGNORE INTO reviewed_preferences(
                        public_id, memory_id, source_proposal_id, category, scope, project,
                        content, confidence, status, expires_at, created_at, updated_at, reviewed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        memory_id,
                        proposal_id,
                        str(row["preference_category"] or "general"),
                        str(row["preference_scope"] or "global"),
                        row["project"],
                        str(row["content"]),
                        float(row["confidence"]),
                        row["expires_at"],
                        now,
                        now,
                        now,
                    ),
                )
            updated = connection.execute(
                "SELECT * FROM memory_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        assert updated is not None
        return dict(updated)

    def reject_proposal(self, proposal_id: int) -> bool:
        now = _now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_proposals
                SET status = 'rejected', reviewed_at = ?, updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now, now, proposal_id),
            )
        return cursor.rowcount > 0

    def edit_episode(
        self,
        episode_id: int,
        *,
        content: str,
        kind: str | None = None,
    ) -> dict[str, Any]:
        clean = _clean_text(content, 1000)
        if not clean:
            raise ValueError("El episodio no puede quedar vacío.")
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM chat_episodes WHERE id = ? AND status = 'active'",
                (episode_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Episodio activo no encontrado.")
            clean_kind = (kind or str(row["kind"])).strip().casefold()
            connection.execute(
                """
                UPDATE chat_episodes
                SET kind = ?, content = ?, content_hash = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    clean_kind,
                    clean,
                    _fingerprint(f"{clean_kind}:{clean}"),
                    now,
                    episode_id,
                ),
            )
            try:
                connection.execute(
                    "DELETE FROM episode_fts WHERE episode_id = ?", (str(episode_id),)
                )
                connection.execute(
                    """
                    INSERT INTO episode_fts(episode_id, chat_id, kind, project, content)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(episode_id),
                        str(row["chat_id"]),
                        clean_kind,
                        row["project"] or "",
                        clean,
                    ),
                )
            except sqlite3.OperationalError:
                pass
            self._rebuild_episode_state(connection, int(row["chat_id"]), now)
            updated = connection.execute(
                "SELECT * FROM chat_episodes WHERE id = ?", (episode_id,)
            ).fetchone()
        assert updated is not None
        return dict(updated)

    def forget_episode(self, episode_id: int) -> bool:
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT chat_id FROM chat_episodes WHERE id = ? AND status = 'active'",
                (episode_id,),
            ).fetchone()
            if row is None:
                return False
            cursor = connection.execute(
                """
                UPDATE chat_episodes SET status = 'deleted', updated_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (now, episode_id),
            )
            with suppress(sqlite3.OperationalError):
                connection.execute(
                    "DELETE FROM episode_fts WHERE episode_id = ?", (str(episode_id),)
                )
            self._rebuild_episode_state(connection, int(row["chat_id"]), now)
        return cursor.rowcount > 0

    def add_correction(
        self,
        identifier: str | int,
        *,
        user_text: str,
        original_response: str,
        corrected_response: str,
    ) -> int:
        clean_corrected = _clean_text(corrected_response, 3000)
        if not clean_corrected:
            raise ValueError("La corrección no puede estar vacía.")
        now = _now()
        with self.database.connect() as connection:
            chat = _find_chat(connection, identifier)
            if chat is None:
                raise ValueError(f"Chat no encontrado: {identifier}")
            cursor = connection.execute(
                """
                INSERT INTO response_corrections(
                    chat_id, user_text, original_response, corrected_response,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    int(chat["id"]),
                    _clean_text(user_text, 1000),
                    _clean_text(original_response, 3000),
                    clean_corrected,
                    now,
                    now,
                ),
            )
            correction_id = int(cursor.lastrowid)
            self._insert_episode(
                connection,
                chat_id=int(chat["id"]),
                project=str(chat["project"]) if chat["project"] else None,
                kind="correction",
                content=f"Corrección del propietario: {clean_corrected}",
                turn_index=int(chat["turn_count"]),
                importance=3,
                now=now,
            )
            self._rebuild_episode_state(connection, int(chat["id"]), now)
        return correction_id

    def list_corrections(
        self,
        *,
        chat: str | int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["r.status = 'active'"]
        params: list[Any] = []
        if chat is not None:
            clean = str(chat).strip()
            if clean.isdigit():
                clauses.append("r.chat_id = ?")
                params.append(int(clean))
            else:
                clauses.append("c.public_id = ?")
                params.append(clean)
        params.append(max(1, limit))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT r.*, c.public_id AS chat_public_id, c.title AS chat_title
                FROM response_corrections r
                LEFT JOIN chats c ON c.id = r.chat_id
                WHERE {' AND '.join(clauses)}
                ORDER BY r.id DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def archive_chat(
        self,
        identifier: str | int,
        *,
        transcripts_dir: Path,
        prune: bool = False,
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            chat = _find_chat(connection, identifier)
            if chat is None:
                raise ValueError(f"Chat no encontrado: {identifier}")
            rows = connection.execute(
                """
                SELECT turn_index, user_text, assistant_text, created_at
                FROM chat_turns WHERE chat_id = ? ORDER BY turn_index
                """,
                (int(chat["id"]),),
            ).fetchall()
            if not rows:
                raise ValueError(
                    "Este chat no tiene transcripción completa para archivar. "
                    "Activa el modo full antes de conversar."
                )
            summary = self.render_summary(identifier)

        transcripts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        day_dir = transcripts_dir / _now()[:4]
        day_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = day_dir / f"{chat['public_id']}.jsonl.gz"
        temp = target.with_suffix(target.suffix + ".tmp")
        metadata = {
            "type": "elyndra-chat-archive",
            "version": 1,
            "chat": {
                "public_id": chat["public_id"],
                "title": chat["title"],
                "project": chat["project"],
                "created_at": chat["created_at"],
                "updated_at": chat["updated_at"],
            },
            "summary": summary,
        }
        with gzip.open(temp, "wt", encoding="utf-8", compresslevel=6) as handle:
            handle.write(json.dumps(metadata, ensure_ascii=False) + "\n")
            for row in rows:
                record = {"type": "turn", **dict(row)}
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        temp.replace(target)
        target.chmod(0o600)
        with gzip.open(target, "rt", encoding="utf-8") as handle:
            first_line = handle.readline().strip()
            header = json.loads(first_line) if first_line else {}
            archived_turns = 0
            for line in handle:
                if not line.strip():
                    continue
                if json.loads(line).get("type") == "turn":
                    archived_turns += 1
        if header.get("type") != "elyndra-chat-archive":
            raise RuntimeError("El archivo frío no superó la verificación de cabecera.")
        if archived_turns != len(rows):
            raise RuntimeError("El archivo frío no contiene todos los turnos esperados.")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        size_bytes = target.stat().st_size
        now = _now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO chat_archives(
                    chat_id, path, sha256, size_bytes, turn_count, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?)
                ON CONFLICT(chat_id, path) DO UPDATE SET
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes,
                    turn_count = excluded.turn_count,
                    status = 'active',
                    created_at = excluded.created_at
                """,
                (
                    int(chat["id"]),
                    str(target),
                    digest,
                    size_bytes,
                    len(rows),
                    now,
                ),
            )
            if prune:
                connection.execute(
                    "DELETE FROM chat_turns WHERE chat_id = ?", (int(chat["id"]),)
                )
            archive = connection.execute(
                """
                SELECT a.*, c.public_id AS chat_public_id, c.title AS chat_title
                FROM chat_archives a JOIN chats c ON c.id = a.chat_id
                WHERE a.chat_id = ? AND a.path = ?
                """,
                (int(chat["id"]), str(target)),
            ).fetchone()
        assert archive is not None
        return dict(archive) | {"pruned": prune}

    def list_archives(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*, c.public_id AS chat_public_id, c.title AS chat_title
                FROM chat_archives a JOIN chats c ON c.id = a.chat_id
                WHERE a.status = 'active'
                ORDER BY a.id DESC LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _load_state(connection: sqlite3.Connection, chat_id: int) -> dict[str, list[Any]]:
        row = connection.execute(
            "SELECT * FROM chat_memory_state WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if row is None:
            legacy = connection.execute(
                "SELECT summary FROM chat_summaries WHERE chat_id = ?", (chat_id,)
            ).fetchone()
            return _parse_legacy_summary(str(legacy["summary"]) if legacy else "")
        return {
            "topics": _json_list(row["topics_json"]),
            "decisions": _json_list(row["decisions_json"]),
            "pending": _json_list(row["pending_json"]),
            "outcomes": _json_list(row["outcomes_json"]),
            "recent": _json_list(row["recent_json"]),
        }

    @staticmethod
    def _save_state(
        connection: sqlite3.Connection,
        *,
        chat_id: int,
        topics: list[str],
        decisions: list[str],
        pending: list[str],
        outcomes: list[str],
        recent: list[dict[str, Any]],
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO chat_memory_state(
                chat_id, topics_json, decisions_json, pending_json,
                outcomes_json, recent_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                topics_json = excluded.topics_json,
                decisions_json = excluded.decisions_json,
                pending_json = excluded.pending_json,
                outcomes_json = excluded.outcomes_json,
                recent_json = excluded.recent_json,
                updated_at = excluded.updated_at
            """,
            (
                chat_id,
                json.dumps(topics, ensure_ascii=False),
                json.dumps(decisions, ensure_ascii=False),
                json.dumps(pending, ensure_ascii=False),
                json.dumps(outcomes, ensure_ascii=False),
                json.dumps(recent, ensure_ascii=False),
                now,
            ),
        )

    @staticmethod
    def _insert_episode(
        connection: sqlite3.Connection,
        *,
        chat_id: int,
        project: str | None,
        kind: str,
        content: str,
        turn_index: int,
        importance: int,
        now: str,
    ) -> int | None:
        digest = _fingerprint(f"{kind}:{content}")
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO chat_episodes(
                chat_id, project, kind, content, content_hash, source_turn_index,
                importance, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                chat_id,
                project,
                kind,
                content,
                digest,
                turn_index,
                importance,
                now,
                now,
            ),
        )
        if cursor.rowcount == 0:
            return None
        episode_id = int(cursor.lastrowid)
        with suppress(sqlite3.OperationalError):
            connection.execute(
                """
                INSERT INTO episode_fts(episode_id, chat_id, kind, project, content)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(episode_id), str(chat_id), kind, project or "", content),
            )
        return episode_id

    @staticmethod
    def _insert_proposal(
        connection: sqlite3.Connection,
        *,
        chat_id: int,
        project: str | None,
        kind: str,
        content: str,
        reason: str,
        confidence: float,
        now: str,
    ) -> int | None:
        digest = _fingerprint(f"{kind}:{content}")
        existing = connection.execute(
            """
            SELECT id FROM memory_proposals
            WHERE content_hash = ? AND status IN ('pending', 'approved')
            LIMIT 1
            """,
            (digest,),
        ).fetchone()
        if existing is not None:
            return None
        cursor = connection.execute(
            """
            INSERT INTO memory_proposals(
                chat_id, kind, content, content_hash, project, reason,
                confidence, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                chat_id,
                kind,
                content,
                digest,
                project,
                reason,
                confidence,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def _rebuild_episode_state(
        self,
        connection: sqlite3.Connection,
        chat_id: int,
        now: str,
    ) -> None:
        state = self._load_state(connection, chat_id)
        decisions: list[str] = []
        pending: list[str] = []
        outcomes: list[str] = []
        rows = connection.execute(
            """
            SELECT kind, content FROM chat_episodes
            WHERE chat_id = ? AND status = 'active'
            ORDER BY id
            """,
            (chat_id,),
        ).fetchall()
        for row in rows:
            kind = str(row["kind"])
            content = str(row["content"])
            if kind == "decision":
                decisions = _merge_items(decisions, (content,))
            elif kind == "pending":
                pending = _merge_items(pending, (content,))
            elif kind == "problem":
                pending = _merge_items(pending, (f"Resolver: {content}",))
            elif kind in {"outcome", "correction"}:
                outcomes = _merge_items(outcomes, (content,))
        self._save_state(
            connection,
            chat_id=chat_id,
            topics=list(state["topics"]),
            decisions=decisions,
            pending=pending,
            outcomes=outcomes,
            recent=list(state["recent"]),
            now=now,
        )
        summary = _render_state(
            topics=list(state["topics"]),
            decisions=decisions,
            pending=pending,
            outcomes=outcomes,
            recent=list(state["recent"]),
        )
        self._refresh_chat_fts(connection, chat_id, summary)

    @staticmethod
    def _refresh_chat_fts(
        connection: sqlite3.Connection, chat_id: int, structured_summary: str
    ) -> None:
        try:
            connection.execute("DELETE FROM chat_fts WHERE chat_id = ?", (str(chat_id),))
            row = connection.execute(
                """
                SELECT c.public_id, c.title, COALESCE(c.project, '') AS project,
                       COALESCE(s.summary, '') AS legacy_summary
                FROM chats c LEFT JOIN chat_summaries s ON s.chat_id = c.id
                WHERE c.id = ? AND c.status = 'active'
                """,
                (chat_id,),
            ).fetchone()
            if row is None:
                return
            combined = "\n".join(
                part for part in (structured_summary, str(row["legacy_summary"])) if part
            )[:6000]
            connection.execute(
                """
                INSERT INTO chat_fts(chat_id, public_id, title, project, summary)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(chat_id),
                    row["public_id"],
                    row["title"],
                    row["project"],
                    combined,
                ),
            )
        except sqlite3.OperationalError:
            pass


def _parse_legacy_summary(summary: str) -> dict[str, list[Any]]:
    state: dict[str, list[Any]] = {
        "topics": [],
        "decisions": [],
        "pending": [],
        "outcomes": [],
        "recent": [],
    }
    pattern = re.compile(
        r"^(?P<turn>\d+)\. Usuario: (?P<user>.*?) \| Asistente: (?P<assistant>.*)$"
    )
    for line in summary.splitlines():
        match = pattern.match(line.strip())
        if match is None:
            continue
        user = _clean_text(match.group("user"), _MAX_ITEM_CHARS)
        assistant = _clean_text(match.group("assistant"), _MAX_ITEM_CHARS)
        state["topics"] = _merge_items(state["topics"], _extract_topics(user))
        for kind, content, _importance in _extract_episodes(user):
            if kind == "decision":
                state["decisions"] = _merge_items(state["decisions"], (content,))
            elif kind == "pending":
                state["pending"] = _merge_items(state["pending"], (content,))
            elif kind == "outcome":
                state["outcomes"] = _merge_items(state["outcomes"], (content,))
            elif kind == "problem":
                state["pending"] = _merge_items(
                    state["pending"], (f"Resolver: {content}",)
                )
        state["recent"].append(
            {
                "turn": int(match.group("turn")),
                "user": user,
                "assistant": assistant,
            }
        )
    state["recent"] = state["recent"][-_MAX_RECENT_ITEMS:]
    return state


def _extract_topics(text: str) -> tuple[str, ...]:
    if not text or text.endswith("?"):
        return ()
    normalized = _normalize(text)
    for marker in _TOPIC_MARKERS:
        if _normalize(marker) in normalized:
            candidate = _after_marker(text, marker)
            return (_clean_text(candidate or text, 180),)
    if any(term in normalized for term in ("elyndra", "memoria", "chat", "proyecto")):
        return (_clean_text(text, 180),)
    return ()


def _extract_episodes(text: str) -> tuple[tuple[str, str, int], ...]:
    normalized = _normalize(text)
    episodes: list[tuple[str, str, int]] = []
    if any(_normalize(marker) in normalized for marker in _DECISION_MARKERS):
        episodes.append(("decision", text, 3))
    if any(_normalize(marker) in normalized for marker in _PENDING_MARKERS):
        episodes.append(("pending", text, 2))
    if any(_normalize(marker) in normalized for marker in _OUTCOME_MARKERS):
        episodes.append(("outcome", text, 3))
    if any(_normalize(marker) in normalized for marker in _PROBLEM_MARKERS):
        episodes.append(("problem", text, 2))
    unique: list[tuple[str, str, int]] = []
    seen: set[str] = set()
    for item in episodes:
        key = f"{item[0]}:{_normalize(item[1])}"
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return tuple(unique)


def _extract_proposals(text: str) -> tuple[tuple[str, str, str, float], ...]:
    normalized = _normalize(text)
    for marker in _EXPLICIT_MEMORY_MARKERS:
        marker_normalized = _normalize(marker)
        if marker_normalized in normalized:
            content = _after_marker(text, marker)
            return (("fact", content or text, "solicitud explícita de recuerdo", 1.0),)
    for marker in _RULE_MARKERS:
        if _normalize(marker) in normalized:
            return (("rule", text, "regla estable expresada por el propietario", 0.95),)
    for marker in _PREFERENCE_MARKERS:
        if _normalize(marker) in normalized:
            return (("preference", text, "preferencia personal detectada", 0.9),)
    for marker in _ROUTINE_MARKERS:
        if _normalize(marker) in normalized:
            return (("routine", text, "rutina estable detectada", 0.85),)
    return ()


def _render_state(
    *,
    topics: list[Any],
    decisions: list[Any],
    pending: list[Any],
    outcomes: list[Any],
    recent: list[Any],
) -> str:
    sections: list[str] = []
    for title, values in (
        ("Temas", topics),
        ("Decisiones", decisions),
        ("Pendientes", pending),
        ("Resultados", outcomes),
    ):
        clean_values = [str(item).strip() for item in values if str(item).strip()]
        if clean_values:
            sections.append(title + ":\n" + "\n".join(f"- {item}" for item in clean_values[-6:]))
    clean_recent = [item for item in recent if isinstance(item, dict) and item.get("user")]
    if clean_recent:
        lines = [f"- {item['user']}" for item in clean_recent[-3:]]
        sections.append("Contexto reciente:\n" + "\n".join(lines))
    return "\n\n".join(sections)[:3600]


def _merge_items(current: list[Any], new_items: tuple[str, ...]) -> list[str]:
    result = [str(item) for item in current if str(item).strip()]
    fingerprints = {_normalize(item) for item in result}
    for item in new_items:
        clean = _clean_text(item, _MAX_ITEM_CHARS)
        key = _normalize(clean)
        if clean and key not in fingerprints:
            result.append(clean)
            fingerprints.add(key)
    return result[-_MAX_STATE_ITEMS:]


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _find_chat(connection: sqlite3.Connection, identifier: str | int) -> sqlite3.Row | None:
    clean = str(identifier).strip()
    if clean.isdigit():
        return connection.execute(
            "SELECT * FROM chats WHERE id = ? AND status = 'active'", (int(clean),)
        ).fetchone()
    return connection.execute(
        "SELECT * FROM chats WHERE public_id = ? AND status = 'active'", (clean,)
    ).fetchone()


def _after_marker(text: str, marker: str) -> str:
    folded = _normalize(text)
    target = _normalize(marker)
    index = folded.find(target)
    if index < 0:
        return text
    # Normalization can alter character positions, so prefer a case-insensitive regex.
    match = re.search(re.escape(marker), text, flags=re.IGNORECASE)
    if match:
        return text[match.end() :].strip(" :,-.")
    return text


def _clean_text(value: str, limit: int) -> str:
    clean = " ".join(value.strip().split())
    if len(clean) <= limit:
        return clean
    return clean[: max(1, limit - 1)].rstrip() + "…"


def _fingerprint(value: str) -> str:
    return hashlib.sha256(_normalize(value).encode("utf-8")).hexdigest()


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _fts_query(query: str) -> str:
    words = [word.replace('"', "") for word in _TOKEN.findall(query) if len(word) >= 3]
    return " OR ".join(f'"{word}"*' for word in words[:8])


def _now() -> str:
    return datetime.now(UTC).isoformat()
