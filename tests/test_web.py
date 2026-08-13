from __future__ import annotations

import json
import threading
from http import HTTPStatus
from importlib.resources import files
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths
from elyndra.web.server import (
    ElyndraWebService,
    _handler_factory,
    _LocalThreadingHTTPServer,
)


def test_web_assets_are_packaged() -> None:
    static = files("elyndra.web.static")

    assert "███████╗" in static.joinpath("index.html").read_text(encoding="utf-8")
    assert ".app-shell" in static.joinpath("app.css").read_text(encoding="utf-8")
    app_js = static.joinpath("app.js").read_text(encoding="utf-8")
    assert "Elyn está formulando" in app_js
    assert "attachment_ids" in app_js
    assert "openMemoryInspector" in app_js
    index_html = static.joinpath("index.html").read_text(encoding="utf-8")
    assert "El cerebro visible de Elyndra" in index_html
    assert "window.print()" in static.joinpath("print.js").read_text(encoding="utf-8")


def test_web_service_creates_chat_and_records_canonical_answer(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    detail = service.create_chat(title="Web local", transcript_mode="full")
    chat_id = detail["chat"]["id"]
    response = service.send_message(chat_id, "¿Qué es Elyndra?")
    reopened = service.chat_detail(chat_id)

    assert response["ok"] is True
    assert response["meta"]["fast_path"] == "identity"
    assert response["elapsed_ms"] >= 0
    assert response["chat"]["turn_count"] == 1
    assert reopened["turns"][0]["user_text"] == "¿Qué es Elyndra?"
    assert "marco local-first" in reopened["turns"][0]["assistant_text"]


def test_web_service_searches_history(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    chat = service.create_chat(title="Memoria episódica", transcript_mode="summary")

    service.send_message(chat["chat"]["id"], "¿Qué es Elyndra?")
    matches = service.list_chats(query="episódica")

    assert len(matches) == 1
    assert matches[0]["title"] == "Memoria episódica"


def test_local_http_api_requires_token_for_writes(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    token = "test-local-token"
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0),
        _handler_factory(service, token),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    try:
        with urlopen(f"{base}/api/bootstrap", timeout=3) as response:
            bootstrap = json.load(response)
        assert bootstrap["offline"] is True

        request = Request(
            f"{base}/api/chats",
            data=b'{"transcript_mode":"full"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(request, timeout=3)
        except HTTPError as exc:
            assert exc.code == HTTPStatus.FORBIDDEN
        else:
            raise AssertionError("La escritura sin token debió ser rechazada.")

        authorized = Request(
            f"{base}/api/chats",
            data=b'{"title":"Prueba web","transcript_mode":"full"}',
            headers={
                "Content-Type": "application/json",
                "X-Elyndra-Token": token,
            },
            method="POST",
        )
        with urlopen(authorized, timeout=3) as response:
            payload = json.load(response)
        assert response.status == HTTPStatus.CREATED
        assert payload["chat"]["title"] == "Prueba web"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()


def test_index_contains_security_headers(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0),
        _handler_factory(service, "token"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=3) as response:
            body = response.read().decode("utf-8")
            headers = response.headers
        assert "Elyndra" in body
        assert headers["X-Frame-Options"] == "DENY"
        assert "default-src 'self'" in headers["Content-Security-Policy"]
        assert headers["Cache-Control"] == "no-store"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()
