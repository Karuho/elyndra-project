from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from elyndra.application import ElyndraApplication
from elyndra.engines import ConversationTurn, LanguageReply
from elyndra.orchestration import ActionPlan, AssistantActionPlanner
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService


class RecordingEngine:
    name = "recording-engine"
    supports_vision = False

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def reply(
        self,
        prompt: str,
        *,
        context: tuple[str, ...] = (),
        history: tuple[ConversationTurn, ...] = (),
        response_language: str | None = None,
        keep_alive_seconds: int = 0,
        images: tuple[str, ...] = (),
        max_tokens: int | None = None,
        on_token=None,
    ) -> LanguageReply:
        self.calls.append(
            {
                "prompt": prompt,
                "context": context,
                "history": history,
                "response_language": response_language,
                "keep_alive_seconds": keep_alive_seconds,
                "images": images,
                "max_tokens": max_tokens,
            }
        )
        text = self.replies.pop(0)
        if on_token is not None:
            on_token(text)
        return LanguageReply(text, self.name, True, {})

    def release(self) -> None:
        return None


def _sql_project() -> Path:
    project = Path.home() / "Proyectos" / "sql-orchestration"
    project.mkdir(parents=True)
    (project / "queries.sql").write_text(
        "SELECT id, name FROM users ORDER BY name;\n",
        encoding="utf-8",
    )
    return project


def _prompt(project: Path) -> str:
    return (
        f"Revisa el proyecto SQL {project}, identifica problemas y explícame "
        "qué debería corregir."
    )


def test_planner_creates_bounded_deterministic_sql_plan(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project()
    app = ElyndraApplication.load(isolated_home)

    plan = app.action_planner.propose(_prompt(project))

    assert plan is not None
    assert plan.source == "deterministic-router"
    assert len(plan.steps) == 1
    assert plan.steps[0].skill_name == "sql.verify_project"
    assert plan.steps[0].params == {"path": str(project)}
    assert plan.plan_id.startswith("plan_")


def test_web_plan_requires_approval_and_records_exact_execution(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project()
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    chat_id = service.create_chat(title="Orquestación")['chat']['id']
    prompt = _prompt(project)
    before_hash = hashlib.sha256((project / "queries.sql").read_bytes()).hexdigest()

    pending = service.send_message(chat_id, prompt)

    assert pending["ok"] is False
    assert pending["meta"]["approval_required"] is True
    assert pending["meta"]["skill_name"] == "assistant.action_plan"
    assert pending["meta"]["orchestration"] is True
    assert pending["meta"]["plan_steps"] == 1
    assert pending["meta"]["action_plan"]["steps"][0]["skill_name"] == "sql.verify_project"
    assert service.chat_detail(chat_id)["chat"]["turn_count"] == 0
    assert app.action_runs.count() == 0

    approved = service.send_message(
        chat_id,
        prompt,
        approval_token=pending["meta"]["approval_token"],
    )

    assert approved["ok"] is True
    assert approved["meta"]["orchestration"] is True
    assert approved["meta"]["status"] == "passed"
    assert approved["meta"]["action_run_id"]
    assert service.chat_detail(chat_id)["chat"]["turn_count"] == 1
    assert app.action_runs.count() == 1
    run = app.action_runs.get(approved["meta"]["action_run_id"])
    assert run is not None
    assert run["status"] == "passed"
    assert run["plan"]["plan_id"] == pending["meta"]["plan_id"]
    assert hashlib.sha256((project / "queries.sql").read_bytes()).hexdigest() == before_hash


def test_cancelled_supervised_plan_does_not_execute_or_record_turn(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project()
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    chat_id = service.create_chat(title="Cancelación")['chat']['id']
    prompt = _prompt(project)

    pending = service.send_message(chat_id, prompt)
    token = pending["meta"]["approval_token"]

    assert service.cancel_skill_approval(chat_id, token) is True
    assert app.action_runs.count() == 0
    assert service.chat_detail(chat_id)["chat"]["turn_count"] == 0
    with pytest.raises(ValueError, match="cancelada"):
        service.send_message(chat_id, prompt, approval_token=token)
    assert app.action_runs.count() == 0


def test_approval_executes_frozen_plan_without_replanning(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _sql_project()
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    chat_id = service.create_chat(title="Plan congelado")['chat']['id']
    prompt = _prompt(project)
    pending = service.send_message(chat_id, prompt)

    def fail_replan(*args, **kwargs):
        raise AssertionError("No se debe recalcular un plan ya aprobado.")

    monkeypatch.setattr(app.action_planner, "propose", fail_replan)
    approved = service.send_message(
        chat_id,
        prompt,
        approval_token=pending["meta"]["approval_token"],
    )

    assert approved["meta"]["plan_id"] == pending["meta"]["plan_id"]
    assert approved["meta"]["status"] == "passed"


def test_plan_validator_rejects_non_allowlisted_write_skill(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    payload = {
        "request": "Recuerda un secreto",
        "source": "language-model-proposal",
        "steps": [
            {
                "skill_name": "memory.remember",
                "params": {"content": "x"},
                "purpose": "Guardar memoria",
            }
        ],
    }

    with pytest.raises(ValueError, match="no permitida"):
        ActionPlan.from_dict(payload, registry=app.skills)


def test_model_planner_rejects_a_changed_path(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = RecordingEngine(
        [
            '{"summary":"Plan","steps":[{"skill":"python.verify_project",'
            '"params":{"path":"/tmp/otro"},"purpose":"Verificar"}]}'
        ]
    )
    planner = AssistantActionPlanner(
        registry=app.skills,
        router=app.router,
        language_engine=engine,
    )

    plan = planner.propose(
        "Comprueba de forma controlada /tmp/mi-proyecto y entrega conclusiones."
    )

    assert plan is None
    assert len(engine.calls) == 1


def test_model_synthesis_receives_bounded_real_results(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project()
    app = ElyndraApplication.load(isolated_home)
    engine = RecordingEngine(["El proyecto SQL pasó la verificación controlada."])
    app.language_engine = engine
    app.action_planner.language_engine = engine

    result = app.ask(_prompt(project), approved=True)

    assert result.ok is True
    assert result.message == "El proyecto SQL pasó la verificación controlada."
    assert result.data["generated"] is True
    assert result.data["engine"].startswith("assistant-orchestrator:")
    assert len(engine.calls) == 1
    joined_context = "\n".join(engine.calls[0]["context"])
    assert "PLAN AUTORIZADO Y RESULTADOS REALES" in joined_context
    assert "sql.verify_project" in joined_context
    assert "Estado: passed" in joined_context


def test_schema_advances_to_25_for_action_run_history(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='assistant_action_runs'"
        ).fetchone()
    assert schema == "51"
    assert table is not None


def test_assistant_cli_exposes_supervised_plan_commands() -> None:
    from elyndra.cli import build_parser

    parser = build_parser()
    planned = parser.parse_args(
        ["assistant", "plan", "Revisa el proyecto Python /tmp/proyecto"]
    )
    executed = parser.parse_args(
        ["assistant", "run", "preview_123", "--approve"]
    )
    history = parser.parse_args(["assistant", "history", "--limit", "7"])

    assert planned.assistant_command == "plan"
    assert executed.assistant_command == "run"
    assert executed.plan_id == "preview_123"
    assert executed.approve is True
    assert history.assistant_command == "history"
    assert history.limit == 7


def test_control_center_exposes_supervised_action_history(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project()
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    result = app.ask(_prompt(project), approved=True)
    overview = service.control_overview()
    runs = service.control_action_runs(limit=10)
    static_root = Path(__file__).parents[1] / "src" / "elyndra" / "web" / "static"
    script = (static_root / "app.js").read_text(encoding="utf-8")
    html = (static_root / "index.html").read_text(encoding="utf-8")

    assert result.ok is True
    assert overview["assistant_action_runs"] == 1
    assert len(runs) == 1
    assert runs[0]["plan"]["steps"][0]["skill_name"] == "sql.verify_project"
    assert "/api/control/action-runs" in script
    assert 'id="control-action-runs"' in html


def test_approved_plan_rejects_tampering_before_execution(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project()
    app = ElyndraApplication.load(isolated_home)
    prompt = _prompt(project)
    plan = app.action_planner.propose(prompt)
    assert plan is not None
    payload = plan.to_dict()
    payload["steps"][0]["params"]["path"] = "/tmp/otro"

    result = app.ask(prompt, approved=True, approved_action_plan=payload)

    assert result.ok is False
    assert "identificador" in result.message.lower()
    assert app.action_runs.count() == 0


def test_saved_cli_plan_executes_exactly_once(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project()
    app = ElyndraApplication.load(isolated_home)
    plan = app.action_planner.propose(_prompt(project))
    assert plan is not None
    preview_id = app.action_runs.save_preview(
        plan=plan,
        actor=app.identity.system_user,
    )

    first = app.execute_saved_action_plan(preview_id, approved=True)
    second = app.execute_saved_action_plan(preview_id, approved=True)

    assert first.ok is True
    assert first.data["action_run_id"] == preview_id
    assert second.ok is False
    assert "ya fue utilizado" in second.message
    item = app.action_runs.get(preview_id)
    assert item is not None
    assert item["status"] == "passed"


def test_explicit_python_tools_create_bounded_multi_step_plan(
    isolated_home: ElyndraPaths,
) -> None:
    project = Path.home() / "Proyectos" / "python-orchestration"
    project.mkdir(parents=True)
    app = ElyndraApplication.load(isolated_home)

    plan = app.action_planner.propose(
        f"Inspecciona Python {project} con compileall, Ruff, mypy y Pytest.",
        force=True,
    )

    assert plan is not None
    assert plan.source == "deterministic-tools"
    assert [step.skill_name for step in plan.steps] == [
        "python.project_inspect",
        "python.compile_project",
        "ruff.check",
        "mypy.check",
    ]
    assert len(plan.steps) == 4


def test_approved_plan_id_covers_fail_fast_policy(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project()
    app = ElyndraApplication.load(isolated_home)
    prompt = _prompt(project)
    plan = app.action_planner.propose(prompt)
    assert plan is not None
    payload = plan.to_dict()
    payload["fail_fast"] = False

    result = app.ask(prompt, approved=True, approved_action_plan=payload)

    assert result.ok is False
    assert "identificador" in result.message.lower()
    assert app.action_runs.count() == 0


def test_frozen_plan_requires_valid_approval_flag(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project()
    app = ElyndraApplication.load(isolated_home)
    prompt = _prompt(project)
    plan = app.action_planner.propose(prompt)
    assert plan is not None

    result = app.ask(prompt, approved=False, approved_action_plan=plan.to_dict())

    assert result.ok is False
    assert "aprobación válida" in result.message
    assert app.action_runs.count() == 0


def test_saved_cli_plan_requires_explicit_approval(
    isolated_home: ElyndraPaths,
) -> None:
    project = _sql_project()
    app = ElyndraApplication.load(isolated_home)
    plan = app.action_planner.propose(_prompt(project))
    assert plan is not None
    preview_id = app.action_runs.save_preview(
        plan=plan,
        actor=app.identity.system_user,
    )

    result = app.execute_saved_action_plan(preview_id)

    assert result.ok is False
    assert "aprobación explícita" in result.message
    assert app.action_runs.get(preview_id)["status"] == "planned"


def test_model_plan_rejects_more_than_four_steps(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    steps = ",".join(
        '{"skill":"python.project_inspect","params":{"path":"/tmp/app"},'
        '"purpose":"Inspect"}'
        for _ in range(5)
    )
    engine = RecordingEngine([f'{{"summary":"Too many","steps":[{steps}]}}'])
    planner = AssistantActionPlanner(
        registry=app.skills,
        router=app.router,
        language_engine=engine,
    )

    plan = planner.propose(
        "Analiza de forma controlada /tmp/app y entrega un informe.",
        force=True,
    )

    assert plan is None
    assert len(engine.calls) == 1
