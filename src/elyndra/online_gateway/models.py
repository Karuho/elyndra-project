from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GatewayLimits:
    manifest_bytes: int = 512 * 1024
    artifact_bytes: int = 2 * 1024**3
    operation_bytes: int = 4 * 1024**3
    redirects: int = 5
    total_header_bytes: int = 64 * 1024
    header_bytes: int = 8 * 1024
    header_count: int = 100
    connect_timeout_seconds: int = 10
    read_timeout_seconds: int = 60
    operation_timeout_seconds: int = 8 * 60 * 60
    chunk_bytes: int = 1024 * 1024
    reserve_bytes: int = 512 * 1024**2
    active_downloads: int = 1


@dataclass(frozen=True, slots=True)
class RemoteArtifactDescriptor:
    source_id: str
    artifact_key: str
    artifact_name: str
    manifest_url: str
    expected_size: int
    expected_sha256: str
    descriptor_sha256: str
    hostname: str
    metadata: dict[str, Any]
