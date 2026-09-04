from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
    urlopen,
)

import pytest

from elyndra import __version__
from elyndra.application import ElyndraApplication
from elyndra.cli import main as cli_main
from elyndra.paths import ElyndraPaths
from elyndra.web.server import ElyndraWebService, _handler_factory, _LocalThreadingHTTPServer


def _adult_birth(years: int = 30) -> str:
    today = date.today()
    return date(today.year - years, today.month, min(today.day, 28)).isoformat()


def _register(app: ElyndraApplication, *, developer: bool = False) -> dict:
    return app.accounts.register(
        username="carlos_local",
        email="carlos@example.test",
        password="clave9!segura",
        password_confirmation="clave9!segura",
        birth_date=_adult_birth(),
        preferred_name="Carlos",
        system_user=app.identity.system_user,
        developer_mode=developer,
        telemetry_enabled=True,
    )


def _request_json(
    base: str,
    token: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    cookie: str = "",
) -> tuple[int, dict, str]:
    headers = {"Accept": "application/json", "X-Elyndra-Token": token}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    if cookie:
        headers["Cookie"] = cookie
    request = Request(f"{base}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response), response.headers.get("Set-Cookie", "")
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode()), exc.headers.get("Set-Cookie", "")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req, fp, code, msg, headers, newurl
    ):  # type: ignore[no-untyped-def]
        return None


def _request_page(
    base: str,
    path: str,
    *,
    cookie: str = "",
) -> tuple[int, str, str]:
    headers = {"Accept": "text/html"}
    if cookie:
        headers["Cookie"] = cookie
    request = Request(f"{base}{path}", headers=headers, method="GET")
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=5) as response:
            return (
                response.status,
                response.read().decode(),
                response.headers.get("Location", ""),
            )
    except HTTPError as exc:
        return exc.code, exc.read().decode(), exc.headers.get("Location", "")


def test_local_account_registration_auth_profile_and_password_security(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)

    with pytest.raises(ValueError, match="mayor de edad"):
        app.accounts.register(
            username="menor",
            email="menor@example.test",
            password="clave9!segura",
            password_confirmation="clave9!segura",
            birth_date=_adult_birth(17),
            system_user=app.identity.system_user,
        )
    with pytest.raises(ValueError, match="carácter especial"):
        app.accounts.register(
            username="adulto",
            email="adulto@example.test",
            password="clavesegura9",
            password_confirmation="clavesegura9",
            birth_date=_adult_birth(),
            system_user=app.identity.system_user,
        )

    account = _register(app)
    assert account["preferred_name"] == "Carlos"
    assert account["pronouns"] == ""
    assert account["sexual_orientation"] == ""
    assert account["telemetry_enabled"] is True
    with app.database.connect() as connection:
        password_hash = str(
            connection.execute("SELECT password_hash FROM local_accounts").fetchone()[0]
        )
    assert password_hash.startswith("$argon2id$")
    assert "clave9!segura" not in password_hash

    authenticated, token = app.accounts.authenticate(
        login="carlos@example.test", password="clave9!segura", interface="test"
    )
    assert authenticated["username"] == "carlos_local"
    assert app.accounts.account_for_session(token) is not None

    updated = app.accounts.update_profile(
        approved=True,
        preferred_name="Carly",
        pronouns="elle",
        developer_mode=True,
        telemetry_enabled=False,
    )
    app.refresh_account_identity()
    assert updated["preferred_name"] == "Carly"
    assert app.identity.display_name == "Carly"
    assert app.accounts.telemetry_preview()["enabled"] is False
    assert app.accounts.telemetry_preview()["fields"]["age_range"] == "30-39"

    app.accounts.change_password(
        current_password="clave9!segura",
        new_password="nueva9!clave",
        confirmation="nueva9!clave",
        approved=True,
    )
    assert app.accounts.account_for_session(token) is None
    with pytest.raises(ValueError, match="Credenciales inválidas"):
        app.accounts.authenticate(
            login="carlos_local", password="clave9!segura", interface="test"
        )
    app.accounts.authenticate(
        login="carlos_local", password="nueva9!clave", interface="test"
    )

    app.accounts.reset_password_local(
        system_user=app.identity.system_user,
        login="CARLOS_LOCAL",
        new_password="rescate9!local",
        confirmation="rescate9!local",
        approved=True,
    )
    with pytest.raises(ValueError, match="Credenciales inválidas"):
        app.accounts.authenticate(
            login="carlos_local", password="nueva9!clave", interface="test"
        )
    app.accounts.authenticate(
        login="carlos_local", password="rescate9!local", interface="test"
    )
    with pytest.raises(PermissionError, match="usuario del sistema"):
        app.accounts.reset_password_local(
            system_user="otro-usuario",
            login="carlos_local",
            new_password="otra9!clave",
            confirmation="otra9!clave",
            approved=True,
        )


def test_cli_local_password_reset_is_public_and_accepts_email(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _register(app)

    result = cli_main(
        [
            "account",
            "reset-password-local",
            "--login",
            "carlos@example.test",
            "--new-password",
            "rescate9!publico",
            "--confirm-password",
            "rescate9!publico",
            "--approve",
        ]
    )
    assert result == 0

    refreshed = ElyndraApplication.load(isolated_home)
    account, _ = refreshed.accounts.authenticate(
        login="CARLOS_LOCAL",
        password="rescate9!publico",
        interface="test",
    )
    assert account["email"] == "carlos@example.test"


def test_encrypted_local_export_and_telemetry_exclusions(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _register(app)
    target = isolated_home.data_dir / "exports" / "account.elyndra.json"

    result = app.accounts.export_encrypted(
        output_path=target,
        account_password="clave9!segura",
        export_passphrase="frase-local-9!segura",
        approved=True,
    )
    envelope = json.loads(result.read_text())
    assert envelope["format"] == "elyndra-encrypted-export-v1"
    assert envelope["cipher"] == "aes-256-gcm"
    assert envelope["remote_backup"] is False
    assert "SQLite format 3" not in result.read_text()
    assert result.stat().st_mode & 0o777 == 0o600

    preview = app.accounts.telemetry_preview()
    assert preview["network_delivery_implemented"] is False
    assert "prompts_or_searches" in preview["never_included"]
    assert "health_or_wellbeing" in preview["never_included"]
    assert "email" in preview["never_included"]


def test_dialogue_followup_and_capability_help_use_current_user_directly(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _register(app)
    app.refresh_account_identity()
    app.cognitive_executive.create_goal(
        title="Preparar revisión semanal",
        description="",
        domain="organizacion_personal",
        project="",
        priority="high",
        target_date=None,
        next_action="Recopilar tareas abiertas.",
        actor=app.identity.system_user,
    )

    clarification = app.ask("como voy", chat_id="chat-dialogue")
    followup = app.ask("mis objetivos xfa", chat_id="chat-dialogue")
    help_reply = app.ask("como puedo agregar un nuevo cumple o un objetivo aca")

    assert clarification.data["engine"] == "local-semantic-clarification"
    assert followup.data["engine"] == "local-cognitive-executive"
    assert "Preparar revisión semanal" in followup.message
    assert help_reply.data["engine"] == "local-capability-help"
    assert help_reply.message.startswith("Carlos, puedes hacerlo así")
    assert "Carlos tendrá" not in help_reply.message
    assert "habla con Carlos" not in help_reply.message
    assert "Personal → Cumpleaños" in help_reply.message
    assert "Personal → Objetivos" in help_reply.message


def test_web_registration_login_user_mode_profile_and_encrypted_export(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    local_token = "local-loopback-token"
    server = _LocalThreadingHTTPServer(("127.0.0.1", 0), _handler_factory(service, local_token))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, bootstrap, _ = _request_json(base, local_token, "/api/bootstrap")
        assert status == 200
        assert bootstrap["auth"]["registered"] is False
        assert bootstrap["auth"]["authenticated"] is False

        status, registered, set_cookie = _request_json(
            base,
            local_token,
            "/api/auth/register",
            method="POST",
            payload={
                "username": "persona_local",
                "email": "persona@example.test",
                "password": "clave9!segura",
                "password_confirmation": "clave9!segura",
                "birth_date": _adult_birth(),
                "preferred_name": "María",
                "developer_mode": False,
                "telemetry_enabled": False,
                "approved": True,
            },
        )
        assert status == 201
        assert registered["authenticated"] is True
        cookie = set_cookie.split(";", 1)[0]

        status, bootstrap, _ = _request_json(
            base, local_token, "/api/bootstrap", cookie=cookie
        )
        assert status == 200
        assert bootstrap["auth"]["authenticated"] is True
        assert bootstrap["developer_mode"] is False
        assert bootstrap["version"] == "0.8.10-alpha"

        status, _, _ = _request_json(
            base, local_token, "/api/control/overview", cookie=cookie
        )
        assert status == 403
        status, account_data, _ = _request_json(
            base, local_token, "/api/account", cookie=cookie
        )
        assert status == 200
        assert account_data["account"]["preferred_name"] == "María"
        assert account_data["security"]["two_factor_status"] == "available_not_configured"

        export_request = Request(
            f"{base}/api/account/export",
            data=json.dumps(
                {
                    "password": "clave9!segura",
                    "export_passphrase": "frase-web-9!segura",
                    "approved": True,
                }
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Elyndra-Token": local_token,
                "Cookie": cookie,
            },
            method="POST",
        )
        with urlopen(export_request, timeout=5) as response:
            payload = json.loads(response.read().decode())
            assert response.status == 200
            assert "attachment" in response.headers["Content-Disposition"]
        assert payload["format"] == "elyndra-encrypted-export-v1"
        assert payload["remote_backup"] is False
    finally:
        server.shutdown()
        server.server_close()
        service.close()
        thread.join(timeout=3)


def test_web_auth_pages_redirect_and_persist_session_until_logout(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    local_token = "local-loopback-token"
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0), _handler_factory(service, local_token)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, _, location = _request_page(base, "/")
        assert status == 303
        assert location == "/register"

        status, register_html, location = _request_page(base, "/register")
        assert status == 200
        assert location == ""
        assert 'id="register-form"' in register_html
        assert 'id="register-form" hidden' not in register_html
        assert 'id="login-form" hidden' in register_html

        status, login_html, location = _request_page(base, "/login")
        assert status == 200
        assert location == ""
        assert 'id="auth-tab-register"' in login_html

        status, registered, set_cookie = _request_json(
            base,
            local_token,
            "/api/auth/register",
            method="POST",
            payload={
                "username": "persona_local",
                "email": "persona@example.test",
                "password": "clave9!segura",
                "password_confirmation": "clave9!segura",
                "birth_date": _adult_birth(),
                "preferred_name": "María",
                "developer_mode": False,
                "telemetry_enabled": False,
                "approved": True,
            },
        )
        assert status == 201
        assert registered["authenticated"] is True
        assert "HttpOnly" in set_cookie
        assert "SameSite=Strict" in set_cookie
        assert "Max-Age=2592000" in set_cookie
        cookie = set_cookie.split(";", 1)[0]

        status, register_html, location = _request_page(base, "/register", cookie=cookie)
        assert status == 200
        assert location == ""
        assert 'id="register-form"' in register_html
        status, _, location = _request_page(base, "/login", cookie=cookie)
        assert status == 303
        assert location == "/"

        status, app_html, location = _request_page(base, "/", cookie=cookie)
        assert status == 200
        assert location == ""
        assert 'id="app-shell"' in app_html
        assert 'id="auth-screen" hidden' in app_html

        status, _, location = _request_page(base, "/")
        assert status == 303
        assert location == "/login"

        status, login_html, location = _request_page(base, "/login")
        assert status == 200
        assert location == ""
        assert 'id="login-form"' in login_html
        assert 'id="auth-tab-register" type="button"' in login_html
        assert "usuario o correo" in login_html.casefold()

        status, register_html, location = _request_page(base, "/register")
        assert status == 200
        assert location == ""
        assert 'id="register-form"' in register_html
        assert 'id="auth-tab-login"' in register_html
        assert 'id="auth-tab-register"' in register_html

        status, _, expired_cookie = _request_json(
            base,
            local_token,
            "/api/auth/logout",
            method="POST",
            payload={},
            cookie=cookie,
        )
        assert status == 200
        assert "Max-Age=0" in expired_cookie

        status, _, location = _request_page(base, "/", cookie=cookie)
        assert status == 303
        assert location == "/login"
    finally:
        server.shutdown()
        server.server_close()
        service.close()
        thread.join(timeout=3)


def test_authenticated_web_shell_keeps_account_visible_and_chat_menu_operable() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "src/elyndra/web/static/index.html").read_text()
    css = (root / "src/elyndra/web/static/app.css").read_text()
    javascript = (root / "src/elyndra/web/static/app.js").read_text()

    assert 'id="sidebar-account-button"' in html
    assert 'id="account-context-menu"' in html
    assert 'data-account-action="logout"' in html
    assert ".chat-list {\n  flex: 1 1 auto;\n  min-height: 0;" in css
    assert ".sidebar {\n  display: flex;\n  flex-direction: column;" in css
    assert "height: 100dvh;\n  overflow: hidden;" in css
    assert 'elements.chatActions.addEventListener("click", (event) => {' in javascript
    assert "event.stopPropagation();" in javascript
    assert "handleAccountMenuAction" in javascript


def test_release_v086_schema_and_version(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert schema == "51"
    assert __version__ == "0.8.10-alpha"
    assert {
        "local_accounts",
        "account_sessions",
        "account_consents",
        "account_recovery_settings",
        "account_mfa_factors",
        "assistant_dialogue_states",
        "account_vaults",
    } <= tables
