from __future__ import annotations

import json
import threading
from http import HTTPStatus
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths
from elyndra.web.server import (
    ElyndraWebService,
    _handler_factory,
    _LocalThreadingHTTPServer,
)


def _seed_memory_state(app: ElyndraApplication) -> tuple[str, int]:
    chat = app.chats.create(title="Inspector local", transcript_mode="full")
    chat_id = str(chat["public_id"])
    memory_id = app.memories.add(
        "Carlos prefiere una interfaz sobria.",
        kind="preference",
    )
    app.record_chat_turn(
        chat_id,
        user_text="Decidimos conservar los resúmenes en SQLite.",
        assistant_text="Decisión registrada.",
    )
    app.record_chat_turn(
        chat_id,
        user_text="Prefiero usar VS Code para modificar código.",
        assistant_text="Preferencia pendiente de revisión.",
    )
    return chat_id, memory_id


def test_memory_inspector_overview_counts_local_state(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _seed_memory_state(app)
    service = ElyndraWebService(app)

    overview = service.inspector_overview()

    assert overview["counts"]["memories"] == 1
    assert overview["counts"]["episodes"] >= 1
    assert overview["counts"]["proposals"] == 1
    assert overview["database"]["path"].endswith("elyndra.db")
    assert overview["database"]["size_bytes"] > 0


def test_memory_inspector_edits_and_forgets_semantic_memory(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _, memory_id = _seed_memory_state(app)
    service = ElyndraWebService(app)

    updated = service.update_memory(
        memory_id,
        content="Carlos prefiere interfaces sobrias y legibles.",
        kind="preference",
    )

    assert updated["content"] == "Carlos prefiere interfaces sobrias y legibles."
    assert service.forget_memory(memory_id) is True
    assert service.inspector_memories() == []


def test_memory_inspector_reviews_proposal_and_creates_memory(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _seed_memory_state(app)
    service = ElyndraWebService(app)
    proposal = service.inspector_proposals()[0]

    reviewed = service.approve_proposal(int(proposal["id"]))

    assert reviewed["status"] == "approved"
    assert reviewed["memory_id"] is not None
    assert any("VS Code" in item["content"] for item in service.inspector_memories())
    assert service.inspector_overview()["counts"]["proposals"] == 0


def test_memory_inspector_lists_episodes_corrections_and_audit(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    chat_id, _ = _seed_memory_state(app)
    app.memory_lifecycle.add_correction(
        chat_id,
        user_text="¿Dónde vive la memoria?",
        original_response="En RAM.",
        corrected_response="La memoria duradera vive en SQLite.",
    )
    service = ElyndraWebService(app)

    episodes = service.inspector_episodes()
    corrections = service.inspector_corrections()
    audit = service.inspector_audit()

    assert any(item["kind"] == "decision" for item in episodes)
    assert corrections[0]["corrected_response"] == "La memoria duradera vive en SQLite."
    assert isinstance(audit, list)


def test_memory_page_and_read_api_are_available_locally(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _seed_memory_state(app)
    service = ElyndraWebService(app)
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0),
        _handler_factory(service, "memory-token"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    try:
        with urlopen(f"{base}/memory", timeout=3) as response:
            page = response.read().decode("utf-8")
        with urlopen(f"{base}/api/inspector/overview", timeout=3) as response:
            overview = json.load(response)

        assert "El cerebro visible de Elyndra" in page
        assert overview["counts"]["proposals"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()


def test_memory_http_mutations_require_token_and_approve_proposal(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _seed_memory_state(app)
    service = ElyndraWebService(app)
    proposal_id = int(service.inspector_proposals()[0]["id"])
    token = "memory-token"
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0),
        _handler_factory(service, token),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    try:
        unauthorized = Request(
            f"{base}/api/inspector/proposals/{proposal_id}/approve",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(unauthorized, timeout=3)
        except HTTPError as exc:
            assert exc.code == HTTPStatus.FORBIDDEN
        else:
            raise AssertionError("La aprobación sin token debió ser rechazada.")

        authorized = Request(
            f"{base}/api/inspector/proposals/{proposal_id}/approve",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-Elyndra-Token": token,
            },
            method="POST",
        )
        with urlopen(authorized, timeout=3) as response:
            payload = json.load(response)

        assert payload["item"]["status"] == "approved"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()
