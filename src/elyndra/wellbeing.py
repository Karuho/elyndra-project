from __future__ import annotations

import json
import re
import unicodedata
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from elyndra.db import Database

_ALLOWED_SCORES = range(1, 6)
_ALLOWED_PLAN_STATUS = {"active", "paused", "completed", "cancelled"}
_ALLOWED_ACTION_STATUS = {"pending", "completed", "skipped"}
_MAX_NOTE_CHARS = 1200
_MAX_TITLE_CHARS = 160
_MAX_ACTIONS = 12


class WellbeingRepository:
    """Bounded personal wellbeing tracking without diagnosis or autonomous treatment."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def status(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            checkins = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_wellbeing_checkins"
                ).fetchone()[0]
            )
            active_plans = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_coaching_plans "
                    "WHERE status = 'active'"
                ).fetchone()[0]
            )
            pending_actions = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_coaching_actions "
                    "WHERE status = 'pending'"
                ).fetchone()[0]
            )
        return {
            "checkins": checkins,
            "active_plans": active_plans,
            "pending_actions": pending_actions,
            "diagnosis": False,
            "treatment_authority": False,
            "background_execution": False,
            "automatic_intervention": False,
        }

    def create_checkin(
        self,
        *,
        checkin_date: str,
        mood: int,
        energy: int,
        stress: int,
        focus: int,
        sleep_hours: float | None,
        sleep_quality: int | None,
        hydration: int | None,
        nutrition: int | None,
        activity_minutes: int | None,
        note: str,
        actor: str,
    ) -> dict[str, Any]:
        clean_date = _parse_date(checkin_date).isoformat()
        values = {
            "mood": _score(mood, "ánimo"),
            "energy": _score(energy, "energía"),
            "stress": _score(stress, "estrés"),
            "focus": _score(focus, "concentración"),
        }
        clean_sleep = None if sleep_hours is None else float(sleep_hours)
        if clean_sleep is not None and not 0 <= clean_sleep <= 24:
            raise ValueError("Las horas de sueño deben estar entre 0 y 24.")
        optional_scores = {
            "sleep_quality": _optional_score(sleep_quality, "calidad de sueño"),
            "hydration": _optional_score(hydration, "hidratación"),
            "nutrition": _optional_score(nutrition, "alimentación"),
        }
        clean_activity = None if activity_minutes is None else int(activity_minutes)
        if clean_activity is not None and not 0 <= clean_activity <= 1440:
            raise ValueError("La actividad debe estar entre 0 y 1440 minutos.")
        clean_note = _bounded(note, _MAX_NOTE_CHARS, "nota")
        public_id = uuid.uuid4().hex
        now = _now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_wellbeing_checkins(
                    public_id, checkin_date, mood, energy, stress, focus,
                    sleep_hours, sleep_quality, hydration, nutrition,
                    activity_minutes, note, created_by, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    public_id,
                    clean_date,
                    values["mood"],
                    values["energy"],
                    values["stress"],
                    values["focus"],
                    clean_sleep,
                    optional_scores["sleep_quality"],
                    optional_scores["hydration"],
                    optional_scores["nutrition"],
                    clean_activity,
                    clean_note,
                    actor,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM assistant_wellbeing_checkins WHERE public_id = ?",
                (public_id,),
            ).fetchone()
        return _row(row)

    def list_checkins(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if start_date:
            clauses.append("checkin_date >= ?")
            params.append(_parse_date(start_date).isoformat())
        if end_date:
            clauses.append("checkin_date <= ?")
            params.append(_parse_date(end_date).isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assistant_wellbeing_checkins "
                f"{where} ORDER BY checkin_date DESC, id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [_row(row) for row in rows]

    def summary(self, *, days: int = 7, end_date: str | None = None) -> dict[str, Any]:
        clean_days = max(1, min(int(days), 90))
        end = _parse_date(end_date) if end_date else date.today()
        start = end - timedelta(days=clean_days - 1)
        items = self.list_checkins(
            start_date=start.isoformat(), end_date=end.isoformat(), limit=500
        )
        metrics = {
            "mood": _average(items, "mood"),
            "energy": _average(items, "energy"),
            "stress": _average(items, "stress"),
            "focus": _average(items, "focus"),
            "sleep_hours": _average(items, "sleep_hours"),
            "sleep_quality": _average(items, "sleep_quality"),
            "hydration": _average(items, "hydration"),
            "nutrition": _average(items, "nutrition"),
            "activity_minutes": _average(items, "activity_minutes"),
        }
        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "days": clean_days,
            "checkins": len(items),
            "metrics": metrics,
            "signals": _signals(metrics, len(items)),
            "diagnosis": False,
            "professional_replacement": False,
            "items": items[:14],
        }

    def create_plan(
        self,
        *,
        title: str,
        focus: str,
        objective: str,
        start_date: str,
        review_date: str | None,
        actions: tuple[str, ...],
        actor: str,
    ) -> dict[str, Any]:
        clean_title = _required(title, _MAX_TITLE_CHARS, "título")
        clean_focus = _required(focus, 80, "foco")
        clean_objective = _required(objective, 1000, "objetivo")
        clean_start = _parse_date(start_date).isoformat()
        clean_review = _parse_date(review_date).isoformat() if review_date else None
        if clean_review and clean_review < clean_start:
            raise ValueError("La fecha de revisión no puede ser anterior al inicio.")
        clean_actions = tuple(
            _required(value, 300, "acción") for value in actions if value.strip()
        )
        if not clean_actions or len(clean_actions) > _MAX_ACTIONS:
            raise ValueError(f"El plan requiere entre 1 y {_MAX_ACTIONS} acciones.")
        plan_id = uuid.uuid4().hex
        now = _now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO assistant_coaching_plans(
                    public_id, title, focus, objective, status, start_date,
                    review_date, created_by, created_at, updated_at
                ) VALUES(?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    clean_title,
                    clean_focus,
                    clean_objective,
                    clean_start,
                    clean_review,
                    actor,
                    now,
                    now,
                ),
            )
            row_id = int(cursor.lastrowid)
            for position, action in enumerate(clean_actions, start=1):
                connection.execute(
                    """
                    INSERT INTO assistant_coaching_actions(
                        public_id, plan_id, position, title, status,
                        created_by, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (uuid.uuid4().hex, row_id, position, action, actor, now, now),
                )
        details = self.plan_details(plan_id)
        if details is None:
            raise RuntimeError("No fue posible recuperar el plan creado.")
        return details

    def list_plans(self, *, status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
        clean = status.strip().casefold()
        if clean != "all" and clean not in _ALLOWED_PLAN_STATUS:
            raise ValueError("Estado de plan inválido.")
        where = "" if clean == "all" else "WHERE status = ?"
        params: tuple[Any, ...]
        params = (max(1, min(limit, 500)),) if clean == "all" else (clean, max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assistant_coaching_plans "
                f"{where} ORDER BY updated_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return [_row(row) for row in rows]

    def plan_details(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_coaching_plans WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
            if row is None:
                return None
            actions = connection.execute(
                "SELECT * FROM assistant_coaching_actions WHERE plan_id = ? "
                "ORDER BY position, id",
                (int(row["id"]),),
            ).fetchall()
        return {**_row(row), "actions": [_row(item) for item in actions]}

    def update_plan_status(self, public_id: str, *, status: str) -> dict[str, Any]:
        clean = status.strip().casefold()
        if clean not in _ALLOWED_PLAN_STATUS:
            raise ValueError("Estado de plan inválido.")
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE assistant_coaching_plans SET status = ?, updated_at = ? "
                "WHERE public_id = ?",
                (clean, _now(), public_id.strip()),
            )
            if cursor.rowcount != 1:
                raise ValueError("Plan de coaching no encontrado.")
        details = self.plan_details(public_id)
        if details is None:
            raise RuntimeError("No fue posible recuperar el plan actualizado.")
        return details

    def update_action_status(self, public_id: str, *, status: str) -> dict[str, Any]:
        clean = status.strip().casefold()
        if clean not in _ALLOWED_ACTION_STATUS:
            raise ValueError("Estado de acción inválido.")
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE assistant_coaching_actions SET status = ?, updated_at = ?, "
                "completed_at = ? WHERE public_id = ?",
                (
                    clean,
                    _now(),
                    _now() if clean == "completed" else None,
                    public_id.strip(),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Acción de coaching no encontrada.")
            row = connection.execute(
                "SELECT * FROM assistant_coaching_actions WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
        return _row(row)


def wellbeing_query(text: str) -> dict[str, Any] | None:
    normalized = _normalize(text)
    week_terms = {
        "como he estado esta semana",
        "resumen de bienestar",
        "resumen de bienestar semanal",
        "como estuvo mi semana",
        "como me he sentido esta semana",
        "como dormi esta semana",
        "resumen de animo",
        "resumen de energia",
    }
    month_terms = {
        "como he estado este mes",
        "resumen de bienestar mensual",
        "como me he sentido este mes",
    }
    if normalized in week_terms:
        return {"kind": "summary", "days": 7}
    if normalized in month_terms:
        return {"kind": "summary", "days": 30}
    return None


def render_wellbeing_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"Resumen de bienestar · {summary['start_date']} a {summary['end_date']}",
        f"- Check-ins registrados: {summary['checkins']}",
    ]
    if not summary["checkins"]:
        lines.append("- No hay datos suficientes para resumir este periodo.")
    else:
        labels = {
            "mood": "Ánimo",
            "energy": "Energía",
            "stress": "Estrés",
            "focus": "Concentración",
            "sleep_hours": "Sueño",
            "sleep_quality": "Calidad de sueño",
            "hydration": "Hidratación",
            "nutrition": "Alimentación",
            "activity_minutes": "Actividad",
        }
        for key, label in labels.items():
            value = summary["metrics"].get(key)
            if value is None:
                continue
            suffix = " h" if key == "sleep_hours" else " min" if key == "activity_minutes" else "/5"
            lines.append(f"- {label}: {value:.1f}{suffix}")
        for signal in summary["signals"]:
            lines.append(f"- Observación: {signal}")
    lines.append(
        "Este seguimiento es orientativo: no diagnostica ni reemplaza atención profesional."
    )
    return "\n".join(lines)


def _signals(metrics: dict[str, float | None], count: int) -> list[str]:
    if count < 2:
        return ["Aún hay pocos registros para identificar una tendencia."]
    signals: list[str] = []
    stress = metrics.get("stress")
    sleep = metrics.get("sleep_hours")
    energy = metrics.get("energy")
    if stress is not None and stress >= 4:
        signals.append(
            "El estrés promedio fue alto; conviene reducir carga y pedir apoyo si persiste."
        )
    if sleep is not None and sleep < 6:
        signals.append("El sueño promedio fue menor a seis horas.")
    if energy is not None and energy <= 2:
        signals.append("La energía promedio fue baja; prioriza descanso y tareas esenciales.")
    if not signals:
        signals.append("No aparecen señales simples de deterioro en los datos registrados.")
    return signals


def _average(items: list[dict[str, Any]], key: str) -> float | None:
    values = [float(item[key]) for item in items if item.get(key) is not None]
    return round(sum(values) / len(values), 2) if values else None


def _score(value: int, label: str) -> int:
    clean = int(value)
    if clean not in _ALLOWED_SCORES:
        raise ValueError(f"{label.capitalize()} debe estar entre 1 y 5.")
    return clean


def _optional_score(value: int | None, label: str) -> int | None:
    return None if value is None else _score(value, label)


def _required(value: str, limit: int, label: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{label.capitalize()} no puede estar vacío.")
    return _bounded(clean, limit, label)


def _bounded(value: str, limit: int, label: str) -> str:
    clean = value.strip()
    if len(clean) > limit:
        raise ValueError(f"{label.capitalize()} supera {limit} caracteres.")
    return clean


def _parse_date(value: str | None) -> date:
    if value is None:
        return date.today()
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError("La fecha debe usar YYYY-MM-DD.") from exc


def _normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    clean = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", clean).strip()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row(row: Any) -> dict[str, Any]:
    if row is None:
        raise RuntimeError("Fila de bienestar no disponible.")
    result = dict(row)
    for key in ("metadata_json",):
        if key in result:
            try:
                result[key.removesuffix("_json")] = json.loads(result[key])
            except (TypeError, json.JSONDecodeError):
                result[key.removesuffix("_json")] = {}
    return result
