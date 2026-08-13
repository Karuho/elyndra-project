from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.approvals import ApprovalStore
from elyndra.db import Database
from elyndra.language_packs.bundles import BUNDLE_MANIFEST_NAME, LanguageBundleService
from elyndra.language_packs.registry import LanguagePackRegistry
from elyndra.online_gateway.acquisition import ValidatedBundleManifest
from elyndra.online_gateway.audit import GatewayAudit
from elyndra.online_gateway.errors import GatewayError
from elyndra.online_gateway.sources import canonical_json
from elyndra.online_gateway.storage import GatewayStorage


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SupervisedBundlePipeline:
    """Offline preparation and installation. It has no transport or resolver."""

    def __init__(
        self,
        *,
        vault_database: Database,
        account_id: str,
        storage: GatewayStorage,
        registry: LanguagePackRegistry,
        audit: GatewayAudit,
        approvals: ApprovalStore | None = None,
    ) -> None:
        self.database = vault_database
        self.account_id = account_id
        self.storage = storage
        self.registry = registry
        self.audit = audit
        self.approvals = approvals or ApprovalStore()
        self.staging_root = storage.root / "staging"
        self.staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.staging_root.chmod(0o700)

    def prepare(self, manifest: ValidatedBundleManifest, manifest_path: Path) -> dict[str, Any]:
        operation_id = f"ginstall_{uuid.uuid4().hex}"
        staging = self.staging_root / operation_id
        staging.mkdir(mode=0o700)
        try:
            self._copy_verified(manifest_path, staging / BUNDLE_MANIFEST_NAME)
            for asset in manifest.assets:
                cached = self.storage.cache_path(asset.artifact_key)
                self._verify_cached(cached, asset.expected_size, asset.expected_sha256)
                self._copy_verified(cached, staging / asset.artifact_name)
            inspected = LanguageBundleService(
                self.registry, work_root=self.staging_root
            ).inspect(staging / BUNDLE_MANIFEST_NAME)
            plan = {
                "operation_id": operation_id,
                "operation_kind": "install",
                "bundle_id": manifest.bundle_id,
                "bundle_version": manifest.bundle_version,
                "descriptor_sha256": manifest.descriptor_sha256,
                "bundle_sha256": manifest.manifest_sha256,
                "parse_result_sha256": manifest.result_sha256,
                "destination": "managed-language-pack-registry",
                "enable": False,
            }
            encoded = canonical_json(plan)
            plan_sha = hashlib.sha256(encoded.encode()).hexdigest()
            now = _now()
            with self.database.connect() as connection:
                connection.execute(
                    """INSERT INTO account_gateway_operations(
                    public_id, operation_kind, source_id, artifact_key, descriptor_sha256,
                    immutable_plan_json, plan_sha256, operation_state, install_requested,
                    enable_requested, created_at, updated_at
                    ) VALUES (?, 'install', 'elyndra-official-language-packs', ?, ?, ?, ?,
                    'planned', 1, 0, ?, ?)""",
                    (
                        operation_id,
                        f"bundle:{manifest.bundle_id}:{manifest.bundle_version}",
                        manifest.descriptor_sha256,
                        encoded,
                        plan_sha,
                        now,
                        now,
                    ),
                )
            return {
                "operation_id": operation_id,
                "plan_sha256": plan_sha,
                "plan": plan,
                "bundle": inspected,
                "state": "prepared",
            }
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def request_install_approval(self, operation_id: str) -> str:
        operation = self._operation(operation_id)
        self._validate_plan(operation)
        fingerprint = ApprovalStore.fingerprint(
            self.account_id, str(operation["plan_sha256"]), [operation_id]
        )
        return self.approvals.create(
            chat_id=self.account_id,
            fingerprint=fingerprint,
            skill_name="online_gateway.install_bundle",
        ).token

    def install(self, operation_id: str, *, approval: str) -> dict[str, Any]:
        operation = self._operation(operation_id)
        plan = self._validate_plan(operation)
        fingerprint = ApprovalStore.fingerprint(
            self.account_id, str(operation["plan_sha256"]), [operation_id]
        )
        try:
            self.approvals.consume(
                approval, chat_id=self.account_id, fingerprint=fingerprint
            )
        except ValueError as exc:
            raise GatewayError("gateway_install_approval_invalid") from exc
        staging = self.staging_root / operation_id
        manifest_path = staging / BUNDLE_MANIFEST_NAME
        if plan.get("destination") != "managed-language-pack-registry":
            raise GatewayError("gateway_install_plan_changed")
        try:
            result = LanguageBundleService(
                self.registry, work_root=self.staging_root
            ).install(manifest_path, actor=f"account:{self.account_id}", enable=False)
        except Exception as exc:
            self._set_state(operation_id, "failed", "bundle_install_failed")
            raise GatewayError("bundle_install_failed") from exc
        self._set_state(operation_id, "completed", None)
        shutil.rmtree(staging, ignore_errors=True)
        self.audit.record(
            account_id=self.account_id,
            outcome="completed",
            details={
                "operation_id": operation_id,
                "account_id": self.account_id,
                "operation_kind": "install",
                "artifact_key": operation["artifact_key"],
                "state": "completed",
                "updated_at": _now(),
            },
        )
        return {"operation_id": operation_id, "state": "installed", "result": result}

    def cancel(self, operation_id: str) -> dict[str, Any]:
        operation = self._operation(operation_id)
        self._validate_plan(operation)
        self._set_state(operation_id, "cancelled", "bundle_install_cancelled")
        shutil.rmtree(self.staging_root / operation_id, ignore_errors=True)
        return {"operation_id": operation_id, "state": "cancelled"}

    def status(self, operation_id: str) -> dict[str, Any]:
        value = self._operation(operation_id)
        value.pop("immutable_plan_json", None)
        return value

    def _operation(self, operation_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT * FROM account_gateway_operations
                WHERE public_id=? AND operation_kind='install'""",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise GatewayError("gateway_install_operation_not_found")
        return dict(row)

    def _set_state(self, operation_id: str, state: str, error: str | None) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE account_gateway_operations SET operation_state=?, error_code=?,
                updated_at=?, completed_at=? WHERE public_id=?""",
                (state, error, _now(), _now() if state == "completed" else None, operation_id),
            )

    @staticmethod
    def _validate_plan(operation: dict[str, Any]) -> dict[str, Any]:
        if operation["operation_state"] != "planned":
            raise GatewayError("gateway_install_operation_not_planned")
        encoded = str(operation["immutable_plan_json"])
        observed = hashlib.sha256(encoded.encode()).hexdigest()
        if observed != operation["plan_sha256"]:
            raise GatewayError("gateway_install_plan_changed")
        try:
            plan = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise GatewayError("gateway_install_plan_changed") from exc
        if (
            plan.get("operation_id") != operation["public_id"]
            or plan.get("operation_kind") != "install"
            or plan.get("destination") != "managed-language-pack-registry"
            or plan.get("enable") is not False
        ):
            raise GatewayError("gateway_install_plan_changed")
        return plan

    def _copy_verified(self, source: Path, destination: Path) -> None:
        self.storage.safe_existing(source)
        if destination.exists() or destination.is_symlink():
            raise GatewayError("storage_unsafe")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(destination, flags, 0o600)
        try:
            with source.open("rb") as input_handle, os.fdopen(descriptor, "wb") as output:
                shutil.copyfileobj(input_handle, output, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    def _verify_cached(self, path: Path, size: int, sha256: str) -> None:
        self.storage.safe_existing(path)
        if path.stat().st_size != size:
            raise GatewayError("cache_corrupt")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != sha256:
            raise GatewayError("cache_corrupt")
