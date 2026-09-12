from __future__ import annotations

import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from elyndra.application import ElyndraApplication
from elyndra.autonomy import (
    AutonomyExecutionBinding,
    AutonomyRepository,
    AutonomyRun,
    BubblewrapExecutor,
    Capability,
    CapabilityGrant,
    CommandSpec,
    ExecutionOutcome,
    ExecutionResult,
    RunPlan,
    RunStep,
    SupervisedAutonomyRunner,
    SupervisedTickOutcome,
    SupervisedTickResult,
    WorkspaceScope,
)
from elyndra.cognitive_loop import LocalCognitiveActionLoop
from elyndra.db import Database
from elyndra.engines import LanguageReply, NoModelEngine
from elyndra.memory import (
    MemoryLifecycleRepository,
    MemoryRepository,
    TieredMemoryRepository,
)
from elyndra.paths import ElyndraPaths


class _Engine:
    name = "test-local"
    supports_vision = False

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls: list[dict[str, object]] = []

    def reply(self, prompt: str, **kwargs: object) -> LanguageReply:
        self.calls.append({"prompt": prompt, **kwargs})
        return LanguageReply(self.replies.pop(0), self.name, True)

    def release(self) -> None:
        return None


def _state(
    tmp_path: Path,
    *,
    engine: object | None = None,
    requires_gate: bool = False,
    steps: int = 1,
) -> tuple[Database, AutonomyRepository, AutonomyRun, LocalCognitiveActionLoop, object]:
    root = tmp_path / "project"
    root.mkdir(parents=True)
    executable = str(Path(sys.executable).resolve(strict=True))
    now = datetime.now(UTC)
    process_step = RunStep(
        step_id="run",
        capability=Capability.PROCESS_EXEC,
        action="execute",
        target=".",
        requires_human_gate=requires_gate,
        command=CommandSpec(
            executable=executable,
            argv=(executable, "-c", "print('ok')"),
            cwd=".",
            timeout_seconds=3,
        ),
    )
    plan_steps = tuple(
        replace(process_step, step_id=f"run{index}") for index in range(1, steps + 1)
    )
    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(root),
        grant=CapabilityGrant(
            capabilities=frozenset({Capability.PROCESS_EXEC}),
            issued_at=now,
            expires_at=now + timedelta(hours=1),
            max_steps=4,
            max_commands=4,
            max_retries=2,
            max_runtime_seconds=12,
            allowed_executables=(executable,),
        ),
        plan=RunPlan(objective="Inspect local Python project", steps=plan_steps),
    )
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    repository.create(run)
    repository.transition(run.run_id, "running", actor="owner", summary="start")
    selected_engine = engine or _Engine(['{"decision":"execute_next","step_id":"run1"}'])
    memories = MemoryRepository(database)
    loop = LocalCognitiveActionLoop(
        database,
        language_engine=selected_engine,  # type: ignore[arg-type]
        memory=TieredMemoryRepository(
            database, memories, MemoryLifecycleRepository(database, memories)
        ),
    )
    return database, repository, run, loop, selected_engine


def _cycle(loop: LocalCognitiveActionLoop, run: AutonomyRun) -> dict[str, object]:
    return loop.create_cycle(run.run_id, actor="owner")


def _fake_execution(repository: AutonomyRepository, outcome: ExecutionOutcome):
    def execute(executor, prepared, *, cancellation=None):
        receipt = repository._claim_execution_launch(
            prepared.request,
            actor="owner",
            runtime_seconds=prepared.reserved_runtime_seconds,
            retry=prepared.retry,
        )
        result = ExecutionResult(
            request_id=prepared.request.request_id,
            outcome=outcome,
            summary="untrusted output omitted from cognitive audit",
            exit_code=0 if outcome is ExecutionOutcome.SUCCEEDED else 7,
            error_code={
                ExecutionOutcome.SUCCEEDED: "",
                ExecutionOutcome.FAILED: "process_exit_nonzero",
                ExecutionOutcome.CANCELLED: "cancelled",
            }[outcome],
            stdout="secret stdout",
            stderr="secret stderr",
        )
        repository._record_execution_result(
            prepared.request, result, actor="owner", receipt=receipt
        )
        return result

    return execute


def test_schema_58_is_vault_only_idempotent_and_preserves_schema_57(tmp_path: Path) -> None:
    root = Database(tmp_path / "root.sqlite3", role="root")
    root.migrate()
    with root.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='assistant_cognitive_cycles'"
        ).fetchone() is None

    database, repository, run, _loop, _engine = _state(tmp_path / "state")
    with database.connect() as connection:
        connection.executescript(
            """
            DROP TABLE assistant_cognitive_cycle_events;
            DROP TABLE assistant_cognitive_turns;
            DROP TABLE assistant_cognitive_cycles;
            UPDATE schema_meta SET value='57' WHERE key='schema_version';
            """
        )
    database.migrate()
    database.migrate()
    assert repository.get(run.run_id) is not None
    with database.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "59"


def test_schema_constraints_immutability_and_append_only(tmp_path: Path) -> None:
    database, _repository, run, loop, _engine = _state(tmp_path)
    cycle = _cycle(loop, run)
    loop.advance(str(cycle["public_id"]), actor="owner")
    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute("UPDATE assistant_cognitive_cycles SET actor='intruder'")
    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute("UPDATE assistant_cognitive_turns SET state='abandoned'")
    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute("DELETE FROM assistant_cognitive_turns")
    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute("UPDATE assistant_cognitive_cycle_events SET summary_code='x'")
    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute("DELETE FROM assistant_cognitive_cycle_events")


def test_create_cycle_is_explicit_and_side_effect_free(tmp_path: Path) -> None:
    database, repository, run, loop, engine = _state(tmp_path)
    cycle = _cycle(loop, run)
    assert cycle["status"] == "ready"
    assert (
        cycle["max_advances"],
        cycle["max_model_calls"],
        cycle["max_replans"],
        cycle["max_actions"],
    ) == (12, 8, 2, 4)
    assert not engine.calls  # type: ignore[attr-defined]
    assert repository.execution_results(run.run_id, actor="owner") == []
    assert repository.execution_attempt_gaps(run.run_id, actor="owner") == []
    assert repository.get(run.run_id)["human_gates"] == []  # type: ignore[index]
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_turns"
        ).fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError):
        loop.create_cycle(run.run_id, actor="owner")


def test_create_cycle_revalidates_gaps_inside_atomic_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, repository, run, loop, engine = _state(tmp_path)
    gap_checks = 0
    bind_checks = 0
    original_bind = AutonomyExecutionBinding.bind

    def changing_gaps(run_id: str, *, actor: str) -> list[dict[str, str]]:
        nonlocal gap_checks
        gap_checks += 1
        if gap_checks == 1:
            return []
        return [{"step_id": "run1", "state": "reservation_unlaunched"}]

    def counted_bind(
        binding: AutonomyExecutionBinding,
        run_id: str,
        *,
        actor: str,
        cancellation=None,
    ):
        nonlocal bind_checks
        bind_checks += 1
        return original_bind(
            binding, run_id, actor=actor, cancellation=cancellation
        )

    monkeypatch.setattr(loop.autonomy, "execution_attempt_gaps", changing_gaps)
    monkeypatch.setattr(AutonomyExecutionBinding, "bind", counted_bind)
    with pytest.raises(PermissionError, match="intento incompleto"):
        loop.create_cycle(run.run_id, actor="owner")

    assert gap_checks == 2
    assert bind_checks == 2
    assert not engine.calls  # type: ignore[attr-defined]
    assert repository.execution_results(run.run_id, actor="owner") == []
    assert repository.get(run.run_id)["human_gates"] == []  # type: ignore[index]
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_cycles"
        ).fetchone()[0] == 0


def test_ordinary_ask_never_enters_cognitive_execution(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    assert app.cognitive_loop is None
    result = app.ask("Escribe algo no soportado")
    assert result.ok is True
    with app.database.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='assistant_cognitive_cycles'"
        ).fetchone() is None


def test_vault_application_cognitive_loop_uses_raw_no_model_engine(
    isolated_home: ElyndraPaths,
) -> None:
    root_app = ElyndraApplication.load(isolated_home)
    today = date.today()
    account = root_app.registry_accounts.register(
        username="cognitiva",
        email="cognitiva@example.test",
        password="clave9!segura",
        password_confirmation="clave9!segura",
        birth_date=date(today.year - 30, today.month, min(today.day, 28)).isoformat(),
        system_user=root_app.identity.system_user,
    )
    app = ElyndraApplication.load_for_account(str(account["public_id"]), isolated_home)
    assert app.cognitive_loop is not None
    assert app.cognitive_loop.language_engine is app.language_engine
    assert isinstance(app.cognitive_loop.language_engine, NoModelEngine)

    _database, repository, run, _loop, _engine = _state(
        isolated_home.state_dir / "app-run", engine=app.language_engine
    )
    loop = LocalCognitiveActionLoop(
        repository.database,
        language_engine=app.cognitive_loop.language_engine,
        memory=TieredMemoryRepository(
            repository.database,
            MemoryRepository(repository.database),
            MemoryLifecycleRepository(
                repository.database, MemoryRepository(repository.database)
            ),
        ),
    )
    cycle = loop.create_cycle(run.run_id, actor="owner")
    result = loop.advance(str(cycle["public_id"]), actor="owner")
    assert result.disposition == "model_unavailable"


def test_create_cycle_rejects_wrong_owner_nonrunning_gap_and_mixed_plan(tmp_path: Path) -> None:
    _database, repository, run, loop, _engine = _state(tmp_path / "base")
    with pytest.raises(PermissionError):
        loop.create_cycle(run.run_id, actor="intruder")
    prepared = AutonomyExecutionBinding(repository).bind(
        run.run_id, actor="owner"
    ).prepare("run1")
    assert prepared.request.request_id
    with pytest.raises(PermissionError, match="incompleto"):
        loop.create_cycle(run.run_id, actor="owner")

    database2, repository2, run2, loop2, _engine2 = _state(tmp_path / "mixed")
    with database2.connect() as connection:
        connection.execute("DROP TRIGGER trg_autonomy_runs_authority_immutable")
        connection.execute(
            """
            UPDATE assistant_autonomy_runs SET plan_json=json_set(
                plan_json, '$.steps[0].capability', 'workspace.read',
                '$.steps[0].command', NULL
            ) WHERE public_id=?
            """,
            (run2.run_id,),
        )
    with pytest.raises(PermissionError):
        loop2.create_cycle(run2.run_id, actor="owner")
    assert repository2.get(run2.run_id) is not None


def test_reason_persists_decision_and_never_executes_same_advance(tmp_path: Path) -> None:
    _database, repository, run, loop, engine = _state(tmp_path)
    cycle = _cycle(loop, run)
    result = loop.advance(str(cycle["public_id"]), actor="owner")
    assert result.status == "action_ready"
    assert result.decision == "execute_next"
    assert len(engine.calls) == 1  # type: ignore[attr-defined]
    assert repository.execution_results(run.run_id, actor="owner") == []


@pytest.mark.parametrize(
    "reply",
    (
        '{"decision":"complete"}',
        '{"decision":"execute_next","step_id":"wrong"}',
        '{"decision":"stop","command":["pytest"]}',
        "x" * 8193,
    ),
)
def test_model_output_fails_closed(reply: str, tmp_path: Path) -> None:
    _database, _repository, run, loop, _engine = _state(
        tmp_path, engine=_Engine([reply])
    )
    cycle = _cycle(loop, run)
    result = loop.advance(str(cycle["public_id"]), actor="owner")
    assert result.status == "waiting_owner"
    assert result.disposition == "malformed_model_output"


def test_duplicate_model_keys_fail_closed(tmp_path: Path) -> None:
    reply = '{"decision":"stop","decision":"execute_next","step_id":"run1"}'
    _database, _repository, run, loop, _engine = _state(
        tmp_path, engine=_Engine([reply])
    )
    cycle = _cycle(loop, run)
    result = loop.advance(str(cycle["public_id"]), actor="owner")
    assert result.status == "waiting_owner"
    assert result.disposition == "malformed_model_output"


def test_no_model_never_synthesizes_action(tmp_path: Path) -> None:
    _database, repository, run, loop, _engine = _state(
        tmp_path, engine=NoModelEngine()
    )
    cycle = _cycle(loop, run)
    result = loop.advance(str(cycle["public_id"]), actor="owner")
    assert result.disposition == "model_unavailable"
    assert result.status == "waiting_owner"
    assert repository.execution_results(run.run_id, actor="owner") == []


def test_cognitive_human_decision_creates_no_autonomy_gate(tmp_path: Path) -> None:
    _database, repository, run, loop, _engine = _state(
        tmp_path, engine=_Engine(['{"decision":"request_human"}'])
    )
    cycle = _cycle(loop, run)
    result = loop.advance(str(cycle["public_id"]), actor="owner")
    assert result.status == "waiting_owner"
    assert repository.get(run.run_id)["human_gates"] == []  # type: ignore[index]


def test_required_step_gate_is_trusted_and_does_not_consume_action_quota(
    tmp_path: Path,
) -> None:
    database, repository, run, loop, _engine = _state(tmp_path, requires_gate=True)
    cycle = _cycle(loop, run)
    loop.advance(str(cycle["public_id"]), actor="owner")
    result = loop.advance(str(cycle["public_id"]), actor="owner")
    assert result.status == "waiting_owner"
    assert result.disposition == "authority_blocked"
    item = repository.get(run.run_id)
    assert item is not None
    assert item["human_gates"][0]["kind"] == "approval"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_cycle_events "
            "WHERE event_type='action_delegated'"
        ).fetchone()[0] == 0


def test_cancel_denied_from_ready_does_not_mutate_run(tmp_path: Path) -> None:
    _database, repository, run, loop, _engine = _state(tmp_path)
    cycle_id = str(_cycle(loop, run)["public_id"])
    with pytest.raises(PermissionError):
        loop.cancel(cycle_id, actor="owner")
    assert loop.get(cycle_id, actor="owner")["status"] == "ready"  # type: ignore[index]
    assert repository.get(run.run_id)["status"] == "running"  # type: ignore[index]


def test_valid_waiting_owner_cancel_terminalizes_cycle_then_run(tmp_path: Path) -> None:
    database, repository, run, loop, _engine = _state(
        tmp_path, engine=_Engine(['{"decision":"request_human"}'])
    )
    cycle_id = str(_cycle(loop, run)["public_id"])
    loop.advance(cycle_id, actor="owner")
    with database.connect() as connection:
        wait_id = connection.execute(
            "SELECT public_id FROM assistant_cognitive_owner_waits"
        ).fetchone()[0]
    with pytest.raises(PermissionError):
        loop.cancel(cycle_id, actor="owner")
    assert loop.get(cycle_id, actor="owner")["status"] == "waiting_owner"  # type: ignore[index]
    assert repository.get(run.run_id)["status"] == "running"  # type: ignore[index]
    cancelled = loop.cancel_wait(wait_id, actor="owner")
    assert cancelled["status"] == "cancelled"
    assert repository.get(run.run_id)["status"] == "cancelled"  # type: ignore[index]


def test_pending_gate_cannot_resume_but_exact_approved_gate_can(
    tmp_path: Path,
) -> None:
    database, repository, run, loop, _engine = _state(tmp_path, requires_gate=True)
    cycle_id = str(_cycle(loop, run)["public_id"])
    loop.advance(cycle_id, actor="owner")
    loop.advance(cycle_id, actor="owner")
    item = repository.get(run.run_id)
    assert item is not None
    gate_id = str(item["human_gates"][0]["public_id"])
    with pytest.raises(PermissionError):
        loop.resume(cycle_id, actor="owner")
    with database.connect() as connection:
        wait_id = connection.execute(
            "SELECT public_id FROM assistant_cognitive_owner_waits "
            "WHERE cycle_id=(SELECT id FROM assistant_cognitive_cycles WHERE public_id=?)",
            (cycle_id,),
        ).fetchone()[0]
    with pytest.raises(PermissionError):
        loop.continue_after_ordinary_gate(wait_id, actor="owner")
    repository.resolve_human_gate(gate_id, actor="owner", decision="approved")
    assert loop.continue_after_ordinary_gate(wait_id, actor="owner")["status"] == "action_ready"


def test_retry_blocker_requires_exact_approved_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, repository, run, loop, _engine = _state(
        tmp_path,
        engine=_Engine(
            [
                '{"decision":"execute_next","step_id":"run1"}',
                '{"decision":"execute_next","step_id":"run1"}',
            ]
        ),
    )
    monkeypatch.setattr(BubblewrapExecutor, "__init__", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(
        BubblewrapExecutor,
        "execute",
        _fake_execution(repository, ExecutionOutcome.FAILED),
    )
    cycle_id = str(_cycle(loop, run)["public_id"])
    loop.advance(cycle_id, actor="owner")
    loop.advance(cycle_id, actor="owner")
    loop.advance(cycle_id, actor="owner")
    blocked = loop.advance(cycle_id, actor="owner")
    assert blocked.status == "waiting_owner"
    with pytest.raises(PermissionError):
        loop.resume(cycle_id, actor="owner")
    review = repository.request_retry_review(run.run_id, "run1", actor="owner")
    with database.connect() as connection:
        wait_id = connection.execute(
            "SELECT public_id FROM assistant_cognitive_owner_waits "
            "WHERE cycle_id=(SELECT id FROM assistant_cognitive_cycles WHERE public_id=?)",
            (cycle_id,),
        ).fetchone()[0]
    with pytest.raises(PermissionError):
        loop.continue_after_retry_review(
            wait_id,
            str(review["retry_review_id"]),
            str(review["gate_id"]),
            actor="owner",
        )
    repository.resolve_human_gate(
        str(review["gate_id"]), actor="owner", decision="approved"
    )
    with pytest.raises(PermissionError):
        loop.continue_after_retry_review(
            wait_id, "wrong-review", str(review["gate_id"]), actor="owner"
        )
    with pytest.raises(PermissionError):
        loop.continue_after_retry_review(
            wait_id, str(review["retry_review_id"]), "wrong-gate", actor="owner"
        )
    assert loop.continue_after_retry_review(
        wait_id,
        str(review["retry_review_id"]),
        str(review["gate_id"]),
        actor="owner",
    )["status"] == "action_ready"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_retry_consumptions"
        ).fetchone()[0] == 0


def test_retry_review_race_keeps_exact_retry_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, repository, run, loop, _engine = _state(
        tmp_path,
        engine=_Engine(
            [
                '{"decision":"execute_next","step_id":"run1"}',
                '{"decision":"execute_next","step_id":"run1"}',
            ]
        ),
    )
    monkeypatch.setattr(BubblewrapExecutor, "__init__", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(
        BubblewrapExecutor,
        "execute",
        _fake_execution(repository, ExecutionOutcome.FAILED),
    )
    cycle_id = str(_cycle(loop, run)["public_id"])
    loop.advance(cycle_id, actor="owner")
    failed = loop.advance(cycle_id, actor="owner")
    source_request_id = failed.source_request_id
    loop.advance(cycle_id, actor="owner")
    monkeypatch.setattr(loop.autonomy, "retry_review_available", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        SupervisedAutonomyRunner,
        "tick",
        lambda *args, **kwargs: SupervisedTickResult(
            run.run_id, SupervisedTickOutcome.BLOCKED_PREVIOUS_RESULT
        ),
    )
    blocked = loop.advance(cycle_id, actor="owner")
    assert blocked.disposition == "authority_blocked"
    with database.connect() as connection:
        wait = connection.execute(
            "SELECT reason, source_request_id FROM assistant_cognitive_owner_waits "
            "WHERE state='pending'"
        ).fetchone()
        assert tuple(wait) == ("retry_review_required", source_request_id)


def test_abandoned_act_before_delegation_may_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _repository, run, loop, _engine = _state(tmp_path)
    cycle_id = str(_cycle(loop, run)["public_id"])
    loop.advance(cycle_id, actor="owner")
    monkeypatch.setattr(
        loop,
        "_delegate_action",
        lambda *args: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    with pytest.raises(KeyboardInterrupt):
        loop.advance(cycle_id, actor="owner")
    item = loop.get(cycle_id, actor="owner")
    assert item is not None
    loop.abandon_turn(cycle_id, str(item["turns"][-1]["public_id"]), actor="owner")
    with database.connect() as connection:
        wait_id = connection.execute(
            "SELECT public_id FROM assistant_cognitive_owner_waits "
            "WHERE cycle_id=(SELECT id FROM assistant_cognitive_cycles WHERE public_id=?)",
            (cycle_id,),
        ).fetchone()[0]
    assert loop.continue_abandoned_action(wait_id, actor="owner")["status"] == "action_ready"


def test_abandoned_act_after_delegation_cannot_resume_or_delegate_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _repository, run, loop, _engine = _state(tmp_path)
    cycle_id = str(_cycle(loop, run)["public_id"])
    loop.advance(cycle_id, actor="owner")
    monkeypatch.setattr(
        SupervisedAutonomyRunner,
        "tick",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    with pytest.raises(KeyboardInterrupt):
        loop.advance(cycle_id, actor="owner")
    item = loop.get(cycle_id, actor="owner")
    assert item is not None
    loop.abandon_turn(cycle_id, str(item["turns"][-1]["public_id"]), actor="owner")
    with database.connect() as connection:
        wait_id = connection.execute(
            "SELECT public_id FROM assistant_cognitive_owner_waits "
            "WHERE cycle_id=(SELECT id FROM assistant_cognitive_cycles WHERE public_id=?)",
            (cycle_id,),
        ).fetchone()[0]
    with pytest.raises(PermissionError):
        loop.continue_abandoned_action(wait_id, actor="owner")
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_cycle_events "
            "WHERE event_type='action_delegated'"
        ).fetchone()[0] == 1


def test_action_uses_runner_exact_request_then_later_evaluates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, repository, run, loop, engine = _state(
        tmp_path,
        engine=_Engine(
            [
                '{"decision":"execute_next","step_id":"run1"}',
                '{"decision":"stop"}',
            ]
        ),
    )
    monkeypatch.setattr(BubblewrapExecutor, "__init__", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(
        BubblewrapExecutor,
        "execute",
        _fake_execution(repository, ExecutionOutcome.SUCCEEDED),
    )
    cycle = _cycle(loop, run)
    cycle_id = str(cycle["public_id"])
    assert loop.advance(cycle_id, actor="owner").status == "action_ready"
    action = loop.advance(cycle_id, actor="owner")
    assert action.status == "evaluation_ready"
    assert action.source_request_id
    assert loop.get(cycle_id, actor="owner")["usage"]["actions"] == 1  # type: ignore[index]
    assert len(engine.calls) == 1  # type: ignore[attr-defined]
    assert repository.execution_result(
        run.run_id, action.source_request_id, actor="owner"
    ) is not None
    evaluation = loop.advance(cycle_id, actor="owner")
    assert evaluation.status == "completed"
    assert evaluation.decision == ""
    assert evaluation.disposition == "durable_run_completed"
    assert len(engine.calls) == 1  # type: ignore[attr-defined]
    assert loop.get(cycle_id, actor="owner")["usage"]["model_calls"] == 2  # type: ignore[index]
    with database.connect() as connection:
        turn = connection.execute(
            "SELECT source_request_id FROM assistant_cognitive_turns "
            "WHERE kind='act'"
        ).fetchone()
        assert turn[0] == action.source_request_id
        stored = " ".join(
            str(value)
            for row in connection.execute(
                "SELECT summary_code, payload_json FROM assistant_cognitive_cycle_events"
            )
            for value in row
        )
    assert "secret stdout" not in stored
    assert "secret stderr" not in stored


def test_final_evaluation_completes_after_engine_switches_to_no_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database, repository, run, loop, engine = _state(tmp_path)
    monkeypatch.setattr(BubblewrapExecutor, "__init__", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(
        BubblewrapExecutor,
        "execute",
        _fake_execution(repository, ExecutionOutcome.SUCCEEDED),
    )
    cycle_id = str(_cycle(loop, run)["public_id"])
    loop.advance(cycle_id, actor="owner")
    loop.advance(cycle_id, actor="owner")
    loop.language_engine = NoModelEngine()
    evaluation = loop.advance(cycle_id, actor="owner")
    assert evaluation.status == "completed"
    assert evaluation.disposition == "durable_run_completed"
    assert len(engine.calls) == 1  # type: ignore[attr-defined]


def test_final_evaluation_never_calls_raising_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RaisingEngine(_Engine):
        def reply(self, prompt: str, **kwargs: object) -> LanguageReply:
            self.calls.append({"prompt": prompt, **kwargs})
            raise AssertionError("final durable evaluation must not call the model")

    _database, repository, run, loop, first_engine = _state(tmp_path)
    monkeypatch.setattr(BubblewrapExecutor, "__init__", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(
        BubblewrapExecutor,
        "execute",
        _fake_execution(repository, ExecutionOutcome.SUCCEEDED),
    )
    cycle_id = str(_cycle(loop, run)["public_id"])
    loop.advance(cycle_id, actor="owner")
    loop.advance(cycle_id, actor="owner")
    raising = RaisingEngine([])
    loop.language_engine = raising
    evaluation = loop.advance(cycle_id, actor="owner")
    assert evaluation.status == "completed"
    assert evaluation.disposition == "durable_run_completed"
    assert len(first_engine.calls) == 1  # type: ignore[attr-defined]
    assert raising.calls == []


def test_successful_nonfinal_evaluation_advances_to_next_unexecuted_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database, repository, run, loop, engine = _state(
        tmp_path,
        steps=2,
        engine=_Engine(
            [
                '{"decision":"execute_next","step_id":"run1"}',
                '{"decision":"execute_next","step_id":"run2"}',
            ]
        ),
    )
    monkeypatch.setattr(BubblewrapExecutor, "__init__", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(
        BubblewrapExecutor,
        "execute",
        _fake_execution(repository, ExecutionOutcome.SUCCEEDED),
    )
    cycle = _cycle(loop, run)
    cycle_id = str(cycle["public_id"])
    loop.advance(cycle_id, actor="owner")
    action = loop.advance(cycle_id, actor="owner")
    evaluation = loop.advance(cycle_id, actor="owner")
    assert evaluation.status == "action_ready"
    assert evaluation.step_id == "run2"
    assert evaluation.source_request_id == action.source_request_id
    assert "run2" in engine.calls[-1]["prompt"]  # type: ignore[index]
    assert len(engine.calls) == 2  # type: ignore[attr-defined]


@pytest.mark.parametrize("outcome", (ExecutionOutcome.FAILED, ExecutionOutcome.CANCELLED))
def test_failed_or_cancelled_observation_evaluates_same_incomplete_step(
    outcome: ExecutionOutcome,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database, repository, run, loop, _engine = _state(
        tmp_path,
        engine=_Engine(
            [
                '{"decision":"execute_next","step_id":"run1"}',
                '{"decision":"execute_next","step_id":"run1"}',
            ]
        ),
    )
    monkeypatch.setattr(BubblewrapExecutor, "__init__", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(BubblewrapExecutor, "execute", _fake_execution(repository, outcome))
    cycle_id = str(_cycle(loop, run)["public_id"])
    loop.advance(cycle_id, actor="owner")
    action = loop.advance(cycle_id, actor="owner")
    evaluation = loop.advance(cycle_id, actor="owner")
    assert evaluation.status == "action_ready"
    assert evaluation.step_id == "run1"
    assert evaluation.source_request_id == action.source_request_id
    assert evaluation.disposition != "durable_run_completed"
    assert len(_engine.calls) == 2  # type: ignore[attr-defined]


def test_failed_action_remains_durable_truth_and_retry_is_not_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, repository, run, loop, _engine = _state(tmp_path)
    monkeypatch.setattr(BubblewrapExecutor, "__init__", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(
        BubblewrapExecutor,
        "execute",
        _fake_execution(repository, ExecutionOutcome.FAILED),
    )
    cycle = _cycle(loop, run)
    cycle_id = str(cycle["public_id"])
    loop.advance(cycle_id, actor="owner")
    action = loop.advance(cycle_id, actor="owner")
    exact = repository.execution_result(run.run_id, action.source_request_id, actor="owner")
    assert exact is not None and exact["outcome"] == "failed"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_retry_reviews"
        ).fetchone()[0] == 0


def test_abandoned_turn_is_counted_and_cannot_be_reused(tmp_path: Path) -> None:
    class CrashEngine(_Engine):
        def reply(self, prompt: str, **kwargs: object) -> LanguageReply:
            self.calls.append({"prompt": prompt, **kwargs})
            raise KeyboardInterrupt

    _database, _repository, run, loop, _engine = _state(
        tmp_path, engine=CrashEngine([])
    )
    cycle = _cycle(loop, run)
    cycle_id = str(cycle["public_id"])
    with pytest.raises(KeyboardInterrupt):
        loop.advance(cycle_id, actor="owner")
    item = loop.get(cycle_id, actor="owner")
    assert item is not None and item["usage"]["model_calls"] == 1
    turn_id = str(item["turns"][0]["public_id"])
    loop.abandon_turn(cycle_id, turn_id, actor="owner")
    with pytest.raises(ValueError):
        loop.abandon_turn(cycle_id, turn_id, actor="owner")
    assert loop.get(cycle_id, actor="owner")["usage"]["advances"] == 1  # type: ignore[index]


def test_concurrent_reason_advances_reserve_one_turn(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingEngine(_Engine):
        def reply(self, prompt: str, **kwargs: object) -> LanguageReply:
            self.calls.append({"prompt": prompt, **kwargs})
            entered.set()
            assert release.wait(timeout=5)
            return LanguageReply(
                '{"decision":"execute_next","step_id":"run1"}', self.name, True
            )

    engine = BlockingEngine([])
    database, _repository, run, loop, _engine = _state(tmp_path, engine=engine)
    cycle = _cycle(loop, run)
    cycle_id = str(cycle["public_id"])
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(loop.advance, cycle_id, actor="owner")
        assert entered.wait(timeout=5)
        second = executor.submit(loop.advance, cycle_id, actor="owner")
        with pytest.raises(PermissionError):
            second.result(timeout=5)
        release.set()
        assert first.result(timeout=5).status == "action_ready"
    assert len(engine.calls) == 1
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_turns"
        ).fetchone()[0] == 1


def test_concurrent_action_advances_delegate_at_most_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    database, repository, run, loop, _engine = _state(tmp_path)
    persist = _fake_execution(repository, ExecutionOutcome.SUCCEEDED)

    def blocking_execute(executor, prepared, *, cancellation=None):
        entered.set()
        assert release.wait(timeout=5)
        return persist(executor, prepared, cancellation=cancellation)

    monkeypatch.setattr(BubblewrapExecutor, "__init__", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(BubblewrapExecutor, "execute", blocking_execute)
    cycle = _cycle(loop, run)
    cycle_id = str(cycle["public_id"])
    loop.advance(cycle_id, actor="owner")
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(loop.advance, cycle_id, actor="owner")
        assert entered.wait(timeout=5)
        second = executor.submit(loop.advance, cycle_id, actor="owner")
        with pytest.raises(PermissionError):
            second.result(timeout=5)
        release.set()
        assert first.result(timeout=5).source_request_id
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_cycle_events "
            "WHERE event_type='action_delegated'"
        ).fetchone()[0] == 1


def test_max_advances_and_model_calls_deny_before_model_invocation(tmp_path: Path) -> None:
    for label, count in (
        ("advances", 12),
        ("models", 8),
    ):
        database, _repository, run, loop, engine = _state(tmp_path / label)
        cycle = _cycle(loop, run)
        with database.connect() as connection:
            cycle_db_id = connection.execute(
                "SELECT id FROM assistant_cognitive_cycles WHERE public_id=?",
                (cycle["public_id"],),
            ).fetchone()[0]
            for sequence in range(1, count + 1):
                connection.execute(
                    """
                    INSERT INTO assistant_cognitive_turns(
                        public_id, cycle_id, sequence, kind, state,
                        created_at, abandoned_at
                    ) VALUES (?, ?, ?, 'reason', 'abandoned', 'now', 'now')
                    """,
                    (f"{label}-{sequence}", cycle_db_id, sequence),
                )
        denied = loop.advance(str(cycle["public_id"]), actor="owner")
        assert denied.status == "waiting_owner"
        assert denied.disposition == "limit_exhausted"
        assert len(engine.calls) == 0  # type: ignore[attr-defined]


def test_max_replans_denies_third_durable_replan(tmp_path: Path) -> None:
    database, _repository, run, loop, engine = _state(
        tmp_path, engine=_Engine(['{"decision":"propose_replan"}'])
    )
    cycle = _cycle(loop, run)
    with database.connect() as connection:
        cycle_db_id = connection.execute(
            "SELECT id FROM assistant_cognitive_cycles WHERE public_id=?",
            (cycle["public_id"],),
        ).fetchone()[0]
        for sequence in (1, 2):
            connection.execute(
                """
                INSERT INTO assistant_cognitive_turns(
                    public_id, cycle_id, sequence, kind, state, decision,
                    step_id, created_at, completed_at
                ) VALUES (?, ?, ?, 'reason', 'completed', 'propose_replan',
                          'run1', 'now', 'now')
                """,
                (f"replan-{sequence}", cycle_db_id, sequence),
            )
    result = loop.advance(str(cycle["public_id"]), actor="owner")
    assert result.status == "waiting_owner"
    assert result.decision == ""
    assert result.disposition == "limit_exhausted"
    assert len(engine.calls) == 1  # type: ignore[attr-defined]
    assert loop.get(str(cycle["public_id"]), actor="owner")["usage"]["replans"] == 2  # type: ignore[index]


def test_max_actions_uses_delegation_events_and_never_calls_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _repository, run, loop, _engine = _state(tmp_path)
    cycle = _cycle(loop, run)
    cycle_id = str(cycle["public_id"])
    loop.advance(cycle_id, actor="owner")
    with database.connect() as connection:
        cycle_db_id = connection.execute(
            "SELECT id FROM assistant_cognitive_cycles WHERE public_id=?",
            (cycle_id,),
        ).fetchone()[0]
        start = connection.execute(
            "SELECT MAX(sequence) FROM assistant_cognitive_cycle_events WHERE cycle_id=?",
            (cycle_db_id,),
        ).fetchone()[0]
        for offset in range(1, 5):
            connection.execute(
                """
                INSERT INTO assistant_cognitive_cycle_events(
                    public_id, cycle_id, sequence, event_type, from_status,
                    to_status, summary_code, payload_json, created_at
                ) VALUES (?, ?, ?, 'action_delegated', 'action_reserved',
                          'action_reserved', 'action_delegated', '{}', 'now')
                """,
                (f"delegated-{offset}", cycle_db_id, start + offset),
            )
    runner_calls = 0

    def forbidden_tick(*args: object, **kwargs: object) -> object:
        nonlocal runner_calls
        runner_calls += 1
        pytest.fail("runner no debe invocarse")

    monkeypatch.setattr(
        SupervisedAutonomyRunner,
        "tick",
        forbidden_tick,
    )
    denied = loop.advance(cycle_id, actor="owner")
    assert denied.status == "waiting_owner"
    assert denied.disposition == "limit_exhausted"
    assert loop.get(cycle_id, actor="owner")["usage"]["actions"] == 4  # type: ignore[index]
    with pytest.raises(PermissionError):
        loop.resume(cycle_id, actor="owner")
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_cycle_events "
            "WHERE event_type='action_delegated'"
        ).fetchone()[0] == 4
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_execution_results"
        ).fetchone()[0] == 0
    assert runner_calls == 0


def test_turn_bounds_foreign_keys_and_single_reserved_turn(tmp_path: Path) -> None:
    database, _repository, run, loop, _engine = _state(tmp_path)
    cycle = _cycle(loop, run)
    with database.connect() as connection:
        cycle_db_id = connection.execute(
            "SELECT id FROM assistant_cognitive_cycles WHERE public_id=?",
            (cycle["public_id"],),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO assistant_cognitive_turns(
                public_id, cycle_id, sequence, kind, state, decision,
                disposition, step_id, source_request_id, created_at,
                completed_at, abandoned_at
            ) VALUES ('reserved-one', ?, 1, 'reason', 'reserved', NULL,
                      NULL, 'run1', NULL, 'now', NULL, NULL)
            """,
            (cycle_db_id,),
        )
    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute(
            """
            INSERT INTO assistant_cognitive_turns(
                public_id, cycle_id, sequence, kind, state, created_at
            ) VALUES ('reserved-two', ?, 2, 'reason', 'reserved', 'now')
            """,
            (cycle_db_id,),
        )
    with pytest.raises(sqlite3.IntegrityError), database.connect() as connection:
        connection.execute(
            """
            INSERT INTO assistant_cognitive_turns(
                public_id, cycle_id, sequence, kind, state, created_at
            ) VALUES ('missing-cycle', 999999, 1, 'reason', 'reserved', 'now')
            """
        )


def test_reserved_act_source_cannot_be_preloaded_or_mutated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, repository, run, loop, _engine = _state(tmp_path)
    monkeypatch.setattr(BubblewrapExecutor, "__init__", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(
        BubblewrapExecutor,
        "execute",
        _fake_execution(repository, ExecutionOutcome.SUCCEEDED),
    )
    first_cycle = _cycle(loop, run)
    first_id = str(first_cycle["public_id"])
    loop.advance(first_id, actor="owner")
    action = loop.advance(first_id, actor="owner")
    with database.connect() as connection:
        cycle_db_id = connection.execute(
            "SELECT id FROM assistant_cognitive_cycles WHERE public_id=?",
            (first_id,),
        ).fetchone()[0]
        next_sequence = connection.execute(
            "SELECT MAX(sequence)+1 FROM assistant_cognitive_turns WHERE cycle_id=?",
            (cycle_db_id,),
        ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="source_requires_resolution"), (
        database.connect()
    ) as connection:
        connection.execute(
            """
            INSERT INTO assistant_cognitive_turns(
                public_id, cycle_id, sequence, kind, state, source_request_id, created_at
            ) VALUES ('preloaded-act', ?, ?, 'act', 'reserved', ?, 'now')
            """,
            (cycle_db_id, next_sequence, action.source_request_id),
        )
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO assistant_cognitive_turns(
                public_id, cycle_id, sequence, kind, state, created_at
            ) VALUES ('empty-act', ?, ?, 'act', 'reserved', 'now')
            """,
            (cycle_db_id, next_sequence),
        )
    with pytest.raises(sqlite3.IntegrityError, match="source_immutable"), (
        database.connect()
    ) as connection:
        connection.execute(
            "UPDATE assistant_cognitive_turns SET source_request_id=? "
            "WHERE public_id='empty-act'",
            (action.source_request_id,),
        )
