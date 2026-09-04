"""Bounded autonomy domain primitives for Elyndra."""

from elyndra.autonomy.capabilities import Capability, CapabilityGrant
from elyndra.autonomy.execution import (
    CancellationToken,
    ExecutionBudget,
    ExecutionBudgetSnapshot,
    ExecutionCancelled,
    ExecutionContract,
    ExecutionDenied,
    ExecutionOutcome,
    ExecutionRequest,
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
    "AutonomyRepository",
    "AutonomyRun",
    "AutonomyRunStatus",
    "CancellationToken",
    "Capability",
    "CapabilityGrant",
    "ExecutionBudget",
    "ExecutionBudgetSnapshot",
    "ExecutionCancelled",
    "ExecutionContract",
    "ExecutionDenied",
    "ExecutionOutcome",
    "ExecutionRequest",
    "ExecutionResult",
    "Executor",
    "HumanGate",
    "HumanGateKind",
    "HumanGateStatus",
    "PreparedExecution",
    "RunPlan",
    "RunStep",
    "WorkspaceScope",
]
