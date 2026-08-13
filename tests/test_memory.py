from __future__ import annotations

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths


def test_memory_add_search_and_forget(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    memory_id = app.memories.add(
        "Carlos prefiere rutas exactas",
        kind="preference",
        project="elyndra",
    )

    results = app.memories.search("rutas exactas")
    assert results
    assert results[0]["id"] == memory_id
    assert results[0]["content"] == "Carlos prefiere rutas exactas"

    assert app.memories.forget(memory_id) is True
    assert app.memories.search("rutas exactas") == []
