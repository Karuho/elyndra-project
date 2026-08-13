from __future__ import annotations

import hashlib
import io
import json
import socket
import tarfile
from pathlib import Path

import pytest

from elyndra.db import Database
from elyndra.language_packs import LanguagePackBuilder
from elyndra.language_packs.bundles import LanguageBundleService
from elyndra.language_packs.registry import LanguagePackRegistry
from elyndra.paths import ElyndraPaths


def _registry(tmp_path: Path) -> LanguagePackRegistry:
    paths = ElyndraPaths(
        tmp_path / "config", tmp_path / "data", tmp_path / "state", tmp_path / "cache"
    )
    paths.ensure()
    database = Database(paths.database_file, role="root")
    database.migrate()
    return LanguagePackRegistry(database, paths)


def _packs(tmp_path: Path, registry: LanguagePackRegistry) -> list[dict[str, object]]:
    tmp_path.mkdir(parents=True)
    source = tmp_path / "source.jsonl"
    source.write_text(
        json.dumps({
            "type": "lexeme", "id": "caminar", "lemma": "caminar", "pos": "v",
            "forms": [{"form": "caminando", "features": {"verb_form": "gerund"}}],
            "senses": [{"id": "walk", "definition": "Moverse dando pasos."}],
        }) + "\n"
    )
    license_path = tmp_path / "LICENSE.txt"
    license_path.write_text("Licencia sintética para pruebas.\n")
    metadata = {
        "source_id": "synthetic", "title": "Synthetic", "version": "1",
        "source_url": "https://example.invalid", "path": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "license_id": "LicenseRef-Synthetic", "license_path": str(license_path),
        "attribution": "Fixture sintético.", "format": "wiktionary-jsonl",
    }
    result = []
    identities = (
        ("elyndra-es-informal", 400), ("elyndra-es-wiktionary", 300),
        ("elyndra-es-mcr-omw", 250), ("elyndra-es-cldr", 200),
    )
    for index, (pack_id, priority) in enumerate(identities):
        built = LanguagePackBuilder().build(
            logical_pack_id=pack_id,
            version="1",
            sources=[metadata],
            output_dir=tmp_path / f"pack-{index}",
            build_epoch=1_700_000_000,
        )
        registry.inspect(Path(built["path"]))
        result.append({"path": built["path"], "query_priority": priority})
    return result


def test_bundle_is_reproducible_split_verified_and_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry(tmp_path / "home")
    specs = _packs(tmp_path / "inputs", registry)
    service = LanguageBundleService(registry)
    first = service.create(
        pack_specs=specs, output_dir=tmp_path / "release-one",
        build_epoch=1_700_000_000, part_bytes=1024,
    )
    second = service.create(
        pack_specs=specs, output_dir=tmp_path / "release-two",
        build_epoch=1_700_000_000, part_bytes=1024,
    )
    assert first["bundle_content_sha256"] == second["bundle_content_sha256"]
    assert [asset["sha256"] for asset in first["assets"]] == [
        asset["sha256"] for asset in second["assets"]
    ]
    assert all(asset["parts"] for asset in first["assets"])
    assert [pack["query_priority"] for pack in first["packs"]] == [400, 300, 250, 200]
    manifest = Path(first["path"]) / "elyndra-language-bundle.json"
    monkeypatch.setattr(
        socket, "socket", lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("El bundle no debe abrir sockets.")
        )
    )
    assert service.inspect(manifest)["verified"] is True
    installed = service.install(manifest, actor="test", enable=True)
    assert len(installed["installed"]) == 4
    assert len(registry.list_all(enabled_only=True)) == 4
    assert installed["network_used"] is False


def test_bundle_rejects_corrupt_part_and_incompatible_version(tmp_path: Path) -> None:
    registry = _registry(tmp_path / "home")
    service = LanguageBundleService(registry)
    result = service.create(
        pack_specs=_packs(tmp_path / "inputs", registry),
        output_dir=tmp_path / "release", build_epoch=1_700_000_000, part_bytes=1024,
    )
    manifest = Path(result["path"]) / "elyndra-language-bundle.json"
    payload = json.loads(manifest.read_text())
    part = Path(result["path"]) / payload["assets"][0]["parts"][0]["name"]
    part.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Parte corrupta"):
        service.inspect(manifest)

    incompatible = tmp_path / "incompatible.json"
    payload["minimum_elyndra_version"] = "0.9.0a0"
    payload.pop("bundle_content_sha256")
    from elyndra.language_packs.bundles import _canonical_hash

    payload["bundle_content_sha256"] = _canonical_hash(payload)
    incompatible.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="incompatible"):
        service.inspect(incompatible, verify_archives=False)


def test_bundle_rejects_traversal_and_rolls_back_only_new_packs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    malicious = tmp_path / "traversal.tar.gz"
    with tarfile.open(malicious, "w:gz") as archive:
        payload = b"escape"
        member = tarfile.TarInfo("../escape")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    with pytest.raises(ValueError, match="ruta"):
        LanguageBundleService._inspect_archive(malicious)

    registry = _registry(tmp_path / "home")
    service = LanguageBundleService(registry)
    created = service.create(
        pack_specs=_packs(tmp_path / "inputs", registry),
        output_dir=tmp_path / "release", build_epoch=1_700_000_000,
    )
    original = registry.install
    attempts = 0

    def failing_install(path: Path, *, actor: str, query_priority: int = 100) -> dict:
        nonlocal attempts
        attempts += 1
        if attempts == 3:
            raise RuntimeError("fallo sintético")
        return original(path, actor=actor, query_priority=query_priority)

    monkeypatch.setattr(registry, "install", failing_install)
    manifest = Path(created["path"]) / "elyndra-language-bundle.json"
    with pytest.raises(RuntimeError, match="sintético"):
        service.install(manifest, actor="test", enable=True)
    assert registry.list_all() == []
