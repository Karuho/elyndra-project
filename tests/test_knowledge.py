from __future__ import annotations

from pathlib import Path

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths


def test_import_search_reimport_and_forget(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    document = Path.home() / "Proyectos" / "manual.md"
    document.write_text(
        "# Privacidad\n\nElyndra funciona offline y no utiliza telemetría.",
        encoding="utf-8",
    )

    imported = app.knowledge.import_file(document, project="elyndra")
    assert imported["status"] == "imported"
    assert imported["chunks"] >= 1

    unchanged = app.knowledge.import_file(document, project="elyndra")
    assert unchanged["status"] == "unchanged"

    reindexed = app.knowledge.import_file(document, project="elyndra", force=True)
    assert reindexed["status"] == "updated"

    results = app.knowledge.search("telemetría")
    assert results
    assert results[0]["document_id"] == imported["document_id"]
    assert "telemetría" in results[0]["content"]
    assert "telemetría" in results[0]["excerpt"]

    document.write_text(
        "# Privacidad\n\nElyndra funciona completamente offline y bloquea la telemetría.",
        encoding="utf-8",
    )
    updated = app.knowledge.import_file(document, project="elyndra")
    assert updated["status"] == "updated"

    assert app.knowledge.forget(imported["document_id"]) is True
    assert app.knowledge.search("telemetría") == []


def test_knowledge_import_requires_allowed_extension(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    binary = Path.home() / "Proyectos" / "image.bin"
    binary.write_bytes(b"\x00\x01\x02")

    try:
        app.knowledge.import_file(binary)
    except ValueError as exc:
        assert "Extensión no permitida" in str(exc)
    else:
        raise AssertionError("La importación binaria debió fallar")
