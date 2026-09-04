from __future__ import annotations

import pytest

from elyndra import __version__
from elyndra.application import ElyndraApplication
from elyndra.cli import build_parser
from elyndra.engines import LanguageReply
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService


class ExecutiveEngine:
    name = "executive-engine"
    supports_vision = False

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def reply(self, prompt: str, **kwargs):
        context = tuple(kwargs.get("context", ()))
        self.calls.append((prompt, context))
        return LanguageReply(
            text="respuesta ejecutiva",
            engine=self.name,
            generated=True,
            metadata={"fake": True},
        )

    def release(self) -> None:
        return None


def _promote(
    app: ElyndraApplication,
    *,
    subject: str,
    statement: str,
    domain: str = "",
) -> dict:
    proposal = app.general_knowledge.create_owner_proposal(
        statement=statement,
        subject=subject,
        kind="factual",
        locale="es",
        actor="owner",
        domain=domain,
    )
    return app.general_knowledge.promote(proposal["public_id"], actor="owner")


def test_schema_41_and_executive_status(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert schema == "50"
    assert __version__ == "0.8.10-alpha"
    assert {
        "assistant_executive_decisions",
        "assistant_goals",
        "assistant_goal_tasks",
        "assistant_outcome_verifications",
    } <= tables
    status = app.cognitive_executive.status()
    assert status["prompt_text_stored"] is False
    assert status["automatic_execution"] is False
    assert status["multidimensional_confidence"] is True
    assert service.control_executive()["status"]["enabled"] is True


def test_context_filters_unrelated_global_knowledge(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    photosynthesis = _promote(
        app,
        subject="fotosíntesis",
        statement=(
            "La fotosíntesis convierte energía luminosa en energía química."
        ),
        domain="biología",
    )
    capital = _promote(
        app,
        subject="capital de Chile",
        statement="La capital de Chile es Santiago.",
    )

    context = app.general_knowledge.context_for_query(
        "explica la fotosíntesis",
        domain="biología",
    )

    assert context["knowledge_ids"] == [photosynthesis["public_id"]]
    assert capital["public_id"] not in context["knowledge_ids"]
    assert "capital de Chile" not in "\n".join(context["context"])


def test_ask_records_structured_decision_without_prompt(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _promote(
        app,
        subject="capital de Chile",
        statement="La capital de Chile es Santiago.",
    )

    result = app.ask("¿Qué es la capital de Chile?")

    assert result.ok is True
    assert result.data["executive"]["actual_route"] == "validated_general_knowledge"
    assert result.data["executive"]["prompt_text_stored"] is False
    decision = app.cognitive_executive.decision_details(
        result.data["executive"]["public_id"]
    )
    assert decision is not None
    assert decision["status"] == "completed"
    assert decision["request_sha256"]
    assert "capital de Chile" not in str(decision)


def test_model_receives_executive_and_relevant_knowledge_only(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = ExecutiveEngine()
    app.language_engine = engine
    _promote(
        app,
        subject="fotosíntesis",
        statement="La fotosíntesis transforma energía luminosa en energía química.",
        domain="biología",
    )
    _promote(
        app,
        subject="capital de Chile",
        statement="La capital de Chile es Santiago.",
    )

    result = app.ask("Relaciona fotosíntesis, energía y crecimiento vegetal.")

    assert result.ok is True
    assert engine.calls
    context = "\n".join(engine.calls[0][1])
    assert "EJECUTIVO COGNITIVO" in context
    assert "fotosíntesis" in context.casefold()
    assert "capital de Chile" not in context
    assert result.data["executive"]["intent"] == "information"


def test_goals_tasks_dependencies_and_verification(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    goal = app.cognitive_executive.create_goal(
        title="Preparar revisión semanal",
        description="Ordenar pendientes y revisar avances.",
        domain="organizacion_personal",
        project="",
        priority="high",
        target_date="2026-08-09",
        next_action="Recopilar tareas abiertas.",
        actor="owner",
    )
    first = app.cognitive_executive.create_task(
        goal["public_id"],
        title="Recopilar tareas",
        priority="high",
        due_date=None,
        depends_on=(),
        actor="owner",
    )
    second = app.cognitive_executive.create_task(
        goal["public_id"],
        title="Preparar resumen",
        priority="normal",
        due_date=None,
        depends_on=(first["public_id"],),
        actor="owner",
    )

    try:
        app.cognitive_executive.complete_task(
            second["public_id"], evidence="Resumen preparado."
        )
    except ValueError as exc:
        assert "dependencias" in str(exc)
    else:
        raise AssertionError("La dependencia pendiente debía bloquear la tarea.")

    app.cognitive_executive.complete_task(
        first["public_id"], evidence="Lista recopilada."
    )
    completed = app.cognitive_executive.complete_task(
        second["public_id"], evidence="Resumen preparado."
    )
    assert completed["status"] == "completed"

    decision = app.cognitive_executive.assess(
        "Revisa el objetivo semanal.",
        route=app.router.route("Revisa el objetivo semanal."),
    )
    app.cognitive_executive.complete(
        decision,
        ok=True,
        actual_route="preview_only",
        engine="cognitive-executive",
    )
    verification = app.cognitive_executive.record_verification(
        decision_public_id=decision.public_id,
        expected_outcome="Resumen disponible.",
        observed_outcome="Resumen disponible y revisado.",
        method="revisión local",
        status="success",
        evidence={"task_id": second["public_id"]},
        actor="owner",
    )
    assert verification["status"] == "success"
    shown = app.cognitive_executive.goal_details(goal["public_id"])
    assert shown is not None
    assert len(shown["tasks"]) == 2

    other_goal = app.cognitive_executive.create_goal(
        title="Objetivo separado",
        description="No debe compartir dependencias.",
        domain="organizacion_personal",
        project="",
        priority="normal",
        target_date=None,
        next_action="Crear una tarea.",
        actor="owner",
    )
    with pytest.raises(ValueError, match="mismo objetivo"):
        app.cognitive_executive.create_task(
            other_goal["public_id"],
            title="Dependencia inválida",
            priority="normal",
            due_date=None,
            depends_on=(first["public_id"],),
            actor="owner",
        )

    with pytest.raises(ValueError, match="4000 caracteres"):
        app.cognitive_executive.record_verification(
            decision_public_id=None,
            expected_outcome="Evidencia acotada.",
            observed_outcome="Evidencia excesiva.",
            method="revisión local",
            status="failed",
            evidence={"payload": "x" * 4_100},
            actor="owner",
        )


def test_cli_exposes_executive_and_goal_commands() -> None:
    parser = build_parser()

    evaluated = parser.parse_args(
        ["assistant", "executive-evaluate", "explica la fotosíntesis"]
    )
    goal = parser.parse_args(
        [
            "assistant",
            "goal-create",
            "--title",
            "Revisión semanal",
            "--approve",
        ]
    )
    verify = parser.parse_args(
        [
            "assistant",
            "verify-outcome",
            "--expected",
            "A",
            "--observed",
            "A",
            "--method",
            "manual",
            "--status",
            "success",
            "--approve",
        ]
    )

    assert evaluated.assistant_command == "executive-evaluate"
    assert goal.assistant_command == "goal-create"
    assert verify.assistant_command == "verify-outcome"
