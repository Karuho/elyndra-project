from __future__ import annotations

from pathlib import Path

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths


def test_alexandria_import_search_review_and_disable(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    library = app.alexandria.create_library(
        "PHP",
        description="Referencia local de PHP",
        domain="programming",
        language="es",
        version="8.3",
        license_id="documentation-review-required",
    )
    source_path = Path.home() / "Proyectos" / "php-reference.md"
    source_path.write_text(
        "# PDO\n\nPDO permite consultas preparadas y transacciones locales.\n",
        encoding="utf-8",
    )

    source = app.alexandria.import_file(library["public_id"], source_path)
    results = app.alexandria.search("consultas preparadas", library="php")

    assert source["unit_count"] >= 1
    assert source["reviewed_units"] == 0
    assert results
    assert results[0]["library_name"] == "PHP"
    assert results[0]["review_status"] == "unreviewed"

    reviewed = app.alexandria.review_source(int(source["id"]))
    assert reviewed["reviewed_units"] == reviewed["unit_count"]
    assert app.alexandria.search("transacciones", reviewed_only=True)

    app.alexandria.update_library(library["public_id"], enabled=False)
    assert app.alexandria.search("PDO") == []


def test_alexandria_does_not_duplicate_same_source(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    library = app.alexandria.create_library("Oracle")
    source_path = Path.home() / "Proyectos" / "oracle.txt"
    source_path.write_text("SGA y PGA son áreas de memoria de Oracle.", encoding="utf-8")

    first = app.alexandria.import_file(library["public_id"], source_path)
    second = app.alexandria.import_file(library["public_id"], source_path)

    assert first["import_status"] == "imported"
    assert second["import_status"] == "unchanged"
    assert len(app.alexandria.list_sources(library["public_id"])) == 1


def test_alexandria_schema_and_private_directory(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.alexandria.create_library("Linux")

    with app.database.connect() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]

    assert version == "50"
    assert isolated_home.alexandria_dir.is_dir()


def test_alexandria_overview_has_named_counts_and_delete_is_permanent(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    library = app.alexandria.create_library("Temporal")
    source_path = Path.home() / "Proyectos" / "temporal.md"
    source_path.write_text("# Temporal\n\nContenido de prueba.", encoding="utf-8")
    app.alexandria.import_file(library["public_id"], source_path)

    overview = app.alexandria.overview()
    deleted = app.alexandria.delete_library(library["public_id"])

    assert overview["counts"]["libraries"] == 1
    assert overview["counts"]["sources"] == 1
    assert deleted["removed_sources"] == 1
    assert app.alexandria.get_library(library["public_id"]) is None
    assert not (isolated_home.alexandria_dir / library["public_id"]).exists()


def test_alexandria_reindex_uses_markdown_sections_and_preserves_review(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    library = app.alexandria.create_library(
        "PHP Security",
        domain="programming/php/security",
    )
    source_path = Path.home() / "Proyectos" / "security-sections.md"
    source_path.write_text(
        "# PHP Security\n\n"
        "## Webhooks\n\n"
        "Un webhook debe validar firma e idempotencia.\n\n"
        "## Archivos subidos\n\n"
        "Los archivos deben guardarse fuera del web root.\n",
        encoding="utf-8",
    )
    source = app.alexandria.import_file(library["public_id"], source_path)
    app.alexandria.review_source(int(source["id"]))

    with app.database.connect() as connection:
        connection.execute(
            "UPDATE schema_meta SET value = '1' "
            "WHERE key = 'alexandria_index_version'"
        )

    reloaded = ElyndraApplication.load(isolated_home)
    details = reloaded.alexandria.list_sources(library["public_id"])[0]
    results = reloaded.alexandria.search(
        "webhook firma idempotencia",
        domain_prefixes=("programming/php/security",),
    )

    assert reloaded.alexandria.last_reindex_status["status"] == "reindexed"
    assert reloaded.alexandria.overview()["index_version"] == "2"
    assert details["reviewed_units"] == details["unit_count"]
    assert results
    assert results[0]["heading"] == "Webhooks"
