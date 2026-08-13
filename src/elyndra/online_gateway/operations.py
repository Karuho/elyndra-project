from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from elyndra.db import Database
from elyndra.online_gateway.acquisition import (
    AcquisitionDescriptor,
    ValidatedBundleManifest,
    validate_downloaded_manifest,
)
from elyndra.online_gateway.approvals import GatewayApprovalService
from elyndra.online_gateway.audit import GatewayAudit
from elyndra.online_gateway.bundle_pipeline import SupervisedBundlePipeline
from elyndra.online_gateway.downloads import DownloadManager
from elyndra.online_gateway.errors import GatewayError
from elyndra.online_gateway.models import RemoteArtifactDescriptor
from elyndra.online_gateway.policy import OnlineGatewayPolicy
from elyndra.online_gateway.sources import TrustedSourceRegistry, canonical_json


def _now() -> str:
    return datetime.now(UTC).isoformat()


_CLI_CAPABILITY_KEY = object()


@dataclass(frozen=True, slots=True)
class _CliExecutionCapability:
    operation_id: str
    plan_sha256: str
    command: str
    _key: object


def _issue_cli_execution_capability(
    *, operation_id: str, plan_sha256: str, command: str
) -> _CliExecutionCapability:
    if command not in {"execute-download", "resume-download"}:
        raise GatewayError("gateway_cli_command_denied")
    return _CliExecutionCapability(operation_id, plan_sha256, command, _CLI_CAPABILITY_KEY)


class OnlineGatewayService:
    """Account-scoped Phase 2 planning service. It owns no transport object."""

    def __init__(
        self,
        *,
        root_database: Database,
        vault_database: Database,
        account_id: str,
        global_enabled: bool,
        audit: GatewayAudit,
        approvals: GatewayApprovalService | None = None,
        downloads: DownloadManager | None = None,
        bundle_pipeline: SupervisedBundlePipeline | None = None,
    ) -> None:
        self.root_database = root_database
        self.vault_database = vault_database
        self.account_id = account_id
        self.global_enabled = global_enabled
        self.audit = audit
        self.approvals = approvals or GatewayApprovalService()
        self.policy = OnlineGatewayPolicy()
        self.sources = TrustedSourceRegistry(root_database, vault_database)
        self._downloads = downloads
        self._bundle_pipeline = bundle_pipeline

    def status(self) -> dict[str, Any]:
        result = {
            "global_gateway_enabled": self.global_enabled,
            "account_online_enabled": self.mode() == "online",
            "transport_available": self._downloads is not None,
            "phase": 4 if self._bundle_pipeline is not None else (3 if self._downloads else 2),
        }
        if self._bundle_pipeline is not None:
            result["bundle_install_available"] = True
        return result

    def mode(self) -> str:
        with self.vault_database.connect() as connection:
            row = connection.execute(
                "SELECT online_enabled FROM account_online_preferences WHERE id = 1"
            ).fetchone()
        return "online" if row is not None and bool(row[0]) else "local"

    def set_mode(self, mode: str) -> dict[str, Any]:
        if mode not in {"local", "online"}:
            raise GatewayError("gateway_mode_invalid")
        now = _now()
        with self.vault_database.connect() as connection:
            connection.execute(
                """
                INSERT INTO account_online_preferences(id, online_enabled, updated_at)
                VALUES(1, ?, ?) ON CONFLICT(id) DO UPDATE SET
                    online_enabled = excluded.online_enabled, updated_at = excluded.updated_at
                """,
                (mode == "online", now),
            )
        self.audit.record(
            account_id=self.account_id,
            outcome="completed",
            details={"operation_kind": "mode-set", "state": mode, "updated_at": now},
        )
        return self.status()

    def request_download_approval(self, source_id: str) -> dict[str, str]:
        operation = self._create_plan(source_id)
        token = self.approvals.request(
            account_id=self.account_id,
            operation_id=operation["public_id"],
            plan_sha256=operation["plan_sha256"],
        )
        return {"operation_id": operation["public_id"], "approval": token}

    def preview_download(self, source_id: str) -> dict[str, Any]:
        descriptor = AcquisitionDescriptor.from_official_source(self.sources.get(source_id))
        plan = self._preview_plan(descriptor.artifact())
        return plan | {"plan_digest": hashlib.sha256(canonical_json(plan).encode()).hexdigest()}

    def approve_download(self, source_id: str, *, plan_digest: str) -> dict[str, Any]:
        descriptor = AcquisitionDescriptor.from_official_source(self.sources.get(source_id))
        plan = self._preview_plan(descriptor.artifact())
        observed = hashlib.sha256(canonical_json(plan).encode()).hexdigest()
        if not hmac.compare_digest(observed, plan_digest):
            raise GatewayError("gateway_preview_changed")
        operation = self._create_plan(source_id, preview_digest=observed)
        self.audit.record(
            account_id=self.account_id,
            outcome="planned",
            details={
                "operation_id": operation["public_id"],
                "account_id": self.account_id,
                "operation_kind": "download",
                "source_id": source_id,
                "artifact_key": operation["artifact_key"],
                "state": "planned",
                "updated_at": _now(),
            },
        )
        return operation

    def request_asset_download_approval(
        self, source_id: str, artifact_key: str
    ) -> dict[str, str]:
        artifact = next(
            (
                item
                for item in self._validated_manifest(source_id).assets
                if item.artifact_key == artifact_key
            ),
            None,
        )
        if artifact is None:
            raise GatewayError("gateway_artifact_not_found")
        operation = self._create_plan(source_id, artifact=artifact)
        token = self.approvals.request(
            account_id=self.account_id,
            operation_id=operation["public_id"],
            plan_sha256=operation["plan_sha256"],
        )
        return {"operation_id": operation["public_id"], "approval": token}

    def plan_download(self, source_id: str, *, approval: str) -> dict[str, Any]:
        operation = self._latest_planned(source_id)
        if operation is None:
            operation = self._create_plan(source_id)
        self.policy.require_authority(
            global_enabled=self.global_enabled,
            account_enabled=self.mode() == "online",
            has_plan=True,
        )
        permit = self.approvals.consume(
            approval,
            account_id=self.account_id,
            operation_id=operation["public_id"],
            plan_sha256=operation["plan_sha256"],
        )
        permit.consume(
            account_id=self.account_id,
            operation_id=operation["public_id"],
            plan_sha256=operation["plan_sha256"],
        )
        now = _now()
        with self.vault_database.connect() as connection:
            connection.execute(
                """UPDATE account_gateway_operations
                SET operation_state='transport_unavailable',
                    error_code='gateway_transport_unavailable', updated_at=?, completed_at=?
                WHERE public_id=? AND operation_state='planned'""",
                (now, now, operation["public_id"]),
            )
        plan = json.loads(operation["immutable_plan_json"])
        self.audit.record(
            account_id=self.account_id,
            outcome="transport_unavailable",
            details={
                "operation_id": operation["public_id"],
                "account_id": self.account_id,
                "operation_kind": "download",
                "source_id": source_id,
                "artifact_key": operation["artifact_key"],
                "hostname": plan["hostname"],
                "expected_size": plan["expected_size"],
                "expected_sha256": plan["expected_sha256"],
                "state": "transport_unavailable",
                "error_code": "gateway_transport_unavailable",
                "approval_request_id": hashlib.sha256(approval.encode()).hexdigest(),
                "updated_at": now,
            },
        )
        return self.operation(operation["public_id"])

    def operations(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.vault_database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM account_gateway_operations ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def execute_download(
        self,
        operation_id: str,
        *,
        approval: str,
        resume: bool = False,
        cli_capability: _CliExecutionCapability | None = None,
    ) -> dict[str, Any]:
        if self._downloads is None:
            raise GatewayError("gateway_transport_unavailable")
        operation = self.operation(operation_id)
        self._validate_operation_plan(operation)
        cli_enabled = self._valid_cli_capability(
            cli_capability, operation, resume=resume
        )
        self.policy.require_authority(
            global_enabled=self.global_enabled or cli_enabled,
            account_enabled=self.mode() == "online",
            has_plan=True,
        )
        permit = self.approvals.consume(
            approval,
            account_id=self.account_id,
            operation_id=operation_id,
            plan_sha256=str(operation["plan_sha256"]),
        )
        descriptor = self._descriptor_for_operation(operation)
        if descriptor.descriptor_sha256 != operation["descriptor_sha256"]:
            raise GatewayError("gateway_plan_required")
        job_id = f"gjob_{uuid.uuid4().hex}"
        now = _now()
        try:
            with self.root_database.connect() as connection:
                connection.execute(
                    """INSERT INTO online_gateway_download_jobs(
                        public_id, artifact_key, state, bytes_written, expected_size,
                        partial_relpath, updated_at
                    ) VALUES (?, ?, 'approved', 0, ?, ?, ?)""",
                    (
                        job_id,
                        descriptor.artifact_key,
                        descriptor.expected_size,
                        self._downloads.storage.relative(
                            self._downloads.storage.partial_path(descriptor.artifact_key)
                        ),
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise GatewayError("gateway_download_busy") from exc
        with self.vault_database.connect() as connection:
            connection.execute(
                """UPDATE account_gateway_operations
                SET root_job_public_id=?, updated_at=? WHERE public_id=?""",
                (job_id, now, operation_id),
            )
        allowed_hosts = tuple(
            str(value) for value in descriptor.metadata.get("allowed_redirect_hosts", ())
        ) or (descriptor.hostname,)
        try:
            result = self._downloads.execute(
                job_id=job_id,
                account_id=self.account_id,
                operation_id=operation_id,
                plan_sha256=str(operation["plan_sha256"]),
                permit=permit,
                descriptor=descriptor,
                allowed_redirect_hosts=allowed_hosts,
                resume=resume,
            )
        except GatewayError as exc:
            with self.vault_database.connect() as connection:
                connection.execute(
                    """UPDATE account_gateway_operations SET operation_state='failed',
                    error_code=?, updated_at=? WHERE public_id=?""",
                    (exc.code, _now(), operation_id),
                )
            self._audit_execution(
                operation=operation,
                descriptor=descriptor,
                approval=approval,
                state="failed",
                error_code=exc.code,
                error_context=exc.context,
            )
            raise
        with self.vault_database.connect() as connection:
            connection.execute(
                """UPDATE account_gateway_operations SET operation_state='completed',
                error_code=NULL, updated_at=?, completed_at=? WHERE public_id=?""",
                (_now(), _now(), operation_id),
            )
        self._audit_execution(
            operation=operation,
            descriptor=descriptor,
            approval=approval,
            state="completed",
            error_code=None,
            error_context=None,
        )
        return result | {"operation_id": operation_id, "job_id": job_id}

    def _audit_execution(
        self,
        *,
        operation: dict[str, Any],
        descriptor: RemoteArtifactDescriptor,
        approval: str,
        state: str,
        error_code: str | None,
        error_context: dict[str, Any] | None,
    ) -> None:
        self.audit.record(
            account_id=self.account_id,
            outcome=state,
            details={
                "operation_id": operation["public_id"],
                "account_id": self.account_id,
                "operation_kind": "resume" if state == "resume_rejected" else "download",
                "source_id": descriptor.source_id,
                "artifact_key": descriptor.artifact_key,
                "hostname": descriptor.hostname,
                "expected_size": descriptor.expected_size,
                "expected_sha256": descriptor.expected_sha256,
                "state": state,
                "error_code": error_code,
                "approval_request_id": hashlib.sha256(approval.encode()).hexdigest(),
                "updated_at": _now(),
            }
            | (error_context or {}),
        )

    def request_execution_approval(self, operation_id: str, *, resume: bool = False) -> str:
        operation = self.operation(operation_id)
        self._validate_operation_plan(operation)
        expected_state = "failed" if resume else "planned"
        if operation["operation_state"] != expected_state:
            raise GatewayError("gateway_operation_not_planned")
        return self.approvals.request(
            account_id=self.account_id,
            operation_id=operation_id,
            plan_sha256=str(operation["plan_sha256"]),
        )

    def discard_partial(self, operation_id: str) -> bool:
        if self._downloads is None:
            raise GatewayError("gateway_transport_unavailable")
        operation = self.operation(operation_id)
        return self._downloads.discard_partial(self._descriptor_for_operation(operation))

    def cancel_download(self, operation_id: str) -> dict[str, Any]:
        if self._downloads is None:
            raise GatewayError("gateway_transport_unavailable")
        operation = self.operation(operation_id)
        job_id = operation.get("root_job_public_id")
        if not isinstance(job_id, str) or not job_id:
            raise GatewayError("gateway_download_job_not_found")
        self._downloads.request_cancel(job_id)
        return {"operation_id": operation_id, "cancel_requested": True}

    def cache_show(self, artifact_key: str) -> dict[str, Any] | None:
        if self._downloads is None:
            raise GatewayError("gateway_transport_unavailable")
        return self._downloads.cache_entry(artifact_key)

    def cache_verify(self, artifact_key: str) -> dict[str, Any]:
        if self._downloads is None:
            raise GatewayError("gateway_transport_unavailable")
        source_id = artifact_key.split(":", 1)[0]
        artifact = self.sources.artifact(source_id)
        if artifact.artifact_key != artifact_key:
            artifact = next(
                (item for item in self._validated_manifest(source_id).assets
                 if item.artifact_key == artifact_key),
                None,
            )
        if artifact is None:
            raise GatewayError("gateway_artifact_not_found")
        return self._downloads.verify_cache(artifact)

    def quarantine(self) -> list[dict[str, Any]]:
        if self._downloads is None:
            return []
        return self._downloads.quarantine_entries()

    def official_descriptor(self, source_id: str) -> dict[str, Any]:
        descriptor = AcquisitionDescriptor.from_official_source(self.sources.get(source_id))
        return {
            "source_id": descriptor.source_id,
            "bundle_id": descriptor.bundle_id,
            "bundle_version": descriptor.bundle_version,
            "bundle_schema": descriptor.bundle_schema,
            "manifest_url": descriptor.manifest_url,
            "manifest_size": descriptor.manifest_size,
            "manifest_sha256": descriptor.manifest_sha256,
            "bundle_content_sha256": descriptor.bundle_content_sha256,
            "descriptor_sha256": descriptor.descriptor_sha256,
        }

    def inspect_bundle(self, source_id: str) -> dict[str, Any]:
        manifest = self._validated_manifest(source_id)
        return {
            "source_id": source_id,
            "bundle_id": manifest.bundle_id,
            "bundle_version": manifest.bundle_version,
            "manifest_sha256": manifest.manifest_sha256,
            "bundle_content_sha256": manifest.bundle_content_sha256,
            "parse_result_sha256": manifest.result_sha256,
            "assets": [
                {
                    "artifact_key": item.artifact_key,
                    "name": item.artifact_name,
                    "size": item.expected_size,
                    "sha256": item.expected_sha256,
                }
                for item in manifest.assets
            ],
            "verified": True,
            "network_used": False,
        }

    def prepare_bundle_install(self, source_id: str) -> dict[str, Any]:
        pipeline = self._require_bundle_pipeline()
        manifest = self._validated_manifest(source_id)
        descriptor = AcquisitionDescriptor.from_official_source(self.sources.get(source_id))
        return pipeline.prepare(
            manifest,
            self._downloads.storage.cache_path(descriptor.artifact().artifact_key),
        )

    def request_bundle_install_approval(self, operation_id: str) -> str:
        return self._require_bundle_pipeline().request_install_approval(operation_id)

    def install_bundle(self, operation_id: str, *, approval: str) -> dict[str, Any]:
        return self._require_bundle_pipeline().install(operation_id, approval=approval)

    def cancel_bundle_install(self, operation_id: str) -> dict[str, Any]:
        return self._require_bundle_pipeline().cancel(operation_id)

    def bundle_install_status(self, operation_id: str) -> dict[str, Any]:
        return self._require_bundle_pipeline().status(operation_id)

    def _validated_manifest(self, source_id: str) -> ValidatedBundleManifest:
        if self._downloads is None:
            raise GatewayError("gateway_transport_unavailable")
        descriptor = AcquisitionDescriptor.from_official_source(self.sources.get(source_id))
        path = self._downloads.storage.cache_path(descriptor.artifact().artifact_key)
        self._downloads.storage.safe_existing(path)
        return validate_downloaded_manifest(path.read_bytes(), descriptor)

    def _require_bundle_pipeline(self) -> SupervisedBundlePipeline:
        if self._bundle_pipeline is None or self._downloads is None:
            raise GatewayError("gateway_bundle_pipeline_unavailable")
        return self._bundle_pipeline

    def _descriptor_for_operation(
        self, operation: dict[str, Any]
    ) -> RemoteArtifactDescriptor:
        source_id = str(operation["source_id"])
        artifact = self.sources.artifact(source_id)
        if artifact.artifact_key == operation["artifact_key"]:
            return artifact
        match = next(
            (
                item
                for item in self._validated_manifest(source_id).assets
                if item.artifact_key == operation["artifact_key"]
            ),
            None,
        )
        if match is None:
            raise GatewayError("gateway_artifact_not_found")
        return match

    @staticmethod
    def _valid_cli_capability(
        capability: _CliExecutionCapability | None,
        operation: dict[str, Any],
        *,
        resume: bool,
    ) -> bool:
        expected_command = "resume-download" if resume else "execute-download"
        return bool(
            capability is not None
            and capability._key is _CLI_CAPABILITY_KEY
            and capability.operation_id == operation["public_id"]
            and capability.plan_sha256 == operation["plan_sha256"]
            and capability.command == expected_command
        )

    @staticmethod
    def _validate_operation_plan(operation: dict[str, Any]) -> None:
        encoded = canonical_json(operation["plan"])
        if not hmac.compare_digest(
            hashlib.sha256(encoded.encode()).hexdigest(), str(operation["plan_sha256"])
        ):
            raise GatewayError("gateway_plan_changed")

    def _preview_plan(self, artifact: RemoteArtifactDescriptor) -> dict[str, Any]:
        return {
            "operation_kind": "download",
            "source_id": artifact.source_id,
            "artifact_key": artifact.artifact_key,
            "method": "GET",
            "url": artifact.manifest_url.split("?", 1)[0],
            "hostname": artifact.hostname,
            "expected_size": artifact.expected_size,
            "expected_sha256": artifact.expected_sha256,
            "byte_limit": artifact.expected_size,
            "redirect_limit": self.policy.limits.redirects,
            "allowed_redirect_hosts": list(
                artifact.metadata.get("allowed_redirect_hosts", (artifact.hostname,))
            ),
            "descriptor_sha256": artifact.descriptor_sha256,
            "privacy": "public pinned artifact; no credentials, cookies or telemetry",
        }

    def operation(self, operation_id: str) -> dict[str, Any]:
        with self.vault_database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM account_gateway_operations WHERE public_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise GatewayError("gateway_operation_not_found")
        result = dict(row)
        result["plan"] = json.loads(result.pop("immutable_plan_json"))
        return result

    def clear_history(self) -> int:
        with self.vault_database.connect() as connection:
            cursor = connection.execute("DELETE FROM account_gateway_operations")
        self.audit.record(
            account_id=self.account_id,
            outcome="completed",
            details={"operation_kind": "history-clear", "state": "completed", "updated_at": _now()},
        )
        return int(cursor.rowcount)

    def _create_plan(
        self,
        source_id: str,
        *,
        artifact: RemoteArtifactDescriptor | None = None,
        preview_digest: str | None = None,
    ) -> dict[str, Any]:
        artifact = artifact or self.sources.artifact(source_id)
        operation_id = f"gop_{uuid.uuid4().hex}"
        plan = self._preview_plan(artifact) | {
            "operation_id": operation_id,
            "preview_digest": preview_digest,
        }
        encoded = canonical_json(plan)
        plan_sha = hashlib.sha256(encoded.encode()).hexdigest()
        now = _now()
        with self.vault_database.connect() as connection:
            connection.execute(
                """INSERT INTO account_gateway_operations(
                    public_id, operation_kind, source_id, artifact_key, descriptor_sha256,
                    immutable_plan_json, plan_sha256, operation_state, install_requested,
                    enable_requested, created_at, updated_at
                ) VALUES (?, 'download', ?, ?, ?, ?, ?, 'planned', 0, 0, ?, ?)""",
                (
                    operation_id,
                    source_id,
                    artifact.artifact_key,
                    artifact.descriptor_sha256,
                    encoded,
                    plan_sha,
                    now,
                    now,
                ),
            )
        return self.operation(operation_id) | {"immutable_plan_json": encoded}

    def _latest_planned(self, source_id: str) -> dict[str, Any] | None:
        with self.vault_database.connect() as connection:
            row = connection.execute(
                """SELECT * FROM account_gateway_operations
                WHERE source_id=? AND operation_state='planned' ORDER BY id DESC LIMIT 1""",
                (source_id,),
            ).fetchone()
        return dict(row) if row is not None else None
