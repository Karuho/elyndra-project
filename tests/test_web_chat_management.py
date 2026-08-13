from __future__ import annotations

import json
import threading
from urllib.request import Request, urlopen

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths
from elyndra.web.server import (
    ElyndraWebService,
    _handler_factory,
    _LocalThreadingHTTPServer,
)


def test_web_chat_uses_default_title_and_renames_from_first_message(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    detail = service.create_chat(transcript_mode="full")
    chat_id = detail["chat"]["id"]

    assert detail["chat"]["title"] == "Nuevo chat"

    response = service.send_message(chat_id, "¿Cómo funciona la memoria episódica?")

    assert response["chat"]["title"] == "¿Cómo funciona la memoria episódica?"


def test_web_chat_can_be_pinned_archived_restored_and_deleted(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    detail = service.create_chat(title="Administración web", transcript_mode="full")
    chat_id = detail["chat"]["id"]

    pinned = service.set_pinned(chat_id, True)
    assert pinned["pinned"] is True
    assert service.list_chats(status="pinned")[0]["id"] == chat_id
    assert service.list_chats() == []

    archived = service.archive_chat(chat_id)
    assert archived["status"] == "archived"
    assert service.list_chats() == []
    assert service.list_chats(status="archived")[0]["id"] == chat_id

    restored = service.restore_chat(chat_id)
    assert restored["status"] == "active"

    deleted = service.delete_chat_permanently(chat_id)
    assert deleted["id"] == chat_id
    assert app.chats.get_any(chat_id) is None


def test_printable_chat_contains_local_transcript(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    detail = service.create_chat(title="Exportación local", transcript_mode="full")
    chat_id = detail["chat"]["id"]
    service.send_message(chat_id, "¿Qué es Elyndra?")

    exported = service.printable_chat(chat_id)

    assert "Exportación local" in exported
    assert "¿Qué es Elyndra?" in exported
    assert "Exportado localmente desde Elyndra" in exported
    assert "/assets/print.css" in exported
    assert "/assets/print.js" in exported


def test_http_chat_route_export_and_permanent_delete(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    detail = service.create_chat(title="Ruta estable", transcript_mode="full")
    chat_id = detail["chat"]["id"]
    token = "management-token"
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0),
        _handler_factory(service, token),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    try:
        with urlopen(f"{base}/chat/{chat_id}", timeout=3) as response:
            page = response.read().decode("utf-8")
        assert "ascii-logo" in page

        with urlopen(f"{base}/export/chats/{chat_id}", timeout=3) as response:
            exported = response.read().decode("utf-8")
        assert "Ruta estable" in exported

        request = Request(
            f"{base}/api/chats/{chat_id}",
            headers={"X-Elyndra-Token": token},
            method="DELETE",
        )
        with urlopen(request, timeout=3) as response:
            payload = json.load(response)
        assert payload["deleted"]["id"] == chat_id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()
