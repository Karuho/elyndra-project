from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from elyndra.autonomy.capabilities import Capability, CapabilityGrant
from elyndra.autonomy.commands import CommandSnapshot, CommandSpec
from elyndra.autonomy.execution import (
    ExecutionBudget,
    ExecutionBudgetSnapshot,
    ExecutionRequest,
)
from elyndra.autonomy.models import (
    AutonomyRun,
    AutonomyRunStatus,
    HumanGateKind,
    HumanGateStatus,
    RunPlan,
    RunStep,
)
from elyndra.autonomy.scope import WorkspaceScope
from elyndra.db import Database

_STEP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_GRANT_KEYS_V1 = frozenset(
    {
        "capabilities",
        "issued_at",
        "expires_at",
        "max_steps",
        "max_retries",
        "max_commands",
        "max_runtime_seconds",
        "allowed_hosts",
    }
)

_GRANT_KEYS_V2 = _GRANT_KEYS_V1 | {"allowed_executables"}

_PLAN_KEYS = frozenset({"objective", "steps"})

_PLAN_STEP_KEYS_V1 = frozenset(
    {
        "step_id",
        "capability",
        "action",
        "target",
        "requires_human_gate",
    }
)

_PLAN_STEP_KEYS_V2 = _PLAN_STEP_KEYS_V1 | {"command"}

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

    def execution_budget(
        self,
        run_id: str,
        *,
        actor: str,
    ) -> ExecutionBudget:
        with self.database.connect() as connection:
            row = self._owned_run(
                connection,
                run_id,
                actor=actor,
            )
            grant = _grant_from_json(str(row["grant_json"]))
            return self._execution_budget_from_connection(
                connection,
                run_db_id=int(row["id"]),
                grant=grant,
            )

    def reserve_execution(
        self,
        request: ExecutionRequest,
        *,
        actor: str,
        runtime_seconds: int = 0,
        retry: bool = False,
    ) -> ExecutionBudgetSnapshot:
        if not isinstance(request, ExecutionRequest):
            raise TypeError("request debe ser un ExecutionRequest.")

        if isinstance(runtime_seconds, bool) or not isinstance(
            runtime_seconds,
            int,
        ):
            raise TypeError("runtime_seconds debe ser un entero.")

        if runtime_seconds < 0:
            raise ValueError("runtime_seconds no puede ser negativo.")

        if not isinstance(retry, bool):
            raise TypeError("retry debe ser booleano.")

        request_sha256 = _reservation_sha256(
            request,
            runtime_seconds=runtime_seconds,
            retry=retry,
        )

        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")

            row = self._owned_run(
                connection,
                request.run_id,
                actor=actor,
            )

            if str(row["status"]) != AutonomyRunStatus.RUNNING.value:
                raise PermissionError(
                    "Solo un AutonomyRun running puede reservar ejecución."
                )

            grant = _grant_from_json(str(row["grant_json"]))
            if grant.is_expired(at=_utcnow()):
                raise PermissionError(
                    "CapabilityGrant expirado; no puede reservar ejecución."
                )

            plan = _plan_from_json(str(row["plan_json"]))
            step = next(
                (
                    item
                    for item in plan.steps
                    if item.step_id == request.step_id
                ),
                None,
            )

            if step is None:
                raise PermissionError(
                    "ExecutionRequest apunta a un step ajeno "
                    "al plan congelado."
                )

            if (
                step.capability is not request.capability
                or step.action != request.action
                or step.target != request.target
                or step.requires_human_gate
                is not request.requires_human_gate
            ):
                raise PermissionError(
                    "ExecutionRequest no coincide con el step congelado."
                )

            if step.requires_human_gate:
                self._require_step_human_gate_approved(
                    connection,
                    run_db_id=int(row["id"]),
                    step_id=step.step_id,
                )

            if step.capability is Capability.PROCESS_EXEC:
                command = step.command
                if command is None:
                    raise PermissionError(
                        "process.exec persistido no tiene CommandSpec."
                    )

                if not grant.allows_executable(command.executable):
                    raise PermissionError(
                        "Ejecutable fuera del allowlist persistido."
                    )

                if command.timeout_seconds > grant.max_runtime_seconds:
                    raise PermissionError(
                        "El timeout del comando excede el grant persistido."
                    )

                if runtime_seconds != command.timeout_seconds:
                    raise PermissionError(
                        "process.exec requiere reservar el timeout exacto "
                        "del CommandSpec."
                    )

                try:
                    workspace = WorkspaceScope.from_root(
                        str(row["workspace_root"])
                    )
                    resolved_cwd = workspace.resolve(
                        command.cwd,
                        must_exist=True,
                    )
                    current_snapshot = CommandSnapshot.capture(
                        command,
                        resolved_cwd=resolved_cwd,
                    )
                except (OSError, PermissionError, ValueError) as exc:
                    raise PermissionError(
                        "No se pudo revalidar CommandSnapshot "
                        "desde autoridad persistida."
                    ) from exc

                if (
                    current_snapshot.command_sha256
                    != request.command_sha256
                ):
                    raise PermissionError(
                        "ExecutionRequest no coincide con "
                        "CommandSnapshot actual."
                    )

            existing = connection.execute(
                """
                SELECT
                    run_id,
                    request_sha256,
                    command_sha256
                FROM assistant_autonomy_execution_reservations
                WHERE request_id = ?
                """,
                (request.request_id,),
            ).fetchone()

            if existing is not None:
                stored_command_sha256 = (
                    ""
                    if existing["command_sha256"] is None
                    else str(existing["command_sha256"])
                )

                if (
                    int(existing["run_id"]) != int(row["id"])
                    or str(existing["request_sha256"])
                    != request_sha256
                    or stored_command_sha256
                    != request.command_sha256
                ):
                    raise PermissionError(
                        "request_id reutilizado con una reserva diferente."
                    )

                return self._execution_budget_from_connection(
                    connection,
                    run_db_id=int(row["id"]),
                    grant=grant,
                ).snapshot()

            budget = self._execution_budget_from_connection(
                connection,
                run_db_id=int(row["id"]),
                grant=grant,
            )

            snapshot = budget.reserve(
                runtime_seconds=runtime_seconds,
                retry=retry,
            )

            sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM assistant_autonomy_execution_reservations
                    WHERE run_id = ?
                    """,
                    (int(row["id"]),),
                ).fetchone()[0]
            )

            connection.execute(
                """
                INSERT INTO assistant_autonomy_execution_reservations(
                    request_id,
                    request_sha256,
                    run_id,
                    sequence,
                    step_id,
                    capability,
                    command_sha256,
                    runtime_seconds,
                    is_retry,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.request_id,
                    request_sha256,
                    int(row["id"]),
                    sequence,
                    request.step_id,
                    request.capability.value,
                    request.command_sha256 or None,
                    runtime_seconds,
                    1 if retry else 0,
                    _now(),
                ),
            )

            return snapshot

    @staticmethod
    def _require_step_human_gate_approved(
        connection: Any,
        *,
        run_db_id: int,
        step_id: str,
    ) -> None:
        request_events = connection.execute(
            """
            SELECT
                sequence,
                payload_json
            FROM assistant_autonomy_events
            WHERE
                run_id = ?
                AND event_type = 'human_gate_requested'
                AND step_id = ?
            ORDER BY sequence ASC
            """,
            (
                run_db_id,
                step_id,
            ),
        ).fetchall()

        if not request_events:
            raise PermissionError(
                f"El step {step_id} requiere HumanGate aprobado."
            )

        approval_events = connection.execute(
            """
            SELECT
                sequence,
                payload_json
            FROM assistant_autonomy_events
            WHERE
                run_id = ?
                AND event_type = 'human_gate_approved'
            ORDER BY sequence ASC
            """,
            (run_db_id,),
        ).fetchall()

        parsed_approvals: dict[str, int] = {}

        for event in approval_events:
            try:
                payload = json.loads(str(event["payload_json"]))
            except json.JSONDecodeError as exc:
                raise PermissionError(
                    "Audit de aprobación de HumanGate inválido."
                ) from exc

            if not isinstance(payload, dict):
                raise PermissionError(
                    "Audit de aprobación de HumanGate inválido."
                )

            gate_id = payload.get("gate_id")
            decision = payload.get("decision")

            if not isinstance(gate_id, str) or not gate_id.strip():
                raise PermissionError(
                    "Audit de aprobación de HumanGate sin gate_id válido."
                )

            if decision != HumanGateStatus.APPROVED.value:
                raise PermissionError(
                    "Evento human_gate_approved con decision inconsistente."
                )

            if gate_id in parsed_approvals:
                raise PermissionError(
                    "HumanGate con múltiples eventos de aprobación."
                )

            parsed_approvals[gate_id] = int(event["sequence"])

        for event in request_events:
            try:
                payload = json.loads(str(event["payload_json"]))
            except json.JSONDecodeError as exc:
                raise PermissionError(
                    "Audit de solicitud de HumanGate inválido."
                ) from exc

            if not isinstance(payload, dict):
                raise PermissionError(
                    "Audit de solicitud de HumanGate inválido."
                )

            gate_id = payload.get("gate_id")
            kind = payload.get("kind")

            if not isinstance(gate_id, str) or not gate_id.strip():
                raise PermissionError(
                    "Audit de solicitud de HumanGate sin gate_id válido."
                )

            try:
                audited_kind = HumanGateKind(kind)
            except (TypeError, ValueError) as exc:
                raise PermissionError(
                    "Audit de solicitud de HumanGate con kind inválido."
                ) from exc

            gate = connection.execute(
                """
                SELECT
                    kind,
                    status
                FROM assistant_autonomy_human_gates
                WHERE
                    run_id = ?
                    AND public_id = ?
                """,
                (
                    run_db_id,
                    gate_id,
                ),
            ).fetchone()

            if gate is None:
                continue

            if str(gate["status"]) != HumanGateStatus.APPROVED.value:
                continue

            if str(gate["kind"]) != audited_kind.value:
                raise PermissionError(
                    "El kind del HumanGate no coincide con su audit."
                )

            approval_sequence = parsed_approvals.get(gate_id)
            if approval_sequence is None:
                continue

            if approval_sequence <= int(event["sequence"]):
                raise PermissionError(
                    "La aprobación de HumanGate precede o coincide "
                    "con su solicitud."
                )

            return

        raise PermissionError(
            f"El step {step_id} requiere HumanGate aprobado."
        )

    @staticmethod
    def _execution_budget_from_connection(
        connection: Any,
        *,
        run_db_id: int,
        grant: CapabilityGrant,
    ) -> ExecutionBudget:
        usage = connection.execute(
            """
            SELECT
                COUNT(*) AS commands_reserved,
                COALESCE(SUM(is_retry), 0) AS retries_reserved,
                COALESCE(SUM(runtime_seconds), 0)
                    AS runtime_seconds_reserved
            FROM assistant_autonomy_execution_reservations
            WHERE run_id = ?
            """,
            (run_db_id,),
        ).fetchone()

        if usage is None:
            raise RuntimeError(
                "No se pudo calcular el execution budget persistido."
            )

        try:
            return ExecutionBudget(
                max_commands=grant.max_commands,
                max_retries=grant.max_retries,
                max_runtime_seconds=grant.max_runtime_seconds,
                commands_reserved=int(usage["commands_reserved"]),
                retries_reserved=int(usage["retries_reserved"]),
                runtime_seconds_reserved=int(
                    usage["runtime_seconds_reserved"]
                ),
            )
        except (TypeError, ValueError) as exc:
            raise PermissionError(
                "El ledger persistido excede o viola el CapabilityGrant."
            ) from exc

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
        "allowed_executables": list(grant.allowed_executables),
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
                "command": (
                    step.command.to_data()
                    if step.command is not None
                    else None
                ),
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
    grant = _grant_from_json(encoded)

    if grant.is_expired(at=_utcnow()):
        raise PermissionError(
            "CapabilityGrant expirado; se requiere un nuevo grant explícito."
        )


def _grant_from_json(encoded: str) -> CapabilityGrant:
    try:
        payload = json.loads(encoded)

        if not isinstance(payload, dict):
            raise TypeError("grant must be an object")

        keys = frozenset(payload)
        if keys not in {_GRANT_KEYS_V1, _GRANT_KEYS_V2}:
            raise ValueError("grant fields mismatch")

        capabilities_raw = payload["capabilities"]
        allowed_hosts_raw = payload["allowed_hosts"]
        allowed_executables_raw = payload.get(
            "allowed_executables",
            [],
        )

        if not isinstance(allowed_executables_raw, list):
            raise TypeError("allowed_executables must be a list")

        if not isinstance(capabilities_raw, list):
            raise TypeError("capabilities must be a list")

        if not isinstance(allowed_hosts_raw, list):
            raise TypeError("allowed_hosts must be a list")

        grant = CapabilityGrant(
            capabilities=frozenset(
                Capability(_required(item, "capability", 128))
                for item in capabilities_raw
            ),
            issued_at=_parse_iso_datetime(
                payload["issued_at"],
                "issued_at",
            ),
            expires_at=_parse_iso_datetime(
                payload["expires_at"],
                "expires_at",
            ),
            max_steps=_json_int(
                payload["max_steps"],
                "max_steps",
            ),
            max_retries=_json_int(
                payload["max_retries"],
                "max_retries",
            ),
            max_commands=_json_int(
                payload["max_commands"],
                "max_commands",
            ),
            max_runtime_seconds=_json_int(
                payload["max_runtime_seconds"],
                "max_runtime_seconds",
            ),
            allowed_hosts=tuple(
                _required(item, "allowed_host", 255)
                for item in allowed_hosts_raw
            ),
            allowed_executables=tuple(
                _required(item, "allowed_executable", 4096)
                for item in allowed_executables_raw
            ),
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise PermissionError(
            "CapabilityGrant persistido inválido; autoridad denegada."
        ) from exc

    return grant


def _plan_from_json(encoded: str) -> RunPlan:
    try:
        payload = json.loads(encoded)

        if not isinstance(payload, dict):
            raise TypeError("plan must be an object")

        if frozenset(payload) != _PLAN_KEYS:
            raise ValueError("plan fields mismatch")

        steps_raw = payload["steps"]
        if not isinstance(steps_raw, list):
            raise TypeError("steps must be a list")

        steps: list[RunStep] = []

        for raw_step in steps_raw:
            if not isinstance(raw_step, dict):
                raise TypeError("step must be an object")

            step_keys = frozenset(raw_step)
            if step_keys not in {
                _PLAN_STEP_KEYS_V1,
                _PLAN_STEP_KEYS_V2,
            }:
                raise ValueError("step fields mismatch")

            requires_gate = raw_step["requires_human_gate"]
            if not isinstance(requires_gate, bool):
                raise TypeError(
                    "requires_human_gate must be boolean"
                )

            target = raw_step["target"]
            if not isinstance(target, str):
                raise TypeError("target must be text")

            raw_command = raw_step.get("command")

            command = (
                None
                if raw_command is None
                else CommandSpec.from_data(raw_command)
            )

            steps.append(
                RunStep(
                    step_id=_required(
                        raw_step["step_id"],
                        "step_id",
                        64,
                    ),
                    capability=Capability(
                        _required(
                            raw_step["capability"],
                            "capability",
                            128,
                        )
                    ),
                    action=_required(
                        raw_step["action"],
                        "action",
                        160,
                    ),
                    target=target,
                    requires_human_gate=requires_gate,
                    command=command,
                )
            )

        return RunPlan(
            objective=_required(
                payload["objective"],
                "objective",
                4_000,
            ),
            steps=tuple(steps),
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise PermissionError(
            "RunPlan persistido inválido; autoridad denegada."
        ) from exc


def _reservation_sha256(
    request: ExecutionRequest,
    *,
    runtime_seconds: int,
    retry: bool,
) -> str:
    payload = {
        "request_id": request.request_id,
        "run_id": request.run_id,
        "step_id": request.step_id,
        "capability": request.capability.value,
        "action": request.action,
        "target": request.target,
        "requires_human_gate": request.requires_human_gate,
        "runtime_seconds": runtime_seconds,
        "retry": retry,
    }

    # Keep the pre-schema-54 fingerprint byte-compatible for non-process
    # reservations so an old request_id remains idempotently replayable.
    if request.command_sha256:
        payload["command_sha256"] = request.command_sha256

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def _parse_iso_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be text")

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include timezone")

    return parsed


def _json_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    return value


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
