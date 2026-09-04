from __future__ import annotations

import json
import threading
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from elyndra import __version__
from elyndra.application import ElyndraApplication
from elyndra.paths import ElyndraPaths
from elyndra.personal_organizer import local_today
from elyndra.web.server import (
    ElyndraWebService,
    _handler_factory,
    _LocalThreadingHTTPServer,
)


def _commitment_for_tomorrow(app: ElyndraApplication) -> dict:
    target = local_today("America/Santiago") + timedelta(days=1)
    return app.personal_organizer.create_commitment(
        title="Control médico web",
        description="",
        event_date=target.isoformat(),
        event_time="10:30",
        timezone="America/Santiago",
        domain="salud_personal",
        project="",
        priority="high",
        recurrence="once",
        interval=1,
        weekdays=(),
        until=None,
        goal_public_id="",
        task_public_id="",
        actor="owner",
    )


def _promote_capital(app: ElyndraApplication) -> dict:
    proposal = app.general_knowledge.create_owner_proposal(
        statement="La capital de Chile es Santiago.",
        subject="capital de Chile",
        kind="factual",
        locale="es",
        actor="owner",
    )
    return app.general_knowledge.promote(proposal["public_id"], actor="owner")


def test_web_chat_uses_same_organizer_and_knowledge_runtime_as_cli(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _commitment_for_tomorrow(app)
    _promote_capital(app)
    service = ElyndraWebService(app)
    chat = service.create_chat(title="Paridad", transcript_mode="full")

    organizer = service.send_message(chat["chat"]["id"], "¿Qué tengo mañana?")
    knowledge = service.send_message(
        chat["chat"]["id"], "¿Qué es la capital de Chile?"
    )

    assert organizer["meta"]["engine"] == "local-personal-organizer"
    assert organizer["meta"]["runtime_version"] == __version__
    assert organizer["meta"]["shared_application_runtime"] is True
    assert "Control médico web" in organizer["message"]
    assert knowledge["meta"]["engine"] == "local-general-knowledge"
    assert knowledge["message"] == "La capital de Chile es Santiago."


def test_wellbeing_summary_uses_local_runtime_in_cli_and_web(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    today = local_today("America/Santiago")
    for offset, mood in ((0, 4), (1, 3)):
        app.wellbeing.create_checkin(
            checkin_date=(today - timedelta(days=offset)).isoformat(),
            mood=mood,
            energy=3,
            stress=2,
            focus=3,
            sleep_hours=7,
            sleep_quality=4,
            hydration=3,
            nutrition=3,
            activity_minutes=30,
            note="",
            actor="owner",
        )
    direct = app.ask("¿Cómo he estado esta semana?")
    service = ElyndraWebService(app)
    chat = service.create_chat(title="Bienestar", transcript_mode="full")
    web = service.send_message(chat["chat"]["id"], "¿Cómo he estado esta semana?")

    assert direct.data["engine"] == "local-personal-wellbeing"
    assert web["meta"]["engine"] == "local-personal-wellbeing"
    assert web["message"] == direct.message
    assert "Check-ins registrados: 2" in web["message"]


def test_http_stream_returns_local_organizer_and_runtime_version(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _commitment_for_tomorrow(app)
    service = ElyndraWebService(app)
    chat = service.create_chat(title="HTTP", transcript_mode="full")
    token = "parity-token"
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0), _handler_factory(service, token)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    try:
        request = Request(
            f"{base}/api/chats/{chat['chat']['id']}/messages/stream",
            data=json.dumps({"text": "¿Qué tengo mañana?"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Elyndra-Token": token,
            },
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            events = [
                json.loads(line)
                for line in response.read().decode("utf-8").splitlines()
                if line.strip()
            ]
        done = next(event["response"] for event in events if event["type"] == "done")

        assert done["meta"]["engine"] == "local-personal-organizer"
        assert done["meta"]["runtime_version"] == "0.8.10-alpha"
        assert done["elapsed_ms"] < 5_000
        assert "Control médico web" in done["message"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()


def test_personal_web_page_identifies_runtime_and_requires_approval(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    token = "personal-token"
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0), _handler_factory(service, token)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    try:
        with urlopen(f"{base}/personal", timeout=3) as response:
            page = response.read().decode("utf-8")
            runtime_header = response.headers["X-Elyndra-Version"]
        assert "Elyndra 0.8.10-alpha" in page
        assert runtime_header == "0.8.10-alpha"

        payload = {
            "title": "Compromiso web",
            "date": local_today("America/Santiago").isoformat(),
            "approved": True,
        }
        request = Request(
            f"{base}/api/personal/commitments",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Elyndra-Token": token,
            },
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            created = json.load(response)
        assert created["item"]["title"] == "Compromiso web"

        with urlopen(f"{base}/api/personal/overview", timeout=3) as response:
            overview = json.load(response)
        assert overview["runtime_version"] == "0.8.10-alpha"
        assert overview["interface_parity"]["deterministic_routes_shared"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()


def test_coaching_plan_and_checkin_remain_non_autonomous(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    checkin = app.wellbeing.create_checkin(
        checkin_date="2026-08-03",
        mood=3,
        energy=2,
        stress=4,
        focus=2,
        sleep_hours=5.5,
        sleep_quality=2,
        hydration=3,
        nutrition=3,
        activity_minutes=10,
        note="Día exigente.",
        actor="owner",
    )
    plan = app.wellbeing.create_plan(
        title="Recuperar energía",
        focus="bienestar",
        objective="Mejorar descanso y reducir carga sin reemplazar apoyo profesional.",
        start_date="2026-08-03",
        review_date="2026-08-10",
        actions=("Preparar una hora de descanso.", "Revisar compromisos no esenciales."),
        actor="owner",
    )

    assert checkin["mood"] == 3
    assert plan["status"] == "active"
    assert len(plan["actions"]) == 2
    status = app.wellbeing.status()
    assert status["diagnosis"] is False
    assert status["treatment_authority"] is False
    assert status["background_execution"] is False
    assert status["automatic_intervention"] is False

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


def test_personal_http_operations_match_cli_repositories_and_require_approval(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)
    token = "personal-parity-token"
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0), _handler_factory(service, token)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    target = local_today("America/Santiago").isoformat()

    try:
        status, rejected = _post_json(
            base,
            token,
            "/api/personal/routines",
            {"title": "Rutina sin aprobación", "start_date": target},
        )
        assert status == 400
        assert "confirmación" in rejected["error"].casefold()

        status, created = _post_json(
            base,
            token,
            "/api/personal/routines",
            {
                "title": "Caminar por web",
                "start_date": target,
                "recurrence": "daily",
                "approved": True,
            },
        )
        assert status == 201
        routine_id = created["item"]["public_id"]

        status, checkin = _post_json(
            base,
            token,
            "/api/personal/routines/checkins",
            {
                "routine_id": routine_id,
                "date": target,
                "status": "completed",
                "note": "Hecho desde web.",
                "approved": True,
            },
        )
        assert status == 201
        assert checkin["item"]["status"] == "completed"

        status, proposed = _post_json(
            base,
            token,
            "/api/personal/reminders",
            {
                "item_id": routine_id,
                "minutes_before": 15,
                "approved": True,
            },
        )
        assert status == 201
        reminder_id = proposed["item"]["public_id"]

        status, reviewed = _post_json(
            base,
            token,
            "/api/personal/reminders/review",
            {
                "reminder_id": reminder_id,
                "decision": "approve",
                "approved": True,
            },
        )
        assert status == 200
        assert reviewed["item"]["status"] == "approved"

        status, plan = _post_json(
            base,
            token,
            "/api/personal/coaching/plans",
            {
                "title": "Plan local web",
                "focus": "energía",
                "objective": "Revisar descanso sin diagnóstico.",
                "start_date": target,
                "actions": ["Reservar descanso.", "Reducir una tarea no esencial."],
                "approved": True,
            },
        )
        assert status == 201
        plan_id = plan["item"]["public_id"]
        action_id = plan["item"]["actions"][0]["public_id"]

        status, action = _post_json(
            base,
            token,
            "/api/personal/coaching/actions/status",
            {
                "action_id": action_id,
                "status": "completed",
                "approved": True,
            },
        )
        assert status == 200
        assert action["item"]["status"] == "completed"

        status, paused = _post_json(
            base,
            token,
            "/api/personal/coaching/plans/status",
            {"plan_id": plan_id, "status": "paused", "approved": True},
        )
        assert status == 200
        assert paused["item"]["status"] == "paused"

        with urlopen(f"{base}/api/personal/overview", timeout=3) as response:
            overview = json.load(response)
        assert any(item["public_id"] == routine_id for item in overview["organizer_items"])
        assert any(item["public_id"] == reminder_id for item in overview["reminders"])
        assert any(item["public_id"] == plan_id for item in overview["coaching_plans"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()


def test_personal_page_exposes_full_organizer_and_coaching_forms(
    isolated_home: ElyndraPaths,
) -> None:
    ElyndraApplication.load(isolated_home)
    page = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "elyndra"
        / "web"
        / "static"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert 'id="personal-routine-checkin-form"' in page
    assert 'id="personal-reminder-form"' in page
    assert 'id="personal-reminder-review-form"' in page
    assert 'id="personal-coaching-form"' in page
    assert 'id="personal-coaching-status-form"' in page
    assert 'id="personal-coaching-action-form"' in page
