"""Bounded autonomy domain primitives for Elyndra."""

from elyndra.autonomy.binding import (
    AutonomyExecutionBinding,
    ExecutionBindingError,
)
from elyndra.autonomy.bubblewrap_executor import BubblewrapExecutor
from elyndra.autonomy.capabilities import Capability, CapabilityGrant
from elyndra.autonomy.commands import (
    CommandEnvironmentProfile,
    CommandSandboxProfile,
    CommandSnapshot,
    CommandSpec,
    CommandStdinPolicy,
    ExecutableIdentity,
)
from elyndra.autonomy.execution import (
    CancellationToken,
    ExecutionBudget,
    ExecutionBudgetSnapshot,
    ExecutionCancelled,
    ExecutionContract,
    ExecutionDenied,
    ExecutionOutcome,
    ExecutionRequest,
    ExecutionReservationBackend,
    ExecutionResult,
    Executor,
    PreparedExecution,
)
from elyndra.autonomy.models import (
    AutonomyRun,
    AutonomyRunStatus,
    HumanGate,
    HumanGateKind,
    HumanGateStatus,
    RunPlan,
    RunStep,
)
from elyndra.autonomy.repository import AutonomyRepository
from elyndra.autonomy.scope import WorkspaceScope

__all__ = [
    "AutonomyExecutionBinding",
    "AutonomyRepository",
    "AutonomyRun",
    "AutonomyRunStatus",
    "BubblewrapExecutor",
    "CancellationToken",
    "Capability",
    "CapabilityGrant",
    "ExecutionBindingError",
    "ExecutionBudget",
    "ExecutionBudgetSnapshot",
    "ExecutionCancelled",
    "ExecutionContract",
    "ExecutionDenied",
    "ExecutionOutcome",
    "ExecutionRequest",
    "ExecutionReservationBackend",
    "ExecutionResult",
    "Executor",
    "HumanGate",
    "HumanGateKind",
    "HumanGateStatus",
    "PreparedExecution",
    "RunPlan",
    "RunStep",
    "WorkspaceScope",
    "CommandEnvironmentProfile",
    "CommandSandboxProfile",
    "CommandSnapshot",
    "CommandSpec",
    "CommandStdinPolicy",
    "ExecutableIdentity",
]
