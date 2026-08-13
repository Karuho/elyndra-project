from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.alexandria.repository import AlexandriaRepository
from elyndra.db import Database

_MANIFEST_NAME = "elyndra-package.json"
_PACKAGE_ID = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_MAX_MANIFEST_BYTES = 256_000
_MAX_SOURCES = 64
_MAX_TOTAL_SOURCE_BYTES = 64 * 1024 * 1024


class AlexandriaPackageRepository:
    """Install manifest-based local knowledge packages without network access."""

    def __init__(
        self,
        database: Database,
        alexandria: AlexandriaRepository,
    ) -> None:
        self.database = database
        self.alexandria = alexandria

    def inspect(self, path: Path) -> dict[str, Any]:
        root = path.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError(f"El paquete debe ser una carpeta existente: {root}")
        manifest_path = root / _MANIFEST_NAME
        if not manifest_path.is_file():
            raise ValueError(f"Falta {_MANIFEST_NAME} en {root}")
        if manifest_path.is_symlink():
            raise ValueError("El manifiesto del paquete no puede ser un enlace simbólico.")
        if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise ValueError("El manifiesto supera el límite local de 256 KB.")
        raw = manifest_path.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"El manifiesto no es JSON UTF-8 válido: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("El manifiesto debe contener un objeto JSON.")
        manifest = _validate_manifest(payload)
        sources: list[dict[str, Any]] = []
        total_bytes = 0
        for item in manifest["sources"]:
            source = _resolve_source(root, item)
            total_bytes += source["size_bytes"]
            if total_bytes > _MAX_TOTAL_SOURCE_BYTES:
                raise ValueError("El paquete supera el límite total de 64 MiB.")
            sources.append(source)
        digest = hashlib.sha256(raw).hexdigest()
        return {
            **manifest,
            "package_root": str(root),
            "manifest_path": str(manifest_path),
            "manifest_sha256": digest,
            "source_count": len(sources),
            "total_size_bytes": total_bytes,
            "resolved_sources": sources,
            "network_used": False,
            "execution_performed": False,
        }

    def install(self, path: Path, *, actor: str) -> dict[str, Any]:
        package = self.inspect(path)
        existing = self.get(package["package_id"])
        if existing is not None:
            if (
                existing["version"] == package["version"]
                and existing["manifest_sha256"] == package["manifest_sha256"]
            ):
                return existing | {"install_status": "unchanged"}
            raise ValueError(
                "Ya existe otra versión de este paquete. Elimínala antes de reemplazarla."
            )
        library = self.alexandria.create_library(
            package["name"],
            description=package["description"],
            domain=package["domain"],
            language=package["language"],
            version=package["version"],
            license_id=package["license_id"],
        )
        imported: list[dict[str, Any]] = []
        try:
            for source in package["resolved_sources"]:
                imported.append(
                    self.alexandria.import_file(
                        int(library["id"]),
                        Path(source["path"]),
                        title=source["title"],
                        source_url=source["source_url"],
                    )
                )
            now = datetime.now(UTC).isoformat()
            metadata = {
                "publisher": package["publisher"],
                "tags": package["tags"],
                "source_count": package["source_count"],
                "total_size_bytes": package["total_size_bytes"],
                "description": package["description"],
            }
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO alexandria_packages(
                        package_id,
                        name,
                        version,
                        tier,
                        domain,
                        language,
                        license_id,
                        manifest_sha256,
                        package_root,
                        library_id,
                        enabled,
                        actor,
                        metadata_json,
                        installed_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                    """,
                    (
                        package["package_id"],
                        package["name"],
                        package["version"],
                        package["tier"],
                        package["domain"],
                        package["language"],
                        package["license_id"],
                        package["manifest_sha256"],
                        package["package_root"],
                        int(library["id"]),
                        actor,
                        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                        now,
                        now,
                    ),
                )
        except Exception:
            self.alexandria.delete_library(int(library["id"]))
            raise
        item = self.get(package["package_id"])
        if item is None:
            raise RuntimeError("No se pudo recuperar el paquete instalado.")
        return item | {
            "install_status": "installed",
            "imported_sources": len(imported),
            "sources_reviewed": False,
        }


    def create(
        self,
        destination: Path,
        *,
        package_id: str,
        name: str,
        version: str,
        tier: str,
        domain: str,
        language: str,
        license_id: str,
        source_paths: list[Path],
        description: str = "",
        publisher: str = "unverified",
        tags: list[str] | None = None,
        source_titles: list[str] | None = None,
        source_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        if not 1 <= len(source_paths) <= _MAX_SOURCES:
            raise ValueError(f"Debes incluir entre 1 y {_MAX_SOURCES} fuentes.")
        root = destination.expanduser().resolve(strict=False)
        if root.exists() and any(root.iterdir()):
            raise ValueError(f"La carpeta de destino no está vacía: {root}")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        sources_root = root / "sources"
        sources_root.mkdir(mode=0o700)
        clean_id = str(package_id).strip().casefold()
        if not _PACKAGE_ID.fullmatch(clean_id):
            raise ValueError("package_id no cumple el formato permitido.")
        titles = source_titles or []
        urls = source_urls or []
        manifest_sources: list[dict[str, str]] = []
        total_bytes = 0
        used_names: set[str] = set()
        for index, raw_path in enumerate(source_paths, start=1):
            candidate = raw_path.expanduser()
            if candidate.is_symlink():
                raise ValueError(f"La fuente debe ser un archivo regular: {candidate}")
            source = candidate.resolve(strict=True)
            if not source.is_file():
                raise ValueError(f"La fuente debe ser un archivo regular: {source}")
            total_bytes += source.stat().st_size
            if total_bytes > _MAX_TOTAL_SOURCE_BYTES:
                raise ValueError("El paquete supera el límite total de 64 MiB.")
            filename = _unique_source_name(source.name, index, used_names)
            target = sources_root / filename
            shutil.copyfile(source, target)
            target.chmod(0o600)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            title = titles[index - 1].strip() if index <= len(titles) else source.stem
            manifest_sources.append(
                {
                    "path": f"sources/{filename}",
                    "title": title or source.stem,
                    "sha256": digest,
                    "source_url": urls[index - 1].strip() if index <= len(urls) else "",
                }
            )
        manifest = {
            "schema_version": 1,
            "package_id": clean_id,
            "name": str(name).strip(),
            "version": str(version).strip(),
            "tier": str(tier).strip().casefold(),
            "domain": str(domain).strip(),
            "language": str(language).strip(),
            "license_id": str(license_id).strip(),
            "description": str(description).strip(),
            "publisher": str(publisher).strip() or "unverified",
            "tags": [str(item).strip() for item in (tags or []) if str(item).strip()],
            "sources": manifest_sources,
        }
        _validate_manifest(manifest)
        manifest_path = root / _MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_path.chmod(0o600)
        return self.inspect(root) | {"creation_status": "created"}

    def export(self, package_id: str, destination: Path) -> dict[str, Any]:
        item = self.get(package_id)
        if item is None:
            raise ValueError(f"Paquete de Alejandría no encontrado: {package_id}")
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT title, stored_path, source_url
                FROM alexandria_sources
                WHERE library_id = ? AND status = 'active'
                ORDER BY id
                """,
                (int(item["library_id"]),),
            ).fetchall()
        if not rows:
            raise ValueError("El paquete instalado no contiene fuentes activas.")
        metadata = item.get("metadata", {})
        created = self.create(
            destination,
            package_id=item["package_id"],
            name=item["name"],
            version=item["version"],
            tier=item["tier"],
            domain=item["domain"],
            language=item["language"],
            license_id=item["license_id"],
            source_paths=[Path(str(row["stored_path"])) for row in rows],
            source_titles=[str(row["title"]) for row in rows],
            source_urls=[str(row["source_url"] or "") for row in rows],
            description=str(metadata.get("description") or ""),
            publisher=str(metadata.get("publisher") or "unverified"),
            tags=[str(tag) for tag in metadata.get("tags", [])],
        )
        return created | {"exported_package_id": item["package_id"]}

    def get(self, package_id: str) -> dict[str, Any] | None:
        clean = package_id.strip().casefold()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT p.*, l.public_id AS library_public_id,
                       l.enabled AS library_enabled,
                       l.source_count AS unavailable_source_count
                FROM alexandria_packages p
                JOIN (
                    SELECT l.*, COUNT(s.id) AS source_count
                    FROM alexandria_libraries l
                    LEFT JOIN alexandria_sources s
                      ON s.library_id = l.id AND s.status = 'active'
                    GROUP BY l.id
                ) l ON l.id = p.library_id
                WHERE p.package_id = ?
                """,
                (clean,),
            ).fetchone()
        return _public_package(dict(row)) if row is not None else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT p.*, l.public_id AS library_public_id,
                       l.enabled AS library_enabled,
                       COUNT(s.id) AS source_count
                FROM alexandria_packages p
                JOIN alexandria_libraries l ON l.id = p.library_id
                LEFT JOIN alexandria_sources s
                  ON s.library_id = l.id AND s.status = 'active'
                GROUP BY p.id
                ORDER BY p.tier, p.name COLLATE NOCASE
                """
            ).fetchall()
        return [_public_package(dict(row)) for row in rows]

    def set_enabled(self, package_id: str, *, enabled: bool) -> dict[str, Any]:
        item = self.get(package_id)
        if item is None:
            raise ValueError(f"Paquete de Alejandría no encontrado: {package_id}")
        library = self.alexandria.update_library(
            int(item["library_id"]),
            enabled=enabled,
        )
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE alexandria_packages SET enabled = ?, updated_at = ? WHERE id = ?",
                (1 if enabled else 0, now, int(item["id"])),
            )
        updated = self.get(package_id)
        if updated is None:
            raise RuntimeError("No se pudo recuperar el paquete actualizado.")
        return updated | {"library": library}

    def remove(self, package_id: str) -> dict[str, Any]:
        item = self.get(package_id)
        if item is None:
            raise ValueError(f"Paquete de Alejandría no encontrado: {package_id}")
        removed_library = self.alexandria.delete_library(int(item["library_id"]))
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM alexandria_packages WHERE id = ?",
                (int(item["id"]),),
            )
        return {
            "package_id": item["package_id"],
            "name": item["name"],
            "version": item["version"],
            "removed_library": removed_library,
        }


def _unique_source_name(name: str, index: int, used: set[str]) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).name).strip(".-")
    if not clean:
        clean = f"source-{index}.txt"
    candidate = clean
    stem = Path(clean).stem or f"source-{index}"
    suffix = Path(clean).suffix
    counter = 2
    while candidate.casefold() in used:
        candidate = f"{stem}-{counter}{suffix}"
        counter += 1
    used.add(candidate.casefold())
    return candidate


def _validate_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise ValueError("schema_version debe ser exactamente 1.")
    package_id = str(payload.get("package_id") or "").strip().casefold()
    if not _PACKAGE_ID.fullmatch(package_id):
        raise ValueError("package_id debe usar minúsculas, números, puntos, guiones o guion bajo.")
    name = _required_text(payload, "name", 120)
    version = _required_text(payload, "version", 80)
    domain = _required_text(payload, "domain", 120)
    language = _required_text(payload, "language", 30)
    license_id = _required_text(payload, "license_id", 120)
    tier = str(payload.get("tier") or "optional").strip().casefold()
    if tier not in {"basic", "optional"}:
        raise ValueError("tier debe ser basic u optional.")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= _MAX_SOURCES:
        raise ValueError(f"sources debe contener entre 1 y {_MAX_SOURCES} entradas.")
    clean_sources: list[dict[str, str]] = []
    for item in sources:
        if not isinstance(item, dict):
            raise ValueError("Cada fuente del paquete debe ser un objeto JSON.")
        clean_sources.append(
            {
                "path": _required_text(item, "path", 240),
                "title": _required_text(item, "title", 180),
                "sha256": _sha256_text(item.get("sha256")),
                "source_url": str(item.get("source_url") or "").strip()[:1000],
            }
        )
    tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
    return {
        "schema_version": 1,
        "package_id": package_id,
        "name": name,
        "version": version,
        "tier": tier,
        "domain": domain,
        "language": language,
        "license_id": license_id,
        "description": str(payload.get("description") or "").strip()[:1000],
        "publisher": str(payload.get("publisher") or "unverified").strip()[:120],
        "tags": [str(item).strip()[:60] for item in tags[:20] if str(item).strip()],
        "sources": clean_sources,
    }


def _resolve_source(root: Path, item: dict[str, str]) -> dict[str, Any]:
    relative = Path(item["path"])
    if relative.is_absolute():
        raise ValueError("Las fuentes del paquete deben usar rutas relativas.")
    source_path = root / relative
    if source_path.is_symlink():
        raise ValueError("Las fuentes del paquete no pueden ser enlaces simbólicos.")
    candidate = source_path.resolve(strict=True)
    if candidate != root and root not in candidate.parents:
        raise ValueError("Una fuente intenta salir de la carpeta del paquete.")
    if not candidate.is_file():
        raise ValueError(f"La fuente no es un archivo regular: {candidate}")
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if digest != item["sha256"]:
        raise ValueError(f"SHA-256 incorrecto para {relative.as_posix()}.")
    return {
        **item,
        "path": str(candidate),
        "relative_path": relative.as_posix(),
        "size_bytes": candidate.stat().st_size,
    }


def _required_text(payload: dict[str, Any], name: str, limit: int) -> str:
    value = str(payload.get(name) or "").strip()
    if not value:
        raise ValueError(f"Falta el campo obligatorio {name}.")
    if len(value) > limit:
        raise ValueError(f"{name} supera {limit} caracteres.")
    return value


def _sha256_text(value: Any) -> str:
    clean = str(value or "").strip().casefold()
    if len(clean) != 64 or any(char not in "0123456789abcdef" for char in clean):
        raise ValueError("Cada fuente debe declarar un SHA-256 hexadecimal válido.")
    return clean


def _public_package(item: dict[str, Any]) -> dict[str, Any]:
    item["enabled"] = bool(item.get("enabled"))
    item["library_enabled"] = bool(item.get("library_enabled"))
    try:
        metadata = json.loads(str(item.pop("metadata_json", "{}")))
    except json.JSONDecodeError:
        metadata = {}
    item["metadata"] = metadata if isinstance(metadata, dict) else {}
    if "unavailable_source_count" in item and "source_count" not in item:
        item["source_count"] = int(item.pop("unavailable_source_count") or 0)
    return item
