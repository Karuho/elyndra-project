from __future__ import annotations

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths
from elyndra.retrieval import retrieval_queries


def test_retrieval_queries_remove_common_question_words() -> None:
    queries = retrieval_queries("¿Podrías explicar por qué Elyndra protege la privacidad local?")

    assert queries[0].startswith("¿Podrías")
    assert "Elyndra" in queries
    assert "privacidad" in queries
    assert "por" not in queries


def test_application_retrieves_relevant_memory_from_noisy_question(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.memories.add(
        "Elyndra debe funcionar en equipos modestos",
        kind="principle",
    )

    result = app.ask(
        "¿Podrías explicar por qué los equipos modestos son relevantes para el proyecto?"
    )

    assert result.ok is True
    assert "equipos modestos" in result.message
