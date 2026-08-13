from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from elyndra.db import Database

LESSON_SOURCE_TYPES = (
    "owner_feedback",
    "reviewed_evidence",
    "deterministic_evidence",
)
COMPARISON_OUTCOMES = ("match", "partial", "mismatch")
COMPARISON_METHODS = ("exact_hash", "owner_review")
_SOURCE_WEIGHTS = {
    "owner_feedback": 1.0,
    "reviewed_evidence": 1.5,
    "deterministic_evidence": 2.0,
}
_MAX_CONTEXT_LESSONS = 4
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TutorLearningRepository:
    """Owner-reviewed tutor lessons and task/source confidence calibration."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def status(self) -> dict[str, Any]:
        self.expire_due()
        with self.database.connect() as connection:
            counts = {
                "pending_proposals": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_tutor_lesson_proposals "
                        "WHERE status = 'pending'"
                    ).fetchone()[0]
                ),
                "active_lessons": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_tutor_lessons "
                        "WHERE status = 'active'"
                    ).fetchone()[0]
                ),
                "expired_lessons": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_tutor_lessons "
                        "WHERE status = 'expired'"
                    ).fetchone()[0]
                ),
                "forgotten_lessons": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_tutor_lessons "
                        "WHERE status = 'forgotten'"
                    ).fetchone()[0]
                ),
                "evidence_comparisons": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_tutor_evidence_comparisons"
                    ).fetchone()[0]
                ),
            }
        return {
            **counts,
            "source_types": list(LESSON_SOURCE_TYPES),
            "calibration_policy": "task-source-conservative",
            "max_context_lessons": _MAX_CONTEXT_LESSONS,
            "owner_review_required": True,
            "silent_learning": False,
            "automatic_model_update": False,
            "automatic_memory_update": False,
            "automatic_preference_update": False,
            "raw_prompt_stored": False,
            "raw_output_stored": False,
        }

    def propose(
        self,
        *,
        tutor_id: str,
        task: str,
        lesson: str,
        source_type: str,
        source_sha256: str,
        observed_score: float,
        review_confidence: float = 1.0,
        source_ref: str | None = None,
        expires_days: int | None = None,
        actor: str,
    ) -> dict[str, Any]:
        prepared = self._prepare_proposal(
            tutor_id=tutor_id,
            task=task,
            lesson=lesson,
            source_type=source_type,
            source_sha256=source_sha256,
            observed_score=observed_score,
            review_confidence=review_confidence,
            source_ref=source_ref,
            expires_days=expires_days,
            actor=actor,
        )
        with self.database.connect() as connection:
            return self._insert_proposal(connection, prepared)

    def _prepare_proposal(
        self,
        *,
        tutor_id: str,
        task: str,
        lesson: str,
        source_type: str,
        source_sha256: str,
        observed_score: float,
        review_confidence: float,
        source_ref: str | None,
        expires_days: int | None,
        actor: str,
    ) -> dict[str, Any]:
        clean_lesson = _lesson(lesson)
        return {
            "public_id": uuid.uuid4().hex,
            "tutor_id": _identifier(tutor_id, "tutor"),
            "task_type": _identifier(task, "tarea"),
            "lesson_text": clean_lesson,
            "lesson_sha256": hashlib.sha256(
                clean_lesson.encode("utf-8")
            ).hexdigest(),
            "source_type": _source_type(source_type),
            "source_ref": _optional_text(source_ref, limit=160),
            "source_sha256": _sha256(source_sha256, "fuente"),
            "observed_score": _score(observed_score, "observed_score"),
            "review_confidence": _score(
                review_confidence, "review_confidence"
            ),
            "expires_at": _expiry(expires_days),
            "created_by": _identifier(actor, "actor"),
            "created_at": _now(),
        }

    def _insert_proposal(
        self, connection: sqlite3.Connection, prepared: dict[str, Any]
    ) -> dict[str, Any]:
        duplicate = connection.execute(
            """
            SELECT proposal.public_id
            FROM assistant_tutor_lesson_proposals AS proposal
            LEFT JOIN assistant_tutor_lessons AS lesson
              ON lesson.id = proposal.lesson_id
            WHERE proposal.tutor_id = ?
              AND proposal.task_type = ?
              AND proposal.lesson_sha256 = ?
              AND (
                proposal.status = 'pending'
                OR (proposal.status = 'approved' AND lesson.status = 'active')
              )
            LIMIT 1
            """,
            (
                prepared["tutor_id"],
                prepared["task_type"],
                prepared["lesson_sha256"],
            ),
        ).fetchone()
        if duplicate is not None:
            raise ValueError(
                "Ya existe una propuesta pendiente o una lección activa "
                "con el mismo contenido."
            )
        connection.execute(
            """
            INSERT INTO assistant_tutor_lesson_proposals(
                public_id, tutor_id, task_type, lesson_text, lesson_sha256,
                source_type, source_ref, source_sha256, observed_score,
                review_confidence, status, expires_at, created_by,
                created_at, updated_at, reviewed_at, reviewed_by, lesson_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, NULL, NULL, NULL)
            """,
            (
                prepared["public_id"],
                prepared["tutor_id"],
                prepared["task_type"],
                prepared["lesson_text"],
                prepared["lesson_sha256"],
                prepared["source_type"],
                prepared["source_ref"],
                prepared["source_sha256"],
                prepared["observed_score"],
                prepared["review_confidence"],
                prepared["expires_at"],
                prepared["created_by"],
                prepared["created_at"],
                prepared["created_at"],
            ),
        )
        row = connection.execute(
            "SELECT * FROM assistant_tutor_lesson_proposals WHERE public_id = ?",
            (prepared["public_id"],),
        ).fetchone()
        assert row is not None
        return dict(row)

    def edit_proposal(
        self,
        public_id: str,
        *,
        lesson: str | None = None,
        observed_score: float | None = None,
        review_confidence: float | None = None,
        expires_days: int | None = None,
        clear_expiration: bool = False,
    ) -> dict[str, Any]:
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_tutor_lesson_proposals "
                "WHERE public_id = ? AND status = 'pending'",
                (public_id.strip(),),
            ).fetchone()
            if row is None:
                raise ValueError("Propuesta pendiente de lección no encontrada.")
            clean_lesson = _lesson(lesson) if lesson is not None else str(row["lesson_text"])
            score = (
                _score(observed_score, "observed_score")
                if observed_score is not None
                else float(row["observed_score"])
            )
            confidence = (
                _score(review_confidence, "review_confidence")
                if review_confidence is not None
                else float(row["review_confidence"])
            )
            expires_at = row["expires_at"]
            if clear_expiration:
                expires_at = None
            elif expires_days is not None:
                expires_at = _expiry(expires_days)
            lesson_hash = hashlib.sha256(clean_lesson.encode("utf-8")).hexdigest()
            duplicate = connection.execute(
                """
                SELECT proposal.public_id
                FROM assistant_tutor_lesson_proposals AS proposal
                LEFT JOIN assistant_tutor_lessons AS active_lesson
                  ON active_lesson.id = proposal.lesson_id
                WHERE proposal.public_id <> ?
                  AND proposal.tutor_id = ?
                  AND proposal.task_type = ?
                  AND proposal.lesson_sha256 = ?
                  AND (
                    proposal.status = 'pending'
                    OR (
                      proposal.status = 'approved'
                      AND active_lesson.status = 'active'
                    )
                  )
                LIMIT 1
                """,
                (
                    public_id.strip(),
                    row["tutor_id"],
                    row["task_type"],
                    lesson_hash,
                ),
            ).fetchone()
            if duplicate is not None:
                raise ValueError(
                    "Ya existe otra propuesta pendiente o lección activa "
                    "con el mismo contenido."
                )
            connection.execute(
                """
                UPDATE assistant_tutor_lesson_proposals
                SET lesson_text = ?, lesson_sha256 = ?, observed_score = ?,
                    review_confidence = ?, expires_at = ?, updated_at = ?
                WHERE public_id = ?
                """,
                (
                    clean_lesson,
                    lesson_hash,
                    score,
                    confidence,
                    expires_at,
                    now,
                    public_id.strip(),
                ),
            )
            updated = connection.execute(
                "SELECT * FROM assistant_tutor_lesson_proposals WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
        assert updated is not None
        return dict(updated)

    def approve(self, public_id: str, *, actor: str) -> dict[str, Any]:
        self.expire_due()
        now = _now()
        lesson_public_id = uuid.uuid4().hex
        with self.database.connect() as connection:
            proposal = connection.execute(
                "SELECT * FROM assistant_tutor_lesson_proposals "
                "WHERE public_id = ? AND status = 'pending'",
                (public_id.strip(),),
            ).fetchone()
            if proposal is None:
                raise ValueError("Propuesta pendiente de lección no encontrada.")
            cursor = connection.execute(
                """
                INSERT INTO assistant_tutor_lessons(
                    public_id, proposal_id, tutor_id, task_type, lesson_text,
                    lesson_sha256, source_type, source_ref, source_sha256,
                    observed_score, review_confidence, status, expires_at,
                    created_at, updated_at, reviewed_at, reviewed_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    lesson_public_id,
                    int(proposal["id"]),
                    proposal["tutor_id"],
                    proposal["task_type"],
                    proposal["lesson_text"],
                    proposal["lesson_sha256"],
                    proposal["source_type"],
                    proposal["source_ref"],
                    proposal["source_sha256"],
                    proposal["observed_score"],
                    proposal["review_confidence"],
                    proposal["expires_at"],
                    now,
                    now,
                    now,
                    _identifier(actor, "actor"),
                ),
            )
            lesson_id = int(cursor.lastrowid)
            connection.execute(
                """
                UPDATE assistant_tutor_lesson_proposals
                SET status = 'approved', lesson_id = ?, reviewed_at = ?,
                    reviewed_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    lesson_id,
                    now,
                    _identifier(actor, "actor"),
                    now,
                    int(proposal["id"]),
                ),
            )
            lesson = connection.execute(
                "SELECT * FROM assistant_tutor_lessons WHERE id = ?", (lesson_id,)
            ).fetchone()
        assert lesson is not None
        return dict(lesson)

    def reject(self, public_id: str, *, actor: str) -> bool:
        now = _now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE assistant_tutor_lesson_proposals
                SET status = 'rejected', reviewed_at = ?, reviewed_by = ?, updated_at = ?
                WHERE public_id = ? AND status = 'pending'
                """,
                (now, _identifier(actor, "actor"), now, public_id.strip()),
            )
        return cursor.rowcount > 0

    def forget(self, public_id: str, *, actor: str) -> bool:
        now = _now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE assistant_tutor_lessons
                SET status = 'forgotten', updated_at = ?, reviewed_by = ?
                WHERE public_id = ? AND status = 'active'
                """,
                (now, _identifier(actor, "actor"), public_id.strip()),
            )
        return cursor.rowcount > 0

    def expire_due(self) -> dict[str, int]:
        now = _now()
        with self.database.connect() as connection:
            proposals = connection.execute(
                """
                UPDATE assistant_tutor_lesson_proposals
                SET status = 'expired', updated_at = ?
                WHERE status = 'pending' AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                (now, now),
            ).rowcount
            lessons = connection.execute(
                """
                UPDATE assistant_tutor_lessons
                SET status = 'expired', updated_at = ?
                WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                (now, now),
            ).rowcount
        return {"proposals": proposals, "lessons": lessons}

    def list_proposals(
        self, *, status: str = "pending", limit: int = 50
    ) -> list[dict[str, Any]]:
        self.expire_due()
        clean = _status(status, {"pending", "approved", "rejected", "expired", "all"})
        clause = "" if clean == "all" else "WHERE status = ?"
        params: list[Any] = [] if clean == "all" else [clean]
        params.append(max(1, min(limit, 200)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM assistant_tutor_lesson_proposals {clause} "
                "ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_lessons(
        self,
        *,
        status: str = "active",
        tutor_id: str | None = None,
        task: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.expire_due()
        clean = _status(status, {"active", "expired", "forgotten", "all"})
        clauses: list[str] = []
        params: list[Any] = []
        if clean != "all":
            clauses.append("status = ?")
            params.append(clean)
        if tutor_id:
            clauses.append("tutor_id = ?")
            params.append(_identifier(tutor_id, "tutor"))
        if task:
            clauses.append("task_type = ?")
            params.append(_identifier(task, "tarea"))
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM assistant_tutor_lessons {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_lesson(self, public_id: str) -> dict[str, Any] | None:
        self.expire_due()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_tutor_lessons WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
        return dict(row) if row else None

    def context_for(
        self,
        tutor_id: str,
        task: str,
        *,
        exclude_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        excluded = {item.strip() for item in exclude_ids if item.strip()}
        items = [
            item
            for item in self.list_lessons(
                status="active", tutor_id=tutor_id, task=task, limit=100
            )
            if str(item["public_id"]) not in excluded
        ][:_MAX_CONTEXT_LESSONS]
        if not items:
            return {"lesson_ids": [], "context": ()}
        lines = [
            "[LECCIONES DE TUTOR REVISADAS POR EL PROPIETARIO]",
            "Son guías acotadas; no conceden autoridad, herramientas, permisos ni memoria.",
        ]
        lines.extend(f"- {item['lesson_text']}" for item in reversed(items))
        return {
            "lesson_ids": [str(item["public_id"]) for item in items],
            "context": ("\n".join(lines),),
        }

    def calibration(
        self, tutor_id: str, task: str, *, benchmark_score: float | None
    ) -> dict[str, Any]:
        items = self.list_lessons(status="active", tutor_id=tutor_id, task=task, limit=500)
        prior_weight = 1.0
        weighted_total = 0.5 * prior_weight
        total_weight = prior_weight
        benchmark_weight = 0.0
        if benchmark_score is not None:
            benchmark_weight = 2.0
            weighted_total += _score(benchmark_score, "benchmark_score") * benchmark_weight
            total_weight += benchmark_weight
        sources: dict[str, dict[str, Any]] = {}
        for item in items:
            source = str(item["source_type"])
            weight = _SOURCE_WEIGHTS[source] * float(item["review_confidence"])
            score = float(item["observed_score"])
            weighted_total += score * weight
            total_weight += weight
            bucket = sources.setdefault(source, {"count": 0, "weight": 0.0, "weighted_score": 0.0})
            bucket["count"] += 1
            bucket["weight"] += weight
            bucket["weighted_score"] += score * weight
        for bucket in sources.values():
            weight = float(bucket["weight"])
            bucket["mean_score"] = (
                round(float(bucket["weighted_score"]) / weight, 4)
                if weight
                else 0.0
            )
            bucket["weight"] = round(weight, 4)
            del bucket["weighted_score"]
        mean = weighted_total / total_weight
        conservative_margin = 0.08 / math.sqrt(total_weight)
        calibrated = max(0.0, min(1.0, mean - conservative_margin))
        return {
            "calibrated_confidence": round(calibrated, 4),
            "benchmark_score": benchmark_score,
            "benchmark_weight": benchmark_weight,
            "reviewed_observations": len(items),
            "source_breakdown": sources,
            "total_weight": round(total_weight, 4),
            "universal_intelligence_claim": False,
        }

    def compare_evidence(
        self,
        *,
        tutor_id: str,
        task: str,
        tutor_output_sha256: str,
        evidence_sha256: str,
        method: str,
        outcome: str | None,
        lesson: str,
        review_confidence: float,
        actor: str,
        selection_id: str | None = None,
        expires_days: int | None = None,
    ) -> dict[str, Any]:
        clean_method = method.strip().casefold()
        if clean_method not in COMPARISON_METHODS:
            raise ValueError("Método de comparación inválido.")
        output_hash = _sha256(tutor_output_sha256, "salida del tutor")
        evidence_hash = _sha256(evidence_sha256, "evidencia")
        if clean_method == "exact_hash":
            clean_outcome = "match" if output_hash == evidence_hash else "mismatch"
            if outcome is not None and outcome != clean_outcome:
                raise ValueError(
                    "El resultado indicado contradice la comparación exacta de hashes."
                )
        else:
            clean_outcome = _outcome(outcome)
        observed_score = {"match": 1.0, "partial": 0.5, "mismatch": 0.0}[
            clean_outcome
        ]
        comparison_id = uuid.uuid4().hex
        source_hash = hashlib.sha256(
            f"{output_hash}:{evidence_hash}:{clean_outcome}:{clean_method}".encode()
        ).hexdigest()
        prepared = self._prepare_proposal(
            tutor_id=tutor_id,
            task=task,
            lesson=lesson,
            source_type="deterministic_evidence",
            source_ref=comparison_id,
            source_sha256=source_hash,
            observed_score=observed_score,
            review_confidence=review_confidence,
            expires_days=expires_days,
            actor=actor,
        )
        clean_selection = _optional_text(selection_id, limit=64)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_tutor_evidence_comparisons(
                    public_id, tutor_id, task_type, selection_id,
                    tutor_output_sha256, evidence_sha256, comparison_method,
                    outcome, created_by, created_at, proposal_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    comparison_id,
                    prepared["tutor_id"],
                    prepared["task_type"],
                    clean_selection,
                    output_hash,
                    evidence_hash,
                    clean_method,
                    clean_outcome,
                    prepared["created_by"],
                    prepared["created_at"],
                ),
            )
            proposal = self._insert_proposal(connection, prepared)
            connection.execute(
                "UPDATE assistant_tutor_evidence_comparisons SET proposal_id = ? "
                "WHERE public_id = ?",
                (int(proposal["id"]), comparison_id),
            )
            comparison = connection.execute(
                "SELECT * FROM assistant_tutor_evidence_comparisons "
                "WHERE public_id = ?",
                (comparison_id,),
            ).fetchone()
        assert comparison is not None
        return {"comparison": dict(comparison), "proposal": proposal}

    def list_comparisons(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assistant_tutor_evidence_comparisons "
                "ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(row) for row in rows]


def _identifier(value: str, label: str) -> str:
    clean = value.strip()
    if not clean or len(clean) > 100:
        raise ValueError(f"Identificador de {label} inválido.")
    return clean


def _lesson(value: str | None) -> str:
    if value is None:
        raise ValueError("La lección no puede estar vacía.")
    clean = " ".join(value.strip().split())
    if not clean:
        raise ValueError("La lección no puede estar vacía.")
    if len(clean) > 500:
        raise ValueError("La lección supera 500 caracteres.")
    return clean


def _source_type(value: str) -> str:
    clean = value.strip().casefold()
    if clean not in LESSON_SOURCE_TYPES:
        raise ValueError("Tipo de fuente de lección inválido.")
    return clean


def _sha256(value: str, label: str) -> str:
    clean = value.strip().casefold()
    if not _SHA256_RE.fullmatch(clean):
        raise ValueError(f"SHA-256 de {label} inválido.")
    return clean


def _optional_text(value: str | None, *, limit: int) -> str | None:
    if value is None:
        return None
    clean = " ".join(value.strip().split())
    if not clean:
        return None
    if len(clean) > limit:
        raise ValueError(f"El texto opcional supera {limit} caracteres.")
    return clean


def _score(value: float, label: str) -> float:
    clean = float(value)
    if clean < 0.0 or clean > 1.0:
        raise ValueError(f"{label} debe estar entre 0 y 1.")
    return clean


def _outcome(value: str | None) -> str:
    if value is None:
        raise ValueError("La revisión del propietario requiere un resultado explícito.")
    clean = value.strip().casefold()
    if clean not in COMPARISON_OUTCOMES:
        raise ValueError("Resultado de comparación inválido.")
    return clean


def _status(value: str, allowed: set[str]) -> str:
    clean = value.strip().casefold()
    if clean not in allowed:
        raise ValueError("Estado inválido.")
    return clean


def _expiry(days: int | None) -> str | None:
    if days is None:
        return None
    if days < 1 or days > 3650:
        raise ValueError("La expiración debe estar entre 1 y 3650 días.")
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _now() -> str:
    return datetime.now(UTC).isoformat()
