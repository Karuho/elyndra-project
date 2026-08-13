from __future__ import annotations

import base64
import json
import threading
from dataclasses import dataclass, field
from urllib.request import Request, urlopen

import pytest

from elyndra.application import ElyndraApplication
from elyndra.engines import ConversationTurn, LanguageReply
from elyndra.paths import ElyndraPaths
from elyndra.web.server import (
    ElyndraWebService,
    _handler_factory,
    _LocalThreadingHTTPServer,
)


@dataclass(slots=True)
class _AttachmentCapturingEngine:
    name: str = "capture"
    supports_vision: bool = False
    contexts: list[tuple[str, ...]] = field(default_factory=list)
    images: list[tuple[str, ...]] = field(default_factory=list)

    def reply(
        self,
        prompt: str,
        *,
        context: tuple[str, ...] = (),
        history: tuple[ConversationTurn, ...] = (),
        response_language: str | None = None,
        keep_alive_seconds: int = 0,
        images: tuple[str, ...] = (),
    ) -> LanguageReply:
        del prompt, history, response_language, keep_alive_seconds
        self.contexts.append(context)
        self.images.append(images)
        return LanguageReply("Archivo analizado.", self.name, True)

    def release(self) -> None:
        return None


def test_text_attachment_is_redacted_bound_and_reopened(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = _AttachmentCapturingEngine()
    app.language_engine = engine
    service = ElyndraWebService(app)
    detail = service.create_chat(title="Adjuntos", transcript_mode="full")
    chat_id = detail["chat"]["id"]
    raw = b"name = 'Elyndra'\napi_key = super-secret-value\n"

    attachment = service.create_attachment(
        chat_id,
        filename="config.py",
        mime_type="text/x-python",
        data_base64=base64.b64encode(raw).decode("ascii"),
    )

    assert attachment["kind"] == "text"
    assert attachment["secrets_redacted"] is True

    response = service.send_message(
        chat_id,
        "Resume este archivo.",
        attachment_ids=[attachment["id"]],
    )

    assert response["attachments"][0]["filename"] == "config.py"
    rendered_context = "\n".join(engine.contexts[0])
    assert "super-secret-value" not in rendered_context
    assert "[REDACTADO]" in rendered_context

    reopened = service.chat_detail(chat_id)
    assert reopened["turns"][0]["attachments"][0]["id"] == attachment["id"]
    assert reopened["pending_attachments"] == []


def test_image_attachment_is_kept_but_not_falsely_analyzed_without_vision(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = _AttachmentCapturingEngine()
    service = ElyndraWebService(app)
    detail = service.create_chat(title="Imagen", transcript_mode="full")
    chat_id = detail["chat"]["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"local-image"
    attachment = service.create_attachment(
        chat_id,
        filename="captura.png",
        mime_type="image/png",
        data_base64=base64.b64encode(png).decode("ascii"),
    )

    response = service.send_message(
        chat_id,
        "¿Qué aparece aquí?",
        attachment_ids=[attachment["id"]],
    )

    assert response["meta"]["fast_path"] == "vision_unavailable"
    assert "no tiene capacidad visual" in response["message"]
    assert service.chat_detail(chat_id)["turns"][0]["attachments"][0]["kind"] == "image"


def test_image_is_sent_only_to_a_vision_capable_engine(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = _AttachmentCapturingEngine(supports_vision=True)
    app.language_engine = engine
    service = ElyndraWebService(app)
    detail = service.create_chat(title="Visión", transcript_mode="full")
    chat_id = detail["chat"]["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"local-vision-image"
    attachment = service.create_attachment(
        chat_id,
        filename="vision.png",
        mime_type="image/png",
        data_base64=base64.b64encode(png).decode("ascii"),
    )

    response = service.send_message(
        chat_id,
        "Describe la imagen.",
        attachment_ids=[attachment["id"]],
    )

    assert response["message"] == "Archivo analizado."
    assert engine.images and engine.images[0]
    assert base64.b64decode(engine.images[0][0]).startswith(b"\x89PNG")


def test_pending_attachment_can_be_removed(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    chat_id = service.create_chat(title="Pendiente")["chat"]["id"]
    attachment = service.create_attachment(
        chat_id,
        filename="nota.txt",
        mime_type="text/plain",
        data_base64=base64.b64encode(b"contenido").decode("ascii"),
    )

    assert service.delete_pending_attachment(attachment["id"]) is True
    assert app.attachments.get(attachment["id"]) is None


def test_hidden_and_unsupported_files_are_rejected(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    chat_id = service.create_chat(title="Tipos")["chat"]["id"]

    with pytest.raises(ValueError, match="sensible u oculto"):
        service.create_attachment(
            chat_id,
            filename=".env",
            mime_type="text/plain",
            data_base64=base64.b64encode(b"TOKEN=x").decode("ascii"),
        )

    with pytest.raises(ValueError, match="Tipo de archivo"):
        service.create_attachment(
            chat_id,
            filename="programa.exe",
            mime_type="application/octet-stream",
            data_base64=base64.b64encode(b"MZ").decode("ascii"),
        )


def test_only_five_chats_can_be_pinned(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    chat_ids = [
        service.create_chat(title=f"Chat {index}")["chat"]["id"]
        for index in range(6)
    ]

    for chat_id in chat_ids[:5]:
        service.set_pinned(chat_id, True)

    with pytest.raises(ValueError, match="máximo 5"):
        service.set_pinned(chat_ids[5], True)

    assert len(service.list_chats(status="pinned")) == 5


def test_http_attachment_upload_and_local_content(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    chat_id = service.create_chat(title="HTTP adjunto")["chat"]["id"]
    token = "attachment-token"
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0),
        _handler_factory(service, token),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    try:
        body = json.dumps(
            {
                "filename": "ejemplo.txt",
                "mime_type": "text/plain",
                "data_base64": base64.b64encode(b"contenido local").decode("ascii"),
            }
        ).encode("utf-8")
        request = Request(
            f"{base}/api/chats/{chat_id}/attachments",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Elyndra-Token": token,
            },
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            payload = json.load(response)
        attachment_id = payload["attachment"]["id"]

        with urlopen(
            f"{base}/api/attachments/{attachment_id}/content", timeout=3
        ) as response:
            content = response.read()
        assert content == b"contenido local"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()


def test_printable_chat_includes_attachment_metadata_and_image(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = _AttachmentCapturingEngine(supports_vision=True)
    service = ElyndraWebService(app)
    chat_id = service.create_chat(title="Exportar adjuntos", transcript_mode="full")[
        "chat"
    ]["id"]
    text_attachment = service.create_attachment(
        chat_id,
        filename="ejemplo.php",
        mime_type="text/x-php",
        data_base64=base64.b64encode(b"<?php echo 'Elyndra';").decode("ascii"),
    )
    image_attachment = service.create_attachment(
        chat_id,
        filename="captura.png",
        mime_type="image/png",
        data_base64=base64.b64encode(
            b"\x89PNG\r\n\x1a\n" + b"print-image"
        ).decode("ascii"),
    )
    service.send_message(
        chat_id,
        "Revisa los adjuntos.",
        attachment_ids=[text_attachment["id"], image_attachment["id"]],
    )

    page = service.printable_chat(chat_id)

    assert "Adjunto: ejemplo.php" in page
    assert "captura.png" in page
    assert image_attachment["content_url"] in page
    assert "/assets/print.js" in page
