from __future__ import annotations

import sqlite3
import sys
import threading
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
    RunPlan,
    RunStep,
    WorkspaceScope,
)
from elyndra.cognitive_loop import LocalCognitiveActionLoop
from elyndra.db import Database
from elyndra.engines import LanguageReply, NoModelEngine


class _Recall:
    items: list[dict[str, str]] = []


class _Memory:
    def recall(self, *args: object, **kwargs: object) -> _Recall:
        return _Recall()


class _Engine:
    name = "owner-continuation-test"
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
    tmp_path: Path, engine: object, *, requires_gate: bool = False
) -> tuple[Database, AutonomyRepository, AutonomyRun, LocalCognitiveActionLoop, str]:
    root = tmp_path / "project"
    root.mkdir(parents=True)
    executable = str(Path(sys.executable).resolve(strict=True))
    now = datetime.now(UTC)
    run = AutonomyRun(
        actor="owner",
        workspace=WorkspaceScope.from_root(root),
        grant=CapabilityGrant(
            capabilities=frozenset({Capability.PROCESS_EXEC}),
            issued_at=now,
            expires_at=now + timedelta(hours=1),
            max_steps=4,
            max_retries=2,
            max_commands=4,
            max_runtime_seconds=12,
            allowed_executables=(executable,),
        ),
        plan=RunPlan(
            objective="Inspect local project",
            steps=(
                RunStep(
                    step_id="run",
                    capability=Capability.PROCESS_EXEC,
                    action="run",
                    target=".",
                    requires_human_gate=requires_gate,
                    command=CommandSpec(
                        executable=executable,
                        argv=(executable, "-c", "print('ok')"),
                        cwd=".",
                        timeout_seconds=3,
                    ),
                ),
            ),
        ),
    )
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    database.migrate()
    repository = AutonomyRepository(database)
    repository.create(run)
    repository.transition(run.run_id, "running", actor="owner", summary="start")
    loop = LocalCognitiveActionLoop(
        database,
        language_engine=engine,  # type: ignore[arg-type]
        memory=_Memory(),  # type: ignore[arg-type]
    )
    cycle = loop.create_cycle(run.run_id, actor="owner")
    return database, repository, run, loop, str(cycle["public_id"])


def _wait(database: Database) -> sqlite3.Row:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM assistant_cognitive_owner_waits ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    return row


@pytest.mark.parametrize(
    ("reply", "reason"),
    (
        ('{"decision":"request_human"}', "model_request_human"),
        ('{"decision":"insufficient_evidence"}', "insufficient_evidence"),
        ('{"decision":"propose_replan"}', "replan_requested"),
    ),
)
def test_live_model_decisions_create_exact_typed_wait(
    tmp_path: Path, reply: str, reason: str
) -> None:
    database, _repository, _run, loop, cycle_id = _state(tmp_path, _Engine([reply]))
    result = loop.advance(cycle_id, actor="owner")
    assert result.status == "waiting_owner"
    wait = _wait(database)
    assert wait["reason"] == reason
    assert wait["source_turn_id"] is not None
    assert wait["source_request_id"] is None


@pytest.mark.parametrize(
    ("engine", "reason"),
    (
        (NoModelEngine(), "model_unavailable"),
        (_Engine([]), "model_error"),
        (_Engine(["not-json"]), "malformed_model_output"),
    ),
)
def test_live_model_dispositions_create_exact_typed_wait(
    tmp_path: Path, engine: object, reason: str
) -> None:
    database, _repository, _run, loop, cycle_id = _state(tmp_path, engine)
    result = loop.advance(cycle_id, actor="owner")
    assert result.status == "waiting_owner"
    assert _wait(database)["reason"] == reason


def test_context_continuation_is_one_shot_and_not_in_events(tmp_path: Path) -> None:
    engine = _Engine(
        [
            '{"decision":"request_human"}',
            '{"decision":"request_human"}',
            '{"decision":"execute_next","step_id":"run"}',
        ]
    )
    database, _repository, _run, loop, cycle_id = _state(tmp_path, engine)
    loop.advance(cycle_id, actor="owner")
    wait_id = str(_wait(database)["public_id"])
    with pytest.raises(ValueError):
        loop.continue_with_context(wait_id, actor="owner", context="")
    with pytest.raises(ValueError):
        loop.continue_with_context(wait_id, actor="owner", context="á" * 1001)
    resumed = loop.continue_with_context(
        wait_id, actor="owner", context="evidence supplied once"
    )
    assert resumed["status"] == "ready"
    loop.advance(cycle_id, actor="owner")
    assert any("evidence supplied once" in item for item in engine.calls[1]["context"])
    with database.connect() as connection:
        wait = connection.execute(
            "SELECT resumed_turn_id FROM assistant_cognitive_owner_waits WHERE public_id=?",
            (wait_id,),
        ).fetchone()
        assert wait["resumed_turn_id"] is not None
        events = "".join(
            str(row[0])
            for row in connection.execute(
                "SELECT payload_json FROM assistant_cognitive_cycle_events"
            )
        )
        assert "evidence supplied once" not in events
    with pytest.raises(PermissionError):
        loop.continue_with_context(wait_id, actor="owner", context="again")
    second_wait_id = str(_wait(database)["public_id"])
    loop.retry_reasoning(second_wait_id, actor="owner")
    loop.advance(cycle_id, actor="owner")
    assert not any(
        "evidence supplied once" in item for item in engine.calls[2]["context"]
    )


def test_context_claim_rolls_back_with_failed_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _Engine(['{"decision":"request_human"}'])
    database, _repository, _run, loop, cycle_id = _state(tmp_path, engine)
    loop.advance(cycle_id, actor="owner")
    wait_id = str(_wait(database)["public_id"])
    loop.continue_with_context(wait_id, actor="owner", context="one shot")
    monkeypatch.setattr(
        loop,
        "_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected")),
    )
    with pytest.raises(RuntimeError, match="injected"):
        loop.advance(cycle_id, actor="owner")
    with database.connect() as connection:
        wait = connection.execute(
            "SELECT resumed_turn_id FROM assistant_cognitive_owner_waits WHERE public_id=?",
            (wait_id,),
        ).fetchone()
        assert wait["resumed_turn_id"] is None
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_turns WHERE cycle_id="
            "(SELECT id FROM assistant_cognitive_cycles WHERE public_id=?)",
            (cycle_id,),
        ).fetchone()[0] == 1


def test_context_stays_bound_after_model_crash_and_abandonment(tmp_path: Path) -> None:
    class _CrashEngine(_Engine):
        def reply(self, prompt: str, **kwargs: object) -> LanguageReply:
            self.calls.append({"prompt": prompt, **kwargs})
            raise KeyboardInterrupt

    engine = _CrashEngine(['{"decision":"request_human"}'])
    database, _repository, _run, loop, cycle_id = _state(tmp_path, engine)
    # Use a normal first response, then make the resumed invocation crash.
    engine.reply = _Engine.reply.__get__(engine, _CrashEngine)
    loop.advance(cycle_id, actor="owner")
    wait_id = str(_wait(database)["public_id"])
    loop.continue_with_context(wait_id, actor="owner", context="bound forever")
    engine.reply = _CrashEngine.reply.__get__(engine, _CrashEngine)
    with pytest.raises(KeyboardInterrupt):
        loop.advance(cycle_id, actor="owner")
    with database.connect() as connection:
        resumed_turn_id = connection.execute(
            "SELECT resumed_turn_id FROM assistant_cognitive_owner_waits WHERE public_id=?",
            (wait_id,),
        ).fetchone()[0]
        turn_public_id = connection.execute(
            "SELECT public_id FROM assistant_cognitive_turns WHERE id=?",
            (resumed_turn_id,),
        ).fetchone()[0]
    loop.abandon_turn(cycle_id, turn_public_id, actor="owner")
    with database.connect() as connection:
        assert connection.execute(
            "SELECT resumed_turn_id FROM assistant_cognitive_owner_waits WHERE public_id=?",
            (wait_id,),
        ).fetchone()[0] == resumed_turn_id


def test_owner_context_participates_in_total_context_budget(tmp_path: Path) -> None:
    class _FullMemory:
        def recall(self, *args: object, **kwargs: object) -> _Recall:
            result = _Recall()
            result.items = [{"content": "m" * 2_000} for _ in range(8)]
            return result

    engine = _Engine(
        ['{"decision":"request_human"}', '{"decision":"execute_next","step_id":"run"}']
    )
    database, _repository, _run, loop, cycle_id = _state(tmp_path, engine)
    loop.advance(cycle_id, actor="owner")
    loop.continue_with_context(
        str(_wait(database)["public_id"]), actor="owner", context="o" * 2_000
    )
    loop.memory = _FullMemory()  # type: ignore[assignment]
    loop.advance(cycle_id, actor="owner")
    context = engine.calls[1]["context"]
    assert len(context) <= 8
    assert sum(len(str(item).encode("utf-8")) for item in context) <= 8_000
    assert "o" * 2_000 in context[0]


def test_concurrent_advances_claim_owner_context_once(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class _BlockingEngine(_Engine):
        def reply(self, prompt: str, **kwargs: object) -> LanguageReply:
            self.calls.append({"prompt": prompt, **kwargs})
            if len(self.calls) == 1:
                return LanguageReply(
                    '{"decision":"request_human"}', self.name, True
                )
            entered.set()
            assert release.wait(5)
            return LanguageReply(
                '{"decision":"execute_next","step_id":"run"}', self.name, True
            )

    engine = _BlockingEngine([])
    database, _repository, _run, loop, cycle_id = _state(tmp_path, engine)
    loop.advance(cycle_id, actor="owner")
    wait_id = str(_wait(database)["public_id"])
    loop.continue_with_context(wait_id, actor="owner", context="single claimant")
    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(loop.advance, cycle_id, actor="owner")
        assert entered.wait(5)
        loser = pool.submit(loop.advance, cycle_id, actor="owner")
        with pytest.raises(PermissionError):
            loser.result(timeout=5)
        release.set()
        assert winner.result(timeout=5).status == "action_ready"
    with database.connect() as connection:
        resumed_turn_id = connection.execute(
            "SELECT resumed_turn_id FROM assistant_cognitive_owner_waits WHERE public_id=?",
            (wait_id,),
        ).fetchone()[0]
        assert resumed_turn_id is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_turns WHERE id=?",
            (resumed_turn_id,),
        ).fetchone()[0] == 1


def test_retry_reasoning_and_replan_decline_derive_target_without_model_call(
    tmp_path: Path,
) -> None:
    engine = _Engine(['{"decision":"request_human"}'])
    database, _repository, _run, loop, cycle_id = _state(tmp_path, engine)
    loop.advance(cycle_id, actor="owner")
    wait_id = str(_wait(database)["public_id"])
    loop.retry_reasoning(wait_id, actor="owner")
    assert len(engine.calls) == 1
    assert loop.get(cycle_id, actor="owner")["status"] == "ready"  # type: ignore[index]

    other_engine = _Engine(['{"decision":"propose_replan"}'])
    other_db, _repo, _run_item, other_loop, other_cycle = _state(
        tmp_path / "replan", other_engine
    )
    other_loop.advance(other_cycle, actor="owner")
    before = _repo.get(_run_item.run_id)
    other_loop.continue_without_replan(
        str(_wait(other_db)["public_id"]), actor="owner"
    )
    after = _repo.get(_run_item.run_id)
    assert before is not None and after is not None
    assert before["plan"] == after["plan"] and after["status"] == "running"


def test_retry_reasoning_nomodel_denies_without_mutation(tmp_path: Path) -> None:
    database, _repository, _run, loop, _cycle_id = _state(tmp_path, NoModelEngine())
    loop.advance(_cycle_id, actor="owner")
    wait_id = str(_wait(database)["public_id"])
    with pytest.raises(PermissionError):
        loop.retry_reasoning(wait_id, actor="owner")
    assert _wait(database)["state"] == "pending"


def test_stop_and_cancel_exact_wait_semantics(tmp_path: Path) -> None:
    stop_db, stop_repo, stop_run, stop_loop, stop_cycle = _state(
        tmp_path / "stop", _Engine(['{"decision":"request_human"}'])
    )
    stop_loop.advance(stop_cycle, actor="owner")
    stop_loop.stop_wait(str(_wait(stop_db)["public_id"]), actor="owner")
    assert stop_loop.get(stop_cycle, actor="owner")["status"] == "stopped"  # type: ignore[index]
    assert stop_repo.get(stop_run.run_id)["status"] == "running"  # type: ignore[index]

    cancel_db, cancel_repo, cancel_run, cancel_loop, cancel_cycle = _state(
        tmp_path / "cancel", _Engine(['{"decision":"request_human"}'])
    )
    cancel_loop.advance(cancel_cycle, actor="owner")
    cancel_wait = str(_wait(cancel_db)["public_id"])
    cancel_loop.cancel_wait(cancel_wait, actor="owner")
    assert cancel_loop.get(cancel_cycle, actor="owner")["status"] == "cancelled"  # type: ignore[index]
    assert cancel_repo.get(cancel_run.run_id)["status"] == "cancelled"  # type: ignore[index]
    with pytest.raises(PermissionError):
        cancel_loop.cancel_wait(cancel_wait, actor="owner")


def test_legacy_owner_operations_cannot_bypass_pending_typed_wait(tmp_path: Path) -> None:
    database, repository, run, loop, cycle_id = _state(
        tmp_path, _Engine(['{"decision":"request_human"}'])
    )
    loop.advance(cycle_id, actor="owner")
    wait_id = str(_wait(database)["public_id"])
    with database.connect() as connection:
        before_events = connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_cycle_events"
        ).fetchone()[0]
    for operation in (loop.resume, loop.stop, loop.cancel):
        with pytest.raises(PermissionError):
            operation(cycle_id, actor="owner")
    assert _wait(database)["state"] == "pending"
    assert loop.get(cycle_id, actor="owner")["status"] == "waiting_owner"  # type: ignore[index]
    assert repository.get(run.run_id)["status"] == "running"  # type: ignore[index]
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_cognitive_cycle_events"
        ).fetchone()[0] == before_events
    loop.stop_wait(wait_id, actor="owner")


def test_waiting_human_gate_cancel_is_one_atomic_owner_operation(tmp_path: Path) -> None:
    database, repository, run, loop, cycle_id = _state(
        tmp_path,
        _Engine(['{"decision":"execute_next","step_id":"run"}']),
        requires_gate=True,
    )
    loop.advance(cycle_id, actor="owner")
    loop.advance(cycle_id, actor="owner")
    wait_id = str(_wait(database)["public_id"])
    assert repository.get(run.run_id)["status"] == "waiting_human"  # type: ignore[index]
    loop.cancel_wait(wait_id, actor="owner")
    item = repository.get(run.run_id)
    assert item is not None and item["status"] == "cancelled"
    assert item["human_gates"][0]["status"] == "cancelled"
    assert loop.get(cycle_id, actor="owner")["status"] == "cancelled"  # type: ignore[index]


def test_waiting_human_cancel_failure_rolls_back_every_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, repository, run, loop, cycle_id = _state(
        tmp_path,
        _Engine(['{"decision":"execute_next","step_id":"run"}']),
        requires_gate=True,
    )
    loop.advance(cycle_id, actor="owner")
    loop.advance(cycle_id, actor="owner")
    wait_id = str(_wait(database)["public_id"])
    monkeypatch.setattr(
        loop.autonomy,
        "_cancel_run_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected")),
    )
    with pytest.raises(RuntimeError, match="injected"):
        loop.cancel_wait(wait_id, actor="owner")
    assert _wait(database)["state"] == "pending"
    assert loop.get(cycle_id, actor="owner")["status"] == "waiting_owner"  # type: ignore[index]
    item = repository.get(run.run_id)
    assert item is not None and item["status"] == "waiting_human"
    assert item["human_gates"][0]["status"] == "pending"


def test_continuation_revalidates_running_gap_free_authority(tmp_path: Path) -> None:
    database, _repository, _run, loop, cycle_id = _state(
        tmp_path, _Engine(['{"decision":"request_human"}'])
    )
    loop.advance(cycle_id, actor="owner")
    wait_id = str(_wait(database)["public_id"])
    with database.connect() as connection:
        run_db_id = connection.execute(
            "SELECT autonomy_run_id FROM assistant_cognitive_cycles WHERE public_id=?",
            (cycle_id,),
        ).fetchone()[0]
        digest = "a" * 64
        connection.execute(
            """INSERT INTO assistant_autonomy_execution_reservations(
                   request_id, request_sha256, run_id, sequence, step_id, capability,
                   runtime_seconds, is_retry, created_at, command_sha256)
               VALUES ('gap-request', ?, ?, 1, 'run', 'process.exec', 1, 0, 'now', ?)""",
            (digest, run_db_id, digest),
        )
    with pytest.raises(PermissionError, match="incompleto"):
        loop.retry_reasoning(wait_id, actor="owner")
    assert _wait(database)["state"] == "pending"


def test_cancel_wait_cannot_rewrite_terminal_predecessor(tmp_path: Path) -> None:
    database, repository, run, loop, cycle_id = _state(
        tmp_path, _Engine(['{"decision":"request_human"}'])
    )
    loop.advance(cycle_id, actor="owner")
    wait_id = str(_wait(database)["public_id"])
    repository.transition(run.run_id, "cancelled", actor="owner", summary="external cancel")
    with pytest.raises(PermissionError):
        loop.cancel_wait(wait_id, actor="owner")
    assert _wait(database)["state"] == "pending"
    assert loop.get(cycle_id, actor="owner")["status"] == "waiting_owner"  # type: ignore[index]
    assert repository.get(run.run_id)["status"] == "cancelled"  # type: ignore[index]


def test_pre_reservation_limits_create_wait_without_new_turn_or_call(tmp_path: Path) -> None:
    engine = _Engine(['{"decision":"execute_next","step_id":"run"}'])
    database, _repository, _run, loop, cycle_id = _state(tmp_path, engine)
    with database.connect() as connection:
        cycle_db_id = connection.execute(
            "SELECT id FROM assistant_cognitive_cycles WHERE public_id=?", (cycle_id,)
        ).fetchone()[0]
        for sequence in range(1, 13):
            connection.execute(
                """INSERT INTO assistant_cognitive_turns(
                       public_id, cycle_id, sequence, kind, state, created_at, abandoned_at)
                   VALUES (?, ?, ?, 'reason', 'abandoned', 'now', 'now')""",
                (f"limit-{sequence}", cycle_db_id, sequence),
            )
    result = loop.advance(cycle_id, actor="owner")
    assert result.disposition == "limit_exhausted"
    assert len(engine.calls) == 0
    assert _wait(database)["reason"] == "limit_exhausted"


def test_post_model_replan_limit_creates_terminal_only_wait(tmp_path: Path) -> None:
    engine = _Engine(['{"decision":"propose_replan"}'])
    database, _repository, _run, loop, cycle_id = _state(tmp_path, engine)
    with database.connect() as connection:
        cycle_db_id = connection.execute(
            "SELECT id FROM assistant_cognitive_cycles WHERE public_id=?", (cycle_id,)
        ).fetchone()[0]
        for sequence in (1, 2):
            connection.execute(
                """INSERT INTO assistant_cognitive_turns(
                       public_id, cycle_id, sequence, kind, state, decision, step_id,
                       created_at, completed_at)
                   VALUES (?, ?, ?, 'reason', 'completed', 'propose_replan', 'run',
                           'now', 'now')""",
                (f"replan-{sequence}", cycle_db_id, sequence),
            )
    result = loop.advance(cycle_id, actor="owner")
    assert result.decision == "" and result.disposition == "limit_exhausted"
    wait = _wait(database)
    assert wait["reason"] == "limit_exhausted" and wait["source_turn_id"] is not None
    with pytest.raises(PermissionError):
        loop.retry_reasoning(str(wait["public_id"]), actor="owner")
