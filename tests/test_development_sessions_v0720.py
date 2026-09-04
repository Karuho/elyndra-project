from __future__ import annotations

import json
from pathlib import Path

from elyndra.application import ElyndraApplication
from elyndra.engines import ConversationTurn, LanguageReply, NoModelEngine
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService


class SessionEngine:
    name = "session-engine"
    supports_vision = False

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)

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
        text = self.replies.pop(0)
        if on_token is not None:
            on_token(text)
        return LanguageReply(text, self.name, True, {})

    def release(self) -> None:
        return None


def _project() -> Path:
    root = Path.home() / "Proyectos" / "development-session"
    (root / "src").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "development-session"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_smoke.py").write_text(
        "def test_smoke() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    return root


def _reply(content: str = "VALUE = 2\n") -> str:
    return json.dumps(
        {
            "summary": "Actualizar el valor de la sesión.",
            "files": [{"path": "src/app.py", "content": content}],
        }
    )


def _propose(app: ElyndraApplication, root: Path, content: str = "VALUE = 2\n"):
    engine = SessionEngine([_reply(content)])
    app.language_engine = engine
    app.change_planner.language_engine = engine
    return app.propose_change(
        project_root=str(root),
        requested_files=["src/app.py"],
        instruction="Actualiza VALUE de forma revisable.",
    )


def test_change_proposal_starts_one_persistent_session(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = ElyndraApplication.load(isolated_home)

    proposed = _propose(app, root)

    assert proposed.ok is True
    session_id = str(proposed.data["development_session_id"])
    session = app.development_sessions.get(session_id)
    assert session is not None
    assert session["status"] == "active"
    assert session["root_change_proposal_id"] == proposed.data["change_proposal_id"]
    assert session["objective"] == "Actualiza VALUE de forma revisable."
    assert [event["event_type"] for event in session["events"]] == [
        "change_proposed"
    ]
    assert app.development_sessions.count() == 1


def test_applied_change_and_validation_share_the_same_timeline(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = ElyndraApplication.load(isolated_home)
    proposed = _propose(app, root)
    proposal_id = str(proposed.data["change_proposal_id"])
    session_id = str(proposed.data["development_session_id"])

    applied = app.apply_saved_change_proposal(proposal_id, approved=True)
    cycle = app.create_validation_cycle(
        proposal_id,
        validation_request=f"Ejecuta compileall en el proyecto Python {root}.",
    )
    app.language_engine = NoModelEngine("resumen determinista")
    validated = app.execute_validation_cycle(cycle["public_id"], approved=True)

    assert applied.ok is True
    assert applied.data["development_session_id"] == session_id
    assert cycle["development_session_id"] == session_id
    assert validated.data["development_session_id"] == session_id
    session = app.development_sessions.get(session_id)
    assert session is not None
    assert session["status"] == "completed"
    assert [event["event_type"] for event in session["events"]] == [
        "change_proposed",
        "change_applied",
        "validation_proposed",
        "validation_passed",
    ]


def test_failed_validation_and_repair_remain_in_original_session(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = ElyndraApplication.load(isolated_home)
    proposed = _propose(app, root, "def broken(\n")
    proposal_id = str(proposed.data["change_proposal_id"])
    session_id = str(proposed.data["development_session_id"])
    assert app.apply_saved_change_proposal(proposal_id, approved=True).ok is True
    cycle = app.create_validation_cycle(
        proposal_id,
        validation_request=f"Ejecuta compileall en el proyecto Python {root}.",
    )
    app.language_engine = NoModelEngine()
    validated = app.execute_validation_cycle(cycle["public_id"], approved=True)
    assert validated.ok is False

    engine = SessionEngine([_reply("VALUE = 3\n")])
    app.language_engine = engine
    app.change_planner.language_engine = engine
    repair = app.propose_repair_for_cycle(
        cycle["public_id"],
        instruction="Corrige únicamente el fallo real.",
    )

    assert repair.ok is True
    assert repair.data["development_session_id"] == session_id
    repair_id = str(repair.data["change_proposal_id"])
    found = app.development_sessions.find_by_change(repair_id)
    assert found is not None
    assert found["public_id"] == session_id
    session = app.development_sessions.get(session_id)
    assert session is not None
    assert session["status"] == "active"
    assert session["current_change_proposal_id"] == repair_id
    assert session["events"][-1]["event_type"] == "repair_proposed"


def test_session_close_requires_approval_and_does_not_execute_actions(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = ElyndraApplication.load(isolated_home)
    proposed = _propose(app, root)
    session_id = str(proposed.data["development_session_id"])

    denied = app.close_development_session(session_id)
    closed = app.close_development_session(session_id, approved=True)

    assert denied.ok is False
    assert closed.ok is True
    assert app.action_runs.count() == 0
    session = app.development_sessions.get(session_id)
    assert session is not None
    assert session["status"] == "closed"
    assert session["events"][-1]["event_type"] == "session_closed"


def test_control_center_exposes_session_history(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = ElyndraApplication.load(isolated_home)
    proposed = _propose(app, root)
    service = ElyndraWebService(app)

    overview = service.control_overview()
    sessions = service.control_development_sessions()

    assert overview["assistant_development_sessions"] == 1
    assert sessions[0]["public_id"] == proposed.data["development_session_id"]
    static = Path(__file__).resolve().parents[1] / "src" / "elyndra" / "web" / "static"
    assert "control-development-sessions" in (static / "index.html").read_text()
    app_js = (static / "app.js").read_text()
    assert "/api/control/development-sessions" in app_js
    assert "renderControlDevelopmentSessions" in app_js


def test_schema_advances_to_28_for_development_sessions(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert schema == "51"
    assert "assistant_development_sessions" in tables
    assert "assistant_development_session_events" in tables
    assert len(app.skills.list_all()) == 102


def test_existing_proposal_can_be_adopted_idempotently(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = ElyndraApplication.load(isolated_home)
    engine = SessionEngine([_reply()])
    app.change_planner.language_engine = engine
    proposal = app.change_planner.propose(
        project_root=str(root),
        requested_files=["src/app.py"],
        instruction="Propuesta previa a la migración de sesiones.",
    )
    proposal_id = app.change_proposals.save(
        proposal,
        actor=app.identity.system_user,
    )
    assert app.development_sessions.count() == 0

    first = app.start_development_session(proposal_id)
    second = app.start_development_session(proposal_id)

    assert first.ok is True
    assert second.ok is True
    assert first.data["development_session_id"] == second.data[
        "development_session_id"
    ]
    assert app.development_sessions.count() == 1
