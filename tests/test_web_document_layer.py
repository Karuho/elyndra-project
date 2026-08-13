from __future__ import annotations

import base64
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


def test_attachment_metadata_distinguishes_extraction_and_validation(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    chat = app.chats.create(title="Documentos", transcript_mode="full")
    service = ElyndraWebService(app)

    item = service.create_attachment(
        str(chat["public_id"]),
        filename="config.json",
        mime_type="application/json",
        data_base64=base64.b64encode(b'{"active": true}').decode("ascii"),
    )

    assert item["extraction_status"] == "extracted"
    assert item["validation_status"] == "valid"
    assert item["processor"] == "python-json"
    assert service.inspector_overview()["counts"]["attachments"] == 1
    assert service.inspector_attachments()[0]["id"] == item["id"]


def test_attachment_can_be_reprocessed_through_local_http_api(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    chat = app.chats.create(title="Reprocesar", transcript_mode="full")
    service = ElyndraWebService(app)
    item = service.create_attachment(
        str(chat["public_id"]),
        filename="datos.toml",
        mime_type="application/toml",
        data_base64=base64.b64encode(b"enabled = true\n").decode("ascii"),
    )
    token = "document-token"
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0),
        _handler_factory(service, token),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    try:
        request = Request(
            f"{base}/api/attachments/{item['id']}/reprocess",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-Elyndra-Token": token,
            },
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            payload = json.load(response)

        assert payload["attachment"]["validation_status"] == "valid"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()


def test_web_assets_include_drag_drop_markdown_and_attachment_inspector() -> None:
    from importlib.resources import files

    static = files("elyndra.web.static")
    index_html = static.joinpath("index.html").read_text(encoding="utf-8")
    app_js = static.joinpath("app.js").read_text(encoding="utf-8")

    assert 'data-view="attachments"' in index_html
    assert 'id="drop-overlay"' in index_html
    assert ".pdf,.docx,.odt,.pptx,.xlsx" in index_html
    assert "renderMarkdown" in app_js
    assert "reprocessAttachment" in app_js
