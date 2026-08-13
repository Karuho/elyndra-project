from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.db import Database
from elyndra.orchestration import ActionPlan

_CYCLE_ID_RE = re.compile(r"\b[0-9a-f]{32}\b", re.IGNORECASE)
_VALIDATION_TERMS = (
    "comprueba",
    "comprobar",
    "ejecuta",
    "ejecutar",
    "prueba",
    "probar",
    "valida",
    "validar",
    "verifica",
    "verificar",
)
_REPAIR_TERMS = (
    "arregla",
    "arreglar",
    "corrige",
    "corregir",
    "repara",
    "reparar",
)
_MAX_RESULT_CHARS = 18_000


class ValidationCycleRepository:
    """Persist explicit validation and repair hand-offs without autonomous loops."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save(
        self,
        *,
        source_change_proposal_id: str,
        project_root: str,
        validation_request: str,
        plan: ActionPlan,
        actor: str,
        chat_id: str | None = None,
    ) -> str:
        public_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_validation_cycles(
                    public_id, source_change_proposal_id, repair_proposal_id,
                    validation_run_id, chat_id, project_root, status, actor,
                    validation_request, plan_json, validation_result_json,
                    created_at, updated_at
                ) VALUES (?, ?, NULL, NULL, ?, ?, 'validation_proposed', ?, ?, ?, '{}', ?, ?)
                """,
                (
                    public_id,
                    source_change_proposal_id.strip(),
                    chat_id,
                    str(Path(project_root).expanduser().resolve(strict=False)),
                    actor,
                    validation_request.strip(),
                    json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
        return public_id

    def get(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_validation_cycles WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
        return _public_cycle(dict(row)) if row is not None else None

    def list_recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM assistant_validation_cycles
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [_public_cycle(dict(row)) for row in rows]

    def count(self, *, status: str | None = None) -> int:
        with self.database.connect() as connection:
            if status is None:
                row = connection.execute(
                    "SELECT COUNT(*) FROM assistant_validation_cycles"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) FROM assistant_validation_cycles WHERE status = ?",
                    (status,),
                ).fetchone()
        return int(row[0])

    def claim_validation(self, public_id: str, *, actor: str) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE assistant_validation_cycles
                SET status = 'validating', updated_at = ?
                WHERE public_id = ? AND actor = ? AND status = 'validation_proposed'
                """,
                (now, public_id.strip(), actor),
            )
        if cursor.rowcount != 1:
            raise ValueError(
                "El ciclo no existe, ya fue validado o no está pendiente de aprobación."
            )
        item = self.get(public_id)
        if item is None:
            raise RuntimeError("No se pudo recuperar el ciclo reclamado.")
        return item

    def complete_validation(
        self,
        public_id: str,
        *,
        action_status: str,
        validation_run_id: str | None,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        status = {
            "passed": "validation_passed",
            "failed": "validation_failed",
            "partial": "validation_partial",
        }.get(action_status, "validation_failed")
        now = datetime.now(UTC).isoformat()
        bounded = _bounded_result(result)
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE assistant_validation_cycles
                SET status = ?, validation_run_id = ?, validation_result_json = ?,
                    updated_at = ?
                WHERE public_id = ? AND status = 'validating'
                """,
                (
                    status,
                    validation_run_id,
                    json.dumps(bounded, ensure_ascii=False, sort_keys=True),
                    now,
                    public_id.strip(),
                ),
            )
        if cursor.rowcount != 1:
            raise RuntimeError("El ciclo no estaba ejecutando una validación.")
        item = self.get(public_id)
        if item is None:
            raise RuntimeError("No se pudo recuperar el ciclo validado.")
        return item

    def attach_repair(
        self,
        public_id: str,
        *,
        repair_proposal_id: str,
        actor: str,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE assistant_validation_cycles
                SET status = 'repair_proposed', repair_proposal_id = ?, updated_at = ?
                WHERE public_id = ? AND actor = ?
                  AND status IN ('validation_failed', 'validation_partial')
                  AND repair_proposal_id IS NULL
                """,
                (repair_proposal_id.strip(), now, public_id.strip(), actor),
            )
        if cursor.rowcount != 1:
            raise ValueError(
                "El ciclo no admite reparación: debe haber terminado con fallos o estado parcial."
            )
        item = self.get(public_id)
        if item is None:
            raise RuntimeError("No se pudo recuperar el ciclo con reparación.")
        return item

    def release_repair(
        self,
        repair_proposal_id: str,
        *,
        outcome: str,
    ) -> dict[str, Any] | None:
        if outcome not in {"rejected", "failed", "stale"}:
            raise ValueError("Resultado de reparación inválido.")
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT public_id, validation_result_json
                FROM assistant_validation_cycles
                WHERE repair_proposal_id = ? AND status = 'repair_proposed'
                ORDER BY id DESC LIMIT 1
                """,
                (repair_proposal_id.strip(),),
            ).fetchone()
            if row is None:
                return None
            result = _json_object(row["validation_result_json"])
            validation_status = (
                "validation_partial"
                if str(result.get("status", "")) == "partial"
                else "validation_failed"
            )
            public_id = str(row["public_id"])
            connection.execute(
                """
                UPDATE assistant_validation_cycles
                SET status = ?, repair_proposal_id = NULL, updated_at = ?
                WHERE public_id = ? AND status = 'repair_proposed'
                """,
                (validation_status, now, public_id),
            )
        return self.get(public_id)

    def mark_repair_applied(self, repair_proposal_id: str) -> dict[str, Any] | None:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT public_id FROM assistant_validation_cycles
                WHERE repair_proposal_id = ? AND status = 'repair_proposed'
                ORDER BY id DESC LIMIT 1
                """,
                (repair_proposal_id.strip(),),
            ).fetchone()
            if row is None:
                return None
            public_id = str(row[0])
            connection.execute(
                """
                UPDATE assistant_validation_cycles
                SET status = 'repair_applied', updated_at = ?
                WHERE public_id = ? AND status = 'repair_proposed'
                """,
                (now, public_id),
            )
        return self.get(public_id)

    def cancel(self, public_id: str, *, actor: str) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE assistant_validation_cycles
                SET status = 'cancelled', updated_at = ?
                WHERE public_id = ? AND actor = ? AND status = 'validation_proposed'
                """,
                (now, public_id.strip(), actor),
            )
        if cursor.rowcount != 1:
            raise ValueError("El ciclo no existe o ya no está pendiente.")
        item = self.get(public_id)
        if item is None:
            raise RuntimeError("No se pudo recuperar el ciclo cancelado.")
        return item


def validate_plan_for_project(plan: ActionPlan, project_root: str) -> None:
    root = Path(project_root).expanduser().resolve(strict=False)
    seen_path = False
    for step in plan.steps:
        for key in ("path", "project_root", "database_path"):
            value = step.params.get(key)
            if value is None:
                continue
            seen_path = True
            target = Path(str(value)).expanduser().resolve(strict=False)
            if target != root and root not in target.parents:
                raise ValueError(
                    f"El paso {step.skill_name} sale del proyecto vinculado al ciclo."
                )
    if not seen_path:
        raise ValueError("El plan de validación no contiene una ruta de proyecto verificable.")


def validation_approval_summary(item: dict[str, Any]) -> str:
    plan = item.get("plan", {}) or {}
    steps = plan.get("steps", []) or []
    lines = [
        f"Ciclo de validación {item.get('public_id')}",
        f"Cambio aplicado: {item.get('source_change_proposal_id')}",
        f"Proyecto: {item.get('project_root')}",
        "",
        "Plan exacto que se ejecutará una sola vez:",
    ]
    for index, step in enumerate(steps, start=1):
        lines.append(f"{index}. {step.get('skill_name')} · {step.get('purpose', '')}")
    lines.extend(
        [
            "",
            "La validación no modifica archivos. Si falla, Elyndra no repara nada "
            "automáticamente: una reparación nueva requerirá otra propuesta y otra aprobación.",
        ]
    )
    return "\n".join(lines)


def repair_context(item: dict[str, Any]) -> str:
    result = item.get("validation_result", {}) or {}
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    return (
        "RESULTADOS REALES DE LA VALIDACIÓN SUPERVISADA\n"
        f"Ciclo: {item.get('public_id')}\n"
        f"Proyecto: {item.get('project_root')}\n"
        f"Estado: {item.get('status')}\n\n"
        f"{payload[:_MAX_RESULT_CHARS]}"
    )


def extract_validation_change_id(text: str) -> str | None:
    clean = " ".join(text.casefold().split())
    if not any(term in clean for term in _VALIDATION_TERMS):
        return None
    if "cambio" not in clean and "propuesta" not in clean:
        return None
    match = _CYCLE_ID_RE.search(text)
    return match.group(0).lower() if match else None


def extract_repair_cycle_id(text: str) -> str | None:
    clean = " ".join(text.casefold().split())
    if not any(term in clean for term in _REPAIR_TERMS):
        return None
    if "ciclo" not in clean and "validación" not in clean and "validacion" not in clean:
        return None
    match = _CYCLE_ID_RE.search(text)
    return match.group(0).lower() if match else None


def _bounded_result(result: dict[str, Any]) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    for raw in list(result.get("step_results", []) or [])[:4]:
        if not isinstance(raw, dict):
            continue
        data = raw.get("data", {}) if isinstance(raw.get("data"), dict) else {}
        steps.append(
            {
                "skill_name": str(raw.get("skill_name", ""))[:120],
                "ok": bool(raw.get("ok", False)),
                "message": str(raw.get("message", ""))[:2_500],
                "data": _bounded_value(data),
            }
        )
    return {
        "status": str(result.get("status", "failed"))[:40],
        "action_run_id": str(result.get("action_run_id", ""))[:64] or None,
        "duration_ms": int(result.get("duration_ms", 0) or 0),
        "steps": steps,
        "deterministic_summary": str(result.get("deterministic_summary", ""))[:4_000],
    }


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return "[truncated]"
    if isinstance(value, dict):
        allowed = {
            "status",
            "returncode",
            "duration_ms",
            "tool",
            "project_root",
            "resolved_path",
            "summary",
            "stages",
            "findings",
            "counts",
            "warnings",
            "errors",
            "unavailable",
            "skipped",
        }
        return {
            str(key)[:100]: _bounded_value(item, depth=depth + 1)
            for key, item in list(value.items())[:50]
            if key in allowed
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_value(item, depth=depth + 1) for item in list(value)[:40]]
    if isinstance(value, str):
        return value[:2_000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:500]


def _public_cycle(item: dict[str, Any]) -> dict[str, Any]:
    item["plan"] = _json_object(item.pop("plan_json", "{}"))
    item["validation_result"] = _json_object(
        item.pop("validation_result_json", "{}")
    )
    return item


def _json_object(value: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
