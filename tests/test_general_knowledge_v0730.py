from __future__ import annotations

import json

import pytest

from elyndra import __version__
from elyndra.application import ElyndraApplication
from elyndra.engines import LanguageReply
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService


class KnowledgeEngine:
    name = "knowledge-engine"
    supports_vision = False

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def reply(self, prompt: str, **kwargs):
        context = tuple(kwargs.get("context", ()))
        self.calls.append((prompt, context))
        if prompt.startswith("Sintetiza conocimiento durable"):
            text = json.dumps(
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
                    "keywords": ["fotosíntesis", "energía", "dióxido de carbono"],
                    "limitations": [
                        "Resumen conceptual acotado a la evidencia proporcionada."
                    ],
                    "locale": "es",
                    "confidence": 0.9,
                },
                ensure_ascii=False,
            )
        else:
            text = "respuesta con conocimiento"
        return LanguageReply(
            text=text,
            engine=self.name,
            generated=True,
            metadata={"fake": True},
        )

    def release(self) -> None:
        return None


class FailingKnowledgeEngine(KnowledgeEngine):
    def reply(self, prompt: str, **kwargs):
        self.calls.append((prompt, tuple(kwargs.get("context", ()))))
        raise RuntimeError("fallo controlado de síntesis")


class RejectingAuditorEngine:
    name = "rejecting-auditor"
    supports_vision = False

    def __init__(self) -> None:
        self.calls = 0

    def reply(self, prompt: str, **kwargs):
        self.calls += 1
        return LanguageReply(
            text='{"verdict":"reject","confidence":0.95}',
            engine=self.name,
            generated=True,
            metadata={"fake": True},
        )

    def release(self) -> None:
        return None


def _write_general_auditor(paths: ElyndraPaths) -> None:
    paths.tutors_config_file.write_text(
        """
[arbitration]
enabled = true

[[tutor]]
id = "general-auditor"
name = "General auditor"
backend = "ollama-local"
endpoint = "http://127.0.0.1:11434"
model_name = "audit:test"
profile = "eco"
role = "auditor"
teacher_allowed = false
auditor_allowed = true
license_id = "test-only"
priority = 40
enabled = true
tasks = ["general_language"]
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _promote_owner_statement(
    app: ElyndraApplication,
    *,
    subject: str,
    statement: str,
    supersedes: str | None = None,
) -> dict:
    proposal = app.general_knowledge.create_owner_proposal(
        statement=statement,
        subject=subject,
        kind="factual",
        locale="es",
        actor="owner",
    )
    return app.general_knowledge.promote(
        proposal["public_id"],
        actor="owner",
        supersedes_public_id=supersedes,
        replacement_reason=(
            "Actualización revisada con mayor precisión funcional."
            if supersedes
            else ""
        ),
    )


def test_schema_38_and_general_knowledge_are_non_destructive(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(assistant_general_knowledge)"
            )
        }

    status = app.general_knowledge.status()
    control = service.control_tutors()
    assert schema == "51"
    assert __version__ == "0.8.10-alpha"
    assert status["knowledge_deletion_allowed"] is False
    assert status["silent_learning"] is False
    assert status["automatic_promotion"] is False
    assert "deleted_at" not in columns
    assert "knowledge_acquisition" in control
    assert "general_knowledge" in control


def test_explicit_owner_teaching_creates_reviewable_proposal_only(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)

    result = app.ask("Aprende que la capital de Chile es Santiago.")

    assert result.ok is False
    assert result.data["fast_path"] == "explicit_owner_teaching"
    assert result.data["automatic_promotion"] is False
    proposal = app.general_knowledge.plan_details(result.data["knowledge_plan_id"])
    assert proposal is not None
    assert proposal["status"] == "reviewed"
    assert app.general_knowledge.list_knowledge() == []


def test_validated_general_knowledge_answers_before_ollama(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = KnowledgeEngine()
    app.language_engine = engine
    knowledge = _promote_owner_statement(
        app,
        subject="capital de Chile",
        statement="La capital de Chile es Santiago.",
    )

    result = app.ask("¿Qué es la capital de Chile?")

    assert result.ok is True
    assert result.message == "La capital de Chile es Santiago."
    assert result.data["engine"] == "local-general-knowledge"
    assert result.data["model_used"] is False
    assert result.data["knowledge"]["public_id"] == knowledge["public_id"]
    assert engine.calls == []


def test_tutor_synthesizes_reviewed_evidence_and_never_auto_promotes(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = KnowledgeEngine()
    app.language_engine = engine
    evidence = (
        "La fotosíntesis convierte energía luminosa en energía química. "
        "La fotosíntesis utiliza dióxido de carbono y agua."
    )
    plan = app.tutor_arbitrator.plan_knowledge_acquisition(
        kind="conceptual",
        subject="fotosíntesis",
        question="¿Qué es la fotosíntesis?",
        locale="es",
        source_type="reviewed_text",
        source_title="Fuente local revisada",
        source_ref="test-source",
        evidence_text=evidence,
        source_unit_ids=(),
        tutor_id="primary",
        primary_engine=engine,
        actor="owner",
    )

    assert plan["status"] == "pending"
    assert engine.calls == []
    reviewed = app.tutor_arbitrator.run_knowledge_acquisition(
        plan["public_id"],
        primary_engine=engine,
    )

    assert reviewed["status"] == "reviewed"
    assert reviewed["deterministic_audit"]["verdict"] == "support"
    assert reviewed["promoted_knowledge_id"] is None
    assert app.general_knowledge.list_knowledge() == []

    knowledge = app.general_knowledge.promote(
        reviewed["public_id"], actor="owner"
    )
    assert knowledge["status"] == "active"
    assert knowledge["provenance"]["tutor_id"] == "primary"


def test_general_knowledge_is_shared_with_selected_tutor_context(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = KnowledgeEngine()
    app.language_engine = engine
    knowledge = _promote_owner_statement(
        app,
        subject="regla de respaldo",
        statement="Una copia verificada debe conservar procedencia y fecha.",
    )

    reply = app.tutor_arbitrator.reply(
        "general_language",
        "Explica la regla de respaldo y procedencia.",
        primary_engine=engine,
    )

    selection = reply.metadata["tutor_selection"]
    assert selection["general_knowledge_ids"] == [knowledge["public_id"]]
    assert any(
        "CONOCIMIENTO GENERAL VALIDADO" in block
        for _, context in engine.calls
        for block in context
    )


def test_superior_version_supersedes_without_deleting_history(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    first = _promote_owner_statement(
        app,
        subject="política de respaldo",
        statement="El respaldo se valida con un hash.",
    )
    second = _promote_owner_statement(
        app,
        subject="política de respaldo",
        statement=(
            "El respaldo se valida con un hash y conserva una referencia de procedencia."
        ),
        supersedes=first["public_id"],
    )

    old = app.general_knowledge.knowledge_details(first["public_id"])
    assert old is not None
    assert old["status"] == "superseded"
    assert old["successor_id"] is not None
    assert second["status"] == "active"
    assert second["version"] == 2
    assert second["lineage_id"] == first["lineage_id"]
    assert len(app.general_knowledge.list_knowledge(status="all")) == 2


def test_replacement_requires_same_topic_and_explicit_reason(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    first = _promote_owner_statement(
        app,
        subject="tema estable",
        statement="El conocimiento inicial permanece trazable.",
    )
    proposal = app.general_knowledge.create_owner_proposal(
        statement="Contenido sobre otro tema.",
        subject="otro tema",
        kind="factual",
        locale="es",
        actor="owner",
    )

    with pytest.raises(ValueError, match="Razón de actualización"):
        app.general_knowledge.promote(
            proposal["public_id"],
            actor="owner",
            supersedes_public_id=first["public_id"],
        )
    with pytest.raises(ValueError, match="mismo tema"):
        app.general_knowledge.promote(
            proposal["public_id"],
            actor="owner",
            supersedes_public_id=first["public_id"],
            replacement_reason="Corrección revisada.",
        )


def test_failed_synthesis_leaves_no_partial_knowledge(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = FailingKnowledgeEngine()
    app.language_engine = engine
    plan = app.tutor_arbitrator.plan_knowledge_acquisition(
        kind="factual",
        subject="tema de prueba",
        question="¿Qué debe aprender?",
        locale="es",
        source_type="reviewed_text",
        source_title="Fuente de prueba",
        source_ref="test",
        evidence_text="La evidencia de prueba es explícita y revisada.",
        source_unit_ids=(),
        tutor_id="primary",
        primary_engine=engine,
        actor="owner",
    )

    with pytest.raises(RuntimeError, match="fallo controlado"):
        app.tutor_arbitrator.run_knowledge_acquisition(
            plan["public_id"], primary_engine=engine
        )

    details = app.general_knowledge.plan_details(plan["public_id"])
    assert details is not None
    assert details["status"] == "failed"
    assert details["candidate"] == {}
    assert app.general_knowledge.list_knowledge() == []


def test_migration_37_to_38_preserves_existing_tutor_knowledge(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    with app.database.connect() as connection:
        connection.execute("DROP TABLE assistant_general_knowledge")
        connection.execute("DROP TABLE assistant_knowledge_acquisition_plans")
        connection.execute(
            "UPDATE schema_meta SET value = '37' WHERE key = 'schema_version'"
        )
        connection.execute(
            "INSERT INTO assistant_tutor_lesson_proposals("
            "public_id, tutor_id, task_type, lesson_text, lesson_sha256, "
            "source_type, source_sha256, observed_score, review_confidence, "
            "status, created_by, created_at, updated_at"
            ") VALUES ('preserved', 'primary', 'translation', 'guide', "
            "'hash', 'owner_feedback', 'source', 1.0, 1.0, 'pending', "
            "'owner', 'now', 'now')"
        )

    app.database.migrate()

    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        preserved = connection.execute(
            "SELECT public_id FROM assistant_tutor_lesson_proposals "
            "WHERE public_id = 'preserved'"
        ).fetchone()
        new_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'assistant_general_knowledge'"
        ).fetchone()
    assert schema == "51"
    assert preserved is not None
    assert new_table is not None


def test_model_auditor_can_block_but_never_promote(
    isolated_home: ElyndraPaths,
) -> None:
    _write_general_auditor(isolated_home)
    app = ElyndraApplication.load(isolated_home)
    tutor = KnowledgeEngine()
    auditor = RejectingAuditorEngine()
    app.language_engine = tutor
    app.tutor_arbitrator._engine_cache["general-auditor"] = auditor
    evidence = (
        "La fotosíntesis convierte energía luminosa en energía química. "
        "La fotosíntesis utiliza dióxido de carbono y agua."
    )
    plan = app.tutor_arbitrator.plan_knowledge_acquisition(
        kind="conceptual",
        subject="fotosíntesis",
        question="¿Qué es la fotosíntesis?",
        locale="es",
        source_type="reviewed_text",
        source_title="Fuente revisada",
        source_ref="test",
        evidence_text=evidence,
        source_unit_ids=(),
        tutor_id="primary",
        primary_engine=tutor,
        actor="owner",
        auditor_id="general-auditor",
    )

    reviewed = app.tutor_arbitrator.run_knowledge_acquisition(
        plan["public_id"], primary_engine=tutor
    )

    assert reviewed["auditor_status"] == "returned"
    assert reviewed["auditor_verdict"] == "reject"
    assert auditor.calls == 1
    with pytest.raises(ValueError, match="auditor consultivo rechazó"):
        app.general_knowledge.promote(reviewed["public_id"], actor="owner")
    assert app.general_knowledge.list_knowledge() == []
