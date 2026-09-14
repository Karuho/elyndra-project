from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from elyndra.autonomy import (
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
    name = "model-successor-test"
    supports_vision = False

    def __init__(self, reply: str) -> None:
        self.raw_reply = reply
        self.calls: list[dict[str, object]] = []

    def reply(self, prompt: str, **kwargs: object) -> LanguageReply:
        self.calls.append({"prompt": prompt, **kwargs})
        return LanguageReply(self.raw_reply, self.name, True)

    def release(self) -> None:
        return None


class _Memory:
    def recall(self, *args: object, **kwargs: object) -> object:
        return type("Recall", (), {"items": []})()


def _reply(steps: list[dict[str, str]]) -> str:
    return json.dumps(
        {"decision": "propose_replan", "successor": {"steps": steps}},
        sort_keys=True,
        separators=(",", ":"),
    )


def _state(
    tmp_path: Path,
    reply: str,
    *,
    capabilities: frozenset[Capability] = frozenset(
        {Capability.PROCESS_EXEC, Capability.SELF_MODIFY}
    ),
    max_steps: int = 4,
    max_commands: int = 4,
    max_runtime_seconds: int = 20,
) -> tuple[Database, AutonomyRepository, LocalCognitiveActionLoop, AutonomyRun, _Engine, str]:
    workspace = tmp_path / "project"
    workspace.mkdir(parents=True)
    executable = str(Path(sys.executable).resolve(strict=True))
    command = CommandSpec(
        executable=executable,
        argv=(executable, "-c", "print('frozen argv')"),
        cwd=".",
        timeout_seconds=3,
        stdout_limit_bytes=1234,
        stderr_limit_bytes=2345,
    )
    steps = (
        RunStep(
            step_id="run",
            capability=Capability.PROCESS_EXEC,
            action="run frozen validation",
            target=".",
            command=command,
        ),
        RunStep(
            step_id="modify-old",
            capability=Capability.SELF_MODIFY,
            action="old reviewed mutation",
            target="src/old.py",
            requires_human_gate=True,
        ),
    )
    selected_steps = tuple(step for step in steps if step.capability in capabilities)
    now = datetime.now(UTC)
    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(workspace),
        grant=CapabilityGrant(
            capabilities=capabilities,
            issued_at=now,
            expires_at=now + timedelta(minutes=30),
            max_steps=max_steps,
            max_commands=max_commands,
            max_retries=2,
            max_runtime_seconds=max_runtime_seconds,
            allowed_executables=(executable,) if Capability.PROCESS_EXEC in capabilities else (),
        ),
        plan=RunPlan(objective="exact frozen objective", steps=selected_steps),
    )
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    repository.create(run)
    repository.transition(run.run_id, "running", actor="owner", summary="start")
    engine = _Engine(reply)
    loop = LocalCognitiveActionLoop(
        database, language_engine=engine, memory=_Memory()  # type: ignore[arg-type]
    )
    cycle = loop.create_cycle(run.run_id, actor="owner")
    return database, repository, loop, run, engine, str(cycle["public_id"])


def _counts(database: Database) -> dict[str, int]:
    tables = (
        "assistant_autonomy_runs",
        "assistant_autonomy_execution_reservations",
        "assistant_autonomy_execution_launches",
        "assistant_autonomy_execution_results",
        "assistant_autonomy_human_gates",
        "assistant_autonomy_mutation_proposals",
        "assistant_autonomy_mutation_attempts",
        "assistant_cognitive_successor_handoffs",
        "assistant_cognitive_model_successor_origins",
    )
    with database.connect() as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def test_valid_model_proposal_is_atomic_exact_and_side_effect_free(tmp_path: Path) -> None:
    raw = _reply([{"type": "reuse_process", "source_step_id": "run"}])
    database, repository, loop, run, engine, cycle_id = _state(tmp_path, raw)
    before = _counts(database)
    result = loop.advance(cycle_id, actor="owner")
    assert result.status == "waiting_owner"
    assert result.decision == "propose_replan"
    assert len(engine.calls) == 1
    prompt = str(engine.calls[0]["prompt"])
    assert '"process_exec_available":true' in prompt
    assert '"self_modify_available":true' in prompt
    assert '"source_step_id":"run"' in prompt
    assert '"lineage_generation":0' in prompt

    handoff = loop.list_successor_handoffs(actor="owner")[0]
    assert handoff["proposal_origin"] == "model"
    assert handoff["source_model_turn_id"] == result.turn_id
    assert handoff["successor_run_id"] is None
    assert handoff["objective"] == run.plan.objective
    assert handoff["workspace_root"] == str(run.workspace.root)
    assert handoff["plan"]["steps"][0]["command"] == run.plan.steps[0].command.to_data()
    assert handoff["grant_spec"] == {
        "allowed_executables": [run.plan.steps[0].command.executable],
        "capabilities": ["process.exec"],
        "duration_seconds": 1800,
        "max_commands": 1,
        "max_retries": 0,
        "max_runtime_seconds": 3,
        "max_steps": 1,
    }
    expected_reply_hash = hashlib.sha256(
        b"elyndra.phase9b3.model-successor-reply.v1\0" + raw.encode()
    ).hexdigest()
    assert handoff["model_reply_sha256"] == expected_reply_hash

    after = _counts(database)
    assert after["assistant_autonomy_runs"] == before["assistant_autonomy_runs"] == 1
    for table in (
        "assistant_autonomy_execution_reservations",
        "assistant_autonomy_execution_launches",
        "assistant_autonomy_execution_results",
        "assistant_autonomy_human_gates",
        "assistant_autonomy_mutation_proposals",
        "assistant_autonomy_mutation_attempts",
    ):
        assert after[table] == 0
    with database.connect() as connection:
        source = connection.execute(
            "SELECT id, kind, state, decision FROM assistant_cognitive_turns"
        ).fetchone()
        wait = connection.execute(
            "SELECT state, reason, source_turn_id FROM assistant_cognitive_owner_waits"
        ).fetchone()
        origin = connection.execute(
            "SELECT * FROM assistant_cognitive_model_successor_origins"
        ).fetchone()
        assert tuple(source)[1:] == ("reason", "completed", "propose_replan")
        assert tuple(wait) == ("pending", "replan_requested", source["id"])
        assert origin["source_turn_id"] == source["id"]
        assert origin["candidate_sha256"] == handoff["candidate_sha256"]
        stored_values = [value for row in connection.execute(
            "SELECT * FROM assistant_cognitive_model_successor_origins"
        ) for value in row]
        assert raw not in stored_values
        assert raw not in "\n".join(connection.iterdump())
    assert repository.get(run.run_id)["status"] == "running"  # type: ignore[index]


def test_model_origin_is_immutable_and_schema63_is_vault_only(tmp_path: Path) -> None:
    raw = _reply([{"type": "reuse_process", "source_step_id": "run"}])
    database, _repository, loop, _run, _engine, cycle_id = _state(tmp_path / "state", raw)
    loop.advance(cycle_id, actor="owner")
    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute(
            "UPDATE assistant_cognitive_model_successor_origins SET created_at='changed'"
        )
    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute("DELETE FROM assistant_cognitive_model_successor_origins")
    database.migrate()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "63"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    root = Database(tmp_path / "root.sqlite3", role="root")
    root.migrate()
    root.migrate()
    with root.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "63"
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE name='assistant_cognitive_model_successor_origins'"
        ).fetchone() is None


def test_schema62_to_63_preserves_owner_handoffs_without_invented_origin(
    tmp_path: Path,
) -> None:
    database, _repository, loop, run, _engine, cycle_id = _state(
        tmp_path, '{"decision":"propose_replan"}'
    )
    loop.advance(cycle_id, actor="owner")
    wait = loop.list_owner_waits(actor="owner")[0]
    owner = loop.propose_successor(
        str(wait["public_id"]),
        actor="owner",
        request_key="schema62-owner",
        objective=run.plan.objective,
        workspace_root=str(run.workspace.root),
        plan=RunPlan(objective=run.plan.objective, steps=(run.plan.steps[0],)),
        grant_spec={
            "capabilities": ["process.exec"],
            "allowed_executables": list(run.grant.allowed_executables),
            "max_steps": 1,
            "max_commands": 1,
            "max_retries": 0,
            "max_runtime_seconds": 3,
            "duration_seconds": 600,
        },
    )
    with database.connect() as connection:
        connection.executescript(
            """
            DROP TRIGGER trg_cognitive_model_successor_origin_integrity;
            DROP TRIGGER trg_cognitive_model_successor_origin_no_update;
            DROP TRIGGER trg_cognitive_model_successor_origin_no_delete;
            DROP TABLE assistant_cognitive_model_successor_origins;
            UPDATE schema_meta SET value='62' WHERE key='schema_version';
            """
        )
    database.migrate()
    database.migrate()
    migrated = loop.successor_handoff(str(owner["public_id"]), actor="owner")
    assert migrated is not None and migrated["proposal_origin"] == "owner"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "63"
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_model_successor_origins"
        ).fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_owner_and_legacy_model_paths_remain_owner_controlled(tmp_path: Path) -> None:
    database, _repository, loop, run, _engine, cycle_id = _state(
        tmp_path, '{"decision":"propose_replan"}'
    )
    result = loop.advance(cycle_id, actor="owner")
    assert result.decision == "propose_replan"
    assert loop.list_successor_handoffs(actor="owner") == []
    wait = loop.list_owner_waits(actor="owner")[0]
    owner = loop.propose_successor(
        str(wait["public_id"]),
        actor="owner",
        request_key="owner-authored",
        objective=run.plan.objective,
        workspace_root=str(run.workspace.root),
        plan=RunPlan(objective=run.plan.objective, steps=(run.plan.steps[0],)),
        grant_spec={
            "capabilities": ["process.exec"],
            "allowed_executables": list(run.grant.allowed_executables),
            "max_steps": 1,
            "max_commands": 1,
            "max_retries": 0,
            "max_runtime_seconds": 3,
            "duration_seconds": 600,
        },
    )
    assert owner["proposal_origin"] == "owner"
    assert owner["source_model_turn_id"] is None
    assert owner["model_reply_sha256"] is None
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_model_successor_origins"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "forbidden",
    (
        "argv",
        "executable",
        "grant_spec",
        "capabilities",
        "max_steps",
        "max_commands",
        "max_retries",
        "max_runtime_seconds",
        "duration_seconds",
        "actor",
        "workspace_root",
        "objective",
    ),
)
def test_model_cannot_author_authority_or_command_fields(
    tmp_path: Path, forbidden: str
) -> None:
    payload = {
        "decision": "propose_replan",
        "successor": {
            "steps": [
                {"type": "reuse_process", "source_step_id": "run", forbidden: "x"}
            ]
        },
    }
    database, _repository, loop, _run, engine, cycle_id = _state(
        tmp_path, json.dumps(payload)
    )
    result = loop.advance(cycle_id, actor="owner")
    assert result.disposition == "malformed_model_output"
    assert len(engine.calls) == 1
    counts = _counts(database)
    assert counts["assistant_cognitive_successor_handoffs"] == 0
    assert counts["assistant_cognitive_model_successor_origins"] == 0


@pytest.mark.parametrize(
    ("steps", "capabilities"),
    (
        ([{"type": "reuse_process", "source_step_id": "unknown"}], None),
        (
            [
                {"type": "reuse_process", "source_step_id": "run"},
                {"type": "reuse_process", "source_step_id": "run"},
            ],
            None,
        ),
        ([{"type": "reuse_process", "source_step_id": "modify-old"}], None),
        (
            [{"type": "reuse_process", "source_step_id": "run"}],
            frozenset({Capability.SELF_MODIFY}),
        ),
        (
            [
                {
                    "type": "self_modify",
                    "step_id": "modify",
                    "action": "review mutation",
                    "target": "src/new.py",
                }
            ],
            frozenset({Capability.PROCESS_EXEC}),
        ),
    ),
)
def test_semantically_impossible_successor_fails_closed(
    tmp_path: Path,
    steps: list[dict[str, str]],
    capabilities: frozenset[Capability] | None,
) -> None:
    selected = capabilities or frozenset({Capability.PROCESS_EXEC, Capability.SELF_MODIFY})
    database, _repository, loop, _run, _engine, cycle_id = _state(
        tmp_path, _reply(steps), capabilities=selected
    )
    result = loop.advance(cycle_id, actor="owner")
    assert result.disposition == "malformed_model_output"
    assert _counts(database)["assistant_cognitive_successor_handoffs"] == 0


@pytest.mark.parametrize("target", ("/tmp/x", "../x", "src/../x", "src//x", "src/x\x00"))
def test_self_modify_target_must_be_canonical_and_safe(tmp_path: Path, target: str) -> None:
    raw = _reply(
        [
            {
                "type": "self_modify",
                "step_id": "modify",
                "action": "review exact mutation",
                "target": target,
            }
        ]
    )
    database, _repository, loop, _run, _engine, cycle_id = _state(tmp_path, raw)
    result = loop.advance(cycle_id, actor="owner")
    assert result.disposition == "malformed_model_output"
    assert _counts(database)["assistant_cognitive_successor_handoffs"] == 0


def test_self_modify_only_is_host_forced_and_owner_reviewed(tmp_path: Path) -> None:
    raw = _reply(
        [
            {
                "type": "self_modify",
                "step_id": "modify",
                "action": "prepare exact reviewed mutation",
                "target": "src/new.py",
            }
        ]
    )
    database, _repository, loop, _run, _engine, cycle_id = _state(tmp_path, raw)
    loop.advance(cycle_id, actor="owner")
    handoff = loop.list_successor_handoffs(actor="owner")[0]
    step = handoff["plan"]["steps"][0]
    assert step["capability"] == "self.modify"
    assert step["command"] is None
    assert step["requires_human_gate"] is True
    assert handoff["grant_spec"]["capabilities"] == ["self.modify"]
    assert handoff["grant_spec"]["allowed_executables"] == []
    assert handoff["grant_spec"]["max_commands"] == 1
    assert handoff["grant_spec"]["max_runtime_seconds"] == 1
    assert _counts(database)["assistant_autonomy_human_gates"] == 0


def test_runtime_or_lineage_overflow_and_exhausted_generation_reject(tmp_path: Path) -> None:
    raw = _reply([{"type": "reuse_process", "source_step_id": "run"}])
    database, _repository, loop, _run, _engine, cycle_id = _state(tmp_path / "runtime", raw)
    with database.connect() as connection:
        connection.execute("DROP TRIGGER trg_autonomy_lineages_no_update")
        connection.execute(
            "UPDATE assistant_autonomy_lineages SET max_runtime_seconds_total=2"
        )
    result = loop.advance(cycle_id, actor="owner")
    assert result.disposition == "malformed_model_output"
    assert _counts(database)["assistant_cognitive_successor_handoffs"] == 0

    database, _repository, loop, _run, _engine, cycle_id = _state(
        tmp_path / "generation", raw
    )
    with database.connect() as connection:
        connection.execute("DROP TRIGGER trg_autonomy_lineages_no_update")
        connection.execute("UPDATE assistant_autonomy_lineages SET max_successors=0")
    result = loop.advance(cycle_id, actor="owner")
    assert result.disposition == "malformed_model_output"
    assert _counts(database)["assistant_cognitive_successor_handoffs"] == 0


def test_reject_then_owner_replacement_and_model_acceptance_replay(tmp_path: Path) -> None:
    raw = _reply([{"type": "reuse_process", "source_step_id": "run"}])
    database, repository, loop, run, _engine, cycle_id = _state(tmp_path, raw)
    loop.advance(cycle_id, actor="owner")
    model = loop.list_successor_handoffs(actor="owner")[0]
    rejected = loop.reject_successor(str(model["public_id"]), actor="owner")
    assert rejected["proposal_origin"] == "model"
    assert repository.get(run.run_id)["status"] == "running"  # type: ignore[index]
    wait = loop.list_owner_waits(actor="owner")[0]
    owner = loop.propose_successor(
        str(wait["public_id"]),
        actor="owner",
        request_key="owner-replacement",
        objective=run.plan.objective,
        workspace_root=str(run.workspace.root),
        plan=RunPlan(objective=run.plan.objective, steps=(run.plan.steps[0],)),
        grant_spec={
            "capabilities": ["process.exec"],
            "allowed_executables": list(run.grant.allowed_executables),
            "max_steps": 1,
            "max_commands": 1,
            "max_retries": 0,
            "max_runtime_seconds": 3,
            "duration_seconds": 600,
        },
    )
    assert owner["proposal_origin"] == "owner"

    database2, repository2, loop2, run2, _engine2, cycle2 = _state(
        tmp_path / "accept", raw
    )
    loop2.advance(cycle2, actor="owner")
    proposed = loop2.list_successor_handoffs(actor="owner")[0]
    accepted = loop2.accept_successor(str(proposed["public_id"]), actor="owner")
    replay = loop2.accept_successor(str(proposed["public_id"]), actor="owner")
    assert accepted == replay
    assert accepted["proposal_origin"] == "model"
    assert accepted["successor_run_id"] is not None
    assert len(repository2.list_recent(actor="owner")) == 2
    successor = repository2.get(str(accepted["successor_run_id"]))
    assert successor is not None and successor["status"] == "planned"
    assert successor["grant"]["max_retries"] == 0
    assert repository2.lineage_budget_snapshot(
        str(accepted["successor_run_id"]), actor="owner"
    )["generation"] == 1
    assert repository2.get(run2.run_id)["status"] == "cancelled"  # type: ignore[index]


def test_internal_request_replay_is_exact_and_conflicts_fail_closed(tmp_path: Path) -> None:
    steps = ({"type": "reuse_process", "source_step_id": "run"},)
    raw = _reply(list(steps))
    database, _repository, loop, _run, _engine, cycle_id = _state(tmp_path, raw)
    result = loop.advance(cycle_id, actor="owner")
    with database.connect() as connection:
        cycle = connection.execute(
            "SELECT * FROM assistant_cognitive_cycles WHERE public_id=?", (cycle_id,)
        ).fetchone()
        turn = connection.execute(
            "SELECT * FROM assistant_cognitive_turns WHERE public_id=?", (result.turn_id,)
        ).fetchone()
        wait = connection.execute(
            "SELECT * FROM assistant_cognitive_owner_waits WHERE source_turn_id=?",
            (int(turn["id"]),),
        ).fetchone()
        connection.execute("BEGIN IMMEDIATE")
        first = loop._persist_model_successor_connection(
            connection,
            cycle=cycle,
            turn=turn,
            wait=wait,
            successor_steps=steps,
            raw_model_reply=raw,
            created_at=str(wait["created_at"]),
        )
        assert first is not None
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_successor_handoffs"
        ).fetchone()[0] == 1
        with pytest.raises(PermissionError):
            loop._persist_model_successor_connection(
                connection,
                cycle=cycle,
                turn=turn,
                wait=wait,
                successor_steps=steps,
                raw_model_reply=raw + " ",
                created_at=str(wait["created_at"]),
            )


def test_concurrent_duplicate_model_acceptance_converges(tmp_path: Path) -> None:
    raw = _reply([{"type": "reuse_process", "source_step_id": "run"}])
    _database, repository, loop, _run, _engine, cycle_id = _state(tmp_path, raw)
    loop.advance(cycle_id, actor="owner")
    handoff_id = str(loop.list_successor_handoffs(actor="owner")[0]["public_id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(loop.accept_successor, handoff_id, actor="owner") for _ in range(2)]
    results = [future.result() for future in futures]
    assert results[0]["successor_run_id"] == results[1]["successor_run_id"]
    assert len(repository.list_recent(actor="owner")) == 2


def test_stale_model_proposal_rejects_without_hybrid_state(tmp_path: Path) -> None:
    raw = _reply([{"type": "reuse_process", "source_step_id": "run"}])
    database, repository, loop, run, _engine, cycle_id = _state(tmp_path, raw)
    loop.advance(cycle_id, actor="owner")
    proposed = loop.list_successor_handoffs(actor="owner")[0]
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        run_db_id = int(
            connection.execute(
                "SELECT id FROM assistant_autonomy_runs WHERE public_id=?", (run.run_id,)
            ).fetchone()[0]
        )
        loop.autonomy._insert_event(
            connection,
            run_db_id=run_db_id,
            event_type="owner_state_observed",
            from_status=AutonomyRunStatus.RUNNING,
            to_status=AutonomyRunStatus.RUNNING,
            summary="stale model successor test",
            payload={},
            created_at=datetime.now(UTC).isoformat(),
        )
    before = _counts(database)
    with pytest.raises(PermissionError, match="obsoleta"):
        loop.accept_successor(str(proposed["public_id"]), actor="owner")
    assert _counts(database) == before
    assert len(repository.list_recent(actor="owner")) == 1


@pytest.mark.parametrize(
    "raw",
    (
        '{"decision":"propose_replan","successor":{"steps":[]}}',
        '{"decision":"propose_replan","decision":"stop"}',
        '{"decision":"propose_replan","confidence":NaN}',
        '{"decision":"execute_next","step_id":"run","successor":{"steps":[]}}',
    ),
)
def test_malformed_successor_json_never_partially_persists(
    tmp_path: Path, raw: str
) -> None:
    database, _repository, loop, _run, engine, cycle_id = _state(tmp_path, raw)
    result = loop.advance(cycle_id, actor="owner")
    assert result.disposition == "malformed_model_output"
    assert len(engine.calls) == 1
    counts = _counts(database)
    assert counts["assistant_cognitive_successor_handoffs"] == 0
    assert counts["assistant_cognitive_model_successor_origins"] == 0
