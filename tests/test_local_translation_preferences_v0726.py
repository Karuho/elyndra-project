from __future__ import annotations

from datetime import UTC, datetime, timedelta

from elyndra.application import ElyndraApplication
from elyndra.engines import ConversationTurn
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService


class FailingEngine:
    name = "failing-engine"
    supports_vision = False

    def reply(self, *args, **kwargs):
        raise AssertionError("La ruta local no debía invocar el modelo.")

    def release(self) -> None:
        return None


def test_web_chat_first_aid_direct_phrases_and_catalog_are_local(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = FailingEngine()

    breathing = app.ask("me ahogo")
    catalog = app.ask("que primeros auxilio sabes")

    assert breathing.ok is True
    assert breathing.data["engine"] == "local-emergency-first-aid"
    assert breathing.data["fast_path"] == "emergency_first_aid"
    assert "Dificultad grave para respirar" in breathing.message
    assert "altavoz" in breathing.message
    assert catalog.data["fast_path"] == "local_first_aid_catalog"
    assert "Sangrado grave" in catalog.message
    assert "Atragantamiento" in catalog.message

    service = ElyndraWebService(app)
    chat = service.create_chat(title="Rutas rápidas", transcript_mode="full")
    web_breathing = service.send_message(chat["chat"]["id"], "me ahogo")
    web_translation = service.send_message(
        chat["chat"]["id"],
        "como se dice perro en ingles",
    )
    assert web_breathing["meta"]["fast_path"] == "emergency_first_aid"
    assert web_translation["meta"]["fast_path"] == "local_translation"
    assert web_translation["message"] == "En inglés: dog"


def test_translation_fast_paths_cover_words_phrases_templates_and_pronunciation(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = FailingEngine()

    dog = app.ask("como se dice perro en ingles")
    prevention = app.ask("como se dice prevencion en ingles")
    phrase = app.ask("y como digo mi mama me mima en ingles")
    chinese = app.ask("como puedo decir hola me llamo Carlos en chino")
    followup = app.ask(
        "y como puedo pronunciar eso? no se leer chino",
        history=(
            ConversationTurn(
                user="como puedo decir hola me llamo Carlos en chino",
                assistant=chinese.message,
            ),
        ),
    )

    assert dog.message == "En inglés: dog"
    assert prevention.message == "En inglés: prevention"
    assert phrase.message == "En inglés: My mom spoils me."
    assert "你好，我叫Carlos。" in chinese.message
    assert "Nǐ hǎo" in chinese.message
    assert followup.data["fast_path"] == "local_pronunciation_followup"
    assert "Nǐ hǎo" in followup.message
    for result in (dog, prevention, phrase, chinese, followup):
        assert result.data["model_used"] is False


def test_translation_capability_answer_is_accurate_and_local(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = FailingEngine()

    result = app.ask(
        "puedes traducir cualquier palabra usando la libreria en elyndra o dependes del modelo ia?"
    )

    assert result.data["fast_path"] == "translation_capabilities"
    assert "no contiene todas" in result.message
    assert "modelo local" in result.message


def test_preference_learning_requires_review_and_supports_forget(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)

    proposal = app.preferences.propose(
        "Prefiero respuestas directas y con rutas exactas.",
        category="style",
        scope="global",
        expires_days=90,
    )
    assert app.preferences.status()["active_preferences"] == 0
    assert proposal["status"] == "pending"

    edited = app.preferences.edit_proposal(
        int(proposal["id"]),
        content="Prefiero respuestas directas, cálidas y con rutas exactas.",
        category="style",
        scope="global",
        expires_days=120,
    )
    approved = app.preferences.approve(int(edited["id"]))

    assert approved["status"] == "active"
    assert approved["category"] == "style"
    assert app.memories.get(int(approved["memory_id"]))["kind"] == "preference"
    assert app.preferences.status()["active_preferences"] == 1
    context = app.preferences.context_block()
    assert "PREFERENCIAS REVISADAS DEL PROPIETARIO" in context
    assert "respuestas directas, cálidas" in context
    assert "no conceden permisos" in context
    assert app.preferences.forget(str(approved["public_id"])) is True
    assert app.preferences.status()["active_preferences"] == 0


def test_preference_expiration_removes_semantic_memory(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    proposal = app.preferences.propose("Prefiero modo temporal.", expires_days=1)
    approved = app.preferences.approve(int(proposal["id"]))
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with app.database.connect() as connection:
        connection.execute(
            "UPDATE reviewed_preferences SET expires_at = ? WHERE public_id = ?",
            (past, approved["public_id"]),
        )

    assert app.preferences.expire_due() == 1
    item = app.preferences.get(str(approved["public_id"]))
    memory = app.memories.get(int(approved["memory_id"]))
    assert item is not None and item["status"] == "expired"
    assert memory is not None and memory["status"] == "deleted"


def test_web_control_exposes_translation_preferences_and_schema_35(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    translation = service.translation_lookup("perro", target_language="en")
    preferences = service.control_preferences()
    overview = service.control_overview()

    with app.database.connect() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert translation["model_used"] is False
    assert "dog" in translation["message"]
    assert preferences["status"]["silent_learning"] is False
    assert overview["translation"]["complete_translation_engine"] is False
    assert overview["preferences"]["approval_required"] is True
    assert version == "50"
    assert "reviewed_preferences" in tables
