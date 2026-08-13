from __future__ import annotations

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths


class _ExplodingEngine:
    name = "test-exploding"

    def reply(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("El modelo no debe cargarse para recapitular el chat.")

    def release(self) -> None:
        return


def test_session_recap_uses_persisted_summary_without_model(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = _ExplodingEngine()  # type: ignore[assignment]
    summary = (
        "1. Usuario: Probamos chats. | Asistente: Funcionaron.\n"
        "2. Usuario: Activamos memoria. | Asistente: Quedó en SQLite."
    )

    result = app.ask("¿En qué quedamos?", session_summary=summary)

    assert result.ok is True
    assert result.data["fast_path"] == "session_recap"
    assert "Quedamos en esto" in result.message
    assert "SQLite" in result.message
