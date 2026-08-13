from __future__ import annotations

from elyndra.application import ElyndraApplication
from elyndra.engines import ConversationTurn, LanguageReply
from elyndra.paths import ElyndraPaths
from elyndra.retrieval import select_relevant_history, should_retrieve_context


class _CapturingEngine:
    name = "test-capturing"

    def __init__(self) -> None:
        self.context: tuple[str, ...] = ()
        self.history: tuple[ConversationTurn, ...] = ()

    def reply(
        self,
        prompt: str,
        *,
        context: tuple[str, ...] = (),
        history: tuple[ConversationTurn, ...] = (),
        response_language: str | None = None,
        keep_alive_seconds: int = 0,
    ) -> LanguageReply:
        del prompt, response_language, keep_alive_seconds
        self.context = context
        self.history = history
        return LanguageReply(
            text="Respuesta de prueba.",
            engine=self.name,
            generated=True,
            metadata={},
        )

    def release(self) -> None:
        return


def test_general_chitchat_does_not_retrieve_personal_context(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.memories.add("Ricardo Arjona aparece en una nota privada", kind="fact")
    engine = _CapturingEngine()
    app.language_engine = engine

    result = app.ask("¿Qué piensas de Ricardo Arjona?")

    assert result.ok is True
    assert result.data["retrieval_queries"] == ()
    assert all("MEMORIA" not in block for block in engine.context)


def test_persisted_summary_is_loaded_only_when_related(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = _CapturingEngine()
    app.language_engine = engine
    summary = "Decidimos implementar contenedores de chat y memoria en SQLite."

    app.ask("¿Qué piensas de una canción?", session_summary=summary)
    assert all("RESUMEN PERSISTENTE" not in block for block in engine.context)

    app.ask("¿Qué decidimos sobre los chats?", session_summary=summary)
    assert any("RESUMEN PERSISTENTE" in block for block in engine.context)


def test_history_selection_discards_unrelated_old_topics() -> None:
    history = (
        ConversationTurn("¿Qué piensas de Arjona?", "Tiene una obra divisiva."),
        ConversationTurn("¿Cómo funciona SQLite?", "Trabaja sobre una base local."),
    )

    selected = select_relevant_history("Explícame SQLite", history)

    assert len(selected) == 1
    assert "SQLite" in selected[0].user


def test_context_retrieval_gate() -> None:
    assert should_retrieve_context("¿Qué recuerdas de nuestro proyecto Elyndra?") is True
    assert should_retrieve_context("¿Qué piensas de Ricardo Arjona?") is False
