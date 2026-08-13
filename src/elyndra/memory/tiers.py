from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from elyndra.db import Database
from elyndra.memory.lifecycle import MemoryLifecycleRepository
from elyndra.memory.repository import MemoryRepository

_HOT_QUERY_LIMIT = 16
_HOT_RESULT_LIMIT = 12
_WARM_DAYS = 30


@dataclass(frozen=True, slots=True)
class TieredRecall:
    query: str
    items: tuple[dict[str, Any], ...]
    hot_hit: bool
    hot_items: int
    warm_items: int
    cold_items: int
    total_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "items": [dict(item) for item in self.items],
            "hot_hit": self.hot_hit,
            "hot_items": self.hot_items,
            "warm_items": self.warm_items,
            "cold_items": self.cold_items,
            "total_ms": self.total_ms,
        }


class TieredMemoryRepository:
    """Bounded hot cache plus warm recent episodes and cold durable disk records."""

    def __init__(
        self,
        database: Database,
        memories: MemoryRepository,
        lifecycle: MemoryLifecycleRepository,
    ) -> None:
        self.database = database
        self.memories = memories
        self.lifecycle = lifecycle
        self._hot: OrderedDict[str, tuple[dict[str, Any], ...]] = OrderedDict()

    def invalidate(self) -> None:
        """Discard only the bounded in-process cache; durable records are unchanged."""
        self._hot.clear()

    def status(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            active_memories = int(
                connection.execute(
                    "SELECT COUNT(*) FROM memories WHERE status = 'active'"
                ).fetchone()[0]
            )
            active_episodes = int(
                connection.execute(
                    "SELECT COUNT(*) FROM chat_episodes WHERE status = 'active'"
                ).fetchone()[0]
            )
            cold_index = int(
                connection.execute(
                    "SELECT COUNT(*) FROM memory_cold_index WHERE status = 'active'"
                ).fetchone()[0]
            )
            recall_events = int(
                connection.execute("SELECT COUNT(*) FROM memory_recall_events").fetchone()[0]
            )
        return {
            "hot": {
                "storage": "bounded-process-cache",
                "queries": len(self._hot),
                "max_queries": _HOT_QUERY_LIMIT,
                "max_items_per_query": _HOT_RESULT_LIMIT,
                "durable": False,
            },
            "warm": {
                "storage": "sqlite-chat-state-and-recent-episodes",
                "active_episodes": active_episodes,
                "window_days": _WARM_DAYS,
                "durable": True,
            },
            "cold": {
                "storage": "sqlite-approved-memories-and-provenance-index",
                "approved_memories": active_memories,
                "indexed_episodes": cold_index,
                "durable": True,
            },
            "recall_events": recall_events,
            "full_database_loaded_into_ram": False,
            "automatic_unreviewed_preference_promotion": False,
        }

    def recall(
        self,
        query: str,
        *,
        project: str | None = None,
        chat: str | int | None = None,
        limit: int = 8,
    ) -> TieredRecall:
        clean_query = " ".join(query.strip().split())
        if not clean_query:
            return TieredRecall("", (), False, 0, 0, 0, 0)
        bounded_limit = max(1, min(int(limit), _HOT_RESULT_LIMIT))
        cache_key = self._cache_key(clean_query, project=project, chat=chat)
        started = time.perf_counter()
        cached = self._hot.get(cache_key)
        if cached is not None:
            self._hot.move_to_end(cache_key)
            items = cached[:bounded_limit]
            total_ms = _elapsed_ms(started)
            self._record_recall(
                clean_query,
                project=project,
                chat=chat,
                hot_hit=True,
                hot_items=len(items),
                warm_items=0,
                cold_items=0,
                total_ms=total_ms,
            )
            return TieredRecall(
                clean_query,
                items,
                True,
                len(items),
                0,
                0,
                total_ms,
            )

        warm = self._warm_recall(clean_query, project=project, chat=chat, limit=bounded_limit)
        cold = self._cold_recall(clean_query, project=project, limit=bounded_limit)
        merged = _merge_ranked(warm, cold, limit=bounded_limit)
        self._hot[cache_key] = tuple(merged)
        self._hot.move_to_end(cache_key)
        while len(self._hot) > _HOT_QUERY_LIMIT:
            self._hot.popitem(last=False)
        total_ms = _elapsed_ms(started)
        self._record_recall(
            clean_query,
            project=project,
            chat=chat,
            hot_hit=False,
            hot_items=0,
            warm_items=len(warm),
            cold_items=len(cold),
            total_ms=total_ms,
        )
        return TieredRecall(
            clean_query,
            tuple(merged),
            False,
            0,
            len(warm),
            len(cold),
            total_ms,
        )

    def consolidate(self, *, min_age_days: int = 30) -> dict[str, Any]:
        days = max(0, min(int(min_age_days), 3650))
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        now = datetime.now(UTC).isoformat()
        inserted = 0
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT e.id, e.chat_id, e.kind, e.project, e.content, e.content_hash,
                       e.importance, e.created_at, c.public_id AS chat_public_id
                FROM chat_episodes e
                JOIN chats c ON c.id = e.chat_id
                WHERE e.status = 'active' AND c.status = 'active' AND e.created_at <= ?
                ORDER BY e.id
                """,
                (cutoff,),
            ).fetchall()
            for row in rows:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO memory_cold_index(
                        source_type, source_id, chat_public_id, kind, project,
                        content, content_hash, importance, status, source_created_at,
                        indexed_at, last_accessed_at, access_count
                    ) VALUES ('episode', ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, NULL, 0)
                    """,
                    (
                        int(row["id"]),
                        str(row["chat_public_id"]),
                        str(row["kind"]),
                        row["project"],
                        str(row["content"]),
                        str(row["content_hash"]),
                        float(row["importance"]),
                        str(row["created_at"]),
                        now,
                    ),
                )
                inserted += max(0, cursor.rowcount)
            public_id = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO memory_consolidation_runs(
                    public_id, min_age_days, scanned_items, indexed_items,
                    deleted_items, created_at
                ) VALUES (?, ?, ?, ?, 0, ?)
                """,
                (public_id, days, len(rows), inserted, now),
            )
        self.invalidate()
        return {
            "public_id": public_id,
            "min_age_days": days,
            "scanned_items": len(rows),
            "indexed_items": inserted,
            "deleted_items": 0,
            "provenance_preserved": True,
            "unreviewed_memories_promoted": False,
        }

    def forget_cold(self, index_id: int) -> bool:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_cold_index
                SET status = 'deleted', last_accessed_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (now, int(index_id)),
            )
        self.invalidate()
        return cursor.rowcount > 0

    def recent_recalls(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT public_id, query_sha256, project, chat_public_id,
                       hot_hit, hot_items, warm_items, cold_items, total_ms, created_at
                FROM memory_recall_events
                ORDER BY id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["hot_hit"] = bool(item["hot_hit"])
        return items

    def _warm_recall(
        self,
        query: str,
        *,
        project: str | None,
        chat: str | int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        results = self.lifecycle.search_episodes(query, chat=chat, limit=max(2, limit))
        cutoff = datetime.now(UTC) - timedelta(days=_WARM_DAYS)
        items: list[dict[str, Any]] = []
        for row in results:
            created = _parse_datetime(str(row.get("created_at", "")))
            if created is not None and created < cutoff:
                continue
            item_project = str(row.get("project") or "")
            score = 0.8 + float(row.get("importance") or 0.0) * 0.1
            if project and item_project.casefold() == project.casefold():
                score += 0.35
            items.append(
                {
                    "tier": "warm",
                    "source_type": "episode",
                    "source_id": int(row["id"]),
                    "kind": str(row.get("kind", "episode")),
                    "project": row.get("project"),
                    "content": str(row.get("content", "")),
                    "score": round(score, 4),
                    "created_at": row.get("created_at"),
                    "chat_public_id": row.get("chat_public_id"),
                }
            )
        return items[:limit]

    def _cold_recall(
        self,
        query: str,
        *,
        project: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in self.memories.search(query, limit=max(2, limit)):
            score = 0.7 + float(row.get("confidence") or 0.0) * 0.2
            if project and str(row.get("project") or "").casefold() == project.casefold():
                score += 0.35
            items.append(
                {
                    "tier": "cold",
                    "source_type": "memory",
                    "source_id": int(row["id"]),
                    "kind": str(row.get("kind", "fact")),
                    "project": row.get("project"),
                    "content": str(row.get("content", "")),
                    "score": round(score, 4),
                    "created_at": row.get("created_at"),
                }
            )
        like = f"%{query}%"
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_cold_index
                WHERE status = 'active'
                  AND (content LIKE ? OR kind LIKE ? OR COALESCE(project, '') LIKE ?)
                ORDER BY importance DESC, id DESC
                LIMIT ?
                """,
                (like, like, like, max(2, limit)),
            ).fetchall()
            for row in rows:
                score = 0.55 + float(row["importance"] or 0.0) * 0.1
                if project and str(row["project"] or "").casefold() == project.casefold():
                    score += 0.35
                items.append(
                    {
                        "tier": "cold",
                        "source_type": "indexed_episode",
                        "source_id": int(row["id"]),
                        "kind": str(row["kind"]),
                        "project": row["project"],
                        "content": str(row["content"]),
                        "score": round(score, 4),
                        "created_at": row["source_created_at"],
                        "chat_public_id": row["chat_public_id"],
                    }
                )
                connection.execute(
                    """
                    UPDATE memory_cold_index
                    SET last_accessed_at = ?, access_count = access_count + 1
                    WHERE id = ?
                    """,
                    (datetime.now(UTC).isoformat(), int(row["id"])),
                )
        return items[: max(limit * 2, limit)]

    def _record_recall(
        self,
        query: str,
        *,
        project: str | None,
        chat: str | int | None,
        hot_hit: bool,
        hot_items: int,
        warm_items: int,
        cold_items: int,
        total_ms: int,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_recall_events(
                    public_id, query_sha256, project, chat_public_id, hot_hit,
                    hot_items, warm_items, cold_items, total_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    hashlib.sha256(query.encode("utf-8")).hexdigest(),
                    project,
                    str(chat) if chat is not None else None,
                    int(hot_hit),
                    hot_items,
                    warm_items,
                    cold_items,
                    total_ms,
                    datetime.now(UTC).isoformat(),
                ),
            )

    @staticmethod
    def _cache_key(query: str, *, project: str | None, chat: str | int | None) -> str:
        payload = json.dumps(
            {"query": query.casefold(), "project": project or "", "chat": str(chat or "")},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _merge_ranked(
    warm: list[dict[str, Any]], cold: list[dict[str, Any]], *, limit: int
) -> list[dict[str, Any]]:
    seen: set[tuple[str, int]] = set()
    items: list[dict[str, Any]] = []
    for item in sorted([*warm, *cold], key=lambda value: float(value["score"]), reverse=True):
        key = (str(item["source_type"]), int(item["source_id"]))
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
        if len(items) >= limit:
            break
    return items


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
