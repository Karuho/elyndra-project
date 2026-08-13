from __future__ import annotations

import gzip
import json
from pathlib import Path

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths


def test_chat_turns_build_structured_summary_and_episodes(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    chat = app.chats.create(title="Memoria", transcript_mode="summary")

    app.record_chat_turn(
        chat["public_id"],
        user_text="Estamos trabajando en la memoria persistente de Elyndra.",
        assistant_text="Entendido.",
    )
    app.record_chat_turn(
        chat["public_id"],
        user_text="Decidimos que los resúmenes vivan en SQLite.",
        assistant_text="Queda registrado.",
    )
    app.record_chat_turn(
        chat["public_id"],
        user_text="Falta implementar el inspector HTML.",
        assistant_text="Será el siguiente paso.",
    )

    summary = app.chat_summary(chat["public_id"])
    state = app.memory_lifecycle.summary_data(chat["public_id"])
    episodes = app.memory_lifecycle.list_episodes(chat=chat["public_id"])

    assert "Temas:" in summary
    assert "Decisiones:" in summary
    assert "Pendientes:" in summary
    assert any("SQLite" in item for item in state["decisions"])
    assert any(item["kind"] == "decision" for item in episodes)
    assert any(item["kind"] == "pending" for item in episodes)
    assert app.chats.recent_turns(chat["public_id"]) == []


def test_preference_proposal_requires_owner_approval(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    chat = app.chats.create(title="Preferencias")

    result = app.record_chat_turn(
        chat["public_id"],
        user_text="Prefiero usar VS Code para modificar código.",
        assistant_text="Lo tendré presente cuando corresponda.",
    )

    proposals = app.memory_lifecycle.list_proposals()
    assert result["proposals_created"]
    assert len(proposals) == 1
    assert app.memories.search("VS Code") == []

    approved = app.memory_lifecycle.approve_proposal(int(proposals[0]["id"]))
    memories = app.memories.search("VS Code")

    assert approved["status"] == "approved"
    assert approved["memory_id"] is not None
    assert memories[0]["source"] == "reviewed-chat-proposal"


def test_cold_archive_is_compressed_and_can_prune_full_turns(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    chat = app.chats.create(title="Archivo frío", transcript_mode="full")
    app.record_chat_turn(
        chat["public_id"],
        user_text="Decidimos conservar un resumen pequeño.",
        assistant_text="La transcripción completa será opcional.",
    )

    archive = app.memory_lifecycle.archive_chat(
        chat["public_id"],
        transcripts_dir=app.paths.transcripts_dir,
        prune=True,
    )
    path = Path(str(archive["path"]))

    assert path.exists()
    assert path.suffix == ".gz"
    assert path.stat().st_size == archive["size_bytes"]
    assert app.chats.recent_turns(chat["public_id"]) == []
    assert "Decisiones:" in app.chat_summary(chat["public_id"])

    with gzip.open(path, "rt", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    assert records[0]["type"] == "elyndra-chat-archive"
    assert records[1]["type"] == "turn"


def test_episode_retrieval_uses_disk_without_loading_every_chat(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    chat = app.chats.create(title="Arquitectura")
    app.record_chat_turn(
        chat["public_id"],
        user_text="Decidimos guardar los resúmenes en SQLite y no en RAM.",
        assistant_text="Decisión registrada.",
    )

    memories, episodes, documents = app._retrieve_context_bundle(
        "¿Qué decidimos sobre SQLite?",
        chat_id=chat["public_id"],
    )

    assert memories == []
    assert documents == []
    assert len(episodes) == 1
    assert "SQLite" in episodes[0]["content"]


def test_episode_can_be_corrected_and_removed(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    chat = app.chats.create(title="Corrección episódica")
    app.record_chat_turn(
        chat["public_id"],
        user_text="Decidimos guardar todo en RAM.",
        assistant_text="Decisión registrada.",
    )
    episode = app.memory_lifecycle.list_episodes(chat=chat["public_id"])[0]

    updated = app.memory_lifecycle.edit_episode(
        int(episode["id"]),
        content="Decidimos guardar los resúmenes en SQLite.",
    )
    summary = app.chat_summary(chat["public_id"])

    assert "SQLite" in updated["content"]
    assert "SQLite" in summary
    state = app.memory_lifecycle.summary_data(chat["public_id"])
    assert all("todo en RAM" not in item for item in state["decisions"])
    assert app.memory_lifecycle.forget_episode(int(episode["id"])) is True
    assert "Decisiones:" not in app.chat_summary(chat["public_id"])


def test_owner_correction_is_stored_as_learning_record(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    chat = app.chats.create(title="Correcciones")
    app.record_chat_turn(
        chat["public_id"],
        user_text="¿Dónde vive la memoria semántica?",
        assistant_text="Siempre permanece en RAM.",
    )

    correction_id = app.memory_lifecycle.add_correction(
        chat["public_id"],
        user_text="¿Dónde vive la memoria semántica?",
        original_response="Siempre permanece en RAM.",
        corrected_response="Vive en SQLite y solo se recupera cuando es relevante.",
    )
    corrections = app.memory_lifecycle.list_corrections(chat=chat["public_id"])

    assert correction_id == corrections[0]["id"]
    assert "SQLite" in corrections[0]["corrected_response"]
    assert "Corrección del propietario" in app.chat_summary(chat["public_id"])


def test_routine_becomes_reviewable_proposal(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    chat = app.chats.create(title="Rutinas")

    app.record_chat_turn(
        chat["public_id"],
        user_text="Cada mañana reviso mis proyectos antes de comenzar.",
        assistant_text="Entendido.",
    )

    proposals = app.memory_lifecycle.list_proposals()
    assert proposals[0]["kind"] == "routine"
    assert app.memories.list_active() == []
