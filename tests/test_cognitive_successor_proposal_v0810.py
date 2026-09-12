from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import elyndra.cognitive_loop as cognitive_module
from elyndra.autonomy import (
    AutonomyRepository,
    AutonomyRun,
    Capability,
    CapabilityGrant,
    CommandSpec,
    RunPlan,
    RunStep,
    WorkspaceScope,
)
from elyndra.cognitive_loop import LocalCognitiveActionLoop
from elyndra.db import Database
from elyndra.engines import LanguageReply


class _Engine:
    name = "proposal-test"
    supports_vision = False

    def __init__(self) -> None:
        self.calls = 0

    def reply(self, prompt: str, **kwargs: object) -> LanguageReply:
        self.calls += 1
        return LanguageReply('{"decision":"propose_replan"}', self.name, True)

    def release(self) -> None:
        return None


class _Memory:
    def recall(self, *args: object, **kwargs: object) -> object:
        return type("Recall", (), {"items": []})()


def _fixture(
    tmp_path: Path,
    *,
    duration_seconds: int = 1800,
    max_steps: int = 4,
    max_commands: int = 4,
    extra_executable: bool = False,
) -> tuple[Database, LocalCognitiveActionLoop, AutonomyRun, str]:
    workspace = tmp_path / "project"
    workspace.mkdir()
    executable = str(Path(sys.executable).resolve(strict=True))
    executables = (
        (executable, str(Path("/bin/echo").resolve(strict=True)))
        if extra_executable
        else (executable,)
    )
    now = datetime.now(UTC)
    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(workspace),
        grant=CapabilityGrant(
            capabilities=frozenset({Capability.PROCESS_EXEC}),
            issued_at=now,
            expires_at=now + timedelta(seconds=duration_seconds),
            max_steps=max_steps,
            max_commands=max_commands,
            max_retries=2,
            max_runtime_seconds=20,
            allowed_executables=executables,
        ),
        plan=_plan("Inspect project", executable),
    )
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    repository.create(run)
    repository.transition(run.run_id, "running", actor="owner", summary="start")
    loop = LocalCognitiveActionLoop(
        database, language_engine=_Engine(), memory=_Memory()  # type: ignore[arg-type]
    )
    cycle = loop.create_cycle(run.run_id, actor="owner")
    loop.advance(str(cycle["public_id"]), actor="owner")
    with database.connect() as connection:
        wait_id = str(
            connection.execute(
                "SELECT public_id FROM assistant_cognitive_owner_waits"
            ).fetchone()[0]
        )
    return database, loop, run, wait_id


def _plan(objective: str, executable: str, *, cwd: str = ".", timeout: int = 3) -> RunPlan:
    return RunPlan(
        objective=objective,
        steps=(
            RunStep(
                step_id="run",
                capability=Capability.PROCESS_EXEC,
                action="run tests",
                target=cwd,
                command=CommandSpec(
                    executable=executable,
                    argv=(executable, "-c", "print('ok')"),
                    cwd=cwd,
                    timeout_seconds=timeout,
                ),
            ),
        ),
    )


def _grant(executable: str, **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "capabilities": ["process.exec"],
        "allowed_executables": [executable],
        "max_steps": 4,
        "max_commands": 4,
        "max_retries": 2,
        "max_runtime_seconds": 20,
        "duration_seconds": 1800,
    }
    value.update(changes)
    return value


def _propose(
    loop: LocalCognitiveActionLoop,
    run: AutonomyRun,
    wait_id: str,
    *,
    key: str = "request-one",
    plan: RunPlan | None = None,
    grant: dict[str, object] | None = None,
    objective: str | None = None,
    workspace: str | None = None,
) -> dict[str, object]:
    executable = run.grant.allowed_executables[0]
    return loop.propose_successor(
        wait_id,
        actor="owner",
        request_key=key,
        objective=objective if objective is not None else run.plan.objective,
        workspace_root=workspace if workspace is not None else str(run.workspace.root),
        plan=plan or _plan(run.plan.objective, executable),
        grant_spec=grant or _grant(executable),
    )


def test_happy_proposal_is_metadata_only_and_canonical(tmp_path: Path) -> None:
    database, loop, run, wait_id = _fixture(tmp_path)
    before = AutonomyRepository(database).get(run.run_id)
    item = _propose(loop, run, wait_id)
    assert item["status"] == "proposed"
    assert item["successor_run_id"] is None
    assert item["grant_spec"]["capabilities"] == ["process.exec"]
    assert len(str(item["candidate_sha256"])) == 64
    after = AutonomyRepository(database).get(run.run_id)
    assert before == after
    with database.connect() as connection:
        lineage = connection.execute(
            """SELECT c.public_id FROM assistant_cognitive_cycles c
               JOIN assistant_cognitive_owner_waits w ON w.cycle_id=c.id
               WHERE w.public_id=?""",
            (wait_id,),
        ).fetchone()
        expected_fingerprint = loop.autonomy._predecessor_state_sha256(
            connection,
            run.run_id,
            str(lineage["public_id"]),
            wait_id,
            actor="owner",
        )
        assert item["predecessor_state_sha256"] == expected_fingerprint
        assert connection.execute(
            "SELECT state FROM assistant_cognitive_owner_waits WHERE public_id=?",
            (wait_id,),
        ).fetchone()[0] == "pending"
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_runs"
        ).fetchone()[0] == 1


def test_objective_and_workspace_must_be_exact(tmp_path: Path) -> None:
    _database, loop, run, wait_id = _fixture(tmp_path)
    with pytest.raises(PermissionError):
        _propose(loop, run, wait_id, objective="Different")
    with pytest.raises(PermissionError):
        _propose(loop, run, wait_id, workspace=str(run.workspace.root.parent))
    child = run.workspace.root / "child"
    child.mkdir()
    with pytest.raises(PermissionError):
        _propose(loop, run, wait_id, workspace=str(child))
    alias = run.workspace.root.parent / "alias"
    alias.symlink_to(run.workspace.root, target_is_directory=True)
    assert _propose(loop, run, wait_id, workspace=str(alias))["workspace_root"] == str(
        run.workspace.root
    )


@pytest.mark.parametrize(
    "change",
    [
        {"capabilities": []},
        {"capabilities": ["network.model"]},
        {"allowed_executables": []},
        {"max_steps": 5},
        {"max_commands": 5},
        {"max_retries": 3},
        {"max_runtime_seconds": 21},
        {"duration_seconds": 1801},
        {"max_steps": True},
        {"max_steps": 0},
        {"max_steps": -1},
        {"max_steps": 1.0},
        {"max_commands": 0},
        {"max_commands": -1},
        {"max_commands": 1.0},
        {"max_retries": -1},
        {"max_retries": 1.0},
        {"max_runtime_seconds": 0},
        {"max_runtime_seconds": -1},
        {"max_runtime_seconds": 1.0},
        {"duration_seconds": 0},
        {"duration_seconds": -1},
        {"duration_seconds": 1.0},
    ],
)
def test_grant_authority_bounds_fail_closed(tmp_path: Path, change: dict[str, object]) -> None:
    _database, loop, run, wait_id = _fixture(tmp_path)
    with pytest.raises((PermissionError, TypeError, ValueError)):
        _propose(loop, run, wait_id, grant=_grant(run.grant.allowed_executables[0], **change))


def test_grant_exact_keys_duplicates_and_plan_contract(tmp_path: Path) -> None:
    _database, loop, run, wait_id = _fixture(tmp_path)
    executable = run.grant.allowed_executables[0]
    missing = _grant(executable)
    missing.pop("duration_seconds")
    with pytest.raises(ValueError):
        _propose(loop, run, wait_id, grant=missing)
    with pytest.raises(ValueError):
        _propose(loop, run, wait_id, grant={**_grant(executable), "issued_at": "x"})
    with pytest.raises(ValueError):
        _propose(
            loop,
            run,
            wait_id,
            grant=_grant(executable, allowed_executables=[executable, executable]),
        )
    with pytest.raises(ValueError):
        _propose(
            loop,
            run,
            wait_id,
            grant=_grant(
                executable,
                allowed_executables=[executable, f" {executable} "],
            ),
        )
    with pytest.raises(PermissionError):
        _propose(loop, run, wait_id, plan=_plan(run.plan.objective, executable, timeout=21))
    with pytest.raises((PermissionError, ValueError)):
        _propose(loop, run, wait_id, plan=_plan(run.plan.objective, executable, cwd="../"))


def test_candidate_hash_excludes_request_key_and_replay_is_exact(tmp_path: Path) -> None:
    _database, loop, run, wait_id = _fixture(tmp_path)
    first = _propose(loop, run, wait_id)
    replay = _propose(loop, run, wait_id)
    assert replay["public_id"] == first["public_id"]
    assert replay["candidate_sha256"] == first["candidate_sha256"]
    with pytest.raises(PermissionError):
        _propose(
            loop,
            run,
            wait_id,
            plan=_plan(run.plan.objective, run.grant.allowed_executables[0], timeout=4),
        )
    loop.reject_successor(str(first["public_id"]), actor="owner")
    rejected = _propose(loop, run, wait_id)
    assert rejected["status"] == "rejected"
    second = _propose(loop, run, wait_id, key="request-two")
    assert second["candidate_sha256"] == first["candidate_sha256"]


def test_active_uniqueness_and_rejection_preserve_lineage(tmp_path: Path) -> None:
    database, loop, run, wait_id = _fixture(tmp_path)
    first = _propose(loop, run, wait_id)
    with pytest.raises(PermissionError):
        _propose(loop, run, wait_id, key="other")
    with pytest.raises(PermissionError):
        loop.reject_successor(str(first["public_id"]), actor="intruder")
    rejected = loop.reject_successor(str(first["public_id"]), actor="owner")
    assert rejected["status"] == "rejected"
    with pytest.raises(PermissionError):
        loop.reject_successor(str(first["public_id"]), actor="owner")
    assert AutonomyRepository(database).get(run.run_id)["status"] == "running"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT state FROM assistant_cognitive_owner_waits WHERE public_id=?",
            (wait_id,),
        ).fetchone()[0] == "pending"


def test_concurrent_same_request_converges_and_different_keys_compete(tmp_path: Path) -> None:
    _database, loop, run, wait_id = _fixture(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _n: _propose(loop, run, wait_id), range(2)))
    assert len({item["public_id"] for item in outcomes}) == 1
    loop.reject_successor(str(outcomes[0]["public_id"]), actor="owner")

    def attempt(key: str) -> object:
        try:
            return _propose(loop, run, wait_id, key=key)
        except PermissionError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("a", "b")))
    assert sum(item != "denied" for item in results) == 1


def test_request_key_utf8_bound_and_canonical_json(tmp_path: Path) -> None:
    database, loop, run, wait_id = _fixture(tmp_path)
    with pytest.raises(ValueError):
        _propose(loop, run, wait_id, key="á" * 65)
    item = _propose(loop, run, wait_id)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT plan_json, grant_spec_json FROM assistant_cognitive_successor_handoffs"
        ).fetchone()
    assert row["plan_json"] == json.dumps(
        item["plan"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert row["grant_spec_json"] == json.dumps(
        item["grant_spec"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def test_replay_uses_stored_candidate_after_predecessor_mutation(tmp_path: Path) -> None:
    database, loop, run, wait_id = _fixture(tmp_path)
    proposed = _propose(loop, run, wait_id)
    stored_fingerprint = str(proposed["predecessor_state_sha256"])
    loop.autonomy.request_human_gate(
        run.run_id, actor="owner", reason="independent durable mutation"
    )
    with database.connect() as connection:
        cycle_id = str(
            connection.execute(
                "SELECT public_id FROM assistant_cognitive_cycles"
            ).fetchone()[0]
        )
        current = loop.autonomy._predecessor_state_sha256(
            connection, run.run_id, cycle_id, wait_id, actor="owner"
        )
    assert current != stored_fingerprint
    replay = _propose(loop, run, wait_id)
    assert replay["public_id"] == proposed["public_id"]
    assert replay["candidate_sha256"] == proposed["candidate_sha256"]
    assert replay["predecessor_state_sha256"] == stored_fingerprint
    with pytest.raises(PermissionError):
        _propose(
            loop,
            run,
            wait_id,
            plan=_plan(run.plan.objective, run.grant.allowed_executables[0], timeout=4),
        )


def test_rejected_and_accepted_rows_replay_without_old_preconditions(tmp_path: Path) -> None:
    database, loop, run, wait_id = _fixture(tmp_path)
    rejected_source = _propose(loop, run, wait_id)
    loop.reject_successor(str(rejected_source["public_id"]), actor="owner")
    rejected = _propose(loop, run, wait_id)
    assert rejected["status"] == "rejected"
    proposed = _propose(loop, run, wait_id, key="accepted-key")

    successor = AutonomyRun(
        actor="owner",
        workspace=run.workspace,
        grant=run.grant,
        plan=run.plan,
    )
    loop.autonomy.create(successor)
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        loop._accept_handoff_connection(
            connection,
            str(proposed["public_id"]),
            successor.run_id,
            actor="owner",
        )
    accepted = _propose(loop, run, wait_id, key="accepted-key")
    assert accepted["public_id"] == proposed["public_id"]
    assert accepted["status"] == "accepted"
    assert accepted["successor_run_id"] == successor.run_id


def test_exact_stored_workspace_replay_survives_missing_filesystem(tmp_path: Path) -> None:
    _database, loop, run, wait_id = _fixture(tmp_path)
    proposed = _propose(loop, run, wait_id)
    stored_root = str(proposed["workspace_root"])
    run.workspace.root.rename(run.workspace.root.parent / "moved")
    replay = _propose(loop, run, wait_id, workspace=stored_root)
    assert replay["public_id"] == proposed["public_id"]
    loop.reject_successor(str(proposed["public_id"]), actor="owner")
    with pytest.raises(ValueError):
        _propose(loop, run, wait_id, key="new-key", workspace=stored_root)


def test_global_request_key_collision_is_permission_denial(tmp_path: Path) -> None:
    _database, loop, run, wait_id = _fixture(tmp_path)
    _propose(loop, run, wait_id, key="global-key")
    with pytest.raises(PermissionError, match="otro actor"):
        loop.propose_successor(
            wait_id,
            actor="intruder",
            request_key="global-key",
            objective=run.plan.objective,
            workspace_root=str(run.workspace.root),
            plan=run.plan,
            grant_spec=_grant(run.grant.allowed_executables[0]),
        )


def test_concurrent_same_key_different_candidate_has_one_winner(tmp_path: Path) -> None:
    database, loop, run, wait_id = _fixture(tmp_path)
    executable = run.grant.allowed_executables[0]

    def attempt(timeout: int) -> object:
        try:
            return _propose(
                loop,
                run,
                wait_id,
                key="contended-key",
                plan=_plan(run.plan.objective, executable, timeout=timeout),
            )
        except PermissionError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (3, 4)))
    assert sum(item != "denied" for item in results) == 1
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_successor_handoffs"
        ).fetchone()[0] == 1


def test_proposal_rejection_race_is_serializable(tmp_path: Path) -> None:
    database, loop, run, wait_id = _fixture(tmp_path)
    original = _propose(loop, run, wait_id)

    def reject() -> str:
        loop.reject_successor(str(original["public_id"]), actor="owner")
        return "rejected"

    def replace() -> str:
        try:
            _propose(loop, run, wait_id, key="replacement")
            return "proposed"
        except PermissionError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [pool.submit(reject), pool.submit(replace)]
        assert {future.result() for future in outcomes} <= {
            "rejected",
            "proposed",
            "denied",
        }
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_successor_handoffs "
            "WHERE status='proposed'"
        ).fetchone()[0] <= 1
        assert connection.execute(
            "SELECT state FROM assistant_cognitive_owner_waits WHERE public_id=?",
            (wait_id,),
        ).fetchone()[0] == "pending"
        assert connection.execute(
            "SELECT status FROM assistant_cognitive_cycles"
        ).fetchone()[0] == "waiting_owner"
    assert AutonomyRepository(database).get(run.run_id)["status"] == "running"


def test_predecessor_caps_and_duration_ceiling_are_exact(tmp_path: Path) -> None:
    _database, loop, run, wait_id = _fixture(
        tmp_path, duration_seconds=7200, max_steps=2, max_commands=2
    )
    executable = run.grant.allowed_executables[0]
    accepted = _propose(
        loop,
        run,
        wait_id,
        grant=_grant(
            executable,
            max_steps=2,
            max_commands=2,
            max_retries=0,
            max_runtime_seconds=3,
            duration_seconds=3600,
        ),
    )
    assert accepted["grant_spec"]["duration_seconds"] == 3600
    loop.reject_successor(str(accepted["public_id"]), actor="owner")
    for change in (
        {"max_steps": 3},
        {"max_commands": 3},
        {"duration_seconds": 3601},
    ):
        invalid = _grant(
            executable, max_steps=2, max_commands=2, duration_seconds=3600
        )
        invalid.update(change)
        with pytest.raises(PermissionError):
            _propose(
                loop,
                run,
                wait_id,
                key=f"bad-{next(iter(change))}",
                grant=invalid,
            )


def test_proposal_and_rejection_have_no_execution_or_authority_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, loop, run, wait_id = _fixture(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("Phase 8B.4 crossed its metadata-only boundary")

    monkeypatch.setattr(loop.language_engine, "reply", forbidden)
    monkeypatch.setattr(cognitive_module.SupervisedAutonomyRunner, "tick", forbidden)
    monkeypatch.setattr(loop.autonomy, "create", forbidden)
    monkeypatch.setattr(loop, "create_cycle", forbidden)
    before = AutonomyRepository(database).get(run.run_id)
    proposed = _propose(loop, run, wait_id)
    rejected = loop.reject_successor(str(proposed["public_id"]), actor="owner")
    assert rejected["status"] == "rejected"
    assert AutonomyRepository(database).get(run.run_id) == before
    with database.connect() as connection:
        assert connection.execute(
            "SELECT state FROM assistant_cognitive_owner_waits WHERE public_id=?",
            (wait_id,),
        ).fetchone()[0] == "pending"
        assert connection.execute(
            "SELECT status FROM assistant_cognitive_cycles"
        ).fetchone()[0] == "waiting_owner"
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_runs"
        ).fetchone()[0] == 1


def test_multiple_executables_have_deterministic_canonical_order(tmp_path: Path) -> None:
    _database, loop, run, wait_id = _fixture(tmp_path, extra_executable=True)
    reversed_executables = list(reversed(run.grant.allowed_executables))
    proposed = _propose(
        loop,
        run,
        wait_id,
        grant=_grant(
            run.grant.allowed_executables[0],
            allowed_executables=reversed_executables,
        ),
    )
    assert proposed["grant_spec"]["allowed_executables"] == sorted(
        run.grant.allowed_executables
    )
