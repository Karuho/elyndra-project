from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any

from elyndra.db import Database
from elyndra.online_gateway.errors import GatewayError
from elyndra.online_gateway.models import RemoteArtifactDescriptor
from elyndra.online_gateway.policy import OnlineGatewayPolicy


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class TrustedSourceRegistry:
    def __init__(self, root_database: Database, vault_database: Database) -> None:
        self.root_database = root_database
        self.vault_database = vault_database
        self.policy = OnlineGatewayPolicy()
        self._official = self._load_official()
        self._sync_official_metadata()

    def _sync_official_metadata(self) -> None:
        now = datetime.now(UTC).isoformat()
        with self.root_database.connect() as connection:
            for item in self._official.values():
                digest = hashlib.sha256(canonical_json(item).encode()).hexdigest()
                connection.execute(
                    """INSERT INTO online_gateway_sources(
                        source_id, descriptor_version, display_name, source_kind, enabled,
                        descriptor_sha256, created_at, updated_at
                    ) VALUES (?, 1, ?, 'official-pinned', ?, ?, ?, ?)
                    ON CONFLICT(source_id) DO UPDATE SET
                        enabled=excluded.enabled, updated_at=excluded.updated_at
                    WHERE online_gateway_sources.descriptor_sha256=excluded.descriptor_sha256""",
                    (
                        item["source_id"],
                        item["display_name"],
                        bool(item["enabled"]),
                        digest,
                        now,
                        now,
                    ),
                )

    def _load_official(self) -> dict[str, Any]:
        resource = files("elyndra.resources").joinpath("official_online_sources_v1.json")
        payload = json.loads(resource.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
            raise GatewayError("gateway_official_source_invalid")
        return {str(item["source_id"]): item for item in payload["sources"]}

    def list(self) -> list[dict[str, Any]]:
        values = [dict(item) for item in self._official.values()]
        with self.vault_database.connect() as connection:
            rows = connection.execute(
                "SELECT descriptor_json, enabled FROM account_gateway_sources ORDER BY source_id"
            ).fetchall()
        for row in rows:
            item = json.loads(row["descriptor_json"])
            item["enabled"] = bool(row["enabled"])
            values.append(item)
        return values

    def get(self, source_id: str) -> dict[str, Any]:
        if source_id in self._official:
            return dict(self._official[source_id])
        with self.vault_database.connect() as connection:
            row = connection.execute(
                "SELECT descriptor_json FROM account_gateway_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            raise GatewayError("gateway_source_not_found")
        return json.loads(row[0])

    def artifact(self, source_id: str) -> RemoteArtifactDescriptor:
        item = self.get(source_id)
        url = str(item["manifest_url"])
        hostname = self.policy.validate_url(url)
        sha256 = str(item["manifest_sha256"])
        size = int(item["manifest_size"])
        if len(sha256) != 64 or size < 1 or size > self.policy.limits.manifest_bytes:
            raise GatewayError("gateway_descriptor_invalid")
        canonical = canonical_json(item)
        descriptor_sha = hashlib.sha256(canonical.encode()).hexdigest()
        return RemoteArtifactDescriptor(
            source_id=source_id,
            artifact_key=f"{source_id}:manifest:{sha256}",
            artifact_name=url.rsplit("/", 1)[-1],
            manifest_url=url,
            expected_size=size,
            expected_sha256=sha256,
            descriptor_sha256=descriptor_sha,
            hostname=hostname,
            metadata=item,
        )

    def add_user_source(self, descriptor: dict[str, Any], *, enabled: bool = False) -> None:
        if descriptor.get("trust_class") != "user-pinned":
            raise GatewayError("gateway_user_source_trust_invalid")
        source_id = str(descriptor.get("source_id", ""))
        if not source_id or source_id in self._official or len(source_id) > 64:
            raise GatewayError("gateway_source_id_invalid")
        self.policy.validate_url(str(descriptor.get("manifest_url", "")))
        sha256 = str(descriptor.get("manifest_sha256", ""))
        size = descriptor.get("manifest_size")
        if len(sha256) != 64 or not isinstance(size, int) or not 1 <= size <= 512 * 1024:
            raise GatewayError("gateway_descriptor_invalid")
        encoded = canonical_json(descriptor)
        if len(encoded.encode("utf-8")) > 65536:
            raise GatewayError("gateway_descriptor_too_large")
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        now = datetime.now(UTC).isoformat()
        with self.vault_database.connect() as connection:
            connection.execute(
                """INSERT INTO account_gateway_sources(
                    source_id, descriptor_json, descriptor_sha256, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (source_id, encoded, digest, enabled, now, now),
            )
