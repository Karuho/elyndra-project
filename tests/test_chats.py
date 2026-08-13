from __future__ import annotations

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths


def test_summary_only_chat_persists_compact_digest(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    chat = app.chats.create(title="Prueba", transcript_mode="summary")

    updated = app.chats.append_turn(
        chat["public_id"],
        user_text="Estamos probando memoria persistente.",
        assistant_text="El resumen queda en SQLite.",
    )

    assert updated["turn_count"] == 1
    assert "memoria persistente" in updated["summary"]
    assert app.chats.recent_turns(chat["public_id"]) == []
    assert app.chats.search("memoria persistente")[0]["public_id"] == chat["public_id"]


def test_full_chat_stores_turns_on_disk(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    chat = app.chats.create(title="Completo", transcript_mode="full")

    app.chats.append_turn(
        chat["public_id"],
        user_text="Primera pregunta",
        assistant_text="Primera respuesta",
    )

    turns = app.chats.recent_turns(chat["public_id"])
    assert len(turns) == 1
    assert turns[0]["user_text"] == "Primera pregunta"
    assert turns[0]["assistant_text"] == "Primera respuesta"


def test_chat_can_be_reopened_renamed_and_forgotten(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    chat = app.chats.create()

    renamed = app.chats.rename(chat["public_id"], "Trabajo Elyndra")
    reopened = app.chats.touch(chat["public_id"])

    assert renamed["title"] == "Trabajo Elyndra"
    assert reopened["public_id"] == chat["public_id"]
    assert app.chats.forget(chat["public_id"]) is True
    assert app.chats.get(chat["public_id"]) is None
