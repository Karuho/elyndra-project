from __future__ import annotations

from typing import Any

from elyndra.audit import AuditRepository
from elyndra.online_gateway.errors import GatewayError

_ALLOWED = {
    "operation_id",
    "account_id",
    "operation_kind",
    "source_id",
    "artifact_key",
    "hostname",
    "expected_size",
    "expected_sha256",
    "state",
    "error_code",
    "approval_request_id",
    "created_at",
    "updated_at",
    "http_status",
    "http_status_class",
    "final_host",
    "redirect_count",
    "redirect_hosts",
    "resumable",
}


class GatewayAudit:
    def __init__(self, repository: AuditRepository) -> None:
        self.repository = repository

    def record(self, *, account_id: str, outcome: str, details: dict[str, Any]) -> int:
        safe = {key: value for key, value in details.items() if key in _ALLOWED}
        diagnostic = GatewayError("audit_context", context=safe).context
        for key in {
            "http_status",
            "http_status_class",
            "final_host",
            "redirect_count",
            "redirect_hosts",
            "resumable",
        }:
            safe.pop(key, None)
        safe.update(diagnostic)
        return self.repository.record(
            actor=f"account:{account_id}",
            action="online_gateway.operation",
            outcome=outcome,
            target=str(safe.get("operation_id", "")),
            details=safe,
        )

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_recent(limit, action="online_gateway.operation")
