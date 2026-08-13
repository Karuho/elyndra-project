from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from elyndra.language_packs.bundles import _canonical_hash, _validate_manifest
from elyndra.online_gateway.errors import GatewayError
from elyndra.online_gateway.models import RemoteArtifactDescriptor
from elyndra.online_gateway.policy import OnlineGatewayPolicy
from elyndra.online_gateway.sources import canonical_json

_ID = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")
_VERSION = re.compile(r"[0-9][0-9A-Za-z._-]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_OFFICIAL_FIELDS = {
    "source_id", "trust_class", "display_name", "repository", "release_tag",
    "bundle_id", "bundle_version", "manifest_url", "manifest_size", "manifest_sha256",
    "bundle_content_sha256", "compatibility", "enabled",
}


@dataclass(frozen=True, slots=True)
class AcquisitionDescriptor:
    source_id: str
    bundle_id: str
    bundle_version: str
    bundle_schema: int
    manifest_url: str
    manifest_size: int
    manifest_sha256: str
    bundle_content_sha256: str
    descriptor_sha256: str

    @classmethod
    def from_official_source(cls, value: dict[str, Any]) -> AcquisitionDescriptor:
        if set(value) != _OFFICIAL_FIELDS or value.get("trust_class") != "official-pinned":
            raise GatewayError("acquisition_descriptor_invalid")
        source_id = value.get("source_id")
        bundle_id = value.get("bundle_id")
        version = value.get("bundle_version")
        size = value.get("manifest_size")
        manifest_sha = value.get("manifest_sha256")
        content_sha = value.get("bundle_content_sha256")
        if not isinstance(source_id, str) or not _ID.fullmatch(source_id):
            raise GatewayError("acquisition_descriptor_invalid")
        if not isinstance(bundle_id, str) or not _ID.fullmatch(bundle_id):
            raise GatewayError("acquisition_descriptor_invalid")
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise GatewayError("acquisition_descriptor_invalid")
        if type(size) is not int or not 1 <= size <= 512 * 1024:
            raise GatewayError("acquisition_descriptor_invalid")
        if not isinstance(manifest_sha, str) or not _SHA256.fullmatch(manifest_sha):
            raise GatewayError("acquisition_descriptor_invalid")
        if not isinstance(content_sha, str) or not _SHA256.fullmatch(content_sha):
            raise GatewayError("acquisition_descriptor_invalid")
        url = str(value.get("manifest_url", ""))
        OnlineGatewayPolicy().validate_url(url)
        digest = hashlib.sha256(canonical_json(value).encode()).hexdigest()
        return cls(
            source_id, bundle_id, version, 1, url, size, manifest_sha, content_sha, digest
        )

    def artifact(self) -> RemoteArtifactDescriptor:
        hostname = str(urlsplit(self.manifest_url).hostname)
        return RemoteArtifactDescriptor(
            source_id=self.source_id,
            artifact_key=f"{self.source_id}:manifest:{self.manifest_sha256}",
            artifact_name=self.manifest_url.rsplit("/", 1)[-1],
            manifest_url=self.manifest_url,
            expected_size=self.manifest_size,
            expected_sha256=self.manifest_sha256,
            descriptor_sha256=self.descriptor_sha256,
            hostname=hostname,
            metadata={"bundle_id": self.bundle_id, "bundle_version": self.bundle_version},
        )


@dataclass(frozen=True, slots=True)
class ValidatedBundleManifest:
    descriptor_sha256: str
    manifest_sha256: str
    bundle_content_sha256: str
    bundle_id: str
    bundle_version: str
    assets: tuple[RemoteArtifactDescriptor, ...]
    payload: dict[str, Any]

    @property
    def result_sha256(self) -> str:
        facts = {
            "descriptor_sha256": self.descriptor_sha256,
            "manifest_sha256": self.manifest_sha256,
            "bundle_content_sha256": self.bundle_content_sha256,
            "assets": [asset.expected_sha256 for asset in self.assets],
        }
        return hashlib.sha256(canonical_json(facts).encode()).hexdigest()


def validate_downloaded_manifest(
    raw: bytes, descriptor: AcquisitionDescriptor
) -> ValidatedBundleManifest:
    if len(raw) != descriptor.manifest_size:
        raise GatewayError("acquisition_manifest_size_mismatch")
    observed = hashlib.sha256(raw).hexdigest()
    if observed != descriptor.manifest_sha256:
        raise GatewayError("acquisition_manifest_hash_mismatch")
    try:
        payload = json.loads(raw.decode("utf-8"))
        _validate_manifest(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise GatewayError("acquisition_manifest_invalid") from exc
    if (
        payload["bundle_id"] != descriptor.bundle_id
        or payload["bundle_version"] != descriptor.bundle_version
        or payload["bundle_content_sha256"] != descriptor.bundle_content_sha256
        or _canonical_hash(payload) != descriptor.bundle_content_sha256
    ):
        raise GatewayError("acquisition_manifest_mismatch")
    base = descriptor.manifest_url.rsplit("/", 1)[0]
    assets: list[RemoteArtifactDescriptor] = []
    seen: set[str] = set()
    for item in payload["assets"]:
        name = item.get("name")
        size = item.get("size")
        sha256 = item.get("sha256")
        if (
            not isinstance(name, str)
            or PurePosixPath(name).name != name
            or name in seen
            or type(size) is not int
            or not 1 <= size <= 2 * 1024**3
            or not isinstance(sha256, str)
            or not _SHA256.fullmatch(sha256)
        ):
            raise GatewayError("acquisition_manifest_invalid")
        seen.add(name)
        url = f"{base}/{name}"
        hostname = OnlineGatewayPolicy().validate_url(url)
        assets.append(RemoteArtifactDescriptor(
            source_id=descriptor.source_id,
            artifact_key=f"{descriptor.source_id}:asset:{sha256}",
            artifact_name=name,
            manifest_url=url,
            expected_size=size,
            expected_sha256=sha256,
            descriptor_sha256=descriptor.descriptor_sha256,
            hostname=hostname,
            metadata={
                "bundle_id": descriptor.bundle_id,
                "bundle_version": descriptor.bundle_version,
            },
        ))
    return ValidatedBundleManifest(
        descriptor.descriptor_sha256, observed, descriptor.bundle_content_sha256,
        descriptor.bundle_id, descriptor.bundle_version, tuple(assets), payload,
    )
