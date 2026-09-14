from __future__ import annotations

import hmac
import re
from datetime import UTC, datetime
from typing import Any

from elyndra.autonomy.capabilities import Capability, CapabilityGrant
from elyndra.autonomy.commands import CommandSpec
from elyndra.autonomy.execution import (
    CancellationToken,
    ExecutionBudgetSnapshot,
    ExecutionContract,
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
from elyndra.autonomy.repository import AutonomyRepository
from elyndra.autonomy.scope import WorkspaceScope
from elyndra.autonomy.workspace_lease import WorkspaceLeaseCoordinator

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

_STEP_KEYS_V1 = frozenset(
    {
        "step_id",
        "capability",
        "action",
        "target",
        "requires_human_gate",
    }
)

_STEP_KEYS_V2 = _STEP_KEYS_V1 | {"command"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ExecutionBindingError(PermissionError):
    """Persisted autonomy state cannot safely become an execution contract."""


class _RepositoryExecutionReservationBackend:
    def __init__(
        self,
        repository: AutonomyRepository,
        *,
        actor: str,
    ) -> None:
        self.repository = repository
        self.actor = actor

    def reserve(
        self,
        request: ExecutionRequest,
        *,
        runtime_seconds: int = 0,
        retry: bool = False,
    ) -> ExecutionBudgetSnapshot:
        return self.repository.reserve_execution(
            request,
            actor=self.actor,
            runtime_seconds=runtime_seconds,
            retry=retry,
        )


class AutonomyExecutionBinding:
    """
    Trusted bridge from persisted AutonomyRun state to ExecutionContract.

    Human-gate approvals are derived only from repository state and its
    append-only audit events. Callers cannot supply approved step IDs.

    Phase 4 does not persist execution-budget consumption and does not execute
    anything.
    """

    def __init__(
        self,
        repository: AutonomyRepository,
        *,
        workspace_lease_coordinator: WorkspaceLeaseCoordinator | None = None,
    ) -> None:
        self.repository = repository
        self.workspace_lease_coordinator = workspace_lease_coordinator

    def bind(
        self,
        run_id: str,
        *,
        actor: str,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionContract:
        item = self.repository.get(run_id)
        if item is None:
            raise ExecutionBindingError("AutonomyRun persistido no encontrado.")

        trusted_run_id = _required_str(item.get("public_id"), "public_id", 128)
        trusted_actor = _required_str(item.get("actor"), "actor", 200)
        requested_actor = _required_str(actor, "actor", 200)

        if trusted_run_id != run_id.strip():
            raise ExecutionBindingError(
                "El identificador persistido del run es inconsistente."
            )

        if trusted_actor != requested_actor:
            raise ExecutionBindingError(
                "El actor no puede vincular un AutonomyRun de otro propietario."
            )

        try:
            status = AutonomyRunStatus(item.get("status"))
        except (TypeError, ValueError) as exc:
            raise ExecutionBindingError(
                "El estado persistido del AutonomyRun es inválido."
            ) from exc

        if status is not AutonomyRunStatus.RUNNING:
            raise ExecutionBindingError(
                "Solo un AutonomyRun running puede vincularse para ejecución."
            )

        started_at = _parse_datetime(item.get("started_at"), "started_at")
        if item.get("finished_at") is not None:
            raise ExecutionBindingError(
                "Un run running no puede tener finished_at."
            )

        created_at = _parse_datetime(item.get("created_at"), "created_at")

        try:
            workspace = WorkspaceScope.from_root(
                _required_str(
                    item.get("workspace_root"),
                    "workspace_root",
                    4096,
                )
            )
        except (OSError, PermissionError, ValueError) as exc:
            raise ExecutionBindingError(
                "El workspace persistido ya no es un scope válido."
            ) from exc

        grant = _rebuild_grant(item.get("grant"))
        plan = _rebuild_plan(item.get("plan"))

        objective = _required_str(item.get("objective"), "objective", 4000)
        if objective != plan.objective:
            raise ExecutionBindingError(
                "El objective persistido no coincide con el plan congelado."
            )

        if grant.is_expired(at=_utcnow()):
            raise ExecutionBindingError(
                "CapabilityGrant expirado; no puede vincularse para ejecución."
            )

        try:
            AutonomyRun(
                actor=trusted_actor,
                workspace=workspace,
                grant=grant,
                plan=plan,
                run_id=trusted_run_id,
                status=status,
                created_at=created_at,
            )
        except (PermissionError, TypeError, ValueError) as exc:
            raise ExecutionBindingError(
                "El snapshot persistido del AutonomyRun no supera "
                "las invariantes del dominio."
            ) from exc

        approved_step_ids = _approved_step_ids(
            item,
            plan=plan,
            repository=self.repository,
            run_id=trusted_run_id,
            actor=trusted_actor,
        )

        if started_at < created_at:
            raise ExecutionBindingError(
                "started_at no puede ser anterior a created_at."
            )

        budget = self.repository.execution_budget(
            trusted_run_id,
            actor=trusted_actor,
        )

        reservation_backend = _RepositoryExecutionReservationBackend(
            self.repository,
            actor=trusted_actor,
        )

        return ExecutionContract(
            run_id=trusted_run_id,
            plan=plan,
            workspace=workspace,
            grant=grant,
            approved_step_ids=approved_step_ids,
            budget=budget,
            cancellation=cancellation,
            reservation_backend=reservation_backend,
            workspace_lease_coordinator=self.workspace_lease_coordinator,
        )


def _approved_step_ids(
    item: dict[str, Any],
    *,
    plan: RunPlan,
    repository: AutonomyRepository,
    run_id: str,
    actor: str,
) -> frozenset[str]:
    events = item.get("events")
    gates = item.get("human_gates")

    if not isinstance(events, list):
        raise ExecutionBindingError("El audit de autonomía no es una lista válida.")

    if not isinstance(gates, list):
        raise ExecutionBindingError("Los HumanGate persistidos no son una lista válida.")

    steps = {step.step_id: step for step in plan.steps}

    request_events: dict[str, tuple[str, HumanGateKind]] = {}
    approval_events: set[str] = set()

    for raw_event in events:
        if not isinstance(raw_event, dict):
            raise ExecutionBindingError("Evento de autonomía persistido inválido.")

        event_type = raw_event.get("event_type")

        if event_type == "human_gate_approved":
            if (
                raw_event.get("from_status")
                != AutonomyRunStatus.WAITING_HUMAN.value
            ):
                raise ExecutionBindingError(
                    "Aprobación de HumanGate con from_status inconsistente."
                )

            if raw_event.get("to_status") != AutonomyRunStatus.RUNNING.value:
                raise ExecutionBindingError(
                    "Aprobación de HumanGate con to_status inconsistente."
                )

            payload = raw_event.get("payload")
            if not isinstance(payload, dict):
                raise ExecutionBindingError(
                    "Aprobación de HumanGate sin payload válido."
                )

            gate_id = _required_str(
                payload.get("gate_id"),
                "gate_id",
                128,
            )

            if payload.get("decision") != HumanGateStatus.APPROVED.value:
                raise ExecutionBindingError(
                    "Evento human_gate_approved con decision inconsistente."
                )

            if gate_id in approval_events:
                raise ExecutionBindingError(
                    "Un HumanGate tiene múltiples eventos de aprobación."
                )

            approval_events.add(gate_id)
            continue

        if event_type != "human_gate_requested":
            continue

        if raw_event.get("from_status") != AutonomyRunStatus.RUNNING.value:
            raise ExecutionBindingError(
                "HumanGate auditado con from_status inconsistente."
            )

        if raw_event.get("to_status") != AutonomyRunStatus.WAITING_HUMAN.value:
            raise ExecutionBindingError(
                "HumanGate auditado con to_status inconsistente."
            )

        payload = raw_event.get("payload")
        if not isinstance(payload, dict):
            raise ExecutionBindingError(
                "HumanGate auditado sin payload válido."
            )

        gate_id = _required_str(payload.get("gate_id"), "gate_id", 128)
        step_id = _optional_step_id(raw_event.get("step_id"))

        try:
            kind = HumanGateKind(payload.get("kind"))
        except (TypeError, ValueError) as exc:
            raise ExecutionBindingError(
                "HumanGate auditado con kind inválido."
            ) from exc

        if gate_id in request_events:
            raise ExecutionBindingError(
                "Un HumanGate tiene múltiples eventos de solicitud."
            )

        request_events[gate_id] = (step_id, kind)

    approved: set[str] = set()

    for raw_gate in gates:
        if not isinstance(raw_gate, dict):
            raise ExecutionBindingError("HumanGate persistido inválido.")

        gate_id = _required_str(raw_gate.get("public_id"), "gate_id", 128)

        try:
            status = HumanGateStatus(raw_gate.get("status"))
            kind = HumanGateKind(raw_gate.get("kind"))
        except (TypeError, ValueError) as exc:
            raise ExecutionBindingError(
                "HumanGate persistido con estado o kind inválido."
            ) from exc

        if status is HumanGateStatus.PENDING:
            raise ExecutionBindingError(
                "Un run running no puede conservar un HumanGate pending."
            )

        if status in {
            HumanGateStatus.REJECTED,
            HumanGateStatus.CANCELLED,
        }:
            raise ExecutionBindingError(
                "Un run running no puede contener un HumanGate terminal "
                "rechazado o cancelado."
            )

        if kind is HumanGateKind.MUTATION_REVIEW:
            _validate_approved_mutation_review(
                repository,
                events=events,
                gate_id=gate_id,
                run_id=run_id,
                actor=actor,
            )
            # Specialized mutation approval validates publication lineage only.
            # It never grants ordinary gated-step execution authority.
            continue

        evidence = request_events.get(gate_id)
        if evidence is None:
            raise ExecutionBindingError(
                "HumanGate aprobado sin evento append-only de solicitud."
            )

        step_id, audited_kind = evidence

        if audited_kind is not kind:
            raise ExecutionBindingError(
                "El kind del HumanGate no coincide con su audit."
            )

        if gate_id not in approval_events:
            raise ExecutionBindingError(
                "HumanGate aprobado sin evento append-only de aprobación."
            )

        if kind is HumanGateKind.RETRY_REVIEW:
            # Retry authority is consumed only by the durable reservation
            # transaction and can never approve a normal gated RunStep.
            continue

        # A generic HumanGate can resume a run, but it grants no step-specific
        # execution authority.
        if not step_id:
            continue

        step = steps.get(step_id)
        if step is None:
            raise ExecutionBindingError(
                "HumanGate aprobado para un step ajeno al plan congelado."
            )

        if not step.requires_human_gate:
            raise ExecutionBindingError(
                "HumanGate aprobado para un step que no requiere aprobación."
            )

        approved.add(step_id)

    return frozenset(approved)


def _validate_approved_mutation_review(
    repository: AutonomyRepository,
    *,
    events: list[dict[str, Any]],
    gate_id: str,
    run_id: str,
    actor: str,
) -> None:
    specialized = [
        event
        for event in events
        if event.get("event_type")
        in {"mutation_review_requested", "mutation_review_approved"}
    ]
    for event in specialized:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise ExecutionBindingError("Audit MUTATION_REVIEW sin payload válido.")
        _required_str(payload.get("gate_id"), "mutation gate_id", 128)
    requests = [
        event
        for event in events
        if event.get("event_type") == "mutation_review_requested"
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("gate_id") == gate_id
    ]
    approvals = [
        event
        for event in events
        if event.get("event_type") == "mutation_review_approved"
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("gate_id") == gate_id
    ]
    if len(requests) != 1 or len(approvals) != 1:
        raise ExecutionBindingError(
            "MUTATION_REVIEW aprobada requiere audit especializado exacto."
        )
    request, approval = requests[0], approvals[0]
    request_payload = request["payload"]
    approval_payload = approval["payload"]
    assert isinstance(request_payload, dict) and isinstance(approval_payload, dict)
    if frozenset(request_payload) != {
        "proposal_id",
        "proposal_sha256",
        "gate_id",
        "step_id",
        "state",
    } or frozenset(approval_payload) != {
        "proposal_id",
        "proposal_sha256",
        "gate_id",
        "step_id",
        "decision",
    }:
        raise ExecutionBindingError("Payload especializado MUTATION_REVIEW inválido.")
    request_sequence = _positive_int(request.get("sequence"), "mutation request sequence")
    approval_sequence = _positive_int(
        approval.get("sequence"), "mutation approval sequence"
    )
    request_step = _required_str(request.get("step_id"), "mutation step_id", 64)
    approval_step = _required_str(approval.get("step_id"), "mutation step_id", 64)
    proposal_id = _required_str(
        request_payload.get("proposal_id"), "mutation proposal_id", 128
    )
    approval_proposal_id = _required_str(
        approval_payload.get("proposal_id"), "mutation proposal_id", 128
    )
    proposal_sha256 = _required_sha256(
        request_payload.get("proposal_sha256"), "mutation proposal_sha256"
    )
    approval_sha256 = _required_sha256(
        approval_payload.get("proposal_sha256"), "mutation proposal_sha256"
    )
    if not (
        request_sequence < approval_sequence
        and request.get("from_status") == AutonomyRunStatus.RUNNING.value
        and request.get("to_status") == AutonomyRunStatus.WAITING_HUMAN.value
        and approval.get("from_status") == AutonomyRunStatus.WAITING_HUMAN.value
        and approval.get("to_status") == AutonomyRunStatus.WAITING_HUMAN.value
        and request_payload.get("state") == HumanGateStatus.PENDING.value
        and approval_payload.get("decision") == HumanGateStatus.APPROVED.value
        and request_payload.get("step_id") == request_step
        and approval_payload.get("step_id") == approval_step
        and request_step == approval_step
        and proposal_id == approval_proposal_id
        and hmac.compare_digest(proposal_sha256, approval_sha256)
    ):
        raise ExecutionBindingError("Audit especializado MUTATION_REVIEW inconsistente.")
    with repository.database.connect() as connection:
        rows = connection.execute(
            """SELECT binding.gate_id,binding.step_id AS binding_step_id,
                      binding.actor AS binding_actor,
                      binding.proposal_id,
                      binding.proposal_public_id AS binding_proposal_public_id,
                      binding.proposal_sha256 AS binding_proposal_sha256,
                      proposal.id AS durable_proposal_id,
                      proposal.public_id AS proposal_public_id,
                      proposal.proposal_sha256 AS proposal_sha256,
                      proposal.step_id AS proposal_step_id,
                      proposal.actor AS proposal_actor,proposal.run_id,
                      run.id AS durable_run_id,run.actor AS run_actor,
                      gate.kind,gate.status
               FROM assistant_autonomy_mutation_gate_bindings binding
               JOIN assistant_autonomy_mutation_proposals proposal
                 ON proposal.id=binding.proposal_id
               JOIN assistant_autonomy_runs run ON run.id=binding.run_id
                 AND proposal.run_id=run.id
               JOIN assistant_autonomy_human_gates gate ON gate.public_id=binding.gate_id
                 AND gate.run_id=run.id
               WHERE run.public_id=? AND binding.gate_id=?""",
            (run_id, gate_id),
        ).fetchall()
    if len(rows) != 1:
        raise ExecutionBindingError("MUTATION_REVIEW sin linaje durable exacto.")
    lineage = rows[0]
    if not (
        str(lineage["gate_id"]) == gate_id
        and str(lineage["kind"]) == HumanGateKind.MUTATION_REVIEW.value
        and str(lineage["status"]) == HumanGateStatus.APPROVED.value
        and int(lineage["proposal_id"]) == int(lineage["durable_proposal_id"])
        and str(lineage["binding_proposal_public_id"]) == proposal_id
        and str(lineage["proposal_public_id"]) == proposal_id
        and str(lineage["binding_proposal_sha256"]) == proposal_sha256
        and str(lineage["proposal_sha256"]) == proposal_sha256
        and str(lineage["binding_step_id"]) == request_step
        and str(lineage["proposal_step_id"]) == request_step
        and int(lineage["run_id"]) == int(lineage["durable_run_id"])
        and str(lineage["binding_actor"]) == actor
        and str(lineage["proposal_actor"]) == actor
        and str(lineage["run_actor"]) == actor
    ):
        raise ExecutionBindingError("Linaje durable MUTATION_REVIEW inconsistente.")


def _rebuild_grant(raw: object) -> CapabilityGrant:
    if not isinstance(raw, dict):
        raise ExecutionBindingError(
            "grant debe ser un objeto JSON."
        )

    payload = raw
    keys = frozenset(payload)
    if keys not in {_GRANT_KEYS_V1, _GRANT_KEYS_V2}:
        raise ExecutionBindingError(
            "grant tiene campos inesperados o faltantes."
        )

    capabilities_raw = _exact_list(
        payload["capabilities"],
        "grant.capabilities",
    )
    allowed_hosts_raw = _exact_list(
        payload["allowed_hosts"],
        "grant.allowed_hosts",
    )

    allowed_executables_raw = _exact_list(
        payload.get("allowed_executables", []),
        "grant.allowed_executables",
    )

    try:
        capabilities = frozenset(
            Capability(
                _required_str(
                    value,
                    "grant.capability",
                    128,
                )
            )
            for value in capabilities_raw
        )

        allowed_hosts = tuple(
            _required_str(
                value,
                "grant.allowed_host",
                255,
            )
            for value in allowed_hosts_raw
        )

        grant = CapabilityGrant(
            capabilities=capabilities,
            issued_at=_parse_datetime(
                payload["issued_at"],
                "grant.issued_at",
            ),
            expires_at=_parse_datetime(
                payload["expires_at"],
                "grant.expires_at",
            ),
            max_steps=_exact_int(payload["max_steps"], "grant.max_steps"),
            max_retries=_exact_int(
                payload["max_retries"],
                "grant.max_retries",
            ),
            max_commands=_exact_int(
                payload["max_commands"],
                "grant.max_commands",
            ),
            max_runtime_seconds=_exact_int(
                payload["max_runtime_seconds"],
                "grant.max_runtime_seconds",
            ),
            allowed_hosts=allowed_hosts,
            allowed_executables=tuple(
                _required_str(
                    value,
                    "grant.allowed_executable",
                    4096,
                )
                for value in allowed_executables_raw
            ),
        )
    except (PermissionError, TypeError, ValueError) as exc:
        raise ExecutionBindingError(
            "CapabilityGrant persistido inválido."
        ) from exc

    return grant


def _rebuild_plan(raw: object) -> RunPlan:
    payload = _exact_dict(raw, "plan", _PLAN_KEYS)
    steps_raw = _exact_list(payload["steps"], "plan.steps")

    steps: list[RunStep] = []

    try:
        for raw_step in steps_raw:
            if not isinstance(raw_step, dict):
                raise ExecutionBindingError(
                    "plan.step debe ser un objeto JSON."
                )

            step = raw_step
            step_keys = frozenset(step)

            if step_keys not in {
                _STEP_KEYS_V1,
                _STEP_KEYS_V2,
            }:
                raise ExecutionBindingError(
                    "plan.step tiene campos inesperados o faltantes."
                )

            requires_gate = step["requires_human_gate"]
            if not isinstance(requires_gate, bool):
                raise TypeError(
                    "plan.step.requires_human_gate debe ser booleano."
                )

            raw_command = step.get("command")
            command = (
                None
                if raw_command is None
                else CommandSpec.from_data(raw_command)
            )

            steps.append(
                RunStep(
                    step_id=_required_str(
                        step["step_id"],
                        "plan.step.step_id",
                        64,
                    ),
                    capability=Capability(
                        _required_str(
                            step["capability"],
                            "plan.step.capability",
                            128,
                        )
                    ),
                    action=_required_str(
                        step["action"],
                        "plan.step.action",
                        160,
                    ),
                    target=_optional_str(
                        step["target"],
                        "plan.step.target",
                        2000,
                    ),
                    requires_human_gate=requires_gate,
                    command=command,
                )
            )

        return RunPlan(
            objective=_required_str(
                payload["objective"],
                "plan.objective",
                4000,
            ),
            steps=tuple(steps),
        )
    except (PermissionError, TypeError, ValueError) as exc:
        raise ExecutionBindingError(
            "RunPlan persistido inválido."
        ) from exc


def _exact_dict(
    value: object,
    label: str,
    expected_keys: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExecutionBindingError(f"{label} debe ser un objeto JSON.")

    keys = frozenset(value)
    if keys != expected_keys:
        raise ExecutionBindingError(
            f"{label} tiene campos inesperados o faltantes."
        )

    return value


def _exact_list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ExecutionBindingError(f"{label} debe ser una lista.")
    return value


def _exact_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExecutionBindingError(f"{label} debe ser un entero.")
    return value


def _positive_int(value: object, label: str) -> int:
    result = _exact_int(value, label)
    if result <= 0:
        raise ExecutionBindingError(f"{label} debe ser positivo.")
    return result


def _required_sha256(value: object, label: str) -> str:
    result = _required_str(value, label, 64)
    if not _SHA256_RE.fullmatch(result):
        raise ExecutionBindingError(f"{label} debe ser SHA-256 hexadecimal minúsculo.")
    return result


def _required_str(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ExecutionBindingError(f"{label} debe ser texto.")

    clean = value.strip()
    if not clean:
        raise ExecutionBindingError(f"{label} no puede estar vacío.")

    if len(clean) > maximum:
        raise ExecutionBindingError(
            f"{label} supera el máximo de {maximum} caracteres."
        )

    return clean


def _optional_str(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ExecutionBindingError(f"{label} debe ser texto.")

    clean = value.strip()
    if len(clean) > maximum:
        raise ExecutionBindingError(
            f"{label} supera el máximo de {maximum} caracteres."
        )

    return clean


def _optional_step_id(value: object) -> str:
    if not isinstance(value, str):
        raise ExecutionBindingError("step_id auditado debe ser texto.")

    clean = value.strip().casefold()
    if len(clean) > 64:
        raise ExecutionBindingError("step_id auditado supera 64 caracteres.")

    return clean


def _parse_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ExecutionBindingError(f"{label} debe ser un timestamp.")

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ExecutionBindingError(
            f"{label} no es un timestamp ISO válido."
        ) from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExecutionBindingError(
            f"{label} debe incluir zona horaria."
        )

    return parsed


def _utcnow() -> datetime:
    return datetime.now(UTC)
