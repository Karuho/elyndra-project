from __future__ import annotations

import hashlib
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.db import Database
from elyndra.language_packs.constants import DATABASE_NAME
from elyndra.language_packs.manifest import load_manifest
from elyndra.language_packs.safety import file_sha256
from elyndra.paths import ElyndraPaths


class LanguagePackRegistry:
    def __init__(self, database: Database, root_paths: ElyndraPaths) -> None:
        if database.role != "root":
            raise RuntimeError("El registro de packs requiere database_role=root.")
        self.database = database
        self.paths = root_paths
        self.storage_root = root_paths.language_packs_dir

    def inspect(self, path: Path) -> dict[str, Any]:
        expanded = path.expanduser()
        if expanded.is_symlink():
            raise ValueError("El pack no puede ser un enlace simbólico.")
        root = expanded.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("El pack debe ser un directorio.")
        manifest, raw = load_manifest(root)
        database_path = root / DATABASE_NAME
        if database_path.is_symlink() or not database_path.is_file():
            raise ValueError("Falta lexicon.sqlite como archivo regular.")
        observed = file_sha256(database_path)
        if observed != manifest["database_sha256"]:
            raise ValueError("database_sha256 no coincide.")
        self._verify_database(database_path, str(manifest["content_sha256"]))
        for source in manifest["sources"]:
            license_path = root / str(source["license_text_path"])
            resolved = license_path.resolve(strict=True)
            if root not in resolved.parents or resolved.is_symlink() or not resolved.is_file():
                raise ValueError("Ruta de licencia inválida.")
            if not resolved.read_text(encoding="utf-8").strip():
                raise ValueError("Texto de licencia ausente.")
            if file_sha256(resolved) != source["license_sha256"]:
                raise ValueError("El hash del texto de licencia no coincide.")
        return manifest | {
            "package_root": str(root),
            "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "verified": True,
            "network_used": False,
        }

    def install(self, path: Path, *, actor: str, query_priority: int = 100) -> dict[str, Any]:
        if not 0 <= query_priority <= 1000:
            raise ValueError("query_priority debe estar entre 0 y 1000.")
        item = self.inspect(path)
        relpath = (
            Path("es")
            / str(item["pack_id"])
            / (f"{item['version']}-{item['manifest_sha256'][:12]}")
        )
        final = self.storage_root / relpath
        if final.exists():
            existing = self.get_by_identity(
                str(item["pack_id"]), str(item["version"]), str(item["manifest_sha256"])
            )
            if existing is not None:
                return existing | {"install_status": "unchanged"}
            raise ValueError("El destino del pack ya existe sin registro coincidente.")
        temp = final.parent / f".install-{uuid.uuid4().hex}"
        temp.mkdir(parents=True, mode=0o700)
        try:
            for source in Path(str(item["package_root"])).iterdir():
                if source.is_symlink():
                    raise ValueError("El pack no puede contener enlaces simbólicos.")
                target = temp / source.name
                if source.is_dir():
                    shutil.copytree(source, target, symlinks=False)
                else:
                    shutil.copy2(source, target)
                    target.chmod(0o600)
            final.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temp.replace(final)
            now = _now()
            counts = item.get("counts", {})
            with self.database.connect() as connection:
                cursor = connection.execute(
                    """INSERT INTO alexandria_language_packs(
                    public_id,logical_pack_id,language,locale,version,pack_schema_version,status,
                    query_priority,storage_relpath,manifest_sha256,database_sha256,content_sha256,
                    source_count,lexeme_count,form_count,sense_count,synset_count,builder_version,
                    verification_status,verified_at,installed_by,created_at,installed_at,updated_at)
                    VALUES(?,?,?,?,?,1,'disabled',?,?,?,?,?,?,?,?,?,?,?,'verified',?,?,?,?,?)""",
                    (
                        uuid.uuid4().hex,
                        item["pack_id"],
                        item["language"],
                        item["locale"],
                        item["version"],
                        query_priority,
                        relpath.as_posix(),
                        item["manifest_sha256"],
                        item["database_sha256"],
                        item["content_sha256"],
                        len(item["sources"]),
                        int(counts.get("lexemes", 0)),
                        int(counts.get("forms", 0)),
                        int(counts.get("senses", 0)),
                        int(counts.get("synsets", 0)),
                        item["builder_version"],
                        now,
                        actor[:120],
                        item["created_at"],
                        now,
                        now,
                    ),
                )
                pack_row_id = int(cursor.lastrowid)
                for source in item["sources"]:
                    connection.execute(
                        """INSERT INTO alexandria_language_pack_sources(
                        pack_id,source_id,title,source_version,source_date,source_url,input_filename,
                        original_sha256,license_id,license_text_path,attribution,
                        transformation_notes,imported_record_count,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            pack_row_id,
                            source["source_id"],
                            source["title"],
                            source["source_version"],
                            source.get("source_date", ""),
                            source["source_url"],
                            source["input_filename"],
                            source["original_sha256"],
                            source["license_id"],
                            source["license_text_path"],
                            source["attribution"],
                            source["transformation_notes"],
                            int(source.get("imported_record_count", 0)),
                            now,
                        ),
                    )
            result = self.get_by_identity(
                str(item["pack_id"]), str(item["version"]), str(item["manifest_sha256"])
            )
            if result is None:
                raise RuntimeError("No se pudo recuperar el pack instalado.")
            return result | {"install_status": "installed"}
        except Exception:
            shutil.rmtree(temp, ignore_errors=True)
            if (
                final.exists()
                and self.get_by_identity(
                    str(item["pack_id"]), str(item["version"]), str(item["manifest_sha256"])
                )
                is None
            ):
                shutil.rmtree(final, ignore_errors=True)
            raise

    def list_all(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE status='enabled' AND verification_status='verified'" if enabled_only else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM alexandria_language_packs {where} "
                "ORDER BY query_priority DESC, logical_pack_id, version, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM alexandria_language_packs WHERE public_id=?", (public_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_by_identity(
        self, logical: str, version: str, manifest_sha: str
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM alexandria_language_packs WHERE logical_pack_id=? AND version=? "
                "AND manifest_sha256=?",
                (logical, version, manifest_sha),
            ).fetchone()
        return dict(row) if row else None

    def set_enabled(self, public_id: str, *, enabled: bool) -> dict[str, Any]:
        item = self.get(public_id)
        if item is None:
            raise ValueError("Pack lingüístico no encontrado.")
        if enabled:
            self.verify(public_id)
        with self.database.connect() as connection:
            if enabled:
                connection.execute(
                    "UPDATE alexandria_language_packs SET status='disabled',updated_at=? "
                    "WHERE logical_pack_id=? AND status='enabled'",
                    (_now(), item["logical_pack_id"]),
                )
            connection.execute(
                "UPDATE alexandria_language_packs SET status=?,updated_at=? WHERE public_id=?",
                ("enabled" if enabled else "disabled", _now(), public_id),
            )
        return self.get(public_id) or {}

    def rollback_new_install(self, public_id: str) -> None:
        """Remove one exact registry entry created by the current failed operation."""
        item = self.get(public_id)
        if item is None:
            return
        target = self._storage_path(str(item["storage_relpath"]))
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM alexandria_language_pack_sources WHERE pack_id=?",
                (item["id"],),
            )
            connection.execute(
                "DELETE FROM alexandria_language_packs WHERE public_id=?", (public_id,)
            )
        shutil.rmtree(target, ignore_errors=True)

    def verify(self, public_id: str) -> dict[str, Any]:
        item = self.get(public_id)
        if item is None:
            raise ValueError("Pack lingüístico no encontrado.")
        root = self._storage_path(str(item["storage_relpath"]))
        try:
            inspected = self.inspect(root)
            if inspected["manifest_sha256"] != item["manifest_sha256"]:
                raise ValueError("El hash del manifiesto instalado cambió.")
        except Exception:
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE alexandria_language_packs SET status='invalid',"
                    "verification_status='failed',verified_at=?,updated_at=? WHERE public_id=?",
                    (_now(), _now(), public_id),
                )
            raise
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE alexandria_language_packs SET verification_status='verified',"
                "verified_at=?,updated_at=? WHERE public_id=?",
                (_now(), _now(), public_id),
            )
        return self.get(public_id) or {}

    def database_path(self, item: dict[str, Any]) -> Path:
        return self._storage_path(str(item["storage_relpath"])) / DATABASE_NAME

    def _storage_path(self, relpath: str) -> Path:
        relative = Path(relpath)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("storage_relpath inválido.")
        resolved = (self.storage_root / relative).resolve()
        root = self.storage_root.resolve()
        if root not in resolved.parents:
            raise ValueError("storage_relpath escapa del almacén compartido.")
        return resolved

    @staticmethod
    def _verify_database(path: Path, content_sha256: str) -> None:
        uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("El SQLite del pack está corrupto.")
            row = connection.execute(
                "SELECT value FROM pack_meta WHERE key='content_sha256'"
            ).fetchone()
            if row is None or row[0] != content_sha256:
                raise ValueError("content_sha256 no coincide con pack_meta.")
        except sqlite3.DatabaseError as exc:
            raise ValueError("El SQLite del pack es inválido.") from exc
        finally:
            connection.close()


def _now() -> str:
    return datetime.now(UTC).isoformat()
