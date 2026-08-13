from __future__ import annotations

import gzip
import hashlib
import json
import socket
import sqlite3
from pathlib import Path

import pytest

from elyndra.application import ElyndraApplication
from elyndra.db import Database
from elyndra.dictionary import DictionaryMatch, render_dictionary_matches
from elyndra.language_packs import LanguagePackBuilder
from elyndra.language_packs.importers import (
    iter_cldr_annotations,
    iter_omw_spanish_tab,
    iter_wiktextract_jsonl,
)
from elyndra.language_packs.registry import LanguagePackRegistry
from elyndra.language_packs.repository import SpanishLexicalService, likely_laughter
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService


def _source(tmp_path: Path) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "synthetic.jsonl"
    records = [
        {"type": "lexeme", "id": "frio", "lemma": "frío", "pos": "adjective",
         "forms": ["fría", "fríos"], "senses": [
             {"id": "frio-temperature", "synset": "cold-temperature",
              "definition": "Que tiene una temperatura baja.",
              "relations": [{"type": "antonym", "target": "caliente-temperature"}]},
             {"id": "frio-person", "synset": "cold-person",
             "definition": "Distante en el trato."}]},
        {"type": "lexeme", "id": "frio-variant", "lemma": "frio", "pos": "adjective",
         "senses": [{"id": "frio-variant-sense", "definition": "Grafía alternativa de frío.",
                     "labels": ["obsolete"]}]},
        {"type": "lexeme", "id": "helado-adj", "lemma": "helado", "pos": "adjective",
         "senses": [{"id": "helado-cold", "synset": "cold-temperature",
                     "definition": "Extremadamente frío."}]},
        {"type": "lexeme", "id": "helado-noun", "lemma": "helado", "pos": "noun",
         "senses": [{"id": "ice-cream", "synset": "ice-cream",
                     "definition": "Alimento congelado dulce."}]},
        {"type": "lexeme", "id": "caliente", "lemma": "caliente", "pos": "adjective",
         "senses": [{"id": "caliente-temperature", "synset": "hot-temperature",
                     "definition": "Que tiene temperatura alta."}]},
        {"type": "lexeme", "id": "onomatopeya", "lemma": "onomatopeya", "pos": "noun",
         "senses": [{"id": "onomatopeya-sense", "definition":
                     "Palabra que imita o recrea un sonido."}]},
        {"type": "lexeme", "id": "dormir", "lemma": "dormir", "pos": "verb",
         "forms": [{"form": "dormí", "features": {"tense": "past"}}],
         "senses": [{"id": "dormir-1", "definition": "Permanecer en reposo."},
                    {"id": "dormir-2", "definition": "Pasar la noche en algún sitio."}]},
        {"type": "lexeme", "id": "caminar", "lemma": "caminar", "pos": "verb",
         "forms": [{"form": "caminando", "features": {"verb_form": "gerund"}}],
         "senses": [{"id": "caminar-1", "definition": "Moverse dando pasos."}]},
        {"type": "informal", "id": "xfa", "expression": "xfa",
         "expansion": "por favor", "register": "internet", "category": "abbreviation",
         "ambiguity_notes": "Abreviación informal.", "confidence": 0.96,
         "offensive": False, "examples": ["Revisa esto xfa."]},
        {"type": "emoji", "id": "cry", "emoji": "😭", "short_name": "cara llorando",
         "keywords": ["tristeza", "risa"], "categories": ["reaction"],
         "ambiguity_notes": "Puede ser tristeza, exageración o risa."},
    ]
    source.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records))
    license_path = tmp_path / "SYNTHETIC-LICENSE.txt"
    license_path.write_text("NOTICE FICTICIO: fixture sintético, sin datos de terceros.\n")
    return {
        "source_id": "synthetic.es",
        "title": "Fixture español sintético",
        "version": "1",
        "source_date": "2026-08-04",
        "source_url": "https://example.invalid/synthetic",
        "path": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "license_id": "LicenseRef-Synthetic-Test",
        "license_path": str(license_path),
        "attribution": "Fixture escrito para pruebas de Elyndra.",
        "transformation_notes": "JSONL sintético.",
        "format": "wiktionary-jsonl",
    }


def _paths(tmp_path: Path) -> ElyndraPaths:
    return ElyndraPaths(tmp_path / "config", tmp_path / "data",
                        tmp_path / "state", tmp_path / "cache")


def _installed(tmp_path: Path) -> tuple[LanguagePackRegistry, dict, dict]:
    source = _source(tmp_path)
    built = LanguagePackBuilder().build(
        logical_pack_id="elyndra.synthetic.es", version="1", sources=[source],
        output_dir=tmp_path / "build", build_epoch=1_700_000_000,
    )
    paths = _paths(tmp_path / "home")
    paths.ensure()
    database = Database(paths.database_file, role="root")
    database.migrate()
    registry = LanguagePackRegistry(database, paths)
    installed = registry.install(Path(built["path"]), actor="test")
    return registry, installed, built


def test_schema_49_roles_are_explicit_and_fail_closed(tmp_path: Path) -> None:
    root = Database(tmp_path / "root.db", role="root")
    root.migrate()
    with root.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        role = connection.execute(
            "SELECT value FROM schema_meta WHERE key='database_role'"
        ).fetchone()[0]
        assert schema == "50"
        assert role == "root"
    with pytest.raises(RuntimeError, match="Rol de base incompatible"):
        Database(tmp_path / "root.db", role="vault").migrate()


def test_deterministic_content_hash_atomic_install_and_read_only(tmp_path: Path) -> None:
    source = _source(tmp_path)
    first = LanguagePackBuilder().build(
        logical_pack_id="elyndra.synthetic.es", version="1", sources=[source],
        output_dir=tmp_path / "one", build_epoch=1_700_000_000,
    )
    second = LanguagePackBuilder().build(
        logical_pack_id="elyndra.synthetic.es", version="1", sources=[source],
        output_dir=tmp_path / "two", build_epoch=1_700_000_000,
    )
    assert first["content_sha256"] == second["content_sha256"]
    registry, installed, _ = _installed(tmp_path / "install")
    assert installed["status"] == "disabled"
    assert installed["verification_status"] == "verified"
    registry.set_enabled(installed["public_id"], enabled=True)
    connection = SpanishLexicalService._connect(registry.database_path(installed))
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM lexemes")
    finally:
        connection.close()


def test_lexical_senses_relations_informal_and_corruption(tmp_path: Path) -> None:
    registry, installed, _ = _installed(tmp_path)
    registry.set_enabled(installed["public_id"], enabled=True)
    service = SpanishLexicalService(registry)
    frio = service.lookup("fría")
    assert frio and frio[0]["source"] == "exact_form"
    senses = service.senses("frío")
    assert len(senses) == 2
    synonyms = service.related("frío", relation="synonym",
                               sense_id=senses[0]["sense_id"])
    assert any(item["lemma"] == "helado" for item in synonyms)
    informal = service.lookup("xfa")
    assert informal[0]["source"] == "informal_curated"
    assert likely_laughter("jdsjsdjsdajsad")["confidence"] < 0.6
    assert likely_laughter("asdfghjkl") is None
    path = registry.database_path(installed)
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        registry.verify(installed["public_id"])
    assert registry.get(installed["public_id"])["status"] == "invalid"


def test_overlay_payload_review_and_account_isolation(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    first = app.registry_accounts.register(
        username="lexuno", email="lexuno@example.test", password="clave9!segura",
        password_confirmation="clave9!segura", birth_date="1990-01-01",
        system_user=app.identity.system_user,
    )
    second = app.registry_accounts.register(
        username="lexdos", email="lexdos@example.test", password="clave9!segura",
        password_confirmation="clave9!segura", birth_date="1990-01-01",
        system_user=app.identity.system_user,
    )
    one = ElyndraApplication.load_for_account(first["public_id"], isolated_home)
    two = ElyndraApplication.load_for_account(second["public_id"], isolated_home)
    proposal = one.language_overlays.propose(
        entry_type="informal", expression="pipipi",
        payload={"expression": "pipipi", "expansion": "expresión paralingüística",
                 "category": "informal", "confidence": 0.6, "ambiguity_notes": "Ambigua."},
        actor="owner",
    )
    assert one.language_overlays.lookup("pipipi") == []
    one.language_overlays.review(proposal["public_id"], decision="approve", actor="owner")
    assert one.language_overlays.lookup("pipipi")
    assert two.language_overlays.lookup("pipipi") == []
    with pytest.raises(ValueError, match="campos no permitidos"):
        one.language_overlays.propose(entry_type="informal", expression="x",
                                      payload={"permission": "grant"}, actor="owner")


def test_failed_build_cleans_temporary_output(tmp_path: Path) -> None:
    source = _source(tmp_path)
    source["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        LanguagePackBuilder().build(
            logical_pack_id="elyndra.synthetic.es", version="1", sources=[source],
            output_dir=tmp_path / "failed", build_epoch=1,
        )
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".language-pack-*"))


def test_schema_48_root_and_vault_migrate_idempotently(tmp_path: Path) -> None:
    for role in ("root", "vault"):
        path = tmp_path / f"{role}.db"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        connection.execute("INSERT INTO schema_meta VALUES('schema_version','48')")
        connection.execute("CREATE TABLE preserved(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
        connection.execute("INSERT INTO preserved(value) VALUES('intacto')")
        connection.commit()
        connection.close()
        database = Database(path, role=role)
        database.migrate()
        database.migrate()
        with database.connect() as migrated:
            assert migrated.execute("SELECT value FROM preserved").fetchone()[0] == "intacto"
            assert migrated.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0] == "50"
            assert migrated.execute(
                "SELECT value FROM schema_meta WHERE key='database_role'"
            ).fetchone()[0] == role
            tables = {row[0] for row in migrated.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        if role == "root":
            assert "alexandria_language_packs" in tables
            assert "account_language_overlays" not in tables
        else:
            assert "account_language_overlays" in tables
            assert "alexandria_language_packs" not in tables


def test_web_and_cli_services_share_lookup_and_pack_state(
    isolated_home: ElyndraPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = ElyndraApplication.load(isolated_home)
    account = app.registry_accounts.register(
        username="lexdev", email="lexdev@example.test", password="clave9!segura",
        password_confirmation="clave9!segura", birth_date="1990-01-01",
        system_user=app.identity.system_user, developer_mode=True,
    )
    scoped = ElyndraApplication.load_for_account(account["public_id"], isolated_home)
    source = _source(tmp_path)
    built = LanguagePackBuilder().build(
        logical_pack_id="elyndra.web.es", version="1", sources=[source],
        output_dir=tmp_path / "web-pack", build_epoch=1_700_000_000,
    )
    service = ElyndraWebService(scoped)
    with pytest.raises(PermissionError, match="confirmación"):
        service.install_language_pack({"path": built["path"]})
    installed = service.install_language_pack({"path": built["path"], "approved": True})
    service.set_language_pack_enabled(
        {"id": installed["public_id"], "approved": True}, enabled=True
    )
    monkeypatch.setattr(
        socket, "socket", lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("La presentación léxica no debe abrir sockets.")
        )
    )
    assert service.language_pack_status()["items"][0]["status"] == "enabled"
    web = service.dictionary_lookup("onomatopeya")
    direct = scoped.dictionary.render_lookup("onomatopeya", language="es")
    assert web["matches"] == direct[1]["matches"]
    assert web["matches"][0]["license_id"] == "LicenseRef-Synthetic-Test"
    assert web["matches"][0]["attribution"] == ["Fixture escrito para pruebas de Elyndra."]
    dormi = service.dictionary_lookup("dormí")
    assert dormi["message"].count("dormí — forma de dormir · verbo") == 1
    assert dormi["message"].count("Permanecer en reposo") == 1
    assert "unknown" not in dormi["message"]
    assert "Equivalencias: no disponibles" not in dormi["message"]
    assert scoped.ask("¿Qué significa dormí?").message == dormi["message"]
    caminando = service.dictionary_lookup("caminando")
    assert caminando["message"].count("caminando — forma de caminar · verbo") == 1
    helado = service.dictionary_lookup("helado")["message"]
    assert helado.count("helado · adjetivo") == 1
    assert helado.count("helado · sustantivo") == 1
    canonical_frio = service.dictionary_lookup("frío")
    assert "Grafía alternativa" not in canonical_frio["message"]
    assert "Variante ortográfica:\n- frio" in canonical_frio["message"]
    variant_rows = [
        item for item in canonical_frio["matches"] if item["is_lexical_variant"]
    ]
    assert len(variant_rows) == 1
    assert variant_rows[0]["lexical_relation"] == "orthographic_variant"
    assert variant_rows[0]["relation_target"] == "frío"
    assert variant_rows[0]["source_id"] == "synthetic.es"
    assert variant_rows[0]["package_id"] == "elyndra.web.es"
    assert variant_rows[0]["attribution"] == ["Fixture escrito para pruebas de Elyndra."]
    direct_frio = service.dictionary_lookup("frio")
    assert "frio — variante ortográfica de frío · adjetivo" in direct_frio["message"]
    assert direct_frio["message"].count("Grafía alternativa") == 0
    assert "Variante ortográfica:\n- frio" not in direct_frio["message"]
    assert scoped.dictionary.render_lookup("frio")[0] == direct_frio["message"]
    frio_chat = scoped.ask("¿Qué significa frio?")
    assert frio_chat.data["model_used"] is False
    assert frio_chat.message == direct_frio["message"]
    emoji = service.dictionary_lookup("😭")["message"]
    assert "😭 · emoji" in emoji
    assert "Equivalencias" not in emoji
    assert "unknown" not in emoji
    laughter = service.dictionary_lookup("jdsjsdjsdajsad")["message"]
    assert "posible risa escrita" in laughter
    assert "Confianza limitada: 0,58" in laughter
    assert "Regla heurística local de Elyndra; sin fuente externa." in laughter
    heuristic = service.dictionary_lookup("jdsjsdjsdajsad")["matches"][0]
    assert heuristic["source_type"] == "local_heuristic"
    assert heuristic["external_dataset"] is False
    assert heuristic["deterministic"] is True
    assert heuristic["license_id"] == ""
    keyboard = service.dictionary_lookup("asdfghjkl")["message"]
    assert "secuencia de teclado" in keyboard
    assert "risa segura" in keyboard
    deterministic_queries = {
        "¿Qué significa caminando?": "caminando",
        "¿Cuáles son los sentidos de helado?": "helado",
        "¿Qué expresa 😭?": "😭",
        "¿Qué expresa el emoji 😭?": "😭",
        "¿Qué significa jdsjsdjsdajsad?": "jdsjsdjsdajsad",
        "¿Qué significa asdfghjkl?": "asdfghjkl",
    }
    for question, term in deterministic_queries.items():
        reply = scoped.ask(question)
        assert reply.data["model_used"] is False
        assert reply.message == service.dictionary_lookup(term)["message"]
    form_reply = scoped.ask("¿Dormí viene del verbo dormir?")
    assert form_reply.data["model_used"] is False
    assert "Sí" in form_reply.message and "forma conjugada de dormir" in form_reply.message
    service.close()


def test_orthographic_variant_does_not_consume_the_sense_limit() -> None:
    common = {
        "part_of_speech": "adjective", "matched_language": "es",
        "gloss_language": "es", "translations": {},
        "source": "exact_lemma", "package_id": "elyndra-es-wiktionary",
        "license_id": "CC-BY-SA-4.0 AND GFDL-1.3-or-later",
        "canonical_lemma": "frío", "match_type": "exact_lemma",
    }
    matches = [
        DictionaryMatch(
            concept_id=f"sense-{index}", matched_form="frío",
            gloss=f"Sentido {index}.", **common
        )
        for index in range(1, 7)
    ]
    matches.append(DictionaryMatch(
        concept_id="variant", gloss="Texto que no debe mostrarse.", **common,
        matched_form="frio", is_lexical_variant=True, variant_form="frio",
        lexical_relation="orthographic_variant", relation_target="frío",
    ))
    message, groups = render_dictionary_matches(matches, term="frío", per_group_limit=5)
    assert message.count("\n1. ") == 1
    assert "5. Sentido 5." in message
    assert "Sentido 6" not in message
    assert "Hay 1 sentido más." in message
    assert "Texto que no debe mostrarse" not in message
    assert "Variante ortográfica:\n- frio" in message
    assert groups[0]["sense_count"] == 5


def test_wordnet_and_cldr_xml_adapters_and_entity_rejection(tmp_path: Path) -> None:
    wordnet = tmp_path / "wordnet.xml"
    wordnet.write_text(
        """<LexicalResource><Lexicon>
        <LexicalEntry id="wn-frio"><Lemma writtenForm="frío" partOfSpeech="a"/>
        <Sense id="wn-frio-1" synset="wn-cold"/></LexicalEntry>
        <Synset id="wn-cold" partOfSpeech="a"><Definition>temperatura baja</Definition></Synset>
        </Lexicon></LexicalResource>""",
        encoding="utf-8",
    )
    cldr = tmp_path / "cldr.xml"
    cldr.write_text(
        '<annotations><annotation cp="😭" type="tts">cara llorando</annotation>'
        '<annotation cp="😭">llanto | risa</annotation></annotations>', encoding="utf-8"
    )
    license_path = tmp_path / "NOTICE.txt"
    license_path.write_text("NOTICE FICTICIO PARA PRUEBAS.\n")

    def metadata(path: Path, source_id: str, source_format: str) -> dict:
        return {
            "source_id": source_id, "title": source_id, "version": "test",
            "source_url": "https://example.invalid/test", "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "license_id": "LicenseRef-Synthetic-Test", "license_path": str(license_path),
            "attribution": "Fixture sintético.", "format": source_format,
        }

    built = LanguagePackBuilder().build(
        logical_pack_id="elyndra.xml.es", version="1",
        sources=[metadata(wordnet, "wordnet.synthetic", "wordnet-lmf"),
                 metadata(cldr, "cldr.synthetic", "cldr-xml")],
        output_dir=tmp_path / "xml-pack", build_epoch=1,
    )
    assert built["counts"]["lexemes"] == 1
    assert built["counts"]["emoji"] == 2
    malicious = tmp_path / "malicious.xml"
    malicious.write_text('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>')
    with pytest.raises(ValueError, match="DTD ni entidades"):
        LanguagePackBuilder().build(
            logical_pack_id="elyndra.bad.es", version="1",
            sources=[metadata(malicious, "wordnet.bad", "wordnet-lmf")],
            output_dir=tmp_path / "bad-pack", build_epoch=1,
        )


def test_official_omw_tab_and_cldr_doctype_adapters(tmp_path: Path) -> None:
    omw = tmp_path / "wn-data-spa.tab"
    omw.write_text(
        "# Multilingual Central Repository\tspa\thttps://example.test/MCR\tCC BY 3.0\n"
        "01258264-a\tspa:lemma\tfrío\n"
        "01258264-a\tspa:lemma\thelado\n"
        "01258264-a\tspa:def\t0\tque tiene una temperatura muy baja\n"
        "01258264-a\tspa:exe\t0\tun ejemplo omitido por sentido ambiguo\n",
        encoding="utf-8",
    )
    records = list(iter_omw_spanish_tab(omw))
    assert [record["type"] for record in records] == ["lexeme", "lexeme", "synset"]
    assert records[-1]["definitions"] == ["que tiene una temperatura muy baja"]
    assert records[0]["senses"][0]["synset"] == records[1]["senses"][0]["synset"]

    cldr = tmp_path / "es.xml"
    cldr.write_text(
        '<?xml version="1.0"?><!DOCTYPE ldml SYSTEM "../../common/dtd/ldml.dtd">'
        '<ldml><annotations><annotation cp="😭" type="tts">cara llorando</annotation>'
        "</annotations></ldml>",
        encoding="utf-8",
    )
    assert next(iter_cldr_annotations(cldr))["short_name"] == "cara llorando"


def test_emoji_annotations_are_queryable(tmp_path: Path) -> None:
    registry, installed, _ = _installed(tmp_path)
    registry.set_enabled(installed["public_id"], enabled=True)
    result = SpanishLexicalService(registry).lookup("😭")
    assert result[0]["source"] == "emoji_annotation"
    assert "risa" in result[0]["keywords_json"]
    assert "Puede ser" in result[0]["ambiguity_notes"]


def test_invalid_jsonl_missing_license_and_symlink_are_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    Path(source["path"]).write_bytes(b"\xff\n")
    source["sha256"] = hashlib.sha256(Path(source["path"]).read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="JSONL UTF-8"):
        LanguagePackBuilder().build(
            logical_pack_id="elyndra.invalid.es", version="1", sources=[source],
            output_dir=tmp_path / "invalid", build_epoch=1,
        )
    missing = _source(tmp_path / "missing")
    Path(missing["license_path"]).unlink()
    with pytest.raises(FileNotFoundError):
        LanguagePackBuilder().build(
            logical_pack_id="elyndra.missing.es", version="1", sources=[missing],
            output_dir=tmp_path / "missing-pack", build_epoch=1,
        )
    if hasattr(Path, "symlink_to"):
        linked = _source(tmp_path / "linked")
        original = Path(linked["path"])
        symlink = tmp_path / "linked-source.jsonl"
        symlink.symlink_to(original)
        linked["path"] = str(symlink)
        with pytest.raises(ValueError, match="enlace simbólico"):
            LanguagePackBuilder().build(
                logical_pack_id="elyndra.linked.es", version="1", sources=[linked],
                output_dir=tmp_path / "linked-pack", build_epoch=1,
            )


def test_kaikki_gzip_real_shape_filters_forms_relations_and_examples(tmp_path: Path) -> None:
    path = tmp_path / "kaikki.jsonl.gz"
    rows = [
        {"word": "caminar", "lang": "Español", "lang_code": "es", "pos": "verb",
         "forms": [{"form": "caminando", "tags": ["gerund"]}],
         "senses": [{"sense_index": "1", "glosses": ["Moverse dando pasos."],
                     "examples": [{"text": "Camina despacio."},
                                  {"text": "Cita ajena.", "ref": "Obra externa"}]}],
         "synonyms": [{"word": "andar", "sense_index": "1"}]},
        {"word": "caminando", "lang": "Español", "lang_code": "es", "pos": "verb",
         "senses": [{"sense_index": "1", "tags": ["form-of"],
                     "glosses": ["Gerundio de caminar."],
                     "form_of": [{"word": "caminar"}]}]},
        {"word": "caminarse", "lang": "Español", "lang_code": "es", "pos": "verb",
         "senses": [{"sense_index": "1", "tags": ["alt-of"],
                     "glosses": ["Variante sintética de caminar."],
                     "alt_of": [{"word": "caminar"}]}]},
        {"word": "andando", "lang": "Español", "lang_code": "es", "pos": "verb",
         "tags": ["form-of"],
         "senses": [{"sense_index": "1", "glosses": ["Forma sintética sin objetivo."]}]},
        {"word": "walk", "lang": "Inglés", "lang_code": "en", "pos": "verb",
         "senses": [{"glosses": ["Synthetic foreign entry."]}]},
    ]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    stats: dict[str, int] = {}
    records = list(iter_wiktextract_jsonl(path, stats=stats))
    assert [record["type"] for record in records] == [
        "lexeme", "form-link", "form-link"
    ]
    assert records[0]["senses"][0]["relations"] == [
        {"type": "synonym", "target_term": "andar"}
    ]
    assert records[0]["senses"][0]["examples"] == ["Camina despacio."]
    assert stats["entries_read"] == 5
    assert stats["spanish_entries"] == 4
    assert stats["other_languages"] == 1
    assert stats["unresolved_form_entries"] == 1
    assert stats["examples_rejected_ambiguous_source"] == 1


def test_kaikki_form_of_is_not_a_lemma_and_relations_stay_per_sense(tmp_path: Path) -> None:
    path = tmp_path / "kaikki.jsonl.gz"
    rows = [
        {"word": "frío", "lang": "Español", "lang_code": "es", "pos": "adj",
         "senses": [{"sense_index": "1", "glosses": ["De temperatura baja."]},
                    {"sense_index": "2", "glosses": ["Distante en el trato."]}],
         "antonyms": [{"word": "caliente", "sense_index": "1"}]},
        {"word": "dormir", "lang": "Español", "lang_code": "es", "pos": "verb",
         "senses": [{"sense_index": "1", "glosses": ["Estar en reposo."]}]},
        {"word": "dormí", "lang": "Español", "lang_code": "es", "pos": "verb",
         "senses": [{"sense_index": "1", "tags": ["form-of"],
                     "glosses": ["Forma de dormir."], "form_of": [{"word": "dormir"}]}]},
    ]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    notice = tmp_path / "NOTICE.txt"
    notice.write_text("Fixture sintético sin contenido externo.\n")
    source = {
        "source_id": "kaikki.synthetic", "title": "Kaikki synthetic shape",
        "version": "test", "source_url": "https://example.invalid/kaikki",
        "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "license_id": "LicenseRef-Synthetic-Test", "license_path": str(notice),
        "attribution": "Fixture sintético.", "format": "kaikki-wiktextract-jsonl-gz",
    }
    built = LanguagePackBuilder().build(
        logical_pack_id="elyndra.kaikki.synthetic", version="1", sources=[source],
        output_dir=tmp_path / "pack", build_epoch=1,
    )
    connection = sqlite3.connect(Path(built["path"]) / "lexicon.sqlite")
    assert connection.execute(
        "SELECT COUNT(*) FROM lexemes WHERE lemma='dormí'"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT l.lemma FROM lexeme_forms f JOIN lexemes l ON l.id=f.lexeme_id "
        "WHERE f.form='dormí'"
    ).fetchone()[0] == "dormir"
    relation_rows = connection.execute(
        "SELECT s.sense_order,r.target_term FROM sense_relation_terms r "
        "JOIN senses s ON s.id=r.source_sense_id"
    ).fetchall()
    assert relation_rows == [(0, "caliente")]
    connection.close()


def test_kaikki_rejects_oversize_invalid_json_and_truncated_gzip(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.jsonl.gz"
    with gzip.open(oversized, "wb") as handle:
        handle.write(b'{"word":"' + b"x" * (1024 * 1024) + b'"}\n')
    with pytest.raises(ValueError, match="supera 1 MiB"):
        list(iter_wiktextract_jsonl(oversized))
    invalid = tmp_path / "invalid.jsonl.gz"
    with gzip.open(invalid, "wb") as handle:
        handle.write(b"{invalid}\n")
    with pytest.raises(ValueError, match="JSONL UTF-8"):
        list(iter_wiktextract_jsonl(invalid))
    truncated = tmp_path / "truncated.jsonl.gz"
    truncated_bytes = gzip.compress(b'{"word":"x"}\n')[:-4]
    truncated.write_bytes(truncated_bytes)
    with pytest.raises(ValueError, match="gzip truncado"):
        list(iter_wiktextract_jsonl(truncated))


def test_kaikki_repeated_local_sense_indices_get_distinct_internal_ids(tmp_path: Path) -> None:
    path = tmp_path / "repeated.jsonl.gz"
    row = {
        "word": "sintético", "lang": "Español", "lang_code": "es", "pos": "adj",
        "senses": [
            {"sense_index": "1", "glosses": ["Primera acepción ficticia."]},
            {"sense_index": "1", "glosses": ["Segunda acepción ficticia."]},
        ],
    }
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    senses = next(iter_wiktextract_jsonl(path))["senses"]
    assert senses[0]["id"] != senses[1]["id"]
    assert ":local-sense:1:order:0" in senses[0]["id"]
