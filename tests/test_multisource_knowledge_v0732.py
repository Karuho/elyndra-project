from __future__ import annotations

import json

import pytest

from elyndra.application import ElyndraApplication
from elyndra.cli import build_parser
from elyndra.engines import LanguageReply
from elyndra.paths import ElyndraPaths


class BlankMetadataEngine:
    name = "blank-metadata-engine"
    supports_vision = False

    def __init__(self) -> None:
        self.calls = 0

    def reply(self, prompt: str, **kwargs):
        self.calls += 1
        return LanguageReply(
            text=json.dumps(
                {
                    "kind": "",
                    "subject": "",
                    "title": "",
                    "content": (
                        "La fotosíntesis convierte energía luminosa en energía química "
                        "y utiliza dióxido de carbono y agua."
                    ),
                    "claims": [
                        "La fotosíntesis convierte energía luminosa en energía química.",
                        "La fotosíntesis utiliza dióxido de carbono y agua.",
                    ],
                    "keywords": ["fotosíntesis", "energía", "agua"],
                    "limitations": ["Síntesis limitada a las fuentes revisadas."],
                    "locale": "",
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


class AuditEngine:
    supports_vision = False

    def __init__(self, name: str, verdict: str, confidence: float) -> None:
        self.name = name
        self.verdict = verdict
        self.confidence = confidence
        self.calls = 0

    def reply(self, prompt: str, **kwargs):
        self.calls += 1
        return LanguageReply(
            text=json.dumps(
                {"verdict": self.verdict, "confidence": self.confidence}
            ),
            engine=self.name,
            generated=True,
            metadata={"fake": True},
        )

    def release(self) -> None:
        return None


class FailingEngine(BlankMetadataEngine):
    name = "failing-engine"

    def reply(self, prompt: str, **kwargs):
        self.calls += 1
        raise RuntimeError("fallo controlado")


def _write_auditors(paths: ElyndraPaths) -> None:
    paths.tutors_config_file.write_text(
        """
[arbitration]
enabled = true

[[tutor]]
id = "audit-one"
name = "Audit one"
backend = "ollama-local"
endpoint = "http://127.0.0.1:11434"
model_name = "audit:one"
profile = "eco"
role = "auditor"
teacher_allowed = false
auditor_allowed = true
license_id = "test-only"
priority = 40
enabled = true
tasks = ["general_language"]

[[tutor]]
id = "audit-two"
name = "Audit two"
backend = "ollama-local"
endpoint = "http://127.0.0.1:11434"
model_name = "audit:two"
profile = "eco"
role = "auditor"
teacher_allowed = false
auditor_allowed = true
license_id = "test-only"
priority = 39
enabled = true
tasks = ["general_language"]
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _plan(
    app: ElyndraApplication,
    engine: BlankMetadataEngine,
    **kwargs,
) -> dict:
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
        source_title="Fuente revisada",
        source_ref="test",
        evidence_text=evidence,
        source_unit_ids=(),
        tutor_id="primary",
        primary_engine=engine,
        actor="owner",
        **kwargs,
    )


def test_approved_metadata_survives_blank_model_fields(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = BlankMetadataEngine()
    plan = _plan(app, engine)

    reviewed = app.tutor_arbitrator.run_knowledge_acquisition(
        plan["public_id"], primary_engine=engine
    )

    assert reviewed["status"] == "reviewed"
    assert reviewed["candidate"]["kind"] == "conceptual"
    assert reviewed["candidate"]["subject"] == "fotosíntesis"
    assert reviewed["candidate"]["locale"] == "es"
    assert reviewed["candidate"]["title"] == "fotosíntesis"
    assert reviewed["candidate"]["confidence"] == 0.8
    assert set(reviewed["candidate"]["model_metadata_mismatches"]) == {
        "kind",
        "subject",
        "locale",
    }
    knowledge = app.general_knowledge.promote(reviewed["public_id"], actor="owner")
    assert knowledge["subject"] == "fotosíntesis"
    assert knowledge["provenance"]["model_metadata_mismatches"]["subject"] == {
        "model": "",
        "approved": "fotosíntesis",
    }


def test_multisource_package_preserves_independent_hashes(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = BlankMetadataEngine()
    sources = (
        {
            "type": "reviewed_text",
            "title": "Fuente A",
            "ref": "a",
            "text": "La fotosíntesis convierte energía luminosa en energía química.",
        },
        {
            "type": "reviewed_text",
            "title": "Fuente B",
            "ref": "b",
            "text": "La fotosíntesis utiliza dióxido de carbono y agua.",
        },
    )
    plan = _plan(app, engine, evidence_sources=sources)

    assert len(plan["evidence_sources"]) == 2
    assert plan["evidence_sources"][0]["evidence_sha256"] != plan[
        "evidence_sources"
    ][1]["evidence_sha256"]
    assert "[FUENTE 1:" in plan["evidence_text"]
    reviewed = app.tutor_arbitrator.run_knowledge_acquisition(
        plan["public_id"], primary_engine=engine
    )
    knowledge = app.general_knowledge.promote(reviewed["public_id"], actor="owner")
    assert len(knowledge["provenance"]["evidence_sources"]) == 2


def test_cross_auditors_are_aggregated_conservatively(
    isolated_home: ElyndraPaths,
) -> None:
    _write_auditors(isolated_home)
    app = ElyndraApplication.load(isolated_home)
    engine = BlankMetadataEngine()
    first = AuditEngine("audit-one", "support", 0.9)
    second = AuditEngine("audit-two", "review", 0.6)
    app.tutor_arbitrator._engine_cache["audit-one"] = first
    app.tutor_arbitrator._engine_cache["audit-two"] = second
    plan = _plan(app, engine, auditor_ids=("audit-one", "audit-two"))

    reviewed = app.tutor_arbitrator.run_knowledge_acquisition(
        plan["public_id"], primary_engine=engine
    )

    assert reviewed["auditor_status"] == "returned"
    assert reviewed["auditor_verdict"] == "review"
    assert reviewed["auditor_confidence"] == 0.6
    assert [item["auditor_id"] for item in reviewed["audit_reviews"]] == [
        "audit-one",
        "audit-two",
    ]
    assert first.calls == second.calls == 1


def test_project_scoped_knowledge_outranks_global_without_leaking(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    global_plan = app.general_knowledge.create_owner_proposal(
        statement="El despliegue general usa el procedimiento global.",
        subject="procedimiento de despliegue",
        kind="procedural",
        locale="es",
        actor="owner",
        domain="devops",
    )
    app.general_knowledge.promote(global_plan["public_id"], actor="owner")
    project_plan = app.general_knowledge.create_owner_proposal(
        statement="Elyndra usa el procedimiento específico del proyecto.",
        subject="procedimiento de despliegue",
        kind="procedural",
        locale="es",
        actor="owner",
        domain="devops",
        project="elyndra",
    )
    project_knowledge = app.general_knowledge.promote(
        project_plan["public_id"], actor="owner"
    )

    global_results = app.general_knowledge.search(
        "procedimiento despliegue", domain="devops"
    )
    project_results = app.general_knowledge.search(
        "procedimiento despliegue", domain="devops", project="elyndra"
    )

    assert all(item["project"] == "" for item in global_results)
    assert project_results[0]["public_id"] == project_knowledge["public_id"]
    assert project_results[0]["project"] == "elyndra"


def test_schema_40_adds_multisource_and_scope_columns(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        plan_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(assistant_knowledge_acquisition_plans)"
            )
        }
        knowledge_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(assistant_general_knowledge)"
            )
        }
    assert schema == "50"
    assert {
        "evidence_sources_json",
        "domain",
        "project",
        "auditor_ids_json",
        "auditor_fingerprints_json",
        "audit_reviews_json",
    } <= plan_columns
    assert {"domain", "project"} <= knowledge_columns


def test_failed_plan_errors_explain_retry_and_reject_promotion(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = FailingEngine()
    plan = _plan(app, engine)
    with pytest.raises(RuntimeError, match="fallo controlado"):
        app.tutor_arbitrator.run_knowledge_acquisition(
            plan["public_id"], primary_engine=engine
        )
    with pytest.raises(ValueError, match="knowledge-acquisition-retry"):
        app.tutor_arbitrator.run_knowledge_acquisition(
            plan["public_id"], primary_engine=engine
        )
    with pytest.raises(ValueError, match="knowledge-acquisition-retry"):
        app.general_knowledge.promote(plan["public_id"], actor="owner")


def test_cli_accepts_evidence_package_and_repeated_auditors() -> None:
    args = build_parser().parse_args(
        [
            "model",
            "knowledge-acquisition-plan",
            "--kind",
            "conceptual",
            "--subject",
            "fotosíntesis",
            "--question",
            "¿Qué es la fotosíntesis?",
            "--evidence-package",
            "fuentes.json",
            "--auditor",
            "audit-one",
            "--auditor",
            "audit-two",
            "--approve",
        ]
    )
    assert args.evidence_package == "fuentes.json"
    assert args.auditor == ["audit-one", "audit-two"]
