from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from elyndra import __version__
from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths
from elyndra.web.server import (
    ElyndraWebService,
    _handler_factory,
    _LocalThreadingHTTPServer,
)


def _policy(
    app: ElyndraApplication,
    *,
    level: str = "prepare",
    action: str = "daily_brief.prepare",
) -> dict:
    return app.automation.create_policy(
        title="Política diaria",
        action_type=action,
        autonomy_level=level,
        timezone="America/Santiago",
        window_start="07:00",
        window_end="10:00",
        max_runs_per_day=2,
        starts_at=None,
        expires_at=None,
        domain="organizacion_personal",
        project="",
        actor="owner",
    )


def _automation(app: ElyndraApplication, policy_id: str) -> dict:
    return app.automation.create_automation(
        policy_id,
        title="Preparar agenda",
        schedule_kind="daily",
        start_date="2026-08-03",
        time_of_day="08:00",
        weekdays=(),
        month_day=None,
        interval=1,
        until_date=None,
        params={},
        actor="owner",
    )


def test_prepare_policy_materializes_once_and_uses_local_inbox(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    policy = _policy(app)
    automation = _automation(app, policy["public_id"])

    first = app.automation.scan_due(
        now_value="2026-08-03T09:00:00-04:00",
        actor="owner",
    )
    second = app.automation.scan_due(
        now_value="2026-08-03T09:05:00-04:00",
        actor="owner",
    )

    assert first["summary"] == {
        "created": 1,
        "pending_approval": 0,
        "prepared_or_executed": 1,
        "skipped": 0,
    }
    assert first["runs"][0]["status"] == "prepared"
    assert first["runs"][0]["automation_public_id"] == automation["public_id"]
    assert second["summary"]["created"] == 0
    inbox = app.automation.list_inbox(status="unread")
    assert len(inbox) == 1
    assert inbox[0]["title"] == "Agenda del 2026-08-03"
    assert inbox[0]["external_notification_sent"] is False
    status = app.automation.status()
    assert status["background_execution"] is False
    assert status["network_actions"] is False
    assert status["skills_allowed"] is False
    assert status["file_writes_allowed"] is False


def test_execute_with_approval_requires_separate_run_approval(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    policy = _policy(app, level="execute_with_approval")
    _automation(app, policy["public_id"])

    scan = app.automation.scan_due(
        now_value="2026-08-03T09:00:00-04:00",
        actor="owner",
    )
    run = scan["runs"][0]
    assert run["status"] == "pending_approval"
    assert app.automation.list_inbox(status="all") == []

    approved = app.automation.approve_run(run["public_id"], actor="owner")
    assert approved["status"] == "executed"
    assert approved["approved_by"] == "owner"
    assert len(app.automation.list_inbox(status="unread")) == 1


def test_policy_window_and_daily_limit_skip_without_expanding_authority(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    policy = app.automation.create_policy(
        title="Ventana limitada",
        action_type="goal.review.prepare",
        autonomy_level="execute_under_policy",
        timezone="America/Santiago",
        window_start="09:00",
        window_end="10:00",
        max_runs_per_day=1,
        starts_at=None,
        expires_at=None,
        domain="",
        project="",
        actor="owner",
    )
    app.automation.create_automation(
        policy["public_id"],
        title="Fuera de ventana",
        schedule_kind="once",
        start_date="2026-08-03",
        time_of_day="08:00",
        weekdays=(),
        month_day=None,
        interval=1,
        until_date=None,
        params={},
        actor="owner",
    )

    scan = app.automation.scan_due(
        now_value="2026-08-03T09:30:00-04:00",
        actor="owner",
    )

    assert scan["summary"]["created"] == 0
    assert scan["summary"]["skipped"] == 1
    assert scan["skipped"][0]["result"]["reason"] == "outside_policy_window"
    assert app.automation.list_inbox(status="all") == []


def test_automation_chat_route_is_local_and_does_not_invoke_model(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _policy(app)

    reply = app.ask("¿Qué automatizaciones tengo?")

    assert reply.ok is True
    assert reply.data["engine"] == "local-policy-bounded-automation"
    assert reply.data["model_used"] is False
    assert reply.data["background_execution"] is False
    assert "Políticas activas: 1" in reply.message


def _post_json(base: str, token: str, path: str, payload: dict) -> tuple[int, dict]:
    request = Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Elyndra-Token": token,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_web_automation_operations_share_repository_and_require_approval(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    token = "automation-parity-token"
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0), _handler_factory(service, token)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    try:
        policy_payload = {
            "title": "Agenda web",
            "action_type": "daily_brief.prepare",
            "autonomy_level": "prepare",
            "window_start": "07:00",
            "window_end": "10:00",
            "approved": True,
        }
        code, created = _post_json(
            base, token, "/api/personal/automation/policies", policy_payload
        )
        assert code == 201
        policy_id = created["item"]["public_id"]

        code, denied = _post_json(
            base,
            token,
            "/api/personal/automations",
            {
                "policy_id": policy_id,
                "title": "Sin aprobación",
                "schedule_kind": "daily",
                "start_date": "2026-08-03",
                "time_of_day": "08:00",
            },
        )
        assert code == 400
        assert "confirmación explícita" in denied["error"]

        code, created_automation = _post_json(
            base,
            token,
            "/api/personal/automations",
            {
                "policy_id": policy_id,
                "title": "Agenda web diaria",
                "schedule_kind": "daily",
                "start_date": "2026-08-03",
                "time_of_day": "08:00",
                "approved": True,
            },
        )
        assert code == 201
        assert created_automation["item"]["policy_public_id"] == policy_id

        code, scan = _post_json(
            base,
            token,
            "/api/personal/automations/scan",
            {"now": "2026-08-03T09:00:00-04:00", "approved": True},
        )
        assert code == 200
        assert scan["summary"]["prepared_or_executed"] == 1

        with urlopen(f"{base}/api/personal/overview", timeout=3) as response:
            overview = json.load(response)
        assert overview["runtime_version"] == __version__
        assert overview["automation"]["active_automations"] == 1
        assert len(overview["automation_inbox"]) == 1
        assert app.automation.status()["active_automations"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()
