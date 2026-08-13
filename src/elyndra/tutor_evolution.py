from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from elyndra.db import Database

EVALUATION_STATUSES = (
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
)
EVALUATION_RECOMMENDATIONS = (
    "promote_knowledge",
    "retain_lesson",
    "replace_lesson",
    "insufficient_evidence",
)
KNOWLEDGE_STATUSES = ("active", "superseded", "all")
_MAX_KNOWLEDGE_CONTEXT_ITEMS = 6
_MAX_KNOWLEDGE_CONTEXT_CHARS = 2400


class TutorEvolutionRepository:
    """Supervised lesson evaluation and versioned durable knowledge."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def status(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            counts = {
                "evaluations": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_tutor_lesson_evaluations"
                    ).fetchone()[0]
                ),
                "pending_evaluations": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_tutor_lesson_evaluations "
                        "WHERE status = 'pending'"
                    ).fetchone()[0]
                ),
                "completed_evaluations": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_tutor_lesson_evaluations "
                        "WHERE status = 'completed'"
                    ).fetchone()[0]
                ),
                "active_knowledge": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_tutor_knowledge "
                        "WHERE status = 'active'"
                    ).fetchone()[0]
                ),
                "superseded_knowledge": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_tutor_knowledge "
                        "WHERE status = 'superseded'"
                    ).fetchone()[0]
                ),
            }
        return {
            **counts,
            "evaluation_foreground_only": True,
            "evaluation_single_use": True,
            "owner_approval_required": True,
            "auditor_advisory_only": True,
            "knowledge_versioned": True,
            "knowledge_deletion_allowed": False,
            "knowledge_supersession_required_for_replacement": True,
            "automatic_promotion": False,
            "automatic_model_update": False,
            "raw_prompts_stored": False,
            "raw_outputs_stored": False,
            "max_knowledge_context_items": _MAX_KNOWLEDGE_CONTEXT_ITEMS,
            "max_knowledge_context_chars": _MAX_KNOWLEDGE_CONTEXT_CHARS,
        }

    def create_evaluation(
        self,
        *,
        lesson_public_id: str,
        case_ids: tuple[str, ...],
        knowledge_ids: tuple[str, ...],
        suite_version: str,
        model_fingerprint: str,
        actor: str,
        auditor_id: str | None = None,
        auditor_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        clean_cases = tuple(dict.fromkeys(_identifier(item, "caso") for item in case_ids))
        clean_knowledge_ids = tuple(
            dict.fromkeys(
                _identifier(item, "conocimiento") for item in knowledge_ids
            )
        )
        if not clean_cases:
            raise ValueError("La evaluación requiere al menos un caso aplicable.")
        now = _now()
        public_id = uuid.uuid4().hex
        with self.database.connect() as connection:
            lesson = connection.execute(
                "SELECT * FROM assistant_tutor_lessons "
                "WHERE public_id = ? AND status = 'active'",
                (lesson_public_id.strip(),),
            ).fetchone()
            if lesson is None:
                raise ValueError("Lección activa no encontrada.")
            duplicate = connection.execute(
                """
                SELECT public_id
                FROM assistant_tutor_lesson_evaluations
                WHERE lesson_id = ? AND status IN ('pending', 'running')
                LIMIT 1
                """,
                (int(lesson["id"]),),
            ).fetchone()
            if duplicate is not None:
                raise ValueError(
                    "Ya existe una evaluación pendiente o en ejecución para la lección."
                )
            connection.execute(
                """
                INSERT INTO assistant_tutor_lesson_evaluations(
                    public_id, lesson_id, tutor_id, task_type, auditor_id,
                    suite_version, case_ids_json, knowledge_ids_json,
                    model_fingerprint, auditor_fingerprint, status, recommendation,
                    baseline_score, candidate_score, score_delta,
                    baseline_latency_ms, candidate_latency_ms,
                    auditor_status, auditor_verdict, auditor_confidence,
                    auditor_output_sha256, promoted_knowledge_id,
                    created_by, created_at,
                    started_at, completed_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', NULL, NULL,
                          NULL, 0, 0, 'not_requested', '', NULL, '', NULL, ?, ?, NULL,
                          NULL, '')
                """,
                (
                    public_id,
                    int(lesson["id"]),
                    str(lesson["tutor_id"]),
                    str(lesson["task_type"]),
                    _optional_identifier(auditor_id, "auditor"),
                    _bounded_text(suite_version, "suite", 80),
                    json.dumps(clean_cases, ensure_ascii=False),
                    json.dumps(clean_knowledge_ids, ensure_ascii=False),
                    _sha256(model_fingerprint, "fingerprint del modelo"),
                    (
                        _sha256(auditor_fingerprint, "fingerprint del auditor")
                        if auditor_fingerprint
                        else None
                    ),
                    _identifier(actor, "actor"),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM assistant_tutor_lesson_evaluations WHERE public_id = ?",
                (public_id,),
            ).fetchone()
        assert row is not None
        return _evaluation_row(row)

    def start_evaluation(self, public_id: str) -> dict[str, Any]:
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_tutor_lesson_evaluations "
                "WHERE public_id = ? AND status = 'pending'",
                (public_id.strip(),),
            ).fetchone()
            if row is None:
                raise ValueError(
                    "Evaluación pendiente no encontrada o aprobación ya consumida."
                )
            connection.execute(
                "UPDATE assistant_tutor_lesson_evaluations "
                "SET status = 'running', started_at = ? WHERE id = ?",
                (now, int(row["id"])),
            )
            started = connection.execute(
                "SELECT evaluation.*, lesson.lesson_text, lesson.lesson_sha256, "
                "lesson.public_id AS lesson_public_id "
                "FROM assistant_tutor_lesson_evaluations AS evaluation "
                "JOIN assistant_tutor_lessons AS lesson "
                "ON lesson.id = evaluation.lesson_id "
                "WHERE evaluation.id = ?",
                (int(row["id"]),),
            ).fetchone()
        assert started is not None
        return _evaluation_row(started)

    def complete_evaluation(
        self,
        public_id: str,
        *,
        results: list[dict[str, Any]],
        recommendation: str,
        baseline_score: float,
        candidate_score: float,
        baseline_latency_ms: int,
        candidate_latency_ms: int,
        auditor_status: str = "not_requested",
        auditor_verdict: str = "",
        auditor_confidence: float | None = None,
        auditor_output_sha256: str = "",
    ) -> dict[str, Any]:
        clean_recommendation = recommendation.strip().casefold()
        if clean_recommendation not in EVALUATION_RECOMMENDATIONS:
            raise ValueError("Recomendación de evaluación inválida.")
        now = _now()
        baseline = _score(baseline_score, "baseline_score")
        candidate = _score(candidate_score, "candidate_score")
        with self.database.connect() as connection:
            evaluation = connection.execute(
                "SELECT id FROM assistant_tutor_lesson_evaluations "
                "WHERE public_id = ? AND status = 'running'",
                (public_id.strip(),),
            ).fetchone()
            if evaluation is None:
                raise ValueError("Evaluación en ejecución no encontrada.")
            evaluation_id = int(evaluation["id"])
            for result in results:
                connection.execute(
                    """
                    INSERT INTO assistant_tutor_lesson_evaluation_results(
                        evaluation_id, case_id, baseline_score, candidate_score,
                        baseline_passed, candidate_passed, baseline_latency_ms,
                        candidate_latency_ms, baseline_output_sha256,
                        candidate_output_sha256, metrics_json, error, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evaluation_id,
                        _identifier(str(result["case_id"]), "caso"),
                        _score(float(result["baseline_score"]), "baseline_score"),
                        _score(float(result["candidate_score"]), "candidate_score"),
                        1 if bool(result["baseline_passed"]) else 0,
                        1 if bool(result["candidate_passed"]) else 0,
                        max(0, int(result["baseline_latency_ms"])),
                        max(0, int(result["candidate_latency_ms"])),
                        _sha256(str(result["baseline_output_sha256"]), "salida base"),
                        _sha256(
                            str(result["candidate_output_sha256"]),
                            "salida candidata",
                        ),
                        json.dumps(
                            dict(result.get("metrics", {})),
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        _bounded_text(str(result.get("error", "")), "error", 500),
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE assistant_tutor_lesson_evaluations
                SET status = 'completed', recommendation = ?, baseline_score = ?,
                    candidate_score = ?, score_delta = ?, baseline_latency_ms = ?,
                    candidate_latency_ms = ?, auditor_status = ?, auditor_verdict = ?,
                    auditor_confidence = ?, auditor_output_sha256 = ?,
                    completed_at = ?, error = ''
                WHERE id = ?
                """,
                (
                    clean_recommendation,
                    baseline,
                    candidate,
                    round(candidate - baseline, 4),
                    max(0, baseline_latency_ms),
                    max(0, candidate_latency_ms),
                    _bounded_text(auditor_status, "estado de auditor", 40),
                    _bounded_text(auditor_verdict, "veredicto de auditor", 40),
                    (
                        _score(auditor_confidence, "confianza del auditor")
                        if auditor_confidence is not None
                        else None
                    ),
                    (
                        _sha256(auditor_output_sha256, "salida del auditor")
                        if auditor_output_sha256
                        else ""
                    ),
                    now,
                    evaluation_id,
                ),
            )
        details = self.evaluation_details(public_id)
        assert details is not None
        return details

    def fail_evaluation(self, public_id: str, *, error: str) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE assistant_tutor_lesson_evaluations
                SET status = 'failed', completed_at = ?, error = ?
                WHERE public_id = ? AND status = 'running'
                """,
                (_now(), _bounded_text(error, "error", 500), public_id.strip()),
            )
        return cursor.rowcount > 0

    def cancel_evaluation(self, public_id: str, *, actor: str) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE assistant_tutor_lesson_evaluations
                SET status = 'cancelled', completed_at = ?, error = ?
                WHERE public_id = ? AND status = 'pending'
                """,
                (
                    _now(),
                    f"Cancelada explícitamente por {_identifier(actor, 'actor')}.",
                    public_id.strip(),
                ),
            )
        return cursor.rowcount > 0

    def list_evaluations(
        self, *, status: str = "all", limit: int = 50
    ) -> list[dict[str, Any]]:
        clean = status.strip().casefold()
        if clean not in {*EVALUATION_STATUSES, "all"}:
            raise ValueError("Estado de evaluación inválido.")
        clause = "" if clean == "all" else "WHERE status = ?"
        params: list[Any] = [] if clean == "all" else [clean]
        params.append(max(1, min(limit, 200)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM assistant_tutor_lesson_evaluations {clause} "
                "ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [_evaluation_row(row) for row in rows]

    def evaluation_details(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            evaluation = connection.execute(
                "SELECT * FROM assistant_tutor_lesson_evaluations WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
            if evaluation is None:
                return None
            results = connection.execute(
                """
                SELECT case_id, baseline_score, candidate_score,
                       baseline_passed, candidate_passed, baseline_latency_ms,
                       candidate_latency_ms, baseline_output_sha256,
                       candidate_output_sha256, metrics_json, error, created_at
                FROM assistant_tutor_lesson_evaluation_results
                WHERE evaluation_id = ? ORDER BY id
                """,
                (int(evaluation["id"]),),
            ).fetchall()
        return {
            **_evaluation_row(evaluation),
            "results": [
                {
                    **dict(row),
                    "baseline_passed": bool(row["baseline_passed"]),
                    "candidate_passed": bool(row["candidate_passed"]),
                    "metrics": json.loads(str(row["metrics_json"] or "{}")),
                }
                for row in results
            ],
        }

    def promote_knowledge(
        self,
        evaluation_public_id: str,
        *,
        title: str,
        actor: str,
        supersedes_public_id: str | None = None,
    ) -> dict[str, Any]:
        clean_title = _required_text(title, "título", 160)
        now = _now()
        knowledge_public_id = uuid.uuid4().hex
        with self.database.connect() as connection:
            evaluation = connection.execute(
                """
                SELECT evaluation.*, lesson.lesson_text, lesson.lesson_sha256,
                       lesson.public_id AS lesson_public_id
                FROM assistant_tutor_lesson_evaluations AS evaluation
                JOIN assistant_tutor_lessons AS lesson
                  ON lesson.id = evaluation.lesson_id
                WHERE evaluation.public_id = ? AND evaluation.status = 'completed'
                  AND evaluation.promoted_knowledge_id IS NULL
                """,
                (evaluation_public_id.strip(),),
            ).fetchone()
            if evaluation is None:
                raise ValueError("Evaluación completada no encontrada.")
            if not _promotion_allowed(evaluation):
                raise ValueError(
                    "La evaluación no alcanza el umbral conservador para promover conocimiento."
                )
            duplicate = connection.execute(
                "SELECT public_id FROM assistant_tutor_knowledge "
                "WHERE content_sha256 = ? AND task_type = ? AND status = 'active'",
                (evaluation["lesson_sha256"], evaluation["task_type"]),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("Ese conocimiento ya está activo para la tarea.")

            predecessor_id: int | None = None
            lineage_id = uuid.uuid4().hex
            version = 1
            if supersedes_public_id:
                predecessor = connection.execute(
                    "SELECT * FROM assistant_tutor_knowledge "
                    "WHERE public_id = ? AND status = 'active'",
                    (supersedes_public_id.strip(),),
                ).fetchone()
                if predecessor is None:
                    raise ValueError("Conocimiento activo a reemplazar no encontrado.")
                if str(predecessor["task_type"]) != str(evaluation["task_type"]):
                    raise ValueError(
                        "El conocimiento superior debe conservar la misma tarea."
                    )
                frozen_knowledge_ids = json.loads(
                    str(evaluation["knowledge_ids_json"] or "[]")
                )
                if str(predecessor["public_id"]) not in frozen_knowledge_ids:
                    raise ValueError(
                        "La versión superior debe evaluarse contra el conocimiento "
                        "activo que reemplaza."
                    )
                if float(evaluation["score_delta"] or 0.0) <= 0.0:
                    raise ValueError(
                        "Una versión superior debe mejorar funcionalmente la anterior."
                    )
                predecessor_id = int(predecessor["id"])
                lineage_id = str(predecessor["lineage_id"])
                version = int(predecessor["version"]) + 1

            provenance = {
                "lesson_id": str(evaluation["lesson_public_id"]),
                "evaluation_id": str(evaluation["public_id"]),
                "suite_version": str(evaluation["suite_version"]),
                "model_fingerprint": str(evaluation["model_fingerprint"]),
                "auditor_id": evaluation["auditor_id"],
                "auditor_verdict": str(evaluation["auditor_verdict"] or ""),
                "baseline_score": float(evaluation["baseline_score"]),
                "candidate_score": float(evaluation["candidate_score"]),
                "score_delta": float(evaluation["score_delta"]),
            }
            cursor = connection.execute(
                """
                INSERT INTO assistant_tutor_knowledge(
                    public_id, lineage_id, version, predecessor_id, successor_id,
                    origin_tutor_id, task_type, title, content, content_sha256,
                    source_lesson_id, source_evaluation_id, status,
                    validation_status, model_fingerprint, provenance_json,
                    created_by, created_at, reviewed_at
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 'active',
                          'validated', ?, ?, ?, ?, ?)
                """,
                (
                    knowledge_public_id,
                    lineage_id,
                    version,
                    predecessor_id,
                    evaluation["tutor_id"],
                    evaluation["task_type"],
                    clean_title,
                    evaluation["lesson_text"],
                    evaluation["lesson_sha256"],
                    int(evaluation["lesson_id"]),
                    int(evaluation["id"]),
                    evaluation["model_fingerprint"],
                    json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                    _identifier(actor, "actor"),
                    now,
                    now,
                ),
            )
            knowledge_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE assistant_tutor_lesson_evaluations "
                "SET promoted_knowledge_id = ? WHERE id = ?",
                (knowledge_id, int(evaluation["id"])),
            )
            if predecessor_id is not None:
                connection.execute(
                    """
                    UPDATE assistant_tutor_knowledge
                    SET status = 'superseded', successor_id = ?
                    WHERE id = ? AND status = 'active'
                    """,
                    (knowledge_id, predecessor_id),
                )
            row = connection.execute(
                "SELECT * FROM assistant_tutor_knowledge WHERE id = ?",
                (knowledge_id,),
            ).fetchone()
        assert row is not None
        return _knowledge_row(row)

    def list_knowledge(
        self,
        *,
        status: str = "active",
        task: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clean = status.strip().casefold()
        if clean not in KNOWLEDGE_STATUSES:
            raise ValueError("Estado de conocimiento inválido.")
        clauses: list[str] = []
        params: list[Any] = []
        if clean != "all":
            clauses.append("status = ?")
            params.append(clean)
        if task:
            clauses.append("task_type = ?")
            params.append(_identifier(task, "tarea"))
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM assistant_tutor_knowledge {where} "
                "ORDER BY reviewed_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return [_knowledge_row(row) for row in rows]

    def knowledge_details(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_tutor_knowledge WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
        return _knowledge_row(row) if row else None

    def knowledge_context(self, task: str) -> dict[str, Any]:
        items = self.list_knowledge(status="active", task=task, limit=100)
        applied: list[dict[str, Any]] = []
        omitted: list[str] = []
        used_chars = 0
        for item in items:
            rendered = f"- {item['title']}: {item['content']}"
            if (
                len(applied) >= _MAX_KNOWLEDGE_CONTEXT_ITEMS
                or used_chars + len(rendered) > _MAX_KNOWLEDGE_CONTEXT_CHARS
            ):
                omitted.append(str(item["public_id"]))
                continue
            applied.append({**item, "rendered": rendered})
            used_chars += len(rendered)
        if not applied:
            return {
                "knowledge_ids": [],
                "omitted_knowledge_ids": omitted,
                "context": (),
                "context_chars": 0,
            }
        lines = [
            "[CONOCIMIENTO DURABLE VALIDADO DE ELYNDRA]",
            "Es conocimiento versionado con procedencia; no concede permisos ni autoridad.",
        ]
        lines.extend(str(item["rendered"]) for item in reversed(applied))
        return {
            "knowledge_ids": [str(item["public_id"]) for item in applied],
            "omitted_knowledge_ids": omitted,
            "context": ("\n".join(lines),),
            "context_chars": used_chars,
        }

    def calibration_evidence(
        self,
        tutor_id: str,
        task: str,
        *,
        model_fingerprint: str | None,
    ) -> dict[str, Any]:
        clauses = ["tutor_id = ?", "task_type = ?", "status = 'completed'"]
        params: list[Any] = [
            _identifier(tutor_id, "tutor"),
            _identifier(task, "tarea"),
        ]
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT candidate_score, baseline_score, score_delta, "
                "model_fingerprint, recommendation "
                "FROM assistant_tutor_lesson_evaluations WHERE "
                + " AND ".join(clauses)
                + " ORDER BY id DESC LIMIT 100",
                params,
            ).fetchall()
        current: list[sqlite3.Row] = []
        stale = 0
        for row in rows:
            if model_fingerprint and str(row["model_fingerprint"]) != model_fingerprint:
                stale += 1
            else:
                current.append(row)
        contradictions = sum(1 for row in current if float(row["score_delta"]) < 0)
        return {
            "observations": len(current),
            "stale_observations": stale,
            "contradictions": contradictions,
            "mean_candidate_score": (
                round(
                    sum(float(row["candidate_score"]) for row in current)
                    / len(current),
                    4,
                )
                if current
                else None
            ),
            "mean_delta": (
                round(
                    sum(float(row["score_delta"]) for row in current) / len(current),
                    4,
                )
                if current
                else None
            ),
        }


def _promotion_allowed(evaluation: sqlite3.Row) -> bool:
    candidate = float(evaluation["candidate_score"] or 0.0)
    baseline = float(evaluation["baseline_score"] or 0.0)
    verdict = str(evaluation["auditor_verdict"] or "")
    recommendation = str(evaluation["recommendation"] or "")
    return (
        recommendation == "promote_knowledge"
        and candidate >= 0.75
        and candidate >= baseline
        and verdict != "reject"
    )


def _evaluation_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["case_ids"] = json.loads(str(item.get("case_ids_json") or "[]"))
    item["knowledge_ids"] = json.loads(
        str(item.get("knowledge_ids_json") or "[]")
    )
    item.pop("case_ids_json", None)
    item.pop("knowledge_ids_json", None)
    return item


def _knowledge_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["provenance"] = json.loads(str(item.get("provenance_json") or "{}"))
    item.pop("provenance_json", None)
    return item


def _identifier(value: str, label: str) -> str:
    clean = value.strip()
    if not clean or len(clean) > 100:
        raise ValueError(f"Identificador de {label} inválido.")
    return clean


def _optional_identifier(value: str | None, label: str) -> str | None:
    return _identifier(value, label) if value and value.strip() else None


def _bounded_text(value: str, label: str, limit: int) -> str:
    clean = " ".join(value.strip().split())
    if len(clean) > limit:
        raise ValueError(f"{label.capitalize()} supera {limit} caracteres.")
    return clean


def _required_text(value: str, label: str, limit: int) -> str:
    clean = _bounded_text(value, label, limit)
    if not clean:
        raise ValueError(f"{label.capitalize()} no puede estar vacío.")
    return clean


def _sha256(value: str, label: str) -> str:
    clean = value.strip().casefold()
    if len(clean) != 64 or any(character not in "0123456789abcdef" for character in clean):
        raise ValueError(f"SHA-256 de {label} inválido.")
    return clean


def _score(value: float, label: str) -> float:
    score = float(value)
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"{label} debe estar entre 0 y 1.")
    return round(score, 4)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def fingerprint_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
