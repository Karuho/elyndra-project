from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

from elyndra import __version__
from elyndra.language_packs.registry import LanguagePackRegistry
from elyndra.language_packs.safety import file_sha256, regular_file

BUNDLE_MANIFEST_NAME = "elyndra-language-bundle.json"
DEFAULT_PART_BYTES = int(1.8 * 1024**3)
MAX_MEMBERS = 128
MAX_UNPACKED_BYTES = 64 * 1024**3
MAX_MEMBER_BYTES = 8 * 1024**3
MAX_MEMBER_NAME = 240
MAX_MEMBER_DEPTH = 12
MAX_COMPRESSION_RATIO = 200
DEFAULT_QUERY_PRIORITIES = {
    "elyndra-es-informal": 400,
    "elyndra-es-wiktionary": 300,
    "elyndra-es-mcr-omw": 250,
    "elyndra-es-cldr": 200,
}


class LanguageBundleService:
    def __init__(
        self,
        registry: LanguagePackRegistry | None = None,
        *,
        work_root: Path | None = None,
    ) -> None:
        self.registry = registry
        self.work_root = work_root

    def _temporary(self, prefix: str) -> Path:
        if self.work_root is not None:
            self.work_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.work_root.chmod(0o700)
        return Path(tempfile.mkdtemp(prefix=prefix, dir=self.work_root))

    def create(
        self,
        *,
        pack_specs: list[dict[str, Any]],
        output_dir: Path,
        bundle_id: str = "elyndra-es-core",
        bundle_version: str = "2026.08.01-r1",
        build_epoch: int,
        part_bytes: int = DEFAULT_PART_BYTES,
    ) -> dict[str, Any]:
        if len(pack_specs) != 4 or part_bytes < 1024:
            raise ValueError("El bundle requiere cuatro packs y un límite de parte seguro.")
        output = output_dir.expanduser().resolve()
        if output.exists():
            raise ValueError("El directorio de export ya existe.")
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = Path(tempfile.mkdtemp(prefix=".language-bundle-", dir=output.parent))
        temporary.chmod(0o700)
        try:
            packs: list[dict[str, Any]] = []
            assets: list[dict[str, Any]] = []
            total_unpacked = 0
            for spec in pack_specs:
                root = Path(str(spec["path"])).expanduser().resolve(strict=True)
                if self.registry is None:
                    raise RuntimeError("Crear bundles requiere un registro para verificar packs.")
                inspected = self.registry.inspect(root)
                archive_name = f"{inspected['pack_id']}-{inspected['version']}.tar.gz"
                archive = temporary / archive_name
                self._write_archive(root, archive, build_epoch)
                parts = self._split(archive, part_bytes)
                unpacked = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
                total_unpacked += unpacked
                asset = {
                    "name": archive_name,
                    "sha256": file_sha256(archive),
                    "size": archive.stat().st_size,
                    "parts": parts,
                }
                assets.append(asset)
                licenses = sorted(
                    {str(source["license_id"]) for source in inspected["sources"]}
                )
                packs.append({
                    "logical_pack_id": inspected["pack_id"],
                    "version": inspected["version"],
                    "query_priority": int(spec.get(
                        "query_priority", DEFAULT_QUERY_PRIORITIES.get(inspected["pack_id"], 100)
                    )),
                    "required": bool(spec.get("required", True)),
                    "manifest_sha256": inspected["manifest_sha256"],
                    "database_sha256": inspected["database_sha256"],
                    "content_sha256": inspected["content_sha256"],
                    "unpacked_size": unpacked,
                    "archive_name": archive_name,
                    "archive_sha256": asset["sha256"],
                    "archive_size": asset["size"],
                    "chunks": parts,
                    "license_ids": licenses,
                    "attribution": list(dict.fromkeys(
                        str(source["attribution"]) for source in inspected["sources"]
                    )),
                })
            payload: dict[str, Any] = {
                "schema": 1, "bundle_id": bundle_id, "bundle_version": bundle_version,
                "language": "es", "locale": "es",
                "created_at": _epoch_iso(build_epoch),
                "minimum_elyndra_version": "0.8.8a0",
                "maximum_elyndra_version_exclusive": "0.9.0a0",
                "pack_schema_versions": [1], "total_unpacked_size": total_unpacked,
                "recommended_free_space": total_unpacked * 2,
                "description": "Núcleo léxico español desmontable para Elyndra.",
                "packs": packs, "assets": assets,
                "licenses": sorted({item for pack in packs for item in pack["license_ids"]}),
                "attribution": list(dict.fromkeys(
                    item for pack in packs for item in pack["attribution"]
                )),
            }
            payload["bundle_content_sha256"] = _canonical_hash(payload)
            manifest = temporary / BUNDLE_MANIFEST_NAME
            manifest.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            manifest.chmod(0o600)
            alias = temporary / f"{bundle_id}-{bundle_version}.bundle.json"
            shutil.copyfile(manifest, alias)
            sums = temporary / "SHA256SUMS"
            hashed = [manifest, alias, *(temporary / item["name"] for item in assets)]
            hashed.extend(
                temporary / part["name"] for item in assets for part in item["parts"]
            )
            sums.write_text(
                "".join(f"{file_sha256(path)}  {path.name}\n" for path in hashed),
                encoding="utf-8",
            )
            temporary.replace(output)
            return payload | {"path": str(output), "network_used": False}
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def inspect(self, manifest_path: Path, *, verify_archives: bool = True) -> dict[str, Any]:
        path = regular_file(manifest_path, max_bytes=512 * 1024)
        payload = json.loads(path.read_text(encoding="utf-8"))
        _validate_manifest(payload)
        if payload["bundle_content_sha256"] != _canonical_hash(payload):
            raise ValueError("bundle_content_sha256 no coincide.")
        _check_compatibility(payload)
        if verify_archives:
            for asset in payload["assets"]:
                archive, temporary = self._verify_asset(path.parent, asset)
                try:
                    self._inspect_archive(archive)
                    if self.registry is not None:
                        pack = next(
                            item
                            for item in payload["packs"]
                            if item["archive_name"] == asset["name"]
                        )
                        extracted_root = self._temporary("elyndra-bundle-inspect-")
                        try:
                            self._extract_archive(archive, extracted_root)
                            inspected = self.registry.inspect(extracted_root)
                            for field in (
                                "manifest_sha256", "database_sha256", "content_sha256"
                            ):
                                if inspected[field] != pack[field]:
                                    raise ValueError(
                                        f"El pack del bundle no coincide en {field}."
                                    )
                        finally:
                            shutil.rmtree(extracted_root, ignore_errors=True)
                finally:
                    if temporary:
                        archive.unlink(missing_ok=True)
        return payload | {"manifest_path": str(path), "verified": True, "network_used": False}

    def install(
        self, manifest_path: Path, *, actor: str, enable: bool = False
    ) -> dict[str, Any]:
        if self.registry is None:
            raise RuntimeError("Instalar bundles requiere registro de packs.")
        bundle = self.inspect(manifest_path)
        root = Path(str(bundle["manifest_path"])).parent
        needed = int(bundle["recommended_free_space"])
        if shutil.disk_usage(self.registry.storage_root.parent).free < needed:
            raise ValueError("Espacio libre insuficiente para instalar el bundle.")
        extracted: list[tuple[dict[str, Any], Path]] = []
        temporary = self._temporary("elyndra-bundle-install-")
        installed_new: list[dict[str, Any]] = []
        installed: list[dict[str, Any]] = []
        previous_enabled = {
            str(item["logical_pack_id"]): str(item["public_id"])
            for item in self.registry.list_all(enabled_only=True)
        }
        try:
            for pack in bundle["packs"]:
                asset = next(
                    item
                    for item in bundle["assets"]
                    if item["name"] == pack["archive_name"]
                )
                archive, reconstructed = self._verify_asset(root, asset)
                target = temporary / str(pack["logical_pack_id"])
                target.mkdir(mode=0o700)
                try:
                    self._extract_archive(archive, target)
                finally:
                    if reconstructed:
                        archive.unlink(missing_ok=True)
                inspected = self.registry.inspect(target)
                for field in ("manifest_sha256", "database_sha256", "content_sha256"):
                    if inspected[field] != pack[field]:
                        raise ValueError(f"El pack extraído no coincide en {field}.")
                extracted.append((pack, target))
            for pack, target in extracted:
                item = self.registry.install(
                    target, actor=actor, query_priority=int(pack["query_priority"])
                )
                installed.append(item)
                if item.get("install_status") == "installed":
                    installed_new.append(item)
            if enable:
                for item in installed:
                    self.registry.set_enabled(item["public_id"], enabled=True)
            return bundle | {"installed": installed, "enabled": enable, "network_used": False}
        except Exception:
            for item in installed:
                if item.get("install_status") != "installed":
                    self.registry.set_enabled(item["public_id"], enabled=False)
            for public_id in previous_enabled.values():
                if self.registry.get(public_id) is not None:
                    self.registry.set_enabled(public_id, enabled=True)
            for item in reversed(installed_new):
                self.registry.rollback_new_install(item["public_id"])
            raise
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    @staticmethod
    def _write_archive(root: Path, destination: Path, epoch: int) -> None:
        with (
            destination.open("wb") as raw,
            gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed,
            tarfile.open(fileobj=compressed, mode="w") as archive,
        ):
                    for path in sorted(
                        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
                    ):
                        if path.is_symlink() or not (path.is_file() or path.is_dir()):
                            raise ValueError("El pack contiene un tipo de archivo no permitido.")
                        relative = path.relative_to(root).as_posix()
                        info = archive.gettarinfo(str(path), arcname=relative)
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        info.mtime = epoch
                        info.mode = 0o700 if path.is_dir() else 0o600
                        if path.is_file():
                            with path.open("rb") as handle:
                                archive.addfile(info, handle)
                        else:
                            archive.addfile(info)

    @staticmethod
    def _split(archive: Path, part_bytes: int) -> list[dict[str, Any]]:
        if archive.stat().st_size <= part_bytes:
            return []
        parts: list[dict[str, Any]] = []
        with archive.open("rb") as source:
            order = 1
            while chunk := source.read(part_bytes):
                path = archive.with_name(f"{archive.name}.part-{order:03d}")
                path.write_bytes(chunk)
                parts.append({
                    "order": order, "name": path.name, "size": len(chunk),
                    "sha256": hashlib.sha256(chunk).hexdigest(),
                })
                order += 1
        return parts

    def _verify_asset(self, root: Path, asset: dict[str, Any]) -> tuple[Path, bool]:
        archive = root / str(asset["name"])
        parts = asset.get("parts", [])
        if parts:
            descriptor, name = tempfile.mkstemp(
                prefix="elyndra-reassembled-", dir=self.work_root
            )
            os.close(descriptor)
            reconstructed = Path(name)
            try:
                with reconstructed.open("wb") as output:
                    for expected_order, part in enumerate(parts, 1):
                        if int(part["order"]) != expected_order:
                            raise ValueError("Orden de partes inválido.")
                        path = regular_file(root / str(part["name"]), max_bytes=DEFAULT_PART_BYTES)
                        if (
                            path.stat().st_size != int(part["size"])
                            or file_sha256(path) != part["sha256"]
                        ):
                            raise ValueError("Parte corrupta o incompleta.")
                        with path.open("rb") as source:
                            shutil.copyfileobj(source, output, 1024 * 1024)
                archive = reconstructed
            except Exception:
                reconstructed.unlink(missing_ok=True)
                raise
        else:
            archive = regular_file(archive, max_bytes=MAX_UNPACKED_BYTES)
        if archive.stat().st_size != int(asset["size"]) or file_sha256(archive) != asset["sha256"]:
            if parts:
                archive.unlink(missing_ok=True)
            raise ValueError("Archive corrupto o con hash incorrecto.")
        return archive, bool(parts)

    @staticmethod
    def _inspect_archive(path: Path) -> None:
        with path.open("rb") as handle:
            if handle.read(2) != b"\x1f\x8b":
                raise ValueError("Archive con magic bytes inválidos.")
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > MAX_MEMBERS:
                raise ValueError("Archive con demasiados miembros.")
            total = 0
            exact_names: set[str] = set()
            folded_names: set[str] = set()
            normalized_names: set[str] = set()
            for member in members:
                pure = PurePosixPath(member.name)
                parts = pure.parts
                if (
                    pure.is_absolute()
                    or not parts
                    or any(part in {"", ".", ".."} for part in parts)
                    or len(parts) > MAX_MEMBER_DEPTH
                    or len(member.name) > MAX_MEMBER_NAME
                    or member.issym()
                    or member.islnk()
                ):
                    raise ValueError("Archive con ruta o enlace no permitido.")
                if not (member.isfile() or member.isdir()):
                    raise ValueError("Archive con tipo de miembro no permitido.")
                normalized = unicodedata.normalize("NFC", member.name)
                folded = normalized.casefold()
                if (
                    member.name in exact_names
                    or folded in folded_names
                    or normalized in normalized_names
                ):
                    raise ValueError("Archive con nombres duplicados o ambiguos.")
                exact_names.add(member.name)
                folded_names.add(folded)
                normalized_names.add(normalized)
                if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    raise ValueError("Archive contiene un miembro demasiado grande.")
                if member.isfile() and member.name.casefold().endswith(
                    (".tar", ".tar.gz", ".tgz", ".zip", ".whl", ".exe", ".dll", ".so")
                ):
                    raise ValueError("Archive contiene un archivo anidado peligroso.")
                total += member.size
                if total > MAX_UNPACKED_BYTES:
                    raise ValueError("Archive supera el tamaño descomprimido permitido.")
            compressed = max(1, path.stat().st_size)
            if total > compressed * MAX_COMPRESSION_RATIO:
                raise ValueError("Archive supera el ratio de compresión permitido.")

    @classmethod
    def _extract_archive(cls, path: Path, target: Path) -> None:
        cls._inspect_archive(path)
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                destination = target / member.name
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError("Miembro regular ilegible.")
                    with destination.open("wb") as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
                    destination.chmod(0o600)


def _canonical_hash(payload: dict[str, Any]) -> str:
    canonical = {key: value for key, value in payload.items() if key != "bundle_content_sha256"}
    return hashlib.sha256(json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def _validate_manifest(payload: Any) -> None:
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise ValueError("Manifest de bundle schema 1 inválido.")
    required = (
        "bundle_id", "bundle_version", "language", "locale", "created_at",
        "minimum_elyndra_version", "maximum_elyndra_version_exclusive",
        "pack_schema_versions", "total_unpacked_size", "recommended_free_space",
        "description", "packs", "assets", "licenses", "attribution",
        "bundle_content_sha256",
    )
    if (
        set(payload) != {"schema", *required}
        or len(payload.get("packs", [])) != 4
        or len(payload.get("assets", [])) != 4
    ):
        raise ValueError("Manifest de bundle incompleto.")


def _pep440_key(value: str) -> tuple[int, int, int, int, int]:
    import re
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?", value)
    if match is None:
        raise ValueError(f"Versión PEP 440 no soportada: {value}")
    phase = {None: 3, "rc": 2, "b": 1, "a": 0}[match.group(4)]
    return tuple(map(int, match.group(1, 2, 3))) + (phase, int(match.group(5) or 0))


def _check_compatibility(payload: dict[str, Any]) -> None:
    current = _pep440_key(__version__.replace("-alpha", "a0"))
    if not (
        _pep440_key(str(payload["minimum_elyndra_version"])) <= current
        < _pep440_key(str(payload["maximum_elyndra_version_exclusive"]))
    ):
        raise ValueError("Bundle incompatible con esta versión de Elyndra.")


def _epoch_iso(epoch: int) -> str:
    from datetime import UTC, datetime
    return datetime.fromtimestamp(epoch, UTC).isoformat()
