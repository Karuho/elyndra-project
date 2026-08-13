from __future__ import annotations

import json

import pytest

from elyndra import __version__
from elyndra.application import ElyndraApplication
from elyndra.engines import LanguageReply
from elyndra.knowledge_acquisition import normalize_confidence_value
from elyndra.paths import ElyndraPaths


class QualitativeConfidenceEngine:
    name = "qualitative-confidence-engine"
    supports_vision = False

    def __init__(self) -> None:
        self.calls = 0

    def reply(self, prompt: str, **kwargs):
        self.calls += 1
        return LanguageReply(
            text=json.dumps(
                {
                    "kind": "conceptual",
                    "subject": "fotosíntesis",
                    "title": "Fotosíntesis",
                    "content": (
                        "La fotosíntesis convierte energía luminosa en energía química "
                        "y utiliza dióxido de carbono y agua."
                    ),
                    "claims": [
                        "La fotosíntesis convierte energía luminosa en energía química.",
                        "La fotosíntesis utiliza dióxido de carbono y agua.",
                    ],
                    "keywords": ["fotosíntesis", "energía", "agua"],
                    "limitations": ["Síntesis limitada a la evidencia revisada."],
                    "locale": "es",
                    "confidence": "alta",
                },
                ensure_ascii=False,
            ),
            engine=self.name,
            generated=True,
            metadata={"fake": True},
        )

    def release(self) -> None:
        return None


class FailingEngine(QualitativeConfidenceEngine):
    name = "failing-confidence-engine"

    def reply(self, prompt: str, **kwargs):
        self.calls += 1
        raise RuntimeError("fallo controlado")


def _plan(app: ElyndraApplication, engine) -> dict:
    evidence = (
        "La fotosíntesis convierte energía luminosa en energía química. "
        "La fotosíntesis utiliza dióxido de carbono y agua."
    )
    return app.tutor_arbitrator.plan_knowledge_acquisition(
        kind="conceptual",
        subject="fotosíntesis",
        question="¿Qué es la fotosíntesis?",
        locale="es",
        source_type="reviewed_text",
        source_title="Fuente local revisada",
        source_ref="test",
        evidence_text=evidence,
        source_unit_ids=(),
        tutor_id="primary",
        primary_engine=engine,
        actor="owner",
    )


def _owner_proposal(
    app: ElyndraApplication,
    *,
    subject: str,
    statement: str,
    revalidate_after: str | None = None,
) -> dict:
    return app.general_knowledge.create_owner_proposal(
        statement=statement,
        subject=subject,
        kind="factual",
        locale="es",
        actor="owner",
        revalidate_after=revalidate_after,
    )


def test_qualitative_confidence_is_normalized_conservatively(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = QualitativeConfidenceEngine()
    app.language_engine = engine
    plan = _plan(app, engine)

    reviewed = app.tutor_arbitrator.run_knowledge_acquisition(
        plan["public_id"], primary_engine=engine
    )

    assert reviewed["status"] == "reviewed"
    assert reviewed["candidate"]["confidence"] == 0.8
    assert reviewed["candidate"]["confidence_input"] == "alta"
    assert reviewed["candidate"]["confidence_mapping"] == "qualitative:alta"
    assert engine.calls == 1


@pytest.mark.parametrize(
    ("raw", "expected", "mapping"),
    [
        ("85%", 0.85, "percentage"),
        ("high", 0.8, "qualitative:high"),
        ("muy alta", 0.9, "qualitative:muy alta"),
        ("0,75", 0.75, "numeric_string"),
    ],
)
def test_confidence_normalization_accepts_controlled_formats(
    raw: str, expected: float, mapping: str
) -> None:
    assert normalize_confidence_value(raw) == (expected, mapping)


def test_unknown_confidence_label_fails_closed() -> None:
    with pytest.raises(ValueError, match="etiqueta cualitativa controlada"):
        normalize_confidence_value("supuestamente suficiente")


def test_failed_plan_retry_creates_new_approval_without_reusing_old_one(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    failing = FailingEngine()
    app.language_engine = failing
    failed = _plan(app, failing)

    with pytest.raises(RuntimeError, match="fallo controlado"):
        app.tutor_arbitrator.run_knowledge_acquisition(
            failed["public_id"], primary_engine=failing
        )

    working = QualitativeConfidenceEngine()
    retried = app.tutor_arbitrator.retry_knowledge_acquisition(
        failed["public_id"],
        primary_engine=working,
        actor="owner",
    )

    assert retried["public_id"] != failed["public_id"]
    assert retried["status"] == "pending"
    assert retried["evidence_sha256"] == failed["evidence_sha256"]
    assert app.general_knowledge.plan_details(failed["public_id"])["status"] == "failed"
    reviewed = app.tutor_arbitrator.run_knowledge_acquisition(
        retried["public_id"], primary_engine=working
    )
    assert reviewed["status"] == "reviewed"


def test_parallel_same_subject_requires_explicit_reason_and_creates_conflict(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    first_plan = _owner_proposal(
        app,
        subject="capital de Chile",
        statement="La capital de Chile es Santiago.",
    )
    first = app.general_knowledge.promote(first_plan["public_id"], actor="owner")
    second_plan = _owner_proposal(
        app,
        subject="capital de Chile",
        statement="Santiago es la capital política y administrativa de Chile.",
    )

    assert second_plan["conflict_status"] == "review_required"
    assert second_plan["related_knowledge_ids"] == [first["public_id"]]
    with pytest.raises(ValueError, match="razón explícita"):
        app.general_knowledge.promote(second_plan["public_id"], actor="owner")

    second = app.general_knowledge.promote(
        second_plan["public_id"],
        actor="owner",
        parallel_reason="Ambas unidades son complementarias y se revisan juntas.",
    )
    conflicts = app.general_knowledge.list_conflicts()
    assert second["status"] == "active"
    assert len(conflicts) == 1
    assert {conflicts[0]["knowledge_a_public_id"], conflicts[0]["knowledge_b_public_id"]} == {
        first["public_id"],
        second["public_id"],
    }
    assert app.general_knowledge.answer_for_query("¿Qué es la capital de Chile?") is None

    resolved = app.general_knowledge.resolve_conflict(
        conflicts[0]["public_id"],
        resolution="compatible",
        note="Las afirmaciones son compatibles y no se reemplazan.",
        actor="owner",
    )
    assert resolved["status"] == "resolved"
    assert len(app.general_knowledge.list_knowledge(status="all")) == 2


def test_revalidation_due_preserves_but_excludes_operational_knowledge(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    proposal = _owner_proposal(
        app,
        subject="dato temporal",
        statement="Este dato fue validado para una fecha anterior.",
        revalidate_after="2000-01-01",
    )
    knowledge = app.general_knowledge.promote(proposal["public_id"], actor="owner")

    assert knowledge["status"] == "active"
    assert knowledge["revalidation_due"] is True
    assert knowledge["effective_validation_status"] == "needs_revalidation"
    due = app.general_knowledge.revalidation_due()
    assert [item["public_id"] for item in due] == [knowledge["public_id"]]
    assert app.general_knowledge.answer_for_query("¿Qué es el dato temporal?") is None
    context = app.general_knowledge.context_for_query("explica el dato temporal")
    assert context["knowledge_ids"] == []
    assert context["revalidation_due_ids"] == [knowledge["public_id"]]
    assert app.general_knowledge.knowledge_details(knowledge["public_id"])["content"]


def test_schema_39_adds_non_destructive_knowledge_governance(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        conflict_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'assistant_knowledge_conflicts'"
        ).fetchone()
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(assistant_general_knowledge)"
            )
        }
    assert schema == "50"
    assert __version__ == "0.8.9-alpha"
    assert conflict_table is not None
    assert {"revalidate_after", "validation_status", "last_revalidated_at"} <= columns
    assert "deleted_at" not in columns


def test_exact_duplicate_active_knowledge_is_blocked(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    first_plan = _owner_proposal(
        app,
        subject="regla estable",
        statement="La regla estable conserva procedencia.",
    )
    app.general_knowledge.promote(first_plan["public_id"], actor="owner")
    duplicate = _owner_proposal(
        app,
        subject="regla estable",
        statement="La regla estable conserva procedencia.",
    )

    assert duplicate["conflict_status"] == "duplicate"
    with pytest.raises(ValueError, match="mismo contenido"):
        app.general_knowledge.promote(duplicate["public_id"], actor="owner")
    assert len(app.general_knowledge.list_knowledge(status="all")) == 1


def test_promotion_rechecks_knowledge_created_after_review(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    earlier_review = _owner_proposal(
        app,
        subject="tema concurrente",
        statement="Primera propuesta revisada antes de existir conocimiento activo.",
    )
    later_plan = _owner_proposal(
        app,
        subject="tema concurrente",
        statement="Conocimiento promovido después de la primera revisión.",
    )
    later = app.general_knowledge.promote(later_plan["public_id"], actor="owner")

    assert earlier_review["related_knowledge_ids"] == []
    with pytest.raises(ValueError, match="razón explícita"):
        app.general_knowledge.promote(earlier_review["public_id"], actor="owner")
    assert app.general_knowledge.knowledge_details(later["public_id"])["status"] == "active"
