from __future__ import annotations

import hashlib
import secrets
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(slots=True)
class ApprovalGrant:
    token: str
    chat_id: str
    fingerprint: str
    skill_name: str
    created_at: str
    expires_at: str
    expires_monotonic: float
    action_plan: dict[str, Any] | None = None
    change_proposal_id: str | None = None
    validation_cycle_id: str | None = None
    consumed: bool = False
    cancelled: bool = False


class ApprovalStore:
    def __init__(self, *, ttl_seconds: int = 120) -> None:
        self.ttl_seconds = max(30, min(ttl_seconds, 600))
        self._grants: dict[str, ApprovalGrant] = {}
        self._lock = threading.RLock()

    @staticmethod
    def fingerprint(chat_id: str, text: str, attachment_ids: list[str]) -> str:
        payload = "\0".join((chat_id, text, *sorted(attachment_ids)))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def create(
        self,
        *,
        chat_id: str,
        fingerprint: str,
        skill_name: str,
        action_plan: dict[str, Any] | None = None,
        change_proposal_id: str | None = None,
        validation_cycle_id: str | None = None,
    ) -> ApprovalGrant:
        with self._lock:
            self._purge()
            token = secrets.token_urlsafe(32)
            now = datetime.now(UTC)
            grant = ApprovalGrant(
                token=token,
                chat_id=chat_id,
                fingerprint=fingerprint,
                skill_name=skill_name,
                created_at=now.isoformat(),
                expires_at=(now + timedelta(seconds=self.ttl_seconds)).isoformat(),
                expires_monotonic=time.monotonic() + self.ttl_seconds,
                action_plan=deepcopy(action_plan) if action_plan is not None else None,
                change_proposal_id=(
                    str(change_proposal_id) if change_proposal_id is not None else None
                ),
                validation_cycle_id=(
                    str(validation_cycle_id) if validation_cycle_id is not None else None
                ),
            )
            self._grants[token] = grant
            return grant

    def consume(self, token: str, *, chat_id: str, fingerprint: str) -> ApprovalGrant:
        with self._lock:
            self._purge()
            grant = self._grants.get(token)
            if grant is None:
                raise ValueError("La aprobación no existe o expiró.")
            if grant.cancelled:
                raise ValueError("La aprobación fue cancelada.")
            if grant.consumed:
                raise ValueError("La aprobación ya fue utilizada.")
            if grant.chat_id != chat_id or grant.fingerprint != fingerprint:
                raise ValueError("La aprobación no corresponde a esta solicitud.")
            grant.consumed = True
            return grant

    def peek(self, token: str, *, chat_id: str) -> ApprovalGrant | None:
        with self._lock:
            self._purge()
            grant = self._grants.get(token)
            if grant is None or grant.chat_id != chat_id:
                return None
            return grant

    def cancel(self, token: str, *, chat_id: str) -> bool:
        with self._lock:
            grant = self._grants.get(token)
            if grant is None or grant.chat_id != chat_id or grant.consumed:
                return False
            grant.cancelled = True
            return True

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [token for token, grant in self._grants.items() if grant.expires_monotonic <= now]
        for token in expired:
            self._grants.pop(token, None)
