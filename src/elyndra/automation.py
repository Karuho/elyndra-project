from __future__ import annotations

import json
import uuid
from calendar import monthrange
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from elyndra.cognitive_executive import CognitiveExecutiveRepository
from elyndra.db import Database
from elyndra.personal_organizer import PersonalOrganizerRepository, render_daily_brief
from elyndra.wellbeing import WellbeingRepository, render_wellbeing_summary

_AUTONOMY_LEVELS = (
    "observe",
    "suggest",
    "prepare",
    "execute_with_approval",
    "execute_under_policy",
    "forbidden",
)
_POLICY_STATUSES = ("active", "paused", "revoked", "expired")
_AUTOMATION_STATUSES = ("active", "paused", "completed", "cancelled")
_RUN_STATUSES = (
    "pending_approval",
    "observed",
    "suggested",
    "prepared",
    "executed",
    "skipped",
    "failed",
)
_INBOX_STATUSES = ("unread", "read", "dismissed")
_SCHEDULE_KINDS = ("once", "daily", "weekly", "monthly")
_ACTION_TYPES = (
    "daily_brief.prepare",
    "organizer.upcoming.prepare",
    "wellbeing.weekly_summary.prepare",
    "coaching.review.prepare",
    "goal.review.prepare",
    "routine.missed_checkin.suggest",
)
_LOW_RISK_ACTIONS = frozenset(_ACTION_TYPES)
_DEFAULT_TIMEZONE = "America/Santiago"
_MAX_POLICIES = 500
_MAX_AUTOMATIONS = 1_000
_MAX_SCAN_AUTOMATIONS = 200
_MAX_CATCHUP_DAYS = 7
_MAX_RUNS_PER_DAY = 48
_MAX_RESULT_CHARS = 8_000
_MAX_INBOX_BODY_CHARS = 6_000
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
    "miércoles": 2,
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
    "sábado": 5,
    "sab": 5,
    "sun": 6,
    "sunday": 6,
    "domingo": 6,
    "dom": 6,
}


class AutomationRepository:
    """Foreground policy-bounded automations with a local inbox."""

    def __init__(
        self,
        database: Database,
        organizer: PersonalOrganizerRepository,
        wellbeing: WellbeingRepository,
        executive: CognitiveExecutiveRepository,
    ) -> None:
        self.database = database
        self.organizer = organizer
        self.wellbeing = wellbeing
        self.executive = executive

    def status(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            active_policies = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_automation_policies "
                    "WHERE status = 'active'"
                ).fetchone()[0]
            )
            active_automations = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_automations "
                    "WHERE status = 'active'"
                ).fetchone()[0]
            )
            pending_runs = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_automation_runs "
                    "WHERE status = 'pending_approval'"
                ).fetchone()[0]
            )
            unread = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_local_inbox "
                    "WHERE status = 'unread'"
                ).fetchone()[0]
            )
        return {
            "enabled": True,
            "active_policies": active_policies,
            "active_automations": active_automations,
            "pending_approval_runs": pending_runs,
            "unread_inbox": unread,
            "foreground_dispatch_only": True,
            "background_execution": False,
            "network_actions": False,
            "external_notifications": False,
            "skills_allowed": False,
            "file_writes_allowed": False,
            "automatic_policy_expansion": False,
            "idempotent_occurrences": True,
            "policy_levels": list(_AUTONOMY_LEVELS),
            "action_types": list(_ACTION_TYPES),
        }

    def create_policy(
        self,
        *,
        title: str,
        action_type: str,
        autonomy_level: str,
        timezone: str = _DEFAULT_TIMEZONE,
        window_start: str | None = None,
        window_end: str | None = None,
        max_runs_per_day: int = 1,
        starts_at: str | None = None,
        expires_at: str | None = None,
        domain: str = "",
        project: str = "",
        actor: str,
    ) -> dict[str, Any]:
        clean_action = _choice(action_type, _ACTION_TYPES, "acción")
        clean_level = _choice(autonomy_level, _AUTONOMY_LEVELS, "nivel de autonomía")
        if clean_level == "forbidden":
            raise ValueError("Una política forbidden no puede activarse.")
        if clean_action not in _LOW_RISK_ACTIONS:
            raise ValueError("La acción no está permitida para automatización local.")
        zone = _timezone(timezone)
        start_time = _optional_time(window_start, "inicio de ventana")
        end_time = _optional_time(window_end, "fin de ventana")
        if (start_time is None) != (end_time is None):
            raise ValueError("La ventana requiere hora inicial y final.")
        if start_time is not None and start_time >= end_time:
            raise ValueError("La ventana debe comenzar antes de terminar.")
        daily_limit = max(1, min(int(max_runs_per_day), _MAX_RUNS_PER_DAY))
        start_iso = _optional_datetime(starts_at, zone, "inicio de política")
        expiry_iso = _optional_datetime(expires_at, zone, "vencimiento de política")
        if start_iso and expiry_iso and start_iso >= expiry_iso:
            raise ValueError("El vencimiento debe ser posterior al inicio.")
        with self.database.connect() as connection:
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_automation_policies"
                ).fetchone()[0]
            )
            if count >= _MAX_POLICIES:
                raise ValueError("Se alcanzó el límite de políticas de automatización.")
            public_id = uuid.uuid4().hex
            now = _now()
            connection.execute(
                """
                INSERT INTO assistant_automation_policies(
                    public_id, title, action_type, autonomy_level, status,
                    timezone, window_start, window_end, max_runs_per_day,
                    starts_at, expires_at, domain, project, created_by,
                    created_at, updated_at, reviewed_at
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    public_id,
                    _required(title, "título", 200),
                    clean_action,
                    clean_level,
                    zone.key,
                    start_time.isoformat(timespec="minutes") if start_time else None,
                    end_time.isoformat(timespec="minutes") if end_time else None,
                    daily_limit,
                    start_iso,
                    expiry_iso,
                    _bounded(domain, 100),
                    _bounded(project, 160),
                    actor,
                    now,
                    now,
                    now,
                ),
            )
        item = self.policy_details(public_id)
        if item is None:
            raise RuntimeError("No fue posible recuperar la política creada.")
        return item

    def update_policy_status(self, policy_id: str, *, status: str) -> dict[str, Any]:
        clean = _choice(status, _POLICY_STATUSES, "estado de política")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id FROM assistant_automation_policies WHERE public_id = ?",
                (policy_id.strip(),),
            ).fetchone()
            if row is None:
                raise ValueError("Política de automatización no encontrada.")
            connection.execute(
                "UPDATE assistant_automation_policies SET status = ?, updated_at = ? "
                "WHERE id = ?",
                (clean, _now(), int(row["id"])),
            )
        item = self.policy_details(policy_id)
        if item is None:
            raise RuntimeError("No fue posible recuperar la política actualizada.")
        return item

    def policy_details(self, policy_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_automation_policies WHERE public_id = ?",
                (policy_id.strip(),),
            ).fetchone()
        return _public_policy(row) if row is not None else None

    def list_policies(self, *, status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
        clean = status.strip().casefold()
        if clean != "all" and clean not in _POLICY_STATUSES:
            raise ValueError("Estado de política inválido.")
        where = "" if clean == "all" else "WHERE status = ?"
        params: tuple[Any, ...] = (
            (max(1, min(limit, 500)),)
            if clean == "all"
            else (clean, max(1, min(limit, 500)))
        )
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assistant_automation_policies "
                f"{where} ORDER BY updated_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return [_public_policy(row) for row in rows]

    def create_automation(
        self,
        policy_id: str,
        *,
        title: str,
        schedule_kind: str,
        start_date: str,
        time_of_day: str,
        weekdays: tuple[str, ...] = (),
        month_day: int | None = None,
        interval: int = 1,
        until_date: str | None = None,
        params: dict[str, Any] | None = None,
        actor: str,
    ) -> dict[str, Any]:
        policy = self.policy_details(policy_id)
        if policy is None:
            raise ValueError("Política de automatización no encontrada.")
        if policy["status"] != "active":
            raise ValueError("La política debe estar activa.")
        clean_schedule = _choice(schedule_kind, _SCHEDULE_KINDS, "recurrencia")
        anchor = _date(start_date, "fecha de inicio")
        run_time = _time(time_of_day, "hora")
        clean_weekdays = _weekdays(weekdays)
        day = month_day
        if clean_schedule == "weekly" and not clean_weekdays:
            clean_weekdays = (anchor.weekday(),)
        if clean_schedule != "weekly" and clean_weekdays:
            raise ValueError("Los días de semana solo aplican a recurrencia semanal.")
        if clean_schedule == "monthly":
            day = int(day or anchor.day)
            if not 1 <= day <= 31:
                raise ValueError("Día mensual inválido.")
        elif day is not None:
            raise ValueError("month_day solo aplica a recurrencia mensual.")
        clean_interval = max(1, min(int(interval), 365))
        end = _date(until_date, "fecha final") if until_date else None
        if end is not None and end < anchor:
            raise ValueError("La fecha final no puede preceder al inicio.")
        clean_params = _params(params or {})
        with self.database.connect() as connection:
            count = int(
                connection.execute("SELECT COUNT(*) FROM assistant_automations").fetchone()[0]
            )
            if count >= _MAX_AUTOMATIONS:
                raise ValueError("Se alcanzó el límite de automatizaciones.")
            policy_row = connection.execute(
                "SELECT id FROM assistant_automation_policies WHERE public_id = ?",
                (policy_id.strip(),),
            ).fetchone()
            if policy_row is None:
                raise ValueError("Política de automatización no encontrada.")
            public_id = uuid.uuid4().hex
            now = _now()
            connection.execute(
                """
                INSERT INTO assistant_automations(
                    public_id, policy_id, title, schedule_kind, start_date,
                    time_of_day, weekdays_json, month_day, schedule_interval,
                    until_date, action_params_json, status, created_by,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    public_id,
                    int(policy_row["id"]),
                    _required(title, "título", 200),
                    clean_schedule,
                    anchor.isoformat(),
                    run_time.isoformat(timespec="minutes"),
                    json.dumps(list(clean_weekdays), ensure_ascii=False),
                    day,
                    clean_interval,
                    end.isoformat() if end else None,
                    json.dumps(clean_params, ensure_ascii=False, sort_keys=True),
                    actor,
                    now,
                    now,
                ),
            )
        item = self.automation_details(public_id)
        if item is None:
            raise RuntimeError("No fue posible recuperar la automatización creada.")
        return item

    def update_automation_status(
        self, automation_id: str, *, status: str
    ) -> dict[str, Any]:
        clean = _choice(status, _AUTOMATION_STATUSES, "estado de automatización")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id FROM assistant_automations WHERE public_id = ?",
                (automation_id.strip(),),
            ).fetchone()
            if row is None:
                raise ValueError("Automatización no encontrada.")
            connection.execute(
                "UPDATE assistant_automations SET status = ?, updated_at = ? WHERE id = ?",
                (clean, _now(), int(row["id"])),
            )
        item = self.automation_details(automation_id)
        if item is None:
            raise RuntimeError("No fue posible recuperar la automatización actualizada.")
        return item

    def automation_details(self, automation_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT a.*, p.public_id AS policy_public_id, p.action_type,
                       p.autonomy_level, p.status AS policy_status, p.timezone,
                       p.window_start, p.window_end, p.max_runs_per_day,
                       p.starts_at, p.expires_at, p.domain, p.project
                FROM assistant_automations a
                JOIN assistant_automation_policies p ON p.id = a.policy_id
                WHERE a.public_id = ?
                """,
                (automation_id.strip(),),
            ).fetchone()
        return _public_automation(row) if row is not None else None

    def list_automations(
        self, *, status: str = "all", limit: int = 100
    ) -> list[dict[str, Any]]:
        clean = status.strip().casefold()
        if clean != "all" and clean not in _AUTOMATION_STATUSES:
            raise ValueError("Estado de automatización inválido.")
        where = "" if clean == "all" else "WHERE a.status = ?"
        params: tuple[Any, ...] = (
            (max(1, min(limit, 500)),)
            if clean == "all"
            else (clean, max(1, min(limit, 500)))
        )
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*, p.public_id AS policy_public_id, p.action_type,
                       p.autonomy_level, p.status AS policy_status, p.timezone,
                       p.window_start, p.window_end, p.max_runs_per_day,
                       p.starts_at, p.expires_at, p.domain, p.project
                FROM assistant_automations a
                JOIN assistant_automation_policies p ON p.id = a.policy_id
                """
                f"{where} ORDER BY a.updated_at DESC, a.id DESC LIMIT ?",
                params,
            ).fetchall()
        return [_public_automation(row) for row in rows]

    def scan_due(
        self,
        *,
        now_value: str | None = None,
        actor: str,
    ) -> dict[str, Any]:
        automations = self.list_automations(status="active", limit=_MAX_SCAN_AUTOMATIONS)
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for automation in automations:
            zone = _timezone(str(automation["timezone"]))
            now = _parse_now(now_value, zone)
            for occurrence in _due_occurrences(automation, now):
                outcome = self._materialize_occurrence(
                    automation, occurrence=occurrence, actor=actor
                )
                if outcome is None:
                    continue
                if outcome["status"] == "skipped":
                    skipped.append(outcome)
                else:
                    created.append(outcome)
        return {
            "now": now_value or _now(),
            "runs": created,
            "skipped": skipped,
            "summary": {
                "created": len(created),
                "pending_approval": sum(
                    1 for item in created if item["status"] == "pending_approval"
                ),
                "prepared_or_executed": sum(
                    1
                    for item in created
                    if item["status"] in {"observed", "suggested", "prepared", "executed"}
                ),
                "skipped": len(skipped),
            },
            "foreground_dispatch": True,
            "background_execution": False,
        }

    def approve_run(self, run_id: str, *, actor: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT r.*, a.public_id AS automation_public_id,
                       a.action_params_json, p.action_type, p.autonomy_level,
                       p.domain, p.project, p.timezone
                FROM assistant_automation_runs r
                JOIN assistant_automations a ON a.id = r.automation_id
                JOIN assistant_automation_policies p ON p.id = a.policy_id
                WHERE r.public_id = ?
                """,
                (run_id.strip(),),
            ).fetchone()
        if row is None:
            raise ValueError("Ejecución de automatización no encontrada.")
        if str(row["status"]) != "pending_approval":
            raise ValueError("La ejecución no está pendiente de aprobación.")
        if str(row["autonomy_level"]) != "execute_with_approval":
            raise ValueError("La ejecución no requiere aprobación individual.")
        result = self._execute_action(
            action_type=str(row["action_type"]),
            params=_json_object(row["action_params_json"]),
            occurrence_at=str(row["occurrence_at"]),
            domain=str(row["domain"]),
            project=str(row["project"]),
        )
        final_status = "executed"
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE assistant_automation_runs
                SET status = ?, started_at = ?, finished_at = ?, result_json = ?,
                    approved_by = ?, approved_at = ?, verification_status = ?
                WHERE public_id = ? AND status = 'pending_approval'
                """,
                (
                    final_status,
                    _now(),
                    _now(),
                    _result_json(result),
                    actor,
                    _now(),
                    "success",
                    run_id.strip(),
                ),
            )
            self._insert_inbox(connection, run_id, result)
        item = self.run_details(run_id)
        if item is None:
            raise RuntimeError("No fue posible recuperar la ejecución aprobada.")
        return item

    def run_details(self, run_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT r.*, a.public_id AS automation_public_id, a.title,
                       p.public_id AS policy_public_id, p.action_type,
                       p.autonomy_level
                FROM assistant_automation_runs r
                JOIN assistant_automations a ON a.id = r.automation_id
                JOIN assistant_automation_policies p ON p.id = a.policy_id
                WHERE r.public_id = ?
                """,
                (run_id.strip(),),
            ).fetchone()
        return _public_run(row) if row is not None else None

    def list_runs(self, *, status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
        clean = status.strip().casefold()
        if clean != "all" and clean not in _RUN_STATUSES:
            raise ValueError("Estado de ejecución inválido.")
        where = "" if clean == "all" else "WHERE r.status = ?"
        params: tuple[Any, ...] = (
            (max(1, min(limit, 500)),)
            if clean == "all"
            else (clean, max(1, min(limit, 500)))
        )
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.*, a.public_id AS automation_public_id, a.title,
                       p.public_id AS policy_public_id, p.action_type,
                       p.autonomy_level
                FROM assistant_automation_runs r
                JOIN assistant_automations a ON a.id = r.automation_id
                JOIN assistant_automation_policies p ON p.id = a.policy_id
                """
                f"{where} ORDER BY r.created_at DESC, r.id DESC LIMIT ?",
                params,
            ).fetchall()
        return [_public_run(row) for row in rows]

    def list_inbox(self, *, status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
        clean = status.strip().casefold()
        if clean != "all" and clean not in _INBOX_STATUSES:
            raise ValueError("Estado de bandeja inválido.")
        where = "" if clean == "all" else "WHERE i.status = ?"
        params: tuple[Any, ...] = (
            (max(1, min(limit, 500)),)
            if clean == "all"
            else (clean, max(1, min(limit, 500)))
        )
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT i.*, r.public_id AS run_public_id,
                       a.public_id AS automation_public_id, a.title AS automation_title
                FROM assistant_local_inbox i
                JOIN assistant_automation_runs r ON r.id = i.run_id
                JOIN assistant_automations a ON a.id = r.automation_id
                """
                f"{where} ORDER BY i.visible_at DESC, i.id DESC LIMIT ?",
                params,
            ).fetchall()
        return [_public_inbox(row) for row in rows]

    def update_inbox_status(self, inbox_id: str, *, status: str) -> dict[str, Any]:
        clean = _choice(status, _INBOX_STATUSES, "estado de bandeja")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id FROM assistant_local_inbox WHERE public_id = ?",
                (inbox_id.strip(),),
            ).fetchone()
            if row is None:
                raise ValueError("Elemento de bandeja no encontrado.")
            connection.execute(
                "UPDATE assistant_local_inbox SET status = ?, updated_at = ? WHERE id = ?",
                (clean, _now(), int(row["id"])),
            )
            updated = connection.execute(
                """
                SELECT i.*, r.public_id AS run_public_id,
                       a.public_id AS automation_public_id, a.title AS automation_title
                FROM assistant_local_inbox i
                JOIN assistant_automation_runs r ON r.id = i.run_id
                JOIN assistant_automations a ON a.id = r.automation_id
                WHERE i.id = ?
                """,
                (int(row["id"]),),
            ).fetchone()
        return _public_inbox(updated)

    def render_overview(self) -> str:
        status = self.status()
        policies = self.list_policies(status="active", limit=20)
        automations = self.list_automations(status="active", limit=20)
        lines = [
            "Automatización supervisada local",
            f"- Políticas activas: {status['active_policies']}",
            f"- Automatizaciones activas: {status['active_automations']}",
            f"- Ejecuciones pendientes de aprobación: {status['pending_approval_runs']}",
            f"- Bandeja local sin leer: {status['unread_inbox']}",
            "- Segundo plano: no",
            "- Red, skills y escritura de archivos: no",
        ]
        for item in automations[:5]:
            lines.append(
                f"- {item['public_id']} · {item['schedule_kind']} · "
                f"{item['action_type']} · {item['title']}"
            )
        if policies and not automations:
            lines.append("- Hay políticas activas, pero aún no hay automatizaciones.")
        return "\n".join(lines)

    def _materialize_occurrence(
        self,
        automation: dict[str, Any],
        *,
        occurrence: datetime,
        actor: str,
    ) -> dict[str, Any] | None:
        occurrence_key = occurrence.isoformat(timespec="minutes")
        with self.database.connect() as connection:
            exists = connection.execute(
                """
                SELECT r.public_id
                FROM assistant_automation_runs r
                JOIN assistant_automations a ON a.id = r.automation_id
                WHERE a.public_id = ? AND r.occurrence_key = ?
                """,
                (automation["public_id"], occurrence_key),
            ).fetchone()
            if exists is not None:
                return None
        skip_reason = self._policy_skip_reason(automation, occurrence)
        if skip_reason:
            return self._insert_run(
                automation,
                occurrence=occurrence,
                status="skipped",
                result={"reason": skip_reason},
                actor=actor,
                verification_status="inconclusive",
            )
        level = str(automation["autonomy_level"])
        if level == "execute_with_approval":
            return self._insert_run(
                automation,
                occurrence=occurrence,
                status="pending_approval",
                result={"reason": "requires_per_run_approval"},
                actor=actor,
                verification_status="pending",
            )
        try:
            result = self._execute_action(
                action_type=str(automation["action_type"]),
                params=dict(automation.get("action_params", {})),
                occurrence_at=occurrence.isoformat(),
                domain=str(automation["domain"]),
                project=str(automation["project"]),
            )
            status = {
                "observe": "observed",
                "suggest": "suggested",
                "prepare": "prepared",
                "execute_under_policy": "executed",
            }.get(level)
            if status is None:
                raise ValueError("Nivel de autonomía no ejecutable.")
            item = self._insert_run(
                automation,
                occurrence=occurrence,
                status=status,
                result=result,
                actor=actor,
                verification_status="success",
            )
            if status in {"suggested", "prepared", "executed"}:
                with self.database.connect() as connection:
                    self._insert_inbox(connection, str(item["public_id"]), result)
                item = self.run_details(str(item["public_id"])) or item
            return item
        except Exception as exc:
            return self._insert_run(
                automation,
                occurrence=occurrence,
                status="failed",
                result={"error": str(exc)[:1_000]},
                actor=actor,
                verification_status="failed",
            )

    def _policy_skip_reason(
        self, automation: dict[str, Any], occurrence: datetime
    ) -> str:
        if automation["policy_status"] != "active":
            return "policy_not_active"
        if automation["status"] != "active":
            return "automation_not_active"
        starts_at = automation.get("starts_at")
        expires_at = automation.get("expires_at")
        if starts_at and occurrence < datetime.fromisoformat(str(starts_at)):
            return "policy_not_started"
        if expires_at and occurrence > datetime.fromisoformat(str(expires_at)):
            return "policy_expired"
        window_start = automation.get("window_start")
        window_end = automation.get("window_end")
        if window_start and window_end:
            current = occurrence.timetz().replace(tzinfo=None)
            if current < time.fromisoformat(str(window_start)):
                return "outside_policy_window"
            if current > time.fromisoformat(str(window_end)):
                return "outside_policy_window"
        local_day = occurrence.date().isoformat()
        with self.database.connect() as connection:
            count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM assistant_automation_runs r
                    JOIN assistant_automations a ON a.id = r.automation_id
                    WHERE a.policy_id = (
                        SELECT policy_id FROM assistant_automations WHERE public_id = ?
                    ) AND substr(r.occurrence_at, 1, 10) = ?
                    AND r.status NOT IN ('skipped', 'failed')
                    """,
                    (automation["public_id"], local_day),
                ).fetchone()[0]
            )
        if count >= int(automation["max_runs_per_day"]):
            return "daily_policy_limit"
        return ""

    def _execute_action(
        self,
        *,
        action_type: str,
        params: dict[str, Any],
        occurrence_at: str,
        domain: str,
        project: str,
    ) -> dict[str, Any]:
        occurrence = datetime.fromisoformat(occurrence_at)
        target_date = occurrence.date().isoformat()
        if action_type == "daily_brief.prepare":
            data = self.organizer.daily_brief(
                target_date,
                timezone=_occurrence_timezone(occurrence),
                domain=domain,
                project=project,
            )
            return {
                "title": f"Agenda del {target_date}",
                "body": render_daily_brief(data),
                "action_type": action_type,
                "data": data,
            }
        if action_type == "organizer.upcoming.prepare":
            days = max(1, min(int(params.get("days", 7)), 31))
            data = self.organizer.upcoming(
                start_date=target_date,
                days=days,
                timezone=_occurrence_timezone(occurrence),
                domain=domain,
                project=project,
            )
            lines = [
                f"- {item['date']} {item['time'] or ''} · {item['title']}"
                for item in data["entries"]
            ]
            return {
                "title": f"Próximos {days} días",
                "body": "\n".join(lines) or "Sin elementos próximos.",
                "action_type": action_type,
                "data": data,
            }
        if action_type == "wellbeing.weekly_summary.prepare":
            days = max(1, min(int(params.get("days", 7)), 31))
            data = self.wellbeing.summary(days=days, end_date=target_date)
            return {
                "title": "Resumen de bienestar",
                "body": render_wellbeing_summary(data),
                "action_type": action_type,
                "data": data,
            }
        if action_type == "coaching.review.prepare":
            plans = self.wellbeing.list_plans(status="active", limit=100)
            due = [
                item
                for item in plans
                if not item.get("review_date") or str(item["review_date"]) <= target_date
            ]
            lines = [
                (
                    f"- {item['public_id']} · {item['title']} · revisión "
                    f"{item.get('review_date') or 'sin fecha'}"
                )
                for item in due
            ]
            return {
                "title": "Revisión de coaching",
                "body": "\n".join(lines) or "Sin planes activos para revisar.",
                "action_type": action_type,
                "data": {"plans": due, "date": target_date},
            }
        if action_type == "goal.review.prepare":
            goals = self.executive.list_goals(status="active", limit=100)
            lines = [
                f"- {item['public_id']} · {item['priority']} · {item['title']}"
                for item in goals
            ]
            return {
                "title": "Revisión de objetivos",
                "body": "\n".join(lines) or "Sin objetivos activos.",
                "action_type": action_type,
                "data": {"goals": goals, "date": target_date},
            }
        if action_type == "routine.missed_checkin.suggest":
            data = self.organizer.daily_brief(
                target_date,
                timezone=_occurrence_timezone(occurrence),
                domain=domain,
                project=project,
            )
            routines = [
                item
                for item in data.get("entries", [])
                if item.get("item_type") == "routine"
                and item.get("checkin_status") not in {"completed", "skipped"}
            ]
            lines = [f"- {item['title']}" for item in routines]
            return {
                "title": "Rutinas sin check-in",
                "body": "\n".join(lines) or "No hay rutinas pendientes de check-in.",
                "action_type": action_type,
                "data": {"routines": routines, "date": target_date},
            }
        raise ValueError("Acción de automatización no soportada.")

    def _insert_run(
        self,
        automation: dict[str, Any],
        *,
        occurrence: datetime,
        status: str,
        result: dict[str, Any],
        actor: str,
        verification_status: str,
    ) -> dict[str, Any]:
        public_id = uuid.uuid4().hex
        occurrence_key = occurrence.isoformat(timespec="minutes")
        now = _now()
        with self.database.connect() as connection:
            automation_row = connection.execute(
                "SELECT id FROM assistant_automations WHERE public_id = ?",
                (automation["public_id"],),
            ).fetchone()
            if automation_row is None:
                raise ValueError("Automatización no encontrada.")
            try:
                connection.execute(
                    """
                    INSERT INTO assistant_automation_runs(
                        public_id, automation_id, occurrence_key, occurrence_at,
                        status, created_by, created_at, started_at, finished_at,
                        result_json, verification_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        public_id,
                        int(automation_row["id"]),
                        occurrence_key,
                        occurrence.isoformat(),
                        status,
                        actor,
                        now,
                        None if status == "pending_approval" else now,
                        None if status == "pending_approval" else now,
                        _result_json(result),
                        verification_status,
                    ),
                )
            except Exception:
                existing = connection.execute(
                    """
                    SELECT r.public_id
                    FROM assistant_automation_runs r
                    WHERE r.automation_id = ? AND r.occurrence_key = ?
                    """,
                    (int(automation_row["id"]), occurrence_key),
                ).fetchone()
                if existing is not None:
                    item = self.run_details(str(existing["public_id"]))
                    if item is not None:
                        return item
                raise
        item = self.run_details(public_id)
        if item is None:
            raise RuntimeError("No fue posible recuperar la ejecución creada.")
        return item

    def _insert_inbox(
        self,
        connection: Any,
        run_public_id: str,
        result: dict[str, Any],
    ) -> None:
        run = connection.execute(
            "SELECT id FROM assistant_automation_runs WHERE public_id = ?",
            (run_public_id,),
        ).fetchone()
        if run is None:
            raise ValueError("Ejecución no encontrada para la bandeja.")
        existing = connection.execute(
            "SELECT id FROM assistant_local_inbox WHERE run_id = ?",
            (int(run["id"]),),
        ).fetchone()
        if existing is not None:
            return
        now = _now()
        connection.execute(
            """
            INSERT INTO assistant_local_inbox(
                public_id, run_id, title, body, status,
                visible_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'unread', ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                int(run["id"]),
                _bounded(str(result.get("title", "Resultado de automatización")), 200),
                _bounded(str(result.get("body", "")), _MAX_INBOX_BODY_CHARS),
                now,
                now,
                now,
            ),
        )


def automation_query(text: str) -> bool:
    clean = _fold(text)
    terms = (
        "automatizaciones",
        "automatizacion",
        "automatización",
        "politicas de automatizacion",
        "políticas de automatización",
        "bandeja local",
        "que preparo elyndra",
        "qué preparó elyndra",
    )
    return any(term in clean for term in terms)


def _due_occurrences(automation: dict[str, Any], now: datetime) -> list[datetime]:
    start = date.fromisoformat(str(automation["start_date"]))
    until = (
        date.fromisoformat(str(automation["until_date"]))
        if automation.get("until_date")
        else None
    )
    floor = max(start, now.date() - timedelta(days=_MAX_CATCHUP_DAYS))
    dates: list[date] = []
    current = floor
    while current <= now.date():
        if until is not None and current > until:
            break
        if _date_matches(automation, current, start):
            dates.append(current)
        current += timedelta(days=1)
    zone = _timezone(str(automation["timezone"]))
    run_time = time.fromisoformat(str(automation["time_of_day"]))
    occurrences = [datetime.combine(day, run_time, zone) for day in dates]
    return [item for item in occurrences if item <= now]


def _date_matches(automation: dict[str, Any], candidate: date, start: date) -> bool:
    kind = str(automation["schedule_kind"])
    interval = int(automation["schedule_interval"])
    if candidate < start:
        return False
    if kind == "once":
        return candidate == start
    if kind == "daily":
        return (candidate - start).days % interval == 0
    if kind == "weekly":
        weekdays = tuple(int(value) for value in automation.get("weekdays", ()))
        weeks = (candidate - start).days // 7
        return weeks % interval == 0 and candidate.weekday() in weekdays
    if kind == "monthly":
        months = (candidate.year - start.year) * 12 + candidate.month - start.month
        target_day = min(
            int(automation["month_day"]),
            monthrange(candidate.year, candidate.month)[1],
        )
        return months % interval == 0 and candidate.day == target_day
    return False


def _public_policy(row: Any) -> dict[str, Any]:
    return {
        "public_id": str(row["public_id"]),
        "title": str(row["title"]),
        "action_type": str(row["action_type"]),
        "autonomy_level": str(row["autonomy_level"]),
        "status": str(row["status"]),
        "timezone": str(row["timezone"]),
        "window_start": row["window_start"],
        "window_end": row["window_end"],
        "max_runs_per_day": int(row["max_runs_per_day"]),
        "starts_at": row["starts_at"],
        "expires_at": row["expires_at"],
        "domain": str(row["domain"]),
        "project": str(row["project"]),
        "created_by": str(row["created_by"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "reviewed_at": str(row["reviewed_at"]),
        "background_execution": False,
        "external_authority": False,
    }


def _public_automation(row: Any) -> dict[str, Any]:
    data = {
        "public_id": str(row["public_id"]),
        "policy_public_id": str(row["policy_public_id"]),
        "title": str(row["title"]),
        "schedule_kind": str(row["schedule_kind"]),
        "start_date": str(row["start_date"]),
        "time_of_day": str(row["time_of_day"]),
        "weekdays": _json_list(row["weekdays_json"]),
        "month_day": row["month_day"],
        "schedule_interval": int(row["schedule_interval"]),
        "until_date": row["until_date"],
        "action_params": _json_object(row["action_params_json"]),
        "status": str(row["status"]),
        "action_type": str(row["action_type"]),
        "autonomy_level": str(row["autonomy_level"]),
        "policy_status": str(row["policy_status"]),
        "timezone": str(row["timezone"]),
        "window_start": row["window_start"],
        "window_end": row["window_end"],
        "max_runs_per_day": int(row["max_runs_per_day"]),
        "starts_at": row["starts_at"],
        "expires_at": row["expires_at"],
        "domain": str(row["domain"]),
        "project": str(row["project"]),
        "created_by": str(row["created_by"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "background_execution": False,
    }
    return data


def _public_run(row: Any) -> dict[str, Any]:
    return {
        "public_id": str(row["public_id"]),
        "automation_public_id": str(row["automation_public_id"]),
        "policy_public_id": str(row["policy_public_id"]),
        "title": str(row["title"]),
        "action_type": str(row["action_type"]),
        "autonomy_level": str(row["autonomy_level"]),
        "occurrence_key": str(row["occurrence_key"]),
        "occurrence_at": str(row["occurrence_at"]),
        "status": str(row["status"]),
        "result": _json_object(row["result_json"]),
        "verification_status": str(row["verification_status"]),
        "approved_by": row["approved_by"],
        "approved_at": row["approved_at"],
        "created_by": str(row["created_by"]),
        "created_at": str(row["created_at"]),
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def _public_inbox(row: Any) -> dict[str, Any]:
    return {
        "public_id": str(row["public_id"]),
        "run_public_id": str(row["run_public_id"]),
        "automation_public_id": str(row["automation_public_id"]),
        "automation_title": str(row["automation_title"]),
        "title": str(row["title"]),
        "body": str(row["body"]),
        "status": str(row["status"]),
        "visible_at": str(row["visible_at"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "external_notification_sent": False,
    }


def _params(value: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(encoded) > 4_000:
        raise ValueError("Los parámetros de automatización exceden el límite.")
    if any(key.casefold() in {"token", "secret", "password", "credential"} for key in value):
        raise ValueError("Los parámetros no pueden contener secretos.")
    return json.loads(encoded)


def _result_json(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(encoded) <= _MAX_RESULT_CHARS:
        return encoded
    return json.dumps(
        {
            "title": _bounded(str(value.get("title", "Resultado de automatización")), 200),
            "body": _bounded(str(value.get("body", "")), 5_000),
            "truncated": True,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        loaded = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(loaded) if isinstance(loaded, dict) else {}


def _json_list(value: Any) -> list[Any]:
    try:
        loaded = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return list(loaded) if isinstance(loaded, list) else []


def _weekdays(values: tuple[str, ...]) -> tuple[int, ...]:
    result: set[int] = set()
    for value in values:
        clean = _fold(value)
        if clean not in _WEEKDAY_ALIASES:
            raise ValueError(f"Día de semana inválido: {value}")
        result.add(_WEEKDAY_ALIASES[clean])
    return tuple(sorted(result))


def _timezone(value: str) -> ZoneInfo:
    clean = value.strip() or _DEFAULT_TIMEZONE
    try:
        return ZoneInfo(clean)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Zona horaria inválida: {clean}") from exc


def _occurrence_timezone(value: datetime) -> str:
    zone = value.tzinfo
    return str(zone.key) if hasattr(zone, "key") else _DEFAULT_TIMEZONE


def _parse_now(value: str | None, zone: ZoneInfo) -> datetime:
    if not value:
        return datetime.now(zone)
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError("Fecha/hora de escaneo inválida.") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _optional_datetime(value: str | None, zone: ZoneInfo, label: str) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label.capitalize()} inválido.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone).isoformat()


def _date(value: str | None, label: str) -> date:
    if value is None:
        raise ValueError(f"{label.capitalize()} requerida.")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label.capitalize()} inválida.") from exc


def _time(value: str | None, label: str) -> time:
    if value is None:
        raise ValueError(f"{label.capitalize()} requerida.")
    try:
        parsed = time.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label.capitalize()} inválida.") from exc
    return parsed.replace(second=0, microsecond=0)


def _optional_time(value: str | None, label: str) -> time | None:
    return _time(value, label) if value and value.strip() else None


def _choice(value: str, choices: tuple[str, ...], label: str) -> str:
    clean = value.strip().casefold()
    if clean not in choices:
        raise ValueError(f"{label.capitalize()} inválido.")
    return clean


def _required(value: str, label: str, limit: int) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{label.capitalize()} no puede estar vacío.")
    if len(clean) > limit:
        raise ValueError(f"{label.capitalize()} excede el límite.")
    return clean


def _bounded(value: str, limit: int) -> str:
    return value.strip()[:limit]


def _fold(value: str) -> str:
    return "".join(
        char
        for char in value.strip().casefold()
        if char.isalnum() or char.isspace()
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
