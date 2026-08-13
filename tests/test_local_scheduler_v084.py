from __future__ import annotations

import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths
from elyndra.scheduler import SchedulerAlreadyRunningError, SchedulerController
from elyndra.web.server import (
    ElyndraWebService,
    _handler_factory,
    _LocalThreadingHTTPServer,
)


def _scheduled_agenda(app: ElyndraApplication) -> None:
    policy = app.automation.create_policy(
        title="Agenda local",
        action_type="daily_brief.prepare",
        autonomy_level="prepare",
        timezone="America/Santiago",
        window_start=None,
        window_end=None,
        max_runs_per_day=1,
        starts_at=None,
        expires_at=None,
        domain="organizacion_personal",
        project="",
        actor="owner",
    )
    app.automation.create_automation(
        policy["public_id"],
        title="Agenda diaria",
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


def test_scheduler_lock_prevents_second_process_and_closes_cleanly(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    first = app.scheduler.open(interval_seconds=15, actor="owner", mode="test")

    assert app.scheduler.status()["running"] is True
    with pytest.raises(SchedulerAlreadyRunningError):
        app.scheduler.open(interval_seconds=15, actor="owner", mode="duplicate")

    first.close(status="stopped")
    status = app.scheduler.status()
    assert status["running"] is False
    assert status["latest_session"]["status"] == "stopped"
    assert not app.scheduler.lock_path.exists()


def test_scheduler_cycle_materializes_one_local_notification(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _scheduled_agenda(app)
    lease = app.scheduler.open(interval_seconds=15, actor="owner", mode="test-cycle")
    try:
        first = lease.cycle(now_value="2026-08-03T09:00:00-04:00")
        second = lease.cycle(now_value="2026-08-03T09:05:00-04:00")
    finally:
        lease.close(status="stopped")

    assert first["summary"]["created"] == 1
    assert first["summary"]["notifications_created"] == 1
    assert second["summary"]["created"] == 0
    assert second["summary"]["notifications_created"] == 0
    notifications = app.scheduler.list_notifications(status="pending")
    assert len(notifications) == 1
    assert notifications[0]["title"] == "Agenda del 2026-08-03"
    assert notifications[0]["external_delivery"] is False

    updated = app.scheduler.update_notification_status(
        notifications[0]["public_id"], status="seen"
    )
    assert updated["status"] == "seen"
    assert updated["seen_at"]


def test_web_scheduler_controller_stops_when_service_closes(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    started = service.scheduler_start({"interval_seconds": 15, "approved": True})
    assert started["web_thread_running"] is True
    assert app.scheduler.status()["running"] is True

    service.close()
    assert app.scheduler.status()["running"] is False


def _post_json(base: str, token: str, path: str, payload: dict) -> tuple[int, dict]:
    request = Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Elyndra-Token": token},
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_web_scheduler_requires_approval_and_shares_notifications(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _scheduled_agenda(app)
    service = ElyndraWebService(app)
    token = "scheduler-parity-token"
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0), _handler_factory(service, token)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    try:
        code, denied = _post_json(
            base,
            token,
            "/api/personal/scheduler/cycle",
            {"now": "2026-08-03T09:00:00-04:00"},
        )
        assert code == 400
        assert "confirmación explícita" in denied["error"]

        code, cycle = _post_json(
            base,
            token,
            "/api/personal/scheduler/cycle",
            {"now": "2026-08-03T09:00:00-04:00", "approved": True},
        )
        assert code == 200
        assert cycle["summary"]["notifications_created"] == 1

        with urlopen(f"{base}/personal", timeout=3) as response:
            page = response.read().decode("utf-8")
        assert "Elyndra 0.8.9-alpha" in page
        assert 'id="personal-scheduler-start"' in page
        assert 'id="personal-local-notifications"' in page

        with urlopen(f"{base}/api/personal/overview", timeout=3) as response:
            overview = json.load(response)
        assert overview["scheduler"]["interprocess_lock"] is True
        assert len(overview["local_notifications"]) == 1
        notification_id = overview["local_notifications"][0]["public_id"]

        code, changed = _post_json(
            base,
            token,
            "/api/personal/notifications/status",
            {
                "notification_id": notification_id,
                "status": "dismissed",
                "approved": True,
            },
        )
        assert code == 200
        assert changed["item"]["status"] == "dismissed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()


def test_scheduler_thread_runs_and_stops_without_daemonizing(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    controller = SchedulerController(app.scheduler, actor="owner")
    started = controller.start(interval_seconds=15)
    assert started["web_thread_running"] is True

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        latest = app.scheduler.status().get("latest_session") or {}
        if latest.get("scans_count", 0) >= 1:
            break
        time.sleep(0.02)
    stopped = controller.stop()

    assert stopped["web_thread_running"] is False
    assert app.scheduler.status()["running"] is False
    assert app.scheduler.status()["latest_session"]["status"] == "stopped"


def test_scheduler_chat_route_is_local_without_ollama(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)

    reply = app.ask("¿Está activo el scheduler local?")

    assert reply.ok is True
    assert reply.data["engine"] == "local-optional-scheduler"
    assert reply.data["model_used"] is False
    assert "Scheduler local opcional" in reply.message
