from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import unicodedata
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.db import Database
from elyndra.documents import process_document
from elyndra.paths import ElyndraPaths

_MAX_SOURCE_BYTES = 12 * 1024 * 1024
_MAX_SOURCE_CHARS = 600_000
_CHUNK_SIZE = 1_600
_CHUNK_OVERLAP = 120
_INDEX_VERSION = "2"

_SEARCH_STOPWORDS = {
    "segun",
    "alejandria",
    "revisa",
    "explica",
    "cuando",
    "como",
    "cual",
    "cuales",
    "este",
    "esta",
    "estos",
    "estas",
    "para",
    "por",
    "que",
    "una",
    "uno",
    "del",
    "los",
    "las",
    "con",
    "sin",
    "deberia",
    "diferencia",
    "problemas",
    "resuelve",
    "implica",
    "necesito",
    "sentido",
    "the",
    "and",
    "what",
    "when",
    "how",
    "from",
    "according",
}


_SEARCH_SYNONYMS: dict[str, tuple[str, ...]] = {
    "interfaz": ("interface",),
    "interface": ("interfaz",),
    "transaccion": ("transaction",),
    "transaction": ("transaccion",),
    "inyeccion": ("injection",),
    "injection": ("inyeccion",),
    "despliegue": ("deployment",),
    "deployment": ("despliegue",),
    "prueba": ("test", "testing"),
    "testing": ("prueba", "test"),
}

_TECHNICAL_ANCHORS = {
    "group_concat",
    "pdo",
    "mariadb",
    "mysql",
    "transaccion",
    "stock",
    "interfaz",
    "interface",
    "phpstan",
    "phpunit",
    "psalm",
    "pest",
    "webhook",
    "opcache",
    "fpm",
    "csrf",
    "xss",
    "composer",
    "autoload",
    "prepared",
    "lint",
    "sintaxis",
}


class AlexandriaRepository:
    def __init__(self, database: Database, paths: ElyndraPaths) -> None:
        self.database = database
        self.paths = paths
        self.last_reindex_status = self.reindex_all()

    def reindex_all(self, *, force: bool = False) -> dict[str, Any]:
        with self.database.connect() as connection:
            version_row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'alexandria_index_version'"
            ).fetchone()
            current_version = str(version_row[0]) if version_row else "1"
            sources = connection.execute(
                """
                SELECT s.*, l.name AS library_name,
                       COUNT(u.id) AS unit_count,
                       SUM(CASE WHEN u.review_status = 'reviewed' THEN 1 ELSE 0 END)
                           AS reviewed_units
                FROM alexandria_sources s
                JOIN alexandria_libraries l ON l.id = s.library_id
                LEFT JOIN alexandria_units u
                  ON u.source_id = s.id AND u.status = 'active'
                WHERE s.status = 'active' AND l.status = 'active'
                GROUP BY s.id
                ORDER BY s.id
                """
            ).fetchall()

        if current_version == _INDEX_VERSION and not force:
            return {
                "status": "current",
                "version": _INDEX_VERSION,
                "sources": len(sources),
                "reindexed": 0,
                "units": 0,
                "errors": [],
            }

        reindexed = 0
        unit_count = 0
        errors: list[dict[str, str | int]] = []
        for source in sources:
            source_id = int(source["id"])
            stored_path = Path(str(source["stored_path"]))
            try:
                data = stored_path.read_bytes()
                processed = process_document(str(source["filename"]), data)
                if processed.extraction_status not in {"extracted", "empty"}:
                    raise ValueError(processed.extraction_status)
                text = processed.extracted_text.strip()[:_MAX_SOURCE_CHARS]
                units = _chunk_units(text, str(source["title"]))
                if not units:
                    raise ValueError("sin unidades extraíbles")
                reviewed = (
                    int(source["unit_count"] or 0) > 0
                    and int(source["reviewed_units"] or 0)
                    == int(source["unit_count"] or 0)
                )
                self._replace_source_units(
                    source_id=source_id,
                    library_id=int(source["library_id"]),
                    library_name=str(source["library_name"]),
                    units=units,
                    reviewed=reviewed,
                )
                reindexed += 1
                unit_count += len(units)
            except (OSError, ValueError) as exc:
                errors.append({"source_id": source_id, "error": str(exc)[:300]})

        stored_version = _INDEX_VERSION if not errors else current_version
        with self.database.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES(?, ?)",
                ("alexandria_index_version", stored_version),
            )
        status = "partial" if errors else ("reindexed" if reindexed else "current")
        return {
            "status": status,
            "version": stored_version,
            "target_version": _INDEX_VERSION,
            "sources": len(sources),
            "reindexed": reindexed,
            "units": unit_count,
            "errors": errors,
        }

    def _replace_source_units(
        self,
        *,
        source_id: int,
        library_id: int,
        library_name: str,
        units: list[dict[str, str]],
        reviewed: bool,
    ) -> None:
        now = _now()
        review_status = "reviewed" if reviewed else "unreviewed"
        with self.database.connect() as connection:
            old_rows = connection.execute(
                "SELECT id FROM alexandria_units WHERE source_id = ?",
                (source_id,),
            ).fetchall()
            if _alexandria_fts_enabled(connection):
                for row in old_rows:
                    connection.execute(
                        "DELETE FROM alexandria_fts WHERE unit_id = ?",
                        (str(int(row["id"])),),
                    )
            connection.execute(
                "DELETE FROM alexandria_units WHERE source_id = ?",
                (source_id,),
            )
            fts_enabled = _alexandria_fts_enabled(connection)
            for index, unit in enumerate(units):
                content_hash = hashlib.sha256(
                    unit["content"].encode("utf-8")
                ).hexdigest()
                cursor = connection.execute(
                    """
                    INSERT INTO alexandria_units(
                        library_id, source_id, unit_index, heading, content,
                        content_hash, review_status, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        library_id,
                        source_id,
                        index,
                        unit["heading"][:240],
                        unit["content"],
                        content_hash,
                        review_status,
                        now,
                        now,
                    ),
                )
                if fts_enabled:
                    connection.execute(
                        """
                        INSERT INTO alexandria_fts(
                            unit_id, library_id, library_name, heading, content
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            str(int(cursor.lastrowid)),
                            str(library_id),
                            library_name,
                            unit["heading"],
                            unit["content"],
                        ),
                    )
            connection.execute(
                "UPDATE alexandria_sources SET updated_at = ? WHERE id = ?",
                (now, source_id),
            )

    def create_library(
        self,
        name: str,
        *,
        description: str = "",
        domain: str = "general",
        language: str = "auto",
        version: str = "1",
        license_id: str = "unverified",
    ) -> dict[str, Any]:
        clean_name = " ".join(name.strip().split())
        if not clean_name:
            raise ValueError("El nombre de la biblioteca no puede estar vacío.")
        if len(clean_name) > 100:
            raise ValueError("El nombre de la biblioteca supera 100 caracteres.")
        slug = _unique_slug(self.database, _slugify(clean_name))
        public_id = f"lib_{secrets.token_hex(6)}"
        now = _now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO alexandria_libraries(
                    public_id, name, slug, description, domain, language,
                    version, license_id, enabled, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'active', ?, ?)
                """,
                (
                    public_id,
                    clean_name,
                    slug,
                    description.strip()[:1000],
                    _clean_label(domain, "general"),
                    _clean_label(language, "auto"),
                    _clean_label(version, "1"),
                    _clean_label(license_id, "unverified"),
                    now,
                    now,
                ),
            )
            library_id = int(cursor.lastrowid)
        self._library_dir(public_id).mkdir(parents=True, exist_ok=True, mode=0o700)
        return self.get_library(library_id) or {}

    def list_libraries(
        self,
        *,
        query: str = "",
        include_disabled: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["l.status = 'active'"]
        params: list[Any] = []
        if not include_disabled:
            clauses.append("l.enabled = 1")
        clean_query = query.strip()
        if clean_query:
            like = f"%{clean_query}%"
            clauses.append(
                "(l.name LIKE ? OR l.description LIKE ? OR l.domain LIKE ? OR l.slug LIKE ?)"
            )
            params.extend([like, like, like, like])
        params.append(max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT l.*,
                       COUNT(DISTINCT s.id) AS source_count,
                       COUNT(DISTINCT u.id) AS unit_count,
                       SUM(CASE WHEN u.review_status = 'reviewed' THEN 1 ELSE 0 END)
                           AS reviewed_units
                FROM alexandria_libraries l
                LEFT JOIN alexandria_sources s
                  ON s.library_id = l.id AND s.status = 'active'
                LEFT JOIN alexandria_units u
                  ON u.library_id = l.id AND u.status = 'active'
                WHERE {' AND '.join(clauses)}
                GROUP BY l.id
                ORDER BY l.enabled DESC, l.updated_at DESC, l.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_library_row(row) for row in rows]

    def get_library(self, identifier: int | str) -> dict[str, Any] | None:
        field = "id" if isinstance(identifier, int) else "public_id"
        with self.database.connect() as connection:
            row = connection.execute(
                f"""
                SELECT l.*,
                       COUNT(DISTINCT s.id) AS source_count,
                       COUNT(DISTINCT u.id) AS unit_count,
                       SUM(CASE WHEN u.review_status = 'reviewed' THEN 1 ELSE 0 END)
                           AS reviewed_units
                FROM alexandria_libraries l
                LEFT JOIN alexandria_sources s
                  ON s.library_id = l.id AND s.status = 'active'
                LEFT JOIN alexandria_units u
                  ON u.library_id = l.id AND u.status = 'active'
                WHERE l.{field} = ? AND l.status = 'active'
                GROUP BY l.id
                """,
                (identifier,),
            ).fetchone()
        return _library_row(row) if row else None

    def update_library(
        self,
        identifier: int | str,
        *,
        name: str | None = None,
        description: str | None = None,
        domain: str | None = None,
        language: str | None = None,
        version: str | None = None,
        license_id: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        library = self.get_library(identifier)
        if library is None:
            raise ValueError("Biblioteca no encontrada.")
        updates: list[str] = []
        params: list[Any] = []
        if name is not None:
            clean_name = " ".join(name.strip().split())
            if not clean_name:
                raise ValueError("El nombre de la biblioteca no puede estar vacío.")
            updates.append("name = ?")
            params.append(clean_name[:100])
        for field, value, fallback, limit in (
            ("description", description, "", 1000),
            ("domain", domain, "general", 80),
            ("language", language, "auto", 30),
            ("version", version, "1", 80),
            ("license_id", license_id, "unverified", 120),
        ):
            if value is not None:
                updates.append(f"{field} = ?")
                params.append((_clean_label(value, fallback))[:limit])
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)
        if not updates:
            return library
        updates.append("updated_at = ?")
        params.append(_now())
        params.append(int(library["id"]))
        with self.database.connect() as connection:
            connection.execute(
                f"UPDATE alexandria_libraries SET {', '.join(updates)} WHERE id = ?",
                params,
            )
        return self.get_library(int(library["id"])) or {}

    def delete_library(self, identifier: int | str) -> dict[str, Any]:
        library = self.get_library(identifier)
        if library is None:
            raise ValueError("Biblioteca no encontrada.")

        library_id = int(library["id"])
        public_id = str(library["public_id"])
        library_dir = self._library_dir(public_id)
        file_count = (
            sum(1 for item in library_dir.rglob("*") if item.is_file())
            if library_dir.exists()
            else 0
        )

        with self.database.connect() as connection:
            if _alexandria_fts_enabled(connection):
                connection.execute(
                    "DELETE FROM alexandria_fts WHERE library_id = ?",
                    (str(library_id),),
                )
            cursor = connection.execute(
                "DELETE FROM alexandria_libraries WHERE id = ?",
                (library_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("Biblioteca no encontrada.")

        if library_dir.exists():
            try:
                shutil.rmtree(library_dir)
            except OSError as exc:
                raise ValueError(
                    "La biblioteca se eliminó de la base, pero no fue posible "
                    f"borrar todos sus archivos locales: {exc}"
                ) from exc

        return {
            "id": library_id,
            "public_id": public_id,
            "name": str(library["name"]),
            "removed_sources": int(library["source_count"]),
            "removed_units": int(library["unit_count"]),
            "removed_files": file_count,
        }

    def import_file(
        self,
        library_identifier: int | str,
        path: Path,
        *,
        title: str | None = None,
        source_url: str = "",
    ) -> dict[str, Any]:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise ValueError(f"La fuente no es un archivo existente: {resolved}")
        data = resolved.read_bytes()
        return self.import_bytes(
            library_identifier,
            filename=resolved.name,
            data=data,
            title=title,
            original_path=str(resolved),
            source_url=source_url,
        )

    def import_bytes(
        self,
        library_identifier: int | str,
        *,
        filename: str,
        data: bytes,
        title: str | None = None,
        original_path: str = "",
        source_url: str = "",
    ) -> dict[str, Any]:
        library = self.get_library(library_identifier)
        if library is None:
            raise ValueError("Biblioteca no encontrada.")
        if not data:
            raise ValueError("La fuente está vacía.")
        if len(data) > _MAX_SOURCE_BYTES:
            raise ValueError("La fuente supera el límite local de 12 MiB.")
        clean_filename = _safe_filename(filename)
        processed = process_document(clean_filename, data)
        if processed.kind == "image":
            raise ValueError(
                "Alejandría 0.6.0 admite fuentes textuales; "
                "las imágenes quedan en chats."
            )
        if processed.extraction_status not in {"extracted", "empty"}:
            detail = "; ".join(processed.diagnostics.get("messages", []))
            raise ValueError(
                f"No se pudo extraer la fuente: "
                f"{detail or processed.extraction_status}"
            )
        text = processed.extracted_text.strip()[:_MAX_SOURCE_CHARS]
        if not text:
            raise ValueError("La fuente no contiene texto extraíble.")

        digest = hashlib.sha256(data).hexdigest()
        public_id = f"src_{secrets.token_hex(6)}"
        extension = Path(clean_filename).suffix.casefold().lstrip(".") or "text"
        target_dir = self._library_dir(str(library["public_id"])) / "sources"
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = target_dir / f"{digest[:12]}-{clean_filename}"
        if not target.exists():
            target.write_bytes(data)
            with suppress(PermissionError):
                target.chmod(0o600)

        clean_title = " ".join((title or Path(clean_filename).stem).strip().split())
        units = _chunk_units(text, clean_title)
        if not units:
            raise ValueError("No fue posible crear unidades de conocimiento.")
        now = _now()
        provenance = {
            "original_path": original_path,
            "source_url": source_url.strip(),
            "filename": clean_filename,
            "sha256": digest,
            "processor": processed.processor,
            "validation_status": processed.validation_status,
        }
        with self.database.connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM alexandria_sources
                WHERE library_id = ? AND sha256 = ? AND status = 'active'
                """,
                (int(library["id"]), digest),
            ).fetchone()
            if existing:
                return self._source_detail(connection, int(existing["id"])) | {
                    "import_status": "unchanged"
                }
            cursor = connection.execute(
                """
                INSERT INTO alexandria_sources(
                    public_id, library_id, title, filename, stored_path,
                    original_path, source_url, source_type, sha256, size_bytes,
                    processor, validation_status, provenance_json, status,
                    imported_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    public_id,
                    int(library["id"]),
                    clean_title[:180],
                    clean_filename,
                    str(target),
                    original_path,
                    source_url.strip()[:1000],
                    extension,
                    digest,
                    len(data),
                    processed.processor,
                    processed.validation_status,
                    json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
            source_id = int(cursor.lastrowid)
            fts_enabled = _alexandria_fts_enabled(connection)
            for index, unit in enumerate(units):
                content_hash = hashlib.sha256(unit["content"].encode("utf-8")).hexdigest()
                unit_cursor = connection.execute(
                    """
                    INSERT INTO alexandria_units(
                        library_id, source_id, unit_index, heading, content,
                        content_hash, review_status, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'unreviewed', 'active', ?, ?)
                    """,
                    (
                        int(library["id"]),
                        source_id,
                        index,
                        unit["heading"][:240],
                        unit["content"],
                        content_hash,
                        now,
                        now,
                    ),
                )
                if fts_enabled:
                    connection.execute(
                        """
                        INSERT INTO alexandria_fts(
                            unit_id, library_id, library_name, heading, content
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            str(int(unit_cursor.lastrowid)),
                            str(int(library["id"])),
                            str(library["name"]),
                            unit["heading"],
                            unit["content"],
                        ),
                    )
            connection.execute(
                "UPDATE alexandria_libraries SET updated_at = ? WHERE id = ?",
                (now, int(library["id"])),
            )
            detail = self._source_detail(connection, source_id)
        return detail | {"import_status": "imported"}

    def list_sources(
        self,
        library_identifier: int | str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        library = self.get_library(library_identifier)
        if library is None:
            raise ValueError("Biblioteca no encontrada.")
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*,
                       COUNT(u.id) AS unit_count,
                       SUM(CASE WHEN u.review_status = 'reviewed' THEN 1 ELSE 0 END)
                           AS reviewed_units
                FROM alexandria_sources s
                LEFT JOIN alexandria_units u
                  ON u.source_id = s.id AND u.status = 'active'
                WHERE s.library_id = ? AND s.status = 'active'
                GROUP BY s.id
                ORDER BY s.updated_at DESC, s.id DESC
                LIMIT ?
                """,
                (int(library["id"]), max(1, min(limit, 500))),
            ).fetchall()
        return [_source_row(row) for row in rows]

    def review_source(self, source_id: int, *, reviewed: bool = True) -> dict[str, Any]:
        now = _now()
        status = "reviewed" if reviewed else "unreviewed"
        with self.database.connect() as connection:
            source = connection.execute(
                "SELECT * FROM alexandria_sources WHERE id = ? AND status = 'active'",
                (source_id,),
            ).fetchone()
            if source is None:
                raise ValueError("Fuente no encontrada.")
            connection.execute(
                """
                UPDATE alexandria_units
                SET review_status = ?, updated_at = ?
                WHERE source_id = ? AND status = 'active'
                """,
                (status, now, source_id),
            )
            connection.execute(
                "UPDATE alexandria_sources SET updated_at = ? WHERE id = ?",
                (now, source_id),
            )
            return self._source_detail(connection, source_id)

    def search(
        self,
        query: str,
        *,
        library: str | None = None,
        domain_prefixes: tuple[str, ...] = (),
        limit: int = 6,
        reviewed_only: bool = False,
        prefer_reviewed: bool = True,
    ) -> list[dict[str, Any]]:
        clean_query = query.strip()
        if not clean_query:
            return []
        safe_limit = max(1, min(limit, 30))
        candidate_limit = min(120, max(safe_limit * 8, 24))
        with self.database.connect() as connection:
            library_row = None
            if library:
                library_row = connection.execute(
                    """
                    SELECT id FROM alexandria_libraries
                    WHERE (public_id = ? OR slug = ? OR name = ? COLLATE NOCASE)
                      AND status = 'active' AND enabled = 1
                    """,
                    (library, library, library),
                ).fetchone()
                if library_row is None:
                    return []
            clauses = ["l.status = 'active'", "l.enabled = 1", "u.status = 'active'"]
            params: list[Any] = []
            if library_row is not None:
                clauses.append("l.id = ?")
                params.append(int(library_row["id"]))
            if reviewed_only:
                clauses.append("u.review_status = 'reviewed'")
            domain_clause = ""
            if domain_prefixes:
                domain_parts: list[str] = []
                for prefix in domain_prefixes:
                    clean_prefix = prefix.strip().rstrip("/")
                    if not clean_prefix:
                        continue
                    domain_parts.append("(l.domain = ? OR l.domain LIKE ?)")
                    params.extend((clean_prefix, f"{clean_prefix}/%"))
                if domain_parts:
                    domain_clause = f" AND ({' OR '.join(domain_parts)})"
            review_order = (
                "CASE WHEN u.review_status = 'reviewed' THEN 0 ELSE 1 END, "
                if prefer_reviewed
                else ""
            )
            if _alexandria_fts_enabled(connection):
                try:
                    fts_clauses = list(clauses)
                    fts_params = [_fts_query(clean_query), *params, candidate_limit]
                    rows = connection.execute(
                        f"""
                        SELECT u.id AS unit_id, u.unit_index, u.heading, u.content,
                               u.review_status, s.id AS source_id, s.public_id AS source_public_id,
                               s.title AS source_title, s.filename, s.sha256,
                               l.id AS library_id, l.public_id AS library_public_id,
                               l.name AS library_name, l.slug AS library_slug,
                               l.domain AS library_domain, l.version AS library_version,
                               l.license_id AS library_license_id,
                               bm25(alexandria_fts) AS rank
                        FROM alexandria_fts
                        JOIN alexandria_units u
                          ON u.id = CAST(alexandria_fts.unit_id AS INTEGER)
                        JOIN alexandria_sources s ON s.id = u.source_id
                        JOIN alexandria_libraries l ON l.id = u.library_id
                        WHERE alexandria_fts MATCH ? AND {' AND '.join(fts_clauses)}
                              {domain_clause}
                        ORDER BY {review_order}rank, u.id DESC
                        LIMIT ?
                        """,
                        fts_params,
                    ).fetchall()
                    return _ranked_search_rows(
                        rows, clean_query, safe_limit, prefer_reviewed=prefer_reviewed
                    )
                except sqlite3.OperationalError:
                    pass
            like = f"%{clean_query}%"
            rows = connection.execute(
                f"""
                SELECT u.id AS unit_id, u.unit_index, u.heading, u.content,
                       u.review_status, s.id AS source_id, s.public_id AS source_public_id,
                       s.title AS source_title, s.filename, s.sha256,
                       l.id AS library_id, l.public_id AS library_public_id,
                       l.name AS library_name, l.slug AS library_slug,
                       l.domain AS library_domain, l.version AS library_version,
                       l.license_id AS library_license_id,
                       0.0 AS rank
                FROM alexandria_units u
                JOIN alexandria_sources s ON s.id = u.source_id
                JOIN alexandria_libraries l ON l.id = u.library_id
                WHERE {' AND '.join(clauses)}
                  {domain_clause}
                  AND (u.content LIKE ? OR u.heading LIKE ? OR s.title LIKE ? OR l.name LIKE ?)
                ORDER BY {review_order}u.id DESC
                LIMIT ?
                """,
                [*params, like, like, like, like, candidate_limit],
            ).fetchall()
        return _ranked_search_rows(
            rows, clean_query, safe_limit, prefer_reviewed=prefer_reviewed
        )

    def overview(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM alexandria_libraries WHERE status = 'active') AS libraries,
                  (SELECT COUNT(*) FROM alexandria_libraries
                    WHERE status = 'active' AND enabled = 1) AS enabled_libraries,
                  (SELECT COUNT(*) FROM alexandria_sources WHERE status = 'active') AS sources,
                  (SELECT COUNT(*) FROM alexandria_units WHERE status = 'active') AS units,
                  (SELECT COUNT(*) FROM alexandria_units
                    WHERE status = 'active' AND review_status = 'reviewed') AS reviewed_units
                """
            ).fetchone()
            index_row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'alexandria_fts5'"
            ).fetchone()
            version_row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'alexandria_index_version'"
            ).fetchone()
        root = self.paths.alexandria_dir
        size_bytes = sum(
            item.stat().st_size for item in root.rglob("*") if item.is_file()
        ) if root.exists() else 0
        return {
            "counts": {
                key: int(value or 0) for key, value in dict(row).items()
            },
            "storage": {"path": str(root), "size_bytes": size_bytes},
            "fts5": bool(index_row and index_row[0] == "enabled"),
            "index_version": str(version_row[0]) if version_row else "1",
            "last_reindex": self.last_reindex_status,
        }

    def _library_dir(self, public_id: str) -> Path:
        return self.paths.alexandria_dir / public_id

    @staticmethod
    def _source_detail(connection: sqlite3.Connection, source_id: int) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT s.*,
                   COUNT(u.id) AS unit_count,
                   SUM(CASE WHEN u.review_status = 'reviewed' THEN 1 ELSE 0 END)
                       AS reviewed_units
            FROM alexandria_sources s
            LEFT JOIN alexandria_units u
              ON u.source_id = s.id AND u.status = 'active'
            WHERE s.id = ?
            GROUP BY s.id
            """,
            (source_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Fuente no encontrada.")
        return _source_row(row)


def _library_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["enabled"] = bool(item.get("enabled", 0))
    item["source_count"] = int(item.get("source_count") or 0)
    item["unit_count"] = int(item.get("unit_count") or 0)
    item["reviewed_units"] = int(item.get("reviewed_units") or 0)
    return item


def _source_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["unit_count"] = int(item.get("unit_count") or 0)
    item["reviewed_units"] = int(item.get("reviewed_units") or 0)
    try:
        item["provenance"] = json.loads(str(item.get("provenance_json", "{}")))
    except json.JSONDecodeError:
        item["provenance"] = {}
    item.pop("provenance_json", None)
    return item


def _ranked_search_rows(
    rows: list[sqlite3.Row],
    query: str,
    limit: int,
    *,
    prefer_reviewed: bool,
) -> list[dict[str, Any]]:
    items = [_search_row(row, query) for row in rows]
    strong_items = [item for item in items if not _weak_search_heading(item)]
    if strong_items:
        items = strong_items
    anchors = _anchor_terms(query)
    if anchors:
        anchored = [item for item in items if int(item.get("anchor_matches") or 0) > 0]
        if anchored:
            items = anchored
    items = [item for item in items if float(item.get("relevance_score") or 0.0) > 0]
    items.sort(
        key=lambda item: (
            0
            if not prefer_reviewed or item.get("review_status") == "reviewed"
            else 1,
            -int(item.get("anchor_matches") or 0),
            -float(item.get("relevance_score") or 0.0),
            -int(item.get("matched_terms") or 0),
            float(item.get("rank") or 0.0),
            -int(item.get("unit_id") or 0),
        )
    )
    return items[:limit]


def _weak_search_heading(item: dict[str, Any]) -> bool:
    heading = _normalize_search(str(item.get("heading") or ""))
    return any(
        marker in heading
        for marker in ("fuentes", "referencias", "skills futuras", "estado de revision")
    )


def _search_row(row: sqlite3.Row, query: str) -> dict[str, Any]:
    item = dict(row)
    item["excerpt"] = _excerpt(str(item["content"]), query)
    content = _normalize_search(str(item.get("content") or ""))
    heading = _normalize_search(str(item.get("heading") or ""))
    metadata = _normalize_search(
        " ".join(
            str(item.get(key, ""))
            for key in ("source_title", "library_name", "library_domain")
        )
    )
    haystack = f"{heading} {content} {metadata}"
    terms = [_normalize_search(term) for term in _search_terms(query)]
    anchors = {_normalize_search(term) for term in _anchor_terms(query)}
    matched = {term for term in terms if term and term in haystack}
    anchor_matches = {term for term in anchors if term and term in haystack}
    heading_matches = {term for term in terms if term and term in heading}
    heading_anchor_matches = {term for term in anchors if term and term in heading}
    coverage = len(matched) / len(terms) if terms else 0.0
    relevance = (
        len(matched) * 2
        + len(anchor_matches) * 8
        + len(heading_matches) * 3
        + len(heading_anchor_matches) * 6
        + coverage * 4
    )
    item["matched_terms"] = len(matched)
    item["anchor_matches"] = len(anchor_matches)
    item["heading_matches"] = len(heading_matches)
    item["query_terms"] = len(terms)
    item["term_coverage"] = round(coverage, 3)
    item["relevance_score"] = round(relevance, 3)
    return item


def _excerpt(content: str, query: str, max_chars: int = 700) -> str:
    compact = content.strip()
    if len(compact) <= max_chars:
        return compact
    folded = compact.casefold()
    positions = [folded.find(term.casefold()) for term in query.split() if term.strip()]
    matches = [position for position in positions if position >= 0]
    center = min(matches) if matches else 0
    start = max(0, center - max_chars // 3)
    end = min(len(compact), start + max_chars)
    prefix = "..." if start else ""
    suffix = "..." if end < len(compact) else ""
    return prefix + compact[start:end].strip() + suffix


def _chunk_units(text: str, title: str) -> list[dict[str, str]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    normalized = _strip_front_matter(normalized)
    if not normalized:
        return []
    sections = _markdown_sections(normalized, title)
    if not sections:
        return _legacy_chunk_units(normalized, title)

    units: list[dict[str, str]] = []
    for heading, content in sections:
        for part in _split_section(content):
            clean = part.strip()
            if clean:
                units.append({"heading": heading, "content": clean})
    return units


def _markdown_sections(text: str, title: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading = title
    lines: list[str] = []
    found_heading = False

    def flush() -> None:
        nonlocal lines
        content = "\n".join(lines).strip()
        lines = []
        if content:
            sections.append((heading, content))

    for line in text.splitlines():
        match = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
        if not match:
            lines.append(line)
            continue
        flush()
        found_heading = True
        clean_heading = " ".join(match.group(1).split())
        heading = clean_heading[:240] if clean_heading else title
    flush()
    return sections if found_heading else []


def _split_section(content: str) -> list[str]:
    if len(content) <= _CHUNK_SIZE:
        return [content]
    paragraphs = re.split(r"\n{2,}", content)
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        clean = paragraph.strip()
        if not clean:
            continue
        candidate = f"{current}\n\n{clean}".strip() if current else clean
        if len(candidate) <= _CHUNK_SIZE:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(clean) <= _CHUNK_SIZE:
            current = clean
            continue
        long_parts = _legacy_chunk_units(clean, "")
        chunks.extend(part["content"] for part in long_parts[:-1])
        current = long_parts[-1]["content"] if long_parts else ""
    if current:
        chunks.append(current)
    return chunks


def _legacy_chunk_units(text: str, title: str) -> list[dict[str, str]]:
    units: list[dict[str, str]] = []
    start = 0
    total = len(text)
    while start < total:
        target_end = min(total, start + _CHUNK_SIZE)
        end = target_end
        if target_end < total:
            floor = start + _CHUNK_SIZE // 2
            candidates = [
                text.rfind("\n\n", floor, target_end),
                text.rfind("\n", floor, target_end),
                text.rfind(". ", floor, target_end),
                text.rfind(" ", floor, target_end),
            ]
            best = max(candidates)
            if best > start:
                end = best + (2 if text[best : best + 2] in {"\n\n", ". "} else 1)
        content = text[start:end].strip()
        if content:
            units.append({"heading": title, "content": content})
        if end >= total:
            break
        start = max(end - _CHUNK_OVERLAP, start + 1)
    return units


def _strip_front_matter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    return text[end + 5 :].lstrip() if end >= 0 else text


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "biblioteca"


def _unique_slug(database: Database, base: str) -> str:
    candidate = base
    counter = 2
    with database.connect() as connection:
        while connection.execute(
            "SELECT 1 FROM alexandria_libraries WHERE slug = ?", (candidate,)
        ).fetchone():
            candidate = f"{base}-{counter}"
            counter += 1
    return candidate


def _safe_filename(value: str) -> str:
    name = Path(value).name.strip()
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    if not name:
        raise ValueError("Nombre de archivo inválido.")
    return name[:180]


def _clean_label(value: str, fallback: str) -> str:
    clean = " ".join(value.strip().split())
    return clean or fallback


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _search_terms(query: str) -> list[str]:
    words: list[str] = []
    seen: set[str] = set()
    normalized_query = _normalize_search(query)
    expansions: list[str] = []
    if re.search(r"(?<!\w)php\s*-\s*l(?!\w)", normalized_query):
        expansions.extend(("lint", "sintaxis"))
    if "webhook" in normalized_query:
        expansions.extend(
            ("firma", "timestamp", "replay", "idempotencia", "monto", "moneda", "proveedor")
        )
    if "opcache" in normalized_query:
        expansions.extend(("bytecode", "compilado", "consultas", "algoritmos"))
    if "stock" in normalized_query:
        expansions.extend(("update", "atomico", "bloqueo", "filas", "afectadas"))
    for expansion in expansions:
        if expansion not in seen:
            seen.add(expansion)
            words.append(expansion)
    for raw in re.findall(r"[\w.]+", normalized_query, re.UNICODE):
        clean = raw.strip("._")
        if len(clean) < 3 or clean in _SEARCH_STOPWORDS or clean in seen:
            continue
        seen.add(clean)
        words.append(clean.replace('"', ""))
        for synonym in _SEARCH_SYNONYMS.get(clean, ()):
            if synonym not in seen:
                seen.add(synonym)
                words.append(synonym)
        if len(words) >= 12:
            break
    return words


def _anchor_terms(query: str) -> list[str]:
    normalized = _normalize_search(query)
    topic_terms: set[str] = set()
    if "webhook" in normalized:
        topic_terms.update(
            {
                "webhook",
                "firma",
                "timestamp",
                "replay",
                "idempotencia",
                "monto",
                "moneda",
                "proveedor",
                "pago",
            }
        )
    if "opcache" in normalized:
        topic_terms.update(
            {"opcache", "bytecode", "compilado", "consultas", "algoritmos"}
        )
    return [
        term
        for term in _search_terms(query)
        if term in _TECHNICAL_ANCHORS
        or term in topic_terms
        or "_" in term
        or "." in term
    ]


def _fts_query(query: str) -> str:
    words = _search_terms(query)
    if not words:
        words = [_normalize_search(query.replace('"', "").strip())]
    anchors = _anchor_terms(query)
    quoted = {word: f'"{word}"*' for word in words if word}
    if anchors:
        anchor_expression = " OR ".join(quoted[word] for word in anchors if word in quoted)
        return f"({anchor_expression})"
    return " OR ".join(quoted.values())


def _normalize_search(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _alexandria_fts_enabled(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key = 'alexandria_fts5'"
    ).fetchone()
    return bool(row and row[0] == "enabled")
