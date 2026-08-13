from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from elyndra.application import ElyndraApplication
from elyndra.engines import ConversationTurn, LanguageReply
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService


class ChangeEngine:
    name = "change-engine"
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


def _project() -> tuple[Path, Path]:
    root = Path.home() / "Proyectos" / "change-proposals"
    source = root / "src" / "example.py"
    source.parent.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "change-proposals"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    source.write_text("VALUE = 1\n", encoding="utf-8")
    return root, source


def _reply(path: str = "src/example.py", content: str = "VALUE = 2\n") -> str:
    return json.dumps(
        {
            "summary": "Actualizar el valor de ejemplo.",
            "files": [{"path": path, "content": content}],
        }
    )


def _app_with_engine(
    isolated_home: ElyndraPaths, replies: list[str]
) -> tuple[ElyndraApplication, ChangeEngine]:
    app = ElyndraApplication.load(isolated_home)
    engine = ChangeEngine(replies)
    app.change_planner.language_engine = engine
    return app, engine


def test_change_proposal_creates_exact_diff_without_writing(
    isolated_home: ElyndraPaths,
) -> None:
    root, source = _project()
    app, engine = _app_with_engine(isolated_home, [_reply()])
    before = source.read_text(encoding="utf-8")

    result = app.propose_change(
        project_root=str(root),
        requested_files=["src/example.py"],
        instruction="Cambia VALUE a 2.",
    )

    assert result.ok is True
    assert source.read_text(encoding="utf-8") == before
    public_id = result.data["change_proposal_id"]
    stored = app.change_proposals.get(public_id)
    assert stored is not None
    assert stored["status"] == "proposed"
    assert "-VALUE = 1" in stored["diff"]
    assert "+VALUE = 2" in stored["diff"]
    assert stored["proposal"]["changes"][0]["base_sha256"] == hashlib.sha256(
        before.encode()
    ).hexdigest()
    assert "No tienes herramientas" in engine.calls[0]["prompt"]


def test_approved_change_applies_once_and_preserves_mode(
    isolated_home: ElyndraPaths,
) -> None:
    root, source = _project()
    source.chmod(0o640)
    app, _ = _app_with_engine(isolated_home, [_reply()])
    proposal = app.propose_change(
        project_root=str(root),
        requested_files=["src/example.py"],
        instruction="Cambia VALUE a 2.",
    )
    public_id = proposal.data["change_proposal_id"]

    applied = app.apply_saved_change_proposal(public_id, approved=True)
    repeated = app.apply_saved_change_proposal(public_id, approved=True)

    assert applied.ok is True
    assert applied.data["status"] == "applied"
    assert source.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert source.stat().st_mode & 0o777 == 0o640
    assert repeated.ok is False
    assert "ya fue utilizada" in repeated.message
    assert app.change_proposals.get(public_id)["status"] == "applied"


def test_multi_file_apply_rolls_back_when_second_replace_fails(
    isolated_home: ElyndraPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, first = _project()
    second = root / "src" / "second.py"
    second.write_text("SECOND = 1\n", encoding="utf-8")
    app, _ = _app_with_engine(
        isolated_home,
        [
            json.dumps(
                {
                    "summary": "Actualizar dos archivos.",
                    "files": [
                        {"path": "src/example.py", "content": "VALUE = 2\n"},
                        {"path": "src/second.py", "content": "SECOND = 2\n"},
                    ],
                }
            )
        ],
    )
    proposal = app.propose_change(
        project_root=str(root),
        requested_files=["src/example.py", "src/second.py"],
        instruction="Actualiza ambos valores.",
    )
    assert proposal.ok is True

    real_replace = os.replace
    calls = 0

    def fail_second_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("fallo simulado")
        return real_replace(source, destination)

    monkeypatch.setattr("elyndra.change_proposals.os.replace", fail_second_replace)
    applied = app.apply_saved_change_proposal(
        proposal.data["change_proposal_id"], approved=True
    )

    assert applied.ok is False
    assert first.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert second.read_text(encoding="utf-8") == "SECOND = 1\n"
    assert app.change_proposals.get(proposal.data["change_proposal_id"])[
        "status"
    ] == "failed"


def test_stale_change_is_rejected_without_overwriting_new_content(
    isolated_home: ElyndraPaths,
) -> None:
    root, source = _project()
    app, _ = _app_with_engine(isolated_home, [_reply()])
    proposal = app.propose_change(
        project_root=str(root),
        requested_files=["src/example.py"],
        instruction="Cambia VALUE a 2.",
    )
    public_id = proposal.data["change_proposal_id"]
    source.write_text("VALUE = 99\n", encoding="utf-8")

    result = app.apply_saved_change_proposal(public_id, approved=True)

    assert result.ok is False
    assert result.data["status"] == "stale"
    assert source.read_text(encoding="utf-8") == "VALUE = 99\n"
    assert app.change_proposals.get(public_id)["status"] == "stale"


def test_model_cannot_add_an_unrequested_file(
    isolated_home: ElyndraPaths,
) -> None:
    root, source = _project()
    app, _ = _app_with_engine(isolated_home, [_reply("src/other.py")])

    result = app.propose_change(
        project_root=str(root),
        requested_files=[str(source.relative_to(root))],
        instruction="Cambia VALUE a 2.",
    )

    assert result.ok is False
    assert "no solicitado" in result.message
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert app.change_proposals.count() == 0


def test_change_proposal_can_create_file_but_not_directory(
    isolated_home: ElyndraPaths,
) -> None:
    root, _ = _project()
    app, _ = _app_with_engine(
        isolated_home,
        [_reply("src/new_module.py", "def answer() -> int:\n    return 42\n")],
    )

    proposal = app.propose_change(
        project_root=str(root),
        requested_files=["src/new_module.py"],
        instruction="Crea el módulo solicitado.",
    )
    assert proposal.ok is True
    new_file = root / "src" / "new_module.py"
    assert not new_file.exists()

    applied = app.apply_saved_change_proposal(
        proposal.data["change_proposal_id"], approved=True
    )

    assert applied.ok is True
    assert new_file.read_text(encoding="utf-8").startswith("def answer")

    failed = app.propose_change(
        project_root=str(root),
        requested_files=["missing/new.py"],
        instruction="Crea otro módulo.",
    )
    assert failed.ok is False
    assert "no crea carpetas" in failed.message


def test_secret_paths_and_symlinks_are_rejected(
    isolated_home: ElyndraPaths,
) -> None:
    root, source = _project()
    app, _ = _app_with_engine(isolated_home, [_reply(), _reply()])

    secret = app.propose_change(
        project_root=str(root),
        requested_files=[".env"],
        instruction="Agrega configuración.",
    )
    assert secret.ok is False
    assert "secretos" in secret.message

    link = root / "src" / "linked.py"
    link.symlink_to(source)
    linked = app.propose_change(
        project_root=str(root),
        requested_files=["src/linked.py"],
        instruction="Corrige el enlace.",
    )
    assert linked.ok is False
    assert "enlaces simbólicos" in linked.message


def test_empty_replacement_and_existing_secret_material_are_rejected(
    isolated_home: ElyndraPaths,
) -> None:
    root, source = _project()
    app, _ = _app_with_engine(isolated_home, [_reply(content="   \n")])

    emptied = app.propose_change(
        project_root=str(root),
        requested_files=["src/example.py"],
        instruction="Vacía el archivo.",
    )
    assert emptied.ok is False
    assert "vaciaría" in emptied.message
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"

    source.write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n", encoding="utf-8"
    )
    secret = app.propose_change(
        project_root=str(root),
        requested_files=["src/example.py"],
        instruction="Corrige el archivo.",
    )
    assert secret.ok is False
    assert "apariencia de secreto" in secret.message


def test_protected_directory_and_symlink_project_root_are_rejected(
    isolated_home: ElyndraPaths,
) -> None:
    root, _ = _project()
    app, _ = _app_with_engine(isolated_home, [_reply(".github/workflow.yml")])

    protected = app.propose_change(
        project_root=str(root),
        requested_files=[".github/workflow.yml"],
        instruction="Crea el workflow.",
    )
    assert protected.ok is False
    assert "carpeta protegida" in protected.message

    link_root = root.parent / "change-proposals-link"
    link_root.symlink_to(root, target_is_directory=True)
    linked = app.propose_change(
        project_root=str(link_root),
        requested_files=["src/example.py"],
        instruction="Cambia VALUE a 2.",
    )
    assert linked.ok is False
    assert "raíz del proyecto" in linked.message


def test_quoted_absolute_path_with_spaces_creates_a_reviewable_proposal(
    isolated_home: ElyndraPaths,
) -> None:
    root = Path.home() / "Proyectos" / "project with spaces"
    source = root / "src" / "example file.py"
    source.parent.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "space-project"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    source.write_text("VALUE = 1\n", encoding="utf-8")
    app, _ = _app_with_engine(
        isolated_home, [_reply("src/example file.py", "VALUE = 2\n")]
    )

    result = app.ask(f'Corrige "{source}" y cambia VALUE a 2.')

    assert result.ok is False
    assert result.data["approval_required"] is True
    assert result.data["change_files"] == ["src/example file.py"]
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_chat_ignores_project_directory_and_uses_only_explicit_files(
    isolated_home: ElyndraPaths,
) -> None:
    root, source = _project()
    app, _ = _app_with_engine(isolated_home, [_reply()])

    result = app.ask(
        f"En {root}, corrige {source} para cambiar VALUE a 2."
    )

    assert result.ok is False
    assert result.data["approval_required"] is True
    assert result.data["change_files"] == ["src/example.py"]


def test_model_response_must_be_strict_json_with_text_fields(
    isolated_home: ElyndraPaths,
) -> None:
    root, _ = _project()
    app, _ = _app_with_engine(
        isolated_home,
        [
            'Aquí está: {"summary":"x","files":[]}',
            json.dumps(
                {
                    "summary": "x",
                    "files": [{"path": "src/example.py", "content": None}],
                }
            ),
        ],
    )

    wrapped = app.propose_change(
        project_root=str(root),
        requested_files=["src/example.py"],
        instruction="Cambia VALUE.",
    )
    wrong_type = app.propose_change(
        project_root=str(root),
        requested_files=["src/example.py"],
        instruction="Cambia VALUE.",
    )

    assert wrapped.ok is False
    assert "JSON estricto" in wrapped.message
    assert wrong_type.ok is False
    assert "path y content como texto" in wrong_type.message


def test_web_change_requires_exact_single_use_approval(
    isolated_home: ElyndraPaths,
) -> None:
    _, source = _project()
    app, _ = _app_with_engine(isolated_home, [_reply()])
    service = ElyndraWebService(app)
    chat_id = service.create_chat(title="Cambios")['chat']['id']
    prompt = f"Corrige {source} para cambiar VALUE a 2."

    pending = service.send_message(chat_id, prompt)

    assert pending["ok"] is False
    assert pending["meta"]["approval_required"] is True
    assert pending["meta"]["skill_name"] == "assistant.change_proposal.apply"
    assert pending["meta"]["change_proposal_id"]
    assert "-VALUE = 1" in pending["meta"]["change_diff"]
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert service.chat_detail(chat_id)["chat"]["turn_count"] == 0

    applied = service.send_message(
        chat_id,
        prompt,
        approval_token=pending["meta"]["approval_token"],
    )

    assert applied["ok"] is True
    assert applied["meta"]["status"] == "applied"
    assert source.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert service.chat_detail(chat_id)["chat"]["turn_count"] == 1
    with pytest.raises(ValueError, match="utilizada"):
        service.send_message(
            chat_id,
            prompt,
            approval_token=pending["meta"]["approval_token"],
        )


def test_cancelled_web_change_does_not_write(
    isolated_home: ElyndraPaths,
) -> None:
    _, source = _project()
    app, _ = _app_with_engine(isolated_home, [_reply()])
    service = ElyndraWebService(app)
    chat_id = service.create_chat(title="Cancelar cambio")['chat']['id']
    prompt = f"Modifica {source} y cambia VALUE a 2."

    pending = service.send_message(chat_id, prompt)
    token = pending["meta"]["approval_token"]

    assert service.cancel_skill_approval(chat_id, token) is True
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert app.change_proposals.get(pending["meta"]["change_proposal_id"])[
        "status"
    ] == "rejected"


def test_schema_advances_to_26_for_change_proposals(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='assistant_change_proposals'"
        ).fetchone()
    assert schema == "50"
    assert table is not None


def test_assistant_cli_exposes_change_commands() -> None:
    from elyndra.cli import build_parser

    parser = build_parser()
    planned = parser.parse_args(
        [
            "assistant",
            "change-plan",
            "/tmp/project",
            "--file",
            "src/app.py",
            "--instruction",
            "Corrige el error",
        ]
    )
    applied = parser.parse_args(
        ["assistant", "change-apply", "change_123", "--approve"]
    )
    listed = parser.parse_args(["assistant", "changes", "--limit", "5"])

    assert planned.assistant_command == "change-plan"
    assert planned.change_files == ["src/app.py"]
    assert applied.assistant_command == "change-apply"
    assert applied.approve is True
    assert listed.limit == 5


def test_control_center_exposes_change_proposals(
    isolated_home: ElyndraPaths,
) -> None:
    root, _ = _project()
    app, _ = _app_with_engine(isolated_home, [_reply()])
    service = ElyndraWebService(app)
    result = app.propose_change(
        project_root=str(root),
        requested_files=["src/example.py"],
        instruction="Cambia VALUE a 2.",
    )
    assert result.ok is True

    overview = service.control_overview()
    items = service.control_change_proposals(limit=10)
    static_root = Path(__file__).parents[1] / "src" / "elyndra" / "web" / "static"
    script = (static_root / "app.js").read_text(encoding="utf-8")
    html = (static_root / "index.html").read_text(encoding="utf-8")

    assert overview["assistant_change_proposals"] == 1
    assert overview["assistant_pending_changes"] == 1
    assert len(items) == 1
    assert "/api/control/change-proposals" in script
    assert 'id="control-change-proposals"' in html
