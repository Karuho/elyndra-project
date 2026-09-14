from __future__ import annotations

import sqlite3
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from elyndra.autonomy import (
    AutonomyRepository,
    AutonomyRun,
    Capability,
    CapabilityGrant,
    CommandSpec,
    ExecutionRequest,
    RunPlan,
    RunStep,
    WorkspaceScope,
)
from elyndra.cognitive_loop import LocalCognitiveActionLoop
from elyndra.db import Database
from elyndra.engines import LanguageReply


class _Engine:
    name = "lineage-budget-test"
    supports_vision = False

    def reply(self, prompt: str, **kwargs: object) -> LanguageReply:
        return LanguageReply('{"decision":"propose_replan"}', self.name, True)

    def release(self) -> None:
        return None


class _Memory:
    def recall(self, *args: object, **kwargs: object) -> object:
        return type("Recall", (), {"items": []})()


def _process_plan(objective: str, executable: str) -> RunPlan:
    return RunPlan(
        objective=objective,
        steps=(
            RunStep(
                step_id="run",
                capability=Capability.PROCESS_EXEC,
                action="run exact command",
                target=".",
                command=CommandSpec(
                    executable=executable,
                    argv=(executable, "-c", "print('ok')"),
                    cwd=".",
                    timeout_seconds=1,
                ),
            ),
        ),
    )


def _self_modify_plan(objective: str) -> RunPlan:
    return RunPlan(
        objective=objective,
        steps=(
            RunStep(
                step_id="modify",
                capability=Capability.SELF_MODIFY,
                action="apply reviewed mutation",
                target="src/generated.py",
                requires_human_gate=True,
            ),
        ),
    )


def _state(
    tmp_path: Path,
    *,
    max_commands: int = 4,
    max_retries: int = 2,
    max_runtime_seconds: int = 20,
    capabilities: frozenset[Capability] = frozenset(
        {Capability.PROCESS_EXEC, Capability.SELF_MODIFY}
    ),
) -> tuple[
    Database,
    AutonomyRepository,
    LocalCognitiveActionLoop,
    AutonomyRun,
    RunPlan,
]:
    workspace = tmp_path / "project"
    workspace.mkdir()
    executable = str(Path(sys.executable).resolve(strict=True))
    objective = "bounded lineage objective"
    plan = _process_plan(objective, executable)
    now = datetime.now(UTC)
    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(workspace),
        grant=CapabilityGrant(
            capabilities=capabilities,
            issued_at=now,
            expires_at=now + timedelta(minutes=30),
            max_steps=4,
            max_commands=max_commands,
            max_retries=max_retries,
            max_runtime_seconds=max_runtime_seconds,
            allowed_executables=(executable,),
        ),
        plan=plan,
    )
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    repository.create(run)
    loop = LocalCognitiveActionLoop(
        database, language_engine=_Engine(), memory=_Memory()  # type: ignore[arg-type]
    )
    return database, repository, loop, run, plan


def _wait_for_successor(
    loop: LocalCognitiveActionLoop, repository: AutonomyRepository, run_id: str
) -> str:
    repository.transition(run_id, "running", actor="owner", summary="start")
    cycle = loop.create_cycle(run_id, actor="owner")
    loop.advance(str(cycle["public_id"]), actor="owner")
    with loop.database.connect() as connection:
        return str(
            connection.execute(
                "SELECT public_id FROM assistant_cognitive_owner_waits "
                "WHERE cycle_id=(SELECT id FROM assistant_cognitive_cycles "
                "WHERE public_id=?)",
                (cycle["public_id"],),
            ).fetchone()[0]
        )


def _propose(
    loop: LocalCognitiveActionLoop,
    repository: AutonomyRepository,
    run_id: str,
    plan: RunPlan,
    *,
    key: str,
    max_commands: int,
    max_retries: int,
    max_runtime_seconds: int,
    capabilities: list[str] | None = None,
    wait_id: str | None = None,
) -> dict[str, object]:
    run = repository.get(run_id)
    assert run is not None
    selected_wait_id = wait_id or _wait_for_successor(loop, repository, run_id)
    selected_capabilities = capabilities or ["process.exec"]
    executables = (
        run["grant"]["allowed_executables"]
        if "process.exec" in selected_capabilities
        else []
    )
    return loop.propose_successor(
        selected_wait_id,
        actor="owner",
        request_key=key,
        objective=str(run["objective"]),
        workspace_root=str(run["workspace_root"]),
        plan=plan,
        grant_spec={
            "capabilities": selected_capabilities,
            "allowed_executables": executables,
            "max_steps": len(plan.steps),
            "max_commands": max_commands,
            "max_retries": max_retries,
            "max_runtime_seconds": max_runtime_seconds,
            "duration_seconds": min(
                600,
                int(
                    (
                        datetime.fromisoformat(run["grant"]["expires_at"])
                        - datetime.fromisoformat(run["grant"]["issued_at"])
                    ).total_seconds()
                ),
            ),
        },
    )


def _accept(
    loop: LocalCognitiveActionLoop,
    repository: AutonomyRepository,
    run_id: str,
    plan: RunPlan,
    *,
    key: str,
    max_commands: int = 4,
    max_retries: int = 2,
    max_runtime_seconds: int = 20,
    capabilities: list[str] | None = None,
    wait_id: str | None = None,
) -> tuple[dict[str, object], str]:
    proposed = _propose(
        loop,
        repository,
        run_id,
        plan,
        key=key,
        max_commands=max_commands,
        max_retries=max_retries,
        max_runtime_seconds=max_runtime_seconds,
        capabilities=capabilities,
        wait_id=wait_id,
    )
    accepted = loop.accept_successor(str(proposed["public_id"]), actor="owner")
    return accepted, str(accepted["successor_run_id"])


def _insert_reservation(
    database: Database,
    run_id: str,
    label: str,
    *,
    runtime_seconds: int = 1,
    retry: bool = False,
) -> None:
    with database.connect() as connection:
        run_db_id = int(
            connection.execute(
                "SELECT id FROM assistant_autonomy_runs WHERE public_id=?", (run_id,)
            ).fetchone()[0]
        )
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 "
                "FROM assistant_autonomy_execution_reservations WHERE run_id=?",
                (run_db_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """INSERT INTO assistant_autonomy_execution_reservations(
                   request_id, request_sha256, run_id, sequence, step_id, capability,
                   command_sha256, runtime_seconds, is_retry, created_at)
               VALUES (?, ?, ?, ?, 'run', 'process.exec', ?, ?, ?, ?)""",
            (
                f"request-{label}",
                "a" * 64,
                run_db_id,
                sequence,
                "b" * 64,
                runtime_seconds,
                int(retry),
                datetime.now(UTC).isoformat(),
            ),
        )
        connection.execute(
            """INSERT INTO assistant_autonomy_execution_launches(
                   request_id, request_sha256, run_id, command_sha256, created_at,
                   observation_receipt_sha256)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                f"request-{label}",
                "a" * 64,
                run_db_id,
                "b" * 64,
                datetime.now(UTC).isoformat(),
                "c" * 64,
            ),
        )
        connection.execute(
            """INSERT INTO assistant_autonomy_execution_results(
                   request_id, request_sha256, run_id, sequence, step_id,
                   command_sha256, runtime_seconds, is_retry, outcome, exit_code,
                   duration_ms, summary, error_code, stdout, stderr, stdout_sha256,
                   stderr_sha256, timed_out, stdout_truncated, stderr_truncated,
                   created_at)
               VALUES (?, ?, ?, ?, 'run', ?, ?, ?, 'failed', 1, 1,
                       'synthetic completed attempt', 'process_exit_nonzero', '', '',
                       ?, ?, 0, 0, 0, ?)""",
            (
                f"request-{label}",
                "a" * 64,
                run_db_id,
                sequence,
                "b" * 64,
                runtime_seconds,
                int(retry),
                "d" * 64,
                "d" * 64,
                datetime.now(UTC).isoformat(),
            ),
        )


def _strip_lineage_to_schema61(database: Database) -> None:
    with database.connect() as connection:
        connection.executescript(
            """
            DROP TRIGGER IF EXISTS trg_autonomy_reservation_lineage_budget;
            DROP TRIGGER IF EXISTS trg_autonomy_lineage_runs_integrity;
            DROP TRIGGER IF EXISTS trg_autonomy_lineage_runs_no_update;
            DROP TRIGGER IF EXISTS trg_autonomy_lineage_runs_no_delete;
            DROP TRIGGER IF EXISTS trg_autonomy_lineages_no_update;
            DROP TRIGGER IF EXISTS trg_autonomy_lineages_no_delete;
            DROP TABLE IF EXISTS assistant_autonomy_lineage_runs;
            DROP TABLE IF EXISTS assistant_autonomy_lineages;
            UPDATE schema_meta SET value='61' WHERE key='schema_version';
            """
        )


def test_root_lineage_has_exact_ceiling_and_generation_zero(tmp_path: Path) -> None:
    database, repository, _loop, run, _plan = _state(tmp_path)
    snapshot = repository.lineage_budget_snapshot(run.run_id, actor="owner")
    assert snapshot["generation"] == 0
    assert snapshot["max_successors"] == 3
    assert snapshot["limits"] == {
        "commands": 4,
        "retries": 2,
        "runtime_seconds": 20,
    }
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_lineages"
        ).fetchone()[0] == 1
        assert tuple(
            connection.execute(
                """SELECT generation, predecessor_run_id, source_handoff_id,
                          commands_reserved_before, retries_reserved_before,
                          runtime_seconds_reserved_before
                   FROM assistant_autonomy_lineage_runs"""
            ).fetchone()
        ) == (0, None, None, 0, 0, 0)


def test_successors_share_lineage_and_increment_generation(tmp_path: Path) -> None:
    _database, repository, loop, root, plan = _state(tmp_path)
    root_snapshot = repository.lineage_budget_snapshot(root.run_id, actor="owner")
    accepted_b, run_b = _accept(loop, repository, root.run_id, plan, key="a-b")
    accepted_c, run_c = _accept(loop, repository, run_b, plan, key="b-c")
    snapshot_b = repository.lineage_budget_snapshot(run_b, actor="owner")
    snapshot_c = repository.lineage_budget_snapshot(run_c, actor="owner")
    assert accepted_b["status"] == accepted_c["status"] == "accepted"
    assert snapshot_b["lineage_id"] == root_snapshot["lineage_id"]
    assert snapshot_c["lineage_id"] == root_snapshot["lineage_id"]
    assert (snapshot_b["generation"], snapshot_c["generation"]) == (1, 2)


def test_generation_three_is_last_allowed_successor(tmp_path: Path) -> None:
    _database, repository, loop, root, plan = _state(tmp_path)
    current = root.run_id
    for generation in range(1, 4):
        _accepted, current = _accept(
            loop, repository, current, plan, key=f"generation-{generation}"
        )
    with pytest.raises(PermissionError, match="max_successors"):
        _propose(
            loop,
            repository,
            current,
            plan,
            key="generation-four",
            max_commands=4,
            max_retries=2,
            max_runtime_seconds=20,
        )


@pytest.mark.parametrize(
    ("reservations", "candidate", "match"),
    (
        (((1, False), (1, False)), (3, 2, 18), "lineage"),
        (((6, False),), (3, 2, 15), "lineage"),
        (((1, True),), (3, 2, 19), "lineage"),
    ),
)
def test_proposal_rejects_authority_above_remaining_lineage_budget(
    tmp_path: Path,
    reservations: tuple[tuple[int, bool], ...],
    candidate: tuple[int, int, int],
    match: str,
) -> None:
    database, repository, loop, root, plan = _state(tmp_path)
    wait_id = _wait_for_successor(loop, repository, root.run_id)
    for index, (runtime, retry) in enumerate(reservations):
        _insert_reservation(
            database, root.run_id, f"used-{index}", runtime_seconds=runtime, retry=retry
        )
    with pytest.raises(PermissionError, match=match):
        _propose(
            loop,
            repository,
            root.run_id,
            plan,
            key="too-large",
            max_commands=candidate[0],
            max_retries=candidate[1],
            max_runtime_seconds=candidate[2],
            wait_id=wait_id,
        )


def test_acceptance_rechecks_budget_consumed_after_proposal(tmp_path: Path) -> None:
    database, repository, loop, root, plan = _state(tmp_path)
    proposed = _propose(
        loop,
        repository,
        root.run_id,
        plan,
        key="stale-budget",
        max_commands=4,
        max_retries=2,
        max_runtime_seconds=20,
    )
    _insert_reservation(database, root.run_id, "after-proposal")
    with pytest.raises(PermissionError, match="lineage"):
        loop.accept_successor(str(proposed["public_id"]), actor="owner")


def test_accepted_replay_uses_historical_snapshot_not_current_remaining(
    tmp_path: Path,
) -> None:
    database, repository, loop, root, plan = _state(tmp_path, max_commands=2)
    accepted, successor_id = _accept(
        loop,
        repository,
        root.run_id,
        plan,
        key="historical-replay",
        max_commands=2,
    )
    _insert_reservation(database, successor_id, "successor-one")
    _insert_reservation(database, successor_id, "successor-two")
    replay = loop.accept_successor(str(accepted["public_id"]), actor="owner")
    assert replay["successor_run_id"] == successor_id
    with database.connect() as connection, pytest.raises(
        sqlite3.IntegrityError, match="immutable"
    ):
        connection.execute(
            "UPDATE assistant_autonomy_lineage_runs "
            "SET commands_reserved_before=1 WHERE run_id="
            "(SELECT id FROM assistant_autonomy_runs WHERE public_id=?)",
            (successor_id,),
        )


def test_database_trigger_enforces_aggregate_successor_budget(tmp_path: Path) -> None:
    database, repository, loop, root, plan = _state(
        tmp_path, max_commands=2, max_retries=1, max_runtime_seconds=3
    )
    wait_id = _wait_for_successor(loop, repository, root.run_id)
    _insert_reservation(database, root.run_id, "root", runtime_seconds=1)
    _accepted, successor_id = _accept(
        loop,
        repository,
        root.run_id,
        plan,
        key="aggregate",
        max_commands=1,
        max_retries=1,
        max_runtime_seconds=2,
        wait_id=wait_id,
    )
    _insert_reservation(
        database, successor_id, "successor", runtime_seconds=2, retry=True
    )
    for label, runtime, retry in (
        ("commands", 0, False),
        ("retries", 0, True),
        ("runtime", 1, False),
    ):
        with pytest.raises(sqlite3.IntegrityError, match="lineage_budget"):
            _insert_reservation(
                database,
                successor_id,
                f"overflow-{label}",
                runtime_seconds=runtime,
                retry=retry,
            )


def _workspace_read_state(
    tmp_path: Path,
) -> tuple[AutonomyRepository, AutonomyRun, tuple[ExecutionRequest, ExecutionRequest]]:
    workspace = tmp_path / "read-project"
    workspace.mkdir()
    now = datetime.now(UTC)
    steps = tuple(
        RunStep(
            step_id=f"read-{index}",
            capability=Capability.WORKSPACE_READ,
            action="read bounded file",
            target=f"file-{index}.txt",
        )
        for index in range(2)
    )
    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(workspace),
        grant=CapabilityGrant(
            capabilities=frozenset({Capability.WORKSPACE_READ}),
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
            max_steps=2,
            max_commands=1,
            max_retries=0,
            max_runtime_seconds=1,
        ),
        plan=RunPlan(objective="reservation replay", steps=steps),
    )
    database = Database(tmp_path / "read-vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    repository.create(run)
    repository.transition(run.run_id, "running", actor="owner", summary="start")
    requests = tuple(
        ExecutionRequest(
            run_id=run.run_id,
            step_id=step.step_id,
            capability=step.capability,
            action=step.action,
            target=step.target,
            requires_human_gate=step.requires_human_gate,
            request_id=uuid.uuid4().hex,
        )
        for step in steps
    )
    return repository, run, requests  # type: ignore[return-value]


def test_exact_reservation_replay_after_exhaustion_is_idempotent(tmp_path: Path) -> None:
    repository, _run, requests = _workspace_read_state(tmp_path)
    first = repository.reserve_execution(requests[0], actor="owner")
    replay = repository.reserve_execution(requests[0], actor="owner")
    assert replay == first
    assert replay.commands_reserved == 1


def test_concurrent_final_slot_cannot_overspend(tmp_path: Path) -> None:
    repository, run, requests = _workspace_read_state(tmp_path)

    def reserve(request: ExecutionRequest) -> str:
        try:
            repository.reserve_execution(request, actor="owner")
            return "reserved"
        except PermissionError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, requests))
    assert sorted(outcomes) == ["denied", "reserved"]
    assert repository.lineage_budget_snapshot(run.run_id, actor="owner")["reserved"][
        "commands"
    ] == 1


def test_self_modify_only_survives_exhaustion_but_process_cannot_return(
    tmp_path: Path,
) -> None:
    database, repository, loop, root, process_plan = _state(tmp_path, max_commands=1)
    wait_id = _wait_for_successor(loop, repository, root.run_id)
    _insert_reservation(database, root.run_id, "all", runtime_seconds=20)
    self_plan = _self_modify_plan(process_plan.objective)
    _accepted, self_run = _accept(
        loop,
        repository,
        root.run_id,
        self_plan,
        key="self-only",
        max_commands=1,
        max_retries=2,
        max_runtime_seconds=20,
        capabilities=["self.modify"],
        wait_id=wait_id,
    )
    with pytest.raises(PermissionError):
        _propose(
            loop,
            repository,
            self_run,
            process_plan,
            key="process-regain",
            max_commands=1,
            max_retries=2,
            max_runtime_seconds=20,
        )


def test_independent_owner_roots_have_independent_lineages(tmp_path: Path) -> None:
    _database, repository, _loop, root, _plan = _state(tmp_path)
    workspace = tmp_path / "second-project"
    workspace.mkdir()
    executable = str(Path(sys.executable).resolve(strict=True))
    now = datetime.now(UTC)
    second = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(workspace),
        grant=CapabilityGrant(
            capabilities=frozenset({Capability.PROCESS_EXEC}),
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
            max_commands=1,
            max_retries=0,
            max_runtime_seconds=1,
            allowed_executables=(executable,),
        ),
        plan=_process_plan("independent objective", executable),
    )
    repository.create(second)
    assert repository.lineage_budget_snapshot(root.run_id, actor="owner")[
        "lineage_id"
    ] != repository.lineage_budget_snapshot(second.run_id, actor="owner")["lineage_id"]


def test_schema62_is_vault_only_and_idempotent(tmp_path: Path) -> None:
    root = Database(tmp_path / "root.sqlite3", role="root")
    vault = Database(tmp_path / "empty-vault.sqlite3", role="vault")
    root.migrate()
    vault.migrate()
    root.migrate()
    vault.migrate()
    with root.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "63"
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='assistant_autonomy_lineages'"
        ).fetchone() is None
    with vault.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "63"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_schema61_backfills_standalone_and_accepted_chain_replay(
    tmp_path: Path,
) -> None:
    database, repository, loop, root, plan = _state(tmp_path)
    accepted_b, run_b = _accept(loop, repository, root.run_id, plan, key="history-a-b")
    _accepted_c, run_c = _accept(loop, repository, run_b, plan, key="history-b-c")
    _strip_lineage_to_schema61(database)
    database.migrate()
    assert repository.lineage_budget_snapshot(root.run_id, actor="owner")[
        "lineage_id"
    ] == repository.lineage_budget_snapshot(run_c, actor="owner")["lineage_id"]
    assert repository.lineage_budget_snapshot(run_c, actor="owner")["generation"] == 2
    replay = loop.accept_successor(str(accepted_b["public_id"]), actor="owner")
    assert replay["successor_run_id"] == run_b
    database.migrate()
    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_schema61_grandfathers_historical_overuse_without_new_capacity(
    tmp_path: Path,
) -> None:
    database, repository, _loop, root, _plan = _state(
        tmp_path, max_commands=1, max_retries=0, max_runtime_seconds=1
    )
    with database.connect() as connection:
        connection.execute("DROP TRIGGER trg_autonomy_reservation_lineage_budget")
    _insert_reservation(database, root.run_id, "historical-1", retry=True)
    _insert_reservation(database, root.run_id, "historical-2")
    _strip_lineage_to_schema61(database)
    database.migrate()
    snapshot = repository.lineage_budget_snapshot(root.run_id, actor="owner")
    assert snapshot["limits"] == {
        "commands": 2,
        "retries": 1,
        "runtime_seconds": 2,
    }
    assert snapshot["remaining"] == {
        "commands": 0,
        "retries": 0,
        "runtime_seconds": 0,
    }


def test_proposed_and_rejected_handoffs_do_not_create_edges(tmp_path: Path) -> None:
    database, repository, loop, root, plan = _state(tmp_path)
    proposed = _propose(
        loop,
        repository,
        root.run_id,
        plan,
        key="not-accepted",
        max_commands=4,
        max_retries=2,
        max_runtime_seconds=20,
    )
    loop.reject_successor(str(proposed["public_id"]), actor="owner")
    _strip_lineage_to_schema61(database)
    database.migrate()
    snapshot = repository.lineage_budget_snapshot(root.run_id, actor="owner")
    assert snapshot["generation"] == 0


def test_schema61_inconsistent_accepted_graph_fails_closed(tmp_path: Path) -> None:
    database, repository, loop, root, plan = _state(tmp_path)
    accepted, _successor = _accept(
        loop, repository, root.run_id, plan, key="bad-history"
    )
    with database.connect() as connection:
        connection.execute("DROP TRIGGER trg_cognitive_handoff_resolved_immutable")
        connection.execute(
            "UPDATE assistant_cognitive_successor_handoffs SET successor_run_id="
            "(SELECT id FROM assistant_autonomy_runs WHERE public_id=?) "
            "WHERE public_id=?",
            (root.run_id, accepted["public_id"]),
        )
    _strip_lineage_to_schema61(database)
    with pytest.raises(RuntimeError, match="ambiguo"):
        database.migrate()
