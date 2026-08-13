from __future__ import annotations

import base64

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService


def test_web_alexandria_create_import_search_and_review(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    library = service.create_alexandria_library(
        {
            "name": "Minecraft",
            "description": "Configuración local del servidor",
            "domain": "gaming",
            "language": "es",
            "version": "1",
            "license_id": "owner-content",
        }
    )
    source = service.import_alexandria_source(
        library["public_id"],
        filename="towny.yml",
        data_base64=base64.b64encode(
            b"towns:\n  upkeep: 100\n  mayor: required\n"
        ).decode("ascii"),
    )

    detail = service.alexandria_library(library["public_id"])
    matches = service.search_alexandria("upkeep")

    assert detail["library"]["source_count"] == 1
    assert detail["sources"][0]["validation_status"] == "valid"
    assert matches[0]["library_name"] == "Minecraft"
    reviewed = service.review_alexandria_source(int(source["id"]))
    assert reviewed["reviewed_units"] == reviewed["unit_count"]

    updated = service.update_alexandria_library(
        library["public_id"],
        {"name": "Minecraft avanzado", "version": "2"},
    )
    deleted = service.delete_alexandria_library(library["public_id"])

    assert updated["name"] == "Minecraft avanzado"
    assert updated["version"] == "2"
    assert deleted["removed_sources"] == 1
    assert service.alexandria_libraries() == []


def test_document_validation_fast_paths_are_concise(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    chat = service.create_chat(title="Validación", transcript_mode="full")
    chat_id = chat["chat"]["id"]
    attachment = service.create_attachment(
        chat_id,
        filename="items.yml",
        mime_type="application/yaml",
        data_base64=base64.b64encode(
            b"items:\n  sword:\n    material: DIAMOND_SWORD\n    damage: 15\n"
        ).decode("ascii"),
    )

    response = service.send_message(
        chat_id,
        "Valida la sintaxis de este archivo.",
        attachment_ids=[attachment["id"]],
    )
    invalid = service.send_message(chat_id, "valida este yml items: [sword")

    assert response["meta"]["engine"] == "document-validator"
    assert "es válido" in response["message"]
    assert len(response["message"]) < 180
    assert invalid["meta"]["fast_path"] == "inline_validation_invalid"
    assert "no es válido" in invalid["message"].casefold()


def test_web_assets_include_alexandria_and_code_rendering() -> None:
    from importlib.resources import files

    static = files("elyndra.web.static")
    index = static.joinpath("index.html").read_text(encoding="utf-8")
    app_js = static.joinpath("app.js").read_text(encoding="utf-8")
    app_css = static.joinpath("app.css").read_text(encoding="utf-8")

    assert 'id="open-alexandria"' in index
    assert 'id="alexandria"' in index
    assert "openAlexandria" in app_js
    assert "Editar biblioteca" in app_js
    assert "Eliminar biblioteca" in app_js
    assert "renderUserText" in app_js
    assert ".user-code-block" in app_css


def test_alexandria_page_and_http_api_are_local(
    isolated_home: ElyndraPaths,
) -> None:
    import json
    import threading
    from http import HTTPStatus
    from urllib.request import Request, urlopen

    from elyndra.web.server import _handler_factory, _LocalThreadingHTTPServer

    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    token = "alexandria-local-token"
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0),
        _handler_factory(service, token),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    try:
        with urlopen(f"{base}/alexandria", timeout=3) as response:
            page = response.read().decode("utf-8")
        request = Request(
            f"{base}/api/alexandria/libraries",
            data=json.dumps({"name": "Oracle"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Elyndra-Token": token,
            },
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            payload = json.load(response)

        library_id = payload["library"]["public_id"]
        update_request = Request(
            f"{base}/api/alexandria/libraries/{library_id}",
            data=json.dumps({"name": "Oracle local", "version": "19c"}).encode(
                "utf-8"
            ),
            headers={
                "Content-Type": "application/json",
                "X-Elyndra-Token": token,
            },
            method="PATCH",
        )
        with urlopen(update_request, timeout=3) as update_response:
            updated = json.load(update_response)

        delete_request = Request(
            f"{base}/api/alexandria/libraries/{library_id}",
            headers={"X-Elyndra-Token": token},
            method="DELETE",
        )
        with urlopen(delete_request, timeout=3) as delete_response:
            deleted = json.load(delete_response)

        assert "Alejandría" in page
        assert response.status == HTTPStatus.CREATED
        assert payload["library"]["name"] == "Oracle"
        assert updated["library"]["name"] == "Oracle local"
        assert deleted["deleted"]["public_id"] == library_id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()
