from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from elyndra.db import Database
from elyndra.memory import MemoryRepository

_ALLOWED_CATEGORIES = frozenset(
    {
        "general",
        "style",
        "workflow",
        "tools",
        "content",
        "dietary",
        "accessibility",
        "locale",
    }
)
_ALLOWED_SCOPES = frozenset({"global", "project"})


class PreferenceLearningRepository:
    """Reviewable preference learning; never promotes observations silently."""

    def __init__(self, database: Database, memories: MemoryRepository) -> None:
        self.database = database
        self.memories = memories

    def status(self) -> dict[str, Any]:
        self.expire_due()
        with self.database.connect() as connection:
            pending = int(
                connection.execute(
                    "SELECT COUNT(*) FROM memory_proposals "
                    "WHERE kind = 'preference' AND status = 'pending'"
                ).fetchone()[0]
            )
            active = int(
                connection.execute(
                    "SELECT COUNT(*) FROM reviewed_preferences WHERE status = 'active'"
                ).fetchone()[0]
            )
            expired = int(
                connection.execute(
                    "SELECT COUNT(*) FROM reviewed_preferences WHERE status = 'expired'"
                ).fetchone()[0]
            )
        return {
            "pending_proposals": pending,
            "active_preferences": active,
            "expired_preferences": expired,
            "silent_learning": False,
            "approval_required": True,
            "editable_before_approval": True,
            "expiration_supported": True,
            "forget_supported": True,
        }

    def context_block(
        self,
        *,
        project: str | None = None,
        limit: int = 8,
    ) -> str:
        """Return bounded owner-approved preferences for language context."""
        items = self.list_preferences(
            status="active",
            project=project,
            limit=max(1, min(int(limit), 12)),
        )
        if not items:
            return ""
        lines = [
            "PREFERENCIAS REVISADAS DEL PROPIETARIO:",
            "Úsalas como orientación; no conceden permisos ni anulan seguridad o evidencia.",
        ]
        for item in reversed(items):
            content = " ".join(str(item["content"]).split())[:240]
            scope = str(item.get("scope") or "global")
            category = str(item.get("category") or "general")
            lines.append(f"- [{scope}/{category}] {content}")
        return "\n".join(lines)[:2200]

    def propose(
        self,
        content: str,
        *,
        category: str = "general",
        scope: str = "global",
        project: str | None = None,
        expires_days: int | None = None,
        reason: str = "Propuesta explícita del propietario.",
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        clean = _clean(content)
        clean_category = _category(category)
        clean_scope = _scope(scope)
        clean_project = project.strip() if project and project.strip() else None
        if clean_scope == "project" and not clean_project:
            raise ValueError("Las preferencias de proyecto requieren --project.")
        expires_at = _expiry(expires_days)
        digest = _fingerprint(f"preference:{clean_scope}:{clean_project or ''}:{clean}")
        now = _now()
        with self.database.connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM memory_proposals
                WHERE content_hash = ? AND kind = 'preference'
                  AND status IN ('pending', 'approved')
                LIMIT 1
                """,
                (digest,),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            cursor = connection.execute(
                """
                INSERT INTO memory_proposals(
                    chat_id, kind, content, content_hash, project, reason, confidence,
                    status, created_at, updated_at, preference_category,
                    preference_scope, expires_at
                ) VALUES (NULL, 'preference', ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
                """,
                (
                    clean,
                    digest,
                    clean_project,
                    reason.strip() or "Propuesta explícita.",
                    min(1.0, max(0.0, float(confidence))),
                    now,
                    now,
                    clean_category,
                    clean_scope,
                    expires_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM memory_proposals WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        assert row is not None
        return dict(row)

    def list_proposals(
        self, *, status: str = "pending", limit: int = 50
    ) -> list[dict[str, Any]]:
        clean_status = status.strip().casefold()
        if clean_status not in {"pending", "approved", "rejected", "all"}:
            raise ValueError("Estado inválido para propuestas de preferencias.")
        clause = "" if clean_status == "all" else "AND p.status = ?"
        params: list[Any] = [] if clean_status == "all" else [clean_status]
        params.append(max(1, limit))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT p.*, c.public_id AS chat_public_id, c.title AS chat_title
                FROM memory_proposals p
                LEFT JOIN chats c ON c.id = p.chat_id
                WHERE p.kind = 'preference' {clause}
                ORDER BY p.id DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def edit_proposal(
        self,
        proposal_id: int,
        *,
        content: str,
        category: str | None = None,
        scope: str | None = None,
        project: str | None = None,
        expires_days: int | None = None,
        clear_expiration: bool = False,
    ) -> dict[str, Any]:
        clean = _clean(content)
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_proposals "
                "WHERE id = ? AND kind = 'preference' AND status = 'pending'",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Propuesta pendiente de preferencia no encontrada.")
            clean_category = _category(
                category or str(row["preference_category"] or "general")
            )
            clean_scope = _scope(scope or str(row["preference_scope"] or "global"))
            clean_project = (
                project.strip()
                if project is not None and project.strip()
                else row["project"]
            )
            if clean_scope == "project" and not clean_project:
                raise ValueError("Las preferencias de proyecto requieren --project.")
            expires_at = row["expires_at"]
            if clear_expiration:
                expires_at = None
            elif expires_days is not None:
                expires_at = _expiry(expires_days)
            digest = _fingerprint(
                f"preference:{clean_scope}:{clean_project or ''}:{clean}"
            )
            connection.execute(
                """
                UPDATE memory_proposals
                SET content = ?, content_hash = ?, project = ?, updated_at = ?,
                    preference_category = ?, preference_scope = ?, expires_at = ?
                WHERE id = ?
                """,
                (
                    clean,
                    digest,
                    clean_project,
                    now,
                    clean_category,
                    clean_scope,
                    expires_at,
                    proposal_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM memory_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        assert updated is not None
        return dict(updated)

    def approve(self, proposal_id: int) -> dict[str, Any]:
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_proposals "
                "WHERE id = ? AND kind = 'preference' AND status = 'pending'",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Propuesta pendiente de preferencia no encontrada.")
        memory_id = self.memories.add(
            str(row["content"]),
            kind="preference",
            project=str(row["project"]) if row["project"] else None,
            source="reviewed-preference",
            confidence=float(row["confidence"]),
        )
        public_id = uuid.uuid4().hex
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO reviewed_preferences(
                    public_id, memory_id, source_proposal_id, category, scope, project,
                    content, confidence, status, expires_at, created_at, updated_at, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    public_id,
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
            connection.execute(
                """
                UPDATE memory_proposals
                SET status = 'approved', memory_id = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (memory_id, now, now, proposal_id),
            )
            result = connection.execute(
                "SELECT * FROM reviewed_preferences WHERE public_id = ?", (public_id,)
            ).fetchone()
        assert result is not None
        return dict(result)

    def reject(self, proposal_id: int) -> bool:
        now = _now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_proposals
                SET status = 'rejected', reviewed_at = ?, updated_at = ?
                WHERE id = ? AND kind = 'preference' AND status = 'pending'
                """,
                (now, now, proposal_id),
            )
        return cursor.rowcount > 0

    def list_preferences(
        self,
        *,
        status: str = "active",
        project: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.expire_due()
        clean_status = status.strip().casefold()
        if clean_status not in {"active", "expired", "deleted", "all"}:
            raise ValueError("Estado inválido para preferencias.")
        clauses: list[str] = []
        params: list[Any] = []
        if clean_status != "all":
            clauses.append("status = ?")
            params.append(clean_status)
        if project:
            clauses.append("(scope = 'global' OR project = ?)")
            params.append(project.strip())
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, limit))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM reviewed_preferences {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, public_id: str) -> dict[str, Any] | None:
        self.expire_due()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM reviewed_preferences WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
        return dict(row) if row else None

    def forget(self, public_id: str) -> bool:
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT memory_id FROM reviewed_preferences "
                "WHERE public_id = ? AND status = 'active'",
                (public_id.strip(),),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                "UPDATE reviewed_preferences SET status = 'deleted', updated_at = ? "
                "WHERE public_id = ?",
                (now, public_id.strip()),
            )
        self.memories.forget(int(row["memory_id"]))
        return True

    def expire_due(self) -> int:
        now = _now()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT public_id, memory_id FROM reviewed_preferences
                WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                (now,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE reviewed_preferences SET status = 'expired', updated_at = ? "
                    "WHERE public_id = ?",
                    (now, row["public_id"]),
                )
        for row in rows:
            self.memories.forget(int(row["memory_id"]))
        return len(rows)


def _clean(content: str) -> str:
    clean = " ".join(content.strip().split())
    if not clean:
        raise ValueError("La preferencia no puede estar vacía.")
    if len(clean) > 1000:
        raise ValueError("La preferencia supera 1000 caracteres.")
    return clean


def _category(value: str) -> str:
    clean = value.strip().casefold()
    if clean not in _ALLOWED_CATEGORIES:
        raise ValueError("Categoría de preferencia inválida.")
    return clean


def _scope(value: str) -> str:
    clean = value.strip().casefold()
    if clean not in _ALLOWED_SCOPES:
        raise ValueError("Ámbito de preferencia inválido.")
    return clean


def _expiry(days: int | None) -> str | None:
    if days is None:
        return None
    if days < 1 or days > 3650:
        raise ValueError("La expiración debe estar entre 1 y 3650 días.")
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()
