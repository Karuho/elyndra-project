from __future__ import annotations

import hashlib
import io
import json
import socket
import tarfile
from pathlib import Path
from typing import cast

import pytest

from elyndra.audit import AuditRepository
from elyndra.db import Database
from elyndra.language_packs.bundles import LanguageBundleService
from elyndra.language_packs.registry import LanguagePackRegistry
from elyndra.online_gateway.acquisition import (
    AcquisitionDescriptor,
    ValidatedBundleManifest,
    validate_downloaded_manifest,
)
from elyndra.online_gateway.audit import GatewayAudit
from elyndra.online_gateway.bundle_pipeline import SupervisedBundlePipeline
from elyndra.online_gateway.errors import GatewayError
from elyndra.online_gateway.models import RemoteArtifactDescriptor
from elyndra.online_gateway.storage import GatewayStorage
from elyndra.paths import ElyndraPaths


@pytest.fixture(autouse=True)
def _deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("Phase 4 tests must not use the network")

    monkeypatch.setattr(socket, "create_connection", deny)


def _manifest() -> dict[str, object]:
    assets = [
        {"name": f"pack-{index}.tar.gz", "size": index + 1, "sha256": f"{index + 1:064x}"}
        for index in range(4)
    ]
    packs = [
        {
            "logical_pack_id": f"pack-{index}",
            "archive_name": assets[index]["name"],
        }
        for index in range(4)
    ]
    payload: dict[str, object] = {
        "schema": 1,
        "bundle_id": "elyndra-es-core",
        "bundle_version": "2026.08.01-r1",
        "language": "es",
        "locale": "es",
        "created_at": "2026-08-01T00:00:00+00:00",
        "minimum_elyndra_version": "0.8.8a0",
        "maximum_elyndra_version_exclusive": "0.9.0a0",
        "pack_schema_versions": [1],
        "total_unpacked_size": 10,
        "recommended_free_space": 20,
        "description": "Synthetic",
        "packs": packs,
        "assets": assets,
        "licenses": ["LicenseRef-Synthetic"],
        "attribution": ["Synthetic fixture"],
    }
    from elyndra.language_packs.bundles import _canonical_hash

    payload["bundle_content_sha256"] = _canonical_hash(payload)
    return payload


def _descriptor(raw: bytes, payload: dict[str, object]) -> AcquisitionDescriptor:
    source = {
        "source_id": "elyndra-official-language-packs",
        "trust_class": "official-pinned",
        "display_name": "Official",
        "repository": "Karuho/elyndra-language-packs",
        "release_tag": "spanish-core-2026.08.01-r1",
        "bundle_id": payload["bundle_id"],
        "bundle_version": payload["bundle_version"],
        "manifest_url": (
            "https://github.com/Karuho/elyndra-language-packs/releases/download/"
            "spanish-core-2026.08.01-r1/elyndra-es-core.bundle.json"
        ),
        "manifest_size": len(raw),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "bundle_content_sha256": payload["bundle_content_sha256"],
        "compatibility": {"minimum": "0.8.8a0"},
        "enabled": True,
    }
    return AcquisitionDescriptor.from_official_source(source)


def test_official_descriptor_and_manifest_are_closed_and_hash_bound() -> None:
    payload = _manifest()
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    descriptor = _descriptor(raw, payload)
    validated = validate_downloaded_manifest(raw, descriptor)

    assert validated.bundle_id == "elyndra-es-core"
    assert len(validated.assets) == 4
    assert validated.result_sha256
    with pytest.raises(GatewayError, match="acquisition_manifest_hash_mismatch"):
        validate_downloaded_manifest(raw[:-1] + b" ", descriptor)

    source = {
        "source_id": descriptor.source_id,
        "unexpected": True,
    }
    with pytest.raises(GatewayError, match="acquisition_descriptor_invalid"):
        AcquisitionDescriptor.from_official_source(source)


def _archive(path: Path, members: list[tarfile.TarInfo]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for member in members:
            data = b"x" * member.size
            archive.addfile(member, io.BytesIO(data) if member.isfile() else None)


@pytest.mark.parametrize(
    ("members", "message"),
    [
        ([tarfile.TarInfo("../escape")], "ruta"),
        ([tarfile.TarInfo("nested.zip")], "anidado"),
        ([tarfile.TarInfo("A"), tarfile.TarInfo("a")], "ambiguos"),
        ([tarfile.TarInfo("e\u0301"), tarfile.TarInfo("é")], "ambiguos"),
    ],
)
def test_bundle_archive_rejects_unsafe_names(
    tmp_path: Path, members: list[tarfile.TarInfo], message: str
) -> None:
    for member in members:
        member.size = 1
    path = tmp_path / "unsafe.tar.gz"
    _archive(path, members)
    with pytest.raises(ValueError, match=message):
        LanguageBundleService._inspect_archive(path)


def test_bundle_archive_rejects_links_special_files_and_wrong_magic(tmp_path: Path) -> None:
    link = tarfile.TarInfo("link")
    link.type = tarfile.SYMTYPE
    link.linkname = "target"
    path = tmp_path / "link.tar.gz"
    _archive(path, [link])
    with pytest.raises(ValueError, match="enlace"):
        LanguageBundleService._inspect_archive(path)

    fifo = tarfile.TarInfo("pipe")
    fifo.type = tarfile.FIFOTYPE
    path = tmp_path / "fifo.tar.gz"
    _archive(path, [fifo])
    with pytest.raises(ValueError, match="tipo"):
        LanguageBundleService._inspect_archive(path)

    plain = tmp_path / "plain.tar.gz"
    plain.write_bytes(b"not-gzip")
    with pytest.raises(ValueError, match="magic"):
        LanguageBundleService._inspect_archive(plain)


def test_bundle_archive_enforces_member_and_ratio_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import elyndra.language_packs.bundles as bundles

    member = tarfile.TarInfo("large.txt")
    member.size = 10_000
    path = tmp_path / "large.tar.gz"
    _archive(path, [member])
    monkeypatch.setattr(bundles, "MAX_MEMBER_BYTES", 64)
    with pytest.raises(ValueError, match="miembro demasiado grande"):
        LanguageBundleService._inspect_archive(path)

    monkeypatch.setattr(bundles, "MAX_MEMBER_BYTES", 20_000)
    monkeypatch.setattr(bundles, "MAX_COMPRESSION_RATIO", 1)
    with pytest.raises(ValueError, match="ratio"):
        LanguageBundleService._inspect_archive(path)


def test_install_plan_has_separate_single_use_approval_and_can_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = ElyndraPaths(
        tmp_path / "config", tmp_path / "data", tmp_path / "state", tmp_path / "cache"
    )
    paths.ensure()
    storage = GatewayStorage(paths)
    vault = Database(tmp_path / "vault.sqlite3", role="vault")
    vault.migrate()
    assets = []
    for index in range(4):
        content = f"asset-{index}".encode()
        sha256 = hashlib.sha256(content).hexdigest()
        descriptor = RemoteArtifactDescriptor(
            source_id="elyndra-official-language-packs",
            artifact_key=f"official:asset:{sha256}",
            artifact_name=f"pack-{index}.tar.gz",
            manifest_url=f"https://github.com/official/pack-{index}.tar.gz",
            expected_size=len(content),
            expected_sha256=sha256,
            descriptor_sha256="d" * 64,
            hostname="github.com",
            metadata={},
        )
        storage.cache_path(descriptor.artifact_key).write_bytes(content)
        assets.append(descriptor)
    manifest_path = storage.cache_path("official:manifest")
    manifest_path.write_bytes(b"manifest")
    manifest = ValidatedBundleManifest(
        descriptor_sha256="d" * 64,
        manifest_sha256=hashlib.sha256(b"manifest").hexdigest(),
        bundle_content_sha256="b" * 64,
        bundle_id="elyndra-es-core",
        bundle_version="2026.08.01-r1",
        assets=tuple(assets),
        payload={},
    )
    pipeline = SupervisedBundlePipeline(
        vault_database=vault,
        account_id="acct-1",
        storage=storage,
        registry=cast(LanguagePackRegistry, object()),
        audit=GatewayAudit(AuditRepository(vault)),
    )
    monkeypatch.setattr(LanguageBundleService, "inspect", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(
        LanguageBundleService,
        "install",
        lambda *args, **kwargs: {"installed": [], "enabled": False},
    )
    prepared = pipeline.prepare(manifest, manifest_path)
    operation_id = prepared["operation_id"]
    with pytest.raises(GatewayError, match="approval_invalid"):
        pipeline.install(operation_id, approval="wrong")

    approval = pipeline.request_install_approval(operation_id)
    installed = pipeline.install(operation_id, approval=approval)
    assert installed["state"] == "installed"
    assert not (pipeline.staging_root / operation_id).exists()
    with pytest.raises(GatewayError, match="not_planned"):
        pipeline.install(operation_id, approval=approval)

    cancelled = pipeline.prepare(manifest, manifest_path)
    assert pipeline.cancel(cancelled["operation_id"])["state"] == "cancelled"
    assert not (pipeline.staging_root / cancelled["operation_id"]).exists()
