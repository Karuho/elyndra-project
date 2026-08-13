from __future__ import annotations

import hashlib

import pytest

from elyndra import __version__
from elyndra.application import ElyndraApplication
from elyndra.engines import LanguageReply
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService


class LessonAwareEngine:
    supports_vision = False

    def __init__(self, marker: str, *, name: str = "lesson-aware") -> None:
        self.marker = marker
        self.name = name
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def reply(self, prompt: str, **kwargs):
        context = tuple(kwargs.get("context", ()))
        self.calls.append((prompt, context))
        joined = "\n".join(context)
        text = ("dog" if self.marker in joined else "cat") if "'perro'" in prompt else "respuesta"
        return LanguageReply(
            text=text,
            engine=self.name,
            generated=True,
            metadata={"fake": True},
        )

    def release(self) -> None:
        return None


class FailingEvaluationEngine(LessonAwareEngine):
    def reply(self, prompt: str, **kwargs):
        if len(self.calls) == 1:
            raise RuntimeError("fallo controlado del motor")
        return super().reply(prompt, **kwargs)


class AuditorEngine:
    name = "auditor-engine"
    supports_vision = False

    def __init__(self, verdict: str = "support") -> None:
        self.verdict = verdict
        self.calls = 0

    def reply(self, prompt: str, **kwargs):
        self.calls += 1
        return LanguageReply(
            text=(
                '{"verdict":"' + self.verdict + '","confidence":0.9}'
            ),
            engine=self.name,
            generated=True,
            metadata={"fake": True},
        )

    def release(self) -> None:
        return None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _approved_lesson(
    app: ElyndraApplication,
    *,
    text: str,
    task: str = "translation",
) -> dict:
    proposal = app.tutor_learning.propose(
        tutor_id="primary",
        task=task,
        lesson=text,
        source_type="reviewed_evidence",
        source_sha256=_hash(text),
        observed_score=1.0,
        review_confidence=1.0,
        actor="owner",
    )
    return app.tutor_learning.approve(proposal["public_id"], actor="owner")


def _write_auditor(paths: ElyndraPaths) -> None:
    paths.tutors_config_file.write_text(
        """
[arbitration]
enabled = true

[[tutor]]
id = "local-auditor"
name = "Local auditor"
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
tasks = ["translation"]
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _plan_and_run(
    app: ElyndraApplication,
    lesson_id: str,
    *,
    auditor_id: str | None = None,
) -> dict:
    plan = app.tutor_arbitrator.plan_lesson_evaluation(
        lesson_id,
        primary_engine=app.language_engine,
        actor="owner",
        auditor_id=auditor_id,
    )
    return app.tutor_arbitrator.run_lesson_evaluation(
        plan["public_id"],
        primary_engine=app.language_engine,
    )


def test_schema_37_and_evolution_status_are_non_destructive(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        knowledge_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(assistant_tutor_knowledge)"
            )
        }

    control = service.control_tutors()
    status = app.tutor_evolution.status()
    assert schema == "50"
    assert __version__ == "0.8.9-alpha"
    assert status["knowledge_deletion_allowed"] is False
    assert status["automatic_promotion"] is False
    assert "lesson_evaluations" in control
    assert "durable_knowledge" in control
    assert "deleted_at" not in knowledge_columns


def test_evaluation_plan_does_not_invoke_model_and_is_single_use(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = LessonAwareEngine("GUIDE")
    app.language_engine = engine
    lesson = _approved_lesson(app, text="GUIDE")

    plan = app.tutor_arbitrator.plan_lesson_evaluation(
        lesson["public_id"],
        primary_engine=engine,
        actor="owner",
    )

    assert plan["status"] == "pending"
    assert engine.calls == []
    result = app.tutor_arbitrator.run_lesson_evaluation(
        plan["public_id"],
        primary_engine=engine,
    )
    assert result["status"] == "completed"
    with pytest.raises(ValueError, match="consumida"):
        app.tutor_arbitrator.run_lesson_evaluation(
            plan["public_id"],
            primary_engine=engine,
        )


def test_evaluation_compares_baseline_candidate_and_stores_only_hashes(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = LessonAwareEngine("EXACT_DOG")
    lesson = _approved_lesson(app, text="EXACT_DOG")

    result = _plan_and_run(app, lesson["public_id"])

    assert result["baseline_score"] == pytest.approx(0.0)
    assert result["candidate_score"] == pytest.approx(1.0)
    assert result["recommendation"] == "promote_knowledge"
    assert result["results"][0]["baseline_output_sha256"] == _hash("cat")
    assert result["results"][0]["candidate_output_sha256"] == _hash("dog")
    with app.database.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(assistant_tutor_lesson_evaluation_results)"
            )
        }
    assert "prompt" not in columns
    assert "baseline_output" not in columns
    assert "candidate_output" not in columns


def test_validated_evaluation_promotes_durable_knowledge_for_elyndra(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = LessonAwareEngine("EXACT_DOG")
    app.language_engine = engine
    lesson = _approved_lesson(app, text="EXACT_DOG")
    evaluation = _plan_and_run(app, lesson["public_id"])

    knowledge = app.tutor_evolution.promote_knowledge(
        evaluation["public_id"],
        title="Traducción canónica de perro",
        actor="owner",
    )
    context = app.tutor_evolution.knowledge_context("translation")
    reply = app.tutor_arbitrator.reply(
        "translation",
        "Traduce 'perro' al inglés.",
        primary_engine=engine,
    )

    assert knowledge["status"] == "active"
    assert knowledge["version"] == 1
    assert knowledge["public_id"] in context["knowledge_ids"]
    assert "CONOCIMIENTO DURABLE VALIDADO" in context["context"][0]
    selection = reply.metadata["tutor_selection"]
    assert selection["durable_knowledge_ids"] == [knowledge["public_id"]]
    assert selection["automatic_knowledge_promotion"] is False


def test_newer_functional_version_supersedes_without_deleting_history(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = LessonAwareEngine("FIRST_GUIDE", name="stable-engine")
    first_lesson = _approved_lesson(app, text="FIRST_GUIDE")
    first_evaluation = _plan_and_run(app, first_lesson["public_id"])
    first = app.tutor_evolution.promote_knowledge(
        first_evaluation["public_id"],
        title="Guía inicial",
        actor="owner",
    )

    app.language_engine = LessonAwareEngine("SECOND_GUIDE", name="stable-engine")
    second_lesson = _approved_lesson(app, text="SECOND_GUIDE")
    second_evaluation = _plan_and_run(app, second_lesson["public_id"])
    assert second_evaluation["knowledge_ids"] == [first["public_id"]]
    second = app.tutor_evolution.promote_knowledge(
        second_evaluation["public_id"],
        title="Guía superior",
        actor="owner",
        supersedes_public_id=first["public_id"],
    )

    old = app.tutor_evolution.knowledge_details(first["public_id"])
    assert old is not None
    assert old["status"] == "superseded"
    assert old["successor_id"] is not None
    assert second["status"] == "active"
    assert second["version"] == 2
    assert second["lineage_id"] == first["lineage_id"]
    assert len(app.tutor_evolution.list_knowledge(status="all")) == 2


def test_old_evaluation_cannot_replace_newer_knowledge(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = LessonAwareEngine("OLD_CANDIDATE", name="stable-engine")
    old_candidate_lesson = _approved_lesson(app, text="OLD_CANDIDATE")
    old_candidate_evaluation = _plan_and_run(
        app, old_candidate_lesson["public_id"]
    )

    app.language_engine = LessonAwareEngine("CURRENT_GUIDE", name="stable-engine")
    current_lesson = _approved_lesson(app, text="CURRENT_GUIDE")
    current_evaluation = _plan_and_run(app, current_lesson["public_id"])
    current = app.tutor_evolution.promote_knowledge(
        current_evaluation["public_id"],
        title="Conocimiento actual",
        actor="owner",
    )

    with pytest.raises(ValueError, match="debe evaluarse contra"):
        app.tutor_evolution.promote_knowledge(
            old_candidate_evaluation["public_id"],
            title="Candidato antiguo",
            actor="owner",
            supersedes_public_id=current["public_id"],
        )

    preserved = app.tutor_evolution.knowledge_details(current["public_id"])
    assert preserved is not None
    assert preserved["status"] == "active"


def test_auditor_is_advisory_and_never_selected_for_normal_reply(
    isolated_home: ElyndraPaths,
) -> None:
    _write_auditor(isolated_home)
    app = ElyndraApplication.load(isolated_home)
    primary = LessonAwareEngine("AUDITED_GUIDE")
    auditor = AuditorEngine("reject")
    app.language_engine = primary
    app.tutor_arbitrator._engine_cache["local-auditor"] = auditor
    lesson = _approved_lesson(app, text="AUDITED_GUIDE")

    recommendation = app.tutor_arbitrator.recommend(
        "translation",
        primary_engine=primary,
    )
    result = _plan_and_run(
        app,
        lesson["public_id"],
        auditor_id="local-auditor",
    )

    assert recommendation.tutor_id == "primary"
    assert result["auditor_status"] == "returned"
    assert result["auditor_verdict"] == "reject"
    assert result["recommendation"] == "replace_lesson"
    with pytest.raises(ValueError, match="umbral conservador"):
        app.tutor_evolution.promote_knowledge(
            result["public_id"],
            title="No promovible",
            actor="owner",
        )


def test_changed_durable_knowledge_invalidates_frozen_plan_before_model_call(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = LessonAwareEngine("TARGET_GUIDE", name="stable-engine")
    app.language_engine = engine
    target_lesson = _approved_lesson(app, text="TARGET_GUIDE")
    frozen_plan = app.tutor_arbitrator.plan_lesson_evaluation(
        target_lesson["public_id"],
        primary_engine=engine,
        actor="owner",
    )
    assert frozen_plan["knowledge_ids"] == []

    other_lesson = _approved_lesson(app, text="OTHER_GUIDE")
    other_evaluation = _plan_and_run(app, other_lesson["public_id"])
    app.tutor_evolution.promote_knowledge(
        other_evaluation["public_id"],
        title="Nuevo conocimiento activo",
        actor="owner",
    )
    engine.calls.clear()

    with pytest.raises(ValueError, match="conocimiento durable cambió"):
        app.tutor_arbitrator.run_lesson_evaluation(
            frozen_plan["public_id"],
            primary_engine=engine,
        )

    details = app.tutor_evolution.evaluation_details(frozen_plan["public_id"])
    assert details is not None
    assert details["status"] == "failed"
    assert details["results"] == []
    assert engine.calls == []


def test_changed_model_fingerprint_makes_old_evaluation_stale_for_calibration(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    first_engine = LessonAwareEngine("GUIDE", name="engine-a")
    app.language_engine = first_engine
    lesson = _approved_lesson(app, text="GUIDE")
    _plan_and_run(app, lesson["public_id"])

    second_engine = LessonAwareEngine("GUIDE", name="engine-b")
    app.language_engine = second_engine
    calibration = app.tutor_arbitrator.calibration(
        "primary",
        "translation",
        primary_engine=second_engine,
    )

    assert calibration["evaluation_evidence"]["observations"] == 0
    assert calibration["evaluation_evidence"]["stale_observations"] == 1


def test_completed_evaluation_cannot_promote_twice(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = LessonAwareEngine("GUIDE")
    lesson = _approved_lesson(app, text="GUIDE")
    evaluation = _plan_and_run(app, lesson["public_id"])
    app.tutor_evolution.promote_knowledge(
        evaluation["public_id"],
        title="Conocimiento único",
        actor="owner",
    )

    with pytest.raises(ValueError, match="completada"):
        app.tutor_evolution.promote_knowledge(
            evaluation["public_id"],
            title="Reutilización inválida",
            actor="owner",
        )


def test_failed_evaluation_keeps_no_partial_results(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = FailingEvaluationEngine("GUIDE")
    app.language_engine = engine
    lesson = _approved_lesson(app, text="GUIDE")
    plan = app.tutor_arbitrator.plan_lesson_evaluation(
        lesson["public_id"],
        primary_engine=engine,
        actor="owner",
    )

    with pytest.raises(RuntimeError, match="fallo controlado"):
        app.tutor_arbitrator.run_lesson_evaluation(
            plan["public_id"],
            primary_engine=engine,
        )

    details = app.tutor_evolution.evaluation_details(plan["public_id"])
    assert details is not None
    assert details["status"] == "failed"
    assert details["results"] == []


def test_cancelled_evaluation_never_invokes_model(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = LessonAwareEngine("GUIDE")
    app.language_engine = engine
    lesson = _approved_lesson(app, text="GUIDE")
    plan = app.tutor_arbitrator.plan_lesson_evaluation(
        lesson["public_id"],
        primary_engine=engine,
        actor="owner",
    )

    assert app.tutor_evolution.cancel_evaluation(
        plan["public_id"], actor="owner"
    ) is True
    assert engine.calls == []
    with pytest.raises(ValueError, match="consumida"):
        app.tutor_arbitrator.run_lesson_evaluation(
            plan["public_id"],
            primary_engine=engine,
        )


def test_migration_36_to_37_preserves_existing_tutor_data(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    lesson = _approved_lesson(app, text="PRESERVED_GUIDE")
    with app.database.connect() as connection:
        connection.execute("DROP TABLE assistant_tutor_knowledge")
        connection.execute("DROP TABLE assistant_tutor_lesson_evaluation_results")
        connection.execute("DROP TABLE assistant_tutor_lesson_evaluations")
        connection.execute(
            "UPDATE schema_meta SET value = '36' WHERE key = 'schema_version'"
        )

    app.database.migrate()

    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        preserved = connection.execute(
            "SELECT public_id FROM assistant_tutor_lessons WHERE public_id = ?",
            (lesson["public_id"],),
        ).fetchone()
        new_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'assistant_tutor_knowledge'"
        ).fetchone()
    assert schema == "50"
    assert preserved is not None
    assert new_table is not None
