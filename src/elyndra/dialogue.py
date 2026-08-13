from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from elyndra.db import Database

_WORD_RE = re.compile(r"[\wáéíóúüñ]+", re.IGNORECASE)


class DialogueStateRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def remember_clarification(
        self,
        chat_id: str | None,
        *,
        options: dict[str, str],
        prompt: str,
        ttl_minutes: int = 30,
    ) -> None:
        if not chat_id or not options:
            return
        now = datetime.now(UTC)
        payload = {
            "options": options,
            "prompt": prompt[:500],
            "nonce": uuid.uuid4().hex,
        }
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_dialogue_states(
                    chat_id, state_type, state_json, expires_at, created_at, updated_at
                ) VALUES (?, 'clarification', ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    state_type = excluded.state_type,
                    state_json = excluded.state_json,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (
                    chat_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    (now + timedelta(minutes=max(1, ttl_minutes))).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )

    def resolve_followup(self, chat_id: str | None, text: str) -> str | None:
        if not chat_id:
            return None
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT state_json FROM assistant_dialogue_states
                WHERE chat_id = ? AND state_type = 'clarification' AND expires_at > ?
                """,
                (chat_id, now),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["state_json"]))
            options = payload.get("options", {})
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(options, dict):
            return None
        tokens = _tokens(text)
        if not tokens:
            return None
        scored: list[tuple[int, str]] = []
        for label, intent in options.items():
            label_tokens = _tokens(label)
            score = len(tokens & label_tokens)
            if intent == "goal.status" and tokens & {"objetivo", "objetivos", "meta", "metas"}:
                score += 3
            if intent == "wellbeing.current" and tokens & {
                "bienestar",
                "animo",
                "estado",
                "check",
                "emocional",
            }:
                score += 3
            if intent == "coaching.progress" and tokens & {
                "coaching",
                "plan",
                "descanso",
                "progreso",
            }:
                score += 3
            scored.append((score, str(intent)))
        scored.sort(reverse=True)
        if not scored or scored[0][0] <= 0:
            return None
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM assistant_dialogue_states WHERE chat_id = ?",
                (chat_id,),
            )
        return scored[0][1]

    def clear(self, chat_id: str | None) -> None:
        if not chat_id:
            return
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM assistant_dialogue_states WHERE chat_id = ?",
                (chat_id,),
            )


def capability_help_query(text: str, *, interface: str = "chat") -> dict[str, Any] | None:
    folded = _normalize(text)
    help_signal = any(
        phrase in folded
        for phrase in (
            "como puedo agregar",
            "como agrego",
            "como crear",
            "como creo",
            "como registrar",
            "como registro",
            "donde agrego",
            "donde creo",
        )
    )
    if not help_signal:
        return None
    targets: list[str] = []
    checks = {
        "birthday": ("cumple", "cumpleanos", "cumpleaños"),
        "goal": ("objetivo", "objetivos", "meta", "metas"),
        "routine": ("rutina", "rutinas", "habito", "hábito"),
        "commitment": ("compromiso", "evento", "cita"),
        "wellbeing": ("bienestar", "check-in", "checkin"),
    }
    for target, aliases in checks.items():
        if any(alias in folded for alias in aliases):
            targets.append(target)
    if not targets:
        return None
    return {"targets": targets, "interface": interface}


def render_capability_help(data: dict[str, Any], *, preferred_name: str = "") -> str:
    target_lines = {
        "birthday": (
            "Cumpleaños: abre Personal → Cumpleaños, completa persona, mes y día, "
            "y confirma el registro."
        ),
        "goal": (
            "Objetivo: abre Personal → Objetivos o usa `assistant goal-create`; "
            "define título, prioridad y siguiente acción, y confirma."
        ),
        "routine": (
            "Rutina: abre Personal → Nueva rutina, elige inicio, horario y recurrencia, "
            "y confirma."
        ),
        "commitment": (
            "Compromiso: abre Personal → Nuevo compromiso, indica título, fecha y hora, "
            "y confirma."
        ),
        "wellbeing": (
            "Check-in de bienestar: abre Personal → Check-in de bienestar, registra solo "
            "los valores que quieras compartir y confirma."
        ),
    }
    lines = [target_lines[item] for item in data.get("targets", []) if item in target_lines]
    greeting = f"{preferred_name}, " if preferred_name else ""
    return greeting + "puedes hacerlo así:\n\n- " + "\n- ".join(lines)


def _tokens(value: str) -> set[str]:
    return set(_WORD_RE.findall(_normalize(value)))


def _normalize(value: str) -> str:
    table = str.maketrans("áéíóúüñ", "aeiouun")
    return " ".join(value.casefold().translate(table).split())
