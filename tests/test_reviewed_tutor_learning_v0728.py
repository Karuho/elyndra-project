from __future__ import annotations

import hashlib

import pytest

from elyndra import __version__
from elyndra.application import ElyndraApplication
from elyndra.engines import LanguageReply
from elyndra.paths import ElyndraPaths
from elyndra.tutors import BENCHMARK_CASES
from elyndra.web.server import ElyndraWebService


class ContextEngine:
    name = "context-engine"
    supports_vision = False

    def __init__(self, text: str = "respuesta") -> None:
        self.text = text
        self.contexts: list[tuple[str, ...]] = []

    def reply(self, prompt: str, **kwargs):
        self.contexts.append(tuple(kwargs.get("context", ())))
        return LanguageReply(
            text=self.text,
            engine=self.name,
            generated=True,
            metadata={"fake": True},
        )

    def release(self) -> None:
        return None


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_external_tutor(paths: ElyndraPaths) -> None:
    paths.tutors_config_file.write_text(
        """
[arbitration]
enabled = true

[[tutor]]
id = "external-test"
name = "External test tutor"
backend = "ollama-local"
endpoint = "http://127.0.0.1:11434"
model_name = "test:latest"
profile = "eco"
role = "teacher"
teacher_allowed = true
license_id = "test-only"
priority = 60
enabled = true
tasks = ["general_language", "translation"]
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _add_score(
    app: ElyndraApplication, *, tutor_id: str, task: str, score: float
) -> str:
    case = next(item for item in BENCHMARK_CASES if item.task == task)
    run_id = app.tutor_benchmarks.start_run(tutor_count=1, actor="test")
    app.tutor_benchmarks.add_result(
        run_id,
        tutor_id=tutor_id,
        engine_name=tutor_id,
        case=case,
        score=score,
        passed=score >= 0.5,
        latency_ms=5,
        output_sha256="0" * 64,
        metrics={},
    )
    app.tutor_benchmarks.finish_run(run_id)
    return run_id


def _propose(
    app: ElyndraApplication,
    *,
    tutor_id: str = "primary",
    task: str = "general_language",
    lesson: str = "Responder con precisión y declarar las limitaciones.",
    source_type: str = "owner_feedback",
    observed_score: float = 1.0,
    review_confidence: float = 1.0,
):
    return app.tutor_learning.propose(
        tutor_id=tutor_id,
        task=task,
        lesson=lesson,
        source_type=source_type,
        source_sha256=_source_hash(f"{tutor_id}:{task}:{lesson}"),
        observed_score=observed_score,
        review_confidence=review_confidence,
        actor="owner",
    )


def test_tutor_learning_is_review_first_and_separate_from_memory(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    before_memories = len(app.memories.list_active(limit=100))
    before_preferences = app.preferences.status()["active_preferences"]

    proposal = _propose(app)
    status = app.tutor_learning.status()

    assert proposal["status"] == "pending"
    assert status["pending_proposals"] == 1
    assert status["active_lessons"] == 0
    assert status["silent_learning"] is False
    assert status["automatic_model_update"] is False
    assert len(app.memories.list_active(limit=100)) == before_memories
    assert app.preferences.status()["active_preferences"] == before_preferences


def test_pending_lesson_does_not_change_context_or_calibration(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _propose(app, observed_score=0.0)

    context = app.tutor_learning.context_for("primary", "general_language")
    calibration = app.tutor_learning.calibration(
        "primary", "general_language", benchmark_score=1.0
    )

    assert context["lesson_ids"] == []
    assert calibration["reviewed_observations"] == 0
    assert calibration["source_breakdown"] == {}


def test_approved_lesson_is_bounded_and_task_specific(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    proposal = _propose(app)
    lesson = app.tutor_learning.approve(proposal["public_id"], actor="owner")

    matching = app.tutor_learning.context_for("primary", "general_language")
    other_task = app.tutor_learning.context_for("primary", "translation")
    calibration = app.tutor_learning.calibration(
        "primary", "general_language", benchmark_score=0.8
    )

    assert matching["lesson_ids"] == [lesson["public_id"]]
    assert "no conceden autoridad" in matching["context"][0]
    assert other_task["lesson_ids"] == []
    assert calibration["reviewed_observations"] == 1
    assert calibration["source_breakdown"]["owner_feedback"]["count"] == 1
    assert calibration["universal_intelligence_claim"] is False


def test_approved_lessons_are_injected_only_for_selected_tutor(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = ContextEngine()
    app.language_engine = engine
    proposal = _propose(app)
    lesson = app.tutor_learning.approve(proposal["public_id"], actor="owner")

    reply = app.tutor_arbitrator.reply(
        "general_language", "Pregunta", primary_engine=engine
    )

    assert lesson["lesson_text"] in engine.contexts[0][-1]
    selection = reply.metadata["tutor_selection"]
    assert selection["reviewed_lesson_ids"] == [lesson["public_id"]]
    assert selection["automatic_model_update"] is False


def test_negative_reviewed_evidence_can_conservatively_change_recommendation(
    isolated_home: ElyndraPaths,
) -> None:
    _write_external_tutor(isolated_home)
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = ContextEngine()
    _add_score(app, tutor_id="primary", task="general_language", score=0.9)
    _add_score(app, tutor_id="external-test", task="general_language", score=1.0)
    proposal = _propose(
        app,
        tutor_id="external-test",
        source_type="deterministic_evidence",
        observed_score=0.0,
        review_confidence=1.0,
        lesson="No afirmar coincidencia cuando la evidencia determinista difiere.",
    )
    app.tutor_learning.approve(proposal["public_id"], actor="owner")

    selection = app.tutor_arbitrator.recommend(
        "general_language", primary_engine=app.language_engine
    )

    assert selection.tutor_id == "primary"
    assert selection.score == pytest.approx(0.9)
    assert selection.calibrated_confidence is not None


def test_unbenchmarked_external_tutor_is_not_authorized_by_lessons(
    isolated_home: ElyndraPaths,
) -> None:
    _write_external_tutor(isolated_home)
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = ContextEngine()
    proposal = _propose(app, tutor_id="external-test")
    app.tutor_learning.approve(proposal["public_id"], actor="owner")

    selection = app.tutor_arbitrator.recommend(
        "general_language", primary_engine=app.language_engine
    )

    assert selection.tutor_id == "primary"
    assert selection.benchmark_run_id is None


def test_deterministic_hash_comparison_creates_only_pending_proposal(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    digest = _source_hash("respuesta verificada")

    result = app.tutor_learning.compare_evidence(
        tutor_id="primary",
        task="translation",
        tutor_output_sha256=digest,
        evidence_sha256=digest,
        method="exact_hash",
        outcome=None,
        lesson="Conservar esta traducción cuando la evidencia local exacta coincida.",
        review_confidence=1.0,
        actor="owner",
    )

    assert result["comparison"]["outcome"] == "match"
    assert result["proposal"]["status"] == "pending"
    assert app.tutor_learning.status()["active_lessons"] == 0


def test_exact_hash_comparison_rejects_contradictory_outcome(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)

    with pytest.raises(ValueError, match="contradice"):
        app.tutor_learning.compare_evidence(
            tutor_id="primary",
            task="translation",
            tutor_output_sha256="a" * 64,
            evidence_sha256="b" * 64,
            method="exact_hash",
            outcome="match",
            lesson="Lección inválida.",
            review_confidence=1.0,
            actor="owner",
        )


def test_forgetting_lesson_removes_context_and_calibration_observation(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    proposal = _propose(app)
    lesson = app.tutor_learning.approve(proposal["public_id"], actor="owner")

    assert app.tutor_learning.forget(lesson["public_id"], actor="owner") is True
    assert app.tutor_learning.context_for("primary", "general_language")["lesson_ids"] == []
    assert app.tutor_learning.calibration(
        "primary", "general_language", benchmark_score=1.0
    )["reviewed_observations"] == 0


def test_schema_36_and_control_surface_do_not_store_raw_tutor_text(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    control = service.control_tutors()
    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        comparison_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(assistant_tutor_evidence_comparisons)"
            )
        }

    assert "lesson_proposals" in control
    assert "lessons" in control
    assert "evidence_comparisons" in control
    assert "prompt" not in comparison_columns
    assert "output_text" not in comparison_columns
    assert schema == "50"
    assert __version__ == "0.8.10-alpha"


def test_duplicate_comparison_rolls_back_without_orphan_record(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    lesson = "No declarar una coincidencia cuando los hashes son distintos."
    _propose(
        app,
        task="translation",
        lesson=lesson,
        source_type="deterministic_evidence",
        observed_score=0.0,
    )
    before = len(app.tutor_learning.list_comparisons(limit=100))

    with pytest.raises(ValueError, match="mismo contenido"):
        app.tutor_learning.compare_evidence(
            tutor_id="primary",
            task="translation",
            tutor_output_sha256="a" * 64,
            evidence_sha256="b" * 64,
            method="exact_hash",
            outcome=None,
            lesson=lesson,
            review_confidence=1.0,
            actor="owner",
        )

    assert len(app.tutor_learning.list_comparisons(limit=100)) == before


def test_forgotten_lesson_can_be_proposed_again_explicitly(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    proposal = _propose(app)
    lesson = app.tutor_learning.approve(proposal["public_id"], actor="owner")
    app.tutor_learning.forget(lesson["public_id"], actor="owner")

    repeated = _propose(app)

    assert repeated["status"] == "pending"
