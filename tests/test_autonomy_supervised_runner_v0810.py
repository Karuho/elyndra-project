from __future__ import annotations

import inspect
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from elyndra.autonomy import (
    AutonomyExecutionBinding,
    AutonomyRepository,
    AutonomyRun,
    AutonomyRunStatus,
    BubblewrapExecutor,
    CancellationToken,
    Capability,
    CapabilityGrant,
    CommandSpec,
    ExecutionOutcome,
    RunPlan,
    RunStep,
    SupervisedAutonomyRunner,
    SupervisedTickOutcome,
    WorkspaceScope,
)
from elyndra.db import Database


def _sandbox_python() -> str:
    candidate = Path("/usr/bin/python3")
    if not candidate.exists():
        pytest.fail("El Python del sandbox no está disponible en /usr/bin/python3.")
    return str(candidate.resolve(strict=True))


def _require_runtime() -> None:
    bwrap = Path("/usr/bin/bwrap")
    if not bwrap.exists():
        pytest.fail("Bubblewrap no está instalado.")

    argv = [
        str(bwrap),
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--cap-drop",
        "ALL",
        "--ro-bind",
        "/usr",
        "/usr",
    ]

    for system_path in (
        "/lib",
        "/lib64",
    ):
        if Path(system_path).exists():
            argv.extend(
                (
                    "--ro-bind",
                    system_path,
                    system_path,
                )
            )

    argv.extend(
        (
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--",
            _sandbox_python(),
            "--version",
        )
    )

    probe = subprocess.run(  # noqa: S603
        tuple(argv),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr.decode(
        errors="replace"
    )


def _process_step(step_id: str, code: str, *, timeout: int = 3) -> RunStep:
    executable = _sandbox_python()
    return RunStep(
        step_id=step_id,
        capability=Capability.PROCESS_EXEC,
        action=f"execute {step_id}",
        target=".",
        command=CommandSpec(
            executable=executable,
            argv=(executable, "-c", code),
            cwd=".",
            timeout_seconds=timeout,
        ),
    )


def _state(
    tmp_path: Path,
    steps: tuple[RunStep, ...],
) -> tuple[Database, AutonomyRepository, AutonomyRun]:
    root = tmp_path / "project"
    root.mkdir()
    executable = _sandbox_python()
    capabilities = frozenset(step.capability for step in steps)
    now = datetime.now(UTC)
    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(root),
        grant=CapabilityGrant(
            capabilities=capabilities,
            issued_at=now,
            expires_at=now + timedelta(hours=1),
            max_steps=4,
            max_commands=4,
            max_retries=0,
            max_runtime_seconds=20,
            allowed_executables=(executable,),
        ),
        plan=RunPlan(objective="Supervised tick", steps=steps),
    )
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    repository.create(run)
    repository.transition(
        run.run_id,
        AutonomyRunStatus.RUNNING,
        actor="owner",
        summary="Run iniciado.",
    )
    return database, repository, run


def _runner(repository: AutonomyRepository) -> SupervisedAutonomyRunner:
    return SupervisedAutonomyRunner(repository, actor="owner")


def test_one_tick_executes_one_process_and_completes(tmp_path: Path) -> None:
    _require_runtime()
    _database, repository, run = _state(
        tmp_path,
        (_process_step("one", "print('ONE')"),),
    )

    result = _runner(repository).tick(run.run_id)

    assert result.outcome is SupervisedTickOutcome.EXECUTED_SUCCEEDED
    assert len(repository.execution_results(run.run_id, actor="owner")) == 1
    assert repository.get(run.run_id)["status"] == "completed"  # type: ignore[index]


def test_two_steps_require_two_ticks_and_never_advance_twice(tmp_path: Path) -> None:
    _require_runtime()
    _database, repository, run = _state(
        tmp_path,
        (
            _process_step("one", "print('ONE')"),
            _process_step("two", "print('TWO')"),
        ),
    )
    runner = _runner(repository)

    first = runner.tick(run.run_id)
    after_first = repository.execution_results(run.run_id, actor="owner")
    second = runner.tick(run.run_id)

    assert first.step_id == "one"
    assert [item["step_id"] for item in after_first] == ["one"]
    assert repository.get(run.run_id)["status"] == "completed"  # type: ignore[index]
    assert second.step_id == "two"


def test_completed_run_later_tick_executes_nothing(tmp_path: Path) -> None:
    _require_runtime()
    _database, repository, run = _state(
        tmp_path,
        (_process_step("one", "print('ONE')"),),
    )
    runner = _runner(repository)
    runner.tick(run.run_id)

    later = runner.tick(run.run_id)

    assert later.outcome is SupervisedTickOutcome.NOT_RUNNING
    assert len(repository.execution_results(run.run_id, actor="owner")) == 1


@pytest.mark.parametrize(
    ("code", "expected"),
    (("raise SystemExit(7)", SupervisedTickOutcome.EXECUTED_FAILED),),
)
def test_failed_result_blocks_following_tick(
    tmp_path: Path,
    code: str,
    expected: SupervisedTickOutcome,
) -> None:
    _require_runtime()
    _database, repository, run = _state(
        tmp_path,
        (_process_step("one", code),),
    )
    runner = _runner(repository)

    assert runner.tick(run.run_id).outcome is expected
    assert runner.tick(run.run_id).outcome is SupervisedTickOutcome.BLOCKED_PREVIOUS_RESULT
    assert len(repository.execution_results(run.run_id, actor="owner")) == 1


def test_cancelled_result_blocks_following_tick(tmp_path: Path) -> None:
    _require_runtime()
    _database, repository, run = _state(
        tmp_path,
        (_process_step("one", "import time; time.sleep(5)", timeout=8),),
    )
    token = CancellationToken()
    timer = threading.Timer(0.25, token.cancel)
    timer.start()
    try:
        first = _runner(repository).tick(run.run_id, cancellation=token)
    finally:
        timer.cancel()

    assert first.outcome is SupervisedTickOutcome.EXECUTED_CANCELLED
    assert _runner(repository).tick(run.run_id).outcome is (
        SupervisedTickOutcome.BLOCKED_PREVIOUS_RESULT
    )
    assert len(repository.execution_results(run.run_id, actor="owner")) == 1


def test_reservation_without_launch_blocks_fail_closed(tmp_path: Path) -> None:
    _database, repository, run = _state(
        tmp_path,
        (_process_step("one", "print('NO')"),),
    )
    AutonomyExecutionBinding(repository).bind(run.run_id, actor="owner").prepare("one")

    gaps = repository.execution_attempt_gaps(run.run_id, actor="owner")
    result = _runner(repository).tick(run.run_id)

    assert gaps[0]["state"] == "reservation_unlaunched"
    assert "launched_at" not in gaps[0]
    assert (
        repository.finalize_execution_run_if_ready(
            run.run_id,
            actor="owner",
        )
        is False
    )
    assert result.outcome is SupervisedTickOutcome.BLOCKED_INCOMPLETE_ATTEMPT


def test_launch_without_result_blocks_fail_closed(tmp_path: Path) -> None:
    _database, repository, run = _state(
        tmp_path,
        (_process_step("one", "print('NO')"),),
    )
    prepared = AutonomyExecutionBinding(repository).bind(
        run.run_id, actor="owner"
    ).prepare("one")
    repository._claim_execution_launch(
        prepared.request,
        actor="owner",
        runtime_seconds=prepared.reserved_runtime_seconds,
        retry=False,
        workspace_lease_receipt=prepared.workspace_session.receipt,
    )

    gaps = repository.execution_attempt_gaps(run.run_id, actor="owner")
    result = _runner(repository).tick(run.run_id)

    assert gaps[0]["state"] == "observation_unresolved"
    assert gaps[0]["launched_at"]
    assert (
        repository.finalize_execution_run_if_ready(
            run.run_id,
            actor="owner",
        )
        is False
    )
    assert result.outcome is SupervisedTickOutcome.BLOCKED_INCOMPLETE_ATTEMPT


def test_duplicate_initial_reservation_denied_without_budget(tmp_path: Path) -> None:
    _database, repository, run = _state(
        tmp_path,
        (_process_step("one", "print('NO')"),),
    )
    contract = AutonomyExecutionBinding(repository).bind(run.run_id, actor="owner")
    contract.prepare("one", retry=False)

    with pytest.raises(PermissionError, match="reserva inicial"):
        contract.prepare("one", retry=False)

    budget = repository.execution_budget(run.run_id, actor="owner").snapshot()
    assert budget.commands_reserved == 1
    assert budget.runtime_seconds_reserved == 3


def test_concurrent_initial_reservation_allows_only_one(tmp_path: Path) -> None:
    _database, repository, run = _state(
        tmp_path,
        (_process_step("one", "print('NO')"),),
    )
    barrier = threading.Barrier(2)

    def prepare() -> str:
        contract = AutonomyExecutionBinding(repository).bind(
            run.run_id,
            actor="owner",
        )
        barrier.wait()
        try:
            contract.prepare("one", retry=False)
        except PermissionError:
            return "denied"
        return "reserved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: prepare(), range(2)))

    assert sorted(outcomes) == ["denied", "reserved"]
    budget = repository.execution_budget(run.run_id, actor="owner").snapshot()
    assert budget.commands_reserved == 1
    assert budget.runtime_seconds_reserved == 3


def test_preparation_denial_is_bounded_without_reservation(tmp_path: Path) -> None:
    guarded = RunStep(
        step_id="guarded",
        capability=Capability.PROCESS_EXEC,
        action="execute guarded",
        target=".",
        requires_human_gate=True,
        command=_process_step("source", "print('NO')").command,
    )
    _database, repository, run = _state(tmp_path, (guarded,))

    result = _runner(repository).tick(run.run_id)

    assert result.outcome is SupervisedTickOutcome.BLOCKED_PREPARATION
    assert repository.execution_attempt_gaps(run.run_id, actor="owner") == []
    assert repository.execution_budget(
        run.run_id, actor="owner"
    ).snapshot().commands_reserved == 0


def test_unsupported_first_step_is_not_skipped(tmp_path: Path) -> None:
    unsupported = RunStep(
        step_id="read",
        capability=Capability.WORKSPACE_READ,
        action="inspect",
        target=".",
    )
    _database, repository, run = _state(
        tmp_path,
        (unsupported, _process_step("process", "print('NO')")),
    )

    result = _runner(repository).tick(run.run_id)

    assert result.outcome is SupervisedTickOutcome.UNSUPPORTED_CAPABILITY
    assert result.step_id == "read"
    assert repository.execution_results(run.run_id, actor="owner") == []
    assert repository.execution_attempt_gaps(run.run_id, actor="owner") == []


def test_actor_mismatch_is_denied(tmp_path: Path) -> None:
    _database, repository, run = _state(
        tmp_path,
        (_process_step("one", "print('NO')"),),
    )

    with pytest.raises(PermissionError, match="propietario"):
        SupervisedAutonomyRunner(repository, actor="intruder").tick(run.run_id)

    assert repository.execution_attempt_gaps(run.run_id, actor="owner") == []


def test_runner_has_no_executor_or_launcher_injection(tmp_path: Path) -> None:
    _database, repository, _run = _state(
        tmp_path,
        (_process_step("one", "print('NO')"),),
    )
    parameters = inspect.signature(SupervisedAutonomyRunner).parameters
    assert set(parameters) == {"repository", "actor"}

    with pytest.raises(TypeError):
        SupervisedAutonomyRunner(  # type: ignore[call-arg]
            repository,
            actor="owner",
            executor=object(),
        )


def test_already_succeeded_plan_recovers_completion_without_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_runtime()
    _database, repository, run = _state(
        tmp_path,
        (_process_step("one", "print('ONE')"),),
    )
    contract = AutonomyExecutionBinding(repository).bind(run.run_id, actor="owner")
    prepared = contract.prepare("one")
    execution = BubblewrapExecutor(repository, actor="owner").execute(
        prepared,
        cancellation=CancellationToken(),
    )
    assert execution.outcome is ExecutionOutcome.SUCCEEDED
    assert repository.get(run.run_id)["status"] == "running"  # type: ignore[index]

    def forbidden_bind(*_args: object, **_kwargs: object) -> object:
        raise AssertionError(
            "Crash recovery must not rebuild execution authority."
        )

    monkeypatch.setattr(
        AutonomyExecutionBinding,
        "bind",
        forbidden_bind,
    )

    result = _runner(repository).tick(run.run_id)

    assert result.outcome is SupervisedTickOutcome.RUN_COMPLETED
    assert len(repository.execution_results(run.run_id, actor="owner")) == 1
    assert repository.get(run.run_id)["status"] == "completed"  # type: ignore[index]


def test_durable_completion_is_idempotent_under_concurrency(
    tmp_path: Path,
) -> None:
    _require_runtime()
    _database, repository, run = _state(
        tmp_path,
        (_process_step("one", "print('ONE')"),),
    )

    prepared = AutonomyExecutionBinding(repository).bind(
        run.run_id,
        actor="owner",
    ).prepare("one")

    execution = BubblewrapExecutor(
        repository,
        actor="owner",
    ).execute(
        prepared,
        cancellation=CancellationToken(),
    )
    assert execution.outcome is ExecutionOutcome.SUCCEEDED

    barrier = threading.Barrier(2)

    def finalize(_index: int) -> bool:
        barrier.wait()
        return repository.finalize_execution_run_if_ready(
            run.run_id,
            actor="owner",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(finalize, range(2)))

    assert outcomes == [True, True]

    item = repository.get(run.run_id)
    assert item is not None
    assert item["status"] == "completed"

    completion_events = [
        event
        for event in item["events"]
        if event.get("to_status") == "completed"
    ]
    assert len(completion_events) == 1
