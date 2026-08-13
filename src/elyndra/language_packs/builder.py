from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.language_packs.constants import (
    BUILDER_VERSION,
    DATABASE_NAME,
    DEFAULT_DATABASE_BYTES,
    DEFAULT_RECORDS,
    DEFAULT_SOURCE_BYTES,
    DEFAULT_TOTAL_BYTES,
    MANIFEST_NAME,
    MAX_DATABASE_BYTES,
    MAX_RECORDS,
    MAX_SOURCE_BYTES,
    MAX_TOTAL_BYTES,
)
from elyndra.language_packs.importers import (
    iter_cldr_annotations,
    iter_jsonl,
    iter_omw_spanish_tab,
    iter_wiktextract_jsonl,
    iter_wordnet_lmf,
    normalize_term,
)
from elyndra.language_packs.safety import file_sha256, regular_file, stable_id
from elyndra.language_packs.schema import create_pack_schema


class LanguagePackBuilder:
    def build(
        self,
        *,
        logical_pack_id: str,
        version: str,
        sources: list[dict[str, Any]],
        output_dir: Path,
        locale: str = "es",
        build_epoch: int | None = None,
        allow_large: bool = False,
    ) -> dict[str, Any]:
        if not sources or len(sources) > 16:
            raise ValueError("El build requiere entre 1 y 16 fuentes separadas.")
        source_limit = MAX_SOURCE_BYTES if allow_large else DEFAULT_SOURCE_BYTES
        total_limit = MAX_TOTAL_BYTES if allow_large else DEFAULT_TOTAL_BYTES
        database_limit = MAX_DATABASE_BYTES if allow_large else DEFAULT_DATABASE_BYTES
        record_limit = MAX_RECORDS if allow_large else DEFAULT_RECORDS
        epoch = build_epoch
        if epoch is None:
            raw_epoch = os.environ.get("SOURCE_DATE_EPOCH", "")
            epoch = int(raw_epoch) if raw_epoch.isdigit() else int(time.time())
        created_at = datetime.fromtimestamp(epoch, UTC).isoformat()
        inspected = [self._inspect_source(item, source_limit) for item in sources]
        if sum(int(item["size_bytes"]) for item in inspected) > total_limit:
            raise ValueError("Las fuentes combinadas superan el límite permitido.")
        destination = output_dir.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            raise ValueError("El directorio de salida ya existe.")
        temporary = Path(tempfile.mkdtemp(prefix=".language-pack-", dir=destination.parent))
        temporary.chmod(0o700)
        started = time.monotonic()
        try:
            database_path = temporary / DATABASE_NAME
            connection = sqlite3.connect(database_path)
            connection.row_factory = sqlite3.Row
            content_digest = hashlib.sha256()
            content_digest.update(b"elyndra-language-pack-content\0schema=1\0")
            content_digest.update(BUILDER_VERSION.encode() + b"\0")
            counts = {
                "records": 0,
                "lexemes": 0,
                "forms": 0,
                "senses": 0,
                "definitions": 0,
                "examples": 0,
                "relations": 0,
                "synsets": 0,
                "informal": 0,
                "emoji": 0,
            }
            try:
                create_pack_schema(connection)
                connection.execute(
                    "CREATE TEMP TABLE pending_forms("
                    "source_id TEXT, external_id TEXT, form TEXT, normalized_lemma TEXT, "
                    "features_json TEXT)"
                )
                connection.execute("BEGIN IMMEDIATE")
                for source in inspected:
                    self._insert_source(connection, source)
                    source_identity = {
                        key: str(source.get(key, ""))
                        for key in (
                            "source_id",
                            "title",
                            "version",
                            "source_date",
                            "source_url",
                            "original_sha256",
                            "license_id",
                            "attribution",
                            "transformation_notes",
                        )
                    }
                    if source.get("provenance"):
                        source_identity["provenance"] = json.dumps(
                            source["provenance"], ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"),
                        )
                    content_digest.update(
                        json.dumps(
                            source_identity,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                        + b"\n"
                    )
                    source_records = 0
                    for record in self._records(source):
                        source_records += 1
                        counts["records"] += 1
                        if counts["records"] > record_limit:
                            raise ValueError("El build supera el límite de registros.")
                        canonical = json.dumps(
                            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                        ).encode()
                        content_digest.update(
                            str(source["source_id"]).encode() + b"\0" + canonical + b"\n"
                        )
                        self._insert_record(connection, source, record, counts)
                    source["imported_record_count"] = source_records
                    if source.get("import_stats"):
                        source["import_stats"] = dict(source["import_stats"])
                self._resolve_pending_forms(connection, counts)
                self._finish_indexes(connection)
                for key, table in (
                    ("lexemes", "lexemes"), ("forms", "lexeme_forms"),
                    ("senses", "senses"), ("synsets", "synsets"),
                    ("definitions", "sense_definitions"), ("examples", "sense_examples"),
                    ("informal", "informal_entries"), ("emoji", "emoji_annotations"),
                ):
                    row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                    counts[key] = int(row[0])
                counts["relations"] = sum(
                    int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    for table in (
                        "sense_relations", "sense_relation_terms", "synset_relations"
                    )
                )
                content_sha256 = content_digest.hexdigest()
                connection.execute(
                    "INSERT INTO pack_meta(key,value) VALUES('content_sha256',?)",
                    (content_sha256,),
                )
                connection.execute("INSERT INTO pack_meta VALUES('schema','1')")
                connection.commit()
                if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("El pack contiene referencias inválidas.")
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("El pack SQLite no supera integrity_check.")
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            database_path.chmod(0o600)
            if database_path.stat().st_size > database_limit:
                raise ValueError("El SQLite final supera el límite permitido.")
            database_sha256 = file_sha256(database_path)
            manifest = {
                "schema": 1,
                "pack_id": logical_pack_id,
                "language": "es",
                "locale": locale,
                "version": version,
                "builder_version": BUILDER_VERSION,
                "database_sha256": database_sha256,
                "content_sha256": content_sha256,
                "counts": counts,
                "sources": [self._manifest_source(item) for item in inspected],
                "limitations": [
                    "Recurso léxico local; no sustituye contexto ni análisis gramatical completo.",
                    *[
                        str(limitation)[:2000]
                        for source in inspected
                        for limitation in source.get("limitations", [])[:20]
                        if str(limitation).strip()
                    ],
                ],
                "created_at": created_at,
            }
            manifest_path = temporary / MANIFEST_NAME
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            manifest_path.chmod(0o600)
            attribution = temporary / "ATTRIBUTION.md"
            attribution.write_text(
                "# Attribution\n\n"
                + "\n".join(
                    f"- {item['title']}: {item['attribution']} ({item['license_id']})"
                    for item in inspected
                )
                + "\n",
                encoding="utf-8",
            )
            attribution.chmod(0o600)
            licenses = temporary / "LICENSES"
            licenses.mkdir(mode=0o700)
            for item in inspected:
                target = licenses / f"{item['source_id']}.txt"
                shutil.copyfile(item["license_path"], target)
                target.chmod(0o600)
            temporary.replace(destination)
            return manifest | {
                "path": str(destination),
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "network_used": False,
                "resumable": False,
            }
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    @staticmethod
    def _inspect_source(item: dict[str, Any], max_bytes: int) -> dict[str, Any]:
        source_id = str(item.get("source_id", "")).strip()
        if not source_id or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for char in source_id
        ):
            raise ValueError("source_id inválido.")
        path = regular_file(Path(str(item.get("path", ""))), max_bytes=max_bytes)
        license_path = regular_file(Path(str(item.get("license_path", ""))), max_bytes=2 * 1024**2)
        expected = str(item.get("sha256", "")).lower()
        observed = file_sha256(path)
        if expected != observed:
            raise ValueError(f"SHA-256 incorrecto para {source_id}.")
        license_text = license_path.read_text(encoding="utf-8")
        if not license_text.strip():
            raise ValueError(f"La licencia de {source_id} está vacía.")
        required = ("title", "version", "source_url", "license_id", "attribution", "format")
        if any(not str(item.get(field, "")).strip() for field in required):
            raise ValueError(f"La fuente {source_id} carece de procedencia obligatoria.")
        limitations = item.get("limitations", [])
        if not isinstance(limitations, list) or len(limitations) > 20:
            raise ValueError(f"Las limitaciones de {source_id} deben ser una lista acotada.")
        return dict(item) | {
            "source_id": source_id,
            "path": path,
            "license_path": license_path,
            "license_sha256": file_sha256(license_path),
            "original_sha256": observed,
            "size_bytes": path.stat().st_size,
        }

    @staticmethod
    def _records(source: dict[str, Any]) -> Iterator[dict[str, Any]]:
        source_format = str(source["format"])
        if source_format == "wordnet-lmf":
            yield from iter_wordnet_lmf(Path(source["path"]))
        elif source_format == "omw-spanish-tab":
            yield from iter_omw_spanish_tab(Path(source["path"]))
        elif source_format == "cldr-xml":
            yield from iter_cldr_annotations(Path(source["path"]))
        elif source_format in {"wiktionary-jsonl", "informal-jsonl", "cldr-jsonl"}:
            yield from iter_jsonl(Path(source["path"]))
        elif source_format == "kaikki-wiktextract-jsonl-gz":
            stats: dict[str, int] = {}
            source["import_stats"] = stats
            yield from iter_wiktextract_jsonl(Path(source["path"]), stats=stats)
        else:
            raise ValueError(f"Formato de fuente no soportado: {source_format}")

    @staticmethod
    def _insert_source(connection: sqlite3.Connection, source: dict[str, Any]) -> None:
        connection.execute(
            "INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)",
            (
                source["source_id"],
                source["title"],
                source["version"],
                str(source.get("source_date", "")),
                source["source_url"],
                source["original_sha256"],
                source["license_id"],
                source["attribution"],
                str(source.get("transformation_notes", "Importación local normalizada.")),
            ),
        )

    def _insert_record(
        self,
        connection: sqlite3.Connection,
        source: dict[str, Any],
        record: dict[str, Any],
        counts: dict[str, int],
    ) -> None:
        kind = str(record.get("type", "lexeme"))
        external = str(record.get("id", "")).strip()
        if not external or len(external) > 240:
            raise ValueError("Cada registro requiere id acotado.")
        source_id = str(source["source_id"])
        record_id = stable_id(source_id, external)
        canonical = hashlib.sha256(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        connection.execute(
            "INSERT INTO source_records VALUES(?,?,?,?)", (source_id, external, kind, canonical)
        )
        if kind == "informal":
            self._insert_informal(connection, source_id, record_id, record)
            counts["informal"] += 1
            return
        if kind == "emoji":
            self._insert_emoji(connection, source_id, record_id, record)
            counts["emoji"] += 1
            return
        if kind == "synset":
            self._insert_synset(connection, source_id, record_id, record)
            counts["synsets"] += 1
            return
        if kind == "form-link":
            form = str(record.get("form", "")).strip()
            lemma = normalize_term(str(record.get("lemma", "")))
            if not form or not lemma:
                raise ValueError("Un form-link requiere forma y lema.")
            connection.execute(
                "INSERT INTO pending_forms VALUES(?,?,?,?,?)",
                (source_id, external, form, lemma,
                 json.dumps(record.get("features", {}), sort_keys=True)),
            )
            return
        if kind != "lexeme":
            raise ValueError(f"Tipo léxico no soportado: {kind}")
        lemma = str(record.get("lemma", "")).strip()
        if not lemma or len(lemma) > 300:
            raise ValueError("Un lexema requiere lemma acotado.")
        connection.execute(
            "INSERT INTO lexemes VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                record_id,
                source_id,
                external,
                "es",
                str(record.get("locale", "es")),
                lemma,
                normalize_term(lemma),
                str(record.get("pos", "unknown"))[:40],
                json.dumps(record.get("features", {}), sort_keys=True),
                str(record.get("register", "neutral"))[:40],
            ),
        )
        connection.execute(
            "INSERT INTO lexical_terms_fts VALUES(?,?,?)", (lemma, "lemma", record_id)
        )
        counts["lexemes"] += 1
        forms = record.get("forms", [])
        if not isinstance(forms, list) or len(forms) > 512:
            raise ValueError("forms debe ser una lista de máximo 512 entradas.")
        for index, raw_form in enumerate(forms):
            form = str(
                raw_form if not isinstance(raw_form, dict) else raw_form.get("form", "")
            ).strip()
            if not form:
                continue
            form_id = stable_id(source_id, f"{external}:form:{index}:{form}")
            connection.execute(
                "INSERT INTO lexeme_forms VALUES(?,?,?,?,?,?,?)",
                (form_id, record_id, source_id, form, normalize_term(form), "inflected", "{}"),
            )
            connection.execute(
                "INSERT INTO lexical_terms_fts VALUES(?,?,?)", (form, "form", form_id)
            )
            counts["forms"] += 1
        senses = record.get("senses", [])
        if not isinstance(senses, list) or len(senses) > 128:
            raise ValueError("senses debe ser una lista acotada.")
        for order, sense in enumerate(senses):
            if not isinstance(sense, dict):
                raise ValueError("Cada sense debe ser un objeto.")
            sense_external = str(sense.get("id", f"{external}:sense:{order}"))
            synset_external = str(sense.get("synset", ""))
            synset_id = stable_id(source_id, synset_external) if synset_external else None
            if synset_id:
                connection.execute(
                    "INSERT OR IGNORE INTO synsets VALUES(?,?,?,?,?)",
                    (synset_id, source_id, synset_external, str(record.get("pos", "unknown")), ""),
                )
            sense_id = stable_id(source_id, sense_external)
            connection.execute(
                "INSERT INTO senses VALUES(?,?,?,?,?,?)",
                (sense_id, record_id, synset_id, source_id, sense_external, order),
            )
            if synset_id:
                connection.execute("INSERT INTO synset_members VALUES(?,?)", (synset_id, sense_id))
            definition = str(sense.get("definition", "")).strip()
            if definition:
                definition = definition[:16384]
                definition_id = stable_id(source_id, f"{sense_external}:definition:0")
                connection.execute(
                    "INSERT INTO sense_definitions VALUES(?,?,?,?,?,?)",
                    (definition_id, sense_id, source_id, "es", definition, 0),
                )
                connection.execute(
                    "INSERT INTO definitions_fts VALUES(?,?)", (definition, sense_id)
                )
                counts["definitions"] += 1
            examples = sense.get("examples", [])
            if not isinstance(examples, list) or len(examples) > 32:
                raise ValueError("examples debe ser una lista acotada.")
            for example_order, raw_example in enumerate(examples):
                example = str(raw_example).strip()[:4096]
                if not example:
                    continue
                example_id = stable_id(
                    source_id, f"{sense_external}:example:{example_order}"
                )
                connection.execute(
                    "INSERT INTO sense_examples VALUES(?,?,?,?,?,?)",
                    (example_id, sense_id, source_id, "es", example, example_order),
                )
                counts["examples"] += 1
            for label in sense.get("labels", [])[:32]:
                clean_label = str(label).strip()[:120]
                if clean_label:
                    connection.execute(
                        "INSERT OR IGNORE INTO usage_labels VALUES(?,?,?,?)",
                        ("sense", sense_id, "wiktextract_tag", clean_label),
                    )
            for relation in sense.get("relations", [])[:256]:
                if isinstance(relation, dict) and relation.get("target_term"):
                    target_term = str(relation["target_term"]).strip()[:300]
                    connection.execute(
                        "INSERT OR IGNORE INTO sense_relation_terms VALUES(?,?,?,?,?)",
                        (sense_id, str(relation.get("type", "related"))[:40], target_term,
                         normalize_term(target_term), source_id),
                    )
                    counts["relations"] += 1
                elif isinstance(relation, dict) and relation.get("target"):
                    connection.execute(
                        "INSERT OR IGNORE INTO sense_relations VALUES(?,?,?)",
                        (
                            sense_id,
                            str(relation.get("type", "related"))[:40],
                            stable_id(source_id, str(relation["target"])),
                        ),
                    )
                    counts["relations"] += 1
            counts["senses"] += 1

    @staticmethod
    def _resolve_pending_forms(connection: sqlite3.Connection, counts: dict[str, int]) -> None:
        rows = connection.execute(
            "SELECT source_id,external_id,form,normalized_lemma,features_json "
            "FROM pending_forms ORDER BY source_id,external_id"
        )
        for row in rows:
            lexemes = connection.execute(
                "SELECT id FROM lexemes WHERE source_id=? AND normalized_lemma=? ORDER BY id",
                (row[0], row[3]),
            ).fetchall()
            for lexeme in lexemes:
                form_id = stable_id(str(row[0]), f"{row[1]}:{lexeme[0]}")
                connection.execute(
                    "INSERT OR IGNORE INTO lexeme_forms VALUES(?,?,?,?,?,?,?)",
                    (form_id, lexeme[0], row[0], row[2], normalize_term(str(row[2])),
                     "form_of", row[4]),
                )
                connection.execute(
                    "INSERT INTO lexical_terms_fts VALUES(?,?,?)", (row[2], "form", form_id)
                )
                counts["forms"] += 1

    @staticmethod
    def _insert_synset(
        connection: sqlite3.Connection, source_id: str, record_id: str, record: dict[str, Any]
    ) -> None:
        connection.execute(
            "INSERT INTO synsets VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "part_of_speech=excluded.part_of_speech, domain=excluded.domain",
            (
                record_id,
                source_id,
                str(record["id"]),
                str(record.get("pos", "unknown")),
                str(record.get("domain", ""))[:120],
            ),
        )
        definitions = record.get("definitions", [])
        if isinstance(definitions, list):
            sense_rows = connection.execute(
                "SELECT sense_id FROM synset_members WHERE synset_id=? ORDER BY sense_id",
                (record_id,),
            ).fetchall()
            for sense_row in sense_rows:
                sense_id = str(sense_row[0])
                for order, raw_definition in enumerate(definitions[:8]):
                    definition = str(raw_definition).strip()[:16384]
                    if not definition:
                        continue
                    definition_id = stable_id(
                        source_id, f"{sense_id}:synset-definition:{order}"
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO sense_definitions VALUES(?,?,?,?,?,?)",
                        (definition_id, sense_id, source_id, "es", definition, order),
                    )
                    connection.execute(
                        "INSERT INTO definitions_fts VALUES(?,?)", (definition, sense_id)
                    )
        for relation in record.get("relations", [])[:256]:
            if isinstance(relation, dict) and relation.get("target"):
                connection.execute(
                    "INSERT OR IGNORE INTO synset_relations VALUES(?,?,?)",
                    (
                        record_id,
                        str(relation.get("type", "related"))[:40],
                        stable_id(source_id, str(relation["target"])),
                    ),
                )

    @staticmethod
    def _insert_informal(
        connection: sqlite3.Connection, source_id: str, record_id: str, record: dict[str, Any]
    ) -> None:
        expression = str(record.get("expression", ""))[:300]
        confidence = float(record.get("confidence", 0.5))
        if not expression or not 0 <= confidence <= 1:
            raise ValueError("Entrada informal inválida.")
        connection.execute(
            "INSERT INTO informal_entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record_id,
                source_id,
                "es",
                str(record.get("locale", "es")),
                expression,
                normalize_term(expression),
                str(record.get("expansion", ""))[:1000],
                str(record.get("register", "internet"))[:40],
                str(record.get("category", "informal"))[:40],
                str(record.get("ambiguity_notes", ""))[:2000],
                confidence,
                int(bool(record.get("offensive", False))),
                json.dumps(record.get("examples", [])[:10], ensure_ascii=False),
            ),
        )

    @staticmethod
    def _insert_emoji(
        connection: sqlite3.Connection, source_id: str, record_id: str, record: dict[str, Any]
    ) -> None:
        sequence = str(record.get("emoji", ""))[:64]
        if not sequence:
            raise ValueError("Entrada emoji sin secuencia.")
        connection.execute(
            "INSERT INTO emoji_annotations VALUES(?,?,?,?,?,?,?,?)",
            (
                record_id,
                source_id,
                "es",
                sequence,
                str(record.get("short_name", ""))[:300],
                json.dumps(record.get("keywords", [])[:50], ensure_ascii=False),
                json.dumps(record.get("categories", [])[:20], ensure_ascii=False),
                str(record.get("ambiguity_notes", ""))[:2000],
            ),
        )

    @staticmethod
    def _finish_indexes(connection: sqlite3.Connection) -> None:
        connection.execute("INSERT INTO lexical_terms_fts(lexical_terms_fts) VALUES('optimize')")
        connection.execute("INSERT INTO definitions_fts(definitions_fts) VALUES('optimize')")

    @staticmethod
    def _manifest_source(source: dict[str, Any]) -> dict[str, Any]:
        item = {
            "source_id": source["source_id"],
            "title": source["title"],
            "source_version": source["version"],
            "source_date": str(source.get("source_date", "")),
            "source_url": source["source_url"],
            "input_filename": Path(source["path"]).name,
            "original_sha256": source["original_sha256"],
            "license_id": source["license_id"],
            "license_text_path": f"LICENSES/{source['source_id']}.txt",
            "license_sha256": source["license_sha256"],
            "attribution": source["attribution"],
            "transformation_notes": str(source.get("transformation_notes", "Importación local.")),
            "imported_record_count": int(source.get("imported_record_count", 0)),
            "import_stats": dict(source.get("import_stats", {})),
        }
        provenance = source.get("provenance", {})
        if provenance:
            if not isinstance(provenance, dict):
                raise ValueError("provenance debe ser un objeto.")
            encoded = json.dumps(provenance, ensure_ascii=False, sort_keys=True)
            if len(encoded.encode("utf-8")) > 32 * 1024:
                raise ValueError("provenance supera 32 KiB.")
            item["provenance"] = provenance
        return item
