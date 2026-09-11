from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from elyndra.autonomy.binding import AutonomyExecutionBinding
from elyndra.autonomy.bubblewrap_executor import BubblewrapExecutor
from elyndra.autonomy.capabilities import Capability
from elyndra.autonomy.execution import (
    CancellationToken,
    ExecutionCancelled,
    ExecutionDenied,
    ExecutionOutcome,
)
from elyndra.autonomy.models import AutonomyRunStatus
from elyndra.autonomy.repository import AutonomyRepository


class SupervisedTickOutcome(StrEnum):
    EXECUTED_SUCCEEDED = "executed_succeeded"
    EXECUTED_FAILED = "executed_failed"
    EXECUTED_CANCELLED = "executed_cancelled"
    RUN_COMPLETED = "run_completed"
    NOT_RUNNING = "not_running"
    BLOCKED_INCOMPLETE_ATTEMPT = "blocked_incomplete_attempt"
    BLOCKED_PREVIOUS_RESULT = "blocked_previous_result"
    BLOCKED_PREPARATION = "blocked_preparation"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"


@dataclass(frozen=True, slots=True)
class SupervisedTickResult:
    run_id: str
    outcome: SupervisedTickOutcome
    step_id: str = ""
    execution_outcome: ExecutionOutcome | None = None
    reason: str = ""


class SupervisedAutonomyRunner:
    """Advance one persisted run by zero or one supervised process launch."""

    def __init__(self, repository: AutonomyRepository, *, actor: str) -> None:
        if not isinstance(repository, AutonomyRepository):
            raise TypeError("repository debe ser AutonomyRepository.")
        clean_actor = actor.strip()
        if not clean_actor:
            raise ValueError("actor no puede estar vacío.")
        if len(clean_actor) > 200:
            raise ValueError("actor supera 200 caracteres.")
        self.repository = repository
        self.actor = clean_actor

    def tick(
        self,
        run_id: str,
        *,
        cancellation: CancellationToken | None = None,
    ) -> SupervisedTickResult:
        item = self.repository.get(run_id)
        if item is None:
            raise ValueError("AutonomyRun no encontrado.")
        if item.get("actor") != self.actor:
            raise PermissionError("El actor no es propietario del AutonomyRun.")

        status = AutonomyRunStatus(item["status"])
        if status is not AutonomyRunStatus.RUNNING:
            return SupervisedTickResult(
                run_id=run_id,
                outcome=SupervisedTickOutcome.NOT_RUNNING,
                reason=f"AutonomyRun está {status.value}.",
            )

        gaps = self.repository.execution_attempt_gaps(run_id, actor=self.actor)
        if gaps:
            return SupervisedTickResult(
                run_id=run_id,
                outcome=SupervisedTickOutcome.BLOCKED_INCOMPLETE_ATTEMPT,
                step_id=str(gaps[0]["step_id"]),
                reason=str(gaps[0]["state"]),
            )

        if self.repository.finalize_execution_run_if_ready(
            run_id,
            actor=self.actor,
        ):
            return SupervisedTickResult(
                run_id=run_id,
                outcome=SupervisedTickOutcome.RUN_COMPLETED,
            )

        token = cancellation or CancellationToken()
        contract = AutonomyExecutionBinding(self.repository).bind(
            run_id,
            actor=self.actor,
            cancellation=token,
        )
        results = self.repository.execution_results(
            run_id,
            actor=self.actor,
        )
        succeeded = {
            str(result["step_id"])
            for result in results
            if result["outcome"] == ExecutionOutcome.SUCCEEDED.value
        }

        next_step = next(
            (step for step in contract.plan.steps if step.step_id not in succeeded),
            None,
        )
        if next_step is None:
            if self.repository.finalize_execution_run_if_ready(
                run_id,
                actor=self.actor,
            ):
                return SupervisedTickResult(
                    run_id=run_id,
                    outcome=SupervisedTickOutcome.RUN_COMPLETED,
                )

            gaps = self.repository.execution_attempt_gaps(
                run_id,
                actor=self.actor,
            )
            if gaps:
                return SupervisedTickResult(
                    run_id=run_id,
                    outcome=(
                        SupervisedTickOutcome.BLOCKED_INCOMPLETE_ATTEMPT
                    ),
                    step_id=str(gaps[0]["step_id"]),
                    reason=str(gaps[0]["state"]),
                )

            return SupervisedTickResult(
                run_id=run_id,
                outcome=SupervisedTickOutcome.BLOCKED_PREPARATION,
                reason="durable_completion_not_ready",
            )

        previous = [
            result
            for result in results
            if result["step_id"] == next_step.step_id
        ]
        latest = previous[-1] if previous else None
        retry = bool(
            latest
            and latest["outcome"]
            in {ExecutionOutcome.FAILED.value, ExecutionOutcome.CANCELLED.value}
        )
        if retry and not self.repository.retry_review_available(
            run_id, next_step.step_id, actor=self.actor
        ):
            return SupervisedTickResult(
                run_id=run_id,
                outcome=SupervisedTickOutcome.BLOCKED_PREVIOUS_RESULT,
                step_id=next_step.step_id,
            )

        if next_step.capability is not Capability.PROCESS_EXEC:
            return SupervisedTickResult(
                run_id=run_id,
                outcome=SupervisedTickOutcome.UNSUPPORTED_CAPABILITY,
                step_id=next_step.step_id,
                reason=next_step.capability.value,
            )

        try:
            prepared = contract.prepare(next_step.step_id, retry=retry)
            result = BubblewrapExecutor(
                self.repository,
                actor=self.actor,
            ).execute(prepared, cancellation=token)
        except (ExecutionCancelled, ExecutionDenied, PermissionError) as exc:
            gaps = self.repository.execution_attempt_gaps(run_id, actor=self.actor)
            if gaps:
                return SupervisedTickResult(
                    run_id=run_id,
                    outcome=SupervisedTickOutcome.BLOCKED_INCOMPLETE_ATTEMPT,
                    step_id=str(gaps[0]["step_id"]),
                    reason=str(gaps[0]["state"]),
                )
            return SupervisedTickResult(
                run_id=run_id,
                outcome=SupervisedTickOutcome.BLOCKED_PREPARATION,
                step_id=next_step.step_id,
                reason=str(exc),
            )

        tick_outcome = {
            ExecutionOutcome.SUCCEEDED: SupervisedTickOutcome.EXECUTED_SUCCEEDED,
            ExecutionOutcome.FAILED: SupervisedTickOutcome.EXECUTED_FAILED,
            ExecutionOutcome.CANCELLED: SupervisedTickOutcome.EXECUTED_CANCELLED,
        }[result.outcome]

        if result.outcome is ExecutionOutcome.SUCCEEDED:
            self.repository.finalize_execution_run_if_ready(
                run_id,
                actor=self.actor,
            )

        return SupervisedTickResult(
            run_id=run_id,
            outcome=tick_outcome,
            step_id=next_step.step_id,
            execution_outcome=result.outcome,
        )
