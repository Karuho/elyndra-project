from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.db import Database
from elyndra.documents import SUPPORTED_EXTENSIONS, process_document
from elyndra.paths import ElyndraPaths

_MAX_FILE_BYTES = 5 * 1024 * 1024
_MAX_TEXT_CHARS = 200_000
_MAX_CONTEXT_CHARS = 6_000
_MAX_ATTACHMENTS_PER_MESSAGE = 5

_BLOCKED_FILENAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
    "secrets.toml",
}
_SECRET_ASSIGNMENT = re.compile(
    r"(?im)^(\s*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|authorization)\s*[:=]\s*)(.+)$"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    flags=re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class AttachmentContent:
    path: Path
    mime_type: str
    filename: str


class AttachmentRepository:
    def __init__(self, database: Database, paths: ElyndraPaths) -> None:
        self.database = database
        self.paths = paths

    def create(
        self,
        chat_identifier: str,
        *,
        filename: str,
        mime_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        clean_filename = _clean_filename(filename)
        if not data:
            raise ValueError("El archivo adjunto está vacío.")
        if len(data) > _MAX_FILE_BYTES:
            raise ValueError("El archivo supera el límite local de 5 MiB.")

        analysis = process_document(
            clean_filename,
            data,
            supplied_mime=mime_type,
        )
        kind = analysis.kind
        detected_mime = analysis.mime_type
        extracted_text, redacted = _redact_secrets(
            analysis.extracted_text[:_MAX_TEXT_CHARS]
        )

        now = _now()
        public_id = f"att_{secrets.token_hex(6)}"
        digest = hashlib.sha256(data).hexdigest()
        with self.database.connect() as connection:
            chat = _chat_row(connection, chat_identifier)
            chat_id = int(chat["id"])
            directory = self.paths.attachments_dir / str(chat["public_id"])
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            stored_name = f"{public_id}_{clean_filename}"
            path = directory / stored_name
            path.write_bytes(data)
            with suppress(PermissionError):
                path.chmod(0o600)
            connection.execute(
                """
                INSERT INTO chat_attachments(
                    public_id, chat_id, turn_index, filename, stored_path,
                    mime_type, kind, sha256, size_bytes, extracted_text,
                    secrets_redacted, extraction_status, validation_status,
                    processor, diagnostics_json, status, created_at, updated_at
                ) VALUES (
                    ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?
                )
                """,
                (
                    public_id,
                    chat_id,
                    clean_filename,
                    str(path),
                    detected_mime,
                    kind,
                    digest,
                    len(data),
                    extracted_text,
                    1 if redacted else 0,
                    analysis.extraction_status,
                    analysis.validation_status,
                    analysis.processor,
                    json.dumps(analysis.diagnostics, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        result = self.get(public_id)
        assert result is not None
        return self._public(result)

    def get(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT a.*, c.public_id AS chat_public_id
                FROM chat_attachments a
                JOIN chats c ON c.id = a.chat_id
                WHERE a.public_id = ? AND a.status != 'deleted'
                  AND c.status != 'deleted'
                """,
                (str(public_id).strip(),),
            ).fetchone()
        return dict(row) if row else None

    def list_for_chat(self, chat_identifier: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            chat = _chat_row(connection, chat_identifier, include_archived=True)
            rows = connection.execute(
                """
                SELECT * FROM chat_attachments
                WHERE chat_id = ? AND status != 'deleted'
                ORDER BY id ASC
                """,
                (int(chat["id"]),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_all(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*, c.public_id AS chat_public_id, c.title AS chat_title
                FROM chat_attachments a
                JOIN chats c ON c.id = a.chat_id
                WHERE a.status != 'deleted' AND c.status != 'deleted'
                ORDER BY a.id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._public(dict(row)) for row in rows]

    def reprocess(self, public_id: str) -> dict[str, Any]:
        item = self.get(public_id)
        if item is None:
            raise ValueError(f"Adjunto no encontrado: {public_id}")
        content = self.content(public_id)
        data = content.path.read_bytes()
        analysis = process_document(
            str(item["filename"]),
            data,
            supplied_mime=str(item["mime_type"]),
        )
        extracted_text, redacted = _redact_secrets(
            analysis.extracted_text[:_MAX_TEXT_CHARS]
        )
        now = _now()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE chat_attachments
                SET mime_type = ?, kind = ?, extracted_text = ?,
                    secrets_redacted = ?, extraction_status = ?,
                    validation_status = ?, processor = ?, diagnostics_json = ?,
                    updated_at = ?
                WHERE public_id = ? AND status != 'deleted'
                """,
                (
                    analysis.mime_type,
                    analysis.kind,
                    extracted_text,
                    1 if redacted else 0,
                    analysis.extraction_status,
                    analysis.validation_status,
                    analysis.processor,
                    json.dumps(analysis.diagnostics, ensure_ascii=False),
                    now,
                    public_id,
                ),
            )
        refreshed = self.get(public_id)
        assert refreshed is not None
        return self._public(refreshed)

    def content(self, public_id: str) -> AttachmentContent:
        item = self.get(public_id)
        if item is None:
            raise ValueError(f"Adjunto no encontrado: {public_id}")
        path = Path(str(item["stored_path"]))
        root = self.paths.attachments_dir.resolve()
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError("El archivo adjunto ya no existe en disco.") from exc
        if not resolved.is_relative_to(root):
            raise ValueError("La ruta del adjunto está fuera del almacén autorizado.")
        return AttachmentContent(
            path=resolved,
            mime_type=str(item["mime_type"]),
            filename=str(item["filename"]),
        )

    def delete(self, public_id: str, *, pending_only: bool = False) -> bool:
        item = self.get(public_id)
        if item is None:
            return False
        if pending_only and str(item["status"]) != "pending":
            raise ValueError("Solo se puede retirar un adjunto pendiente desde el compositor.")
        path = Path(str(item["stored_path"]))
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ValueError(f"No se pudo borrar el adjunto {item['filename']}: {exc}") from exc
        now = _now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE chat_attachments
                SET status = 'deleted', updated_at = ?
                WHERE public_id = ? AND status != 'deleted'
                """,
                (now, public_id),
            )
        return cursor.rowcount > 0

    def bind_to_turn(
        self,
        chat_identifier: str,
        attachment_ids: list[str],
        *,
        turn_index: int,
    ) -> list[dict[str, Any]]:
        unique_ids = _validated_ids(attachment_ids)
        if len(unique_ids) > _MAX_ATTACHMENTS_PER_MESSAGE:
            raise ValueError("Cada mensaje admite como máximo 5 adjuntos.")
        if not unique_ids:
            return []
        now = _now()
        with self.database.connect() as connection:
            chat = _chat_row(connection, chat_identifier)
            placeholders = ",".join("?" for _ in unique_ids)
            rows = connection.execute(
                f"""
                SELECT * FROM chat_attachments
                WHERE public_id IN ({placeholders})
                  AND chat_id = ? AND status = 'pending'
                """,
                (*unique_ids, int(chat["id"])),
            ).fetchall()
            if len(rows) != len(unique_ids):
                raise ValueError("Uno o más adjuntos no pertenecen al chat o ya fueron usados.")
            connection.execute(
                f"""
                UPDATE chat_attachments
                SET turn_index = ?, status = 'active', updated_at = ?
                WHERE public_id IN ({placeholders}) AND chat_id = ?
                """,
                (turn_index, now, *unique_ids, int(chat["id"])),
            )
        return [
            self._public(
                dict(row) | {"turn_index": turn_index, "status": "active"}
            )
            for row in rows
        ]

    def image_payloads(
        self,
        chat_identifier: str,
        attachment_ids: list[str],
    ) -> tuple[str, ...]:
        unique_ids = _validated_ids(attachment_ids)
        if not unique_ids:
            return ()
        with self.database.connect() as connection:
            chat = _chat_row(connection, chat_identifier)
            placeholders = ",".join("?" for _ in unique_ids)
            rows = connection.execute(
                f"""
                SELECT public_id, stored_path, kind FROM chat_attachments
                WHERE public_id IN ({placeholders})
                  AND chat_id = ? AND status = 'pending'
                ORDER BY id ASC
                """,
                (*unique_ids, int(chat["id"])),
            ).fetchall()
        payloads: list[str] = []
        for row in rows:
            if str(row["kind"]) != "image":
                continue
            path = Path(str(row["stored_path"]))
            root = self.paths.attachments_dir.resolve()
            try:
                resolved = path.resolve(strict=True)
            except FileNotFoundError as exc:
                raise ValueError("Una imagen adjunta ya no existe en disco.") from exc
            if not resolved.is_relative_to(root):
                raise ValueError("La ruta de una imagen está fuera del almacén autorizado.")
            payloads.append(base64.b64encode(resolved.read_bytes()).decode("ascii"))
        return tuple(payloads)

    def context_blocks(
        self,
        chat_identifier: str,
        attachment_ids: list[str],
    ) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
        unique_ids = _validated_ids(attachment_ids)
        if len(unique_ids) > _MAX_ATTACHMENTS_PER_MESSAGE:
            raise ValueError("Cada mensaje admite como máximo 5 adjuntos.")
        if not unique_ids:
            return (), []
        with self.database.connect() as connection:
            chat = _chat_row(connection, chat_identifier)
            placeholders = ",".join("?" for _ in unique_ids)
            rows = connection.execute(
                f"""
                SELECT * FROM chat_attachments
                WHERE public_id IN ({placeholders})
                  AND chat_id = ? AND status = 'pending'
                ORDER BY id ASC
                """,
                (*unique_ids, int(chat["id"])),
            ).fetchall()
        if len(rows) != len(unique_ids):
            raise ValueError("Uno o más adjuntos no están disponibles para este mensaje.")

        items = [dict(row) for row in rows]
        public = [self._public(item) for item in items]
        remaining = _MAX_CONTEXT_CHARS
        blocks: list[str] = []
        for item in items:
            filename = str(item["filename"])
            if str(item["kind"]) == "image":
                block = (
                    f"IMAGEN ADJUNTA: {filename} ({item['mime_type']}, "
                    f"{item['size_bytes']} bytes). El motor actual puede no tener visión; "
                    "no describas contenido visual que no hayas recibido realmente."
                )
            else:
                content = str(item.get("extracted_text", ""))
                validation = str(item.get("validation_status", "not_checked"))
                extraction = str(item.get("extraction_status", "not_checked"))
                processor = str(item.get("processor", ""))
                diagnostics = _diagnostics(item.get("diagnostics_json", "{}"))
                messages = diagnostics.get("messages", [])
                diagnostic = " ".join(str(value) for value in messages[:2])[:500]
                header = (
                    f"ARCHIVO ADJUNTO: {filename} ({item['mime_type']}) "
                    f"[extracción={extraction}; validación={validation}; "
                    f"procesador={processor or 'ninguno'}]"
                )
                if diagnostic:
                    header += f" [diagnóstico determinista: {diagnostic}]"
                if bool(item.get("secrets_redacted", 0)):
                    header += " [secretos potenciales redactados antes del modelo]"
                allowance = max(0, min(3_200, remaining - len(header) - 2))
                body = content[:allowance]
                block = f"{header}\n{body}" if body else header
            if len(block) > remaining:
                block = block[:remaining]
            if block:
                blocks.append(block)
                remaining -= len(block)
            if remaining <= 0:
                break
        return tuple(blocks), public

    @staticmethod
    def _public(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(item["public_id"]),
            "filename": str(item["filename"]),
            "mime_type": str(item["mime_type"]),
            "kind": str(item["kind"]),
            "size_bytes": int(item["size_bytes"]),
            "sha256": str(item["sha256"]),
            "turn_index": item.get("turn_index"),
            "status": str(item["status"]),
            "secrets_redacted": bool(item.get("secrets_redacted", 0)),
            "extraction_status": str(item.get("extraction_status", "not_checked")),
            "validation_status": str(item.get("validation_status", "not_checked")),
            "processor": str(item.get("processor", "")),
            "diagnostics": _diagnostics(item.get("diagnostics_json", "{}")),
            "chat_id": item.get("chat_public_id"),
            "chat_title": item.get("chat_title"),
            "content_url": f"/api/attachments/{item['public_id']}/content",
        }


def _chat_row(connection, identifier: str, *, include_archived: bool = False):
    status_clause = "c.status != 'deleted'" if include_archived else "c.status = 'active'"
    row = connection.execute(
        f"SELECT c.* FROM chats c WHERE c.public_id = ? AND {status_clause}",
        (identifier.strip(),),
    ).fetchone()
    if row is None:
        raise ValueError(f"Chat no encontrado: {identifier}")
    return row


def _validated_ids(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        clean = str(value).strip()
        if not clean.startswith("att_") or not clean.replace("_", "").isalnum():
            raise ValueError("Identificador de adjunto inválido.")
        if clean not in unique:
            unique.append(clean)
    return unique


def _clean_filename(value: str) -> str:
    clean = Path(value.strip()).name
    clean = re.sub(r"[^\w.()\- +]", "_", clean, flags=re.UNICODE)
    clean = " ".join(clean.split())[:120]
    if not clean or clean in {".", ".."}:
        raise ValueError("El archivo necesita un nombre válido.")
    if clean.casefold() in _BLOCKED_FILENAMES or clean.startswith("."):
        raise ValueError("Ese archivo sensible u oculto no puede adjuntarse.")
    return clean


def _diagnostics(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _redact_secrets(text: str) -> tuple[str, bool]:
    redacted = _PRIVATE_KEY_BLOCK.sub("[CLAVE PRIVADA REDACTADA]", text)
    redacted = _SECRET_ASSIGNMENT.sub(r"\1[REDACTADO]", redacted)
    return redacted, redacted != text


def _now() -> str:
    return datetime.now(UTC).isoformat()


def supported_extensions() -> tuple[str, ...]:
    return tuple(sorted(SUPPORTED_EXTENSIONS))
