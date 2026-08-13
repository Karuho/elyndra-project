from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from elyndra.db import Database
from elyndra.language_packs.importers import normalize_term

_FIELDS = {
    "lexeme": {"lemma", "part_of_speech", "definition"},
    "form": {"form", "lemma", "features"},
    "sense": {"lemma", "definition", "part_of_speech"},
    "relation": {"source", "target", "relation_type", "sense_id"},
    "informal": {"expression", "expansion", "category", "confidence", "ambiguity_notes"},
}
_FEATURE_FIELDS = {
    "gender", "number", "person", "tense", "mood", "degree", "form_type"
}


class AccountLanguageOverlayRepository:
    def __init__(self, database: Database) -> None:
        if database.role != "vault":
            raise RuntimeError("Los overlays lingüísticos requieren database_role=vault.")
        self.database = database

    def propose(
        self,
        *,
        entry_type: str,
        expression: str,
        payload: dict[str, Any],
        actor: str,
        language: str = "es",
        locale: str = "",
    ) -> dict[str, Any]:
        clean = normalize_term(expression)
        payload_text = self._payload(entry_type, payload)
        if not clean or len(clean) > 300:
            raise ValueError("Expresión de overlay inválida.")
        now = _now()
        public_id = uuid.uuid4().hex
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO account_language_overlay_proposals(
                public_id,entry_type,language,locale,normalized_expression,expression_sha256,
                payload_json,source,status,proposed_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'owner_correction','pending',?,?,?)""",
                (
                    public_id,
                    entry_type,
                    language[:20],
                    locale[:40],
                    clean,
                    hashlib.sha256(clean.encode()).hexdigest(),
                    payload_text,
                    actor[:120],
                    now,
                    now,
                ),
            )
        return self.proposal(public_id) or {}

    def review(self, public_id: str, *, decision: str, actor: str) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise ValueError("La decisión debe ser approve o reject.")
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM account_language_overlay_proposals "
                "WHERE public_id=? AND status='pending'",
                (public_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Propuesta lingüística pendiente no encontrada.")
            status = "approved" if decision == "approve" else "rejected"
            connection.execute(
                "UPDATE account_language_overlay_proposals SET status=?,reviewed_by=?,"
                "reviewed_at=?,updated_at=? WHERE id=?",
                (status, actor[:120], now, now, row["id"]),
            )
            if status == "approved":
                connection.execute(
                    """INSERT INTO account_language_overlays(
                    public_id,proposal_id,entry_type,language,locale,normalized_expression,
                    payload_json,status,actor,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,'active',?,?,?)""",
                    (
                        uuid.uuid4().hex,
                        row["id"],
                        row["entry_type"],
                        row["language"],
                        row["locale"],
                        row["normalized_expression"],
                        row["payload_json"],
                        actor[:120],
                        now,
                        now,
                    ),
                )
        return self.proposal(public_id) or {}

    def lookup(
        self, expression: str, *, language: str = "es", limit: int = 20
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM account_language_overlays WHERE language=? "
                "AND normalized_expression=? AND status='active' ORDER BY id LIMIT ?",
                (language, normalize_term(expression), max(1, min(limit, 20))),
            ).fetchall()
        return [
            dict(row) | {"payload": json.loads(row["payload_json"]), "source": "account_overlay"}
            for row in rows
        ]

    def proposal(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM account_language_overlay_proposals WHERE public_id=?", (public_id,)
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _payload(entry_type: str, payload: dict[str, Any]) -> str:
        allowed = _FIELDS.get(entry_type)
        if allowed is None or not isinstance(payload, dict) or not payload:
            raise ValueError("Tipo o payload de overlay inválido.")
        if set(payload) - allowed:
            raise ValueError("El payload contiene campos no permitidos.")
        for key, value in payload.items():
            if key == "features":
                if not isinstance(value, dict) or set(value) - _FEATURE_FIELDS:
                    raise ValueError("features contiene campos no permitidos.")
                if any(
                    not isinstance(item, str) or len(item) > 80
                    for item in value.values()
                ):
                    raise ValueError("features requiere valores de texto acotados.")
                continue
            if not isinstance(value, (str, int, float, bool)):
                raise ValueError("El payload no admite estructuras arbitrarias.")
            if isinstance(value, str) and len(value) > 8_000:
                raise ValueError("Un campo del payload supera el límite permitido.")
        if "confidence" in payload:
            confidence = payload["confidence"]
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise ValueError("confidence debe ser numérica.")
            if not 0 <= float(confidence) <= 1:
                raise ValueError("confidence debe estar entre 0 y 1.")
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(text.encode()) > 16 * 1024:
            raise ValueError("El payload supera 16 KiB.")
        return text


def _now() -> str:
    return datetime.now(UTC).isoformat()
