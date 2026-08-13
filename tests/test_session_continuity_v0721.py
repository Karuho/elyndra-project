from __future__ import annotations

import json
from pathlib import Path

from elyndra.application import ElyndraApplication
from elyndra.engines import ConversationTurn, LanguageReply
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService


class ProposalEngine:
    name = "proposal-engine"
    supports_vision = False

    def __init__(self, content: str = "VALUE = 2\n") -> None:
        self.content = content

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
        payload = json.dumps(
            {
                "summary": "Actualizar VALUE de forma supervisada.",
                "files": [{"path": "src/app.py", "content": self.content}],
            }
        )
        return LanguageReply(payload, self.name, True, {})

    def release(self) -> None:
        return None


class ContextEngine:
    name = "context-engine"
    supports_vision = False

    def __init__(self) -> None:
        self.contexts: list[tuple[str, ...]] = []

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
        self.contexts.append(context)
        return LanguageReply("Contexto de sesión recibido.", self.name, True, {})

    def release(self) -> None:
        return None


def _project() -> Path:
    root = Path.home() / "Proyectos" / "session-continuity"
    (root / "src").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "session-continuity"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root


def _app_with_proposal_engine(paths: ElyndraPaths) -> ElyndraApplication:
    app = ElyndraApplication.load(paths)
    engine = ProposalEngine()
    app.language_engine = engine
    app.change_planner.language_engine = engine
    return app


def test_change_plan_message_prints_proposal_and_session_ids(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = _app_with_proposal_engine(isolated_home)

    result = app.propose_change(
        project_root=str(root),
        requested_files=["src/app.py"],
        instruction="Actualiza VALUE.",
    )

    proposal_id = str(result.data["change_proposal_id"])
    session_id = str(result.data["development_session_id"])
    assert proposal_id in result.message
    assert session_id in result.message
    assert "ID de sesión de desarrollo" in result.message


def test_chat_created_change_starts_and_focuses_session(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = _app_with_proposal_engine(isolated_home)
    chat_id = "chat_session_continuity"

    result = app.ask(
        f"Modifica {root / 'src' / 'app.py'} para cambiar VALUE a 2.",
        chat_id=chat_id,
    )

    assert result.ok is False
    assert result.data["approval_required"] is True
    session_id = str(result.data["development_session_id"])
    focused = app.development_sessions.focused_for_chat(
        chat_id,
        actor=app.identity.system_user,
    )
    assert focused is not None
    assert focused["public_id"] == session_id
    assert session_id in str(result.data["approval_summary"])


def test_what_next_returns_guidance_without_executing_or_writing(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    target = root / "src" / "app.py"
    app = _app_with_proposal_engine(isolated_home)
    chat_id = "chat_next_action"
    proposed = app.propose_change(
        project_root=str(root),
        requested_files=["src/app.py"],
        instruction="Actualiza VALUE.",
        chat_id=chat_id,
    )
    proposal_id = str(proposed.data["change_proposal_id"])

    result = app.ask("¿Qué sigue?", chat_id=chat_id)

    assert result.ok is True
    assert result.data["fast_path"] == "development_session_guidance"
    assert result.data["automatic_execution"] is False
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert app.action_runs.count() == 0
    commands = [item["command"] for item in result.data["suggested_actions"]]
    assert any(f"change-show {proposal_id}" in command for command in commands)
    assert any(f"change-apply {proposal_id} --approve" in command for command in commands)


def test_applied_change_guidance_suggests_validation_not_execution(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = _app_with_proposal_engine(isolated_home)
    chat_id = "chat_after_apply"
    proposed = app.propose_change(
        project_root=str(root),
        requested_files=["src/app.py"],
        instruction="Actualiza VALUE.",
        chat_id=chat_id,
    )
    proposal_id = str(proposed.data["change_proposal_id"])
    assert app.apply_saved_change_proposal(proposal_id, approved=True).ok is True

    result = app.ask("¿Cuál es el siguiente paso?", chat_id=chat_id)

    assert result.ok is True
    commands = [item["command"] for item in result.data["suggested_actions"]]
    assert any(f"validate-plan {proposal_id}" in command for command in commands)
    assert app.validation_cycles.count() == 0


def test_explicit_session_reference_focuses_another_chat(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = _app_with_proposal_engine(isolated_home)
    proposed = app.propose_change(
        project_root=str(root),
        requested_files=["src/app.py"],
        instruction="Actualiza VALUE.",
    )
    session_id = str(proposed.data["development_session_id"])

    result = app.ask(
        f"Estado de la sesión {session_id}",
        chat_id="chat_explicit_focus",
    )

    assert result.ok is True
    assert result.data["development_session_id"] == session_id
    focused = app.development_sessions.focused_for_chat(
        "chat_explicit_focus",
        actor=app.identity.system_user,
    )
    assert focused is not None
    assert focused["public_id"] == session_id


def test_active_session_context_is_bounded_and_sent_without_authority(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = _app_with_proposal_engine(isolated_home)
    chat_id = "chat_context"
    proposed = app.propose_change(
        project_root=str(root),
        requested_files=["src/app.py"],
        instruction="Actualiza VALUE.",
        chat_id=chat_id,
    )
    session_id = str(proposed.data["development_session_id"])
    engine = ContextEngine()
    app.language_engine = engine

    result = app.ask(
        "Explícame el objetivo con otras palabras.",
        chat_id=chat_id,
    )

    assert result.ok is True
    assert result.data["development_session_id"] == session_id
    assert engine.contexts
    joined = "\n".join(engine.contexts[-1])
    assert "CONTEXTO LOCAL DE SESIÓN DE DESARROLLO" in joined
    assert "NO ES AUTORIZACIÓN" in joined
    assert session_id in joined
    assert "No afirmes que una acción fue ejecutada" in joined


def test_closed_session_clears_chat_focus(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = _app_with_proposal_engine(isolated_home)
    chat_id = "chat_close_focus"
    proposed = app.propose_change(
        project_root=str(root),
        requested_files=["src/app.py"],
        instruction="Actualiza VALUE.",
        chat_id=chat_id,
    )
    session_id = str(proposed.data["development_session_id"])

    closed = app.close_development_session(session_id, approved=True)

    assert closed.ok is True
    assert (
        app.development_sessions.focused_for_chat(
            chat_id,
            actor=app.identity.system_user,
        )
        is None
    )


def test_control_center_exposes_next_actions_and_frontend_renders_them(
    isolated_home: ElyndraPaths,
) -> None:
    root = _project()
    app = _app_with_proposal_engine(isolated_home)
    proposed = app.propose_change(
        project_root=str(root),
        requested_files=["src/app.py"],
        instruction="Actualiza VALUE.",
    )
    service = ElyndraWebService(app)

    sessions = service.control_development_sessions()

    assert sessions[0]["public_id"] == proposed.data["development_session_id"]
    assert sessions[0]["guidance"]["actions"]
    app_js = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "elyndra"
        / "web"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")
    assert "Acciones sugeridas · no ejecutadas" in app_js
    assert "meta.suggested_actions" in app_js


def test_schema_advances_to_29_with_chat_session_focus(
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
    assert schema == "50"
    assert "assistant_chat_session_focus" in tables
    assert len(app.skills.list_all()) == 102
