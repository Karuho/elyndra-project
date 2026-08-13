from __future__ import annotations

from dataclasses import dataclass

from elyndra.approvals import ApprovalStore
from elyndra.online_gateway.errors import GatewayError


@dataclass(slots=True)
class NetworkPermit:
    _account_id: str
    _operation_id: str
    _plan_sha256: str
    _used: bool = False

    def __reduce__(self) -> object:
        raise TypeError("NetworkPermit no es serializable.")

    def consume(self, *, account_id: str, operation_id: str, plan_sha256: str) -> None:
        if self._used:
            raise GatewayError("gateway_permit_reused")
        if (account_id, operation_id, plan_sha256) != (
            self._account_id,
            self._operation_id,
            self._plan_sha256,
        ):
            raise GatewayError("gateway_permit_mismatch")
        self._used = True


class GatewayApprovalService:
    def __init__(self, store: ApprovalStore | None = None) -> None:
        self._store = store or ApprovalStore()

    def request(self, *, account_id: str, operation_id: str, plan_sha256: str) -> str:
        fingerprint = ApprovalStore.fingerprint(account_id, plan_sha256, [operation_id])
        return self._store.create(
            chat_id=account_id,
            fingerprint=fingerprint,
            skill_name="online_gateway.plan",
        ).token

    def consume(
        self, token: str, *, account_id: str, operation_id: str, plan_sha256: str
    ) -> NetworkPermit:
        fingerprint = ApprovalStore.fingerprint(account_id, plan_sha256, [operation_id])
        try:
            self._store.consume(token, chat_id=account_id, fingerprint=fingerprint)
        except ValueError as exc:
            raise GatewayError("gateway_approval_invalid") from exc
        return NetworkPermit(account_id, operation_id, plan_sha256)
