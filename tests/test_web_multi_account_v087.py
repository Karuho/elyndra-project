from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from elyndra import __version__
from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths
from elyndra.web.server import (
    ElyndraWebService,
    _handler_factory,
    _LocalThreadingHTTPServer,
)


def _adult_birth() -> str:
    today = date.today()
    return date(today.year - 30, today.month, min(today.day, 28)).isoformat()


def _register(app: ElyndraApplication, username: str) -> dict:
    return app.registry_accounts.register(
        username=username,
        email=f"{username}@example.test",
        password="clave9!segura",
        password_confirmation="clave9!segura",
        birth_date=_adult_birth(),
        system_user=app.identity.system_user,
        preferred_name=username.title(),
    )


def test_accounts_receive_isolated_sqlite_vaults(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    first = _register(app, "primera")
    second = _register(app, "segunda")

    first_app = ElyndraApplication.load_for_account(first["public_id"], isolated_home)
    second_app = ElyndraApplication.load_for_account(second["public_id"], isolated_home)
    first_app.chats.create(title="Privado de primera")
    first_app.memories.add("Solo primera puede leer esto", kind="preference")

    assert first_app.paths.database_file != second_app.paths.database_file
    assert first_app.paths.database_file.is_file()
    assert second_app.paths.database_file.is_file()
    assert [item["title"] for item in first_app.chats.list_active()] == [
        "Privado de primera"
    ]
    assert second_app.chats.list_active() == []
    assert second_app.memories.list_active() == []


def test_first_account_preserves_legacy_personal_data(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.chats.create(title="Chat anterior al registro")
    account = _register(app, "propietaria")

    scoped = ElyndraApplication.load_for_account(account["public_id"], isolated_home)

    assert [item["title"] for item in scoped.chats.list_active()] == [
        "Chat anterior al registro"
    ]
    with app.registry_accounts.database.connect() as connection:
        vault = connection.execute(
            "SELECT * FROM account_vaults WHERE account_id = 1"
        ).fetchone()
    with scoped.database.connect() as connection:
        copied_credentials = connection.execute(
            "SELECT COUNT(*) FROM local_accounts"
        ).fetchone()[0]
    assert vault is not None
    assert int(vault["migrated_from_legacy"]) == 1
    assert copied_credentials == 0


def test_web_login_switches_account_and_revokes_previous_web_session(
    isolated_home: ElyndraPaths,
) -> None:
    service = ElyndraWebService(ElyndraApplication.load(isolated_home))
    payload = {
        "password": "clave9!segura",
        "password_confirmation": "clave9!segura",
        "birth_date": _adult_birth(),
        "approved": True,
    }
    first, first_token = service.register_account(
        {**payload, "username": "webuno", "email": "webuno@example.test"}
    )
    service.app.chats.create(title="Chat web uno")
    second, second_token = service.register_account(
        {**payload, "username": "webdos", "email": "webdos@example.test"}
    )

    assert service.registry_accounts.account_for_session(first_token) is None
    assert service.registry_accounts.account_for_session(second_token) is not None
    assert service.app.account_public_id == second["public_id"]
    assert service.app.chats.list_active() == []

    logged, third_token = service.login_account(
        {"login": "webuno@example.test", "password": "clave9!segura"}
    )
    assert logged["public_id"] == first["public_id"]
    assert service.registry_accounts.account_for_session(second_token) is None
    assert service.registry_accounts.account_for_session(third_token) is not None
    assert [item["title"] for item in service.app.chats.list_active()] == [
        "Chat web uno"
    ]
    service.close()


def test_web_shell_is_compact_and_new_chat_is_lazy() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "src/elyndra/web/static/index.html").read_text()
    css = (root / "src/elyndra/web/static/app.css").read_text()
    javascript = (root / "src/elyndra/web/static/app.js").read_text()

    assert 'class="brand-mark"' not in html
    assert 'href="/" aria-label="Ir al inicio de Elyndra"' in html
    assert 'id="connection-local"' in html
    assert 'id="connection-online"' in html
    assert 'id="new-chat-mode"' not in html
    assert 'id="toggle-chat-search"' in html
    assert 'data-account-action="register"' in html
    assert 'data-account-action="switch"' in html
    assert '[data-developer-technical][hidden]' in css
    assert 'elements.newChat.addEventListener("click", openNewChat);' in javascript
    assert javascript.count('elements.newChat.addEventListener("click", openNewChat);') == 1
    assert 'body: JSON.stringify({ transcript_mode: "full" })' in javascript
    assert 'if (!state.activeChatId && state.draftChat && !globalWorkspaceActive)' in javascript


def test_new_chat_navigation_is_global_lazy_and_history_aware() -> None:
    root = Path(__file__).resolve().parents[1]
    javascript = (root / "src/elyndra/web/static/app.js").read_text()
    start = javascript.index("function openNewChat() {")
    end = javascript.index("\nasync function openChat", start)
    action = javascript[start:end]

    for workspace in (
        "state.inspectorActive",
        "state.alexandriaActive",
        "state.personalActive",
        "state.profileActive",
        "state.controlActive",
    ):
        assert workspace in action
    assert "state.activeChatId = null;" in action
    assert "state.activeChat = null;" in action
    assert "state.draftChat = true;" in action
    assert "renderWelcome({ updateUrl: false });" in action
    assert "updateChatUrl(null);" in action
    assert "elements.input.focus();" in action
    assert "createChat(" not in action
    assert "sendMessage(" not in action
    assert "api(" not in action
    assert 'window.addEventListener("popstate", async () => {' in javascript
    assert (
        "if (chatId) await openChat(chatId, { updateUrl: false });\n"
        "    else renderWelcome();"
    ) in javascript
    assert (
        "if (initialChatId) await openChat(initialChatId, { updateUrl: false });\n"
        "    else renderWelcome();"
    ) in javascript


def test_new_chat_frontend_action_does_not_mutate_persisted_chats(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    detail = service.create_chat(title="Conservar", transcript_mode="full")
    chat_id = detail["chat"]["id"]
    service.send_message(chat_id, "Este mensaje debe permanecer.")
    before = app.chats.get(chat_id)

    root = Path(__file__).resolve().parents[1]
    javascript = (root / "src/elyndra/web/static/app.js").read_text()
    start = javascript.index("function openNewChat() {")
    end = javascript.index("\nasync function openChat", start)
    assert "/api/" not in javascript[start:end]

    after = app.chats.get(chat_id)
    assert after == before
    assert len(service.list_chats()) == 1
    service.close()


def test_release_v087_version_and_schema(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert __version__ == "0.8.10-alpha"
    assert schema == "50"

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _cookie_from_response(response) -> str:  # noqa: ANN001
    value = response.headers.get("Set-Cookie", "")
    return value.split(";", 1)[0]


def test_user_mode_redirects_technical_pages_and_developer_mode_allows_them(
    isolated_home: ElyndraPaths,
) -> None:
    service = ElyndraWebService(ElyndraApplication.load(isolated_home))
    token = "v087-http-token"
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0), _handler_factory(service, token)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    opener = build_opener(_NoRedirect())
    try:
        payload = {
            "username": "normal",
            "email": "normal@example.test",
            "password": "clave9!segura",
            "password_confirmation": "clave9!segura",
            "birth_date": _adult_birth(),
            "approved": True,
        }
        request = Request(
            f"{base}/api/auth/register",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Elyndra-Token": token,
            },
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            cookie = _cookie_from_response(response)
        for path in ("/alexandria", "/control"):
            request = Request(f"{base}{path}", headers={"Cookie": cookie})
            try:
                opener.open(request, timeout=3)
            except HTTPError as exc:
                assert exc.code == 303
                assert exc.headers["Location"] == "/"
            else:
                raise AssertionError("La ruta técnica debía redirigir al inicio.")

        developer, developer_token = service.register_account(
            {
                **payload,
                "username": "developer",
                "email": "developer@example.test",
                "developer_mode": True,
            }
        )
        assert developer["developer_mode"] is True
        dev_cookie = f"elyndra_session={developer_token}"
        for path in ("/alexandria", "/control"):
            request = Request(f"{base}{path}", headers={"Cookie": dev_cookie})
            with urlopen(request, timeout=3) as response:
                assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        service.close()
