from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from elyndra.application import ElyndraApplication
from elyndra.engines import ConversationTurn, LanguageReply, NoModelEngine
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService


class RepairEngine:
    name = "repair-engine"
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


def _project() -> Path:
    root = Path.home() / "Proyectos" / "repair-cycle"
    (root / "src").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "repair-cycle"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (root / "src" / "app.py").write_text(
        "def value() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_smoke.py").write_text(
        "def test_smoke() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    return root


def _proposal_reply(content: str, summary: str) -> str:
    return json.dumps(
        {
            "summary": summary,
            "files": [{"path": "src/app.py", "content": content}],
        }
    )


def _apply_broken_change(app: ElyndraApplication, root: Path) -> str:
    engine = RepairEngine(
        [_proposal_reply("def value() -> int\n    return 2\n", "Introduce fallo")]
    )
    app.language_engine = engine
    app.change_planner.language_engine = engine
    proposed = app.propose_change(
        project_root=str(root),
        requested_files=["src/app.py"],
        instruction="Cambia el valor e introduce el contenido solicitado.",
    )
    assert proposed.ok is True
    proposal_id = str(proposed.data["change_proposal_id"])
    applied = app.apply_saved_change_proposal(proposal_id, approved=True)
    assert applied.ok is True
    return proposal_id


def test_failed_validation_requires_new_repair_approval(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = ElyndraApplication.load(isolated_home)
    change_id = _apply_broken_change(app, root)

    cycle = app.create_validation_cycle(
        change_id,
        validation_request=f"Ejecuta compileall en el proyecto Python {root}.",
    )

    assert cycle["status"] == "validation_proposed"
    assert cycle["source_change_proposal_id"] == change_id
    assert cycle["plan"]["steps"][0]["skill_name"] == "python.compile_project"
    assert app.action_runs.count() == 0

    app.language_engine = NoModelEngine("prueba determinista")
    validated = app.execute_validation_cycle(cycle["public_id"], approved=True)

    assert validated.ok is False
    assert validated.data["repair_available"] is True
    stored = app.validation_cycles.get(cycle["public_id"])
    assert stored is not None
    assert stored["status"] == "validation_failed"
    assert stored["repair_proposal_id"] is None
    assert app.change_proposals.count() == 1

    repair_engine = RepairEngine(
        [_proposal_reply("def value() -> int:\n    return 2\n", "Repara sintaxis")]
    )
    app.language_engine = repair_engine
    app.change_planner.language_engine = repair_engine
    repaired = app.propose_repair_for_cycle(
        cycle["public_id"],
        instruction="Corrige únicamente los fallos reales de la validación.",
    )

    assert repaired.ok is True
    repair_id = str(repaired.data["change_proposal_id"])
    assert (root / "src" / "app.py").read_text(encoding="utf-8") == (
        "def value() -> int\n    return 2\n"
    )
    assert "RESULTADOS REALES DE LA VALIDACIÓN SUPERVISADA" in repair_engine.calls[0][
        "prompt"
    ]
    assert "python.compile_project" in repair_engine.calls[0]["prompt"]
    assert app.validation_cycles.get(cycle["public_id"])["status"] == "repair_proposed"

    applied = app.apply_saved_change_proposal(repair_id, approved=True)

    assert applied.ok is True
    assert (root / "src" / "app.py").read_text(encoding="utf-8") == (
        "def value() -> int:\n    return 2\n"
    )
    final_cycle = app.validation_cycles.get(cycle["public_id"])
    assert final_cycle is not None
    assert final_cycle["status"] == "repair_applied"
    assert app.validation_cycles.count() == 1
    assert app.action_runs.count() == 1

    next_cycle = app.create_validation_cycle(
        repair_id,
        validation_request=f"Ejecuta compileall en el proyecto Python {root}.",
    )
    assert next_cycle["status"] == "validation_proposed"
    assert next_cycle["source_change_proposal_id"] == repair_id


def test_rejected_repair_reopens_cycle_for_a_new_manual_proposal(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = ElyndraApplication.load(isolated_home)
    change_id = _apply_broken_change(app, root)
    cycle = app.create_validation_cycle(
        change_id,
        validation_request=f"Ejecuta compileall en el proyecto Python {root}.",
    )
    app.language_engine = NoModelEngine()
    assert app.execute_validation_cycle(cycle["public_id"], approved=True).ok is False

    first_engine = RepairEngine(
        [_proposal_reply("def value() -> int:\n    return 2\n", "Primera reparación")]
    )
    app.language_engine = first_engine
    app.change_planner.language_engine = first_engine
    first = app.propose_repair_for_cycle(
        cycle["public_id"],
        instruction="Corrige el fallo real.",
    )
    first_id = str(first.data["change_proposal_id"])
    rejected = app.reject_saved_change_proposal(first_id, approved=True)

    assert rejected.ok is True
    reopened = app.validation_cycles.get(cycle["public_id"])
    assert reopened is not None
    assert reopened["status"] == "validation_failed"
    assert reopened["repair_proposal_id"] is None

    second_engine = RepairEngine(
        [_proposal_reply("def value() -> int:\n    return 3\n", "Segunda reparación")]
    )
    app.language_engine = second_engine
    app.change_planner.language_engine = second_engine
    second = app.propose_repair_for_cycle(
        cycle["public_id"],
        instruction="Propón otra reparación basada en la misma validación.",
    )

    assert second.ok is True
    assert second.data["change_proposal_id"] != first_id
    assert app.validation_cycles.get(cycle["public_id"])["status"] == (
        "repair_proposed"
    )


def test_passed_validation_does_not_offer_repair(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = ElyndraApplication.load(isolated_home)
    engine = RepairEngine(
        [_proposal_reply("def value() -> int:\n    return 2\n", "Cambio válido")]
    )
    app.language_engine = engine
    app.change_planner.language_engine = engine
    proposed = app.propose_change(
        project_root=str(root),
        requested_files=["src/app.py"],
        instruction="Cambia el valor a dos.",
    )
    change_id = str(proposed.data["change_proposal_id"])
    assert app.apply_saved_change_proposal(change_id, approved=True).ok is True
    cycle = app.create_validation_cycle(
        change_id,
        validation_request=f"Ejecuta compileall en el proyecto Python {root}.",
    )
    app.language_engine = NoModelEngine()

    result = app.execute_validation_cycle(cycle["public_id"], approved=True)

    assert result.ok is True
    assert result.data["repair_available"] is False
    assert app.validation_cycles.get(cycle["public_id"])["status"] == (
        "validation_passed"
    )
    repair = app.propose_repair_for_cycle(
        cycle["public_id"], instruction="Haz otra modificación."
    )
    assert repair.ok is False
    assert "Solo se puede proponer reparación" in repair.message


def test_validation_cycle_rejects_plan_outside_original_project(
    isolated_home: ElyndraPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project()
    app = ElyndraApplication.load(isolated_home)
    change_id = _apply_broken_change(app, root)
    outside = Path.home() / "Proyectos" / "outside"
    outside.mkdir(parents=True)
    original = app.action_planner.propose

    def changed_path(text: str, route=None, *, force: bool = False):
        return original(
            f"Ejecuta compileall en el proyecto Python {outside}.",
            route,
            force=True,
        )

    monkeypatch.setattr(app.action_planner, "propose", changed_path)

    with pytest.raises(ValueError, match="sale del proyecto"):
        app.create_validation_cycle(
            change_id,
            validation_request=f"Ejecuta compileall en {root}.",
        )


def test_web_validation_approval_is_single_use_and_cancellable(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = ElyndraApplication.load(isolated_home)
    change_id = _apply_broken_change(app, root)
    app.language_engine = NoModelEngine()
    service = ElyndraWebService(app)
    chat_id = service.create_chat(title="Validación")['chat']['id']
    prompt = f"Valida el cambio {change_id} ejecutando compileall en el proyecto Python {root}."

    pending = service.send_message(chat_id, prompt)

    assert pending["ok"] is False
    assert pending["meta"]["approval_required"] is True
    assert pending["meta"]["skill_name"] == "assistant.validation_cycle.run"
    cycle_id = pending["meta"]["validation_cycle_id"]
    token = pending["meta"]["approval_token"]
    assert app.action_runs.count() == 0
    assert service.cancel_skill_approval(chat_id, token) is True
    assert app.validation_cycles.get(cycle_id)["status"] == "cancelled"
    with pytest.raises(ValueError, match="cancelada"):
        service.send_message(chat_id, prompt, approval_token=token)
    assert app.action_runs.count() == 0


def test_schema_and_cli_expose_validation_cycles(
    isolated_home: ElyndraPaths,
) -> None:
    from elyndra.cli import build_parser

    app = ElyndraApplication.load(isolated_home)
    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='assistant_validation_cycles'"
        ).fetchone()
    assert schema == "51"
    assert table is not None

    parser = build_parser()
    planned = parser.parse_args(
        [
            "assistant",
            "validate-plan",
            "a" * 32,
            "--request",
            "Ejecuta Ruff en /tmp/proyecto",
        ]
    )
    run = parser.parse_args(
        ["assistant", "validate-run", "b" * 32, "--approve"]
    )
    repair = parser.parse_args(
        [
            "assistant",
            "repair-plan",
            "c" * 32,
            "--instruction",
            "Corrige el fallo",
        ]
    )
    assert planned.assistant_command == "validate-plan"
    assert run.approve is True
    assert repair.assistant_command == "repair-plan"
