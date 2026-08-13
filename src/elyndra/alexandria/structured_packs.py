from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from elyndra.db import Database
from elyndra.paths import ElyndraPaths

_MANIFEST_NAME = "elyndra-structured-package.json"
_PACKAGE_ID = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_VERSION = re.compile(
    r"^[0-9]+(?:\.[0-9]+){2}(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_ALLOWED_ADAPTERS = {
    "dictionary.monolingual",
    "dictionary.bilingual",
    "language.morphology",
    "language.dialect",
    "first_aid.topic",
}
_MAX_MANIFEST_BYTES = 512_000
_MAX_TOTAL_BYTES = 128 * 1024 * 1024
_MAX_SOURCES = 64
_MAX_RECORDS = 500_000
_MAX_LINE_BYTES = 1_000_000
_MAX_CACHE_KEYS = 64
_MAX_LOOKUP_RESULTS = 20
_MAX_INDEXED_FORMS = 5_000_000
_MAX_FIRST_AID_CARDS = 50_000
_MAX_FIRST_AID_ALIASES = 500_000


class StructuredPackRepository:
    """Inspect and install disk-backed structured Alejandría packages."""

    def __init__(self, database: Database, paths: ElyndraPaths) -> None:
        self.database = database
        self.paths = paths
        self.storage_root = paths.alexandria_dir / "structured-packs"
        self.storage_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._dictionary_cache: OrderedDict[tuple[str, str, str, str], list[dict[str, Any]]] = (
            OrderedDict()
        )
        self._first_aid_cache: OrderedDict[tuple[str, str, str], dict[str, Any] | None] = (
            OrderedDict()
        )

    def inspect(self, path: Path) -> dict[str, Any]:
        expanded = path.expanduser()
        if expanded.is_symlink():
            raise ValueError("La carpeta del paquete no puede ser un enlace simbólico.")
        root = expanded.resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"El paquete estructurado debe ser una carpeta: {root}")
        manifest_path = root / _MANIFEST_NAME
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise ValueError(f"Falta {_MANIFEST_NAME} como archivo regular en {root}")
        if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise ValueError("El manifiesto estructurado supera 512 KB.")
        raw = manifest_path.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"El manifiesto no es JSON UTF-8 válido: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("El manifiesto estructurado debe ser un objeto JSON.")
        manifest = _validate_manifest(payload)
        resolved_sources: list[dict[str, Any]] = []
        total_size = 0
        record_count = 0
        entry_count = 0
        card_count = 0
        seen_keys: set[str] = set()
        indexed_form_count = 0
        alias_count = 0
        for source_item in manifest["sources"]:
            source = _resolve_source(root, source_item)
            total_size += int(source["size_bytes"])
            if total_size > _MAX_TOTAL_BYTES:
                raise ValueError("El paquete estructurado supera el límite de 128 MiB.")
            source_records = 0
            for record in _iter_jsonl(Path(source["path"])):
                source_records += 1
                record_count += 1
                if record_count > _MAX_RECORDS:
                    raise ValueError(
                        f"El paquete supera el límite de {_MAX_RECORDS} registros."
                    )
                if manifest["content_type"] == "language":
                    clean = _validate_language_record(record, manifest)
                    key = clean["entry_key"]
                    entry_count += 1
                    indexed_form_count += len(clean["indexed_forms"])
                    if indexed_form_count > _MAX_INDEXED_FORMS:
                        raise ValueError(
                            "El paquete supera el límite de formas léxicas indexadas."
                        )
                else:
                    clean = _validate_first_aid_record(record, manifest)
                    key = f"{clean['card_id']}:{clean['language']}:{clean['locale']}"
                    card_count += 1
                    alias_count += len(clean["aliases"])
                    if card_count > _MAX_FIRST_AID_CARDS:
                        raise ValueError(
                            "El paquete supera el límite de tarjetas de primeros auxilios."
                        )
                    if alias_count > _MAX_FIRST_AID_ALIASES:
                        raise ValueError(
                            "El paquete supera el límite de alias de primeros auxilios."
                        )
                if key in seen_keys:
                    raise ValueError(f"Registro estructurado duplicado: {key}")
                seen_keys.add(key)
            if source_records == 0:
                raise ValueError(f"La fuente {source['relative_path']} no contiene registros.")
            resolved_sources.append(source | {"record_count": source_records})
        return {
            **manifest,
            "package_root": str(root),
            "manifest_path": str(manifest_path),
            "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "resolved_sources": resolved_sources,
            "source_count": len(resolved_sources),
            "record_count": record_count,
            "entry_count": entry_count,
            "card_count": card_count,
            "indexed_form_count": indexed_form_count,
            "alias_count": alias_count,
            "total_size_bytes": total_size,
            "network_used": False,
            "execution_performed": False,
            "installation_requires_approval": True,
        }

    def install(
        self,
        path: Path,
        *,
        actor: str,
        replace: bool = False,
    ) -> dict[str, Any]:
        package = self.inspect(path)
        existing = self.get(package["package_id"])
        if existing is not None:
            if (
                existing["version"] == package["version"]
                and existing["manifest_sha256"] == package["manifest_sha256"]
            ):
                return existing | {"install_status": "unchanged"}
            if not replace:
                raise ValueError(
                    "Ya existe otra versión. Usa --replace junto con --approve para "
                    "reemplazarla explícitamente."
                )
        storage_path = self._copy_to_storage(package)
        previous_storage = Path(str(existing["storage_path"])) if existing else None
        try:
            with self.database.connect() as connection:
                if existing is not None:
                    connection.execute(
                        "DELETE FROM alexandria_structured_packs WHERE id = ?",
                        (int(existing["id"]),),
                    )
                now = _now()
                cursor = connection.execute(
                    """
                    INSERT INTO alexandria_structured_packs(
                        package_id, name, version, content_type, adapter,
                        language, target_language, locale, dialect,
                        license_id, publisher, manifest_sha256, storage_path,
                        enabled, review_status, reviewed_on, reviewer,
                        limitations_json, attribution_json, metadata_json,
                        actor, installed_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        package["package_id"],
                        package["name"],
                        package["version"],
                        package["content_type"],
                        package["adapter"],
                        package["language"],
                        package["target_language"],
                        package["locale"],
                        package["dialect"],
                        package["license_id"],
                        package["publisher"],
                        package["manifest_sha256"],
                        str(storage_path),
                        package["review"]["status"],
                        package["review"]["reviewed_on"],
                        package["review"]["reviewer"],
                        _json(package["limitations"]),
                        _json(package["attribution"]),
                        _json(
                            {
                                "description": package["description"],
                                "source_count": package["source_count"],
                                "record_count": package["record_count"],
                                "entry_count": package["entry_count"],
                                "card_count": package["card_count"],
                                "indexed_form_count": package["indexed_form_count"],
                                "alias_count": package["alias_count"],
                                "total_size_bytes": package["total_size_bytes"],
                            }
                        ),
                        actor,
                        now,
                        now,
                    ),
                )
                pack_id = int(cursor.lastrowid)
                for source in package["resolved_sources"]:
                    source_cursor = connection.execute(
                        """
                        INSERT INTO alexandria_structured_sources(
                            pack_id, relative_path, title, source_format,
                            sha256, source_url, attribution, size_bytes, record_count
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            pack_id,
                            source["relative_path"],
                            source["title"],
                            source["format"],
                            source["sha256"],
                            source["source_url"],
                            source["attribution"],
                            int(source["size_bytes"]),
                            int(source["record_count"]),
                        ),
                    )
                    source_id = int(source_cursor.lastrowid)
                    source_path = storage_path / str(source["relative_path"])
                    for record in _iter_jsonl(source_path):
                        if package["content_type"] == "language":
                            clean = _validate_language_record(record, package)
                            self._insert_language_entry(
                                connection,
                                pack_id,
                                source_id,
                                clean,
                            )
                        else:
                            clean = _validate_first_aid_record(record, package)
                            self._insert_first_aid_card(
                                connection,
                                pack_id,
                                source_id,
                                clean,
                            )
        except Exception:
            shutil.rmtree(storage_path, ignore_errors=True)
            raise
        if previous_storage is not None and previous_storage != storage_path:
            shutil.rmtree(previous_storage, ignore_errors=True)
        self._clear_caches()
        item = self.get(package["package_id"])
        if item is None:
            raise RuntimeError("No se pudo recuperar el paquete estructurado instalado.")
        return item | {
            "install_status": "replaced" if existing is not None else "installed"
        }

    def get(self, package_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT p.*,
                       (SELECT COUNT(*) FROM alexandria_lexical_entries e
                        WHERE e.pack_id = p.id) AS entry_count,
                       (SELECT COUNT(*) FROM alexandria_first_aid_cards c
                        WHERE c.pack_id = p.id) AS card_count,
                       (SELECT COUNT(*) FROM alexandria_structured_sources s
                        WHERE s.pack_id = p.id) AS source_count
                FROM alexandria_structured_packs p
                WHERE p.package_id = ?
                """,
                (package_id.strip().casefold(),),
            ).fetchone()
        return _public_pack(dict(row)) if row is not None else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT p.*,
                       (SELECT COUNT(*) FROM alexandria_lexical_entries e
                        WHERE e.pack_id = p.id) AS entry_count,
                       (SELECT COUNT(*) FROM alexandria_first_aid_cards c
                        WHERE c.pack_id = p.id) AS card_count,
                       (SELECT COUNT(*) FROM alexandria_structured_sources s
                        WHERE s.pack_id = p.id) AS source_count
                FROM alexandria_structured_packs p
                ORDER BY p.content_type, p.language, p.locale, p.package_id
                """
            ).fetchall()
        return [_public_pack(dict(row)) for row in rows]

    def status(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS pack_count,
                       SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled_count,
                       SUM(CASE WHEN content_type = 'language' THEN 1 ELSE 0 END)
                           AS language_pack_count,
                       SUM(CASE WHEN content_type = 'first_aid' THEN 1 ELSE 0 END)
                           AS first_aid_pack_count
                FROM alexandria_structured_packs
                """
            ).fetchone()
            entries = connection.execute(
                "SELECT COUNT(*) FROM alexandria_lexical_entries"
            ).fetchone()[0]
            cards = connection.execute(
                "SELECT COUNT(*) FROM alexandria_first_aid_cards"
            ).fetchone()[0]
            sources = connection.execute(
                "SELECT COUNT(*) FROM alexandria_structured_sources"
            ).fetchone()[0]
        return {
            "pack_count": int(row["pack_count"] or 0),
            "enabled_count": int(row["enabled_count"] or 0),
            "language_pack_count": int(row["language_pack_count"] or 0),
            "first_aid_pack_count": int(row["first_aid_pack_count"] or 0),
            "lexical_entry_count": int(entries or 0),
            "first_aid_card_count": int(cards or 0),
            "source_count": int(sources or 0),
            "storage_root": str(self.storage_root),
            "disk_backed": True,
            "full_database_loaded_in_ram": False,
            "dictionary_cache_keys": len(self._dictionary_cache),
            "first_aid_cache_keys": len(self._first_aid_cache),
            "automatic_download": False,
            "installation_requires_approval": True,
            "replacement_requires_approval": True,
        }

    def sources(self, package_id: str) -> list[dict[str, Any]]:
        item = self.get(package_id)
        if item is None:
            raise ValueError(f"Paquete estructurado no encontrado: {package_id}")
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT relative_path, title, source_format, sha256,
                       source_url, attribution, size_bytes, record_count
                FROM alexandria_structured_sources
                WHERE pack_id = ?
                ORDER BY id
                """,
                (int(item["id"]),),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_enabled(self, package_id: str, *, enabled: bool) -> dict[str, Any]:
        item = self.get(package_id)
        if item is None:
            raise ValueError(f"Paquete estructurado no encontrado: {package_id}")
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE alexandria_structured_packs SET enabled = ?, updated_at = ? WHERE id = ?",
                (1 if enabled else 0, _now(), int(item["id"])),
            )
        self._clear_caches()
        updated = self.get(package_id)
        if updated is None:
            raise RuntimeError("No se pudo recuperar el paquete actualizado.")
        return updated

    def remove(self, package_id: str) -> dict[str, Any]:
        item = self.get(package_id)
        if item is None:
            raise ValueError(f"Paquete estructurado no encontrado: {package_id}")
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM alexandria_structured_packs WHERE id = ?",
                (int(item["id"]),),
            )
        shutil.rmtree(Path(str(item["storage_path"])), ignore_errors=True)
        self._clear_caches()
        return {
            "package_id": item["package_id"],
            "version": item["version"],
            "removed": True,
        }

    def lookup_dictionary(
        self,
        term: str,
        *,
        language: str | None = None,
        output_language: str = "es",
        dialect: str | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        normalized = normalize_structured_term(term)
        if not normalized:
            return []
        clean_language = _base_language(language) if language else ""
        clean_output = _base_language(output_language)
        clean_dialect = (dialect or "").strip()
        capped = max(1, min(int(limit), _MAX_LOOKUP_RESULTS))
        cache_key = (clean_language, normalized, clean_output, clean_dialect)
        cached = self._cache_get(self._dictionary_cache, cache_key)
        if cached is not None:
            return [dict(item) for item in cached[:capped]]
        clauses = ["p.enabled = 1", "f.normalized_form = ?"]
        params: list[Any] = [normalized]
        if clean_language:
            clauses.append("f.language = ?")
            params.append(clean_language)
        if clean_dialect:
            clauses.append("(f.locale = ? OR f.locale = '' OR p.dialect = ?)")
            params.extend([clean_dialect, clean_dialect])
        params.append(_MAX_LOOKUP_RESULTS)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT e.*, f.form AS matched_form, f.language AS matched_language,
                       f.form_type, f.locale AS matched_locale,
                       p.package_id, p.name AS package_name, p.adapter,
                       p.review_status, p.reviewed_on, p.license_id,
                       p.locale AS package_locale, p.dialect AS package_dialect,
                       p.attribution_json, p.limitations_json,
                       s.relative_path AS source_relative_path,
                       s.title AS source_title, s.sha256 AS source_sha256,
                       s.source_url, s.attribution AS source_attribution
                FROM alexandria_lexical_forms f
                JOIN alexandria_lexical_entries e ON e.id = f.entry_id
                JOIN alexandria_structured_packs p ON p.id = e.pack_id
                JOIN alexandria_structured_sources s ON s.id = e.source_id
                WHERE {' AND '.join(clauses)}
                ORDER BY (p.review_status = 'reviewed') DESC,
                         (f.locale = ?) DESC,
                         p.updated_at DESC,
                         e.id
                LIMIT ?
                """,
                params[:-1] + [clean_dialect, params[-1]],
            ).fetchall()
        results = [_public_dictionary_row(dict(row), output_language=clean_output) for row in rows]
        self._cache_put(self._dictionary_cache, cache_key, results)
        return [dict(item) for item in results[:capped]]

    def lookup_first_aid(
        self,
        query: str,
        *,
        language: str = "es",
        locale: str | None = None,
    ) -> dict[str, Any] | None:
        normalized = normalize_structured_term(query)
        if not normalized:
            return None
        clean_language = _base_language(language)
        clean_locale = (locale or "").strip()
        cache_key = (clean_language, normalized, clean_locale)
        if cache_key in self._first_aid_cache:
            cached = self._first_aid_cache.pop(cache_key)
            self._first_aid_cache[cache_key] = cached
            return dict(cached) if cached is not None else None
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT c.*, p.package_id, p.name AS package_name,
                       p.review_status, p.reviewed_on AS package_reviewed_on,
                       p.license_id, p.attribution_json, p.limitations_json,
                       s.relative_path AS source_relative_path,
                       s.title AS source_title, s.sha256 AS source_sha256,
                       s.source_url, s.attribution AS source_attribution,
                       a.alias AS matched_alias
                FROM alexandria_first_aid_aliases a
                JOIN alexandria_first_aid_cards c ON c.id = a.card_id
                JOIN alexandria_structured_packs p ON p.id = c.pack_id
                JOIN alexandria_structured_sources s ON s.id = c.source_id
                WHERE p.enabled = 1
                  AND p.review_status = 'reviewed'
                  AND c.language = ?
                  AND instr(?, a.normalized_alias) > 0
                  AND (c.locale = ? OR c.locale = '' OR ? = '')
                ORDER BY (c.locale = ?) DESC,
                         length(a.normalized_alias) DESC,
                         p.reviewed_on DESC,
                         c.id
                LIMIT 1
                """,
                (
                    clean_language,
                    normalized,
                    clean_locale,
                    clean_locale,
                    clean_locale,
                ),
            ).fetchone()
        result = _public_first_aid_row(dict(row)) if row is not None else None
        self._cache_put(self._first_aid_cache, cache_key, result)
        return dict(result) if result is not None else None

    def get_first_aid_card(self, package_id: str, card_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT c.*, p.package_id, p.name AS package_name,
                       p.review_status, p.reviewed_on AS package_reviewed_on,
                       p.license_id, p.attribution_json, p.limitations_json,
                       s.relative_path AS source_relative_path,
                       s.title AS source_title, s.sha256 AS source_sha256,
                       s.source_url, s.attribution AS source_attribution,
                       '' AS matched_alias
                FROM alexandria_first_aid_cards c
                JOIN alexandria_structured_packs p ON p.id = c.pack_id
                JOIN alexandria_structured_sources s ON s.id = c.source_id
                WHERE p.enabled = 1 AND p.review_status = 'reviewed'
                  AND p.package_id = ? AND c.card_key = ?
                LIMIT 1
                """,
                (package_id.strip().casefold(), card_id.strip()),
            ).fetchone()
        return _public_first_aid_row(dict(row)) if row is not None else None

    def _copy_to_storage(self, package: dict[str, Any]) -> Path:
        package_root = Path(str(package["package_root"]))
        final = (
            self.storage_root
            / package["package_id"]
            / f"{package['version']}-{package['manifest_sha256'][:12]}"
        )
        if final.exists():
            shutil.rmtree(final)
        temp = final.parent / f".install-{uuid.uuid4().hex}"
        temp.mkdir(parents=True, mode=0o700)
        try:
            manifest_target = temp / _MANIFEST_NAME
            shutil.copy2(package_root / _MANIFEST_NAME, manifest_target)
            manifest_target.chmod(0o600)
            for source in package["resolved_sources"]:
                relative = Path(str(source["relative_path"]))
                target = temp / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(Path(str(source["path"])), target)
                target.chmod(0o600)
            final.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temp.replace(final)
            return final
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            raise

    @staticmethod
    def _insert_language_entry(
        connection: Any,
        pack_id: int,
        source_id: int,
        record: dict[str, Any],
    ) -> None:
        cursor = connection.execute(
            """
            INSERT INTO alexandria_lexical_entries(
                pack_id, source_id, entry_key, language, target_language, lemma,
                part_of_speech, sense_id, definition, translations_json,
                morphology_json, pronunciation_json, dialect_json,
                examples_json, source_ref
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pack_id,
                source_id,
                record["entry_key"],
                record["language"],
                record["target_language"],
                record["lemma"],
                record["part_of_speech"],
                record["sense_id"],
                record["definition"],
                _json(record["translations"]),
                _json(record["morphology"]),
                _json(record["pronunciation"]),
                _json(record["dialects"]),
                _json(record["examples"]),
                record["source_ref"],
            ),
        )
        entry_id = int(cursor.lastrowid)
        for form in record["indexed_forms"]:
            connection.execute(
                """
                INSERT INTO alexandria_lexical_forms(
                    entry_id, pack_id, language, form, normalized_form,
                    form_type, locale
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    pack_id,
                    form["language"],
                    form["form"],
                    form["normalized_form"],
                    form["form_type"],
                    form["locale"],
                ),
            )

    @staticmethod
    def _insert_first_aid_card(
        connection: Any,
        pack_id: int,
        source_id: int,
        record: dict[str, Any],
    ) -> None:
        cursor = connection.execute(
            """
            INSERT INTO alexandria_first_aid_cards(
                pack_id, source_id, card_key, language, locale, title, summary, urgency,
                steps_json, avoid_json, red_flags_json, source_refs_json,
                reviewed_on
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pack_id,
                source_id,
                record["card_id"],
                record["language"],
                record["locale"],
                record["title"],
                record["summary"],
                record["urgency"],
                _json(record["steps"]),
                _json(record["avoid"]),
                _json(record["red_flags"]),
                _json(record["source_refs"]),
                record["reviewed_on"],
            ),
        )
        card_id = int(cursor.lastrowid)
        for alias in record["aliases"]:
            connection.execute(
                """
                INSERT INTO alexandria_first_aid_aliases(
                    card_id, pack_id, language, locale, alias, normalized_alias
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    card_id,
                    pack_id,
                    record["language"],
                    record["locale"],
                    alias,
                    normalize_structured_term(alias),
                ),
            )

    def _clear_caches(self) -> None:
        self._dictionary_cache.clear()
        self._first_aid_cache.clear()

    @staticmethod
    def _cache_get(cache: OrderedDict[Any, Any], key: Any) -> Any | None:
        if key not in cache:
            return None
        value = cache.pop(key)
        cache[key] = value
        return value

    @staticmethod
    def _cache_put(cache: OrderedDict[Any, Any], key: Any, value: Any) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > _MAX_CACHE_KEYS:
            cache.popitem(last=False)


def normalize_structured_term(value: str) -> str:
    clean = unicodedata.normalize("NFKC", value).casefold().strip()
    if not clean:
        return ""
    if any(
        marker in unicodedata.name(char, "")
        for char in clean
        for marker in ("CJK", "HIRAGANA", "KATAKANA")
    ):
        return "".join(clean.split())
    decomposed = unicodedata.normalize("NFKD", clean)
    clean = "".join(char for char in decomposed if not unicodedata.combining(char))
    clean = re.sub(r"[^a-z0-9\s'-]", " ", clean)
    return " ".join(clean.split())


def _validate_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 2:
        raise ValueError("schema_version debe ser exactamente 2.")
    package_id = _required_text(payload, "package_id", 160).casefold()
    if not _PACKAGE_ID.fullmatch(package_id):
        raise ValueError("package_id contiene caracteres no permitidos.")
    adapter = _required_text(payload, "adapter", 80)
    if adapter not in _ALLOWED_ADAPTERS:
        raise ValueError(f"Adapter estructurado no soportado: {adapter}")
    content_type = "first_aid" if adapter == "first_aid.topic" else "language"
    declared_type = str(payload.get("content_type") or content_type).strip()
    if declared_type != content_type:
        raise ValueError("content_type no coincide con el adapter declarado.")
    language = _language_tag(_required_text(payload, "language", 40))
    target_language = str(payload.get("target_language") or "").strip()
    if adapter == "dictionary.bilingual":
        if not target_language:
            raise ValueError("Un paquete bilingüe requiere target_language.")
        target_language = _language_tag(target_language)
    elif target_language:
        target_language = _language_tag(target_language)
    locale = str(payload.get("locale") or "").strip()
    dialect = str(payload.get("dialect") or "").strip()
    if locale:
        locale = _language_tag(locale)
    if dialect:
        dialect = _language_tag(dialect)
    review_raw = payload.get("review")
    if not isinstance(review_raw, dict):
        raise ValueError("Falta el objeto review.")
    review_status = str(review_raw.get("status") or "unreviewed").strip().casefold()
    if review_status not in {"reviewed", "unreviewed"}:
        raise ValueError("review.status debe ser reviewed o unreviewed.")
    reviewed_on = str(review_raw.get("reviewed_on") or "").strip()
    reviewer = str(review_raw.get("reviewer") or "").strip()[:160]
    if reviewed_on:
        _iso_date(reviewed_on)
    if adapter == "first_aid.topic":
        if review_status != "reviewed" or not reviewed_on or not reviewer:
            raise ValueError(
                "Un paquete de primeros auxilios debe estar reviewed y declarar "
                "reviewed_on y reviewer."
            )
        if not locale:
            raise ValueError("Un paquete de primeros auxilios requiere locale.")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= _MAX_SOURCES:
        raise ValueError(f"sources debe contener entre 1 y {_MAX_SOURCES} entradas.")
    clean_sources: list[dict[str, str]] = []
    for item in sources:
        if not isinstance(item, dict):
            raise ValueError("Cada fuente estructurada debe ser un objeto.")
        source_format = str(item.get("format") or "jsonl").strip().casefold()
        if source_format != "jsonl":
            raise ValueError("0.7.25 solo admite fuentes JSONL UTF-8.")
        clean_sources.append(
            {
                "path": _required_text(item, "path", 300),
                "title": _required_text(item, "title", 200),
                "format": source_format,
                "sha256": _sha256(item.get("sha256")),
                "source_url": str(item.get("source_url") or "").strip()[:1000],
                "attribution": _required_text(item, "attribution", 1000),
            }
        )
    limitations = payload.get("limitations", [])
    attribution = payload.get("attribution", [])
    if not isinstance(limitations, list) or not isinstance(attribution, list):
        raise ValueError("limitations y attribution deben ser listas.")
    return {
        "schema_version": 2,
        "package_id": package_id,
        "name": _required_text(payload, "name", 160),
        "version": _version(_required_text(payload, "version", 80)),
        "content_type": content_type,
        "adapter": adapter,
        "language": language,
        "target_language": target_language,
        "locale": locale,
        "dialect": dialect,
        "license_id": _required_text(payload, "license_id", 160),
        "publisher": _required_text(payload, "publisher", 160),
        "description": str(payload.get("description") or "").strip()[:2000],
        "review": {
            "status": review_status,
            "reviewed_on": reviewed_on,
            "reviewer": reviewer,
        },
        "limitations": [str(item).strip()[:500] for item in limitations[:50] if str(item).strip()],
        "attribution": [str(item).strip()[:1000] for item in attribution[:50] if str(item).strip()],
        "sources": clean_sources,
    }


def _validate_language_record(
    record: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    entry_key = _required_text(record, "id", 240)
    language = _language_tag(str(record.get("language") or manifest["language"]))
    target_language = str(record.get("target_language") or manifest["target_language"])
    if target_language:
        target_language = _language_tag(target_language)
    lemma = _required_text(record, "lemma", 300)
    definition = str(record.get("definition") or "").strip()[:8000]
    translations = _string_list_map(record.get("translations", {}), value_limit=300)
    morphology = _object(record.get("morphology", {}), "morphology")
    pronunciation = _object(record.get("pronunciation", {}), "pronunciation")
    dialects = _object(record.get("dialects", {}), "dialects")
    examples = _string_list(record.get("examples", []), limit=20, value_limit=1000)
    adapter = manifest["adapter"]
    if adapter == "dictionary.monolingual" and not definition:
        raise ValueError(f"La entrada monolingüe {entry_key} requiere definition.")
    if adapter == "dictionary.bilingual":
        expected = manifest["target_language"]
        if expected not in translations or not translations[expected]:
            raise ValueError(
                f"La entrada bilingüe {entry_key} requiere traducciones para {expected}."
            )
    if adapter == "language.morphology" and not morphology:
        raise ValueError(f"La entrada morfológica {entry_key} requiere morphology.")
    if adapter == "language.dialect" and not dialects:
        raise ValueError(f"La entrada dialectal {entry_key} requiere dialects.")
    forms = _string_list(record.get("forms", []), limit=100, value_limit=300)
    indexed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add_form(form: str, form_type: str, form_language: str, locale: str = "") -> None:
        normalized = normalize_structured_term(form)
        key = (form_language, normalized, form_type, locale)
        if not normalized or key in seen:
            return
        seen.add(key)
        indexed.append(
            {
                "form": form,
                "normalized_form": normalized,
                "form_type": form_type,
                "language": _base_language(form_language),
                "locale": locale,
            }
        )

    add_form(lemma, "lemma", language, manifest.get("locale", ""))
    for form in forms:
        add_form(form, "form", language, manifest.get("locale", ""))
    morphology_forms = morphology.get("forms", []) if isinstance(morphology, dict) else []
    if isinstance(morphology_forms, list):
        for form in morphology_forms[:200]:
            if isinstance(form, str):
                add_form(form, "morphology", language, manifest.get("locale", ""))
            elif isinstance(form, dict):
                value = str(form.get("form") or "").strip()
                if value:
                    add_form(
                        value,
                        str(form.get("type") or "morphology")[:80],
                        str(form.get("language") or language),
                        str(form.get("locale") or manifest.get("locale", ""))[:40],
                    )
    for dialect_tag, dialect_value in dialects.items():
        if not isinstance(dialect_value, dict):
            continue
        dialect_forms = dialect_value.get("forms", [])
        if isinstance(dialect_forms, list):
            for form in dialect_forms[:100]:
                add_form(str(form), "dialect", language, str(dialect_tag)[:40])
    for translation_language, values in translations.items():
        for value in values:
            add_form(value, "translation", translation_language)
    if not indexed:
        raise ValueError(f"La entrada {entry_key} no contiene formas indexables.")
    return {
        "entry_key": entry_key,
        "language": _base_language(language),
        "target_language": _base_language(target_language) if target_language else "",
        "lemma": lemma,
        "part_of_speech": str(record.get("pos") or "unknown").strip()[:80],
        "sense_id": str(record.get("sense_id") or "").strip()[:200],
        "definition": definition,
        "translations": translations,
        "morphology": morphology,
        "pronunciation": pronunciation,
        "dialects": dialects,
        "examples": examples,
        "source_ref": str(record.get("source_ref") or "").strip()[:500],
        "indexed_forms": indexed,
    }


def _validate_first_aid_record(
    record: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    card_id = _required_text(record, "id", 240)
    language = _base_language(str(record.get("language") or manifest["language"]))
    locale = str(record.get("locale") or manifest["locale"]).strip()
    if locale:
        locale = _language_tag(locale)
    aliases = _string_list(record.get("aliases", []), limit=100, value_limit=300)
    title = _required_text(record, "title", 400)
    summary = _required_text(record, "summary", 3000)
    urgency = str(record.get("urgency") or "emergency").strip().casefold()
    if urgency not in {"emergency", "urgent", "routine"}:
        raise ValueError(
            f"La tarjeta {card_id} tiene urgency inválida: {urgency}"
        )
    steps = _string_list(record.get("steps", []), limit=30, value_limit=2000)
    avoid = _string_list(record.get("avoid", []), limit=30, value_limit=2000)
    red_flags = _string_list(record.get("red_flags", []), limit=30, value_limit=2000)
    source_refs = _string_list(record.get("source_refs", []), limit=30, value_limit=1000)
    reviewed_on = str(record.get("reviewed_on") or manifest["review"]["reviewed_on"])
    _iso_date(reviewed_on)
    if not steps:
        raise ValueError(f"La tarjeta {card_id} requiere al menos un paso.")
    if not source_refs:
        raise ValueError(f"La tarjeta {card_id} requiere source_refs.")
    aliases = [title, card_id.replace("_", " "), *aliases]
    return {
        "card_id": card_id,
        "language": language,
        "locale": locale,
        "title": title,
        "summary": summary,
        "urgency": urgency,
        "aliases": list(dict.fromkeys(aliases)),
        "steps": steps,
        "avoid": avoid,
        "red_flags": red_flags,
        "source_refs": source_refs,
        "reviewed_on": reviewed_on,
    }


def _resolve_source(root: Path, item: dict[str, str]) -> dict[str, Any]:
    relative = Path(item["path"])
    if relative.is_absolute():
        raise ValueError("Las fuentes estructuradas deben usar rutas relativas.")
    if ".." in relative.parts:
        raise ValueError("Las fuentes estructuradas no pueden contener '..'.")
    raw_path = root / relative
    if raw_path.is_symlink():
        raise ValueError("Las fuentes estructuradas no pueden ser enlaces simbólicos.")
    path = raw_path.resolve(strict=True)
    if root not in path.parents:
        raise ValueError("Una fuente intenta salir de la carpeta del paquete.")
    if not path.is_file():
        raise ValueError(f"La fuente no es un archivo regular: {path}")
    digest = _file_sha256(path)
    if digest != item["sha256"]:
        raise ValueError(f"SHA-256 incorrecto para {relative.as_posix()}.")
    return {
        **item,
        "path": str(path),
        "relative_path": relative.as_posix(),
        "size_bytes": path.stat().st_size,
    }


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("rb") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if len(raw) > _MAX_LINE_BYTES:
                raise ValueError(f"{path.name}:{line_number} supera 1 MB por línea.")
            if not raw.strip():
                continue
            try:
                item = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"{path.name}:{line_number} no es JSON UTF-8 válido.") from exc
            if not isinstance(item, dict):
                raise ValueError(f"{path.name}:{line_number} debe contener un objeto JSON.")
            yield item


def _public_pack(item: dict[str, Any]) -> dict[str, Any]:
    item["enabled"] = bool(item.get("enabled"))
    for field in ("limitations_json", "attribution_json", "metadata_json"):
        raw = str(item.pop(field, "[]" if field != "metadata_json" else "{}"))
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = [] if field != "metadata_json" else {}
        item[field.removesuffix("_json")] = value
    return item


def _public_dictionary_row(item: dict[str, Any], *, output_language: str) -> dict[str, Any]:
    translations = _load_object(item.pop("translations_json", "{}"))
    morphology = _load_object(item.pop("morphology_json", "{}"))
    pronunciation = _load_object(item.pop("pronunciation_json", "{}"))
    dialects = _load_object(item.pop("dialect_json", "{}"))
    examples = _load_list(item.pop("examples_json", "[]"))
    item["translations"] = translations
    item["morphology"] = morphology
    item["pronunciation"] = pronunciation
    item["dialects"] = dialects
    item["examples"] = examples
    item["attribution"] = _load_list(item.pop("attribution_json", "[]"))
    item["limitations"] = _load_list(item.pop("limitations_json", "[]"))
    item["gloss_language"] = item["language"]
    item["gloss"] = item["definition"]
    item["output_language"] = output_language
    item["source"] = "alexandria-structured-pack"
    return item


def _public_first_aid_row(item: dict[str, Any]) -> dict[str, Any]:
    for source, target in (
        ("steps_json", "steps"),
        ("avoid_json", "avoid"),
        ("red_flags_json", "red_flags"),
        ("source_refs_json", "source_refs"),
        ("attribution_json", "attribution"),
        ("limitations_json", "limitations"),
    ):
        item[target] = _load_list(item.pop(source, "[]"))
    item["topic_id"] = f"{item['package_id']}::{item['card_key']}"
    item["source"] = "alexandria-structured-pack"
    return item


def _required_text(payload: dict[str, Any], name: str, limit: int) -> str:
    value = str(payload.get(name) or "").strip()
    if not value:
        raise ValueError(f"Falta el campo obligatorio {name}.")
    if len(value) > limit:
        raise ValueError(f"{name} supera {limit} caracteres.")
    return value


def _sha256(value: Any) -> str:
    clean = str(value or "").strip().casefold()
    if len(clean) != 64 or any(char not in "0123456789abcdef" for char in clean):
        raise ValueError("Cada fuente debe declarar un SHA-256 válido.")
    return clean


def _version(value: str) -> str:
    if not _VERSION.fullmatch(value):
        raise ValueError("version debe usar un identificador semántico seguro, por ejemplo 1.0.0.")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _language_tag(value: str) -> str:
    clean = value.strip()
    if not _LANGUAGE_TAG.fullmatch(clean):
        raise ValueError(f"Etiqueta de idioma o locale inválida: {value}")
    parts = clean.split("-")
    return "-".join([parts[0].casefold(), *parts[1:]])


def _base_language(value: str | None) -> str:
    if not value:
        return ""
    return _language_tag(value).split("-", 1)[0]


def _iso_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Fecha ISO inválida: {value}") from exc
    return value


def _string_list(value: Any, *, limit: int, value_limit: int) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("Se esperaba una lista de textos.")
    return [str(item).strip()[:value_limit] for item in value[:limit] if str(item).strip()]


def _string_list_map(value: Any, *, value_limit: int) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise ValueError("translations debe ser un objeto por idioma.")
    result: dict[str, list[str]] = {}
    for raw_language, raw_values in value.items():
        language = _base_language(str(raw_language))
        result[language] = _string_list(raw_values, limit=100, value_limit=value_limit)
    return result


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} debe ser un objeto JSON.")
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded) > 100_000:
        raise ValueError(f"{name} supera el límite de 100 KB por entrada.")
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_object(value: str) -> dict[str, Any]:
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _load_list(value: str) -> list[Any]:
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return loaded if isinstance(loaded, list) else []


def _now() -> str:
    return datetime.now(UTC).isoformat()
