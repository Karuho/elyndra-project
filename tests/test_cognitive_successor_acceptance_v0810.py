from __future__ import annotations

import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import elyndra.cognitive_loop as cognitive_module
from elyndra.autonomy import (
    AutonomyExecutionBinding,
    AutonomyRepository,
    AutonomyRun,
    AutonomyRunStatus,
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
    name = "acceptance-test"
    supports_vision = False

    def reply(self, prompt: str, **kwargs: object) -> LanguageReply:
        return LanguageReply('{"decision":"propose_replan"}', self.name, True)

    def release(self) -> None:
        return None


class _Memory:
    def recall(self, *args: object, **kwargs: object) -> object:
        return type("Recall", (), {"items": []})()


def _fixture(tmp_path: Path) -> tuple[Database, LocalCognitiveActionLoop, AutonomyRun, str]:
    workspace = tmp_path / "project"
    workspace.mkdir()
    executable = str(Path(sys.executable).resolve(strict=True))
    now = datetime.now(UTC)
    plan = RunPlan(
        objective="Inspect project",
        steps=(
            RunStep(
                step_id="run",
                capability=Capability.PROCESS_EXEC,
                action="run tests",
                target=".",
                command=CommandSpec(
                    executable=executable,
                    argv=(executable, "-c", "print('ok')"),
                    cwd=".",
                    timeout_seconds=3,
                ),
            ),
        ),
    )
    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(workspace),
        grant=CapabilityGrant(
            capabilities=frozenset({Capability.PROCESS_EXEC}),
            issued_at=now,
            expires_at=now + timedelta(minutes=30),
            max_steps=4,
            max_commands=4,
            max_retries=2,
            max_runtime_seconds=20,
            allowed_executables=(executable,),
        ),
        plan=plan,
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


def _propose(
    loop: LocalCognitiveActionLoop, run: AutonomyRun, wait_id: str
) -> dict[str, object]:
    executable = run.grant.allowed_executables[0]
    return loop.propose_successor(
        wait_id,
        actor="owner",
        request_key="acceptance-request",
        objective=run.plan.objective,
        workspace_root=str(run.workspace.root),
        plan=run.plan,
        grant_spec={
            "capabilities": ["process.exec"],
            "allowed_executables": [executable],
            "max_steps": 4,
            "max_commands": 4,
            "max_retries": 2,
            "max_runtime_seconds": 20,
            "duration_seconds": 1800,
        },
    )


def _rows(database: object) -> dict[str, object]:
    with database.connect() as connection:  # type: ignore[attr-defined]
        runs = connection.execute(
            "SELECT public_id, status FROM assistant_autonomy_runs ORDER BY id"
        ).fetchall()
        cycle = connection.execute(
            "SELECT status FROM assistant_cognitive_cycles"
        ).fetchone()
        wait = connection.execute(
            "SELECT state, reason, resolution FROM assistant_cognitive_owner_waits"
        ).fetchone()
        handoff = connection.execute(
            "SELECT status, successor_run_id FROM assistant_cognitive_successor_handoffs"
        ).fetchone()
        autonomy_events = connection.execute(
            """SELECT r.public_id, e.event_type, e.from_status, e.to_status, e.summary
               FROM assistant_autonomy_events e
               JOIN assistant_autonomy_runs r ON r.id=e.run_id ORDER BY e.id"""
        ).fetchall()
        cognitive_events = connection.execute(
            """SELECT event_type, from_status, to_status, summary_code
               FROM assistant_cognitive_cycle_events ORDER BY id"""
        ).fetchall()
    return {
        "runs": [(row["public_id"], row["status"]) for row in runs],
        "cycle": tuple(cycle),
        "wait": tuple(wait),
        "handoff": tuple(handoff),
        "autonomy_events": [tuple(row) for row in autonomy_events],
        "cognitive_events": [tuple(row) for row in cognitive_events],
    }


def test_happy_acceptance_is_one_complete_handoff(tmp_path: Path) -> None:
    database, loop, predecessor, wait_id = _fixture(tmp_path)
    proposed = _propose(loop, predecessor, wait_id)
    accepted = loop.accept_successor(str(proposed["public_id"]), actor="owner")
    assert accepted["status"] == "accepted"
    successor_id = str(accepted["successor_run_id"])
    successor = loop.autonomy.get(successor_id)
    assert successor is not None
    assert successor["status"] == "planned"
    assert successor["actor"] == "owner"
    assert successor["objective"] == predecessor.plan.objective
    assert successor["workspace_root"] == str(predecessor.workspace.root)
    assert successor["plan"] == accepted["plan"]
    grant = successor["grant"]
    spec = accepted["grant_spec"]
    for key in (
        "capabilities",
        "allowed_executables",
        "max_steps",
        "max_commands",
        "max_retries",
        "max_runtime_seconds",
    ):
        assert grant[key] == spec[key]
    issued = datetime.fromisoformat(grant["issued_at"])
    expires = datetime.fromisoformat(grant["expires_at"])
    assert int((expires - issued).total_seconds()) == spec["duration_seconds"]
    assert grant["allowed_hosts"] == []

    predecessor_row = loop.autonomy.get(predecessor.run_id)
    assert predecessor_row is not None and predecessor_row["status"] == "cancelled"
    with database.connect() as connection:
        cycle = connection.execute(
            "SELECT status FROM assistant_cognitive_cycles WHERE autonomy_run_id="
            "(SELECT id FROM assistant_autonomy_runs WHERE public_id=?)",
            (predecessor.run_id,),
        ).fetchone()
        assert cycle["status"] == "stopped"
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_cycle_events "
            "WHERE event_type='cycle_stopped' AND summary_code='cycle_superseded'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT state || ':' || resolution FROM assistant_cognitive_owner_waits "
            "WHERE public_id=?",
            (wait_id,),
        ).fetchone()[0] == "resolved:successor_accepted"
        successor_db_id = connection.execute(
            "SELECT id FROM assistant_autonomy_runs WHERE public_id=?", (successor_id,)
        ).fetchone()[0]
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_events "
            "WHERE run_id=? AND event_type='run_created'",
            (successor_db_id,),
        ).fetchone()[0] == 1
        for table, column in (
            ("assistant_autonomy_execution_reservations", "run_id"),
            ("assistant_autonomy_execution_launches", "run_id"),
            ("assistant_autonomy_execution_results", "run_id"),
            ("assistant_autonomy_human_gates", "run_id"),
            ("assistant_autonomy_retry_reviews", "run_id"),
            ("assistant_cognitive_cycles", "autonomy_run_id"),
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (successor_db_id,)
            ).fetchone()[0] == 0
        assert connection.execute(
            """SELECT COUNT(*) FROM assistant_autonomy_retry_consumptions rc
               JOIN assistant_autonomy_retry_reviews rr ON rr.id=rc.retry_review_id
               WHERE rr.run_id=?""",
            (successor_db_id,),
        ).fetchone()[0] == 0


def test_accepted_replay_is_read_only_after_start_and_missing_workspace(
    tmp_path: Path,
) -> None:
    database, loop, predecessor, wait_id = _fixture(tmp_path)
    proposed = _propose(loop, predecessor, wait_id)
    accepted = loop.accept_successor(str(proposed["public_id"]), actor="owner")
    successor_id = str(accepted["successor_run_id"])
    loop.autonomy.transition(successor_id, "running", actor="owner", summary="explicit start")
    predecessor.workspace.root.rename(predecessor.workspace.root.parent / "moved")
    with database.connect() as connection:
        before_events = connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_events"
        ).fetchone()[0]
    replay = loop.accept_successor(str(proposed["public_id"]), actor="owner")
    assert replay["successor_run_id"] == successor_id
    assert loop.autonomy.get(successor_id)["status"] == "running"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_events"
        ).fetchone()[0] == before_events


def test_wrong_actor_rejected_and_stale_fingerprint_has_zero_writes(tmp_path: Path) -> None:
    database, loop, predecessor, wait_id = _fixture(tmp_path)
    proposed = _propose(loop, predecessor, wait_id)
    with pytest.raises(PermissionError):
        loop.accept_successor(str(proposed["public_id"]), actor="intruder")
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        run_db_id = connection.execute(
            "SELECT id FROM assistant_autonomy_runs WHERE public_id=?",
            (predecessor.run_id,),
        ).fetchone()[0]
        loop.autonomy._insert_event(
            connection,
            run_db_id=run_db_id,
            event_type="owner_state_observed",
            from_status=AutonomyRunStatus.RUNNING,
            to_status=AutonomyRunStatus.RUNNING,
            summary="Durable predecessor mutation for stale-fingerprint test.",
            payload={},
            created_at=datetime.now(UTC).isoformat(),
        )
    before = _rows(database)
    with pytest.raises(PermissionError, match="obsoleta"):
        loop.accept_successor(str(proposed["public_id"]), actor="owner")
    assert _rows(database) == before
    assert len(before["runs"]) == 1  # type: ignore[arg-type]


def test_preexisting_gap_is_denied_even_when_proposal_fingerprint_includes_it(
    tmp_path: Path,
) -> None:
    database, loop, predecessor, wait_id = _fixture(tmp_path)
    contract = AutonomyExecutionBinding(loop.autonomy).bind(
        predecessor.run_id, actor="owner"
    )
    contract.prepare("run")
    proposed = _propose(loop, predecessor, wait_id)
    before = _rows(database)
    with pytest.raises(PermissionError, match="intento incompleto"):
        loop.accept_successor(str(proposed["public_id"]), actor="owner")
    assert _rows(database) == before
    assert before["handoff"] == ("proposed", None)
    assert before["wait"] == ("pending", "replan_requested", None)
    assert before["cycle"] == ("waiting_owner",)
    assert len(before["runs"]) == 1  # type: ignore[arg-type]


def test_rejected_and_unknown_handoffs_are_denied(tmp_path: Path) -> None:
    _database, loop, predecessor, wait_id = _fixture(tmp_path)
    proposed = _propose(loop, predecessor, wait_id)
    loop.reject_successor(str(proposed["public_id"]), actor="owner")
    with pytest.raises(PermissionError):
        loop.accept_successor(str(proposed["public_id"]), actor="owner")
    with pytest.raises(PermissionError):
        loop.accept_successor("missing", actor="owner")


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("candidate_sha256", "f" * 64),
        ("predecessor_state_sha256", "e" * 64),
        ("plan_json", "{}"),
        ("grant_spec_json", "{}"),
        ("objective", "tampered"),
        ("workspace_root", "/tampered"),
    ),
)
def test_candidate_identity_tampering_is_rejected_by_schema(
    tmp_path: Path, column: str, value: str
) -> None:
    database, loop, predecessor, wait_id = _fixture(tmp_path)
    proposed = _propose(loop, predecessor, wait_id)
    with database.connect() as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            f"UPDATE assistant_cognitive_successor_handoffs SET {column}=? "
            "WHERE public_id=?",
            (value, proposed["public_id"]),
        )


@pytest.mark.parametrize("defect", ("host", "fractional_duration"))
def test_accepted_replay_rejects_host_or_inexact_duration_authority(
    tmp_path: Path, defect: str
) -> None:
    database, loop, predecessor, wait_id = _fixture(tmp_path)
    proposed = _propose(loop, predecessor, wait_id)
    issued = datetime.now(UTC)
    extra = timedelta(microseconds=1) if defect == "fractional_duration" else timedelta()
    bad_grant = CapabilityGrant(
        capabilities=frozenset({Capability.PROCESS_EXEC}),
        issued_at=issued,
        expires_at=issued + timedelta(seconds=1800) + extra,
        max_steps=4,
        max_commands=4,
        max_retries=2,
        max_runtime_seconds=20,
        allowed_hosts=("example.invalid",) if defect == "host" else (),
        allowed_executables=predecessor.grant.allowed_executables,
    )
    successor = AutonomyRun(
        actor="owner",
        workspace=predecessor.workspace,
        grant=bad_grant,
        plan=predecessor.plan,
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
    with database.connect() as connection:
        row = loop._handoff_lineage_connection(
            connection, str(proposed["public_id"])
        )
        with pytest.raises(PermissionError, match="Autoridad durable"):
            loop.autonomy._require_accepted_successor_integrity_connection(
                connection, row, actor="owner"
            )


def test_concurrent_accepts_converge_on_one_successor(tmp_path: Path) -> None:
    database, loop, predecessor, wait_id = _fixture(tmp_path)
    proposed = _propose(loop, predecessor, wait_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _n: loop.accept_successor(
                    str(proposed["public_id"]), actor="owner"
                ),
                range(2),
            )
        )
    assert len({row["successor_run_id"] for row in results}) == 1
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_runs"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_events WHERE event_type='run_created'"
        ).fetchone()[0] == 2


def test_accept_reject_race_has_no_hybrid_state(tmp_path: Path) -> None:
    database, loop, predecessor, wait_id = _fixture(tmp_path)
    proposed = _propose(loop, predecessor, wait_id)

    def accept() -> str:
        try:
            loop.accept_successor(str(proposed["public_id"]), actor="owner")
            return "accepted"
        except PermissionError:
            return "denied"

    def reject() -> str:
        try:
            loop.reject_successor(str(proposed["public_id"]), actor="owner")
            return "rejected"
        except PermissionError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(accept), pool.submit(reject))
        outcomes = {future.result() for future in futures}
    assert outcomes in ({"accepted", "denied"}, {"rejected", "denied"})
    state = _rows(database)
    if "accepted" in outcomes:
        assert state["handoff"][0] == "accepted"  # type: ignore[index]
        assert len(state["runs"]) == 2  # type: ignore[arg-type]
        assert state["cycle"] == ("stopped",)
        assert state["wait"] == ("resolved", "replan_requested", "successor_accepted")
    else:
        assert state["handoff"] == ("rejected", None)
        assert len(state["runs"]) == 1  # type: ignore[arg-type]
        assert state["cycle"] == ("waiting_owner",)
        assert state["wait"] == ("pending", "replan_requested", None)


def test_accept_and_predecessor_mutation_serialize_without_hybrid(tmp_path: Path) -> None:
    database, loop, predecessor, wait_id = _fixture(tmp_path)
    proposed = _propose(loop, predecessor, wait_id)

    def accept() -> str:
        try:
            loop.accept_successor(str(proposed["public_id"]), actor="owner")
            return "accepted"
        except PermissionError:
            return "denied"

    def mutate() -> str:
        try:
            loop.autonomy.request_human_gate(
                predecessor.run_id, actor="owner", reason="concurrent mutation"
            )
            return "mutated"
        except (PermissionError, ValueError):
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(accept), pool.submit(mutate))
        outcomes = {future.result() for future in futures}
    assert outcomes in ({"accepted", "denied"}, {"mutated", "denied"})
    state = _rows(database)
    if "accepted" in outcomes:
        assert outcomes == {"accepted", "denied"}
        assert len(state["runs"]) == 2  # type: ignore[arg-type]
        assert state["handoff"][0] == "accepted"  # type: ignore[index]
    else:
        assert outcomes == {"mutated", "denied"}
        assert len(state["runs"]) == 1  # type: ignore[arg-type]
        assert state["handoff"] == ("proposed", None)


@pytest.mark.parametrize(
    "stage",
    ("insert", "cancel", "cycle", "wait", "link", "verify"),
)
def test_every_acceptance_write_boundary_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    database, loop, predecessor, wait_id = _fixture(tmp_path)
    proposed = _propose(loop, predecessor, wait_id)
    before = _rows(database)
    targets = {
        "insert": (loop.autonomy, "_insert_run_connection"),
        "cancel": (loop.autonomy, "_transition_connection"),
        "cycle": (loop, "_terminalize_cycle_connection"),
        "wait": (loop, "_resolve_successor_wait_connection"),
        "link": (loop, "_accept_handoff_connection"),
        "verify": (loop, "_require_first_acceptance_invariants_connection"),
    }
    owner, name = targets[stage]
    original = getattr(owner, name)

    def fail_after(*args: object, **kwargs: object) -> object:
        original(*args, **kwargs)
        raise RuntimeError(f"fault after {stage}")

    monkeypatch.setattr(owner, name, fail_after)
    with pytest.raises(RuntimeError, match=stage):
        loop.accept_successor(str(proposed["public_id"]), actor="owner")
    assert _rows(database) == before


def test_acceptance_uses_no_public_nested_or_execution_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _database, loop, predecessor, wait_id = _fixture(tmp_path)
    proposed = _propose(loop, predecessor, wait_id)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("acceptance crossed its transaction boundary")

    monkeypatch.setattr(loop.autonomy, "create", forbidden)
    monkeypatch.setattr(loop.autonomy, "transition", forbidden)
    monkeypatch.setattr(loop.language_engine, "reply", forbidden)
    monkeypatch.setattr(cognitive_module.SupervisedAutonomyRunner, "tick", forbidden)
    monkeypatch.setattr(loop, "create_cycle", forbidden)
    monkeypatch.setattr(loop, "advance", forbidden)
    accepted = loop.accept_successor(str(proposed["public_id"]), actor="owner")
    assert loop.autonomy.get(str(accepted["successor_run_id"]))["status"] == "planned"
