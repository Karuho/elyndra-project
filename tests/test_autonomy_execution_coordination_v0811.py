from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import elyndra.autonomy.bubblewrap_executor as bubblewrap_module
import elyndra.autonomy.execution as execution_module
from elyndra.autonomy import (
    BubblewrapExecutor,
    Capability,
    CapabilityGrant,
    CommandSpec,
    ExecutionBudgetSnapshot,
    ExecutionContract,
    ExecutionDenied,
    RunPlan,
    RunStep,
    WorkspaceLeaseCoordinator,
    WorkspaceLeaseMode,
    WorkspaceScope,
)
from elyndra.autonomy.execution import ExecutionRequest
from elyndra.autonomy.repository import AutonomyRepository
from elyndra.autonomy.workspace_lease import WorkspaceLeaseTimeout
from elyndra.db import Database


class _Backend:
    def __init__(self) -> None:
        self.calls = 0

    def reserve(
        self,
        request: ExecutionRequest,
        *,
        runtime_seconds: int = 0,
        retry: bool = False,
    ) -> ExecutionBudgetSnapshot:
        self.calls += 1
        return ExecutionBudgetSnapshot(1, 0, 5, 1, 0, runtime_seconds)


class _NoWaitCoordinator(WorkspaceLeaseCoordinator):
    def execution_session(self, root, *, cancellation=None, timeout_seconds=10.0):
        return super().execution_session(root, cancellation=cancellation, timeout_seconds=0)


def _contract(
    tmp_path: Path,
    coordinator: WorkspaceLeaseCoordinator,
    backend: _Backend,
) -> ExecutionContract:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "work").mkdir(exist_ok=True)
    executable = str(Path("/usr/bin/python3").resolve(strict=True))
    now = datetime.now(UTC)
    command = CommandSpec(
        executable=executable,
        argv=(executable, "-c", "print('ok')"),
        cwd="work",
        timeout_seconds=5,
    )
    return ExecutionContract(
        run_id="run-lease",
        plan=RunPlan(
            objective="coordinate",
            steps=(
                RunStep(
                    step_id="run",
                    capability=Capability.PROCESS_EXEC,
                    action="run",
                    target="work",
                    command=command,
                ),
            ),
        ),
        workspace=WorkspaceScope.from_root(workspace),
        grant=CapabilityGrant(
            capabilities=frozenset({Capability.PROCESS_EXEC}),
            issued_at=now,
            expires_at=now + timedelta(minutes=10),
            max_steps=1,
            max_commands=1,
            max_retries=0,
            max_runtime_seconds=5,
            allowed_executables=(executable,),
        ),
        reservation_backend=backend,
        workspace_lease_coordinator=coordinator,
    )


def _coordinator(tmp_path: Path, cls=WorkspaceLeaseCoordinator):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    return cls._for_test(runtime)


def test_prepare_acquires_shared_lease_before_reservation(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    backend = _Backend()
    prepared = _contract(tmp_path, coordinator, backend).prepare("run")
    assert backend.calls == 1
    identity = prepared.workspace_session.identity
    with pytest.raises(WorkspaceLeaseTimeout):
        coordinator.acquire(identity, WorkspaceLeaseMode.EXCLUSIVE, timeout_seconds=0)
    prepared.close_workspace_session()
    coordinator.acquire(identity, WorkspaceLeaseMode.EXCLUSIVE).close()


def test_snapshot_capture_occurs_after_shared_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = _coordinator(tmp_path)
    backend = _Backend()
    events: list[str] = []
    original_session = coordinator.execution_session
    original_capture = execution_module.CommandSnapshot.capture

    def session(*args, **kwargs):
        events.append("lease")
        return original_session(*args, **kwargs)

    def capture(*args, **kwargs):
        events.append("snapshot")
        return original_capture(*args, **kwargs)

    monkeypatch.setattr(coordinator, "execution_session", session)
    monkeypatch.setattr(execution_module.CommandSnapshot, "capture", capture)
    prepared = _contract(tmp_path, coordinator, backend).prepare("run")
    try:
        assert events == ["lease", "snapshot"]
        assert backend.calls == 1
    finally:
        prepared.close_workspace_session()


def test_workspace_change_before_lease_cannot_reserve_old_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = _coordinator(tmp_path)
    backend = _Backend()
    contract = _contract(tmp_path, coordinator, backend)
    original_session = coordinator.execution_session
    captures = 0
    outside = tmp_path / "outside"
    outside.mkdir()

    def session(*args, **kwargs):
        nonlocal captures
        assert captures == 0
        (contract.workspace.root / "work").rmdir()
        (contract.workspace.root / "work").symlink_to(outside, target_is_directory=True)
        return original_session(*args, **kwargs)

    original_capture = execution_module.CommandSnapshot.capture

    def capture(*args, **kwargs):
        nonlocal captures
        captures += 1
        return original_capture(*args, **kwargs)

    monkeypatch.setattr(coordinator, "execution_session", session)
    monkeypatch.setattr(execution_module.CommandSnapshot, "capture", capture)
    with pytest.raises(ExecutionDenied, match="CommandSnapshot"):
        contract.prepare("run")
    assert captures == 0
    assert backend.calls == 0


@pytest.mark.parametrize("failure_point", ["snapshot", "request", "reservation"])
def test_pretransfer_failure_releases_shared_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    coordinator = _coordinator(tmp_path)
    backend = _Backend()
    contract = _contract(tmp_path, coordinator, backend)
    identity = coordinator.identity(contract.workspace.root)

    if failure_point == "snapshot":
        def fail_capture(*_args, **_kwargs):
            raise PermissionError("injected snapshot failure")

        monkeypatch.setattr(execution_module.CommandSnapshot, "capture", fail_capture)
    elif failure_point == "request":
        def fail_request(*_args, **_kwargs):
            raise ValueError("injected request failure")

        monkeypatch.setattr(execution_module, "ExecutionRequest", fail_request)
    else:
        def fail_reserve(*_args, **_kwargs):
            raise RuntimeError("injected reservation failure")

        monkeypatch.setattr(backend, "reserve", fail_reserve)

    with pytest.raises((ExecutionDenied, RuntimeError, ValueError)):
        contract.prepare("run")
    coordinator.acquire(identity, WorkspaceLeaseMode.EXCLUSIVE, timeout_seconds=0).close()
    assert backend.calls == 0


def test_lease_contention_creates_no_reservation(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path, _NoWaitCoordinator)
    backend = _Backend()
    contract = _contract(tmp_path, coordinator, backend)
    identity = coordinator.identity(contract.workspace.root)
    exclusive = coordinator.acquire(identity, WorkspaceLeaseMode.EXCLUSIVE)
    try:
        with pytest.raises(ExecutionDenied, match="coordinación"):
            contract.prepare("run")
    finally:
        exclusive.close()
    assert backend.calls == 0


def test_blockade_denies_before_reservation(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    backend = _Backend()
    contract = _contract(tmp_path, coordinator, backend)
    session = coordinator.execution_session(contract.workspace.root)
    session.close()
    blockade = contract.workspace.root / ".elyndra-mutation-journal" / "blockade.json"
    blockade.write_text(json.dumps({"format_version": "v1"}), encoding="utf-8")
    blockade.chmod(0o600)
    with pytest.raises(ExecutionDenied, match="coordinación"):
        contract.prepare("run")
    assert backend.calls == 0


def test_bubblewrap_mask_follows_writable_workspace_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = _coordinator(tmp_path)
    backend = _Backend()
    contract = _contract(tmp_path, coordinator, backend)
    prepared = contract.prepare("run")
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    monkeypatch.setattr(bubblewrap_module, "_trusted_bwrap_path", lambda: "/usr/bin/bwrap")
    executor = BubblewrapExecutor(AutonomyRepository(database), actor="owner")
    argv = executor._build_bwrap_argv(
        snapshot=prepared.command_snapshot,
        workspace=contract.workspace,
        executable_fd=9,
        journal_mask=prepared.workspace_session.journal_mask_path,
    )
    workspace_text = str(contract.workspace.root)
    bind_index = argv.index("--bind")
    mask_index = argv.index("--ro-bind", bind_index + 1)
    assert argv[bind_index + 1 : bind_index + 3] == (workspace_text, workspace_text)
    assert argv[mask_index + 2] == str(contract.workspace.root / ".elyndra-mutation-journal")
    prepared.close_workspace_session()


def test_process_prepared_execution_cannot_exist_without_live_session(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path)
    prepared = _contract(tmp_path, coordinator, _Backend()).prepare("run")
    prepared.close_workspace_session()
    with pytest.raises(PermissionError, match="vivo"):
        _ = prepared.workspace_session
