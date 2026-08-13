from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import threading
import time
import webbrowser
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from contextlib import suppress
from html import escape
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from elyndra import __version__
from elyndra.application import ElyndraApplication
from elyndra.documents import document_capabilities, process_document
from elyndra.engines import ConversationTurn
from elyndra.ethics import ethics_status, principles
from elyndra.language_packs.bundles import DEFAULT_QUERY_PRIORITIES, LanguageBundleService
from elyndra.models import LanguageConfig, LanguageConfigError
from elyndra.personal_organizer import local_today
from elyndra.scheduler import SchedulerController
from elyndra.session_continuity import build_session_guidance
from elyndra.skills.base import SkillResult
from elyndra.web.approvals import ApprovalStore

_HOST = "127.0.0.1"
_DEFAULT_PORT = 8765
_AUTH_COOKIE = "elyndra_session"
_MAX_BODY_BYTES = 8 * 1024 * 1024
_MAX_MESSAGE_CHARS = 12_000
_MAX_ACTIVE_CHAT_WINDOWS = 12
_MAX_PINNED_CHATS = 5
_ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
_DEFAULT_TITLES = {
    "es": "Nuevo chat",
    "en": "New chat",
    "pt": "Novo chat",
    "fr": "Nouvelle discussion",
    "de": "Neuer Chat",
    "it": "Nuova chat",
    "zh": "新对话",
    "ja": "新しいチャット",
    "ko": "새 채팅",
}


class ElyndraWebService:
    """Small application layer shared by the local HTTP handler and tests."""

    def __init__(self, app: ElyndraApplication) -> None:
        self.app = app
        self.registry_accounts = app.registry_accounts
        self.root_paths = app.root_paths
        self._active_account_public_id = app.account_public_id
        self._lock = threading.RLock()
        self._session_history: OrderedDict[str, deque[ConversationTurn]] = OrderedDict()
        self._approvals = ApprovalStore(ttl_seconds=120)
        self._scheduler = SchedulerController(
            app.scheduler,
            actor=app.identity.system_user,
        )

    def ensure_session_app(self, session_token: str) -> dict[str, Any] | None:
        account = self.registry_accounts.account_for_session(session_token)
        if account is None:
            return None
        public_id = str(account["public_id"])
        with self._lock:
            if public_id != self._active_account_public_id:
                self._scheduler.close()
                self.app = ElyndraApplication.load_for_account(
                    public_id, self.root_paths
                )
                self._active_account_public_id = public_id
                self._session_history.clear()
                self._approvals = ApprovalStore(ttl_seconds=120)
                self._scheduler = SchedulerController(
                    self.app.scheduler, actor=self.app.identity.system_user
                )
        return account

    def bootstrap(self, *, session_token: str = "") -> dict[str, Any]:
        language = self._preferred_language()
        account = self.ensure_session_app(session_token)
        registered = self.registry_accounts.has_account()
        return {
            "version": __version__,
            "agent_name": self.app.persona.agent_name,
            "project_name": self.app.persona.project_name,
            "owner_name": (
                str(account.get("preferred_name") or account.get("username"))
                if account is not None
                else ""
            ),
            "auth": {
                "registered": registered,
                "authenticated": account is not None,
                "account": account,
                "single_account": False,
                "account_count": self.registry_accounts.account_count(),
                "maximum_accounts": 16,
                "isolated_account_vaults": True,
                "minimum_age": 18,
            },
            "developer_mode": bool(account and account.get("developer_mode")),
            "engine": self.app.language_engine.name,
            "offline": self.app.config.offline,
            "network_allowed": self.app.config.network_allowed,
            "telemetry": self.app.config.telemetry,
            "preferred_language": language,
            "default_chat_title": _DEFAULT_TITLES.get(language, "Nuevo chat"),
            "supports_vision": bool(
                getattr(self.app.language_engine, "supports_vision", False)
            ),
            "attachment_max_bytes": 5 * 1024 * 1024,
            "attachment_max_count": 5,
            "document_capabilities": document_capabilities(),
            "runtime_version": __version__,
            "interface_parity": {
                "cli_and_web_share_application_ask": True,
                "deterministic_routes_shared": True,
                "personal_automation_shared": True,
                "local_scheduler_shared": True,
                "local_notifications_shared": True,
                "semantic_intent_resolution_shared": True,
                "reviewed_language_learning_shared": True,
                "spanish_lexical_core_shared": True,
                "stale_runtime_visible": True,
            },
        }

    def language_pack_status(self) -> dict[str, Any]:
        items = self.app.language_packs.list_all()
        required = {
            "elyndra-es-informal",
            "elyndra-es-wiktionary",
            "elyndra-es-mcr-omw",
            "elyndra-es-cldr",
        }
        present = {str(item["logical_pack_id"]) for item in items}
        return {
            "items": items,
            "runtime_version": __version__,
            "spanish_core": {
                "bundle_id": "elyndra-es-core",
                "bundle_version": "2026.08.01-r1",
                "installed": required <= present,
                "present": sorted(required & present),
                "missing": sorted(required - present),
                "automatic_download": False,
                "expected_priorities": dict(DEFAULT_QUERY_PRIORITIES),
            },
        }

    def inspect_language_bundle(self, payload: dict[str, Any]) -> dict[str, Any]:
        return LanguageBundleService(self.app.language_packs).inspect(
            Path(str(payload.get("manifest", "")))
        )

    def install_language_bundle(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not bool(payload.get("approved", False)):
            raise PermissionError("La instalación requiere confirmación explícita.")
        return LanguageBundleService(self.app.language_packs).install(
            Path(str(payload.get("manifest", ""))),
            actor=self.app.identity.system_user,
            enable=bool(payload.get("enable", False)),
        )

    def inspect_language_pack(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not bool(payload.get("approved", False)):
            raise PermissionError("La inspección requiere confirmación explícita.")
        item = self.app.language_packs.inspect(Path(str(payload.get("path", ""))))
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="language_pack.inspect",
            target=str(item["pack_id"]),
            outcome="success",
            details={"manifest_sha256": item["manifest_sha256"]},
        )
        return item

    def install_language_pack(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not bool(payload.get("approved", False)):
            raise PermissionError("La instalación requiere confirmación explícita.")
        item = self.app.language_packs.install(
            Path(str(payload.get("path", ""))),
            actor=self.app.identity.system_user,
            query_priority=int(payload.get("query_priority", 100)),
        )
        self.app.audit.record(actor=self.app.identity.system_user,
                              action="language_pack.install", target=item["public_id"],
                              outcome="success")
        return item

    def set_language_pack_enabled(
        self, payload: dict[str, Any], *, enabled: bool
    ) -> dict[str, Any]:
        if not bool(payload.get("approved", False)):
            raise PermissionError("El cambio requiere confirmación explícita.")
        item = self.app.language_packs.set_enabled(str(payload.get("id", "")), enabled=enabled)
        self.app.audit.record(actor=self.app.identity.system_user,
                              action=f"language_pack.{'enable' if enabled else 'disable'}",
                              target=item["public_id"], outcome="success")
        return item

    def propose_language_overlay(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not bool(payload.get("approved", False)):
            raise PermissionError("La propuesta requiere confirmación explícita.")
        if self.app.language_overlays is None:
            raise PermissionError("La propuesta requiere una cuenta autenticada.")
        raw_payload = payload.get("payload")
        if not isinstance(raw_payload, dict):
            raise ValueError("payload debe ser un objeto estricto.")
        item = self.app.language_overlays.propose(
            entry_type=str(payload.get("entry_type", "")),
            expression=str(payload.get("expression", "")),
            payload=raw_payload,
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="language_overlay.propose",
            target=item["public_id"],
            outcome="pending",
        )
        return item

    def review_language_overlay(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not bool(payload.get("approved", False)):
            raise PermissionError("La revisión requiere confirmación explícita.")
        if self.app.language_overlays is None:
            raise PermissionError("La revisión requiere una cuenta autenticada.")
        item = self.app.language_overlays.review(
            str(payload.get("id", "")),
            decision=str(payload.get("decision", "")),
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="language_overlay.review",
            target=str(payload.get("id", "")),
            outcome=str(item["status"]),
        )
        return item

    def register_account(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if not bool(payload.get("approved", False)):
            raise PermissionError("El registro requiere confirmación explícita.")
        account = self.registry_accounts.register(
            username=str(payload.get("username", "")),
            email=str(payload.get("email", "")),
            password=str(payload.get("password", "")),
            password_confirmation=str(payload.get("password_confirmation", "")),
            birth_date=str(payload.get("birth_date", "")),
            preferred_name=str(payload.get("preferred_name", "")),
            system_user=self.app.identity.system_user,
            developer_mode=bool(payload.get("developer_mode", False)),
            telemetry_enabled=bool(payload.get("telemetry_enabled", False)),
            timezone=str(payload.get("timezone", "America/Santiago")),
            language=str(payload.get("language", "es-CL")),
        )
        self.registry_accounts.revoke_interface_sessions("web")
        account, token = self.registry_accounts.authenticate(
            login=str(account["username"]),
            password=str(payload.get("password", "")),
            interface="web",
            user_agent=str(payload.get("user_agent", "")),
        )
        self.ensure_session_app(token)
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="account.register",
            target=str(account["public_id"]),
            outcome="success",
            details={
                "interface": "web",
                "developer_mode": bool(account["developer_mode"]),
                "telemetry_enabled": bool(account["telemetry_enabled"]),
                "password_stored": False,
            },
        )
        return account, token

    def login_account(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        self.registry_accounts.revoke_interface_sessions("web")
        account, token = self.registry_accounts.authenticate(
            login=str(payload.get("login", "")),
            password=str(payload.get("password", "")),
            interface="web",
            user_agent=str(payload.get("user_agent", "")),
        )
        self.ensure_session_app(token)
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="account.login",
            target=str(account["public_id"]),
            outcome="success",
            details={"interface": "web", "password_stored": False},
        )
        return account, token

    def account_overview(self, token: str) -> dict[str, Any]:
        account = self.ensure_session_app(token)
        if account is None:
            raise PermissionError("Sesión web no autenticada.")
        return {
            "account": account,
            "security": self.app.accounts.security_status(),
            "telemetry": self.app.accounts.telemetry_preview(),
        }

    def online_status(self) -> dict[str, Any]:
        gateway = self.app.online_gateway
        if gateway is None:
            raise PermissionError("No hay una cuenta seleccionada.")
        return gateway.status() | {
            "operations": gateway.operations(limit=25),
            "quarantine": gateway.quarantine(),
            "execution_surface": "cli-only",
        }

    def online_sources(self) -> list[dict[str, Any]]:
        gateway = self.app.online_gateway
        if gateway is None:
            raise PermissionError("No hay una cuenta seleccionada.")
        return gateway.sources.list()

    def online_operations(self) -> list[dict[str, Any]]:
        gateway = self.app.online_gateway
        if gateway is None:
            raise PermissionError("No hay una cuenta seleccionada.")
        return gateway.operations()

    def online_source(self, source_id: str) -> dict[str, Any]:
        gateway = self.app.online_gateway
        if gateway is None:
            raise PermissionError("No hay una cuenta seleccionada.")
        return gateway.sources.get(source_id)

    def online_operation(self, operation_id: str) -> dict[str, Any]:
        gateway = self.app.online_gateway
        if gateway is None:
            raise PermissionError("No hay una cuenta seleccionada.")
        return gateway.operation(operation_id)

    def online_preview(self, source_id: str) -> dict[str, Any]:
        gateway = self.app.online_gateway
        if gateway is None:
            raise PermissionError("No hay una cuenta seleccionada.")
        return gateway.preview_download(source_id)

    def online_write(self, payload: dict[str, Any]) -> dict[str, Any]:
        gateway = self.app.online_gateway
        if gateway is None:
            raise PermissionError("No hay una cuenta seleccionada.")
        if not bool(payload.get("approved", False)):
            raise PermissionError("La operación online requiere aprobación explícita.")
        action = str(payload.get("action", ""))
        if action == "mode-set":
            return gateway.set_mode(str(payload.get("mode", "")))
        if action == "bundle-prepare":
            return gateway.prepare_bundle_install(str(payload.get("source_id", "")))
        if action == "bundle-install":
            operation_id = str(payload.get("operation_id", ""))
            approval = gateway.request_bundle_install_approval(operation_id)
            return gateway.install_bundle(operation_id, approval=approval)
        if action == "bundle-install-cancel":
            return gateway.cancel_bundle_install(str(payload.get("operation_id", "")))
        if action == "cancel-download":
            return gateway.cancel_download(str(payload.get("operation_id", "")))
        if action == "discard-partial":
            operation_id = str(payload.get("operation_id", ""))
            return {"discarded": gateway.discard_partial(operation_id)}
        if action == "history-clear":
            return {"cleared": gateway.clear_history()}
        raise ValueError("Operación online no reconocida.")

    def update_account_profile(
        self, token: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if self.ensure_session_app(token) is None:
            raise PermissionError("Sesión web no autenticada.")
        account = self.app.accounts.update_profile(
            approved=bool(payload.get("approved", False)),
            preferred_name=payload.get("preferred_name"),
            pronouns=payload.get("pronouns"),
            sex=payload.get("sex"),
            gender_identity=payload.get("gender_identity"),
            sexual_orientation=payload.get("sexual_orientation"),
            timezone=payload.get("timezone"),
            language=payload.get("language"),
            developer_mode=payload.get("developer_mode"),
            telemetry_enabled=payload.get("telemetry_enabled"),
            birthday_greeting_enabled=payload.get("birthday_greeting_enabled"),
        )
        self.app.refresh_account_identity()
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="account.profile.update",
            target=str(account["public_id"]),
            outcome="success",
            details={
                "interface": "web",
                "developer_mode": bool(account["developer_mode"]),
                "telemetry_enabled": bool(account["telemetry_enabled"]),
                "sensitive_values_logged": False,
            },
        )
        return account

    def change_account_email(
        self, token: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if self.ensure_session_app(token) is None:
            raise PermissionError("Sesión web no autenticada.")
        return self.app.accounts.change_email(
            password=str(payload.get("password", "")),
            email=str(payload.get("email", "")),
            approved=bool(payload.get("approved", False)),
        )

    def change_account_password(self, token: str, payload: dict[str, Any]) -> None:
        if self.ensure_session_app(token) is None:
            raise PermissionError("Sesión web no autenticada.")
        self.app.accounts.change_password(
            current_password=str(payload.get("current_password", "")),
            new_password=str(payload.get("new_password", "")),
            confirmation=str(payload.get("password_confirmation", "")),
            approved=bool(payload.get("approved", False)),
        )

    def export_account(self, token: str, payload: dict[str, Any]) -> bytes:
        if self.ensure_session_app(token) is None:
            raise PermissionError("Sesión web no autenticada.")
        return self.app.accounts.export_encrypted_payload(
            account_password=str(payload.get("password", "")),
            export_passphrase=str(payload.get("export_passphrase", "")),
            approved=bool(payload.get("approved", False)),
            destination_label="local-web-download",
        )

    def list_chats(
        self,
        *,
        query: str = "",
        status: str = "active",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clean_status = status.strip().casefold()
        if clean_status not in {"active", "archived", "pinned"}:
            raise ValueError("El estado debe ser active, archived o pinned.")
        clean_query = query.strip()
        if clean_status == "pinned":
            chats = self.app.chats.list_pinned(min(_MAX_PINNED_CHATS, max(1, limit)))
        elif clean_status == "archived":
            chats = self.app.chats.list_archived(max(1, min(limit, 100)))
        elif clean_query:
            chats = self.app.chats.search(clean_query, limit=max(1, min(limit, 100)))
            chats = [item for item in chats if not bool(item.get("pinned", 0))]
        else:
            chats = [
                item
                for item in self.app.chats.list_active(max(1, min(limit + 5, 105)))
                if not bool(item.get("pinned", 0))
            ][:limit]
        if clean_query and clean_status in {"archived", "pinned"}:
            folded = clean_query.casefold()
            chats = [
                item
                for item in chats
                if folded
                in " ".join(
                    (
                        str(item.get("title", "")),
                        str(item.get("summary", "")),
                        str(item.get("project", "")),
                    )
                ).casefold()
            ]
        return [self._public_chat(item) for item in chats]

    def create_chat(
        self,
        *,
        title: str | None = None,
        transcript_mode: str = "full",
    ) -> dict[str, Any]:
        with self._lock:
            chat = self.app.chats.create(
                title=title or self.bootstrap()["default_chat_title"],
                transcript_mode=transcript_mode,
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.chat.create",
                target=str(chat["public_id"]),
                outcome="success",
                details={"transcript_mode": transcript_mode},
            )
            return self.chat_detail(str(chat["public_id"]))

    def chat_detail(self, chat_id: str) -> dict[str, Any]:
        chat = self.app.chats.get_any(chat_id)
        if chat is None:
            raise ValueError(f"Chat no encontrado: {chat_id}")
        turns = self.app.chats.recent_turns(chat_id, limit=100)
        summary = (
            self.app.chat_summary(chat_id)
            if str(chat["status"]) == "active"
            else str(chat.get("summary", ""))
        )
        attachments = self.app.attachments.list_for_chat(chat_id)
        attachments_by_turn: dict[int, list[dict[str, Any]]] = {}
        pending: list[dict[str, Any]] = []
        for item in attachments:
            public = self.app.attachments._public(item)
            turn_index = item.get("turn_index")
            if turn_index is None:
                if str(item.get("status")) == "pending":
                    pending.append(public)
                continue
            attachments_by_turn.setdefault(int(turn_index), []).append(public)
        return {
            "chat": self._public_chat(chat),
            "summary": summary,
            "pending_attachments": pending,
            "turns": [
                {
                    "turn_index": int(item["turn_index"]),
                    "user_text": str(item["user_text"]),
                    "assistant_text": str(item["assistant_text"]),
                    "created_at": str(item["created_at"]),
                    "attachments": attachments_by_turn.get(int(item["turn_index"]), []),
                }
                for item in turns
            ],
        }

    def rename_chat(self, chat_id: str, title: str) -> dict[str, Any]:
        with self._lock:
            chat = self.app.chats.rename(chat_id, title)
            return self._public_chat(chat)

    def set_transcript_mode(self, chat_id: str, mode: str) -> dict[str, Any]:
        with self._lock:
            chat = self.app.chats.set_transcript_mode(chat_id, mode)
            return self._public_chat(chat)

    def set_pinned(self, chat_id: str, pinned: bool) -> dict[str, Any]:
        with self._lock:
            current = self.app.chats.get(chat_id)
            if current is None:
                raise ValueError(f"Chat no encontrado: {chat_id}")
            if (
                pinned
                and not bool(current.get("pinned", 0))
                and self.app.chats.count_pinned() >= _MAX_PINNED_CHATS
            ):
                raise ValueError("Puedes anclar como máximo 5 conversaciones.")
            chat = self.app.chats.set_pinned(chat_id, pinned)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.chat.pin",
                target=chat_id,
                outcome="success",
                details={"pinned": pinned},
            )
            return self._public_chat(chat)

    def archive_chat(self, chat_id: str) -> dict[str, Any]:
        with self._lock:
            chat = self.app.chats.archive(chat_id)
            self._session_history.pop(chat_id, None)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.chat.archive",
                target=chat_id,
                outcome="success",
            )
            return self._public_chat(chat)

    def restore_chat(self, chat_id: str) -> dict[str, Any]:
        with self._lock:
            chat = self.app.chats.restore(chat_id)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.chat.restore",
                target=chat_id,
                outcome="success",
            )
            return self._public_chat(chat)

    def delete_chat_permanently(self, chat_id: str) -> dict[str, Any]:
        with self._lock:
            manifest = self.app.chats.deletion_manifest(chat_id)
            removed_files = 0
            for value in [*manifest["archive_paths"], *manifest["attachment_paths"]]:
                path = Path(value)
                if not path.exists():
                    continue
                try:
                    path.unlink()
                except OSError as exc:
                    raise ValueError(
                        f"No se pudo borrar el archivo local asociado {path}: {exc}"
                    ) from exc
                removed_files += 1
            deleted = self.app.chats.hard_delete(chat_id)
            self._session_history.pop(chat_id, None)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.chat.delete_permanently",
                target=chat_id,
                outcome="success",
                details={"removed_local_files": removed_files},
            )
            return {
                "id": deleted["public_id"],
                "title": deleted["title"],
                "removed_local_files": removed_files,
            }

    def create_attachment(
        self,
        chat_id: str,
        *,
        filename: str,
        mime_type: str,
        data_base64: str,
    ) -> dict[str, Any]:
        try:
            data = base64.b64decode(data_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("El archivo adjunto no contiene base64 válido.") from exc
        with self._lock:
            item = self.app.attachments.create(
                chat_id,
                filename=filename,
                mime_type=mime_type,
                data=data,
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.attachment.create",
                target=str(item["id"]),
                outcome="success",
                details={
                    "chat_id": chat_id,
                    "filename": item["filename"],
                    "kind": item["kind"],
                    "size_bytes": item["size_bytes"],
                    "secrets_redacted": item["secrets_redacted"],
                },
            )
            return item

    def delete_pending_attachment(self, attachment_id: str) -> bool:
        with self._lock:
            deleted = self.app.attachments.delete(attachment_id, pending_only=True)
            if deleted:
                self.app.audit.record(
                    actor=self.app.identity.system_user,
                    action="web.attachment.delete_pending",
                    target=attachment_id,
                    outcome="success",
                )
            return deleted

    def attachment_content(self, attachment_id: str):
        return self.app.attachments.content(attachment_id)

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        attachment_ids: list[str] | None = None,
        approval_token: str | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        clean_attachment_ids = [str(value) for value in (attachment_ids or [])]
        original_text = text.strip()
        clean_text = original_text
        if not clean_text and clean_attachment_ids:
            clean_text = "Analiza y resume los archivos adjuntos."
        if not clean_text:
            raise ValueError("El mensaje no puede estar vacío.")
        if len(clean_text) > _MAX_MESSAGE_CHARS:
            raise ValueError(
                f"El mensaje supera el límite local de {_MAX_MESSAGE_CHARS} caracteres."
            )

        request_fingerprint = self._approvals.fingerprint(
            chat_id, clean_text, clean_attachment_ids
        )
        approved = False
        consumed_grant = None
        if approval_token:
            consumed_grant = self._approvals.consume(
                approval_token,
                chat_id=chat_id,
                fingerprint=request_fingerprint,
            )
            approved = True
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.skill_approval.consume",
                target=consumed_grant.skill_name,
                outcome="success",
                details={
                    "chat_id": chat_id,
                    "approval_token_consumed": True,
                },
            )

        with self._lock:
            chat = self.app.chats.touch(chat_id)
            attachment_context, public_attachments = self.app.attachments.context_blocks(
                chat_id, clean_attachment_ids
            )
            image_data = self.app.attachments.image_payloads(
                chat_id, clean_attachment_ids
            )
            supports_vision = bool(
                getattr(self.app.language_engine, "supports_vision", False)
            )
            image_only = bool(public_attachments) and all(
                item["kind"] == "image" for item in public_attachments
            )
            history = self._history_for(chat_id)
            summary = self.app.chat_summary(chat_id)
            started = time.perf_counter()
            validation_result = _validation_reply(
                clean_text,
                public_attachments,
            ) or _inline_validation_reply(clean_text)
            if validation_result is not None:
                result = validation_result
            elif image_only and image_data and not supports_vision:
                result = SkillResult(
                    True,
                    (
                        "La imagen quedó adjunta y guardada localmente, pero el modelo "
                        "lingüístico actual no tiene capacidad visual. Puedo conservarla "
                        "en este chat; para analizar su contenido debes configurar un "
                        "motor local que declare visión."
                    ),
                    {
                        "engine": "attachment-gateway",
                        "generated": False,
                        "fast_path": "vision_unavailable",
                    },
                )
            else:
                result = self.app.ask(
                    clean_text,
                    approved=approved,
                    approved_action_plan=(
                        consumed_grant.action_plan if consumed_grant is not None else None
                    ),
                    approved_change_proposal_id=(
                        consumed_grant.change_proposal_id
                        if consumed_grant is not None
                        else None
                    ),
                    approved_validation_cycle_id=(
                        consumed_grant.validation_cycle_id
                        if consumed_grant is not None
                        else None
                    ),
                    history=tuple(history),
                    interactive=True,
                    session_summary=summary,
                    chat_id=chat_id,
                    attachment_context=attachment_context,
                    image_data=image_data if supports_vision else (),
                    on_token=on_token,
                )
            elapsed_ms = round((time.perf_counter() - started) * 1000)

            result_engine = str(result.data.get("engine", ""))
            record_result = (
                result.ok
                or result_engine.startswith("local-skill")
                or (
                    (
                        result_engine.startswith("assistant-orchestrator")
                        or result_engine.startswith("assistant-change-proposal")
                        or result_engine.startswith("assistant-validation-cycle")
                    )
                    and not bool(result.data.get("approval_required"))
                )
            )
            if record_result:
                previous_turn_count = int(chat["turn_count"])
                chat = self.app.record_chat_turn(
                    chat_id,
                    user_text=clean_text,
                    assistant_text=result.message,
                )
                if clean_attachment_ids:
                    public_attachments = self.app.attachments.bind_to_turn(
                        chat_id,
                        clean_attachment_ids,
                        turn_index=int(chat["turn_count"]),
                    )
                if previous_turn_count == 0 and str(chat["title"]) in set(
                    _DEFAULT_TITLES.values()
                ):
                    title_source = original_text
                    if not title_source and public_attachments:
                        title_source = f"Archivo: {public_attachments[0]['filename']}"
                    chat = self.app.chats.rename(
                        chat_id,
                        _title_from_message(title_source or clean_text),
                    )
                history.append(
                    ConversationTurn(user=clean_text, assistant=result.message)
                )

            pending_approval = None
            if result.data.get("approval_required") and not approval_token:
                skill_name = str(result.data.get("skill_name") or "skill")
                pending_approval = self._approvals.create(
                    chat_id=chat_id,
                    fingerprint=request_fingerprint,
                    skill_name=skill_name,
                    action_plan=(
                        result.data.get("action_plan")
                        if isinstance(result.data.get("action_plan"), dict)
                        else None
                    ),
                    change_proposal_id=(
                        str(result.data.get("change_proposal_id"))
                        if result.data.get("change_proposal_id")
                        else None
                    ),
                    validation_cycle_id=(
                        str(result.data.get("validation_cycle_id"))
                        if result.data.get("validation_cycle_id")
                        else None
                    ),
                )
                self.app.audit.record(
                    actor=self.app.identity.system_user,
                    action="web.skill_approval.create",
                    target=skill_name,
                    outcome="pending",
                    details={
                        "chat_id": chat_id,
                        "expires_at": pending_approval.expires_at,
                        "plan_id": result.data.get("plan_id"),
                        "plan_steps": result.data.get("plan_steps"),
                        "change_proposal_id": result.data.get("change_proposal_id"),
                        "validation_cycle_id": result.data.get("validation_cycle_id"),
                    },
                )

            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.chat.message",
                target=chat_id,
                outcome="success" if result.ok else "failed",
                details={
                    "elapsed_ms": elapsed_ms,
                    "engine": result.data.get("engine", "unknown"),
                    "generated": bool(result.data.get("generated", False)),
                    "attachments": len(public_attachments),
                    "approved": approved,
                },
            )
            return {
                "ok": result.ok,
                "message": result.message,
                "elapsed_ms": elapsed_ms,
                "chat": self._public_chat(chat),
                "summary": self.app.chat_summary(chat_id),
                "attachments": public_attachments,
                "meta": {
                    "runtime_version": __version__,
                    "interface": "web",
                    "shared_application_runtime": True,
                    "engine": result.data.get("engine"),
                    "generated": result.data.get("generated", False),
                    "fast_path": result.data.get("fast_path"),
                    "semantic": result.data.get("semantic"),
                    "timings": result.data.get("timings", {}),
                    "alexandria_domains": result.data.get("alexandria_domains", []),
                    "alexandria_units": len(result.data.get("alexandria", [])),
                    "max_tokens": result.data.get("max_tokens"),
                    "risk": result.data.get("risk"),
                    "approval_required": result.data.get("approval_required", False),
                    "approval_summary": result.data.get("approval_summary"),
                    "skill_name": (
                        result.data.get("skill_name") or result.data.get("skill")
                    ),
                    "returncode": result.data.get("returncode"),
                    "duration_ms": result.data.get("duration_ms"),
                    "authorization_scope": result.data.get("authorization_scope"),
                    "authorization_source": result.data.get("authorization_source"),
                    "resolved_path": result.data.get("resolved_path"),
                    "project_root": result.data.get("project_root"),
                    "tool": result.data.get("tool"),
                    "timeout_seconds": result.data.get("timeout_seconds"),
                    "action_argv": result.data.get("action_argv", []),
                    "orchestration": result.data.get("orchestration", False),
                    "action_plan": result.data.get("action_plan"),
                    "action_run_id": result.data.get("action_run_id"),
                    "plan_id": result.data.get("plan_id"),
                    "plan_source": result.data.get("plan_source"),
                    "plan_steps": result.data.get("plan_steps"),
                    "executed_steps": result.data.get("executed_steps"),
                    "status": result.data.get("status"),
                    "change_proposal": result.data.get("change_proposal", False),
                    "change_proposal_id": result.data.get("change_proposal_id"),
                    "change_proposal_hash": result.data.get("change_proposal_hash"),
                    "change_project_root": result.data.get("change_project_root"),
                    "change_files": result.data.get("change_files", []),
                    "change_diff": result.data.get("change_diff"),
                    "validation_cycle": result.data.get("validation_cycle", False),
                    "validation_cycle_id": result.data.get("validation_cycle_id"),
                    "repair_cycle": result.data.get("repair_cycle", False),
                    "repair_available": result.data.get("repair_available", False),
                    "development_session_id": result.data.get("development_session_id"),
                    "development_session": result.data.get("development_session"),
                    "suggested_actions": result.data.get("suggested_actions", []),
                    "automatic_execution": result.data.get(
                        "automatic_execution", False
                    ),
                    "approval_token": (
                        pending_approval.token if pending_approval is not None else None
                    ),
                    "approval_expires_at": (
                        pending_approval.expires_at
                        if pending_approval is not None
                        else None
                    ),
                    "approval_single_use": pending_approval is not None,
                },
            }

    def cancel_skill_approval(self, chat_id: str, token: str) -> bool:
        grant = self._approvals.peek(token, chat_id=chat_id)
        cancelled = self._approvals.cancel(token, chat_id=chat_id)
        if cancelled and grant is not None and grant.change_proposal_id:
            with suppress(RuntimeError, ValueError):
                self.app.reject_saved_change_proposal(
                    grant.change_proposal_id, approved=True
                )
        if cancelled and grant is not None and grant.validation_cycle_id:
            with suppress(RuntimeError, ValueError):
                self.app.validation_cycles.cancel(
                    grant.validation_cycle_id, actor=self.app.identity.system_user
                )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.skill_approval.cancel",
            target=chat_id,
            outcome="success" if cancelled else "not_found",
        )
        return cancelled

    def alexandria_overview(self) -> dict[str, Any]:
        return self.app.alexandria.overview()

    def alexandria_libraries(self, *, query: str = "") -> list[dict[str, Any]]:
        return self.app.alexandria.list_libraries(query=query, limit=100)

    def alexandria_library(self, library_id: str) -> dict[str, Any]:
        library = self.app.alexandria.get_library(library_id)
        if library is None:
            raise ValueError("Biblioteca no encontrada.")
        return {
            "library": library,
            "sources": self.app.alexandria.list_sources(library_id, limit=200),
        }

    def create_alexandria_library(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            library = self.app.alexandria.create_library(
                str(payload.get("name", "")),
                description=str(payload.get("description", "")),
                domain=str(payload.get("domain", "general")),
                language=str(payload.get("language", "auto")),
                version=str(payload.get("version", "1")),
                license_id=str(payload.get("license_id", "unverified")),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.alexandria.library.create",
                target=str(library["public_id"]),
                outcome="success",
            )
            return library

    def update_alexandria_library(
        self,
        library_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            library = self.app.alexandria.update_library(
                library_id,
                name=_optional_string(payload.get("name")),
                description=_optional_string(payload.get("description")),
                domain=_optional_string(payload.get("domain")),
                language=_optional_string(payload.get("language")),
                version=_optional_string(payload.get("version")),
                license_id=_optional_string(payload.get("license_id")),
                enabled=(bool(payload["enabled"]) if "enabled" in payload else None),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.alexandria.library.update",
                target=library_id,
                outcome="success",
                details={"enabled": library["enabled"]},
            )
            return library

    def delete_alexandria_library(self, library_id: str) -> dict[str, Any]:
        with self._lock:
            deleted = self.app.alexandria.delete_library(library_id)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.alexandria.library.delete_permanently",
                target=library_id,
                outcome="success",
                details={
                    "removed_sources": deleted["removed_sources"],
                    "removed_units": deleted["removed_units"],
                    "removed_files": deleted["removed_files"],
                },
            )
            return deleted

    def import_alexandria_source(
        self,
        library_id: str,
        *,
        filename: str,
        data_base64: str,
        title: str = "",
        source_url: str = "",
    ) -> dict[str, Any]:
        try:
            data = base64.b64decode(data_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("La fuente no contiene base64 válido.") from exc
        with self._lock:
            source = self.app.alexandria.import_bytes(
                library_id,
                filename=filename,
                data=data,
                title=title or None,
                source_url=source_url,
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.alexandria.source.import",
                target=str(source["public_id"]),
                outcome="success",
                details={
                    "library": library_id,
                    "units": source["unit_count"],
                    "validation_status": source["validation_status"],
                },
            )
            return source

    def review_alexandria_source(self, source_id: int) -> dict[str, Any]:
        with self._lock:
            source = self.app.alexandria.review_source(source_id, reviewed=True)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.alexandria.source.review",
                target=str(source_id),
                outcome="success",
            )
            return source

    def search_alexandria(
        self,
        query: str,
        *,
        library: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.app.alexandria.search(query, library=library, limit=30)

    def personal_overview(self, *, date_value: str | None = None) -> dict[str, Any]:
        target = date_value or None
        return {
            "runtime_version": __version__,
            "organizer": self.app.personal_organizer.status(),
            "daily_brief": self.app.personal_organizer.daily_brief(
                target or local_today("America/Santiago").isoformat()
            ),
            "wellbeing": self.app.wellbeing.status(),
            "wellbeing_summary": self.app.wellbeing.summary(days=7),
            "organizer_items": self.app.personal_organizer.list_items(
                status="all", limit=100
            ),
            "reminders": self.app.personal_organizer.list_reminders(
                status="all", limit=100
            ),
            "coaching_plans": self.app.wellbeing.list_plans(
                status="all", limit=100
            ),
            "automation": self.app.automation.status(),
            "automation_policies": self.app.automation.list_policies(
                status="all", limit=100
            ),
            "automations": self.app.automation.list_automations(
                status="all", limit=100
            ),
            "automation_runs": self.app.automation.list_runs(
                status="all", limit=100
            ),
            "automation_inbox": self.app.automation.list_inbox(
                status="all", limit=100
            ),
            "scheduler": self._scheduler.status(),
            "local_notifications": self.app.scheduler.list_notifications(
                status="all", limit=100
            ),
            "semantic_intents": self.app.semantic_intents.status(),
            "intent_resolutions": self.app.semantic_intents.list_resolutions(limit=30),
            "intent_learning_proposals": self.app.semantic_intents.list_proposals(
                status="all", limit=100
            ),
            "interface_parity": {
                "chat_runtime": "ElyndraApplication.ask",
                "cli_runtime": "ElyndraApplication.ask",
                "web_runtime": "ElyndraApplication.ask",
                "deterministic_routes_shared": True,
                "personal_automation_shared": True,
                "local_scheduler_shared": True,
                "local_notifications_shared": True,
                "semantic_intent_resolution_shared": True,
                "reviewed_language_learning_shared": True,
            },
        }

    def create_personal_commitment(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        item = self.app.personal_organizer.create_commitment(
            title=str(payload.get("title", "")),
            description=str(payload.get("description", "")),
            event_date=str(payload.get("date", "")),
            event_time=_optional_string(payload.get("time")),
            timezone=str(payload.get("timezone", "America/Santiago")),
            domain=str(payload.get("domain", "organizacion_personal")),
            project=str(payload.get("project", "")),
            priority=str(payload.get("priority", "normal")),
            recurrence=str(payload.get("recurrence", "once")),
            interval=int(payload.get("interval", 1)),
            weekdays=tuple(str(value) for value in payload.get("weekdays", [])),
            until=_optional_string(payload.get("until")),
            goal_public_id=str(payload.get("goal_id", "")),
            task_public_id=str(payload.get("task_id", "")),
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.organizer.commitment.create",
            target=str(item["public_id"]),
            outcome="success",
            details={"automatic_execution": False},
        )
        return item

    def create_personal_routine(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        item = self.app.personal_organizer.create_routine(
            title=str(payload.get("title", "")),
            description=str(payload.get("description", "")),
            start_date=str(payload.get("start_date", "")),
            event_time=_optional_string(payload.get("time")),
            timezone=str(payload.get("timezone", "America/Santiago")),
            domain=str(payload.get("domain", "organizacion_personal")),
            project=str(payload.get("project", "")),
            priority=str(payload.get("priority", "normal")),
            recurrence=str(payload.get("recurrence", "daily")),
            interval=int(payload.get("interval", 1)),
            weekdays=tuple(str(value) for value in payload.get("weekdays", [])),
            until=_optional_string(payload.get("until")),
            goal_public_id=str(payload.get("goal_id", "")),
            task_public_id=str(payload.get("task_id", "")),
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.organizer.routine.create",
            target=str(item["public_id"]),
            outcome="success",
            details={"automatic_completion": False},
        )
        return item

    def create_personal_birthday(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        item = self.app.personal_organizer.create_birthday(
            person_name=str(payload.get("person", "")),
            month=int(payload.get("month", 0)),
            day=int(payload.get("day", 0)),
            birth_year=_optional_int(payload.get("year")),
            timezone=str(payload.get("timezone", "America/Santiago")),
            domain=str(payload.get("domain", "organizacion_personal")),
            project=str(payload.get("project", "")),
            priority=str(payload.get("priority", "normal")),
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.organizer.birthday.create",
            target=str(item["public_id"]),
            outcome="success",
            details={"automatic_execution": False},
        )
        return item

    def create_wellbeing_checkin(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        item = self.app.wellbeing.create_checkin(
            checkin_date=str(payload.get("date", "")),
            mood=int(payload.get("mood", 0)),
            energy=int(payload.get("energy", 0)),
            stress=int(payload.get("stress", 0)),
            focus=int(payload.get("focus", 0)),
            sleep_hours=_optional_float(payload.get("sleep_hours")),
            sleep_quality=_optional_int(payload.get("sleep_quality")),
            hydration=_optional_int(payload.get("hydration")),
            nutrition=_optional_int(payload.get("nutrition")),
            activity_minutes=_optional_int(payload.get("activity_minutes")),
            note=str(payload.get("note", "")),
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.wellbeing.checkin.create",
            target=str(item["public_id"]),
            outcome="success",
            details={"diagnosis": False, "automatic_intervention": False},
        )
        return item

    def create_routine_checkin(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        item = self.app.personal_organizer.checkin_routine(
            str(payload.get("routine_id", "")),
            occurrence_date=str(payload.get("date", "")),
            status=str(payload.get("status", "completed")),
            note=str(payload.get("note", "")),
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.organizer.routine.checkin",
            target=str(item["public_id"]),
            outcome=str(item["status"]),
            details={"occurrence_date": item["occurrence_date"]},
        )
        return item

    def propose_personal_reminder(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        item = self.app.personal_organizer.propose_reminder(
            str(payload.get("item_id", "")),
            minutes_before=int(payload.get("minutes_before", 0)),
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.organizer.reminder.propose",
            target=str(item["public_id"]),
            outcome="proposed",
            details={"automatic_notification": False},
        )
        return item

    def review_personal_reminder(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        item = self.app.personal_organizer.review_reminder(
            str(payload.get("reminder_id", "")),
            decision=str(payload.get("decision", "")),
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.organizer.reminder.review",
            target=str(item["public_id"]),
            outcome=str(item["status"]),
            details={"automatic_notification": False},
        )
        return item

    def update_personal_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        item = self.app.personal_organizer.update_item_status(
            str(payload.get("item_id", "")),
            status=str(payload.get("status", "")),
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.organizer.item.update",
            target=str(item["public_id"]),
            outcome=str(item["status"]),
            details={"automatic_execution": False},
        )
        return item

    def create_coaching_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        raw_actions = payload.get("actions", [])
        if not isinstance(raw_actions, list):
            raise ValueError("actions debe ser una lista.")
        item = self.app.wellbeing.create_plan(
            title=str(payload.get("title", "")),
            focus=str(payload.get("focus", "")),
            objective=str(payload.get("objective", "")),
            start_date=str(payload.get("start_date", "")),
            review_date=_optional_string(payload.get("review_date")),
            actions=tuple(str(value) for value in raw_actions),
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.coaching.plan.create",
            target=str(item["public_id"]),
            outcome="success",
            details={"actions": len(item["actions"]), "automatic_execution": False},
        )
        return item

    def update_coaching_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        item = self.app.wellbeing.update_plan_status(
            str(payload.get("plan_id", "")),
            status=str(payload.get("status", "")),
        )
        return item

    def update_coaching_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        item = self.app.wellbeing.update_action_status(
            str(payload.get("action_id", "")),
            status=str(payload.get("status", "")),
        )
        return item

    def create_automation_policy(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        item = self.app.automation.create_policy(
            title=str(payload.get("title", "")),
            action_type=str(payload.get("action_type", "")),
            autonomy_level=str(payload.get("autonomy_level", "")),
            timezone=str(payload.get("timezone", "America/Santiago")),
            window_start=_optional_string(payload.get("window_start")),
            window_end=_optional_string(payload.get("window_end")),
            max_runs_per_day=int(payload.get("max_runs_per_day", 1)),
            starts_at=_optional_string(payload.get("starts_at")),
            expires_at=_optional_string(payload.get("expires_at")),
            domain=str(payload.get("domain", "")),
            project=str(payload.get("project", "")),
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.automation.policy.create",
            target=str(item["public_id"]),
            outcome="success",
            details={
                "action_type": item["action_type"],
                "autonomy_level": item["autonomy_level"],
                "background_execution": False,
            },
        )
        return item

    def update_automation_policy(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        return self.app.automation.update_policy_status(
            str(payload.get("policy_id", "")),
            status=str(payload.get("status", "")),
        )

    def create_automation(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        raw_params = payload.get("params", {})
        if not isinstance(raw_params, dict):
            raise ValueError("params debe ser un objeto.")
        item = self.app.automation.create_automation(
            str(payload.get("policy_id", "")),
            title=str(payload.get("title", "")),
            schedule_kind=str(payload.get("schedule_kind", "")),
            start_date=str(payload.get("start_date", "")),
            time_of_day=str(payload.get("time_of_day", "")),
            weekdays=tuple(str(value) for value in payload.get("weekdays", [])),
            month_day=_optional_int(payload.get("month_day")),
            interval=int(payload.get("interval", 1)),
            until_date=_optional_string(payload.get("until_date")),
            params=raw_params,
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.automation.create",
            target=str(item["public_id"]),
            outcome="success",
            details={
                "policy_id": item["policy_public_id"],
                "background_execution": False,
            },
        )
        return item

    def update_automation(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        return self.app.automation.update_automation_status(
            str(payload.get("automation_id", "")),
            status=str(payload.get("status", "")),
        )

    def scan_automations(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        data = self.app.automation.scan_due(
            now_value=_optional_string(payload.get("now")),
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.automation.scan",
            target="foreground-dispatch",
            outcome="success",
            details={**data["summary"], "background_execution": False},
        )
        return data

    def approve_automation_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        item = self.app.automation.approve_run(
            str(payload.get("run_id", "")),
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.automation.run.approve",
            target=str(item["public_id"]),
            outcome=str(item["status"]),
        )
        return item

    def update_automation_inbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        return self.app.automation.update_inbox_status(
            str(payload.get("inbox_id", "")),
            status=str(payload.get("status", "")),
        )

    def scheduler_cycle(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        lease = self.app.scheduler.open(
            interval_seconds=60,
            actor=self.app.identity.system_user,
            mode="web-cycle",
        )
        try:
            data = lease.cycle(now_value=_optional_string(payload.get("now")))
        finally:
            lease.close(status="stopped")
        return data

    def scheduler_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        return self._scheduler.start(
            interval_seconds=int(payload.get("interval_seconds", 60))
        )

    def scheduler_stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        return self._scheduler.stop()

    def update_local_notification(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        return self.app.scheduler.update_notification_status(
            str(payload.get("notification_id", "")),
            status=str(payload.get("status", "")),
        )

    def propose_intent_learning(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        item = self.app.semantic_intents.propose_learning(
            phrase=str(payload.get("phrase", "")),
            intent=str(payload.get("intent", "")),
            source=str(payload.get("source", "owner_correction")),
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.intent_learning.propose",
            target=str(item["public_id"]),
            outcome="pending",
            details={"intent": item["intent"], "silent_learning": False},
        )
        return item

    def review_intent_learning(self, payload: dict[str, Any]) -> dict[str, Any]:
        _require_explicit_web_approval(payload)
        item = self.app.semantic_intents.review_learning(
            str(payload.get("proposal_id", "")),
            decision=str(payload.get("decision", "")),
            actor=self.app.identity.system_user,
        )
        self.app.audit.record(
            actor=self.app.identity.system_user,
            action="web.intent_learning.review",
            target=str(item["public_id"]),
            outcome=str(item["status"]),
            details={"intent": item["intent"], "automatic_activation": False},
        )
        return item

    def control_overview(self) -> dict[str, Any]:
        trusted = self.app.trusted_projects.list_all()
        profiles = self.app.php_profiles.list_all()
        web_profiles = self.app.web_profiles.list_all()
        python_profiles = self.app.python_profiles.list_all()
        java_profiles = self.app.java_profiles.list_all()
        kotlin_profiles = self.app.kotlin_profiles.list_all()
        dotnet_profiles = self.app.dotnet_profiles.list_all()
        swift_profiles = self.app.swift_profiles.list_all()
        dart_profiles = self.app.dart_profiles.list_all()
        sql_profiles = self.app.sql_profiles.list_all()
        native_profiles = self.app.native_profiles.list_all()
        ruby_profiles = self.app.ruby_profiles.list_all()
        go_profiles = self.app.go_profiles.list_all()
        rust_profiles = self.app.rust_profiles.list_all()
        with self.app.database.connect() as connection:
            execution_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM audit_events WHERE action = 'skill.execute'"
                ).fetchone()[0]
            )
            approval_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM audit_events "
                    "WHERE action LIKE 'web.skill_approval.%'"
                ).fetchone()[0]
            )
        verification_count = self.app.verification_runs.count(toolchain="php")
        web_verification_count = self.app.verification_runs.count(toolchain="web")
        python_verification_count = self.app.verification_runs.count(toolchain="python")
        java_verification_count = self.app.verification_runs.count(toolchain="java")
        kotlin_verification_count = self.app.verification_runs.count(toolchain="kotlin")
        dotnet_verification_count = self.app.verification_runs.count(toolchain="dotnet")
        swift_verification_count = self.app.verification_runs.count(toolchain="swift")
        dart_verification_count = self.app.verification_runs.count(toolchain="dart")
        sql_verification_count = self.app.verification_runs.count(toolchain="sql")
        native_verification_count = self.app.verification_runs.count(toolchain="native")
        ruby_verification_count = self.app.verification_runs.count(toolchain="ruby")
        go_verification_count = self.app.verification_runs.count(toolchain="go")
        rust_verification_count = self.app.verification_runs.count(toolchain="rust")
        package_count = len(self.app.alexandria_packages.list_all())
        structured_pack_status = self.app.structured_packs.status()
        action_run_count = self.app.action_runs.count()
        change_proposal_count = self.app.change_proposals.count()
        pending_change_count = self.app.change_proposals.count(status="proposed")
        validation_cycle_count = self.app.validation_cycles.count()
        development_session_count = self.app.development_sessions.count()
        ethics_review_count = self.app.ethics_reviews.count()
        ethics_redirect_count = self.app.ethics_reviews.count(decision="redirect")
        return {
            "trusted_projects": len(trusted),
            "configured_roots": [str(root) for root in self.app.config.allowed_roots],
            "php_profiles": len(profiles),
            "web_profiles": len(web_profiles),
            "python_profiles": len(python_profiles),
            "java_profiles": len(java_profiles),
            "kotlin_profiles": len(kotlin_profiles),
            "dotnet_profiles": len(dotnet_profiles),
            "swift_profiles": len(swift_profiles),
            "dart_profiles": len(dart_profiles),
            "sql_profiles": len(sql_profiles),
            "native_profiles": len(native_profiles),
            "ruby_profiles": len(ruby_profiles),
            "go_profiles": len(go_profiles),
            "rust_profiles": len(rust_profiles),
            "php_verifications": verification_count,
            "web_verifications": web_verification_count,
            "python_verifications": python_verification_count,
            "java_verifications": java_verification_count,
            "kotlin_verifications": kotlin_verification_count,
            "dotnet_verifications": dotnet_verification_count,
            "swift_verifications": swift_verification_count,
            "dart_verifications": dart_verification_count,
            "sql_verifications": sql_verification_count,
            "native_verifications": native_verification_count,
            "ruby_verifications": ruby_verification_count,
            "go_verifications": go_verification_count,
            "rust_verifications": rust_verification_count,
            "alexandria_packages": package_count,
            "alexandria_structured_packs": structured_pack_status,
            "assistant_action_runs": action_run_count,
            "assistant_change_proposals": change_proposal_count,
            "assistant_pending_changes": pending_change_count,
            "assistant_validation_cycles": validation_cycle_count,
            "assistant_development_sessions": development_session_count,
            "constitutional_ethics": True,
            "ethics_core_disableable": False,
            "ethical_advice_enabled": self.app.config.ethical_advice_enabled,
            "ethical_tutor_review_enabled": (
                self.app.config.ethical_tutor_review_enabled
            ),
            "ethics_reviews": ethics_review_count,
            "dictionary": self.app.dictionary.status(),
            "translation": self.app.translator.status(),
            "first_aid": self.app.first_aid.status(),
            "memory_tiers": self.app.tiered_memory.status(),
            "preferences": self.app.preferences.status(),
            "tutors": self.app.tutor_status(),
            "cognitive_executive": self.app.cognitive_executive.status(),
            "ethics_redirects": ethics_redirect_count,
            "skill_executions": execution_count,
            "approval_events": approval_count,
        }

    def control_ethics(self, *, limit: int = 20) -> dict[str, Any]:
        return {
            "status": ethics_status(
                proactive_advice=self.app.config.ethical_advice_enabled,
                tutor_review=self.app.config.ethical_tutor_review_enabled,
            ),
            "principles": [item.to_dict() for item in principles()],
            "reviews": self.app.ethics_reviews.list_recent(limit=limit),
        }

    def control_dictionary(self) -> dict[str, Any]:
        return self.app.dictionary.status()

    def dictionary_lookup(
        self,
        term: str,
        *,
        language: str | None = None,
        output_language: str = "es",
        dialect: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        message, data = self.app.dictionary.render_lookup(
            term,
            language=language,
            output_language=output_language,
            dialect=dialect,
            limit=max(1, min(limit, 20)),
        )
        return {"message": message, **data}

    def control_translation(self) -> dict[str, Any]:
        return self.app.translator.status()

    def translation_lookup(
        self, text: str, *, target_language: str, response_language: str = "es"
    ) -> dict[str, Any]:
        result = self.app.translate(
            text,
            target_language,
            response_language=response_language,
        )
        return {"ok": result.ok, "message": result.message, **result.data}

    def control_preferences(self) -> dict[str, Any]:
        return {
            "status": self.app.preferences.status(),
            "proposals": self.app.preferences.list_proposals(status="pending", limit=50),
            "preferences": self.app.preferences.list_preferences(status="active", limit=100),
        }

    def control_tutors(self) -> dict[str, Any]:
        return {
            "status": self.app.tutor_status(),
            "benchmarks": self.app.tutor_benchmarks.list_runs(limit=20),
            "selections": self.app.tutor_benchmarks.list_selections(limit=50),
            "lesson_proposals": self.app.tutor_learning.list_proposals(
                status="pending", limit=50
            ),
            "lessons": self.app.tutor_learning.list_lessons(
                status="active", limit=100
            ),
            "evidence_comparisons": self.app.tutor_learning.list_comparisons(
                limit=50
            ),
            "lesson_evaluations": self.app.tutor_evolution.list_evaluations(
                status="all", limit=50
            ),
            "durable_knowledge": self.app.tutor_evolution.list_knowledge(
                status="all", limit=100
            ),
            "knowledge_acquisition": self.app.general_knowledge.list_plans(
                status="all", limit=50
            ),
            "general_knowledge": self.app.general_knowledge.list_knowledge(
                status="all", limit=100
            ),
        }

    def control_executive(self) -> dict[str, Any]:
        return {
            "status": self.app.cognitive_executive.status(),
            "decisions": self.app.cognitive_executive.list_decisions(limit=50),
            "goals": self.app.cognitive_executive.list_goals(
                status="all", limit=100
            ),
            "verifications": self.app.cognitive_executive.list_verifications(
                limit=100
            ),
        }

    def control_first_aid(self) -> dict[str, Any]:
        return self.app.first_aid.status()

    def control_structured_packs(self) -> dict[str, Any]:
        items = [
            item
            | {"sources": self.app.structured_packs.sources(str(item["package_id"]))}
            for item in self.app.structured_packs.list_all()
        ]
        return {
            "status": self.app.structured_packs.status(),
            "items": items,
        }

    def first_aid_lookup(
        self,
        query: str,
        *,
        language: str = "es",
        locale: str | None = None,
    ) -> dict[str, Any]:
        topic = self.app.first_aid.lookup(
            query,
            language=language,
            locale=locale,
        )
        if topic is None:
            return {
                "found": False,
                "query": query,
                "status": self.app.first_aid.status(),
            }
        message, data = self.app.first_aid.render_topic(topic, language=language)
        return {"message": message, **data}

    def control_memory_tiers(self) -> dict[str, Any]:
        return self.app.tiered_memory.status()

    def control_action_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.app.action_runs.list_recent(limit=limit)

    def control_change_proposals(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.app.change_proposals.list_recent(limit=limit)

    def control_validation_cycles(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.app.validation_cycles.list_recent(limit=limit)

    def control_development_sessions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for session in self.app.development_sessions.list_recent(limit=limit):
            item = self.app.development_sessions.get(str(session["public_id"])) or dict(
                session
            )
            item["guidance"] = build_session_guidance(item).to_dict()
            items.append(item)
        return items

    def control_projects(self) -> dict[str, Any]:
        php_profiles = {
            str(item["project_root"]): item for item in self.app.php_profiles.list_all()
        }
        web_profiles = {
            str(item["project_root"]): item for item in self.app.web_profiles.list_all()
        }
        python_profiles = {
            str(item["project_root"]): item for item in self.app.python_profiles.list_all()
        }
        java_profiles = {
            str(item["project_root"]): item for item in self.app.java_profiles.list_all()
        }
        kotlin_profiles = {
            str(item["project_root"]): item
            for item in self.app.kotlin_profiles.list_all()
        }
        dotnet_profiles = {
            str(item["project_root"]): item
            for item in self.app.dotnet_profiles.list_all()
        }
        swift_profiles = {
            str(item["project_root"]): item
            for item in self.app.swift_profiles.list_all()
        }
        dart_profiles = {
            str(item["project_root"]): item
            for item in self.app.dart_profiles.list_all()
        }
        sql_profiles = {
            str(item["project_root"]): item
            for item in self.app.sql_profiles.list_all()
        }
        native_profiles = {
            str(item["project_root"]): item for item in self.app.native_profiles.list_all()
        }
        ruby_profiles = {
            str(item["project_root"]): item for item in self.app.ruby_profiles.list_all()
        }
        go_profiles = {
            str(item["project_root"]): item for item in self.app.go_profiles.list_all()
        }
        rust_profiles = {
            str(item["project_root"]): item for item in self.app.rust_profiles.list_all()
        }
        trusted = [
            {
                **item,
                "source": "trusted_project",
                "profile": php_profiles.get(item["path"]),
                "web_profile": web_profiles.get(item["path"]),
                "python_profile": python_profiles.get(item["path"]),
                "java_profile": java_profiles.get(item["path"]),
                "kotlin_profile": kotlin_profiles.get(item["path"]),
                "dotnet_profile": dotnet_profiles.get(item["path"]),
                "swift_profile": swift_profiles.get(item["path"]),
                "dart_profile": dart_profiles.get(item["path"]),
                "sql_profile": sql_profiles.get(item["path"]),
                "native_profile": native_profiles.get(item["path"]),
                "ruby_profile": ruby_profiles.get(item["path"]),
                "go_profile": go_profiles.get(item["path"]),
                "rust_profile": rust_profiles.get(item["path"]),
            }
            for item in self.app.trusted_projects.list_all()
        ]
        configured = [
            {
                "path": str(root),
                "source": "configured_root",
                "profile": php_profiles.get(str(root)),
                "web_profile": web_profiles.get(str(root)),
                "python_profile": python_profiles.get(str(root)),
                "java_profile": java_profiles.get(str(root)),
                "kotlin_profile": kotlin_profiles.get(str(root)),
                "dotnet_profile": dotnet_profiles.get(str(root)),
                "swift_profile": swift_profiles.get(str(root)),
                "dart_profile": dart_profiles.get(str(root)),
                "sql_profile": sql_profiles.get(str(root)),
                "native_profile": native_profiles.get(str(root)),
                "ruby_profile": ruby_profiles.get(str(root)),
                "go_profile": go_profiles.get(str(root)),
                "rust_profile": rust_profiles.get(str(root)),
            }
            for root in self.app.config.allowed_roots
        ]
        return {
            "configured_roots": configured,
            "trusted_projects": trusted,
            "profiles": list(php_profiles.values()),
            "php_profiles": list(php_profiles.values()),
            "web_profiles": list(web_profiles.values()),
            "python_profiles": list(python_profiles.values()),
            "java_profiles": list(java_profiles.values()),
            "kotlin_profiles": list(kotlin_profiles.values()),
            "dotnet_profiles": list(dotnet_profiles.values()),
            "swift_profiles": list(swift_profiles.values()),
            "dart_profiles": list(dart_profiles.values()),
            "sql_profiles": list(sql_profiles.values()),
            "native_profiles": list(native_profiles.values()),
            "ruby_profiles": list(ruby_profiles.values()),
            "go_profiles": list(go_profiles.values()),
            "rust_profiles": list(rust_profiles.values()),
        }

    def trust_project(self, path: str) -> dict[str, Any]:
        clean_path = path.strip()
        if not clean_path:
            raise ValueError("Falta la ruta del proyecto confiable.")
        with self._lock:
            item = self.app.trusted_projects.trust(
                Path(clean_path), actor=self.app.identity.system_user
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.trust",
                target=item["path"],
                outcome="success",
                details={"authorization_scope": "project_persistent"},
            )
            return item

    def untrust_project(self, path: str) -> bool:
        resolved = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            removed = self.app.trusted_projects.untrust(resolved)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.untrust",
                target=str(resolved),
                outcome="success" if removed else "not_found",
            )
            return removed

    def save_php_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_root = str(payload.get("project_root", "")).strip()
        if not raw_root:
            raise ValueError("Falta la raíz del proyecto para el perfil PHP.")
        root = Path(raw_root).expanduser().resolve(strict=True)
        decision = self.app.authorization.project(root)
        if not decision.allowed or decision.scope.value != "project_persistent":
            raise PermissionError(
                "El perfil PHP requiere una raíz configurada o un proyecto confiable."
            )
        with self._lock:
            item = self.app.php_profiles.save(
                root,
                actor=self.app.identity.system_user,
                phpstan_config=(
                    str(payload.get("phpstan_config", ""))
                    if "phpstan_config" in payload
                    else None
                ),
                phpstan_level=(
                    str(payload.get("phpstan_level", ""))
                    if "phpstan_level" in payload
                    else None
                ),
                phpunit_config=(
                    str(payload.get("phpunit_config", ""))
                    if "phpunit_config" in payload
                    else None
                ),
                phpunit_testsuite=(
                    str(payload.get("phpunit_testsuite", ""))
                    if "phpunit_testsuite" in payload
                    else None
                ),
                composer_strict=(
                    bool(payload["composer_strict"])
                    if "composer_strict" in payload
                    else None
                ),
                composer_enabled=_optional_bool(payload, "composer_enabled"),
                syntax_scan_enabled=_optional_bool(payload, "syntax_scan_enabled"),
                phpstan_enabled=_optional_bool(payload, "phpstan_enabled"),
                phpunit_enabled=_optional_bool(payload, "phpunit_enabled"),
                fail_fast=_optional_bool(payload, "fail_fast"),
                require_tools=_optional_bool(payload, "require_tools"),
                max_php_files=_optional_int(payload.get("max_php_files")),
                exclude_paths=payload.get("exclude_paths"),
                timeout_seconds=_optional_int(payload.get("timeout_seconds")),
                max_output_chars=_optional_int(payload.get("max_output_chars")),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.php_profile.save",
                target=str(root),
                outcome="success",
                details={"profile_id": item["id"]},
            )
            return item

    def delete_php_profile(self, path: str) -> bool:
        root = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            removed = self.app.php_profiles.delete(root)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.php_profile.delete",
                target=str(root),
                outcome="success" if removed else "not_found",
            )
            return removed

    def save_web_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        root = Path(str(payload.get("project_root", ""))).expanduser().resolve(
            strict=True
        )
        decision = self.app.authorization.project(root)
        if not decision.allowed or decision.scope.value != "project_persistent":
            raise PermissionError(
                "El perfil web requiere una raíz configurada o un proyecto confiable."
            )
        with self._lock:
            item = self.app.web_profiles.save(
                root,
                actor=self.app.identity.system_user,
                html_enabled=_optional_bool(payload, "html_enabled"),
                css_enabled=_optional_bool(payload, "css_enabled"),
                javascript_enabled=_optional_bool(payload, "javascript_enabled"),
                typescript_enabled=_optional_bool(payload, "typescript_enabled"),
                eslint_enabled=_optional_bool(payload, "eslint_enabled"),
                stylelint_enabled=_optional_bool(payload, "stylelint_enabled"),
                framework_checks_enabled=_optional_bool(
                    payload, "framework_checks_enabled"
                ),
                framework_preset=(
                    str(payload.get("framework_preset", ""))
                    if "framework_preset" in payload
                    else None
                ),
                eslint_config=(
                    str(payload.get("eslint_config", ""))
                    if "eslint_config" in payload
                    else None
                ),
                stylelint_config=(
                    str(payload.get("stylelint_config", ""))
                    if "stylelint_config" in payload
                    else None
                ),
                fail_fast=_optional_bool(payload, "fail_fast"),
                require_tools=_optional_bool(payload, "require_tools"),
                max_files=_optional_int(payload.get("max_files")),
                exclude_paths=payload.get("exclude_paths"),
                timeout_seconds=_optional_int(payload.get("timeout_seconds")),
                max_output_chars=_optional_int(payload.get("max_output_chars")),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.web_profile.save",
                target=str(root),
                outcome="success",
                details={"profile_id": item["id"]},
            )
            return item

    def delete_web_profile(self, path: str) -> bool:
        root = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            removed = self.app.web_profiles.delete(root)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.web_profile.delete",
                target=str(root),
                outcome="success" if removed else "not_found",
            )
            return removed

    def save_python_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        root = Path(str(payload.get("project_root", ""))).expanduser().resolve(
            strict=True
        )
        decision = self.app.authorization.project(root)
        if not decision.allowed or decision.scope.value != "project_persistent":
            raise PermissionError(
                "El perfil Python requiere una raíz configurada o un proyecto confiable."
            )
        with self._lock:
            item = self.app.python_profiles.save(
                root,
                actor=self.app.identity.system_user,
                pyproject_enabled=_optional_bool(payload, "pyproject_enabled"),
                compile_enabled=_optional_bool(payload, "compile_enabled"),
                ruff_enabled=_optional_bool(payload, "ruff_enabled"),
                mypy_enabled=_optional_bool(payload, "mypy_enabled"),
                pytest_enabled=_optional_bool(payload, "pytest_enabled"),
                ruff_config=(
                    str(payload.get("ruff_config", ""))
                    if "ruff_config" in payload
                    else None
                ),
                mypy_config=(
                    str(payload.get("mypy_config", ""))
                    if "mypy_config" in payload
                    else None
                ),
                pytest_path=(
                    str(payload.get("pytest_path", ""))
                    if "pytest_path" in payload
                    else None
                ),
                fail_fast=_optional_bool(payload, "fail_fast"),
                require_tools=_optional_bool(payload, "require_tools"),
                max_python_files=_optional_int(payload.get("max_python_files")),
                exclude_paths=payload.get("exclude_paths"),
                timeout_seconds=_optional_int(payload.get("timeout_seconds")),
                max_output_chars=_optional_int(payload.get("max_output_chars")),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.python_profile.save",
                target=str(root),
                outcome="success",
                details={"profile_id": item["id"]},
            )
            return item

    def delete_python_profile(self, path: str) -> bool:
        root = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            removed = self.app.python_profiles.delete(root)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.python_profile.delete",
                target=str(root),
                outcome="success" if removed else "not_found",
            )
            return removed

    def save_java_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        root = Path(str(payload.get("project_root", ""))).expanduser().resolve(
            strict=True
        )
        decision = self.app.authorization.project(root)
        if not decision.allowed or decision.scope.value != "project_persistent":
            raise PermissionError(
                "El perfil Java requiere una raíz configurada o un proyecto confiable."
            )
        with self._lock:
            item = self.app.java_profiles.save(
                root,
                actor=self.app.identity.system_user,
                descriptor_enabled=_optional_bool(payload, "descriptor_enabled"),
                javac_enabled=_optional_bool(payload, "javac_enabled"),
                build_enabled=_optional_bool(payload, "build_enabled"),
                tests_enabled=_optional_bool(payload, "tests_enabled"),
                build_tool=(
                    str(payload.get("build_tool", ""))
                    if "build_tool" in payload
                    else None
                ),
                java_release=_optional_int(payload.get("java_release")),
                fail_fast=_optional_bool(payload, "fail_fast"),
                require_tools=_optional_bool(payload, "require_tools"),
                max_java_files=_optional_int(payload.get("max_java_files")),
                exclude_paths=payload.get("exclude_paths"),
                timeout_seconds=_optional_int(payload.get("timeout_seconds")),
                max_output_chars=_optional_int(payload.get("max_output_chars")),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.java_profile.save",
                target=str(root),
                outcome="success",
                details={"profile_id": item["id"]},
            )
            return item

    def delete_java_profile(self, path: str) -> bool:
        root = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            removed = self.app.java_profiles.delete(root)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.java_profile.delete",
                target=str(root),
                outcome="success" if removed else "not_found",
            )
            return removed

    def control_java_verifications(
        self,
        *,
        project_root: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        root = Path(project_root) if project_root else None
        return self.app.verification_runs.list_recent(
            toolchain="java",
            project_root=root,
            limit=limit,
        )

    def save_kotlin_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        root = Path(str(payload.get("project_root", ""))).expanduser().resolve(
            strict=True
        )
        decision = self.app.authorization.project(root)
        if not decision.allowed or decision.scope.value != "project_persistent":
            raise PermissionError(
                "El perfil Kotlin requiere una raíz configurada o un proyecto confiable."
            )
        with self._lock:
            item = self.app.kotlin_profiles.save(
                root,
                actor=self.app.identity.system_user,
                descriptor_enabled=_optional_bool(payload, "descriptor_enabled"),
                kotlinc_enabled=_optional_bool(payload, "kotlinc_enabled"),
                build_enabled=_optional_bool(payload, "build_enabled"),
                tests_enabled=_optional_bool(payload, "tests_enabled"),
                build_tool=(
                    str(payload.get("build_tool", ""))
                    if "build_tool" in payload
                    else None
                ),
                jvm_target=_optional_int(payload.get("jvm_target")),
                fail_fast=_optional_bool(payload, "fail_fast"),
                require_tools=_optional_bool(payload, "require_tools"),
                max_kotlin_files=_optional_int(payload.get("max_kotlin_files")),
                exclude_paths=payload.get("exclude_paths"),
                timeout_seconds=_optional_int(payload.get("timeout_seconds")),
                max_output_chars=_optional_int(payload.get("max_output_chars")),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.kotlin_profile.save",
                target=str(root),
                outcome="success",
                details={"profile_id": item["id"]},
            )
            return item

    def delete_kotlin_profile(self, path: str) -> bool:
        root = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            removed = self.app.kotlin_profiles.delete(root)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.kotlin_profile.delete",
                target=str(root),
                outcome="success" if removed else "not_found",
            )
            return removed

    def control_kotlin_verifications(
        self,
        *,
        project_root: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        root = Path(project_root) if project_root else None
        return self.app.verification_runs.list_recent(
            toolchain="kotlin",
            project_root=root,
            limit=limit,
        )

    def save_dotnet_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        root = Path(str(payload.get("project_root", ""))).expanduser().resolve(
            strict=True
        )
        decision = self.app.authorization.project(root)
        if not decision.allowed or decision.scope.value != "project_persistent":
            raise PermissionError(
                "El perfil .NET requiere una raíz configurada o un proyecto confiable."
            )
        with self._lock:
            item = self.app.dotnet_profiles.save(
                root,
                actor=self.app.identity.system_user,
                descriptor_enabled=_optional_bool(payload, "descriptor_enabled"),
                format_enabled=_optional_bool(payload, "format_enabled"),
                build_enabled=_optional_bool(payload, "build_enabled"),
                tests_enabled=_optional_bool(payload, "tests_enabled"),
                configuration=(
                    str(payload.get("configuration", ""))
                    if "configuration" in payload
                    else None
                ),
                fail_fast=_optional_bool(payload, "fail_fast"),
                require_tools=_optional_bool(payload, "require_tools"),
                max_dotnet_files=_optional_int(payload.get("max_dotnet_files")),
                exclude_paths=payload.get("exclude_paths"),
                timeout_seconds=_optional_int(payload.get("timeout_seconds")),
                max_output_chars=_optional_int(payload.get("max_output_chars")),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.dotnet_profile.save",
                target=str(root),
                outcome="success",
                details={"profile_id": item["id"]},
            )
            return item

    def delete_dotnet_profile(self, path: str) -> bool:
        root = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            removed = self.app.dotnet_profiles.delete(root)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.dotnet_profile.delete",
                target=str(root),
                outcome="success" if removed else "not_found",
            )
            return removed

    def control_dotnet_verifications(
        self,
        *,
        project_root: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        root = Path(project_root) if project_root else None
        return self.app.verification_runs.list_recent(
            toolchain="dotnet",
            project_root=root,
            limit=limit,
        )

    def save_swift_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        root = Path(str(payload.get("project_root", ""))).expanduser().resolve(
            strict=True
        )
        decision = self.app.authorization.project(root)
        if not decision.allowed or decision.scope.value != "project_persistent":
            raise PermissionError(
                "El perfil Swift requiere una raíz configurada o un proyecto confiable."
            )
        with self._lock:
            item = self.app.swift_profiles.save(
                root,
                actor=self.app.identity.system_user,
                manifest_enabled=_optional_bool(payload, "manifest_enabled"),
                syntax_enabled=_optional_bool(payload, "syntax_enabled"),
                format_enabled=_optional_bool(payload, "format_enabled"),
                build_enabled=_optional_bool(payload, "build_enabled"),
                tests_enabled=_optional_bool(payload, "tests_enabled"),
                configuration=(
                    str(payload.get("configuration", ""))
                    if "configuration" in payload
                    else None
                ),
                fail_fast=_optional_bool(payload, "fail_fast"),
                require_tools=_optional_bool(payload, "require_tools"),
                max_swift_files=_optional_int(payload.get("max_swift_files")),
                exclude_paths=payload.get("exclude_paths"),
                timeout_seconds=_optional_int(payload.get("timeout_seconds")),
                max_output_chars=_optional_int(payload.get("max_output_chars")),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.swift_profile.save",
                target=str(root),
                outcome="success",
                details={"profile_id": item["id"]},
            )
            return item

    def delete_swift_profile(self, path: str) -> bool:
        root = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            removed = self.app.swift_profiles.delete(root)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.swift_profile.delete",
                target=str(root),
                outcome="success" if removed else "not_found",
            )
            return removed

    def control_swift_verifications(
        self,
        *,
        project_root: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        root = Path(project_root) if project_root else None
        return self.app.verification_runs.list_recent(
            toolchain="swift",
            project_root=root,
            limit=limit,
        )

    def save_dart_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        root = Path(str(payload.get("project_root", ""))).expanduser().resolve(
            strict=True
        )
        decision = self.app.authorization.project(root)
        if not decision.allowed or decision.scope.value != "project_persistent":
            raise PermissionError(
                "El perfil Dart/Flutter requiere una raíz configurada "
                "o un proyecto confiable."
            )
        with self._lock:
            item = self.app.dart_profiles.save(
                root,
                actor=self.app.identity.system_user,
                descriptor_enabled=_optional_bool(payload, "descriptor_enabled"),
                format_enabled=_optional_bool(payload, "format_enabled"),
                analyze_enabled=_optional_bool(payload, "analyze_enabled"),
                tests_enabled=_optional_bool(payload, "tests_enabled"),
                test_runner=(
                    str(payload.get("test_runner", ""))
                    if "test_runner" in payload
                    else None
                ),
                fail_fast=_optional_bool(payload, "fail_fast"),
                require_tools=_optional_bool(payload, "require_tools"),
                max_dart_files=_optional_int(payload.get("max_dart_files")),
                exclude_paths=payload.get("exclude_paths"),
                timeout_seconds=_optional_int(payload.get("timeout_seconds")),
                max_output_chars=_optional_int(payload.get("max_output_chars")),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.dart_profile.save",
                target=str(root),
                outcome="success",
                details={"profile_id": item["id"]},
            )
            return item

    def delete_dart_profile(self, path: str) -> bool:
        root = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            removed = self.app.dart_profiles.delete(root)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.dart_profile.delete",
                target=str(root),
                outcome="success" if removed else "not_found",
            )
            return removed

    def control_dart_verifications(
        self,
        *,
        project_root: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        root = Path(project_root) if project_root else None
        return self.app.verification_runs.list_recent(
            toolchain="dart",
            project_root=root,
            limit=limit,
        )

    def save_sql_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        root = Path(str(payload.get("project_root", ""))).expanduser().resolve(
            strict=True
        )
        decision = self.app.authorization.project(root)
        if not decision.allowed or decision.scope.value != "project_persistent":
            raise PermissionError(
                "El perfil SQL requiere una raíz configurada o un proyecto confiable."
            )
        with self._lock:
            item = self.app.sql_profiles.save(
                root,
                actor=self.app.identity.system_user,
                static_enabled=_optional_bool(payload, "static_enabled"),
                migrations_enabled=_optional_bool(payload, "migrations_enabled"),
                schema_enabled=_optional_bool(payload, "schema_enabled"),
                dialect=(
                    str(payload.get("dialect", ""))
                    if "dialect" in payload
                    else None
                ),
                allow_mutating_sql=_optional_bool(payload, "allow_mutating_sql"),
                allow_destructive_migrations=_optional_bool(
                    payload,
                    "allow_destructive_migrations",
                ),
                fail_fast=_optional_bool(payload, "fail_fast"),
                max_sql_files=_optional_int(payload.get("max_sql_files")),
                max_database_files=_optional_int(
                    payload.get("max_database_files")
                ),
                exclude_paths=payload.get("exclude_paths"),
                timeout_seconds=_optional_int(payload.get("timeout_seconds")),
                max_output_chars=_optional_int(payload.get("max_output_chars")),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.sql_profile.save",
                target=str(root),
                outcome="success",
                details={"profile_id": item["id"]},
            )
            return item

    def delete_sql_profile(self, path: str) -> bool:
        root = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            removed = self.app.sql_profiles.delete(root)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.sql_profile.delete",
                target=str(root),
                outcome="success" if removed else "not_found",
            )
            return removed

    def control_sql_verifications(
        self,
        *,
        project_root: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        root = Path(project_root) if project_root else None
        return self.app.verification_runs.list_recent(
            toolchain="sql",
            project_root=root,
            limit=limit,
        )

    def save_native_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        root = Path(str(payload.get("project_root", ""))).expanduser().resolve(
            strict=True
        )
        decision = self.app.authorization.project(root)
        if not decision.allowed or decision.scope.value != "project_persistent":
            raise PermissionError(
                "El perfil C/C++ requiere una raíz configurada o un proyecto confiable."
            )
        with self._lock:
            item = self.app.native_profiles.save(
                root,
                actor=self.app.identity.system_user,
                descriptor_enabled=_optional_bool(payload, "descriptor_enabled"),
                c_syntax_enabled=_optional_bool(payload, "c_syntax_enabled"),
                cpp_syntax_enabled=_optional_bool(payload, "cpp_syntax_enabled"),
                static_enabled=_optional_bool(payload, "static_enabled"),
                build_enabled=_optional_bool(payload, "build_enabled"),
                tests_enabled=_optional_bool(payload, "tests_enabled"),
                compiler=(
                    str(payload.get("compiler", ""))
                    if "compiler" in payload
                    else None
                ),
                c_standard=(
                    str(payload.get("c_standard", ""))
                    if "c_standard" in payload
                    else None
                ),
                cpp_standard=(
                    str(payload.get("cpp_standard", ""))
                    if "cpp_standard" in payload
                    else None
                ),
                fail_fast=_optional_bool(payload, "fail_fast"),
                require_tools=_optional_bool(payload, "require_tools"),
                max_native_files=_optional_int(payload.get("max_native_files")),
                exclude_paths=payload.get("exclude_paths"),
                timeout_seconds=_optional_int(payload.get("timeout_seconds")),
                max_output_chars=_optional_int(payload.get("max_output_chars")),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.native_profile.save",
                target=str(root),
                outcome="success",
                details={"profile_id": item["id"]},
            )
            return item

    def delete_native_profile(self, path: str) -> bool:
        root = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            removed = self.app.native_profiles.delete(root)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.native_profile.delete",
                target=str(root),
                outcome="success" if removed else "not_found",
            )
            return removed

    def control_native_verifications(
        self,
        *,
        project_root: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        root = Path(project_root) if project_root else None
        return self.app.verification_runs.list_recent(
            toolchain="native",
            project_root=root,
            limit=limit,
        )

    def save_ruby_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        root = Path(str(payload.get("project_root", ""))).expanduser().resolve(
            strict=True
        )
        decision = self.app.authorization.project(root)
        if not decision.allowed or decision.scope.value != "project_persistent":
            raise PermissionError(
                "El perfil Ruby requiere una raíz configurada o un proyecto confiable."
            )
        with self._lock:
            item = self.app.ruby_profiles.save(
                root,
                actor=self.app.identity.system_user,
                descriptor_enabled=_optional_bool(payload, "descriptor_enabled"),
                bundle_enabled=_optional_bool(payload, "bundle_enabled"),
                syntax_enabled=_optional_bool(payload, "syntax_enabled"),
                rubocop_enabled=_optional_bool(payload, "rubocop_enabled"),
                tests_enabled=_optional_bool(payload, "tests_enabled"),
                test_framework=(
                    str(payload.get("test_framework", ""))
                    if "test_framework" in payload
                    else None
                ),
                fail_fast=_optional_bool(payload, "fail_fast"),
                require_tools=_optional_bool(payload, "require_tools"),
                max_ruby_files=_optional_int(payload.get("max_ruby_files")),
                exclude_paths=payload.get("exclude_paths"),
                timeout_seconds=_optional_int(payload.get("timeout_seconds")),
                max_output_chars=_optional_int(payload.get("max_output_chars")),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.ruby_profile.save",
                target=str(root),
                outcome="success",
                details={"profile_id": item["id"]},
            )
            return item

    def delete_ruby_profile(self, path: str) -> bool:
        root = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            removed = self.app.ruby_profiles.delete(root)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.ruby_profile.delete",
                target=str(root),
                outcome="success" if removed else "not_found",
            )
            return removed

    def control_ruby_verifications(
        self,
        *,
        project_root: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        root = Path(project_root) if project_root else None
        return self.app.verification_runs.list_recent(
            toolchain="ruby",
            project_root=root,
            limit=limit,
        )

    def save_go_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        root = Path(str(payload.get("project_root", ""))).expanduser().resolve(
            strict=True
        )
        decision = self.app.authorization.project(root)
        if not decision.allowed or decision.scope.value != "project_persistent":
            raise PermissionError(
                "El perfil Go requiere una raíz configurada o un proyecto confiable."
            )
        with self._lock:
            item = self.app.go_profiles.save(
                root,
                actor=self.app.identity.system_user,
                module_enabled=_optional_bool(payload, "module_enabled"),
                fmt_enabled=_optional_bool(payload, "fmt_enabled"),
                vet_enabled=_optional_bool(payload, "vet_enabled"),
                build_enabled=_optional_bool(payload, "build_enabled"),
                tests_enabled=_optional_bool(payload, "tests_enabled"),
                test_mode=(
                    str(payload.get("test_mode", ""))
                    if "test_mode" in payload
                    else None
                ),
                fail_fast=_optional_bool(payload, "fail_fast"),
                require_tools=_optional_bool(payload, "require_tools"),
                max_go_files=_optional_int(payload.get("max_go_files")),
                exclude_paths=payload.get("exclude_paths"),
                timeout_seconds=_optional_int(payload.get("timeout_seconds")),
                max_output_chars=_optional_int(payload.get("max_output_chars")),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.go_profile.save",
                target=str(root),
                outcome="success",
                details={"profile_id": item["id"]},
            )
            return item

    def delete_go_profile(self, path: str) -> bool:
        root = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            removed = self.app.go_profiles.delete(root)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.go_profile.delete",
                target=str(root),
                outcome="success" if removed else "not_found",
            )
            return removed

    def control_go_verifications(
        self,
        *,
        project_root: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        root = Path(project_root) if project_root else None
        return self.app.verification_runs.list_recent(
            toolchain="go",
            project_root=root,
            limit=limit,
        )

    def save_rust_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        root = Path(str(payload.get("project_root", ""))).expanduser().resolve(
            strict=True
        )
        decision = self.app.authorization.project(root)
        if not decision.allowed or decision.scope.value != "project_persistent":
            raise PermissionError(
                "El perfil Rust requiere una raíz configurada o un proyecto confiable."
            )
        with self._lock:
            item = self.app.rust_profiles.save(
                root,
                actor=self.app.identity.system_user,
                manifest_enabled=_optional_bool(payload, "manifest_enabled"),
                fmt_enabled=_optional_bool(payload, "fmt_enabled"),
                check_enabled=_optional_bool(payload, "check_enabled"),
                clippy_enabled=_optional_bool(payload, "clippy_enabled"),
                tests_enabled=_optional_bool(payload, "tests_enabled"),
                feature_mode=(
                    str(payload.get("feature_mode", ""))
                    if "feature_mode" in payload
                    else None
                ),
                fail_fast=_optional_bool(payload, "fail_fast"),
                require_tools=_optional_bool(payload, "require_tools"),
                max_rust_files=_optional_int(payload.get("max_rust_files")),
                exclude_paths=payload.get("exclude_paths"),
                timeout_seconds=_optional_int(payload.get("timeout_seconds")),
                max_output_chars=_optional_int(payload.get("max_output_chars")),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.rust_profile.save",
                target=str(root),
                outcome="success",
                details={"profile_id": item["id"]},
            )
            return item

    def delete_rust_profile(self, path: str) -> bool:
        root = Path(path).expanduser().resolve(strict=False)
        with self._lock:
            removed = self.app.rust_profiles.delete(root)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.project.rust_profile.delete",
                target=str(root),
                outcome="success" if removed else "not_found",
            )
            return removed

    def control_rust_verifications(
        self,
        *,
        project_root: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        root = Path(project_root) if project_root else None
        return self.app.verification_runs.list_recent(
            toolchain="rust",
            project_root=root,
            limit=limit,
        )

    def control_python_verifications(
        self,
        *,
        project_root: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        root = Path(project_root) if project_root else None
        return self.app.verification_runs.list_recent(
            toolchain="python",
            project_root=root,
            limit=limit,
        )

    def control_web_verifications(
        self,
        *,
        project_root: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        root = Path(project_root) if project_root.strip() else None
        return self.app.verification_runs.list_recent(
            toolchain="web",
            project_root=root,
            limit=limit,
        )

    def control_alexandria_packages(self) -> list[dict[str, Any]]:
        return self.app.alexandria_packages.list_all()

    def create_alexandria_package(self, payload: dict[str, Any]) -> dict[str, Any]:
        sources = payload.get("sources")
        if not isinstance(sources, list):
            raise ValueError("sources debe ser una lista de rutas locales.")
        with self._lock:
            item = self.app.alexandria_packages.create(
                Path(str(payload.get("destination", ""))),
                package_id=str(payload.get("package_id", "")),
                name=str(payload.get("name", "")),
                version=str(payload.get("version", "")),
                tier=str(payload.get("tier", "optional")),
                domain=str(payload.get("domain", "")),
                language=str(payload.get("language", "es")),
                license_id=str(payload.get("license_id", "")),
                source_paths=[Path(str(item)) for item in sources],
                description=str(payload.get("description", "")),
                publisher=str(payload.get("publisher", "unverified")),
                tags=[str(item) for item in payload.get("tags", [])]
                if isinstance(payload.get("tags"), list)
                else [],
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.alexandria.package.create",
                target=str(item["package_id"]),
                outcome="success",
                details={
                    "version": item["version"],
                    "destination": item["package_root"],
                    "source_count": item["source_count"],
                },
            )
            return item

    def install_alexandria_package(self, path: str) -> dict[str, Any]:
        with self._lock:
            item = self.app.alexandria_packages.install(
                Path(path), actor=self.app.identity.system_user
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.alexandria.package.install",
                target=str(item["package_id"]),
                outcome="success",
                details={
                    "version": item["version"],
                    "tier": item["tier"],
                    "source_count": item.get("source_count", 0),
                },
            )
            return item

    def export_alexandria_package(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            item = self.app.alexandria_packages.export(
                str(payload.get("package_id", "")),
                Path(str(payload.get("destination", ""))),
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.alexandria.package.export",
                target=str(item["package_id"]),
                outcome="success",
                details={
                    "version": item["version"],
                    "destination": item["package_root"],
                    "source_count": item["source_count"],
                },
            )
            return item

    def control_php_verifications(
        self,
        *,
        project_root: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        root = Path(project_root) if project_root.strip() else None
        return self.app.verification_runs.list_recent(
            toolchain="php",
            project_root=root,
            limit=limit,
        )

    def control_audit(
        self,
        *,
        limit: int = 100,
        action: str = "",
        outcome: str = "",
        query: str = "",
    ) -> list[dict[str, Any]]:
        events = self.app.audit.list_recent(
            limit=limit,
            action=action.strip() or None,
            outcome=outcome.strip() or None,
        )
        clean_query = query.strip().casefold()
        items: list[dict[str, Any]] = []
        for event in events:
            try:
                details = json.loads(str(event.get("details_json", "{}")))
            except json.JSONDecodeError:
                details = {}
            public = {**event, "details": details if isinstance(details, dict) else {}}
            public.pop("details_json", None)
            if clean_query and clean_query not in json.dumps(
                public, ensure_ascii=False
            ).casefold():
                continue
            items.append(public)
        return items

    def inspector_overview(self) -> dict[str, Any]:
        with self.app.database.connect() as connection:
            counts = {
                "memories": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM memories WHERE status = 'active'"
                    ).fetchone()[0]
                ),
                "episodes": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM chat_episodes WHERE status = 'active'"
                    ).fetchone()[0]
                ),
                "proposals": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM memory_proposals WHERE status = 'pending'"
                    ).fetchone()[0]
                ),
                "corrections": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM response_corrections WHERE status = 'active'"
                    ).fetchone()[0]
                ),
                "documents": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM documents WHERE status = 'active'"
                    ).fetchone()[0]
                ),
                "archives": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM chat_archives WHERE status = 'active'"
                    ).fetchone()[0]
                ),
                "audit_events": int(
                    connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
                ),
                "attachments": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM chat_attachments WHERE status != 'deleted'"
                    ).fetchone()[0]
                ),
            }
            meta_rows = connection.execute(
                "SELECT key, value FROM schema_meta WHERE key LIKE '%fts5'"
            ).fetchall()
        database_size = (
            self.app.paths.database_file.stat().st_size
            if self.app.paths.database_file.exists()
            else 0
        )
        return {
            "counts": counts,
            "database": {
                "path": str(self.app.paths.database_file),
                "size_bytes": database_size,
            },
            "indexes": {str(row["key"]): str(row["value"]) for row in meta_rows},
        }

    def inspector_memories(
        self, *, query: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        items = (
            self.app.memories.search(query, limit=limit)
            if query.strip()
            else self.app.memories.list_active(limit=limit)
        )
        return [dict(item) for item in items]

    def inspector_episodes(
        self,
        *,
        query: str = "",
        kind: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if query.strip():
            items = self.app.memory_lifecycle.search_episodes(query, limit=limit)
            if kind.strip():
                clean_kind = kind.strip().casefold()
                items = [item for item in items if str(item["kind"]) == clean_kind]
            return items
        return self.app.memory_lifecycle.list_episodes(
            kind=kind.strip() or None,
            limit=limit,
        )

    def inspector_proposals(
        self, *, status: str = "pending", limit: int = 100
    ) -> list[dict[str, Any]]:
        return self.app.memory_lifecycle.list_proposals(status=status, limit=limit)

    def inspector_corrections(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.app.memory_lifecycle.list_corrections(limit=limit)

    def inspector_documents(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.app.knowledge.list_active(limit=limit)

    def inspector_attachments(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.app.attachments.list_all(limit=limit)

    def reprocess_attachment(self, attachment_id: str) -> dict[str, Any]:
        with self._lock:
            item = self.app.attachments.reprocess(attachment_id)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.attachment.reprocess",
                target=attachment_id,
                outcome="success",
                details={
                    "extraction_status": item["extraction_status"],
                    "validation_status": item["validation_status"],
                    "processor": item["processor"],
                },
            )
            return item

    def inspector_audit(self, *, limit: int = 100) -> list[dict[str, Any]]:
        events = self.app.audit.list_recent(limit=limit)
        for event in events:
            try:
                details = json.loads(str(event.get("details_json", "{}")))
            except json.JSONDecodeError:
                details = {}
            event["details"] = details if isinstance(details, dict) else {}
            event.pop("details_json", None)
        return events

    def inspector_archives(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.app.memory_lifecycle.list_archives(limit=limit)

    def update_memory(
        self,
        memory_id: int,
        *,
        content: str,
        kind: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            memory = self.app.memories.update(
                memory_id,
                content=content,
                kind=kind,
                project=project,
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.memory.update",
                target=str(memory_id),
                outcome="success",
            )
            return memory

    def forget_memory(self, memory_id: int) -> bool:
        with self._lock:
            forgotten = self.app.memories.forget(memory_id)
            if forgotten:
                self.app.audit.record(
                    actor=self.app.identity.system_user,
                    action="web.memory.forget",
                    target=str(memory_id),
                    outcome="success",
                )
            return forgotten

    def update_episode(
        self,
        episode_id: int,
        *,
        content: str,
        kind: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            episode = self.app.memory_lifecycle.edit_episode(
                episode_id,
                content=content,
                kind=kind,
            )
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.episode.update",
                target=str(episode_id),
                outcome="success",
            )
            return episode

    def forget_episode(self, episode_id: int) -> bool:
        with self._lock:
            forgotten = self.app.memory_lifecycle.forget_episode(episode_id)
            if forgotten:
                self.app.audit.record(
                    actor=self.app.identity.system_user,
                    action="web.episode.forget",
                    target=str(episode_id),
                    outcome="success",
                )
            return forgotten

    def edit_proposal(self, proposal_id: int, content: str) -> dict[str, Any]:
        with self._lock:
            proposal = self.app.memory_lifecycle.edit_proposal(proposal_id, content)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.memory_proposal.update",
                target=str(proposal_id),
                outcome="success",
            )
            return proposal

    def approve_proposal(self, proposal_id: int) -> dict[str, Any]:
        with self._lock:
            proposal = self.app.memory_lifecycle.approve_proposal(proposal_id)
            self.app.audit.record(
                actor=self.app.identity.system_user,
                action="web.memory_proposal.approve",
                target=str(proposal_id),
                outcome="success",
                details={"memory_id": proposal.get("memory_id")},
            )
            return proposal

    def reject_proposal(self, proposal_id: int) -> bool:
        with self._lock:
            rejected = self.app.memory_lifecycle.reject_proposal(proposal_id)
            if rejected:
                self.app.audit.record(
                    actor=self.app.identity.system_user,
                    action="web.memory_proposal.reject",
                    target=str(proposal_id),
                    outcome="success",
                )
            return rejected

    def printable_chat(self, chat_id: str) -> str:
        detail = self.chat_detail(chat_id)
        chat = detail["chat"]
        turns = detail["turns"]
        rows: list[str] = []
        for turn in turns:
            attachment_html: list[str] = []
            for attachment in turn.get("attachments", []):
                filename = escape(str(attachment["filename"]))
                if str(attachment["kind"]) == "image":
                    content_url = escape(str(attachment["content_url"]), quote=True)
                    attachment_html.append(
                        "<figure class=\"attachment image\">"
                        f"<img src=\"{content_url}\" alt=\"{filename}\">"
                        f"<figcaption>{filename}</figcaption></figure>"
                    )
                else:
                    validation = escape(
                        str(attachment.get("validation_status", "not_checked"))
                    )
                    attachment_html.append(
                        "<p class=\"attachment file\">"
                        f"Adjunto: {filename} · validación={validation}</p>"
                    )
            rows.append(
                "<section class=\"turn\">"
                f"<h2>Usuario</h2><p>{escape(turn['user_text'])}</p>"
                + "".join(attachment_html)
                + f"<h2>{escape(self.app.persona.agent_name)}</h2>"
                + f"<p>{escape(turn['assistant_text'])}</p>"
                + "</section>"
            )
        if not rows:
            rows.append(
                "<section class=\"turn\"><h2>Resumen</h2>"
                f"<p>{escape(detail['summary'] or 'Sin contenido.')}</p></section>"
            )
        return (
            "<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{escape(chat['title'])} · Elyndra</title>"
            "<link rel=\"stylesheet\" href=\"/assets/print.css\">"
            "<script src=\"/assets/print.js\" defer></script></head><body>"
            "<header><p class=\"brand\">ELYNDRA</p>"
            f"<h1>{escape(chat['title'])}</h1>"
            f"<p>{escape(chat['id'])} · {chat['turn_count']} turnos</p></header>"
            "<main>"
            + "".join(rows)
            + "</main><footer>Exportado localmente desde Elyndra.</footer></body></html>"
        )

    def close(self) -> None:
        self._scheduler.close()
        self.app.release_language_engine()

    def _preferred_language(self) -> str:
        try:
            return LanguageConfig.load(self.app.paths).preferred_language
        except LanguageConfigError:
            return "es"

    def _history_for(self, chat_id: str) -> deque[ConversationTurn]:
        existing = self._session_history.get(chat_id)
        if existing is not None:
            self._session_history.move_to_end(chat_id)
            return existing

        recent = self.app.chats.recent_turns(chat_id, limit=6)
        history: deque[ConversationTurn] = deque(maxlen=6)
        for item in recent:
            history.append(
                ConversationTurn(
                    user=str(item["user_text"]),
                    assistant=str(item["assistant_text"]),
                )
            )
        self._session_history[chat_id] = history
        while len(self._session_history) > _MAX_ACTIVE_CHAT_WINDOWS:
            self._session_history.popitem(last=False)
        return history

    @staticmethod
    def _public_chat(chat: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": str(chat["public_id"]),
            "title": str(chat["title"]),
            "project": chat.get("project"),
            "status": str(chat["status"]),
            "transcript_mode": str(chat["transcript_mode"]),
            "pinned": bool(chat.get("pinned", 0)),
            "turn_count": int(chat["turn_count"]),
            "created_at": str(chat["created_at"]),
            "updated_at": str(chat["updated_at"]),
            "last_opened_at": str(chat["last_opened_at"]),
            "summary": str(chat.get("summary", "")),
        }


class _LocalThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def run_web_interface(
    app: ElyndraApplication,
    *,
    port: int = _DEFAULT_PORT,
    open_browser: bool = True,
) -> int:
    if not 1 <= port <= 65_535:
        raise ValueError("El puerto debe estar entre 1 y 65535.")

    service = ElyndraWebService(app)
    token = secrets.token_urlsafe(32)
    handler = _handler_factory(service, token)
    try:
        server = _LocalThreadingHTTPServer((_HOST, port), handler)
    except OSError as exc:
        service.close()
        raise ValueError(f"No se pudo iniciar Elyndra Web en {_HOST}:{port}: {exc}") from exc

    url = f"http://{_HOST}:{server.server_port}/"
    print(
        f"Elyndra Web {__version__}\n"
        f"Interfaz local: {url}\n"
        "Acceso restringido a este equipo (127.0.0.1).\n"
        "Presiona Ctrl+C para cerrar."
    )
    if open_browser:
        threading.Timer(0.35, _open_browser_safely, args=(url,)).start()

    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nElyndra Web cerrada.")
    finally:
        server.server_close()
        service.close()
    return 0


def _handler_factory(
    service: ElyndraWebService,
    token: str,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ElyndraLocal/0.6.0"
        sys_version = ""

        def do_GET(self) -> None:  # noqa: N802
            if not self._valid_host():
                self._json_error(HTTPStatus.FORBIDDEN, "Host local no permitido.")
                return
            parsed = urlparse(self.path)
            authenticated = service.registry_accounts.account_for_session(
                self._session_token()
            ) is not None
            registered = service.registry_accounts.has_account()
            if parsed.path == "/login":
                if authenticated:
                    self._redirect("/")
                    return
                self._send_application_page(auth_mode="login")
                return
            if parsed.path == "/register":
                self._send_application_page(auth_mode="register")
                return
            if (
                parsed.path
                in {"/", "/memory", "/alexandria", "/control", "/personal", "/profile"}
                or _chat_page_id_from_path(parsed.path) is not None
            ):
                if not registered:
                    self._redirect("/register")
                    return
                if not authenticated:
                    self._redirect("/login")
                    return
                if parsed.path in {"/alexandria", "/control"}:
                    account = service.ensure_session_app(self._session_token())
                    if not bool(account and account.get("developer_mode")):
                        self._redirect("/")
                        return
                self._send_application_page()
                return
            if parsed.path == "/assets/app.css":
                self._send_asset("app.css", "text/css; charset=utf-8")
                return
            if parsed.path == "/assets/app.js":
                self._send_asset("app.js", "text/javascript; charset=utf-8")
                return
            if parsed.path == "/assets/print.css":
                self._send_asset("print.css", "text/css; charset=utf-8")
                return
            if parsed.path == "/assets/print.js":
                self._send_asset("print.js", "text/javascript; charset=utf-8")
                return
            if parsed.path == "/api/bootstrap":
                self._send_json(
                    HTTPStatus.OK,
                    service.bootstrap(session_token=self._session_token()),
                )
                return
            if parsed.path.startswith("/api/") and not self._authenticated():
                self._json_error(HTTPStatus.UNAUTHORIZED, "Inicia sesión para continuar.")
                return
            if (
                parsed.path.startswith("/api/control/")
                or parsed.path.startswith("/api/alexandria/")
            ) and not self._developer_authenticated():
                self._json_error(HTTPStatus.FORBIDDEN, "Esta sección requiere modo desarrollador.")
                return
            if parsed.path == "/api/account":
                self._send_json(
                    HTTPStatus.OK, service.account_overview(self._session_token())
                )
                return
            if parsed.path == "/api/online/status":
                self._send_json(HTTPStatus.OK, service.online_status())
                return
            if parsed.path == "/api/online/sources":
                params = parse_qs(parsed.query)
                source_id = params.get("source_id", [""])[0]
                payload = (
                    service.online_source(source_id)
                    if source_id
                    else {"items": service.online_sources()}
                )
                self._send_json(HTTPStatus.OK, payload)
                return
            if parsed.path == "/api/online/operations":
                params = parse_qs(parsed.query)
                operation_id = params.get("operation_id", [""])[0]
                payload = (
                    service.online_operation(operation_id)
                    if operation_id
                    else {"items": service.online_operations()}
                )
                self._send_json(HTTPStatus.OK, payload)
                return
            if parsed.path == "/api/online/preview":
                params = parse_qs(parsed.query)
                source_id = params.get("source_id", [""])[0]
                self._send_json(HTTPStatus.OK, service.online_preview(source_id))
                return
            if parsed.path == "/api/alexandria/overview":
                self._send_json(HTTPStatus.OK, service.alexandria_overview())
                return
            if parsed.path == "/api/alexandria/language-packs":
                self._send_json(HTTPStatus.OK, service.language_pack_status())
                return
            if parsed.path == "/api/dictionary/lookup":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    service.dictionary_lookup(params.get("q", [""])[0]),
                )
                return
            if parsed.path == "/api/alexandria/libraries":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {"items": service.alexandria_libraries(query=params.get("q", [""])[0])},
                )
                return
            if parsed.path == "/api/alexandria/search":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.search_alexandria(
                            params.get("q", [""])[0],
                            library=_optional_string(params.get("library", [""])[0]),
                        )
                    },
                )
                return
            alexandria_library_id = _alexandria_library_id_from_path(parsed.path)
            if alexandria_library_id is not None:
                try:
                    self._send_json(
                        HTTPStatus.OK,
                        service.alexandria_library(alexandria_library_id),
                    )
                except ValueError as exc:
                    self._json_error(HTTPStatus.NOT_FOUND, str(exc))
                return
            if parsed.path == "/api/personal/overview":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    service.personal_overview(
                        date_value=_optional_string(params.get("date", [""])[0])
                    ),
                )
                return
            if parsed.path == "/api/control/overview":
                self._send_json(HTTPStatus.OK, service.control_overview())
                return
            if parsed.path == "/api/control/ethics":
                self._send_json(
                    HTTPStatus.OK,
                    service.control_ethics(),
                )
                return
            if parsed.path == "/api/control/dictionary":
                self._send_json(HTTPStatus.OK, service.control_dictionary())
                return
            if parsed.path == "/api/control/translation":
                self._send_json(HTTPStatus.OK, service.control_translation())
                return
            if parsed.path == "/api/control/preferences":
                self._send_json(HTTPStatus.OK, service.control_preferences())
                return
            if parsed.path == "/api/control/tutors":
                self._send_json(HTTPStatus.OK, service.control_tutors())
                return
            if parsed.path == "/api/control/executive":
                self._send_json(HTTPStatus.OK, service.control_executive())
                return
            if parsed.path == "/api/control/first-aid":
                self._send_json(HTTPStatus.OK, service.control_first_aid())
                return
            if parsed.path == "/api/control/structured-packs":
                self._send_json(HTTPStatus.OK, service.control_structured_packs())
                return
            if parsed.path == "/api/control/memory-tiers":
                self._send_json(HTTPStatus.OK, service.control_memory_tiers())
                return
            if parsed.path == "/api/translation/lookup":
                params = parse_qs(parsed.query)
                text = params.get("text", [""])[0].strip()
                target = params.get("to", [""])[0].strip()
                if not text or not target:
                    self._json_error(
                        HTTPStatus.BAD_REQUEST, "Faltan los parámetros text o to."
                    )
                    return
                self._send_json(
                    HTTPStatus.OK,
                    service.translation_lookup(
                        text,
                        target_language=target,
                        response_language=params.get("response_language", ["es"])[0] or "es",
                    ),
                )
                return
            if parsed.path == "/api/first-aid/lookup":
                params = parse_qs(parsed.query)
                query = params.get("query", [""])[0].strip()
                if not query:
                    self._json_error(HTTPStatus.BAD_REQUEST, "Falta el parámetro query.")
                    return
                self._send_json(
                    HTTPStatus.OK,
                    service.first_aid_lookup(
                        query,
                        language=params.get("language", ["es"])[0] or "es",
                        locale=_optional_string(params.get("locale", [""])[0]),
                    ),
                )
                return
            if parsed.path == "/api/dictionary/lookup":
                params = parse_qs(parsed.query)
                term = params.get("term", [""])[0].strip()
                if not term:
                    self._json_error(HTTPStatus.BAD_REQUEST, "Falta el parámetro term.")
                    return
                try:
                    self._send_json(
                        HTTPStatus.OK,
                        service.dictionary_lookup(
                            term,
                            language=_optional_string(
                                params.get("language", [""])[0]
                            ),
                            output_language=(
                                params.get("output_language", ["es"])[0] or "es"
                            ),
                            dialect=_optional_string(
                                params.get("dialect", [""])[0]
                            ),
                            limit=int(params.get("limit", ["5"])[0]),
                        ),
                    )
                except ValueError as exc:
                    self._json_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if parsed.path == "/api/control/action-runs":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_action_runs(
                            limit=_safe_limit(
                                params.get("limit", ["20"])[0],
                                default=20,
                            )
                        )
                    },
                )
                return
            if parsed.path == "/api/control/change-proposals":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_change_proposals(
                            limit=_safe_limit(
                                params.get("limit", ["20"])[0],
                                default=20,
                            )
                        )
                    },
                )
                return
            if parsed.path == "/api/control/validation-cycles":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_validation_cycles(
                            limit=_safe_limit(
                                params.get("limit", ["20"])[0],
                                default=20,
                            )
                        )
                    },
                )
                return
            if parsed.path == "/api/control/development-sessions":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_development_sessions(
                            limit=_safe_limit(
                                params.get("limit", ["20"])[0],
                                default=20,
                            )
                        )
                    },
                )
                return
            if parsed.path == "/api/control/projects":
                self._send_json(HTTPStatus.OK, service.control_projects())
                return
            if parsed.path == "/api/control/web-verifications":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_web_verifications(
                            project_root=params.get("project_root", [""])[0],
                            limit=_safe_limit(params.get("limit", ["50"])[0]),
                        )
                    },
                )
                return
            if parsed.path == "/api/control/ruby-verifications":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_ruby_verifications(
                            project_root=params.get("project_root", [None])[0],
                            limit=_safe_limit(
                                params.get("limit", ["20"])[0],
                                default=20,
                            ),
                        )
                    },
                )
                return
            if parsed.path == "/api/control/go-verifications":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_go_verifications(
                            project_root=params.get("project_root", [None])[0],
                            limit=_safe_limit(
                                params.get("limit", ["20"])[0],
                                default=20,
                            ),
                        )
                    },
                )
                return
            if parsed.path == "/api/control/kotlin-verifications":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_kotlin_verifications(
                            project_root=params.get("project_root", [None])[0],
                            limit=_safe_limit(
                                params.get("limit", ["20"])[0],
                                default=20,
                            ),
                        )
                    },
                )
                return
            if parsed.path == "/api/control/dotnet-verifications":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_dotnet_verifications(
                            project_root=params.get("project_root", [None])[0],
                            limit=_safe_limit(
                                params.get("limit", ["20"])[0],
                                default=20,
                            ),
                        )
                    },
                )
                return
            if parsed.path == "/api/control/swift-verifications":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_swift_verifications(
                            project_root=params.get("project_root", [None])[0],
                            limit=_safe_limit(
                                params.get("limit", ["20"])[0],
                                default=20,
                            ),
                        )
                    },
                )
                return
            if parsed.path == "/api/control/dart-verifications":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_dart_verifications(
                            project_root=params.get("project_root", [None])[0],
                            limit=_safe_limit(
                                params.get("limit", ["20"])[0],
                                default=20,
                            ),
                        )
                    },
                )
                return
            if parsed.path == "/api/control/sql-verifications":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_sql_verifications(
                            project_root=params.get("project_root", [None])[0],
                            limit=_safe_limit(
                                params.get("limit", ["20"])[0],
                                default=20,
                            ),
                        )
                    },
                )
                return
            if parsed.path == "/api/control/rust-verifications":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_rust_verifications(
                            project_root=params.get("project_root", [None])[0],
                            limit=_safe_limit(
                                params.get("limit", ["20"])[0],
                                default=20,
                            ),
                        )
                    },
                )
                return
            if parsed.path == "/api/control/python-verifications":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_python_verifications(
                            project_root=params.get("project_root", [None])[0],
                            limit=_safe_limit(
                                params.get("limit", ["20"])[0],
                                default=20,
                            ),
                        )
                    },
                )
                return
            if parsed.path == "/api/control/java-verifications":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_java_verifications(
                            project_root=params.get("project_root", [None])[0],
                            limit=_safe_limit(
                                params.get("limit", ["20"])[0],
                                default=20,
                            ),
                        )
                    },
                )
                return
            if parsed.path == "/api/control/native-verifications":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_native_verifications(
                            project_root=params.get("project_root", [None])[0],
                            limit=_safe_limit(
                                params.get("limit", ["20"])[0],
                                default=20,
                            ),
                        )
                    },
                )
                return
            if parsed.path == "/api/control/alexandria-packages":
                self._send_json(
                    HTTPStatus.OK,
                    {"items": service.control_alexandria_packages()},
                )
                return
            if parsed.path == "/api/control/php-verifications":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_php_verifications(
                            project_root=params.get("project_root", [""])[0],
                            limit=_safe_limit(params.get("limit", ["50"])[0]),
                        )
                    },
                )
                return
            if parsed.path == "/api/control/audit":
                params = parse_qs(parsed.query)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "items": service.control_audit(
                            limit=_safe_limit(params.get("limit", ["100"])[0]),
                            action=params.get("action", [""])[0],
                            outcome=params.get("outcome", [""])[0],
                            query=params.get("q", [""])[0],
                        )
                    },
                )
                return
            if parsed.path == "/api/inspector/overview":
                self._send_json(HTTPStatus.OK, service.inspector_overview())
                return
            if parsed.path.startswith("/api/inspector/"):
                params = parse_qs(parsed.query)
                limit = _safe_limit(params.get("limit", ["100"])[0])
                if parsed.path == "/api/inspector/memories":
                    items = service.inspector_memories(
                        query=params.get("q", [""])[0], limit=limit
                    )
                    self._send_json(HTTPStatus.OK, {"items": items})
                    return
                if parsed.path == "/api/inspector/episodes":
                    items = service.inspector_episodes(
                        query=params.get("q", [""])[0],
                        kind=params.get("kind", [""])[0],
                        limit=limit,
                    )
                    self._send_json(HTTPStatus.OK, {"items": items})
                    return
                if parsed.path == "/api/inspector/proposals":
                    try:
                        items = service.inspector_proposals(
                            status=params.get("status", ["pending"])[0],
                            limit=limit,
                        )
                    except ValueError as exc:
                        self._json_error(HTTPStatus.BAD_REQUEST, str(exc))
                        return
                    self._send_json(HTTPStatus.OK, {"items": items})
                    return
                if parsed.path == "/api/inspector/corrections":
                    self._send_json(
                        HTTPStatus.OK,
                        {"items": service.inspector_corrections(limit=limit)},
                    )
                    return
                if parsed.path == "/api/inspector/documents":
                    self._send_json(
                        HTTPStatus.OK,
                        {"items": service.inspector_documents(limit=limit)},
                    )
                    return
                if parsed.path == "/api/inspector/attachments":
                    self._send_json(
                        HTTPStatus.OK,
                        {"items": service.inspector_attachments(limit=limit)},
                    )
                    return
                if parsed.path == "/api/inspector/audit":
                    self._send_json(
                        HTTPStatus.OK,
                        {"items": service.inspector_audit(limit=limit)},
                    )
                    return
                if parsed.path == "/api/inspector/archives":
                    self._send_json(
                        HTTPStatus.OK,
                        {"items": service.inspector_archives(limit=limit)},
                    )
                    return
            if parsed.path == "/api/chats":
                params = parse_qs(parsed.query)
                query = params.get("q", [""])[0]
                status = params.get("status", ["active"])[0]
                try:
                    chats = service.list_chats(query=query, status=status)
                except ValueError as exc:
                    self._json_error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._send_json(HTTPStatus.OK, {"chats": chats})
                return
            export_id = _export_chat_id_from_path(parsed.path)
            if export_id is not None:
                try:
                    body = service.printable_chat(export_id).encode("utf-8")
                except ValueError as exc:
                    self._json_error(HTTPStatus.NOT_FOUND, str(exc))
                    return
                self._send_bytes(HTTPStatus.OK, body, "text/html; charset=utf-8")
                return
            attachment_id = _attachment_content_id_from_path(parsed.path)
            if attachment_id is not None:
                try:
                    content = service.attachment_content(attachment_id)
                except ValueError as exc:
                    self._json_error(HTTPStatus.NOT_FOUND, str(exc))
                    return
                self._send_bytes(
                    HTTPStatus.OK,
                    content.path.read_bytes(),
                    content.mime_type,
                )
                return
            chat_id = _chat_id_from_path(parsed.path, suffix="")
            if chat_id is not None:
                try:
                    self._send_json(HTTPStatus.OK, service.chat_detail(chat_id))
                except ValueError as exc:
                    self._json_error(HTTPStatus.NOT_FOUND, str(exc))
                return
            self._json_error(HTTPStatus.NOT_FOUND, "Ruta no encontrada.")

        def do_POST(self) -> None:  # noqa: N802
            if not self._valid_host():
                self._json_error(HTTPStatus.FORBIDDEN, "Host local no permitido.")
                return
            if not self._valid_token():
                self._json_error(HTTPStatus.FORBIDDEN, "Token local inválido.")
                return
            parsed = urlparse(self.path)
            try:
                payload = self._read_json()
                if parsed.path == "/api/auth/register":
                    account, session_token = service.register_account(payload)
                    self._send_json(
                        HTTPStatus.CREATED,
                        {"account": account, "authenticated": True},
                        cookie=self._auth_cookie(session_token),
                    )
                    return
                if parsed.path == "/api/auth/login":
                    account, session_token = service.login_account(payload)
                    self._send_json(
                        HTTPStatus.OK,
                        {"account": account, "authenticated": True},
                        cookie=self._auth_cookie(session_token),
                    )
                    return
                if parsed.path == "/api/auth/logout":
                    service.registry_accounts.revoke_session(self._session_token())
                    self._send_json(
                        HTTPStatus.OK,
                        {"authenticated": False},
                        cookie=self._expired_auth_cookie(),
                    )
                    return
                if not self._authenticated():
                    self._json_error(HTTPStatus.UNAUTHORIZED, "Inicia sesión para continuar.")
                    return
                if (
                    parsed.path.startswith("/api/control/")
                    or parsed.path.startswith("/api/alexandria/")
                ) and not self._developer_authenticated():
                    self._json_error(
                        HTTPStatus.FORBIDDEN,
                        "Esta sección requiere modo desarrollador.",
                    )
                    return
                if parsed.path == "/api/account/profile":
                    account = service.update_account_profile(
                        self._session_token(), payload
                    )
                    self._send_json(HTTPStatus.OK, {"account": account})
                    return
                if parsed.path == "/api/online":
                    self._send_json(HTTPStatus.OK, service.online_write(payload))
                    return
                if parsed.path == "/api/dictionary/overlays":
                    self._send_json(
                        HTTPStatus.CREATED,
                        {"item": service.propose_language_overlay(payload)},
                    )
                    return
                if parsed.path == "/api/dictionary/overlays/review":
                    self._send_json(
                        HTTPStatus.OK,
                        {"item": service.review_language_overlay(payload)},
                    )
                    return
                if parsed.path == "/api/account/email":
                    account = service.change_account_email(
                        self._session_token(), payload
                    )
                    self._send_json(HTTPStatus.OK, {"account": account})
                    return
                if parsed.path == "/api/account/password":
                    service.change_account_password(self._session_token(), payload)
                    self._send_json(
                        HTTPStatus.OK,
                        {"password_changed": True, "authenticated": False},
                        cookie=self._expired_auth_cookie(),
                    )
                    return
                if parsed.path == "/api/account/export":
                    export_bytes = service.export_account(
                        self._session_token(), payload
                    )
                    self._send_bytes(
                        HTTPStatus.OK,
                        export_bytes,
                        "application/vnd.elyndra.encrypted-export+json",
                        download_name="elyndra-encrypted-export.json",
                    )
                    return
                if parsed.path == "/api/control/trusted-projects":
                    item = service.trust_project(str(payload.get("path", "")))
                    self._send_json(HTTPStatus.CREATED, {"item": item})
                    return
                if parsed.path == "/api/control/php-profiles":
                    item = service.save_php_profile(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/control/web-profiles":
                    item = service.save_web_profile(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/control/python-profiles":
                    item = service.save_python_profile(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/control/java-profiles":
                    item = service.save_java_profile(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/control/kotlin-profiles":
                    item = service.save_kotlin_profile(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/control/kotlin-profiles/delete":
                    removed = service.delete_kotlin_profile(
                        str(payload.get("path", ""))
                    )
                    self._send_json(HTTPStatus.OK, {"removed": removed})
                    return
                if parsed.path == "/api/control/dotnet-profiles":
                    item = service.save_dotnet_profile(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/control/dotnet-profiles/delete":
                    removed = service.delete_dotnet_profile(
                        str(payload.get("path", ""))
                    )
                    self._send_json(HTTPStatus.OK, {"removed": removed})
                    return
                if parsed.path == "/api/control/swift-profiles":
                    item = service.save_swift_profile(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/control/swift-profiles/delete":
                    removed = service.delete_swift_profile(
                        str(payload.get("path", ""))
                    )
                    self._send_json(HTTPStatus.OK, {"removed": removed})
                    return
                if parsed.path == "/api/control/dart-profiles":
                    item = service.save_dart_profile(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/control/dart-profiles/delete":
                    removed = service.delete_dart_profile(
                        str(payload.get("path", ""))
                    )
                    self._send_json(HTTPStatus.OK, {"removed": removed})
                    return
                if parsed.path == "/api/control/sql-profiles":
                    item = service.save_sql_profile(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/control/sql-profiles/delete":
                    removed = service.delete_sql_profile(
                        str(payload.get("path", ""))
                    )
                    self._send_json(HTTPStatus.OK, {"removed": removed})
                    return
                if parsed.path == "/api/control/ruby-profiles":
                    item = service.save_ruby_profile(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/control/ruby-profiles/delete":
                    removed = service.delete_ruby_profile(str(payload.get("path", "")))
                    self._send_json(HTTPStatus.OK, {"removed": removed})
                    return
                if parsed.path == "/api/control/go-profiles":
                    item = service.save_go_profile(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/control/go-profiles/delete":
                    removed = service.delete_go_profile(str(payload.get("path", "")))
                    self._send_json(HTTPStatus.OK, {"removed": removed})
                    return
                if parsed.path == "/api/control/rust-profiles":
                    item = service.save_rust_profile(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/control/rust-profiles/delete":
                    removed = service.delete_rust_profile(str(payload.get("path", "")))
                    self._send_json(HTTPStatus.OK, {"removed": removed})
                    return
                if parsed.path == "/api/control/native-profiles":
                    item = service.save_native_profile(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/control/alexandria-packages/create":
                    item = service.create_alexandria_package(payload)
                    self._send_json(HTTPStatus.CREATED, {"item": item})
                    return
                if parsed.path == "/api/alexandria/language-packs/inspect":
                    self._send_json(HTTPStatus.OK, {"item": service.inspect_language_pack(payload)})
                    return
                if parsed.path == "/api/alexandria/language-packs/install":
                    self._send_json(
                        HTTPStatus.CREATED, {"item": service.install_language_pack(payload)}
                    )
                    return
                if parsed.path == "/api/alexandria/language-bundles/inspect":
                    self._send_json(
                        HTTPStatus.OK, {"item": service.inspect_language_bundle(payload)}
                    )
                    return
                if parsed.path == "/api/alexandria/language-bundles/install":
                    self._send_json(
                        HTTPStatus.CREATED, {"item": service.install_language_bundle(payload)}
                    )
                    return
                if parsed.path in {
                    "/api/alexandria/language-packs/enable",
                    "/api/alexandria/language-packs/disable",
                }:
                    item = service.set_language_pack_enabled(
                        payload, enabled=parsed.path.endswith("/enable")
                    )
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/control/alexandria-packages/install":
                    item = service.install_alexandria_package(
                        str(payload.get("path", ""))
                    )
                    self._send_json(HTTPStatus.CREATED, {"item": item})
                    return
                if parsed.path == "/api/control/alexandria-packages/export":
                    item = service.export_alexandria_package(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                inspector_action = _inspector_action_from_path(parsed.path)
                if inspector_action is not None:
                    resource, item_id, action = inspector_action
                    if resource == "proposals" and action == "approve":
                        proposal = service.approve_proposal(item_id)
                        self._send_json(HTTPStatus.OK, {"item": proposal})
                        return
                    if resource == "proposals" and action == "reject":
                        if not service.reject_proposal(item_id):
                            raise ValueError("Propuesta pendiente no encontrada.")
                        self._send_json(HTTPStatus.OK, {"rejected": item_id})
                        return
                    self._json_error(HTTPStatus.NOT_FOUND, "Acción no encontrada.")
                    return
                attachment_action = _attachment_action_from_path(parsed.path)
                if attachment_action is not None:
                    attachment_id, action = attachment_action
                    if action == "reprocess":
                        item = service.reprocess_attachment(attachment_id)
                        self._send_json(HTTPStatus.OK, {"attachment": item})
                        return
                    self._json_error(HTTPStatus.NOT_FOUND, "Acción no encontrada.")
                    return
                if parsed.path == "/api/alexandria/libraries":
                    library = service.create_alexandria_library(payload)
                    self._send_json(HTTPStatus.CREATED, {"library": library})
                    return
                alexandria_action = _alexandria_action_from_path(parsed.path)
                if alexandria_action is not None:
                    resource, identifier, action_name = alexandria_action
                    if resource == "libraries" and action_name == "sources":
                        source = service.import_alexandria_source(
                            identifier,
                            filename=str(payload.get("filename", "")),
                            data_base64=str(payload.get("data_base64", "")),
                            title=str(payload.get("title", "")),
                            source_url=str(payload.get("source_url", "")),
                        )
                        self._send_json(HTTPStatus.CREATED, {"source": source})
                        return
                    if resource == "sources" and action_name == "review":
                        source = service.review_alexandria_source(int(identifier))
                        self._send_json(HTTPStatus.OK, {"source": source})
                        return
                if parsed.path.startswith("/api/approvals/") and parsed.path.endswith("/cancel"):
                    token_path = parsed.path[len("/api/approvals/") : -len("/cancel")]
                    token_value = unquote(token_path).strip("/")
                    chat_id_value = str(payload.get("chat_id", "")).strip()
                    if not token_value or not chat_id_value:
                        raise ValueError("Faltan token o chat_id para cancelar la aprobación.")
                    cancelled = service.cancel_skill_approval(chat_id_value, token_value)
                    self._send_json(HTTPStatus.OK, {"cancelled": cancelled})
                    return
                if parsed.path == "/api/personal/commitments":
                    item = service.create_personal_commitment(payload)
                    self._send_json(HTTPStatus.CREATED, {"item": item})
                    return
                if parsed.path == "/api/personal/routines":
                    item = service.create_personal_routine(payload)
                    self._send_json(HTTPStatus.CREATED, {"item": item})
                    return
                if parsed.path == "/api/personal/birthdays":
                    item = service.create_personal_birthday(payload)
                    self._send_json(HTTPStatus.CREATED, {"item": item})
                    return
                if parsed.path == "/api/personal/wellbeing/checkins":
                    item = service.create_wellbeing_checkin(payload)
                    self._send_json(HTTPStatus.CREATED, {"item": item})
                    return
                if parsed.path == "/api/personal/routines/checkins":
                    item = service.create_routine_checkin(payload)
                    self._send_json(HTTPStatus.CREATED, {"item": item})
                    return
                if parsed.path == "/api/personal/reminders":
                    item = service.propose_personal_reminder(payload)
                    self._send_json(HTTPStatus.CREATED, {"item": item})
                    return
                if parsed.path == "/api/personal/reminders/review":
                    item = service.review_personal_reminder(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/personal/items/status":
                    item = service.update_personal_item(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/personal/coaching/plans":
                    item = service.create_coaching_plan(payload)
                    self._send_json(HTTPStatus.CREATED, {"item": item})
                    return
                if parsed.path == "/api/personal/coaching/plans/status":
                    item = service.update_coaching_plan(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/personal/coaching/actions/status":
                    item = service.update_coaching_action(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/personal/automation/policies":
                    item = service.create_automation_policy(payload)
                    self._send_json(HTTPStatus.CREATED, {"item": item})
                    return
                if parsed.path == "/api/personal/automation/policies/status":
                    item = service.update_automation_policy(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/personal/automations":
                    item = service.create_automation(payload)
                    self._send_json(HTTPStatus.CREATED, {"item": item})
                    return
                if parsed.path == "/api/personal/automations/status":
                    item = service.update_automation(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/personal/automations/scan":
                    data = service.scan_automations(payload)
                    self._send_json(HTTPStatus.OK, data)
                    return
                if parsed.path == "/api/personal/automations/runs/approve":
                    item = service.approve_automation_run(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/personal/automation/inbox/status":
                    item = service.update_automation_inbox(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/personal/scheduler/cycle":
                    data = service.scheduler_cycle(payload)
                    self._send_json(HTTPStatus.OK, data)
                    return
                if parsed.path == "/api/personal/scheduler/start":
                    data = service.scheduler_start(payload)
                    self._send_json(HTTPStatus.OK, data)
                    return
                if parsed.path == "/api/personal/scheduler/stop":
                    data = service.scheduler_stop(payload)
                    self._send_json(HTTPStatus.OK, data)
                    return
                if parsed.path == "/api/personal/notifications/status":
                    item = service.update_local_notification(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/personal/intents/proposals":
                    item = service.propose_intent_learning(payload)
                    self._send_json(HTTPStatus.CREATED, {"item": item})
                    return
                if parsed.path == "/api/personal/intents/proposals/review":
                    item = service.review_intent_learning(payload)
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                if parsed.path == "/api/chats":
                    detail = service.create_chat(
                        title=_optional_string(payload.get("title")),
                        transcript_mode=str(payload.get("transcript_mode", "full")),
                    )
                    self._send_json(HTTPStatus.CREATED, detail)
                    return
                action = _chat_action_from_path(parsed.path)
                if action is not None:
                    chat_id, name = action
                    if name in {"messages", "messages/stream"}:
                        raw_ids = payload.get("attachment_ids", [])
                        if not isinstance(raw_ids, list):
                            raise ValueError("attachment_ids debe ser una lista.")
                        attachment_ids = [str(value) for value in raw_ids]
                        raw_token = payload.get("approval_token")
                        if raw_token is not None and not isinstance(raw_token, str):
                            raise ValueError("approval_token debe ser texto.")
                        approval_token = raw_token or None
                        if name == "messages/stream":
                            self._send_message_stream(
                                service,
                                chat_id,
                                str(payload.get("text", "")),
                                attachment_ids,
                                approval_token=approval_token,
                            )
                            return
                        response = service.send_message(
                            chat_id,
                            str(payload.get("text", "")),
                            attachment_ids=attachment_ids,
                            approval_token=approval_token,
                        )
                        self._send_json(HTTPStatus.OK, response)
                        return
                    if name == "attachments":
                        attachment = service.create_attachment(
                            chat_id,
                            filename=str(payload.get("filename", "")),
                            mime_type=str(payload.get("mime_type", "")),
                            data_base64=str(payload.get("data_base64", "")),
                        )
                        self._send_json(
                            HTTPStatus.CREATED,
                            {"attachment": attachment},
                        )
                        return
                    if name == "pin":
                        chat = service.set_pinned(chat_id, bool(payload.get("pinned", True)))
                    elif name == "archive":
                        chat = service.archive_chat(chat_id)
                    elif name == "restore":
                        chat = service.restore_chat(chat_id)
                    else:
                        self._json_error(HTTPStatus.NOT_FOUND, "Acción no encontrada.")
                        return
                    self._send_json(HTTPStatus.OK, {"chat": chat})
                    return
            except (PermissionError, ValueError) as exc:
                self._json_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._json_error(HTTPStatus.NOT_FOUND, "Ruta no encontrada.")

        def do_PATCH(self) -> None:  # noqa: N802
            if not self._valid_host():
                self._json_error(HTTPStatus.FORBIDDEN, "Host local no permitido.")
                return
            if not self._valid_token():
                self._json_error(HTTPStatus.FORBIDDEN, "Token local inválido.")
                return
            parsed = urlparse(self.path)
            if not self._authenticated():
                self._json_error(HTTPStatus.UNAUTHORIZED, "Inicia sesión para continuar.")
                return
            if (
                parsed.path.startswith("/api/control/")
                or parsed.path.startswith("/api/alexandria/")
            ) and not self._developer_authenticated():
                self._json_error(HTTPStatus.FORBIDDEN, "Esta sección requiere modo desarrollador.")
                return
            inspector_item = _inspector_item_from_path(parsed.path)
            try:
                payload = self._read_json()
                alexandria_library_id = _alexandria_library_id_from_path(parsed.path)
                if alexandria_library_id is not None:
                    library = service.update_alexandria_library(
                        alexandria_library_id,
                        payload,
                    )
                    self._send_json(HTTPStatus.OK, {"library": library})
                    return
                if inspector_item is not None:
                    resource, item_id = inspector_item
                    if resource == "memories":
                        item = service.update_memory(
                            item_id,
                            content=str(payload.get("content", "")),
                            kind=_optional_string(payload.get("kind")),
                            project=_optional_string(payload.get("project")),
                        )
                    elif resource == "episodes":
                        item = service.update_episode(
                            item_id,
                            content=str(payload.get("content", "")),
                            kind=_optional_string(payload.get("kind")),
                        )
                    elif resource == "proposals":
                        item = service.edit_proposal(
                            item_id, str(payload.get("content", ""))
                        )
                    else:
                        self._json_error(HTTPStatus.NOT_FOUND, "Recurso no encontrado.")
                        return
                    self._send_json(HTTPStatus.OK, {"item": item})
                    return
                chat_id = _chat_id_from_path(parsed.path, suffix="")
                if chat_id is None:
                    self._json_error(HTTPStatus.NOT_FOUND, "Ruta no encontrada.")
                    return
                chat: dict[str, Any] | None = None
                if "title" in payload:
                    chat = service.rename_chat(chat_id, str(payload["title"]))
                if "transcript_mode" in payload:
                    chat = service.set_transcript_mode(
                        chat_id,
                        str(payload["transcript_mode"]),
                    )
                if chat is None:
                    raise ValueError("No se recibió ningún cambio válido.")
                self._send_json(HTTPStatus.OK, {"chat": chat})
            except ValueError as exc:
                self._json_error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_DELETE(self) -> None:  # noqa: N802
            if not self._valid_host():
                self._json_error(HTTPStatus.FORBIDDEN, "Host local no permitido.")
                return
            if not self._valid_token():
                self._json_error(HTTPStatus.FORBIDDEN, "Token local inválido.")
                return
            parsed = urlparse(self.path)
            if not self._authenticated():
                self._json_error(HTTPStatus.UNAUTHORIZED, "Inicia sesión para continuar.")
                return
            if (
                parsed.path.startswith("/api/control/")
                or parsed.path.startswith("/api/alexandria/")
            ) and not self._developer_authenticated():
                self._json_error(HTTPStatus.FORBIDDEN, "Esta sección requiere modo desarrollador.")
                return
            if parsed.path in {
                "/api/control/trusted-projects",
                "/api/control/php-profiles",
                "/api/control/web-profiles",
                "/api/control/python-profiles",
                "/api/control/java-profiles",
                "/api/control/kotlin-profiles",
                "/api/control/dotnet-profiles",
                "/api/control/swift-profiles",
                "/api/control/dart-profiles",
                "/api/control/sql-profiles",
                "/api/control/native-profiles",
                "/api/control/ruby-profiles",
                "/api/control/go-profiles",
                "/api/control/rust-profiles",
            }:
                params = parse_qs(parsed.query)
                path_value = params.get("path", [""])[0]
                if not path_value:
                    self._json_error(HTTPStatus.BAD_REQUEST, "Falta la ruta del proyecto.")
                    return
                if parsed.path == "/api/control/trusted-projects":
                    removed = service.untrust_project(path_value)
                elif parsed.path == "/api/control/php-profiles":
                    removed = service.delete_php_profile(path_value)
                elif parsed.path == "/api/control/web-profiles":
                    removed = service.delete_web_profile(path_value)
                elif parsed.path == "/api/control/python-profiles":
                    removed = service.delete_python_profile(path_value)
                elif parsed.path == "/api/control/java-profiles":
                    removed = service.delete_java_profile(path_value)
                elif parsed.path == "/api/control/kotlin-profiles":
                    removed = service.delete_kotlin_profile(path_value)
                elif parsed.path == "/api/control/dotnet-profiles":
                    removed = service.delete_dotnet_profile(path_value)
                elif parsed.path == "/api/control/swift-profiles":
                    removed = service.delete_swift_profile(path_value)
                elif parsed.path == "/api/control/dart-profiles":
                    removed = service.delete_dart_profile(path_value)
                elif parsed.path == "/api/control/sql-profiles":
                    removed = service.delete_sql_profile(path_value)
                elif parsed.path == "/api/control/native-profiles":
                    removed = service.delete_native_profile(path_value)
                elif parsed.path == "/api/control/ruby-profiles":
                    removed = service.delete_ruby_profile(path_value)
                elif parsed.path == "/api/control/go-profiles":
                    removed = service.delete_go_profile(path_value)
                else:
                    removed = service.delete_rust_profile(path_value)
                if not removed:
                    self._json_error(HTTPStatus.NOT_FOUND, "Elemento no encontrado.")
                    return
                self._send_json(HTTPStatus.OK, {"removed": path_value})
                return
            alexandria_library_id = _alexandria_library_id_from_path(parsed.path)
            if alexandria_library_id is not None:
                try:
                    deleted = service.delete_alexandria_library(
                        alexandria_library_id
                    )
                except ValueError as exc:
                    self._json_error(HTTPStatus.NOT_FOUND, str(exc))
                    return
                self._send_json(HTTPStatus.OK, {"deleted": deleted})
                return
            inspector_item = _inspector_item_from_path(parsed.path)
            if inspector_item is not None:
                resource, item_id = inspector_item
                if resource == "memories":
                    deleted = service.forget_memory(item_id)
                elif resource == "episodes":
                    deleted = service.forget_episode(item_id)
                else:
                    self._json_error(HTTPStatus.NOT_FOUND, "Recurso no encontrado.")
                    return
                if not deleted:
                    self._json_error(HTTPStatus.NOT_FOUND, "Elemento activo no encontrado.")
                    return
                self._send_json(HTTPStatus.OK, {"forgotten": item_id})
                return
            attachment_id = _attachment_id_from_path(parsed.path)
            if attachment_id is not None:
                try:
                    deleted = service.delete_pending_attachment(attachment_id)
                except ValueError as exc:
                    self._json_error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                if not deleted:
                    self._json_error(HTTPStatus.NOT_FOUND, "Adjunto no encontrado.")
                    return
                self._send_json(HTTPStatus.OK, {"deleted": attachment_id})
                return
            chat_id = _chat_id_from_path(parsed.path, suffix="")
            if chat_id is None:
                self._json_error(HTTPStatus.NOT_FOUND, "Ruta no encontrada.")
                return
            try:
                deleted = service.delete_chat_permanently(chat_id)
            except ValueError as exc:
                self._json_error(HTTPStatus.NOT_FOUND, str(exc))
                return
            self._send_json(HTTPStatus.OK, {"deleted": deleted})

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_message_stream(
            self,
            service: ElyndraWebService,
            chat_id: str,
            text: str,
            attachment_ids: list[str],
            *,
            approval_token: str | None = None,
        ) -> None:
            disconnected = False
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError):
                return

            def emit(payload: dict[str, Any]) -> None:
                nonlocal disconnected
                if disconnected:
                    return
                raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
                try:
                    self.wfile.write(raw)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    disconnected = True

            route = service.app.router.route(text)
            orchestration = service.app.action_planner.should_plan(text, route)
            if orchestration:
                initial_message = "Preparando plan supervisado…"
                initial_stage = "orchestration"
            elif route.kind == "skill":
                initial_message = "Preparando skill local…"
                initial_stage = "skill"
            elif route.kind == "organizer":
                initial_message = "Consultando el organizador local…"
                initial_stage = "organizer"
            elif route.kind == "wellbeing":
                initial_message = "Consultando el seguimiento local…"
                initial_stage = "wellbeing"
            else:
                initial_message = "Buscando en Alejandría…"
                initial_stage = "retrieval"
            emit(
                {
                    "type": "status",
                    "stage": initial_stage,
                    "message": initial_message,
                }
            )
            emitted_token = False

            def on_token(token_text: str) -> None:
                nonlocal emitted_token
                if not emitted_token:
                    emit(
                        {
                            "type": "status",
                            "stage": "generation",
                            "message": "Redactando respuesta…",
                        }
                    )
                    emitted_token = True
                emit({"type": "token", "text": token_text})

            try:
                response = service.send_message(
                    chat_id,
                    text,
                    attachment_ids=attachment_ids,
                    approval_token=approval_token,
                    on_token=on_token,
                )
            except (RuntimeError, ValueError) as exc:
                emit({"type": "error", "error": str(exc)})
                return
            emit({"type": "done", "response": response})

        def _read_json(self) -> dict[str, Any]:
            content_type = self.headers.get("Content-Type", "")
            if "application/json" not in content_type:
                raise ValueError("La solicitud debe usar application/json.")
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValueError("Content-Length inválido.") from exc
            if length < 0 or length > _MAX_BODY_BYTES:
                raise ValueError("La solicitud supera el tamaño permitido.")
            try:
                value = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("JSON inválido.") from exc
            if not isinstance(value, dict):
                raise ValueError("El cuerpo JSON debe ser un objeto.")
            return value

        def _valid_host(self) -> bool:
            host_header = self.headers.get("Host", "")
            host = host_header.rsplit(":", 1)[0].strip("[]").casefold()
            return host in _ALLOWED_HOSTS

        def _valid_token(self) -> bool:
            supplied = self.headers.get("X-Elyndra-Token", "")
            return secrets.compare_digest(supplied, token)

        def _session_token(self) -> str:
            raw = self.headers.get("Cookie", "")
            if not raw:
                return ""
            cookie = SimpleCookie()
            with suppress(Exception):
                cookie.load(raw)
            morsel = cookie.get(_AUTH_COOKIE)
            return morsel.value if morsel is not None else ""

        def _authenticated(self) -> bool:
            account = service.ensure_session_app(self._session_token())
            if account is not None:
                return True
            return not service.registry_accounts.has_account()

        def _developer_authenticated(self) -> bool:
            account = service.ensure_session_app(self._session_token())
            if account is not None:
                return bool(account.get("developer_mode"))
            return not service.registry_accounts.has_account()

        @staticmethod
        def _auth_cookie(session_token: str) -> str:
            return (
                f"{_AUTH_COOKIE}={session_token}; Path=/; HttpOnly; "
                "SameSite=Strict; Max-Age=2592000"
            )

        @staticmethod
        def _expired_auth_cookie() -> str:
            return f"{_AUTH_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"

        def _send_application_page(self, *, auth_mode: str = "") -> None:
            html = (
                _asset_text("index.html")
                .replace("__ELYNDRA_TOKEN__", token)
                .replace("__ELYNDRA_VERSION__", __version__)
            )
            if auth_mode == "register":
                html = html.replace(
                    '<button class="active" id="auth-tab-login" type="button">',
                    '<button id="auth-tab-login" type="button">',
                ).replace(
                    '<button id="auth-tab-register" type="button">',
                    '<button class="active" id="auth-tab-register" type="button">',
                ).replace(
                    '<form class="auth-form" id="login-form">',
                    '<form class="auth-form" id="login-form" hidden>',
                ).replace(
                    '<form class="auth-form" id="register-form" hidden>',
                    '<form class="auth-form" id="register-form">',
                ).replace(
                    '<h1 id="auth-title">Bienvenido a Elyndra</h1>',
                    '<h1 id="auth-title">Crear cuenta local</h1>',
                )
            elif auth_mode == "login":
                html = html.replace(
                    '<h1 id="auth-title">Bienvenido a Elyndra</h1>',
                    '<h1 id="auth-title">Iniciar sesión</h1>',
                )
            else:
                html = html.replace(
                    '<section class="auth-screen" id="auth-screen">',
                    '<section class="auth-screen" id="auth-screen" hidden>',
                )
            self._send_bytes(
                HTTPStatus.OK,
                html.encode("utf-8"),
                "text/html; charset=utf-8",
            )

        def _redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", _content_security_policy())
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Elyndra-Version", __version__)
            self.end_headers()

        def _send_asset(self, name: str, content_type: str) -> None:
            self._send_bytes(
                HTTPStatus.OK,
                _asset_text(name).encode("utf-8"),
                content_type,
            )

        def _send_json(
            self,
            status: HTTPStatus,
            payload: object,
            *,
            cookie: str = "",
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send_bytes(
                status, body, "application/json; charset=utf-8", cookie=cookie
            )

        def _json_error(self, status: HTTPStatus, message: str) -> None:
            self._send_json(status, {"ok": False, "error": message})

        def _send_bytes(
            self,
            status: HTTPStatus,
            body: bytes,
            content_type: str,
            *,
            cookie: str = "",
            download_name: str = "",
        ) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", _content_security_policy())
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("X-Elyndra-Version", __version__)
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                if download_name:
                    safe_name = download_name.replace('"', "")
                    self.send_header(
                        "Content-Disposition", f'attachment; filename="{safe_name}"'
                    )
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


_VALIDATION_WORDS = ("valida", "validar", "validación", "sintaxis", "syntax")
_INLINE_VALIDATION = re.compile(
    r"(?is)\bvalida(?:r)?\s+(?:este|esta)?\s*"
    r"(?P<kind>yml|yaml|json|toml|xml)\b\s*[:\-]?\s*(?P<body>.+)$"
)


def _validation_reply(
    text: str,
    attachments: list[dict[str, Any]],
) -> SkillResult | None:
    folded = text.casefold()
    if not attachments or not any(word in folded for word in _VALIDATION_WORDS):
        return None
    relevant = [item for item in attachments if item["kind"] != "image"]
    if not relevant:
        return None
    lines: list[str] = []
    any_invalid = False
    for item in relevant:
        filename = str(item["filename"])
        status = str(item.get("validation_status", "not_checked"))
        processor = str(item.get("processor", "")) or "validador local"
        diagnostics = item.get("diagnostics") or {}
        messages = [str(value) for value in diagnostics.get("messages", []) if value]
        location = ""
        if diagnostics.get("line"):
            location = f" (línea {diagnostics['line']}"
            if diagnostics.get("column"):
                location += f", columna {diagnostics['column']}"
            location += ")"
        if status == "valid":
            lines.append(
                f"Sí: `{filename}` es válido. Lo comprobé localmente con {processor}."
            )
        elif status == "invalid":
            any_invalid = True
            detail = messages[0] if messages else "El validador detectó un error."
            lines.append(f"No: `{filename}` no es válido{location}. {detail}")
        elif status == "partial":
            detail = messages[0] if messages else "La comprobación fue parcial."
            lines.append(f"`{filename}` se pudo leer, pero la validación fue parcial. {detail}")
        elif status == "unavailable":
            detail = messages[0] if messages else "No hay un validador disponible."
            lines.append(f"Pude leer `{filename}`, pero no validar su sintaxis. {detail}")
        else:
            lines.append(
                f"Pude leer `{filename}`, pero este formato no tiene validación "
                "determinista activa."
            )
    return SkillResult(
        True,
        "\n".join(lines),
        {
            "engine": "document-validator",
            "generated": False,
            "fast_path": "document_validation_invalid" if any_invalid else "document_validation",
        },
    )


def _inline_validation_reply(text: str) -> SkillResult | None:
    match = _INLINE_VALIDATION.search(text.strip())
    if match is None:
        return None
    kind = match.group("kind").casefold()
    extension = ".yml" if kind in {"yml", "yaml"} else f".{kind}"
    body = match.group("body").strip()
    if not body:
        return None
    result = process_document(f"fragmento{extension}", body.encode("utf-8"))
    status = result.validation_status
    diagnostics = result.diagnostics
    messages = [str(value) for value in diagnostics.get("messages", []) if value]
    if status == "valid":
        message = (
            f"Sí: el fragmento {extension.removeprefix('.').upper()} es válido. "
            f"Lo comprobé localmente con {result.processor}."
        )
    elif status == "invalid":
        location = ""
        if diagnostics.get("line"):
            location = f" en la línea {diagnostics['line']}"
            if diagnostics.get("column"):
                location += f", columna {diagnostics['column']}"
        detail = messages[0] if messages else "El validador detectó un error."
        message = f"No: el fragmento no es válido{location}. {detail}"
    elif status == "unavailable":
        detail = messages[0] if messages else "El validador no está disponible."
        message = f"Pude leer el fragmento, pero no validarlo. {detail}"
    else:
        message = "Pude leer el fragmento, pero la validación disponible no es concluyente."
    return SkillResult(
        True,
        message,
        {
            "engine": "document-validator",
            "generated": False,
            "fast_path": f"inline_validation_{status}",
        },
    )


def _safe_limit(value: str, default: int = 100) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(1, min(parsed, 500))


def _alexandria_library_id_from_path(path: str) -> str | None:
    prefix = "/api/alexandria/libraries/"
    if not path.startswith(prefix):
        return None
    remainder = path[len(prefix) :]
    if not remainder or "/" in remainder:
        return None
    return remainder if remainder.startswith("lib_") else None


def _alexandria_action_from_path(path: str) -> tuple[str, str, str] | None:
    prefix = "/api/alexandria/"
    if not path.startswith(prefix):
        return None
    parts = path[len(prefix) :].split("/")
    if len(parts) != 3:
        return None
    resource, identifier, action = parts
    if resource == "libraries" and identifier.startswith("lib_") and action == "sources":
        return resource, identifier, action
    if resource == "sources" and identifier.isdigit() and action == "review":
        return resource, identifier, action
    return None


def _inspector_item_from_path(path: str) -> tuple[str, int] | None:
    prefix = "/api/inspector/"
    if not path.startswith(prefix):
        return None
    parts = path[len(prefix) :].split("/")
    if len(parts) != 2 or parts[0] not in {"memories", "episodes", "proposals"}:
        return None
    try:
        item_id = int(parts[1])
    except ValueError:
        return None
    if item_id <= 0:
        return None
    return parts[0], item_id


def _inspector_action_from_path(path: str) -> tuple[str, int, str] | None:
    prefix = "/api/inspector/"
    if not path.startswith(prefix):
        return None
    parts = path[len(prefix) :].split("/")
    if len(parts) != 3 or parts[0] != "proposals":
        return None
    try:
        item_id = int(parts[1])
    except ValueError:
        return None
    if item_id <= 0 or parts[2] not in {"approve", "reject"}:
        return None
    return parts[0], item_id, parts[2]


def _chat_id_from_path(path: str, *, suffix: str) -> str | None:
    prefix = "/api/chats/"
    if not path.startswith(prefix):
        return None
    remainder = path[len(prefix) :]
    if suffix:
        if not remainder.endswith(suffix):
            return None
        remainder = remainder[: -len(suffix)]
    elif "/" in remainder:
        return None
    return _validated_chat_id(remainder)


def _chat_action_from_path(path: str) -> tuple[str, str] | None:
    prefix = "/api/chats/"
    if not path.startswith(prefix):
        return None
    remainder = path[len(prefix) :]
    parts = remainder.split("/", 1)
    if len(parts) != 2:
        return None
    chat_id = _validated_chat_id(parts[0])
    action = parts[1].strip()
    if chat_id is None or action not in {
        "messages",
        "messages/stream",
        "attachments",
        "pin",
        "archive",
        "restore",
    }:
        return None
    return chat_id, action


def _chat_page_id_from_path(path: str) -> str | None:
    prefix = "/chat/"
    if not path.startswith(prefix):
        return None
    return _validated_chat_id(path[len(prefix) :])


def _export_chat_id_from_path(path: str) -> str | None:
    prefix = "/export/chats/"
    if not path.startswith(prefix):
        return None
    return _validated_chat_id(path[len(prefix) :])


def _attachment_action_from_path(path: str) -> tuple[str, str] | None:
    prefix = "/api/attachments/"
    if not path.startswith(prefix):
        return None
    parts = path.removeprefix(prefix).strip("/").split("/")
    if len(parts) != 2:
        return None
    attachment_id, action = parts
    if not attachment_id.startswith("att_") or action not in {"reprocess"}:
        return None
    return unquote(attachment_id), action


def _attachment_content_id_from_path(path: str) -> str | None:
    prefix = "/api/attachments/"
    suffix = "/content"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    return _validated_attachment_id(path[len(prefix) : -len(suffix)])


def _attachment_id_from_path(path: str) -> str | None:
    prefix = "/api/attachments/"
    if not path.startswith(prefix):
        return None
    remainder = path[len(prefix) :]
    if "/" in remainder:
        return None
    return _validated_attachment_id(remainder)


def _validated_attachment_id(value: str) -> str | None:
    candidate = unquote(value).strip().strip("/")
    if not candidate.startswith("att_"):
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
    if any(char not in allowed for char in candidate):
        return None
    return candidate


def _validated_chat_id(value: str) -> str | None:
    candidate = unquote(value).strip().strip("/")
    if not candidate.startswith("chat_"):
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if not candidate or any(char not in allowed for char in candidate):
        return None
    return candidate


def _asset_text(name: str) -> str:
    return files("elyndra.web.static").joinpath(name).read_text(encoding="utf-8")


def _optional_bool(payload: dict[str, Any], key: str) -> bool | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, bool):
        raise ValueError(f"{key} debe ser booleano.")
    return value


def _require_explicit_web_approval(payload: dict[str, Any]) -> None:
    if payload.get("approved") is not True:
        raise ValueError("La acción web requiere confirmación explícita del propietario.")


def _optional_float(value: object) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("El valor decimal no es válido.") from exc


def _optional_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("El valor numérico no es válido.") from exc


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _title_from_message(text: str) -> str:
    clean = " ".join(text.strip().split())
    for separator in ("?", "!", ".", "\n"):
        if separator in clean:
            clean = clean.split(separator, 1)[0] + separator
            break
    return clean[:72].strip() or "Nuevo chat"


def _content_security_policy() -> str:
    return (
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; connect-src 'self'; img-src 'self' data:; "
        "font-src 'self'; object-src 'none'; script-src 'self'; style-src 'self'"
    )


def _open_browser_safely(url: str) -> None:
    try:
        webbrowser.open(url, new=1, autoraise=True)
    except Exception:
        return
