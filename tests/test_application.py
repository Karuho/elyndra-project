from __future__ import annotations

from pathlib import Path

from elyndra.application import ElyndraApplication
from elyndra.engines import ConversationTurn
from elyndra.paths import ElyndraPaths


def test_application_uses_no_model_engine(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    result = app.ask("Escribe algo no soportado")
    assert result.ok is True
    assert result.data["engine"] == "no-model"
    assert result.data["generated"] is False


def test_remember_through_assistant(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    result = app.ask("Recuerda que Elyndra es local")
    assert result.ok is True
    assert app.memories.search("Elyndra local")


def test_local_search_combines_memory_and_documents(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.memories.add("La privacidad es una prioridad", kind="preference")
    document = Path.home() / "Proyectos" / "privacy.md"
    document.write_text("Elyndra no envía telemetría.", encoding="utf-8")
    app.knowledge.import_file(document)

    result = app.ask("¿Qué sabes de privacidad?")
    assert result.ok is True
    assert "Memoria personal" in result.message

    telemetry = app.ask("¿Qué sabes de telemetría?")
    assert "Conocimiento importado" in telemetry.message


def test_local_search_supports_about_phrase(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.memories.add("Elyndra debe funcionar en equipos modestos", kind="principle")

    result = app.ask("¿Qué sabes sobre equipos modestos?")

    assert result.ok is True
    assert "equipos modestos" in result.message


def test_application_bounds_conversation_history(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    history = tuple(
        ConversationTurn(user=f"pregunta {index}", assistant=f"respuesta {index}")
        for index in range(10)
    )

    bounded = app._bounded_history(history)

    assert len(bounded) == 6
    assert bounded[0].user == "pregunta 4"
    assert bounded[-1].assistant == "respuesta 9"


def test_application_limits_retrieved_context(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    for index in range(6):
        app.memories.add(f"privacidad local regla {index}", kind="fact")

    memories, documents = app._retrieve_local_context("privacidad local")
    context = app._language_context(memories, documents)

    assert len(memories) <= 3
    assert len(documents) <= 2
    assert sum(len(item) for item in context) <= 3700


class _ExplodingEngine:
    name = "test-exploding"

    def reply(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("El motor no debe cargarse para respuestas canónicas.")

    def release(self) -> None:
        return


def test_canonical_identity_bypasses_language_model(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = _ExplodingEngine()  # type: ignore[assignment]

    result = app.ask("¿Qué es Elyndra?")

    assert result.ok is True
    assert result.data["generated"] is False
    assert result.data["fast_path"] == "identity"
    assert "marco local-first" in result.message


def test_canonical_requirements_are_precise(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = _ExplodingEngine()  # type: ignore[assignment]

    result = app.ask("¿Qué requisitos necesita un PC para correr Elyndra?")

    assert result.ok is True
    assert result.data["fast_path"] == "requirements"
    assert "Python 3.11" in result.message
    assert "Node.js" in result.message
    assert "opcionales" in result.message


def test_canonical_programming_capability_is_truthful(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = _ExplodingEngine()  # type: ignore[assignment]

    result = app.ask("¿Sabes programar?")

    assert result.ok is True
    assert result.data["fast_path"] == "programming_capability"
    assert "analizar" in result.message
    assert "validar código" in result.message
