from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from elyndra.autonomy.capabilities import CapabilityGrant
from elyndra.autonomy.models import (
    AutonomyRun,
    AutonomyRunStatus,
    HumanGateKind,
    HumanGateStatus,
    RunPlan,
)
from elyndra.db import Database

_STEP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_TERMINAL_STATUSES = frozenset(
    {
        AutonomyRunStatus.COMPLETED,
        AutonomyRunStatus.FAILED,
        AutonomyRunStatus.CANCELLED,
    }
)

_TRANSITIONS = {
    AutonomyRunStatus.PLANNED: frozenset(
        {
            AutonomyRunStatus.RUNNING,
            AutonomyRunStatus.CANCELLED,
        }
    ),
    AutonomyRunStatus.RUNNING: frozenset(
        {
            AutonomyRunStatus.WAITING_HUMAN,
            AutonomyRunStatus.COMPLETED,
            AutonomyRunStatus.FAILED,
            AutonomyRunStatus.CANCELLED,
        }
    ),
    AutonomyRunStatus.WAITING_HUMAN: frozenset(
        {
            AutonomyRunStatus.RUNNING,
            AutonomyRunStatus.CANCELLED,
        }
    ),
    AutonomyRunStatus.COMPLETED: frozenset(),
    AutonomyRunStatus.FAILED: frozenset(),
    AutonomyRunStatus.CANCELLED: frozenset(),
}


class AutonomyRepository:
    """Persistent state and append-only audit for bounded autonomy runs."""

    def __init__(self, database: Database) -> None:
        if database.role == "root":
            raise ValueError(
                "Los runs autónomos pertenecen al vault de la cuenta, no a la base root."
            )
        self.database = database

    def create(self, run: AutonomyRun) -> dict[str, Any]:
        if run.status is not AutonomyRunStatus.PLANNED:
            raise ValueError("Un run nuevo debe comenzar en estado planned.")

        if run.grant.is_expired():
            raise PermissionError(
                "No se puede persistir un run cuyo CapabilityGrant ya expiró."
            )

        grant_json = _json_dump(_grant_data(run.grant), maximum=65_536)
        plan_json = _json_dump(_plan_data(run.plan), maximum=262_144)
        created_at = run.created_at.isoformat()

        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_autonomy_runs(
                    public_id,
                    actor,
                    workspace_root,
                    objective,
                    status,
                    grant_json,
                    plan_json,
                    created_at,
                    updated_at,
                    started_at,
                    finished_at
                ) VALUES (?, ?, ?, ?, 'planned', ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    run.run_id,
                    run.actor,
                    str(run.workspace.root),
                    run.plan.objective,
                    grant_json,
                    plan_json,
                    created_at,
                    created_at,
                ),
            )

            row = connection.execute(
                """
                SELECT * FROM assistant_autonomy_runs
                WHERE public_id = ?
                """,
                (run.run_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("No se pudo recuperar el run recién creado.")

            self._insert_event(
                connection,
                run_db_id=int(row["id"]),
                event_type="run_created",
                from_status=None,
                to_status=AutonomyRunStatus.PLANNED,
                summary="Run autónomo creado con autoridad congelada.",
                payload={},
                created_at=created_at,
            )

        item = self.get(run.run_id)
        if item is None:
            raise RuntimeError("No se pudo recuperar el run persistido.")
        return item

    def get(self, run_id: str) -> dict[str, Any] | None:
        clean_id = _required(run_id, "run_id", 128)

        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM assistant_autonomy_runs
                WHERE public_id = ?
                """,
                (clean_id,),
            ).fetchone()

            if row is None:
                return None

            events = connection.execute(
                """
                SELECT
                    sequence,
                    event_type,
                    from_status,
                    to_status,
                    step_id,
                    summary,
                    payload_json,
                    created_at
                FROM assistant_autonomy_events
                WHERE run_id = ?
                ORDER BY sequence ASC
                """,
                (int(row["id"]),),
            ).fetchall()

            gates = connection.execute(
                """
                SELECT
                    public_id,
                    kind,
                    status,
                    reason,
                    created_at,
                    resolved_at,
                    resolved_by
                FROM assistant_autonomy_human_gates
                WHERE run_id = ?
                ORDER BY id ASC
                """,
                (int(row["id"]),),
            ).fetchall()

        item = dict(row)
        item.pop("id", None)
        item["grant"] = json.loads(item.pop("grant_json"))
        item["plan"] = json.loads(item.pop("plan_json"))
        item["events"] = [_public_event(dict(event)) for event in events]
        item["human_gates"] = [dict(gate) for gate in gates]
        return item

    def list_recent(
        self,
        *,
        actor: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 200))

        with self.database.connect() as connection:
            if actor is None:
                rows = connection.execute(
                    """
                    SELECT
                        public_id,
                        actor,
                        workspace_root,
                        objective,
                        status,
                        created_at,
                        updated_at,
                        started_at,
                        finished_at
                    FROM assistant_autonomy_runs
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (bounded_limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT
                        public_id,
                        actor,
                        workspace_root,
                        objective,
                        status,
                        created_at,
                        updated_at,
                        started_at,
                        finished_at
                    FROM assistant_autonomy_runs
                    WHERE actor = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (_required(actor, "actor", 200), bounded_limit),
                ).fetchall()

        return [dict(row) for row in rows]

    def transition(
        self,
        run_id: str,
        to_status: AutonomyRunStatus | str,
        *,
        actor: str,
        summary: str,
        step_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            target = AutonomyRunStatus(to_status)
        except ValueError as exc:
            raise ValueError("Estado objetivo de autonomía inválido.") from exc

        clean_summary = _required(summary, "summary", 2_000)
        clean_step = _step_id(step_id)
        payload_data = payload or {}
        _json_dump(payload_data, maximum=16_384)

        with self.database.connect() as connection:
            row = self._owned_run(connection, run_id, actor=actor)
            current = AutonomyRunStatus(str(row["status"]))

            if target is AutonomyRunStatus.RUNNING:
                _require_grant_active_json(str(row["grant_json"]))

            if (
                current is AutonomyRunStatus.WAITING_HUMAN
                or target is AutonomyRunStatus.WAITING_HUMAN
            ):
                raise ValueError(
                    "waiting_human solo puede gestionarse mediante HumanGate."
                )

            self._require_transition(current, target)

            now = _now()
            self._set_status(connection, row, target, now=now)
            self._insert_event(
                connection,
                run_db_id=int(row["id"]),
                event_type=_transition_event(target),
                from_status=current,
                to_status=target,
                summary=clean_summary,
                payload=payload_data,
                created_at=now,
                step_id=clean_step,
            )

        item = self.get(run_id)
        if item is None:
            raise RuntimeError("El run desapareció después de la transición.")
        return item

    def request_human_gate(
        self,
        run_id: str,
        *,
        actor: str,
        reason: str,
        kind: HumanGateKind | str = HumanGateKind.APPROVAL,
        step_id: str = "",
    ) -> dict[str, Any]:
        try:
            clean_kind = HumanGateKind(kind)
        except ValueError as exc:
            raise ValueError("Tipo de HumanGate inválido.") from exc

        clean_reason = _required(reason, "reason", 2_000)
        clean_step = _step_id(step_id)
        gate_id = uuid.uuid4().hex
        now = _now()

        with self.database.connect() as connection:
            row = self._owned_run(connection, run_id, actor=actor)
            current = AutonomyRunStatus(str(row["status"]))

            if current is not AutonomyRunStatus.RUNNING:
                raise ValueError(
                    "Solo un run running puede solicitar intervención humana."
                )

            connection.execute(
                """
                INSERT INTO assistant_autonomy_human_gates(
                    public_id,
                    run_id,
                    kind,
                    status,
                    reason,
                    created_at,
                    resolved_at,
                    resolved_by
                ) VALUES (?, ?, ?, 'pending', ?, ?, NULL, NULL)
                """,
                (
                    gate_id,
                    int(row["id"]),
                    clean_kind.value,
                    clean_reason,
                    now,
                ),
            )

            self._set_status(
                connection,
                row,
                AutonomyRunStatus.WAITING_HUMAN,
                now=now,
            )
            self._insert_event(
                connection,
                run_db_id=int(row["id"]),
                event_type="human_gate_requested",
                from_status=AutonomyRunStatus.RUNNING,
                to_status=AutonomyRunStatus.WAITING_HUMAN,
                summary=clean_reason,
                payload={
                    "gate_id": gate_id,
                    "kind": clean_kind.value,
                },
                created_at=now,
                step_id=clean_step,
            )

        item = self.get(run_id)
        if item is None:
            raise RuntimeError("No se pudo recuperar el run después del HumanGate.")
        return item

    def resolve_human_gate(
        self,
        gate_id: str,
        *,
        actor: str,
        decision: HumanGateStatus | str,
    ) -> dict[str, Any]:
        try:
            resolution = HumanGateStatus(decision)
        except ValueError as exc:
            raise ValueError("Resolución de HumanGate inválida.") from exc

        if resolution is HumanGateStatus.PENDING:
            raise ValueError("pending no es una resolución de HumanGate.")

        clean_gate_id = _required(gate_id, "gate_id", 128)
        clean_actor = _required(actor, "actor", 200)
        now = _now()

        with self.database.connect() as connection:
            gate = connection.execute(
                """
                SELECT
                    g.*,
                    r.public_id AS run_public_id,
                    r.actor AS run_actor,
                    r.status AS run_status,
                    r.grant_json AS run_grant_json,
                    r.started_at AS run_started_at,
                    r.finished_at AS run_finished_at
                FROM assistant_autonomy_human_gates AS g
                JOIN assistant_autonomy_runs AS r
                  ON r.id = g.run_id
                WHERE g.public_id = ?
                """,
                (clean_gate_id,),
            ).fetchone()

            if gate is None:
                raise ValueError("HumanGate no encontrado.")

            if str(gate["run_actor"]) != clean_actor:
                raise PermissionError(
                    "El actor no puede resolver un HumanGate de otro propietario."
                )

            if str(gate["status"]) != HumanGateStatus.PENDING.value:
                raise ValueError("El HumanGate ya fue resuelto.")

            if str(gate["run_status"]) != AutonomyRunStatus.WAITING_HUMAN.value:
                raise ValueError(
                    "El run asociado no está esperando intervención humana."
                )

            if resolution is HumanGateStatus.APPROVED:
                _require_grant_active_json(str(gate["run_grant_json"]))

            target = (
                AutonomyRunStatus.RUNNING
                if resolution is HumanGateStatus.APPROVED
                else AutonomyRunStatus.CANCELLED
            )

            connection.execute(
                """
                UPDATE assistant_autonomy_human_gates
                SET status = ?, resolved_at = ?, resolved_by = ?
                WHERE id = ?
                """,
                (
                    resolution.value,
                    now,
                    clean_actor,
                    int(gate["id"]),
                ),
            )

            run_row = {
                "id": int(gate["run_id"]),
                "status": str(gate["run_status"]),
                "started_at": gate["run_started_at"],
                "finished_at": gate["run_finished_at"],
            }
            self._set_status(
                connection,
                run_row,
                target,
                now=now,
            )

            self._insert_event(
                connection,
                run_db_id=int(gate["run_id"]),
                event_type=f"human_gate_{resolution.value}",
                from_status=AutonomyRunStatus.WAITING_HUMAN,
                to_status=target,
                summary=f"HumanGate {resolution.value} por el propietario.",
                payload={
                    "gate_id": clean_gate_id,
                    "decision": resolution.value,
                },
                created_at=now,
            )

        item = self.get(str(gate["run_public_id"]))
        if item is None:
            raise RuntimeError("No se pudo recuperar el run después de resolver el gate.")
        return item

    def _owned_run(
        self,
        connection: Any,
        run_id: str,
        *,
        actor: str,
    ) -> Any:
        clean_id = _required(run_id, "run_id", 128)
        clean_actor = _required(actor, "actor", 200)

        row = connection.execute(
            """
            SELECT * FROM assistant_autonomy_runs
            WHERE public_id = ?
            """,
            (clean_id,),
        ).fetchone()

        if row is None:
            raise ValueError("AutonomyRun no encontrado.")

        if str(row["actor"]) != clean_actor:
            raise PermissionError(
                "El actor no puede modificar un AutonomyRun de otro propietario."
            )

        return row

    @staticmethod
    def _require_transition(
        current: AutonomyRunStatus,
        target: AutonomyRunStatus,
    ) -> None:
        if target not in _TRANSITIONS[current]:
            raise ValueError(
                f"Transición de autonomía inválida: {current.value} -> {target.value}"
            )

    @staticmethod
    def _set_status(
        connection: Any,
        row: Any,
        target: AutonomyRunStatus,
        *,
        now: str,
    ) -> None:
        started_at = row["started_at"]
        finished_at = row["finished_at"]

        if target is AutonomyRunStatus.RUNNING and started_at is None:
            started_at = now

        if target in _TERMINAL_STATUSES:
            finished_at = now

        connection.execute(
            """
            UPDATE assistant_autonomy_runs
            SET
                status = ?,
                updated_at = ?,
                started_at = ?,
                finished_at = ?
            WHERE id = ?
            """,
            (
                target.value,
                now,
                started_at,
                finished_at,
                int(row["id"]),
            ),
        )

    @staticmethod
    def _insert_event(
        connection: Any,
        *,
        run_db_id: int,
        event_type: str,
        from_status: AutonomyRunStatus | None,
        to_status: AutonomyRunStatus,
        summary: str,
        payload: dict[str, Any],
        created_at: str,
        step_id: str = "",
    ) -> None:
        clean_event = _required(event_type, "event_type", 64)
        clean_summary = _required(summary, "summary", 2_000)
        clean_step = _step_id(step_id)
        payload_json = _json_dump(payload, maximum=16_384)

        sequence = int(
            connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1
                FROM assistant_autonomy_events
                WHERE run_id = ?
                """,
                (run_db_id,),
            ).fetchone()[0]
        )

        connection.execute(
            """
            INSERT INTO assistant_autonomy_events(
                run_id,
                sequence,
                event_type,
                from_status,
                to_status,
                step_id,
                summary,
                payload_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_db_id,
                sequence,
                clean_event,
                from_status.value if from_status is not None else None,
                to_status.value,
                clean_step,
                clean_summary,
                payload_json,
                created_at,
            ),
        )


def _grant_data(grant: CapabilityGrant) -> dict[str, Any]:
    return {
        "capabilities": sorted(
            capability.value for capability in grant.capabilities
        ),
        "issued_at": grant.issued_at.isoformat(),
        "expires_at": grant.expires_at.isoformat(),
        "max_steps": grant.max_steps,
        "max_retries": grant.max_retries,
        "max_commands": grant.max_commands,
        "max_runtime_seconds": grant.max_runtime_seconds,
        "allowed_hosts": list(grant.allowed_hosts),
    }


def _plan_data(plan: RunPlan) -> dict[str, Any]:
    return {
        "objective": plan.objective,
        "steps": [
            {
                "step_id": step.step_id,
                "capability": step.capability.value,
                "action": step.action,
                "target": step.target,
                "requires_human_gate": step.requires_human_gate,
            }
            for step in plan.steps
        ],
    }


def _public_event(event: dict[str, Any]) -> dict[str, Any]:
    event["payload"] = json.loads(event.pop("payload_json"))
    return event


def _json_dump(value: Any, *, maximum: int) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("El payload debe ser JSON determinista válido.") from exc

    if len(encoded) > maximum:
        raise ValueError(
            f"El JSON supera el máximo permitido de {maximum} caracteres."
        )

    return encoded


def _step_id(value: str) -> str:
    clean = value.strip().casefold()
    if not clean:
        return ""
    if not _STEP_ID_RE.fullmatch(clean):
        raise ValueError("step_id inválido.")
    return clean


def _required(value: str, label: str, maximum: int) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{label} no puede estar vacío.")
    if len(clean) > maximum:
        raise ValueError(
            f"{label} supera el máximo de {maximum} caracteres."
        )
    return clean


def _require_grant_active_json(encoded: str) -> None:
    try:
        payload = json.loads(encoded)
        expires_raw = payload["expires_at"]
        if not isinstance(expires_raw, str):
            raise TypeError("expires_at must be a string")
        expires_at = datetime.fromisoformat(expires_raw)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PermissionError(
            "CapabilityGrant persistido inválido; autoridad denegada."
        ) from exc

    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise PermissionError(
            "CapabilityGrant persistido sin zona horaria; autoridad denegada."
        )

    if _utcnow() >= expires_at:
        raise PermissionError(
            "CapabilityGrant expirado; se requiere un nuevo grant explícito."
        )


def _transition_event(status: AutonomyRunStatus) -> str:
    return {
        AutonomyRunStatus.RUNNING: "run_started",
        AutonomyRunStatus.COMPLETED: "run_completed",
        AutonomyRunStatus.FAILED: "run_failed",
        AutonomyRunStatus.CANCELLED: "run_cancelled",
    }[status]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _now() -> str:
    return _utcnow().isoformat()
