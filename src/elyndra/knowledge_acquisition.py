from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from datetime import UTC, date, datetime
from typing import Any

from elyndra.db import Database

KNOWLEDGE_KINDS = (
    "factual",
    "conceptual",
    "procedural",
    "linguistic",
    "domain",
)
PLAN_STATUSES = (
    "pending",
    "running",
    "reviewed",
    "failed",
    "cancelled",
    "promoted",
    "all",
)
KNOWLEDGE_STATUSES = ("active", "superseded", "all")
SOURCE_TYPES = ("owner_statement", "reviewed_text", "alexandria_reviewed")
_MAX_EVIDENCE_CHARS = 12_000
_MAX_EVIDENCE_PACKAGE_CHARS = 24_000
_MAX_EVIDENCE_SOURCES = 8
_MAX_CANDIDATE_CHARS = 4_000
_MAX_CONTEXT_ITEMS = 6
_MAX_CONTEXT_CHARS = 2_800
_TEACHING_PREFIXES = (
    "aprende que ",
    "quiero que aprendas que ",
    "te enseño que ",
    "te enseno que ",
    "learn that ",
)
_QUESTION_PREFIXES = (
    "que es ",
    "qué es ",
    "quien es ",
    "quién es ",
    "explica ",
    "explicame ",
    "explícame ",
    "dime sobre ",
    "what is ",
    "who is ",
    "explain ",
)

_CONFIDENCE_LABELS = {
    "muy baja": 0.2,
    "very low": 0.2,
    "baja": 0.35,
    "low": 0.35,
    "media": 0.55,
    "medio": 0.55,
    "moderada": 0.55,
    "moderate": 0.55,
    "medium": 0.55,
    "alta": 0.8,
    "high": 0.8,
    "muy alta": 0.9,
    "very high": 0.9,
}
_CONFLICT_STATUSES = ("open", "resolved", "all")
_CONFLICT_RESOLUTIONS = ("compatible", "superseded_by_version")

_RETRIEVAL_STOPWORDS = {
    "a", "al", "algo", "and", "como", "con", "cual", "cuales",
    "de", "del", "dime", "el", "en", "es", "esta", "este", "explica",
    "explicame", "for", "how", "la", "las", "lo", "los", "me", "of",
    "para", "por", "que", "sobre", "the", "un", "una", "what", "who",
    "y",
}


class GeneralKnowledgeRepository:
    """Reviewed general knowledge proposals and immutable version lineage."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def status(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            counts = {
                "plans": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_knowledge_acquisition_plans"
                    ).fetchone()[0]
                ),
                "pending_plans": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_knowledge_acquisition_plans "
                        "WHERE status = 'pending'"
                    ).fetchone()[0]
                ),
                "reviewed_plans": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_knowledge_acquisition_plans "
                        "WHERE status = 'reviewed'"
                    ).fetchone()[0]
                ),
                "active_knowledge": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_general_knowledge "
                        "WHERE status = 'active'"
                    ).fetchone()[0]
                ),
                "superseded_knowledge": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_general_knowledge "
                        "WHERE status = 'superseded'"
                    ).fetchone()[0]
                ),
                "failed_plans": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_knowledge_acquisition_plans "
                        "WHERE status = 'failed'"
                    ).fetchone()[0]
                ),
                "open_conflicts": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_knowledge_conflicts "
                        "WHERE status = 'open'"
                    ).fetchone()[0]
                ),
                "revalidation_due": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_general_knowledge "
                        "WHERE status = 'active' AND revalidate_after IS NOT NULL "
                        "AND revalidate_after <= ?",
                        (_today(),),
                    ).fetchone()[0]
                ),
            }
        return {
            **counts,
            "owner_approval_required": True,
            "foreground_only": True,
            "single_use_execution": True,
            "deterministic_audit_required": True,
            "model_auditor_optional": True,
            "auditor_advisory_only": True,
            "automatic_promotion": False,
            "silent_learning": False,
            "knowledge_deletion_allowed": False,
            "knowledge_versioned": True,
            "qualitative_confidence_normalization": True,
            "failed_plan_retry": True,
            "conflict_review_required": True,
            "revalidation_is_non_destructive": True,
            "approved_metadata_is_immutable": True,
            "multisource_evidence_packages": True,
            "independent_source_hashes": True,
            "domain_project_scoping": True,
            "cross_auditor_review": True,
            "max_evidence_chars": _MAX_EVIDENCE_CHARS,
            "max_evidence_package_chars": _MAX_EVIDENCE_PACKAGE_CHARS,
            "max_evidence_sources": _MAX_EVIDENCE_SOURCES,
            "max_context_items": _MAX_CONTEXT_ITEMS,
            "max_context_chars": _MAX_CONTEXT_CHARS,
        }

    def create_plan(
        self,
        *,
        kind: str,
        subject: str,
        question: str,
        locale: str,
        source_type: str,
        source_title: str,
        source_ref: str,
        source_observed_at: str | None = None,
        revalidate_after: str | None = None,
        evidence_text: str,
        source_unit_ids: tuple[int, ...],
        tutor_id: str,
        model_fingerprint: str,
        actor: str,
        auditor_id: str | None = None,
        auditor_fingerprint: str | None = None,
        evidence_sources: tuple[dict[str, Any], ...] = (),
        domain: str = "",
        project: str = "",
        auditor_ids: tuple[str, ...] = (),
        auditor_fingerprints: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        clean_kind = _kind(kind)
        prepared_sources = _prepare_evidence_sources(
            evidence_sources
            or (
                {
                    "source_type": source_type,
                    "source_title": source_title,
                    "source_ref": source_ref,
                    "source_observed_at": source_observed_at,
                    "revalidate_after": revalidate_after,
                    "evidence_text": evidence_text,
                    "source_unit_ids": source_unit_ids,
                },
            )
        )
        primary_source = prepared_sources[0]
        observed_dates = [
            str(source["source_observed_at"])
            for source in prepared_sources
            if source["source_observed_at"]
        ]
        revalidation_dates = [
            str(source["revalidate_after"])
            for source in prepared_sources
            if source["revalidate_after"]
        ]
        package_observed_at = max(observed_dates) if observed_dates else None
        package_revalidate_after = (
            min(revalidation_dates) if revalidation_dates else None
        )
        clean_evidence = _render_evidence_package(prepared_sources)
        clean_units = tuple(
            dict.fromkeys(
                int(item)
                for source in prepared_sources
                for item in source["source_unit_ids"]
            )
        )
        clean_auditor_ids = tuple(
            dict.fromkeys(
                _identifier(item, "auditor")
                for item in auditor_ids
            )
        )
        if auditor_id and auditor_id not in clean_auditor_ids:
            clean_auditor_ids = (auditor_id, *clean_auditor_ids)
        if len(clean_auditor_ids) > 4:
            raise ValueError("Un plan admite como máximo cuatro auditores.")
        clean_auditor_fingerprints = {
            _identifier(key, "auditor"): _sha256(value, "auditor")
            for key, value in (auditor_fingerprints or {}).items()
        }
        if auditor_id and auditor_fingerprint:
            clean_auditor_fingerprints[auditor_id] = _sha256(
                auditor_fingerprint, "auditor"
            )
        now = _now()
        public_id = uuid.uuid4().hex
        values = (
            public_id,
            clean_kind,
            _required_text(subject, "tema", 160),
            _required_text(question, "pregunta", 500),
            _required_text(locale, "locale", 24),
            primary_source["source_type"],
            primary_source["source_title"],
            primary_source["source_ref"],
            package_observed_at,
            package_revalidate_after,
            clean_evidence,
            hashlib.sha256(clean_evidence.encode()).hexdigest(),
            json.dumps(clean_units),
            json.dumps(prepared_sources, ensure_ascii=False, sort_keys=True),
            _bounded_text(domain, "dominio", 100),
            _bounded_text(project, "proyecto", 160),
            _identifier(tutor_id, "tutor"),
            clean_auditor_ids[0] if clean_auditor_ids else None,
            json.dumps(clean_auditor_ids, ensure_ascii=False),
            _sha256(model_fingerprint, "modelo"),
            (
                clean_auditor_fingerprints.get(clean_auditor_ids[0])
                if clean_auditor_ids
                else None
            ),
            json.dumps(
                clean_auditor_fingerprints,
                ensure_ascii=False,
                sort_keys=True,
            ),
            "pending",
            "{}",
            "",
            "{}",
            "not_requested",
            "",
            None,
            "",
            "[]",
            None,
            _identifier(actor, "actor"),
            now,
            None,
            None,
            "",
        )
        placeholders = ", ".join("?" for _ in values)
        with self.database.connect() as connection:
            connection.execute(
                f"""
                INSERT INTO assistant_knowledge_acquisition_plans(
                    public_id, knowledge_kind, subject, question, locale,
                    source_type, source_title, source_ref, source_observed_at,
                    revalidate_after, evidence_text, evidence_sha256,
                    source_unit_ids_json, evidence_sources_json, domain, project,
                    tutor_id, auditor_id, auditor_ids_json,
                    model_fingerprint, auditor_fingerprint,
                    auditor_fingerprints_json, status,
                    candidate_json, candidate_sha256, deterministic_audit_json,
                    auditor_status, auditor_verdict, auditor_confidence,
                    auditor_output_sha256, audit_reviews_json,
                    promoted_knowledge_id, created_by, created_at, started_at,
                    completed_at, error
                ) VALUES ({placeholders})
                """,
                values,
            )
            row = connection.execute(
                "SELECT * FROM assistant_knowledge_acquisition_plans "
                "WHERE public_id = ?",
                (public_id,),
            ).fetchone()
        assert row is not None
        return _plan_row(row)

    def create_owner_proposal(
        self,
        *,
        statement: str,
        subject: str,
        kind: str,
        locale: str,
        actor: str,
        source_observed_at: str | None = None,
        revalidate_after: str | None = None,
        domain: str = "",
        project: str = "",
    ) -> dict[str, Any]:
        clean = _required_text(statement, "conocimiento", _MAX_CANDIDATE_CHARS)
        candidate = {
            "kind": _kind(kind),
            "subject": _required_text(subject, "tema", 160),
            "title": _required_text(subject, "título", 160),
            "content": clean,
            "claims": [clean],
            "keywords": _keywords(subject),
            "limitations": [
                "Declaración explícita del propietario; requiere promoción separada."
            ],
            "locale": _required_text(locale, "locale", 24),
            "confidence": 1.0,
        }
        plan = self.create_plan(
            kind=candidate["kind"],
            subject=candidate["subject"],
            question=f"Aprender la declaración explícita sobre {candidate['subject']}",
            locale=candidate["locale"],
            source_type="owner_statement",
            source_title="Enseñanza explícita del propietario",
            source_ref="owner",
            source_observed_at=source_observed_at,
            revalidate_after=revalidate_after,
            evidence_text=clean,
            source_unit_ids=(),
            tutor_id="owner",
            model_fingerprint="0" * 64,
            actor=actor,
            domain=domain,
            project=project,
        )
        audit = {
            "verdict": "support",
            "support_ratio": 1.0,
            "claim_count": 1,
            "supported_claims": 1,
            "reason": "La propuesta conserva literalmente la declaración revisable.",
        }
        return self._complete_reviewed_plan(
            str(plan["public_id"]),
            candidate=candidate,
            deterministic_audit=audit,
            auditor_status="not_requested",
            auditor_verdict="",
            auditor_confidence=None,
            auditor_output_sha256="",
        )

    def start_plan(self, public_id: str) -> dict[str, Any]:
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_knowledge_acquisition_plans "
                "WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
            if row is None:
                raise ValueError("Plan de adquisición no encontrado.")
            status = str(row["status"])
            if status != "pending":
                guidance = {
                    "failed": "crea un nuevo plan con knowledge-acquisition-retry",
                    "reviewed": "usa knowledge-promote con este mismo ID",
                    "promoted": "el conocimiento ya fue promovido",
                    "cancelled": "crea un plan nuevo",
                    "running": "la ejecución ya consumió la aprobación",
                }.get(status, "revisa el estado del plan")
                raise ValueError(
                    f"El plan está {status}; {guidance}."
                )
            connection.execute(
                "UPDATE assistant_knowledge_acquisition_plans "
                "SET status = 'running', started_at = ? WHERE id = ?",
                (now, int(row["id"])),
            )
            started = connection.execute(
                "SELECT * FROM assistant_knowledge_acquisition_plans WHERE id = ?",
                (int(row["id"]),),
            ).fetchone()
        assert started is not None
        return _plan_row(started)

    def complete_plan(
        self,
        public_id: str,
        *,
        candidate: dict[str, Any],
        auditor_status: str,
        auditor_verdict: str,
        auditor_confidence: float | None,
        auditor_output_sha256: str,
        audit_reviews: tuple[dict[str, Any], ...] = (),
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_knowledge_acquisition_plans "
                "WHERE public_id = ? AND status = 'running'",
                (public_id.strip(),),
            ).fetchone()
        if row is None:
            raise ValueError("Plan en ejecución no encontrado.")
        prepared = _candidate(
            candidate,
            approved_kind=str(row["knowledge_kind"]),
            approved_subject=str(row["subject"]),
            approved_locale=str(row["locale"]),
        )
        audit = deterministic_evidence_audit(prepared, str(row["evidence_text"]))
        return self._complete_reviewed_plan(
            public_id,
            candidate=prepared,
            deterministic_audit=audit,
            auditor_status=auditor_status,
            auditor_verdict=auditor_verdict,
            auditor_confidence=auditor_confidence,
            auditor_output_sha256=auditor_output_sha256,
            audit_reviews=audit_reviews,
        )

    def _complete_reviewed_plan(
        self,
        public_id: str,
        *,
        candidate: dict[str, Any],
        deterministic_audit: dict[str, Any],
        auditor_status: str,
        auditor_verdict: str,
        auditor_confidence: float | None,
        auditor_output_sha256: str,
        audit_reviews: tuple[dict[str, Any], ...] = (),
    ) -> dict[str, Any]:
        prepared = _candidate(candidate)
        candidate_json = json.dumps(prepared, ensure_ascii=False, sort_keys=True)
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id, status, domain, project "
                "FROM assistant_knowledge_acquisition_plans "
                "WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
            if row is None or str(row["status"]) not in {"pending", "running"}:
                raise ValueError("Plan no disponible para completar la revisión.")
            related = self._related_active_knowledge(
                connection,
                prepared,
                domain=str(row["domain"] or ""),
                project=str(row["project"] or ""),
            )
            related_ids = [str(item["public_id"]) for item in related]
            candidate_hash = hashlib.sha256(str(prepared["content"]).encode()).hexdigest()
            conflict_status = (
                "duplicate"
                if any(
                    str(item["content_sha256"]) == candidate_hash
                    for item in related
                )
                else "review_required" if related else "none"
            )
            connection.execute(
                """
                UPDATE assistant_knowledge_acquisition_plans
                SET status = 'reviewed', candidate_json = ?, candidate_sha256 = ?,
                    deterministic_audit_json = ?, related_knowledge_ids_json = ?,
                    conflict_status = ?, auditor_status = ?,
                    auditor_verdict = ?, auditor_confidence = ?,
                    auditor_output_sha256 = ?, audit_reviews_json = ?,
                    completed_at = ?, error = ''
                WHERE id = ?
                """,
                (
                    candidate_json,
                    hashlib.sha256(candidate_json.encode()).hexdigest(),
                    json.dumps(deterministic_audit, ensure_ascii=False, sort_keys=True),
                    json.dumps(related_ids, ensure_ascii=False),
                    conflict_status,
                    _bounded_text(auditor_status, "estado del auditor", 40),
                    _bounded_text(auditor_verdict, "veredicto del auditor", 40),
                    _optional_score(auditor_confidence),
                    _optional_sha256(auditor_output_sha256),
                    json.dumps(audit_reviews, ensure_ascii=False, sort_keys=True),
                    now,
                    int(row["id"]),
                ),
            )
            completed = connection.execute(
                "SELECT * FROM assistant_knowledge_acquisition_plans WHERE id = ?",
                (int(row["id"]),),
            ).fetchone()
        assert completed is not None
        return _plan_row(completed)

    @staticmethod
    def _related_active_knowledge(
        connection: sqlite3.Connection,
        candidate: dict[str, Any],
        *,
        domain: str = "",
        project: str = "",
    ) -> list[sqlite3.Row]:
        rows = connection.execute(
            "SELECT * FROM assistant_general_knowledge "
            "WHERE status = 'active' AND knowledge_kind = ? "
            "AND domain = ? AND project = ?",
            (str(candidate["kind"]), domain, project),
        ).fetchall()
        subject = _normalize(str(candidate["subject"]))
        return [row for row in rows if _normalize(str(row["subject"])) == subject]

    def fail_plan(self, public_id: str, *, error: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE assistant_knowledge_acquisition_plans "
                "SET status = 'failed', error = ?, completed_at = ? "
                "WHERE public_id = ? AND status = 'running'",
                (_bounded_text(error, "error", 500), _now(), public_id.strip()),
            )

    def cancel_plan(self, public_id: str) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE assistant_knowledge_acquisition_plans "
                "SET status = 'cancelled', completed_at = ? "
                "WHERE public_id = ? AND status = 'pending'",
                (_now(), public_id.strip()),
            )
        return cursor.rowcount == 1

    def retry_failed_plan(
        self,
        public_id: str,
        *,
        model_fingerprint: str,
        actor: str,
        auditor_fingerprint: str | None = None,
        auditor_fingerprints: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_knowledge_acquisition_plans "
                "WHERE public_id = ? AND status = 'failed'",
                (public_id.strip(),),
            ).fetchone()
        if row is None:
            raise ValueError("Plan fallido no encontrado para reintento.")
        return self.create_plan(
            kind=str(row["knowledge_kind"]),
            subject=str(row["subject"]),
            question=str(row["question"]),
            locale=str(row["locale"]),
            source_type=str(row["source_type"]),
            source_title=str(row["source_title"]),
            source_ref=str(row["source_ref"]),
            source_observed_at=row["source_observed_at"],
            revalidate_after=row["revalidate_after"],
            evidence_text=str(row["evidence_text"]),
            source_unit_ids=tuple(
                int(item)
                for item in json.loads(str(row["source_unit_ids_json"] or "[]"))
            ),
            tutor_id=str(row["tutor_id"]),
            model_fingerprint=model_fingerprint,
            actor=actor,
            auditor_id=row["auditor_id"],
            auditor_fingerprint=auditor_fingerprint,
            evidence_sources=tuple(
                json.loads(str(row["evidence_sources_json"] or "[]"))
            ),
            domain=str(row["domain"] or ""),
            project=str(row["project"] or ""),
            auditor_ids=tuple(
                json.loads(str(row["auditor_ids_json"] or "[]"))
            ),
            auditor_fingerprints=auditor_fingerprints,
        )

    def list_plans(self, *, status: str = "pending", limit: int = 100) -> list[dict[str, Any]]:
        clean = status.strip().casefold()
        if clean not in PLAN_STATUSES:
            raise ValueError("Estado de plan inválido.")
        where = "" if clean == "all" else "WHERE status = ?"
        params: list[Any] = [] if clean == "all" else [clean]
        params.append(max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM assistant_knowledge_acquisition_plans {where} "
                "ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [_plan_row(row) for row in rows]

    def plan_details(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_knowledge_acquisition_plans WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
        return _plan_row(row) if row else None

    def promote(
        self,
        plan_public_id: str,
        *,
        actor: str,
        title: str | None = None,
        supersedes_public_id: str | None = None,
        replacement_reason: str = "",
        parallel_reason: str = "",
    ) -> dict[str, Any]:
        now = _now()
        with self.database.connect() as connection:
            plan = connection.execute(
                "SELECT * FROM assistant_knowledge_acquisition_plans "
                "WHERE public_id = ?",
                (plan_public_id.strip(),),
            ).fetchone()
            if plan is None:
                raise ValueError("Plan o propuesta de conocimiento no encontrada.")
            status = str(plan["status"])
            if status != "reviewed":
                guidance = {
                    "pending": "ejecuta knowledge-acquisition-run primero",
                    "running": "la síntesis todavía está en ejecución",
                    "failed": "crea un nuevo plan con knowledge-acquisition-retry",
                    "cancelled": "crea un plan nuevo",
                    "promoted": "esta propuesta ya fue promovida",
                }.get(status, "revisa el estado del plan")
                raise ValueError(
                    f"El ID corresponde a un plan {status}; {guidance}."
                )
            if plan["promoted_knowledge_id"] is not None:
                raise ValueError("La propuesta ya fue promovida.")
            candidate = json.loads(str(plan["candidate_json"] or "{}"))
            audit = json.loads(str(plan["deterministic_audit_json"] or "{}"))
            if str(audit.get("verdict")) != "support":
                raise ValueError("La auditoría determinista no respalda la promoción.")
            if str(plan["auditor_verdict"] or "") == "reject":
                raise ValueError("El auditor consultivo rechazó la propuesta.")
            confidence = float(candidate.get("confidence", 0.0))
            if confidence < 0.65:
                raise ValueError("La confianza revisada es insuficiente para promoción.")
            live_related = self._related_active_knowledge(
                connection,
                candidate,
                domain=str(plan["domain"] or ""),
                project=str(plan["project"] or ""),
            )
            related_ids = [str(item["public_id"]) for item in live_related]
            candidate_content_hash = hashlib.sha256(
                str(candidate["content"]).encode()
            ).hexdigest()
            if any(
                str(item["content_sha256"]) == candidate_content_hash
                for item in live_related
            ):
                raise ValueError(
                    "Ya existe conocimiento activo con el mismo contenido."
                )
            remaining_related = [
                item for item in related_ids if item != (supersedes_public_id or "")
            ]
            if remaining_related and not parallel_reason.strip():
                raise ValueError(
                    "La propuesta se relaciona con conocimiento activo. "
                    "Indica --supersedes o una razón explícita para conservarla "
                    "en paralelo."
                )
            clean_parallel_reason = (
                _required_text(parallel_reason, "razón de coexistencia", 500)
                if remaining_related
                else ""
            )

            predecessor_id: int | None = None
            lineage_id = uuid.uuid4().hex
            version = 1
            if supersedes_public_id:
                predecessor = connection.execute(
                    "SELECT * FROM assistant_general_knowledge "
                    "WHERE public_id = ? AND status = 'active'",
                    (supersedes_public_id.strip(),),
                ).fetchone()
                if predecessor is None:
                    raise ValueError("Conocimiento activo a sustituir no encontrado.")
                reason = _required_text(
                    replacement_reason,
                    "razón de actualización",
                    500,
                )
                candidate_content_hash = hashlib.sha256(
                    str(candidate["content"]).encode()
                ).hexdigest()
                if str(predecessor["content_sha256"]) == candidate_content_hash:
                    raise ValueError("La versión propuesta no cambia el conocimiento.")
                if str(predecessor["knowledge_kind"]) != str(candidate["kind"]):
                    raise ValueError("La versión superior debe conservar el tipo de conocimiento.")
                if _normalize(str(predecessor["subject"])) != _normalize(
                    str(candidate["subject"])
                ):
                    raise ValueError("La versión superior debe conservar el mismo tema.")
                if str(predecessor["domain"] or "") != str(plan["domain"] or ""):
                    raise ValueError("La versión superior debe conservar el mismo dominio.")
                if str(predecessor["project"] or "") != str(plan["project"] or ""):
                    raise ValueError("La versión superior debe conservar el mismo proyecto.")
                if confidence < float(predecessor["validation_confidence"]):
                    raise ValueError(
                        "La versión superior no puede reducir la confianza validada."
                    )
                predecessor_id = int(predecessor["id"])
                lineage_id = str(predecessor["lineage_id"])
                version = int(predecessor["version"]) + 1
            else:
                reason = ""

            provenance = {
                "plan_id": str(plan["public_id"]),
                "source_type": str(plan["source_type"]),
                "source_title": str(plan["source_title"]),
                "source_ref": str(plan["source_ref"]),
                "source_observed_at": plan["source_observed_at"],
                "revalidate_after": plan["revalidate_after"],
                "evidence_sha256": str(plan["evidence_sha256"]),
                "source_unit_ids": json.loads(str(plan["source_unit_ids_json"] or "[]")),
                "evidence_sources": json.loads(
                    str(plan["evidence_sources_json"] or "[]")
                ),
                "domain": str(plan["domain"] or ""),
                "project": str(plan["project"] or ""),
                "tutor_id": str(plan["tutor_id"]),
                "auditor_ids": json.loads(str(plan["auditor_ids_json"] or "[]")),
                "auditor_verdict": str(plan["auditor_verdict"] or ""),
                "audit_reviews": json.loads(str(plan["audit_reviews_json"] or "[]")),
                "deterministic_audit": audit,
                "confidence_input": candidate.get("confidence_input", ""),
                "confidence_mapping": candidate.get("confidence_mapping", ""),
                "model_metadata_mismatches": candidate.get(
                    "model_metadata_mismatches", {}
                ),
                "related_knowledge_ids": related_ids,
                "parallel_reason": clean_parallel_reason,
                "replacement_reason": reason,
            }
            public_id = uuid.uuid4().hex
            cursor = connection.execute(
                """
                INSERT INTO assistant_general_knowledge(
                    public_id, lineage_id, version, predecessor_id, successor_id,
                    knowledge_kind, subject, title, content, content_sha256,
                    claims_json, keywords_json, limitations_json, locale,
                    domain, project,
                    validation_confidence, source_observed_at, revalidate_after,
                    validation_status, last_revalidated_at, source_plan_id, status,
                    provenance_json, created_by, created_at, reviewed_at
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, 'validated', ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    public_id,
                    lineage_id,
                    version,
                    predecessor_id,
                    candidate["kind"],
                    candidate["subject"],
                    _required_text(title or str(candidate["title"]), "título", 160),
                    candidate["content"],
                    hashlib.sha256(str(candidate["content"]).encode()).hexdigest(),
                    json.dumps(candidate["claims"], ensure_ascii=False),
                    json.dumps(candidate["keywords"], ensure_ascii=False),
                    json.dumps(candidate["limitations"], ensure_ascii=False),
                    candidate["locale"],
                    str(plan["domain"] or ""),
                    str(plan["project"] or ""),
                    confidence,
                    plan["source_observed_at"],
                    plan["revalidate_after"],
                    now,
                    int(plan["id"]),
                    json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                    _identifier(actor, "actor"),
                    now,
                    now,
                ),
            )
            knowledge_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE assistant_knowledge_acquisition_plans "
                "SET status = 'promoted', promoted_knowledge_id = ? WHERE id = ?",
                (knowledge_id, int(plan["id"])),
            )
            if predecessor_id is not None:
                connection.execute(
                    "UPDATE assistant_general_knowledge "
                    "SET status = 'superseded', successor_id = ? "
                    "WHERE id = ? AND status = 'active'",
                    (knowledge_id, predecessor_id),
                )
                connection.execute(
                    "UPDATE assistant_knowledge_conflicts "
                    "SET status = 'resolved', resolution = 'superseded_by_version', "
                    "resolution_note = ?, resolved_by = ?, resolved_at = ? "
                    "WHERE status = 'open' AND "
                    "(knowledge_a_id = ? OR knowledge_b_id = ?)",
                    (
                        reason,
                        _identifier(actor, "actor"),
                        now,
                        predecessor_id,
                        predecessor_id,
                    ),
                )
            for related_public_id in remaining_related:
                related = connection.execute(
                    "SELECT id, subject FROM assistant_general_knowledge "
                    "WHERE public_id = ? AND status = 'active'",
                    (related_public_id,),
                ).fetchone()
                if related is None:
                    continue
                knowledge_a_id, knowledge_b_id = sorted(
                    (knowledge_id, int(related["id"]))
                )
                connection.execute(
                    "INSERT OR IGNORE INTO assistant_knowledge_conflicts("
                    "public_id, knowledge_a_id, knowledge_b_id, subject, "
                    "conflict_kind, status, resolution, resolution_note, "
                    "created_by, created_at, resolved_by, resolved_at"
                    ") VALUES (?, ?, ?, ?, 'potential_conflict', 'open', '', '', "
                    "?, ?, NULL, NULL)",
                    (
                        uuid.uuid4().hex,
                        knowledge_a_id,
                        knowledge_b_id,
                        str(related["subject"]),
                        _identifier(actor, "actor"),
                        now,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM assistant_general_knowledge WHERE id = ?",
                (knowledge_id,),
            ).fetchone()
        assert row is not None
        return _knowledge_row(row)

    def list_knowledge(
        self,
        *,
        status: str = "active",
        kind: str | None = None,
        domain: str = "",
        project: str = "",
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
        if kind:
            clauses.append("knowledge_kind = ?")
            params.append(_kind(kind))
        if domain:
            clauses.append("domain IN ('', ?)")
            params.append(_bounded_text(domain, "dominio", 100))
        if project:
            clauses.append("project IN ('', ?)")
            params.append(_bounded_text(project, "proyecto", 160))
        else:
            clauses.append("project = ''")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM assistant_general_knowledge {where} "
                "ORDER BY reviewed_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return [_knowledge_row(row) for row in rows]

    def knowledge_details(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_general_knowledge WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
        return _knowledge_row(row) if row else None

    def list_conflicts(
        self, *, status: str = "open", limit: int = 100
    ) -> list[dict[str, Any]]:
        clean = status.strip().casefold()
        if clean not in _CONFLICT_STATUSES:
            raise ValueError("Estado de conflicto inválido.")
        where = "" if clean == "all" else "WHERE conflict.status = ?"
        params: list[Any] = [] if clean == "all" else [clean]
        params.append(max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT conflict.*, a.public_id AS knowledge_a_public_id, "
                f"b.public_id AS knowledge_b_public_id "
                f"FROM assistant_knowledge_conflicts AS conflict "
                f"JOIN assistant_general_knowledge AS a "
                f"ON a.id = conflict.knowledge_a_id "
                f"JOIN assistant_general_knowledge AS b "
                f"ON b.id = conflict.knowledge_b_id {where} "
                f"ORDER BY conflict.id DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def conflict_details(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT conflict.*, a.public_id AS knowledge_a_public_id, "
                "b.public_id AS knowledge_b_public_id "
                "FROM assistant_knowledge_conflicts AS conflict "
                "JOIN assistant_general_knowledge AS a "
                "ON a.id = conflict.knowledge_a_id "
                "JOIN assistant_general_knowledge AS b "
                "ON b.id = conflict.knowledge_b_id "
                "WHERE conflict.public_id = ?",
                (public_id.strip(),),
            ).fetchone()
        return dict(row) if row else None

    def resolve_conflict(
        self,
        public_id: str,
        *,
        resolution: str,
        note: str,
        actor: str,
    ) -> dict[str, Any]:
        clean_resolution = resolution.strip().casefold()
        if clean_resolution not in _CONFLICT_RESOLUTIONS:
            raise ValueError("Resolución de conflicto inválida.")
        clean_note = _required_text(note, "nota de resolución", 500)
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_knowledge_conflicts "
                "WHERE public_id = ? AND status = 'open'",
                (public_id.strip(),),
            ).fetchone()
            if row is None:
                raise ValueError("Conflicto abierto no encontrado.")
            if clean_resolution == "superseded_by_version":
                statuses = connection.execute(
                    "SELECT status FROM assistant_general_knowledge "
                    "WHERE id IN (?, ?)",
                    (int(row["knowledge_a_id"]), int(row["knowledge_b_id"])),
                ).fetchall()
                if all(str(item["status"]) == "active" for item in statuses):
                    raise ValueError(
                        "La resolución por versión requiere una versión sustituida."
                    )
            connection.execute(
                "UPDATE assistant_knowledge_conflicts "
                "SET status = 'resolved', resolution = ?, resolution_note = ?, "
                "resolved_by = ?, resolved_at = ? WHERE id = ?",
                (
                    clean_resolution,
                    clean_note,
                    _identifier(actor, "actor"),
                    now,
                    int(row["id"]),
                ),
            )
        resolved = self.conflict_details(public_id)
        assert resolved is not None
        return resolved

    def revalidation_due(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assistant_general_knowledge "
                "WHERE status = 'active' AND revalidate_after IS NOT NULL "
                "AND revalidate_after <= ? "
                "ORDER BY revalidate_after ASC, id ASC LIMIT ?",
                (_today(), max(1, min(limit, 500))),
            ).fetchall()
        return [_knowledge_row(row) for row in rows]

    def _open_conflict_ids(self) -> set[int]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT knowledge_a_id, knowledge_b_id "
                "FROM assistant_knowledge_conflicts WHERE status = 'open'"
            ).fetchall()
        return {
            int(value)
            for row in rows
            for value in (row["knowledge_a_id"], row["knowledge_b_id"])
        }

    def search(
        self,
        query: str,
        *,
        domain: str = "",
        project: str = "",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        tokens = _tokens(query)
        if not tokens:
            return []
        clean_domain = _bounded_text(domain, "dominio", 100)
        clean_project = _bounded_text(project, "proyecto", 160)
        items = self.list_knowledge(
            status="active",
            domain=clean_domain,
            project=clean_project,
            limit=500,
        )
        open_conflict_ids = self._open_conflict_ids()
        ranked: list[tuple[float, dict[str, Any]]] = []
        for item in items:
            item = {
                **item,
                "open_conflict": int(item["id"]) in open_conflict_ids,
            }
            title_tokens = _tokens(f"{item['subject']} {item['title']}")
            keyword_tokens = _tokens(" ".join(item["keywords"]))
            content_tokens = _tokens(item["content"])
            title_hits = len(tokens & title_tokens)
            keyword_hits = len(tokens & keyword_tokens)
            content_hits = len(tokens & content_tokens)
            weighted = title_hits * 3 + keyword_hits * 2 + content_hits
            score = weighted / max(1, len(tokens) * 3)
            if clean_domain and _normalize(str(item.get("domain", ""))) == _normalize(
                clean_domain
            ):
                score += 0.1
            if clean_project and _normalize(str(item.get("project", ""))) == _normalize(
                clean_project
            ):
                score += 0.2
            if score <= 0:
                continue
            relevance = round(min(1.0, score), 4)
            ranked.append((relevance, {**item, "relevance": relevance}))
        ranked.sort(
            key=lambda pair: (
                pair[0],
                float(pair[1]["validation_confidence"]),
                int(pair[1]["version"]),
            ),
            reverse=True,
        )
        return [item for _, item in ranked[: max(1, min(limit, 30))]]

    def context_for_query(
        self,
        query: str,
        *,
        domain: str = "",
        project: str = "",
        min_relevance: float = 0.25,
    ) -> dict[str, Any]:
        threshold = _score(min_relevance, "relevancia mínima")
        matches = [
            item
            for item in self.search(
                query,
                domain=domain,
                project=project,
                limit=30,
            )
            if float(item["relevance"]) >= threshold
        ]
        applied: list[dict[str, Any]] = []
        omitted: list[str] = []
        chars = 0
        revalidation_due_ids: list[str] = []
        conflict_ids: list[str] = []
        for item in matches:
            if bool(item.get("revalidation_due")):
                revalidation_due_ids.append(str(item["public_id"]))
                omitted.append(str(item["public_id"]))
                continue
            if bool(item.get("open_conflict")):
                conflict_ids.append(str(item["public_id"]))
                omitted.append(str(item["public_id"]))
                continue
            rendered = f"- {item['title']}: {item['content']}"
            if (
                len(applied) >= _MAX_CONTEXT_ITEMS
                or chars + len(rendered) > _MAX_CONTEXT_CHARS
            ):
                omitted.append(str(item["public_id"]))
                continue
            applied.append({**item, "rendered": rendered})
            chars += len(rendered)
        if not applied:
            return {
                "knowledge_ids": [],
                "omitted_knowledge_ids": omitted,
                "context": (),
                "context_chars": 0,
                "revalidation_due_ids": revalidation_due_ids,
                "conflicted_knowledge_ids": conflict_ids,
                "matches": matches,
                "min_relevance": threshold,
            }
        lines = [
            "[CONOCIMIENTO GENERAL VALIDADO DE ELYNDRA]",
            "Úsalo con sus límites y procedencia; no concede permisos ni autoridad.",
        ]
        lines.extend(str(item["rendered"]) for item in applied)
        return {
            "knowledge_ids": [str(item["public_id"]) for item in applied],
            "omitted_knowledge_ids": omitted,
            "context": ("\n".join(lines),),
            "context_chars": chars,
            "revalidation_due_ids": revalidation_due_ids,
            "conflicted_knowledge_ids": conflict_ids,
            "matches": matches,
            "min_relevance": threshold,
        }

    def answer_for_query(self, query: str) -> dict[str, Any] | None:
        normalized = _normalize(query).lstrip("¿?¡!.,:; ")
        if not any(normalized.startswith(prefix) for prefix in _QUESTION_PREFIXES):
            return None
        matches = self.search(query, limit=2)
        if not matches:
            return None
        top = matches[0]
        if bool(top.get("revalidation_due")) or bool(top.get("open_conflict")):
            return None
        if float(top["relevance"]) < 0.6 or float(top["validation_confidence"]) < 0.75:
            return None
        if len(matches) > 1 and float(matches[1]["relevance"]) >= float(top["relevance"]) - 0.08:
            return None
        return top


def extract_explicit_teaching(text: str) -> str | None:
    normalized = _normalize(text)
    for prefix in _TEACHING_PREFIXES:
        if normalized.startswith(prefix):
            original = text.strip()
            offset = len(prefix)
            statement = original[offset:].strip(" .:\n\t")
            return statement or None
    return None


def deterministic_evidence_audit(
    candidate: dict[str, Any], evidence_text: str
) -> dict[str, Any]:
    evidence_tokens = _tokens(evidence_text)
    claims = [str(item) for item in candidate.get("claims", [])]
    supported = 0
    ratios: list[float] = []
    for claim in claims:
        claim_tokens = _tokens(claim)
        meaningful = {item for item in claim_tokens if len(item) >= 4}
        if not meaningful:
            ratio = 1.0 if claim_tokens <= evidence_tokens else 0.0
        else:
            ratio = len(meaningful & evidence_tokens) / len(meaningful)
        ratios.append(round(ratio, 4))
        if ratio >= 0.45:
            supported += 1
    support_ratio = supported / max(1, len(claims))
    verdict = "support" if claims and support_ratio >= 0.75 else "review"
    return {
        "verdict": verdict,
        "support_ratio": round(support_ratio, 4),
        "claim_count": len(claims),
        "supported_claims": supported,
        "claim_support": ratios,
        "evidence_sha256": hashlib.sha256(evidence_text.encode()).hexdigest(),
    }


def _prepare_evidence_sources(
    values: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    if not values or len(values) > _MAX_EVIDENCE_SOURCES:
        raise ValueError(
            f"El paquete requiere entre 1 y {_MAX_EVIDENCE_SOURCES} fuentes."
        )
    prepared: list[dict[str, Any]] = []
    total_chars = 0
    for index, value in enumerate(values, start=1):
        if not isinstance(value, dict):
            raise ValueError("Cada fuente de evidencia debe ser un objeto.")
        source_type = str(
            value.get("source_type", value.get("type", ""))
        ).strip().casefold()
        if source_type not in SOURCE_TYPES:
            raise ValueError(f"Tipo de fuente inválido en la fuente {index}.")
        evidence = _required_text(
            str(value.get("evidence_text", value.get("text", ""))),
            f"evidencia de fuente {index}",
            _MAX_EVIDENCE_CHARS,
        )
        total_chars += len(evidence)
        if total_chars > _MAX_EVIDENCE_PACKAGE_CHARS:
            raise ValueError(
                "El paquete de evidencia supera "
                f"{_MAX_EVIDENCE_PACKAGE_CHARS} caracteres."
            )
        units_value = value.get("source_unit_ids", value.get("unit_ids", ()))
        if units_value is None:
            units_value = ()
        if not isinstance(units_value, (list, tuple)):
            raise ValueError(f"Unidades inválidas en la fuente {index}.")
        unit_ids = tuple(
            dict.fromkeys(max(1, int(item)) for item in units_value)
        )
        if len(unit_ids) > 64:
            raise ValueError(f"La fuente {index} supera 64 unidades revisadas.")
        prepared.append(
            {
                "index": index,
                "source_type": source_type,
                "source_title": _required_text(
                    str(value.get("source_title", value.get("title", ""))),
                    f"título de fuente {index}",
                    240,
                ),
                "source_ref": _bounded_text(
                    str(value.get("source_ref", value.get("ref", ""))),
                    f"referencia de fuente {index}",
                    500,
                ),
                "source_observed_at": _optional_date(
                    value.get("source_observed_at", value.get("observed_at")),
                    f"fecha de observación de fuente {index}",
                ),
                "revalidate_after": _optional_date(
                    value.get("revalidate_after"),
                    f"fecha de revalidación de fuente {index}",
                ),
                "evidence_text": evidence,
                "evidence_sha256": hashlib.sha256(evidence.encode()).hexdigest(),
                "source_unit_ids": unit_ids,
            }
        )
    return tuple(prepared)


def _render_evidence_package(sources: tuple[dict[str, Any], ...]) -> str:
    if len(sources) == 1:
        return str(sources[0]["evidence_text"])
    blocks = []
    for source in sources:
        header = (
            f"[FUENTE {source['index']}: {source['source_title']} | "
            f"SHA256={source['evidence_sha256']}]"
        )
        blocks.append(f"{header}\n{source['evidence_text']}")
    return "\n\n".join(blocks)


def _sanitized_metadata_mismatches(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for key in ("kind", "subject", "locale"):
        item = value.get(key)
        if not isinstance(item, dict):
            continue
        result[key] = {
            "model": _bounded_text(str(item.get("model", "")), "metadato", 160),
            "approved": _bounded_text(
                str(item.get("approved", "")), "metadato aprobado", 160
            ),
        }
    return result


def _candidate(
    value: dict[str, Any],
    *,
    approved_kind: str | None = None,
    approved_subject: str | None = None,
    approved_locale: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("La síntesis de conocimiento debe ser un objeto JSON.")
    claims = _string_list(value.get("claims"), "afirmaciones", 12, 500)
    if not claims:
        raise ValueError("La propuesta requiere al menos una afirmación verificable.")
    model_kind = str(value.get("kind", "")).strip()
    model_subject = str(value.get("subject", "")).strip()
    model_locale = str(value.get("locale", "")).strip()
    kind = _kind(approved_kind if approved_kind is not None else model_kind)
    subject = _required_text(
        approved_subject if approved_subject is not None else model_subject,
        "tema",
        160,
    )
    locale = _required_text(
        approved_locale if approved_locale is not None else model_locale,
        "locale",
        24,
    )
    mismatches = _sanitized_metadata_mismatches(
        value.get("model_metadata_mismatches")
    )
    comparisons = (
        ("kind", model_kind, kind),
        ("subject", model_subject, subject),
        ("locale", model_locale, locale),
    )
    if approved_kind is not None or approved_subject is not None or approved_locale is not None:
        mismatches = {
            **mismatches,
            **{
                key: {"model": model_value, "approved": approved_value}
                for key, model_value, approved_value in comparisons
                if _normalize(model_value) != _normalize(approved_value)
            },
        }
    title_input = str(value.get("title", "")).strip()
    return {
        "kind": kind,
        "subject": subject,
        "title": _required_text(title_input or subject, "título", 160),
        "content": _required_text(
            str(value.get("content", "")), "contenido", _MAX_CANDIDATE_CHARS
        ),
        "claims": claims,
        "keywords": _string_list(value.get("keywords"), "palabras clave", 12, 80),
        "limitations": _string_list(value.get("limitations"), "límites", 8, 300),
        "locale": locale,
        "model_metadata_mismatches": mismatches,
        **_normalized_confidence_fields(value),
    }


def _normalized_confidence_fields(value: dict[str, Any]) -> dict[str, Any]:
    raw = value.get("confidence", 0.0)
    score, mapping = _confidence_score(raw)
    original = value.get("confidence_input", raw)
    original_text = _bounded_text(str(original), "confianza original", 80)
    preserved_mapping = str(value.get("confidence_mapping", "")).strip()
    return {
        "confidence": score,
        "confidence_input": original_text,
        "confidence_mapping": preserved_mapping or mapping,
    }


def normalize_confidence_value(value: Any) -> tuple[float, str]:
    """Normalize numeric, percentage, or controlled qualitative confidence."""
    return _confidence_score(value)


def _confidence_score(value: Any) -> tuple[float, str]:
    if isinstance(value, bool):
        raise ValueError("Confidence no acepta valores booleanos.")
    if isinstance(value, (int, float)):
        return _score(float(value), "confidence"), "numeric"
    clean = str(value).strip()
    if not clean:
        raise ValueError("Confidence no puede estar vacío.")
    if clean.endswith("%"):
        try:
            percent = float(clean[:-1].strip().replace(",", "."))
        except ValueError as exc:
            raise ValueError("Porcentaje de confidence inválido.") from exc
        return _score(percent / 100.0, "confidence"), "percentage"
    numeric = clean.replace(",", ".")
    try:
        return _score(float(numeric), "confidence"), "numeric_string"
    except ValueError:
        pass
    normalized = _normalize(clean)
    for token in ("confianza", "confidence"):
        normalized = " ".join(
            part for part in normalized.split() if part != token
        )
    if normalized in _CONFIDENCE_LABELS:
        return _CONFIDENCE_LABELS[normalized], f"qualitative:{normalized}"
    accepted = ", ".join(sorted(_CONFIDENCE_LABELS))
    raise ValueError(
        "Confidence debe ser un número entre 0 y 1, un porcentaje o una "
        f"etiqueta cualitativa controlada: {accepted}."
    )


def _optional_date(value: str | None, label: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    clean = str(value).strip()
    try:
        parsed = (
            datetime.fromisoformat(clean.replace("Z", "+00:00")).date()
            if "T" in clean or " " in clean
            else date.fromisoformat(clean)
        )
    except ValueError as exc:
        raise ValueError(f"{label.capitalize()} debe usar formato ISO YYYY-MM-DD.") from exc
    return parsed.isoformat()


def _is_revalidation_due(value: Any) -> bool:
    if value is None or not str(value).strip():
        return False
    try:
        return date.fromisoformat(str(value)[:10]) <= datetime.now(UTC).date()
    except ValueError:
        return True


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _plan_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["source_unit_ids"] = json.loads(str(item.get("source_unit_ids_json") or "[]"))
    item["evidence_sources"] = json.loads(
        str(item.get("evidence_sources_json") or "[]")
    )
    if not item["evidence_sources"] and item.get("evidence_text"):
        item["evidence_sources"] = [
            {
                "index": 1,
                "source_type": item.get("source_type", "reviewed_text"),
                "source_title": item.get("source_title", "Fuente migrada"),
                "source_ref": item.get("source_ref", ""),
                "source_observed_at": item.get("source_observed_at"),
                "revalidate_after": item.get("revalidate_after"),
                "evidence_text": item["evidence_text"],
                "evidence_sha256": item.get("evidence_sha256", ""),
                "source_unit_ids": item["source_unit_ids"],
            }
        ]
    item["auditor_ids"] = json.loads(str(item.get("auditor_ids_json") or "[]"))
    if not item["auditor_ids"] and item.get("auditor_id"):
        item["auditor_ids"] = [str(item["auditor_id"])]
    item["auditor_fingerprints"] = json.loads(
        str(item.get("auditor_fingerprints_json") or "{}")
    )
    if (
        item.get("auditor_id")
        and item.get("auditor_fingerprint")
        and str(item["auditor_id"]) not in item["auditor_fingerprints"]
    ):
        item["auditor_fingerprints"][str(item["auditor_id"])] = str(
            item["auditor_fingerprint"]
        )
    item["audit_reviews"] = json.loads(
        str(item.get("audit_reviews_json") or "[]")
    )
    item["candidate"] = json.loads(str(item.get("candidate_json") or "{}"))
    item["deterministic_audit"] = json.loads(
        str(item.get("deterministic_audit_json") or "{}")
    )
    item["related_knowledge_ids"] = json.loads(
        str(item.get("related_knowledge_ids_json") or "[]")
    )
    item.pop("source_unit_ids_json", None)
    item.pop("evidence_sources_json", None)
    item.pop("auditor_ids_json", None)
    item.pop("auditor_fingerprints_json", None)
    item.pop("audit_reviews_json", None)
    item.pop("candidate_json", None)
    item.pop("deterministic_audit_json", None)
    item.pop("related_knowledge_ids_json", None)
    return item


def _knowledge_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["claims"] = json.loads(str(item.get("claims_json") or "[]"))
    item["keywords"] = json.loads(str(item.get("keywords_json") or "[]"))
    item["limitations"] = json.loads(str(item.get("limitations_json") or "[]"))
    item["provenance"] = json.loads(str(item.get("provenance_json") or "{}"))
    item["revalidation_due"] = _is_revalidation_due(item.get("revalidate_after"))
    item["effective_validation_status"] = (
        "needs_revalidation"
        if item["revalidation_due"]
        else str(item.get("validation_status") or "validated")
    )
    for key in ("claims_json", "keywords_json", "limitations_json", "provenance_json"):
        item.pop(key, None)
    return item


def _kind(value: str) -> str:
    clean = value.strip().casefold()
    if clean not in KNOWLEDGE_KINDS:
        raise ValueError("Tipo de conocimiento inválido.")
    return clean


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
    if len(clean) != 64 or any(item not in "0123456789abcdef" for item in clean):
        raise ValueError(f"SHA-256 de {label} inválido.")
    return clean


def _optional_sha256(value: str) -> str:
    return _sha256(value, "salida del auditor") if value else ""


def _score(value: float, label: str) -> float:
    score = float(value)
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"{label} debe estar entre 0 y 1.")
    return round(score, 4)


def _optional_score(value: float | None) -> float | None:
    return _score(value, "confianza del auditor") if value is not None else None


def _string_list(value: Any, label: str, max_items: int, max_chars: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > max_items:
        raise ValueError(f"Lista de {label} inválida.")
    return [_required_text(str(item), label, max_chars) for item in value]


def _normalize(value: str) -> str:
    lowered = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(item for item in lowered if not unicodedata.combining(item)).split()
    )


def _tokens(value: str) -> set[str]:
    return {
        item
        for item in re.findall(r"[a-z0-9_áéíóúüñ]+", _normalize(value))
        if len(item) >= 2 and item not in _RETRIEVAL_STOPWORDS
    }


def _keywords(value: str) -> list[str]:
    return sorted(_tokens(value))[:12]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
