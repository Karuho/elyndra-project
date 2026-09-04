from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import elyndra.autonomy.execution as execution_module
from elyndra.autonomy import (
    CancellationToken,
    Capability,
    CapabilityGrant,
    ExecutionBudget,
    ExecutionCancelled,
    ExecutionContract,
    ExecutionDenied,
    ExecutionOutcome,
    ExecutionResult,
    RunPlan,
    RunStep,
    WorkspaceScope,
)


def _workspace(tmp_path: Path) -> WorkspaceScope:
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text(
        "print('ok')\n",
        encoding="utf-8",
    )
    return WorkspaceScope.from_root(root)


def _grant(
    *capabilities: Capability,
    max_commands: int = 4,
    max_retries: int = 1,
    max_runtime_seconds: int = 120,
    allowed_hosts: tuple[str, ...] = (),
) -> CapabilityGrant:
    now = datetime.now(UTC)
    return CapabilityGrant(
        capabilities=frozenset(capabilities),
        issued_at=now,
        expires_at=now + timedelta(hours=1),
        max_steps=10,
        max_commands=max_commands,
        max_retries=max_retries,
        max_runtime_seconds=max_runtime_seconds,
        allowed_hosts=allowed_hosts,
    )


def test_budget_inherits_grant_limits() -> None:
    grant = _grant(
        Capability.WORKSPACE_READ,
        max_commands=3,
        max_retries=2,
        max_runtime_seconds=90,
    )

    budget = ExecutionBudget.from_grant(grant)
    snapshot = budget.snapshot()

    assert snapshot.max_commands == 3
    assert snapshot.max_retries == 2
    assert snapshot.max_runtime_seconds == 90
    assert snapshot.commands_remaining == 3
    assert snapshot.retries_remaining == 2
    assert snapshot.runtime_seconds_remaining == 90


def test_budget_rejects_negative_or_overreserved_state() -> None:
    with pytest.raises(ValueError, match="commands_reserved"):
        ExecutionBudget(
            max_commands=4,
            max_retries=1,
            max_runtime_seconds=60,
            commands_reserved=-1,
        )

    with pytest.raises(ValueError, match="retries_reserved"):
        ExecutionBudget(
            max_commands=4,
            max_retries=1,
            max_runtime_seconds=60,
            retries_reserved=2,
        )

    with pytest.raises(ValueError, match="runtime_seconds_reserved"):
        ExecutionBudget(
            max_commands=4,
            max_retries=1,
            max_runtime_seconds=60,
            runtime_seconds_reserved=61,
        )


def test_budget_rejects_non_integer_runtime_reservation() -> None:
    budget = ExecutionBudget(
        max_commands=4,
        max_retries=1,
        max_runtime_seconds=60,
    )

    with pytest.raises(TypeError, match="runtime_seconds"):
        budget.reserve(runtime_seconds=1.5)  # type: ignore[arg-type]

    assert budget.snapshot().commands_reserved == 0
    assert budget.snapshot().runtime_seconds_reserved == 0


def test_prepare_uses_exact_frozen_step_and_resolves_target(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    grant = _grant(Capability.WORKSPACE_READ)
    plan = RunPlan(
        objective="Inspect source",
        steps=(
            RunStep(
                step_id="inspect",
                capability=Capability.WORKSPACE_READ,
                action="inspect source",
                target="src/main.py",
            ),
        ),
    )

    contract = ExecutionContract(
        run_id="run-1",
        plan=plan,
        workspace=workspace,
        grant=grant,
    )

    prepared = contract.prepare("inspect", runtime_seconds=5)

    assert prepared.request.run_id == "run-1"
    assert prepared.request.step_id == "inspect"
    assert prepared.request.capability is Capability.WORKSPACE_READ
    assert prepared.request.action == "inspect source"
    assert prepared.request.target == "src/main.py"
    assert prepared.resolved_target == str(
        (workspace.root / "src" / "main.py").resolve()
    )
    assert prepared.budget.commands_reserved == 1
    assert prepared.budget.runtime_seconds_reserved == 5


def test_prepare_rejects_workspace_escape(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    grant = _grant(Capability.WORKSPACE_WRITE)
    plan = RunPlan(
        objective="Reject escape",
        steps=(
            RunStep(
                step_id="escape",
                capability=Capability.WORKSPACE_WRITE,
                action="write outside",
                target="../outside.txt",
            ),
        ),
    )

    contract = ExecutionContract(
        run_id="run-1",
        plan=plan,
        workspace=workspace,
        grant=grant,
    )

    with pytest.raises(PermissionError, match="escapa"):
        contract.prepare("escape")

    assert contract.budget.snapshot().commands_reserved == 0


def test_prepare_rejects_missing_capability(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    grant = _grant(Capability.WORKSPACE_READ)
    plan = RunPlan(
        objective="Controlled edit",
        steps=(
            RunStep(
                step_id="edit",
                capability=Capability.WORKSPACE_WRITE,
                action="prepare edit",
                target="src/new.py",
            ),
        ),
    )

    contract = ExecutionContract(
        run_id="run-1",
        plan=plan,
        workspace=workspace,
        grant=grant,
    )

    with pytest.raises(PermissionError, match="workspace.write"):
        contract.prepare("edit")

    assert contract.budget.snapshot().commands_reserved == 0


def test_prepare_rejects_expired_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    grant = _grant(Capability.WORKSPACE_READ)
    plan = RunPlan(
        objective="Inspect",
        steps=(
            RunStep(
                step_id="inspect",
                capability=Capability.WORKSPACE_READ,
                action="inspect",
                target="src/main.py",
            ),
        ),
    )

    contract = ExecutionContract(
        run_id="run-1",
        plan=plan,
        workspace=workspace,
        grant=grant,
    )

    future = grant.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(
        execution_module,
        "_utcnow",
        lambda: future,
    )

    with pytest.raises(PermissionError, match="expirada"):
        contract.prepare("inspect")

    assert contract.budget.snapshot().commands_reserved == 0


def test_human_gate_step_requires_trusted_approval(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    grant = _grant(Capability.WORKSPACE_WRITE)
    plan = RunPlan(
        objective="Prepare controlled write",
        steps=(
            RunStep(
                step_id="write",
                capability=Capability.WORKSPACE_WRITE,
                action="prepare write",
                target="src/new.py",
                requires_human_gate=True,
            ),
        ),
    )

    blocked = ExecutionContract(
        run_id="run-1",
        plan=plan,
        workspace=workspace,
        grant=grant,
    )

    with pytest.raises(ExecutionDenied, match="HumanGate"):
        blocked.prepare("write")

    allowed = ExecutionContract(
        run_id="run-1",
        plan=plan,
        workspace=workspace,
        grant=grant,
        approved_step_ids=frozenset({"write"}),
    )

    prepared = allowed.prepare("write")
    assert prepared.request.step_id == "write"


def test_cancellation_denies_without_consuming_budget(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    grant = _grant(Capability.WORKSPACE_READ)
    plan = RunPlan(
        objective="Inspect",
        steps=(
            RunStep(
                step_id="inspect",
                capability=Capability.WORKSPACE_READ,
                action="inspect",
                target="src/main.py",
            ),
        ),
    )
    token = CancellationToken()
    token.cancel("Owner cancelled.")

    contract = ExecutionContract(
        run_id="run-1",
        plan=plan,
        workspace=workspace,
        grant=grant,
        cancellation=token,
    )

    with pytest.raises(ExecutionCancelled, match="Owner cancelled"):
        contract.prepare("inspect")

    assert contract.budget.snapshot().commands_reserved == 0


def test_command_budget_is_fail_closed_without_partial_increment(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    grant = _grant(
        Capability.WORKSPACE_READ,
        max_commands=1,
    )
    plan = RunPlan(
        objective="Inspect",
        steps=(
            RunStep(
                step_id="inspect",
                capability=Capability.WORKSPACE_READ,
                action="inspect",
                target="src/main.py",
            ),
        ),
    )

    contract = ExecutionContract(
        run_id="run-1",
        plan=plan,
        workspace=workspace,
        grant=grant,
    )

    contract.prepare("inspect")

    with pytest.raises(ExecutionDenied, match="max_commands"):
        contract.prepare("inspect")

    assert contract.budget.snapshot().commands_reserved == 1


def test_retry_budget_is_fail_closed(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    grant = _grant(
        Capability.WORKSPACE_READ,
        max_commands=4,
        max_retries=1,
    )
    plan = RunPlan(
        objective="Inspect",
        steps=(
            RunStep(
                step_id="inspect",
                capability=Capability.WORKSPACE_READ,
                action="inspect",
                target="src/main.py",
            ),
        ),
    )

    contract = ExecutionContract(
        run_id="run-1",
        plan=plan,
        workspace=workspace,
        grant=grant,
    )

    contract.prepare("inspect", retry=True)

    with pytest.raises(ExecutionDenied, match="max_retries"):
        contract.prepare("inspect", retry=True)

    snapshot = contract.budget.snapshot()
    assert snapshot.commands_reserved == 1
    assert snapshot.retries_reserved == 1


def test_runtime_reservation_is_bounded(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    grant = _grant(
        Capability.WORKSPACE_READ,
        max_commands=3,
        max_runtime_seconds=10,
    )
    plan = RunPlan(
        objective="Inspect",
        steps=(
            RunStep(
                step_id="inspect",
                capability=Capability.WORKSPACE_READ,
                action="inspect",
                target="src/main.py",
            ),
        ),
    )

    contract = ExecutionContract(
        run_id="run-1",
        plan=plan,
        workspace=workspace,
        grant=grant,
    )

    contract.prepare("inspect", runtime_seconds=8)

    with pytest.raises(ExecutionDenied, match="max_runtime_seconds"):
        contract.prepare("inspect", runtime_seconds=3)

    snapshot = contract.budget.snapshot()
    assert snapshot.commands_reserved == 1
    assert snapshot.runtime_seconds_reserved == 8


def test_network_capability_requires_exact_allowed_host(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    grant = _grant(
        Capability.NETWORK_MODEL,
        allowed_hosts=("127.0.0.1",),
    )

    allowed_plan = RunPlan(
        objective="Use approved local model host",
        steps=(
            RunStep(
                step_id="model",
                capability=Capability.NETWORK_MODEL,
                action="contact model provider",
                target="127.0.0.1",
            ),
        ),
    )

    allowed = ExecutionContract(
        run_id="run-1",
        plan=allowed_plan,
        workspace=workspace,
        grant=grant,
    )

    assert allowed.prepare("model").resolved_target == "127.0.0.1"

    blocked_plan = RunPlan(
        objective="Reject another host",
        steps=(
            RunStep(
                step_id="model",
                capability=Capability.NETWORK_MODEL,
                action="contact model provider",
                target="example.com",
            ),
        ),
    )

    blocked = ExecutionContract(
        run_id="run-2",
        plan=blocked_plan,
        workspace=workspace,
        grant=grant,
    )

    with pytest.raises(ExecutionDenied, match="allowlist"):
        blocked.prepare("model")


def test_unknown_approved_step_is_rejected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    grant = _grant(Capability.WORKSPACE_READ)
    plan = RunPlan(
        objective="Inspect",
        steps=(
            RunStep(
                step_id="inspect",
                capability=Capability.WORKSPACE_READ,
                action="inspect",
                target="src/main.py",
            ),
        ),
    )

    with pytest.raises(ValueError, match="ajenos al plan"):
        ExecutionContract(
            run_id="run-1",
            plan=plan,
            workspace=workspace,
            grant=grant,
            approved_step_ids=frozenset({"invented"}),
        )


def test_execution_result_is_bounded_and_normalized() -> None:
    result = ExecutionResult(
        request_id="request-1",
        outcome="succeeded",
        summary="Operation completed.",
        exit_code=0,
        duration_ms=12,
    )

    assert result.outcome is ExecutionOutcome.SUCCEEDED
    assert result.duration_ms == 12

    with pytest.raises(ValueError, match="duration_ms"):
        ExecutionResult(
            request_id="request-2",
            outcome="failed",
            summary="Invalid duration.",
            duration_ms=-1,
        )
