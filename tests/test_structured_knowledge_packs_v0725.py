from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from elyndra.application import ElyndraApplication
from elyndra.cli import build_parser
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService


def _write_pack(
    root: Path,
    *,
    package_id: str,
    adapter: str,
    language: str,
    records: list[dict[str, object]],
    version: str = "1.0.0",
    target_language: str = "",
    locale: str = "",
    dialect: str = "",
    review_status: str = "reviewed",
    source_attribution: str = "Test fixture source",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    source = root / "records.jsonl"
    source.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    content_type = "first_aid" if adapter == "first_aid.topic" else "language"
    manifest: dict[str, object] = {
        "schema_version": 2,
        "package_id": package_id,
        "name": f"Fixture {package_id}",
        "version": version,
        "content_type": content_type,
        "adapter": adapter,
        "language": language,
        "license_id": "CC-BY-4.0",
        "publisher": "Elyndra tests",
        "description": "Paquete estructurado de prueba.",
        "review": {
            "status": review_status,
            "reviewed_on": "2026-07-31" if review_status == "reviewed" else "",
            "reviewer": "Test reviewer" if review_status == "reviewed" else "",
        },
        "limitations": ["Cobertura limitada de prueba."],
        "attribution": ["Fixture local para pruebas."],
        "sources": [
            {
                "path": source.name,
                "title": "Registros estructurados",
                "format": "jsonl",
                "sha256": source_sha256,
                "source_url": "https://example.invalid/source",
                "attribution": source_attribution,
            }
        ],
    }
    if target_language:
        manifest["target_language"] = target_language
    if locale:
        manifest["locale"] = locale
    if dialect:
        manifest["dialect"] = dialect
    (root / "elyndra-structured-package.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


def _dialect_record(*, definition: str = "En Chile, pareja romántica.") -> dict[str, object]:
    return {
        "id": "pololo.n.1",
        "language": "es",
        "lemma": "pololo",
        "pos": "noun",
        "definition": definition,
        "forms": ["polola"],
        "translations": {"en": ["boyfriend", "girlfriend", "partner"]},
        "dialects": {"es-CL": {"forms": ["pololear", "pololito"]}},
        "examples": ["Mi pololo vive en Santiago."],
        "source_ref": "fixture:1",
    }


def _first_aid_record() -> dict[str, object]:
    return {
        "id": "nosebleed",
        "language": "es",
        "locale": "es-CL",
        "title": "Sangrado nasal",
        "summary": "Inclina la cabeza hacia adelante y comprime la nariz.",
        "urgency": "urgent",
        "aliases": ["sangrado nasal", "me sangra la nariz"],
        "steps": [
            "Siéntate e inclina la cabeza hacia adelante.",
            "Aprieta la parte blanda de la nariz de 10 a 15 minutos sin soltar.",
        ],
        "avoid": ["No inclines la cabeza hacia atrás."],
        "red_flags": ["El sangrado no cede después de 20 minutos."],
        "source_refs": ["fixture-medical-source"],
        "reviewed_on": "2026-07-31",
    }


def test_language_dialect_pack_install_lookup_replace_and_provenance(
    isolated_home: ElyndraPaths,
    tmp_path: Path,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    v1 = _write_pack(
        tmp_path / "dialect-v1",
        package_id="language.es-cl.demo",
        adapter="language.dialect",
        language="es",
        locale="es-CL",
        dialect="es-CL",
        records=[_dialect_record()],
    )

    inspected = app.structured_packs.inspect(v1)
    assert inspected["entry_count"] == 1
    assert inspected["network_used"] is False
    assert inspected["execution_performed"] is False
    assert inspected["installation_requires_approval"] is True

    installed = app.structured_packs.install(v1, actor="test-owner")
    old_storage = Path(str(installed["storage_path"]))
    sources = app.structured_packs.sources("language.es-cl.demo")
    assert installed["install_status"] == "installed"
    assert old_storage.is_dir()
    assert sources[0]["sha256"] == inspected["resolved_sources"][0]["sha256"]
    assert sources[0]["attribution"] == "Test fixture source"

    matches = app.dictionary.lookup(
        "pololear",
        language="es",
        output_language="en",
        dialect="es-CL",
    )
    assert matches
    assert matches[0].package_id == "language.es-cl.demo"
    assert matches[0].review_status == "reviewed"
    assert matches[0].translations["en"][0] == "boyfriend"
    assert matches[0].source_relative_path == "records.jsonl"
    assert matches[0].source_sha256 == sources[0]["sha256"]
    assert matches[0].source_attribution == "Test fixture source"

    skill_result = app.execute_skill(
        "dictionary.lookup",
        {
            "term": "pololito",
            "language": "es",
            "output_language": "en",
        },
    )
    assert skill_result.ok is True
    assert skill_result.data["matches"][0]["package_id"] == "language.es-cl.demo"

    app.structured_packs.set_enabled("language.es-cl.demo", enabled=False)
    assert app.dictionary.lookup("pololear", language="es", dialect="es-CL") == []
    app.structured_packs.set_enabled("language.es-cl.demo", enabled=True)

    v2 = _write_pack(
        tmp_path / "dialect-v2",
        package_id="language.es-cl.demo",
        adapter="language.dialect",
        language="es",
        locale="es-CL",
        dialect="es-CL",
        version="2.0.0",
        records=[_dialect_record(definition="En Chile, novio, novia o pareja.")],
    )
    with pytest.raises(ValueError, match="--replace"):
        app.structured_packs.install(v2, actor="test-owner")
    replaced = app.structured_packs.install(v2, actor="test-owner", replace=True)
    assert replaced["install_status"] == "replaced"
    assert replaced["version"] == "2.0.0"
    assert not old_storage.exists()
    assert "novio, novia" in app.dictionary.lookup("pololo", language="es")[0].gloss

    removed = app.structured_packs.remove("language.es-cl.demo")
    assert removed["removed"] is True
    assert app.dictionary.lookup("pololear", language="es", dialect="es-CL") == []


@pytest.mark.parametrize(
    ("adapter", "target_language", "record"),
    [
        (
            "dictionary.monolingual",
            "",
            {
                "id": "agua.n.1",
                "lemma": "agua",
                "definition": "Líquido esencial para la vida.",
                "pos": "noun",
            },
        ),
        (
            "dictionary.bilingual",
            "en",
            {
                "id": "agua.en.1",
                "lemma": "agua",
                "translations": {"en": ["water"]},
                "pos": "noun",
            },
        ),
        (
            "language.morphology",
            "",
            {
                "id": "hablar.v.1",
                "lemma": "hablar",
                "morphology": {
                    "forms": [
                        {"form": "hablo", "type": "present-1s"},
                        {"form": "hablamos", "type": "present-1p"},
                    ]
                },
                "pos": "verb",
            },
        ),
    ],
)
def test_supported_language_adapters_are_validated(
    isolated_home: ElyndraPaths,
    tmp_path: Path,
    adapter: str,
    target_language: str,
    record: dict[str, object],
) -> None:
    app = ElyndraApplication.load(isolated_home)
    root = _write_pack(
        tmp_path / adapter.replace(".", "-"),
        package_id=f"fixture.{adapter.replace('.', '-')}",
        adapter=adapter,
        language="es",
        target_language=target_language,
        records=[record],
    )

    inspected = app.structured_packs.inspect(root)

    assert inspected["adapter"] == adapter
    assert inspected["entry_count"] == 1


def test_reviewed_first_aid_pack_integrates_skill_web_and_locale(
    isolated_home: ElyndraPaths,
    tmp_path: Path,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    unreviewed = _write_pack(
        tmp_path / "aid-unreviewed",
        package_id="first-aid.es-cl.unreviewed",
        adapter="first_aid.topic",
        language="es",
        locale="es-CL",
        review_status="unreviewed",
        records=[_first_aid_record()],
    )
    with pytest.raises(ValueError, match="reviewed"):
        app.structured_packs.inspect(unreviewed)

    reviewed = _write_pack(
        tmp_path / "aid-reviewed",
        package_id="first-aid.es-cl.nosebleed",
        adapter="first_aid.topic",
        language="es",
        locale="es-CL",
        records=[_first_aid_record()],
    )
    installed = app.structured_packs.install(reviewed, actor="test-owner")
    assert installed["card_count"] == 1
    assert installed["review_status"] == "reviewed"

    topic = app.first_aid.lookup(
        "me sangra la nariz",
        language="es",
        locale="es-CL",
    )
    assert topic is not None
    assert topic.source_package == "first-aid.es-cl.nosebleed"
    assert topic.urgency == "urgent"
    rendered, data = app.first_aid.render_topic(topic, language="es")
    assert rendered.startswith("PRIMEROS AUXILIOS URGENTES")
    assert "Paquete revisado: first-aid.es-cl.nosebleed" in rendered
    assert data["topic"]["source_refs"] == ["fixture-medical-source"]
    assert data["topic"]["source_relative_path"] == "records.jsonl"
    assert data["topic"]["source_sha256"]
    assert data["topic"]["source_attribution"] == "Test fixture source"

    skill_result = app.execute_skill(
        "first_aid.lookup",
        {
            "query": "sangrado nasal",
            "language": "es",
            "locale": "es-CL",
        },
    )
    assert skill_result.ok is True
    assert skill_result.data["topic"]["source_package"] == (
        "first-aid.es-cl.nosebleed"
    )

    service = ElyndraWebService(app)
    control = service.control_structured_packs()
    web = service.first_aid_lookup(
        "sangrado nasal",
        language="es",
        locale="es-CL",
    )
    assert control["status"]["first_aid_pack_count"] == 1
    assert web["topic"]["locale"] == "es-CL"

    app.structured_packs.set_enabled("first-aid.es-cl.nosebleed", enabled=False)
    assert (
        app.first_aid.lookup("me sangra la nariz", language="es", locale="es-CL")
        is None
    )


def test_structured_pack_rejects_bad_hash_symlinks_duplicates_and_missing_attribution(
    isolated_home: ElyndraPaths,
    tmp_path: Path,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    root = _write_pack(
        tmp_path / "invalid",
        package_id="language.invalid.demo",
        adapter="dictionary.monolingual",
        language="es",
        records=[
            {
                "id": "uno.n.1",
                "lemma": "uno",
                "definition": "Número uno.",
            }
        ],
    )
    manifest_path = root / "elyndra-structured-package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sources"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        app.structured_packs.inspect(root)

    duplicate = _write_pack(
        tmp_path / "duplicate",
        package_id="language.duplicate.demo",
        adapter="dictionary.monolingual",
        language="es",
        records=[
            {"id": "dup", "lemma": "uno", "definition": "Uno."},
            {"id": "dup", "lemma": "dos", "definition": "Dos."},
        ],
    )
    with pytest.raises(ValueError, match="duplicado"):
        app.structured_packs.inspect(duplicate)

    no_attribution = _write_pack(
        tmp_path / "no-attribution",
        package_id="language.no-attribution.demo",
        adapter="dictionary.monolingual",
        language="es",
        records=[{"id": "tres", "lemma": "tres", "definition": "Tres."}],
        source_attribution="",
    )
    with pytest.raises(ValueError, match="attribution"):
        app.structured_packs.inspect(no_attribution)

    link = tmp_path / "package-link"
    link.symlink_to(duplicate, target_is_directory=True)
    with pytest.raises(ValueError, match="enlace simbólico"):
        app.structured_packs.inspect(link)

    invalid_version = _write_pack(
        tmp_path / "invalid-version",
        package_id="language.invalid-version.demo",
        adapter="dictionary.monolingual",
        language="es",
        version="../../escape",
        records=[{"id": "cuatro", "lemma": "cuatro", "definition": "Cuatro."}],
    )
    with pytest.raises(ValueError, match="version"):
        app.structured_packs.inspect(invalid_version)

    source_link = _write_pack(
        tmp_path / "source-link",
        package_id="language.source-link.demo",
        adapter="dictionary.monolingual",
        language="es",
        records=[{"id": "cinco", "lemma": "cinco", "definition": "Cinco."}],
    )
    original_source = source_link / "records.jsonl"
    external_source = tmp_path / "external-records.jsonl"
    external_source.write_bytes(original_source.read_bytes())
    original_source.unlink()
    original_source.symlink_to(external_source)
    with pytest.raises(ValueError, match="enlaces simbólicos"):
        app.structured_packs.inspect(source_link)

    traversal = _write_pack(
        tmp_path / "traversal",
        package_id="language.traversal.demo",
        adapter="dictionary.monolingual",
        language="es",
        records=[{"id": "seis", "lemma": "seis", "definition": "Seis."}],
    )
    traversal_manifest = traversal / "elyndra-structured-package.json"
    traversal_payload = json.loads(traversal_manifest.read_text(encoding="utf-8"))
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"id":"x","lemma":"x","definition":"x"}\n', encoding="utf-8")
    traversal_payload["sources"][0]["path"] = "../outside.jsonl"
    traversal_payload["sources"][0]["sha256"] = hashlib.sha256(
        outside.read_bytes()
    ).hexdigest()
    traversal_manifest.write_text(json.dumps(traversal_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="no pueden contener"):
        app.structured_packs.inspect(traversal)


def test_schema_33_and_cli_expose_structured_packs(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    with app.database.connect() as connection:
        version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    parser = build_parser()
    args = parser.parse_args(
        [
            "alexandria",
            "structured-install",
            "/tmp/fixture",
            "--replace",
            "--approve",
        ]
    )
    show_args = parser.parse_args(
        ["alexandria", "structured-show", "language.example"]
    )
    status = app.structured_packs.status()

    assert version == "50"
    assert {
        "alexandria_structured_packs",
        "alexandria_structured_sources",
        "alexandria_lexical_entries",
        "alexandria_lexical_forms",
        "alexandria_first_aid_cards",
        "alexandria_first_aid_aliases",
    } <= tables
    assert args.alexandria_command == "structured-install"
    assert args.replace is True
    assert show_args.alexandria_command == "structured-show"
    assert show_args.package_id == "language.example"
    assert status["disk_backed"] is True
    assert status["full_database_loaded_in_ram"] is False
    assert status["automatic_download"] is False
    assert status["installation_requires_approval"] is True
    assert len(app.skills.list_all()) == 102
