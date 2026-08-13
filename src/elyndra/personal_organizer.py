from __future__ import annotations

import json
import re
import unicodedata
import uuid
from calendar import monthrange
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from elyndra.db import Database

_ITEM_TYPES = ("commitment", "birthday", "routine")
_ITEM_STATUSES = ("active", "paused", "completed", "cancelled")
_PRIORITIES = ("low", "normal", "high", "critical")
_RECURRENCES = ("once", "daily", "weekly", "monthly", "yearly")
_CHECKIN_STATUSES = ("completed", "skipped")
_REMINDER_STATUSES = ("proposed", "approved", "rejected", "cancelled")
_DEFAULT_TIMEZONE = "America/Santiago"
_MAX_ITEMS = 2_000
_MAX_REMINDER_MINUTES = 43_200
_WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "lunes": 0,
    "lun": 0,
    "tue": 1,
    "tuesday": 1,
    "martes": 1,
    "mar": 1,
    "wed": 2,
    "wednesday": 2,
    "miercoles": 2,
    "mie": 2,
    "thu": 3,
    "thursday": 3,
    "jueves": 3,
    "jue": 3,
    "fri": 4,
    "friday": 4,
    "viernes": 4,
    "vie": 4,
    "sat": 5,
    "saturday": 5,
    "sabado": 5,
    "sab": 5,
    "sun": 6,
    "sunday": 6,
    "domingo": 6,
    "dom": 6,
}
_WEEKDAY_NAMES = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)


class PersonalOrganizerRepository:
    """Local organizer with bounded recurrence and no background authority."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def status(self) -> dict[str, Any]:
        today = local_today(_DEFAULT_TIMEZONE)
        with self.database.connect() as connection:
            items = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_organizer_items"
                ).fetchone()[0]
            )
            active = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_organizer_items "
                    "WHERE status = 'active'"
                ).fetchone()[0]
            )
            routines = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_organizer_items "
                    "WHERE item_type = 'routine' AND status = 'active'"
                ).fetchone()[0]
            )
            birthdays = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_organizer_items "
                    "WHERE item_type = 'birthday' AND status = 'active'"
                ).fetchone()[0]
            )
            reminders = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_organizer_reminders "
                    "WHERE status = 'approved'"
                ).fetchone()[0]
            )
        brief = self.daily_brief(today.isoformat(), timezone=_DEFAULT_TIMEZONE)
        return {
            "enabled": True,
            "items": items,
            "active_items": active,
            "active_routines": routines,
            "active_birthdays": birthdays,
            "approved_reminders": reminders,
            "today_items": brief["summary"]["scheduled"],
            "today_overdue": brief["summary"]["overdue"],
            "timezone": _DEFAULT_TIMEZONE,
            "background_execution": False,
            "automatic_notifications": False,
            "automatic_completion": False,
            "daily_brief_deterministic": True,
            "recurrence_expanded_on_demand": True,
        }

    def create_commitment(
        self,
        *,
        title: str,
        description: str,
        event_date: str,
        event_time: str | None,
        timezone: str,
        domain: str,
        project: str,
        priority: str,
        recurrence: str,
        interval: int,
        weekdays: tuple[str, ...],
        until: str | None,
        goal_public_id: str,
        task_public_id: str,
        actor: str,
    ) -> dict[str, Any]:
        anchor = _parse_date(event_date, "fecha")
        recurrence_kind = _recurrence(recurrence, allow_yearly=True)
        values = self._base_item_values(
            item_type="commitment",
            title=title,
            description=description,
            person_name="",
            anchor_date=anchor,
            event_time=event_time,
            timezone=timezone,
            domain=domain,
            project=project,
            priority=priority,
            recurrence=recurrence_kind,
            interval=interval,
            weekdays=weekdays,
            until=until,
            recurrence_month=anchor.month if recurrence_kind == "yearly" else None,
            recurrence_day=(
                anchor.day if recurrence_kind in {"monthly", "yearly"} else None
            ),
            birth_year=None,
            goal_public_id=goal_public_id,
            task_public_id=task_public_id,
            actor=actor,
        )
        return self._insert_item(values)

    def create_birthday(
        self,
        *,
        person_name: str,
        month: int,
        day: int,
        birth_year: int | None,
        timezone: str,
        domain: str,
        project: str,
        priority: str,
        actor: str,
    ) -> dict[str, Any]:
        if not 1 <= month <= 12:
            raise ValueError("Mes de cumpleaños inválido.")
        max_day = monthrange(2000, month)[1]
        if not 1 <= day <= max_day:
            raise ValueError("Día de cumpleaños inválido.")
        if birth_year is not None and not 1900 <= birth_year <= 9999:
            raise ValueError("Año de nacimiento inválido.")
        anchor = date(birth_year or 2000, month, day)
        person = _required(person_name, "persona", 200)
        values = self._base_item_values(
            item_type="birthday",
            title=f"Cumpleaños de {person}",
            description="",
            person_name=person,
            anchor_date=anchor,
            event_time=None,
            timezone=timezone,
            domain=domain or "organizacion_personal",
            project=project,
            priority=priority,
            recurrence="yearly",
            interval=1,
            weekdays=(),
            until=None,
            recurrence_month=month,
            recurrence_day=day,
            birth_year=birth_year,
            goal_public_id="",
            task_public_id="",
            actor=actor,
        )
        return self._insert_item(values)

    def create_routine(
        self,
        *,
        title: str,
        description: str,
        start_date: str,
        event_time: str | None,
        timezone: str,
        domain: str,
        project: str,
        priority: str,
        recurrence: str,
        interval: int,
        weekdays: tuple[str, ...],
        until: str | None,
        goal_public_id: str,
        task_public_id: str,
        actor: str,
    ) -> dict[str, Any]:
        anchor = _parse_date(start_date, "fecha de inicio")
        recurrence_kind = _recurrence(recurrence, allow_yearly=False)
        if recurrence_kind == "once":
            raise ValueError("Una rutina debe ser diaria, semanal o mensual.")
        values = self._base_item_values(
            item_type="routine",
            title=title,
            description=description,
            person_name="",
            anchor_date=anchor,
            event_time=event_time,
            timezone=timezone,
            domain=domain or "organizacion_personal",
            project=project,
            priority=priority,
            recurrence=recurrence_kind,
            interval=interval,
            weekdays=weekdays,
            until=until,
            recurrence_month=None,
            recurrence_day=anchor.day if recurrence_kind == "monthly" else None,
            birth_year=None,
            goal_public_id=goal_public_id,
            task_public_id=task_public_id,
            actor=actor,
        )
        return self._insert_item(values)

    def _base_item_values(
        self,
        *,
        item_type: str,
        title: str,
        description: str,
        person_name: str,
        anchor_date: date,
        event_time: str | None,
        timezone: str,
        domain: str,
        project: str,
        priority: str,
        recurrence: str,
        interval: int,
        weekdays: tuple[str, ...],
        until: str | None,
        recurrence_month: int | None,
        recurrence_day: int | None,
        birth_year: int | None,
        goal_public_id: str,
        task_public_id: str,
        actor: str,
    ) -> dict[str, Any]:
        clean_weekdays = _weekdays(weekdays)
        if recurrence == "weekly" and not clean_weekdays:
            clean_weekdays = (anchor_date.weekday(),)
        if recurrence != "weekly" and clean_weekdays:
            raise ValueError("Los días de semana solo aplican a recurrencia semanal.")
        clean_interval = _interval(interval)
        clean_until = _optional_date(until)
        if clean_until and _parse_date(clean_until, "fecha final") < anchor_date:
            raise ValueError("La fecha final no puede ser anterior al inicio.")
        goal_id, task_id = self._resolve_links(goal_public_id, task_public_id)
        return {
            "public_id": uuid.uuid4().hex,
            "item_type": item_type,
            "title": _required(title, "título", 240),
            "description": _bounded(description, 2_000),
            "person_name": _bounded(person_name, 200),
            "domain": _bounded(domain, 100),
            "project": _bounded(project, 160),
            "priority": _priority(priority),
            "status": "active",
            "anchor_date": anchor_date.isoformat(),
            "time_of_day": _optional_time(event_time),
            "timezone": _timezone(timezone),
            "recurrence_kind": recurrence,
            "recurrence_interval": clean_interval,
            "weekdays_json": json.dumps(clean_weekdays),
            "recurrence_month": recurrence_month,
            "recurrence_day": recurrence_day,
            "recurrence_until": clean_until,
            "birth_year": birth_year,
            "goal_id": goal_id,
            "task_id": task_id,
            "created_by": _required(actor, "actor", 120),
            "created_at": _now(),
        }

    def _resolve_links(
        self,
        goal_public_id: str,
        task_public_id: str,
    ) -> tuple[int | None, int | None]:
        goal_id: int | None = None
        task_id: int | None = None
        with self.database.connect() as connection:
            if goal_public_id.strip():
                row = connection.execute(
                    "SELECT id FROM assistant_goals WHERE public_id = ?",
                    (goal_public_id.strip(),),
                ).fetchone()
                if row is None:
                    raise ValueError("Objetivo vinculado no encontrado.")
                goal_id = int(row["id"])
            if task_public_id.strip():
                row = connection.execute(
                    "SELECT id, goal_id FROM assistant_goal_tasks WHERE public_id = ?",
                    (task_public_id.strip(),),
                ).fetchone()
                if row is None:
                    raise ValueError("Tarea vinculada no encontrada.")
                task_id = int(row["id"])
                if goal_id is not None and int(row["goal_id"]) != goal_id:
                    raise ValueError("La tarea no pertenece al objetivo vinculado.")
                if goal_id is None:
                    goal_id = int(row["goal_id"])
        return goal_id, task_id

    def _insert_item(self, values: dict[str, Any]) -> dict[str, Any]:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_organizer_items(
                    public_id, item_type, title, description, person_name,
                    domain, project, priority, status, anchor_date,
                    time_of_day, timezone, recurrence_kind,
                    recurrence_interval, weekdays_json, recurrence_month,
                    recurrence_day, recurrence_until, birth_year, goal_id,
                    task_id, created_by, created_at, updated_at, completed_at
                ) VALUES (
                    :public_id, :item_type, :title, :description, :person_name,
                    :domain, :project, :priority, :status, :anchor_date,
                    :time_of_day, :timezone, :recurrence_kind,
                    :recurrence_interval, :weekdays_json, :recurrence_month,
                    :recurrence_day, :recurrence_until, :birth_year, :goal_id,
                    :task_id, :created_by, :created_at, :created_at, NULL
                )
                """,
                values,
            )
            row = connection.execute(
                "SELECT * FROM assistant_organizer_items WHERE public_id = ?",
                (values["public_id"],),
            ).fetchone()
        assert row is not None
        return _item_row(row)

    def list_items(
        self,
        *,
        item_type: str = "all",
        status: str = "all",
        domain: str = "",
        project: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        clean_type = item_type.strip().casefold()
        clean_status = status.strip().casefold()
        if clean_type != "all":
            if clean_type not in _ITEM_TYPES:
                raise ValueError("Tipo de organizador inválido.")
            clauses.append("i.item_type = ?")
            params.append(clean_type)
        if clean_status != "all":
            if clean_status not in _ITEM_STATUSES:
                raise ValueError("Estado de organizador inválido.")
            clauses.append("i.status = ?")
            params.append(clean_status)
        if domain.strip():
            clauses.append("i.domain = ?")
            params.append(_bounded(domain, 100))
        if project.strip():
            clauses.append("i.project = ?")
            params.append(_bounded(project, 160))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT i.*, g.public_id AS goal_public_id, "
                "t.public_id AS task_public_id "
                "FROM assistant_organizer_items i "
                "LEFT JOIN assistant_goals g ON g.id = i.goal_id "
                "LEFT JOIN assistant_goal_tasks t ON t.id = i.task_id "
                f"{where} ORDER BY i.anchor_date ASC, i.id ASC LIMIT ?",
                params,
            ).fetchall()
        return [_item_row(row) for row in rows]

    def item_details(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT i.*, g.public_id AS goal_public_id, "
                "t.public_id AS task_public_id "
                "FROM assistant_organizer_items i "
                "LEFT JOIN assistant_goals g ON g.id = i.goal_id "
                "LEFT JOIN assistant_goal_tasks t ON t.id = i.task_id "
                "WHERE i.public_id = ?",
                (public_id.strip(),),
            ).fetchone()
            if row is None:
                return None
            reminders = connection.execute(
                "SELECT * FROM assistant_organizer_reminders WHERE item_id = ? "
                "ORDER BY id ASC",
                (int(row["id"]),),
            ).fetchall()
            checkins = connection.execute(
                "SELECT * FROM assistant_routine_checkins WHERE routine_id = ? "
                "ORDER BY occurrence_date DESC LIMIT 100",
                (int(row["id"]),),
            ).fetchall()
        return {
            **_item_row(row),
            "reminders": [_reminder_row(item) for item in reminders],
            "checkins": [dict(item) for item in checkins],
        }

    def update_item_status(self, public_id: str, *, status: str) -> dict[str, Any]:
        item = self.item_details(public_id)
        if item is None:
            raise ValueError("Elemento del organizador no encontrado.")
        clean = status.strip().casefold()
        if clean not in _ITEM_STATUSES:
            raise ValueError("Estado de organizador inválido.")
        if item["item_type"] == "birthday" and clean not in {"active", "cancelled"}:
            raise ValueError("Un cumpleaños solo puede estar activo o cancelado.")
        if item["item_type"] == "commitment" and clean == "paused":
            raise ValueError("Un compromiso no admite estado pausado.")
        completed_at = _now() if clean == "completed" else None
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE assistant_organizer_items SET status = ?, updated_at = ?, "
                "completed_at = ? WHERE public_id = ?",
                (clean, _now(), completed_at, public_id.strip()),
            )
        updated = self.item_details(public_id)
        assert updated is not None
        return updated

    def checkin_routine(
        self,
        public_id: str,
        *,
        occurrence_date: str,
        status: str,
        note: str,
        actor: str,
    ) -> dict[str, Any]:
        item = self.item_details(public_id)
        if item is None or item["item_type"] != "routine":
            raise ValueError("Rutina no encontrada.")
        if item["status"] != "active":
            raise ValueError("La rutina no está activa.")
        target = _parse_date(occurrence_date, "fecha del check-in")
        if not occurs_on(item, target):
            raise ValueError("La rutina no tiene una ocurrencia en esa fecha.")
        clean_status = status.strip().casefold()
        if clean_status not in _CHECKIN_STATUSES:
            raise ValueError("Estado de check-in inválido.")
        now = _now()
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM assistant_routine_checkins "
                "WHERE routine_id = ? AND occurrence_date = ?",
                (int(item["id"]), target.isoformat()),
            ).fetchone()
            if existing is not None:
                raise ValueError("La rutina ya tiene un check-in para esa fecha.")
            public_checkin = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO assistant_routine_checkins(
                    public_id, routine_id, occurrence_date, status, note,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    public_checkin,
                    int(item["id"]),
                    target.isoformat(),
                    clean_status,
                    _bounded(note, 1_000),
                    _required(actor, "actor", 120),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM assistant_routine_checkins WHERE public_id = ?",
                (public_checkin,),
            ).fetchone()
        assert row is not None
        return dict(row)

    def propose_reminder(
        self,
        item_public_id: str,
        *,
        minutes_before: int,
        actor: str,
    ) -> dict[str, Any]:
        item = self.item_details(item_public_id)
        if item is None:
            raise ValueError("Elemento del organizador no encontrado.")
        if not 0 <= minutes_before <= _MAX_REMINDER_MINUTES:
            raise ValueError("El recordatorio debe estar entre 0 y 43200 minutos.")
        public_id = uuid.uuid4().hex
        now = _now()
        with self.database.connect() as connection:
            duplicate = connection.execute(
                "SELECT 1 FROM assistant_organizer_reminders "
                "WHERE item_id = ? AND minutes_before = ? "
                "AND status IN ('proposed', 'approved')",
                (int(item["id"]), minutes_before),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("Ya existe un recordatorio equivalente activo.")
            connection.execute(
                """
                INSERT INTO assistant_organizer_reminders(
                    public_id, item_id, minutes_before, status, created_by,
                    created_at, reviewed_by, reviewed_at
                ) VALUES (?, ?, ?, 'proposed', ?, ?, NULL, NULL)
                """,
                (
                    public_id,
                    int(item["id"]),
                    minutes_before,
                    _required(actor, "actor", 120),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM assistant_organizer_reminders WHERE public_id = ?",
                (public_id,),
            ).fetchone()
        assert row is not None
        return _reminder_row(row)

    def review_reminder(
        self,
        public_id: str,
        *,
        decision: str,
        actor: str,
    ) -> dict[str, Any]:
        clean = decision.strip().casefold()
        target_status = {"approve": "approved", "reject": "rejected"}.get(clean)
        if target_status is None:
            raise ValueError("Decisión de recordatorio inválida.")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_organizer_reminders WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
            if row is None:
                raise ValueError("Propuesta de recordatorio no encontrada.")
            if str(row["status"]) != "proposed":
                raise ValueError("La propuesta de recordatorio ya fue revisada.")
            now = _now()
            connection.execute(
                "UPDATE assistant_organizer_reminders SET status = ?, "
                "reviewed_by = ?, reviewed_at = ? WHERE id = ?",
                (
                    target_status,
                    _required(actor, "actor", 120),
                    now,
                    int(row["id"]),
                ),
            )
            updated = connection.execute(
                "SELECT * FROM assistant_organizer_reminders WHERE id = ?",
                (int(row["id"]),),
            ).fetchone()
        assert updated is not None
        return _reminder_row(updated)

    def list_reminders(
        self,
        *,
        status: str = "all",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clean = status.strip().casefold()
        if clean != "all" and clean not in _REMINDER_STATUSES:
            raise ValueError("Estado de recordatorio inválido.")
        where = "" if clean == "all" else "WHERE r.status = ?"
        params: list[Any] = [] if clean == "all" else [clean]
        params.append(max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT r.*, i.public_id AS item_public_id, i.title AS item_title "
                "FROM assistant_organizer_reminders r "
                "JOIN assistant_organizer_items i ON i.id = r.item_id "
                f"{where} ORDER BY r.id DESC LIMIT ?",
                params,
            ).fetchall()
        return [_reminder_row(row) for row in rows]

    def daily_brief(
        self,
        target_date: str,
        *,
        timezone: str = _DEFAULT_TIMEZONE,
        domain: str = "",
        project: str = "",
    ) -> dict[str, Any]:
        target = _parse_date(target_date, "fecha del resumen")
        clean_timezone = _timezone(timezone)
        items = self._active_items(domain=domain, project=project)
        checkins = self._checkins_for_date(target)
        scheduled: list[dict[str, Any]] = []
        overdue: list[dict[str, Any]] = []
        for item in items:
            if occurs_on(item, target):
                scheduled.append(
                    self._occurrence(item, target, checkins.get(item["public_id"]))
                )
            if (
                item["item_type"] == "commitment"
                and item["recurrence_kind"] == "once"
                and item["status"] == "active"
                and _parse_date(item["anchor_date"], "fecha") < target
            ):
                overdue.append(self._occurrence(item, target, None, overdue=True))
        scheduled.sort(key=_occurrence_sort_key)
        overdue.sort(key=_occurrence_sort_key)
        reminders = self._reminders_due(target, items, clean_timezone)
        return {
            "date": target.isoformat(),
            "timezone": clean_timezone,
            "domain": _bounded(domain, 100),
            "project": _bounded(project, 160),
            "scheduled": scheduled,
            "overdue": overdue,
            "reminders": reminders,
            "summary": {
                "scheduled": len(scheduled),
                "commitments": sum(
                    1 for item in scheduled if item["item_type"] == "commitment"
                ),
                "birthdays": sum(
                    1 for item in scheduled if item["item_type"] == "birthday"
                ),
                "routines": sum(
                    1 for item in scheduled if item["item_type"] == "routine"
                ),
                "overdue": len(overdue),
                "reminders": len(reminders),
            },
            "model_used": False,
            "network_access": False,
            "background_execution": False,
            "automatic_notifications": False,
        }

    def upcoming(
        self,
        *,
        start_date: str,
        days: int,
        timezone: str = _DEFAULT_TIMEZONE,
        domain: str = "",
        project: str = "",
    ) -> dict[str, Any]:
        start = _parse_date(start_date, "fecha inicial")
        if not 1 <= days <= 366:
            raise ValueError("La ventana debe estar entre 1 y 366 días.")
        clean_timezone = _timezone(timezone)
        end = start + timedelta(days=days - 1)
        items = self._active_items(domain=domain, project=project)
        checkins = self._checkins_for_range(start, end)
        entries: list[dict[str, Any]] = []
        for offset in range(days):
            target = start + timedelta(days=offset)
            for item in items:
                if not occurs_on(item, target):
                    continue
                checkin = checkins.get((item["public_id"], target.isoformat()))
                entries.append(self._occurrence(item, target, checkin))
                if len(entries) >= 500:
                    break
            if len(entries) >= 500:
                break
        entries.sort(key=lambda item: (item["date"], *_occurrence_sort_key(item)))
        return {
            "start_date": start.isoformat(),
            "days": days,
            "timezone": clean_timezone,
            "entries": entries,
            "truncated": len(entries) >= 500,
            "model_used": False,
            "background_execution": False,
        }

    def _active_items(self, *, domain: str, project: str) -> list[dict[str, Any]]:
        clauses = ["i.status = 'active'"]
        params: list[Any] = []
        if domain.strip():
            clauses.append("(i.domain = ? OR i.domain = '')")
            params.append(_bounded(domain, 100))
        if project.strip():
            clauses.append("(i.project = ? OR i.project = '')")
            params.append(_bounded(project, 160))
        else:
            clauses.append("i.project = ''")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT i.*, g.public_id AS goal_public_id, "
                "t.public_id AS task_public_id "
                "FROM assistant_organizer_items i "
                "LEFT JOIN assistant_goals g ON g.id = i.goal_id "
                "LEFT JOIN assistant_goal_tasks t ON t.id = i.task_id "
                "WHERE "
                + " AND ".join(clauses)
                + " ORDER BY i.id ASC LIMIT ?",
                (*params, _MAX_ITEMS),
            ).fetchall()
        return [_item_row(row) for row in rows]

    def _checkins_for_date(self, target: date) -> dict[str, dict[str, Any]]:
        rows = self._checkins_for_range(target, target)
        return {
            routine_id: item
            for (routine_id, occurrence_date), item in rows.items()
            if occurrence_date == target.isoformat()
        }

    def _checkins_for_range(
        self,
        start: date,
        end: date,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT c.*, i.public_id AS routine_public_id "
                "FROM assistant_routine_checkins c "
                "JOIN assistant_organizer_items i ON i.id = c.routine_id "
                "WHERE c.occurrence_date BETWEEN ? AND ?",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return {
            (str(row["routine_public_id"]), str(row["occurrence_date"])): dict(row)
            for row in rows
        }

    def _occurrence(
        self,
        item: dict[str, Any],
        target: date,
        checkin: dict[str, Any] | None,
        *,
        overdue: bool = False,
    ) -> dict[str, Any]:
        age: int | None = None
        if item["item_type"] == "birthday" and item.get("birth_year"):
            age = target.year - int(item["birth_year"])
        return {
            "item_id": item["public_id"],
            "item_type": item["item_type"],
            "title": item["title"],
            "description": item["description"],
            "date": (
                item["anchor_date"] if overdue else target.isoformat()
            ),
            "time": item["time_of_day"],
            "timezone": item["timezone"],
            "priority": item["priority"],
            "domain": item["domain"],
            "project": item["project"],
            "person_name": item["person_name"],
            "age": age,
            "overdue": overdue,
            "checkin": checkin,
            "goal_public_id": item.get("goal_public_id", ""),
            "task_public_id": item.get("task_public_id", ""),
        }

    def _reminders_due(
        self,
        target: date,
        items: list[dict[str, Any]],
        timezone: str,
    ) -> list[dict[str, Any]]:
        item_by_id = {int(item["id"]): item for item in items}
        if not item_by_id:
            return []
        placeholders = ",".join("?" for _ in item_by_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assistant_organizer_reminders "
                f"WHERE status = 'approved' AND item_id IN ({placeholders})",
                tuple(item_by_id),
            ).fetchall()
        due: list[dict[str, Any]] = []
        horizon = (_MAX_REMINDER_MINUTES // 1_440) + 2
        for row in rows:
            item = item_by_id[int(row["item_id"])]
            for offset in range(horizon + 1):
                occurrence_date = target + timedelta(days=offset)
                if not occurs_on(item, occurrence_date):
                    continue
                event_time = _parse_time(item["time_of_day"] or "09:00")
                event_at = datetime.combine(
                    occurrence_date,
                    event_time,
                    tzinfo=ZoneInfo(str(item["timezone"])),
                )
                reminder_at = event_at - timedelta(minutes=int(row["minutes_before"]))
                local_reminder = reminder_at.astimezone(ZoneInfo(timezone))
                if local_reminder.date() != target:
                    continue
                due.append(
                    {
                        "reminder_id": str(row["public_id"]),
                        "item_id": item["public_id"],
                        "title": item["title"],
                        "reminder_at": local_reminder.isoformat(),
                        "event_at": event_at.isoformat(),
                        "minutes_before": int(row["minutes_before"]),
                    }
                )
        due.sort(key=lambda item: item["reminder_at"])
        return due[:500]


def occurs_on(item: dict[str, Any], target: date) -> bool:
    if str(item.get("status")) != "active":
        return False
    anchor = _parse_date(str(item["anchor_date"]), "fecha")
    if target < anchor and str(item["item_type"]) != "birthday":
        return False
    until = item.get("recurrence_until")
    if until and target > _parse_date(str(until), "fecha final"):
        return False
    kind = str(item["recurrence_kind"])
    interval = max(1, int(item["recurrence_interval"]))
    if kind == "once":
        return target == anchor
    if kind == "daily":
        return (target - anchor).days % interval == 0
    if kind == "weekly":
        weekdays = tuple(int(value) for value in item.get("weekdays", ()))
        weeks = max(0, (target - anchor).days // 7)
        return target.weekday() in weekdays and weeks % interval == 0
    if kind == "monthly":
        months = (target.year - anchor.year) * 12 + target.month - anchor.month
        target_day = int(item.get("recurrence_day") or anchor.day)
        actual_day = min(target_day, monthrange(target.year, target.month)[1])
        return months >= 0 and months % interval == 0 and target.day == actual_day
    if kind == "yearly":
        year_delta = target.year - anchor.year
        month = int(item.get("recurrence_month") or anchor.month)
        day = int(item.get("recurrence_day") or anchor.day)
        actual_day = min(day, monthrange(target.year, month)[1])
        return (
            year_delta >= 0
            and year_delta % interval == 0
            and target.month == month
            and target.day == actual_day
        )
    return False


def organizer_query(text: str) -> dict[str, Any] | None:
    normalized = _normalize(text)
    today_terms = {
        "que tengo hoy",
        "agenda de hoy",
        "mi agenda de hoy",
        "resumen de hoy",
        "resumen diario",
        "como esta mi dia",
        "que hay para hoy",
    }
    tomorrow_terms = {
        "que tengo manana",
        "agenda de manana",
        "mi agenda de manana",
        "resumen de manana",
        "que hay para manana",
    }
    if normalized in today_terms:
        return {"kind": "daily_brief", "offset_days": 0}
    if normalized in tomorrow_terms:
        return {"kind": "daily_brief", "offset_days": 1}
    if normalized in {
        "proximos cumpleanos",
        "cumpleanos proximos",
        "que cumpleanos vienen",
    }:
        return {"kind": "upcoming_birthdays", "days": 60}
    return None


def render_daily_brief(brief: dict[str, Any]) -> str:
    target = _parse_date(str(brief["date"]), "fecha")
    lines = [
        f"Agenda del {_WEEKDAY_NAMES[target.weekday()]} {target.isoformat()}",
    ]
    scheduled = brief["scheduled"]
    if not scheduled:
        lines.append("- Sin compromisos, cumpleaños ni rutinas programadas.")
    for item in scheduled:
        marker = {
            "commitment": "Compromiso",
            "birthday": "Cumpleaños",
            "routine": "Rutina",
        }[item["item_type"]]
        when = f" a las {item['time']}" if item["time"] else ""
        extra = ""
        if item["item_type"] == "birthday" and item.get("age") is not None:
            extra = f" · cumple {item['age']} años"
        if item["item_type"] == "routine" and item.get("checkin"):
            extra = f" · {item['checkin']['status']}"
        lines.append(f"- {marker}{when}: {item['title']}{extra}")
    if brief["overdue"]:
        lines.append("Pendientes vencidos:")
        for item in brief["overdue"]:
            lines.append(f"- {item['title']} · desde {item['date']}")
    if brief["reminders"]:
        lines.append("Recordatorios aprobados para hoy:")
        for reminder in brief["reminders"]:
            local_time = str(reminder["reminder_at"])[11:16]
            lines.append(f"- {local_time}: {reminder['title']}")
    lines.append(
        "No se enviaron notificaciones ni se ejecutaron acciones en segundo plano."
    )
    return "\n".join(lines)


def local_today(timezone: str = _DEFAULT_TIMEZONE) -> date:
    return datetime.now(ZoneInfo(_timezone(timezone))).date()


def _item_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["weekdays"] = tuple(json.loads(str(data.pop("weekdays_json") or "[]")))
    data["goal_public_id"] = str(data.get("goal_public_id") or "")
    data["task_public_id"] = str(data.get("task_public_id") or "")
    return data


def _reminder_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["automatic_notification"] = False
    data["background_execution"] = False
    return data


def _occurrence_sort_key(item: dict[str, Any]) -> tuple[str, int, str]:
    priority_order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    return (
        str(item.get("time") or "99:99"),
        priority_order.get(str(item.get("priority")), 9),
        str(item.get("title", "")).casefold(),
    )


def _required(value: str, label: str, limit: int) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{label.capitalize()} no puede estar vacío.")
    if len(clean) > limit:
        raise ValueError(f"{label.capitalize()} supera {limit} caracteres.")
    return clean


def _bounded(value: str, limit: int) -> str:
    clean = value.strip()
    if len(clean) > limit:
        raise ValueError(f"El valor supera {limit} caracteres.")
    return clean


def _priority(value: str) -> str:
    clean = value.strip().casefold()
    if clean not in _PRIORITIES:
        raise ValueError("Prioridad inválida.")
    return clean


def _recurrence(value: str, *, allow_yearly: bool) -> str:
    clean = value.strip().casefold()
    allowed = _RECURRENCES if allow_yearly else _RECURRENCES[:-1]
    if clean not in allowed:
        raise ValueError("Recurrencia inválida.")
    return clean


def _interval(value: int) -> int:
    if not 1 <= int(value) <= 365:
        raise ValueError("El intervalo debe estar entre 1 y 365.")
    return int(value)


def _weekdays(values: tuple[str, ...]) -> tuple[int, ...]:
    result: list[int] = []
    for value in values:
        normalized = _normalize(value)
        if normalized not in _WEEKDAY_ALIASES:
            raise ValueError(f"Día de semana inválido: {value}")
        result.append(_WEEKDAY_ALIASES[normalized])
    return tuple(sorted(set(result)))


def _timezone(value: str) -> str:
    clean = value.strip() or _DEFAULT_TIMEZONE
    try:
        ZoneInfo(clean)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Zona horaria inválida: {clean}") from exc
    return clean


def _parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label.capitalize()} debe usar YYYY-MM-DD.") from exc


def _optional_date(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return _parse_date(value, "fecha").isoformat()


def _parse_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError("La hora debe usar HH:MM.") from exc
    return parsed.replace(second=0, microsecond=0)


def _optional_time(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return _parse_time(value).strftime("%H:%M")


def _normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", ascii_text).strip(" ¿?¡!.,:;")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def render_upcoming_birthdays(data: dict[str, Any]) -> str:
    birthdays = [
        item for item in data["entries"] if item["item_type"] == "birthday"
    ]
    if not birthdays:
        return "No hay cumpleaños activos en la ventana consultada."
    lines = [f"Próximos cumpleaños ({data['days']} días):"]
    for item in birthdays:
        extra = f" · cumple {item['age']} años" if item.get("age") is not None else ""
        lines.append(f"- {item['date']}: {item['person_name']}{extra}")
    lines.append("No se enviaron notificaciones ni se ejecutaron acciones.")
    return "\n".join(lines)
