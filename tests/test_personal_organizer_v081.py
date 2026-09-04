from __future__ import annotations

from datetime import date

import pytest

from elyndra import __version__
from elyndra.application import ElyndraApplication
from elyndra.cli import build_parser
from elyndra.paths import ElyndraPaths
from elyndra.personal_organizer import local_today, occurs_on, organizer_query


def _commitment(
    app: ElyndraApplication,
    *,
    title: str,
    event_date: str,
    event_time: str | None = None,
    recurrence: str = "once",
    weekdays: tuple[str, ...] = (),
    project: str = "",
) -> dict:
    return app.personal_organizer.create_commitment(
        title=title,
        description="",
        event_date=event_date,
        event_time=event_time,
        timezone="America/Santiago",
        domain="organizacion_personal",
        project=project,
        priority="normal",
        recurrence=recurrence,
        interval=1,
        weekdays=weekdays,
        until=None,
        goal_public_id="",
        task_public_id="",
        actor="owner",
    )


def _routine(
    app: ElyndraApplication,
    *,
    title: str,
    start_date: str,
    recurrence: str = "daily",
    weekdays: tuple[str, ...] = (),
) -> dict:
    return app.personal_organizer.create_routine(
        title=title,
        description="",
        start_date=start_date,
        event_time="08:00",
        timezone="America/Santiago",
        domain="organizacion_personal",
        project="",
        priority="normal",
        recurrence=recurrence,
        interval=1,
        weekdays=weekdays,
        until=None,
        goal_public_id="",
        task_public_id="",
        actor="owner",
    )


def test_schema_42_version_and_organizer_status(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert schema == "51"
    assert __version__ == "0.8.10-alpha"
    assert {
        "assistant_organizer_items",
        "assistant_routine_checkins",
        "assistant_organizer_reminders",
    } <= tables
    status = app.personal_organizer.status()
    assert status["background_execution"] is False
    assert status["automatic_notifications"] is False
    assert status["recurrence_expanded_on_demand"] is True


def test_daily_brief_contains_commitment_birthday_and_routine(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    target = "2026-08-03"
    _commitment(
        app,
        title="Cita médica",
        event_date=target,
        event_time="10:30",
    )
    app.personal_organizer.create_birthday(
        person_name="Ana",
        month=8,
        day=3,
        birth_year=1990,
        timezone="America/Santiago",
        domain="organizacion_personal",
        project="",
        priority="normal",
        actor="owner",
    )
    routine = _routine(app, title="Caminar", start_date=target)
    app.personal_organizer.checkin_routine(
        routine["public_id"],
        occurrence_date=target,
        status="completed",
        note="Rutina realizada.",
        actor="owner",
    )

    brief = app.personal_organizer.daily_brief(target)

    assert brief["summary"] == {
        "scheduled": 3,
        "commitments": 1,
        "birthdays": 1,
        "routines": 1,
        "overdue": 0,
        "reminders": 0,
    }
    assert [item["item_type"] for item in brief["scheduled"]] == [
        "routine",
        "commitment",
        "birthday",
    ]
    birthday = next(
        item for item in brief["scheduled"] if item["item_type"] == "birthday"
    )
    assert birthday["age"] == 36
    routine_item = next(
        item for item in brief["scheduled"] if item["item_type"] == "routine"
    )
    assert routine_item["checkin"]["status"] == "completed"


def test_recurrence_is_bounded_and_calculated_on_demand(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    weekly = _routine(
        app,
        title="Revisión semanal",
        start_date="2026-08-03",
        recurrence="weekly",
        weekdays=("lunes", "miércoles"),
    )
    monthly = app.personal_organizer.create_commitment(
        title="Cierre mensual",
        description="",
        event_date="2026-01-31",
        event_time=None,
        timezone="America/Santiago",
        domain="organizacion_personal",
        project="",
        priority="normal",
        recurrence="monthly",
        interval=1,
        weekdays=(),
        until=None,
        goal_public_id="",
        task_public_id="",
        actor="owner",
    )

    assert occurs_on(weekly, date(2026, 8, 3)) is True
    assert occurs_on(weekly, date(2026, 8, 5)) is True
    assert occurs_on(weekly, date(2026, 8, 4)) is False
    assert occurs_on(monthly, date(2026, 2, 28)) is True
    assert app.personal_organizer.upcoming(
        start_date="2026-08-03",
        days=7,
    )["background_execution"] is False


def test_reminders_require_separate_review_and_only_surface_locally(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    commitment = _commitment(
        app,
        title="Control médico",
        event_date="2026-08-03",
        event_time="10:30",
    )
    proposal = app.personal_organizer.propose_reminder(
        commitment["public_id"],
        minutes_before=30,
        actor="owner",
    )

    assert proposal["status"] == "proposed"
    assert app.personal_organizer.daily_brief("2026-08-03")["reminders"] == []

    approved = app.personal_organizer.review_reminder(
        proposal["public_id"],
        decision="approve",
        actor="owner",
    )
    brief = app.personal_organizer.daily_brief("2026-08-03")

    assert approved["status"] == "approved"
    assert approved["automatic_notification"] is False
    assert len(brief["reminders"]) == 1
    assert brief["reminders"][0]["reminder_at"][11:16] == "10:00"
    with pytest.raises(ValueError, match="ya fue revisada"):
        app.personal_organizer.review_reminder(
            proposal["public_id"],
            decision="reject",
            actor="owner",
        )


def test_routine_checkin_requires_real_occurrence_and_is_single_use(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    routine = _routine(
        app,
        title="Revisión de lunes",
        start_date="2026-08-03",
        recurrence="weekly",
        weekdays=("lunes",),
    )

    with pytest.raises(ValueError, match="no tiene una ocurrencia"):
        app.personal_organizer.checkin_routine(
            routine["public_id"],
            occurrence_date="2026-08-04",
            status="completed",
            note="",
            actor="owner",
        )
    app.personal_organizer.checkin_routine(
        routine["public_id"],
        occurrence_date="2026-08-03",
        status="completed",
        note="Listo.",
        actor="owner",
    )
    with pytest.raises(ValueError, match="ya tiene un check-in"):
        app.personal_organizer.checkin_routine(
            routine["public_id"],
            occurrence_date="2026-08-03",
            status="skipped",
            note="",
            actor="owner",
        )


def test_organizer_items_can_link_to_goal_and_task(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    goal = app.cognitive_executive.create_goal(
        title="Organizar semana",
        description="",
        domain="organizacion_personal",
        project="",
        priority="normal",
        target_date=None,
        next_action="Crear compromiso.",
        actor="owner",
    )
    task = app.cognitive_executive.create_task(
        goal["public_id"],
        title="Asistir a reunión",
        priority="normal",
        due_date="2026-08-04",
        depends_on=(),
        actor="owner",
    )
    item = app.personal_organizer.create_commitment(
        title="Reunión semanal",
        description="",
        event_date="2026-08-04",
        event_time="11:00",
        timezone="America/Santiago",
        domain="organizacion_personal",
        project="",
        priority="normal",
        recurrence="once",
        interval=1,
        weekdays=(),
        until=None,
        goal_public_id=goal["public_id"],
        task_public_id=task["public_id"],
        actor="owner",
    )

    shown = app.personal_organizer.item_details(item["public_id"])
    assert shown is not None
    assert shown["goal_public_id"] == goal["public_id"]
    assert shown["task_public_id"] == task["public_id"]


def test_conversational_daily_brief_does_not_call_model(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    today = local_today("America/Santiago")
    _commitment(
        app,
        title="Compromiso de hoy",
        event_date=today.isoformat(),
        event_time="12:00",
    )

    result = app.ask("¿Qué tengo hoy?")

    assert result.ok is True
    assert result.data["engine"] == "local-personal-organizer"
    assert result.data["model_used"] is False
    assert result.data["background_execution"] is False
    assert "Compromiso de hoy" in result.message
    assert result.data["executive"]["selected_route"] == "organizer"


def test_project_scope_and_overdue_items(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _commitment(
        app,
        title="Global vencido",
        event_date="2026-08-01",
    )
    _commitment(
        app,
        title="Proyecto Elyndra",
        event_date="2026-08-03",
        project="elyndra",
    )
    _commitment(
        app,
        title="Otro proyecto",
        event_date="2026-08-03",
        project="otro",
    )

    brief = app.personal_organizer.daily_brief(
        "2026-08-03",
        project="elyndra",
    )

    titles = {item["title"] for item in brief["scheduled"]}
    assert "Proyecto Elyndra" in titles
    assert "Otro proyecto" not in titles
    assert [item["title"] for item in brief["overdue"]] == ["Global vencido"]


def test_closed_items_do_not_appear_and_no_automatic_goal_progress(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    commitment = _commitment(
        app,
        title="Compromiso cerrado",
        event_date="2026-08-03",
    )
    updated = app.personal_organizer.update_item_status(
        commitment["public_id"],
        status="completed",
    )

    assert updated["status"] == "completed"
    assert app.personal_organizer.daily_brief("2026-08-03")["scheduled"] == []
    assert app.cognitive_executive.status()["automatic_goal_progress"] is False


def test_validation_and_cli_surface(isolated_home: ElyndraPaths) -> None:
    app = ElyndraApplication.load(isolated_home)
    parser = build_parser()
    parsed = parser.parse_args(
        [
            "assistant",
            "routine-create",
            "--title",
            "Caminar",
            "--start-date",
            "2026-08-03",
            "--weekday",
            "lunes",
            "--approve",
        ]
    )
    assert parsed.assistant_command == "routine-create"
    assert organizer_query("agenda de mañana") == {
        "kind": "daily_brief",
        "offset_days": 1,
    }
    with pytest.raises(ValueError, match="Día de semana inválido"):
        _routine(
            app,
            title="Inválida",
            start_date="2026-08-03",
            recurrence="weekly",
            weekdays=("octodía",),
        )
    with pytest.raises(ValueError, match="Zona horaria inválida"):
        app.personal_organizer.create_birthday(
            person_name="Ana",
            month=8,
            day=3,
            birth_year=None,
            timezone="Invalid/Timezone",
            domain="",
            project="",
            priority="normal",
            actor="owner",
        )
