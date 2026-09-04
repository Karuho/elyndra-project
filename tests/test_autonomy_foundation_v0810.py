from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from elyndra.autonomy import (
    AutonomyRun,
    Capability,
    CapabilityGrant,
    HumanGate,
    HumanGateStatus,
    RunPlan,
    RunStep,
    WorkspaceScope,
)
from elyndra.policy.authorization import (
    AuthorizationDecision,
    AuthorizationScope,
)


def _grant(
    *capabilities: Capability,
    max_steps: int = 40,
    expires_delta: timedelta = timedelta(hours=1),
) -> CapabilityGrant:
    now = datetime.now(UTC)
    return CapabilityGrant(
        capabilities=frozenset(capabilities),
        issued_at=now,
        expires_at=now + expires_delta,
        max_steps=max_steps,
    )


def test_workspace_scope_resolves_inside_exact_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    file_path = root / "src" / "example.py"
    file_path.parent.mkdir()
    file_path.write_text("print('ok')\n", encoding="utf-8")

    scope = WorkspaceScope.from_root(root)

    assert scope.root == root.resolve()
    assert scope.resolve("src/example.py", must_exist=True) == file_path.resolve()
    assert scope.contains(file_path)


def test_workspace_scope_rejects_parent_escape(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    scope = WorkspaceScope.from_root(root)

    with pytest.raises(PermissionError):
        scope.resolve("../outside.txt")


def test_workspace_scope_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()

    (root / "escape").symlink_to(outside, target_is_directory=True)

    scope = WorkspaceScope.from_root(root)

    with pytest.raises(PermissionError):
        scope.resolve("escape/secret.txt")


def test_workspace_scope_rejects_home_as_autonomy_root() -> None:
    with pytest.raises(ValueError):
        WorkspaceScope.from_root(Path.home())


def test_workspace_scope_can_be_created_from_project_authorization(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    decision = AuthorizationDecision(
        allowed=True,
        scope=AuthorizationScope.PROJECT_ONCE,
        source="explicit_approval",
        resolved_path=root.resolve(),
        project_root=root.resolve(),
        reason="test",
        expires_after_execution=True,
    )

    scope = WorkspaceScope.from_authorization(decision)

    assert scope.root == root.resolve()


def test_workspace_scope_rejects_denied_authorization(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    decision = AuthorizationDecision(
        allowed=False,
        scope=AuthorizationScope.PROJECT_ONCE,
        source="explicit_approval_required",
        resolved_path=root.resolve(),
        project_root=root.resolve(),
        reason="test",
        expires_after_execution=True,
    )

    with pytest.raises(PermissionError):
        WorkspaceScope.from_authorization(decision)


def test_capability_grant_is_default_deny() -> None:
    grant = _grant(Capability.WORKSPACE_READ)

    assert grant.allows(Capability.WORKSPACE_READ)
    assert not grant.allows(Capability.WORKSPACE_WRITE)
    assert not grant.allows("model.invented.capability")

    with pytest.raises(PermissionError):
        grant.require(Capability.WORKSPACE_WRITE)


def test_expired_capability_grant_denies_authority() -> None:
    now = datetime.now(UTC)
    grant = CapabilityGrant(
        capabilities=frozenset({Capability.WORKSPACE_READ}),
        issued_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1),
    )

    assert grant.is_expired()
    assert not grant.allows(Capability.WORKSPACE_READ)

    with pytest.raises(PermissionError):
        grant.require(Capability.WORKSPACE_READ)


def test_capability_grant_rejects_unknown_capability() -> None:
    now = datetime.now(UTC)

    with pytest.raises(ValueError, match="Capability no registrada"):
        CapabilityGrant(
            capabilities=frozenset({"model.grant.me.root"}),  # type: ignore[arg-type]
            issued_at=now,
            expires_at=now + timedelta(hours=1),
        )


def test_capability_grant_uses_exact_host_allowlist() -> None:
    now = datetime.now(UTC)
    grant = CapabilityGrant(
        capabilities=frozenset({Capability.GIT_PUSH}),
        issued_at=now,
        expires_at=now + timedelta(hours=1),
        allowed_hosts=("GitHub.com", "127.0.0.1"),
    )

    assert grant.allowed_hosts == ("github.com", "127.0.0.1")
    assert grant.allows_host("github.com")
    assert grant.allows_host("GITHUB.COM")
    assert not grant.allows_host("api.github.com")
    assert not grant.allows_host("evilgithub.com")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("max_steps", 0),
        ("max_steps", 501),
        ("max_retries", -1),
        ("max_retries", 11),
        ("max_commands", 0),
        ("max_commands", 2_001),
        ("max_runtime_seconds", 0),
        ("max_runtime_seconds", 86_401),
    ),
)
def test_capability_grant_limits_are_bounded(
    field: str,
    value: int,
) -> None:
    now = datetime.now(UTC)
    kwargs = {
        "capabilities": frozenset({Capability.WORKSPACE_READ}),
        "issued_at": now,
        "expires_at": now + timedelta(hours=1),
        field: value,
    }

    with pytest.raises(ValueError):
        CapabilityGrant(**kwargs)  # type: ignore[arg-type]


def test_run_plan_rejects_duplicate_step_ids() -> None:
    steps = (
        RunStep(
            step_id="read",
            capability=Capability.WORKSPACE_READ,
            action="read file",
        ),
        RunStep(
            step_id="read",
            capability=Capability.WORKSPACE_READ,
            action="read another file",
        ),
    )

    with pytest.raises(ValueError, match="únicos"):
        RunPlan(objective="Inspect project", steps=steps)


def test_autonomy_run_requires_all_plan_capabilities(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    plan = RunPlan(
        objective="Prepare a controlled change",
        steps=(
            RunStep(
                step_id="read",
                capability=Capability.WORKSPACE_READ,
                action="inspect",
            ),
            RunStep(
                step_id="write",
                capability=Capability.WORKSPACE_WRITE,
                action="edit",
            ),
        ),
    )

    with pytest.raises(PermissionError, match="workspace.write"):
        AutonomyRun(
            actor="owner",
            workspace=WorkspaceScope.from_root(root),
            grant=_grant(Capability.WORKSPACE_READ),
            plan=plan,
        )


def test_autonomy_run_respects_grant_step_limit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    plan = RunPlan(
        objective="Inspect two files",
        steps=(
            RunStep(
                step_id="read.one",
                capability=Capability.WORKSPACE_READ,
                action="inspect",
            ),
            RunStep(
                step_id="read.two",
                capability=Capability.WORKSPACE_READ,
                action="inspect",
            ),
        ),
    )

    with pytest.raises(PermissionError, match="max_steps"):
        AutonomyRun(
            actor="owner",
            workspace=WorkspaceScope.from_root(root),
            grant=_grant(Capability.WORKSPACE_READ, max_steps=1),
            plan=plan,
        )


def test_valid_autonomy_run_is_declarative_and_bounded(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    plan = RunPlan(
        objective="Inspect project and prepare one controlled edit",
        steps=(
            RunStep(
                step_id="inspect",
                capability=Capability.WORKSPACE_READ,
                action="inspect project",
            ),
            RunStep(
                step_id="edit",
                capability=Capability.WORKSPACE_WRITE,
                action="prepare edit",
                requires_human_gate=True,
            ),
        ),
    )

    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(root),
        grant=_grant(
            Capability.WORKSPACE_READ,
            Capability.WORKSPACE_WRITE,
        ),
        plan=plan,
    )

    assert run.plan.required_capabilities == frozenset(
        {
            Capability.WORKSPACE_READ,
            Capability.WORKSPACE_WRITE,
        }
    )
    assert run.grant.max_steps == 40


def test_human_gate_starts_pending() -> None:
    gate = HumanGate(
        run_id="run-1",
        reason="External side effect requires owner approval.",
    )

    assert gate.status is HumanGateStatus.PENDING
