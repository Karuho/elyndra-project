from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import elyndra.autonomy.repository as autonomy_repository
from elyndra.autonomy import (
    AutonomyRepository,
    AutonomyRun,
    Capability,
    CapabilityGrant,
    MutationItem,
    MutationOperation,
    MutationProposal,
    RunPlan,
    RunStep,
    WorkspaceScope,
)
from elyndra.db import Database


def _database(path: Path, *, role: str = "vault") -> Database:
    database = Database(path, role=role)
    database.migrate()
    return database


def _run(
    tmp_path: Path,
    *,
    actor: str = "owner",
    capability: Capability = Capability.SELF_MODIFY,
    grant_minutes: int = 60,
) -> tuple[AutonomyRepository, AutonomyRun, Database]:
    root = tmp_path / f"workspace-{capability.value.replace('.', '-')}"
    root.mkdir(exist_ok=True)
    now = datetime.now(UTC)
    run = AutonomyRun(
        actor=actor,
        workspace=WorkspaceScope.from_root(root),
        grant=CapabilityGrant(
            capabilities=frozenset({capability}),
            issued_at=now,
            expires_at=now + timedelta(minutes=grant_minutes),
            max_steps=3,
        ),
        plan=RunPlan(
            objective="Prepare an immutable mutation candidate",
            steps=(
                RunStep(
                    step_id="mutate",
                    capability=capability,
                    action="propose exact source mutation",
                ),
            ),
        ),
    )
    database = _database(tmp_path / f"vault-{capability.value}.sqlite3")
    repository = AutonomyRepository(database)
    repository.create(run)
    return repository, run, database


def _create(path: str = "src/new.py", content: bytes = b"print('new')\n") -> MutationItem:
    return MutationItem(path, MutationOperation.CREATE, False, None, None, content)


def _replace(
    path: str = "src/existing.py", content: bytes = b"print('changed')\n"
) -> MutationItem:
    original = b"print('old')\n"
    return MutationItem(
        path,
        MutationOperation.REPLACE,
        True,
        hashlib.sha256(original).hexdigest(),
        len(original),
        content,
    )


def _proposal(
    run: AutonomyRun,
    *items: MutationItem,
    **overrides: object,
) -> MutationProposal:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "run_id": run.run_id,
        "step_id": "mutate",
        "actor": run.actor,
        "workspace_root": str(run.workspace.root),
        "items": items or (_create(),),
        "created_at": now,
        "expires_at": now + timedelta(minutes=20),
    }
    values.update(overrides)
    return MutationProposal(**values)  # type: ignore[arg-type]


def _table_names(database: Database) -> set[str]:
    with database.connect() as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }


def test_fresh_vault_and_root_migrate_to_schema_60_with_vault_only_tables(
    tmp_path: Path,
) -> None:
    root = _database(tmp_path / "root.sqlite3", role="root")
    vault = _database(tmp_path / "vault.sqlite3", role="vault")

    for database in (root, vault):
        with database.connect() as connection:
            assert connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0] == "60"

    assert "assistant_autonomy_mutation_proposals" not in _table_names(root)
    assert "assistant_autonomy_mutation_items" not in _table_names(root)
    assert "assistant_autonomy_mutation_proposals" in _table_names(vault)
    assert "assistant_autonomy_mutation_items" in _table_names(vault)


def test_schema_59_to_60_preserves_autonomy_rows_and_is_idempotent(
    tmp_path: Path,
) -> None:
    repository, run, database = _run(tmp_path)
    assert repository.get(run.run_id) is not None
    with database.connect() as connection:
        connection.execute("DROP TABLE assistant_autonomy_mutation_items")
        connection.execute("DROP TABLE assistant_autonomy_mutation_proposals")
        connection.execute(
            "UPDATE schema_meta SET value='59' WHERE key='schema_version'"
        )

    database.migrate()
    database.migrate()

    with database.connect() as connection:
        assert connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "60"
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_runs WHERE public_id=?",
            (run.run_id,),
        ).fetchone()[0] == 1
    assert "assistant_autonomy_mutation_items" in _table_names(database)


def test_exact_create_and_replace_round_trip_preserves_bytes_hash_and_order(
    tmp_path: Path,
) -> None:
    repository, run, _ = _run(tmp_path)
    proposal = _proposal(
        run,
        _replace("z.py", b"z\r\n"),
        _create("a.py", "cafe\u0301\n".encode()),
    )

    stored = repository.create_mutation_proposal(
        proposal, request_key="proposal-1", actor="owner"
    )
    loaded = repository.mutation_proposal(stored.public_id, actor="owner")

    assert loaded == stored
    assert loaded is not None
    assert loaded.proposal == proposal
    assert loaded.proposal.proposal_sha256 == proposal.proposal_sha256
    assert [item.relative_path for item in loaded.proposal.items] == ["a.py", "z.py"]
    assert [item.proposed_content for item in loaded.proposal.items] == [
        "cafe\u0301\n".encode(),
        b"z\r\n",
    ]
    assert [item.operation for item in loaded.proposal.items] == [
        MutationOperation.CREATE,
        MutationOperation.REPLACE,
    ]
    assert repository.list_mutation_proposals(run.run_id, actor="owner") == [stored]


def test_same_request_and_proposal_is_idempotent(tmp_path: Path) -> None:
    repository, run, database = _run(tmp_path)
    proposal = _proposal(run)

    first = repository.create_mutation_proposal(
        proposal, request_key="stable-key", actor="owner"
    )
    second = repository.create_mutation_proposal(
        proposal, request_key="stable-key", actor="owner"
    )

    assert second == first
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_proposals"
        ).fetchone()[0] == 1


def test_identical_replay_remains_read_only_after_proposal_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, run, _ = _run(tmp_path)
    proposal = _proposal(run)
    first = repository.create_mutation_proposal(
        proposal, request_key="later-replay", actor="owner"
    )
    monkeypatch.setattr(
        autonomy_repository,
        "_utcnow",
        lambda: proposal.expires_at + timedelta(seconds=1),
    )

    assert repository.create_mutation_proposal(
        proposal, request_key="later-replay", actor="owner"
    ) == first


def test_same_request_with_different_proposal_is_denied(tmp_path: Path) -> None:
    repository, run, _ = _run(tmp_path)
    repository.create_mutation_proposal(
        _proposal(run), request_key="stable-key", actor="owner"
    )

    with pytest.raises(PermissionError, match="request_key"):
        repository.create_mutation_proposal(
            _proposal(run, _create(content=b"different\n")),
            request_key="stable-key",
            actor="owner",
        )


def test_request_key_is_exact_nonempty_and_untrimmed(tmp_path: Path) -> None:
    repository, run, _ = _run(tmp_path)

    for key in ("", " padded", "padded ", "bad\x00key"):
        with pytest.raises(ValueError):
            repository.create_mutation_proposal(
                _proposal(run), request_key=key, actor="owner"
            )


def test_wrong_actor_run_and_step_are_denied(tmp_path: Path) -> None:
    repository, run, _ = _run(tmp_path)
    with pytest.raises(PermissionError):
        repository.create_mutation_proposal(
            _proposal(run), request_key="wrong-actor", actor="intruder"
        )
    with pytest.raises((PermissionError, ValueError)):
        repository.create_mutation_proposal(
            _proposal(run, run_id="missing-run"),
            request_key="wrong-run",
            actor="owner",
        )
    with pytest.raises(PermissionError, match="step inexistente"):
        repository.create_mutation_proposal(
            _proposal(run, step_id="missing-step"),
            request_key="wrong-step",
            actor="owner",
        )


def test_non_self_modify_step_is_denied(tmp_path: Path) -> None:
    repository, run, _ = _run(tmp_path, capability=Capability.WORKSPACE_READ)

    with pytest.raises(PermissionError, match="self.modify"):
        repository.create_mutation_proposal(
            _proposal(run), request_key="read-step", actor="owner"
        )


def test_persisted_grant_without_self_modify_is_denied(tmp_path: Path) -> None:
    repository, run, database = _run(tmp_path)
    with database.connect() as connection:
        connection.execute("DROP TRIGGER trg_autonomy_runs_authority_immutable")
        encoded = json.loads(
            connection.execute(
                "SELECT grant_json FROM assistant_autonomy_runs WHERE public_id=?",
                (run.run_id,),
            ).fetchone()[0]
        )
        encoded["capabilities"] = []
        connection.execute(
            "UPDATE assistant_autonomy_runs SET grant_json=? WHERE public_id=?",
            (json.dumps(encoded), run.run_id),
        )

    with pytest.raises(PermissionError, match="self.modify"):
        repository.create_mutation_proposal(
            _proposal(run), request_key="missing-grant", actor="owner"
        )


def test_workspace_mismatch_is_denied(tmp_path: Path) -> None:
    repository, run, _ = _run(tmp_path)

    with pytest.raises(PermissionError, match="workspace"):
        repository.create_mutation_proposal(
            _proposal(run, workspace_root="/different/workspace"),
            request_key="wrong-workspace",
            actor="owner",
        )


def test_expired_proposal_and_expiry_beyond_grant_are_denied(tmp_path: Path) -> None:
    repository, run, _ = _run(tmp_path, grant_minutes=10)
    past = datetime.now(UTC) - timedelta(minutes=20)
    with pytest.raises(PermissionError, match="expiró"):
        repository.create_mutation_proposal(
            _proposal(
                run,
                created_at=past,
                expires_at=past + timedelta(minutes=10),
            ),
            request_key="expired",
            actor="owner",
        )
    now = datetime.now(UTC)
    with pytest.raises(PermissionError, match="grant"):
        repository.create_mutation_proposal(
            _proposal(
                run,
                created_at=now,
                expires_at=now + timedelta(minutes=20),
            ),
            request_key="beyond-grant",
            actor="owner",
        )


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("assistant_autonomy_mutation_items", "proposed_content", b"altered source!!\n"),
        ("assistant_autonomy_mutation_items", "proposed_sha256", "0" * 64),
        ("assistant_autonomy_mutation_items", "proposed_size", 1),
        ("assistant_autonomy_mutation_proposals", "proposal_sha256", "0" * 64),
    ],
    ids=["blob", "item-sha", "item-size", "proposal-sha"],
)
def test_tampered_durable_commitments_are_detected(
    tmp_path: Path, table: str, column: str, value: object
) -> None:
    repository, run, database = _run(tmp_path)
    stored = repository.create_mutation_proposal(
        _proposal(run), request_key="tamper", actor="owner"
    )
    trigger = (
        "trg_autonomy_mutation_items_no_update"
        if table.endswith("items")
        else "trg_autonomy_mutation_proposals_no_update"
    )
    with database.connect() as connection:
        connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(f"UPDATE {table} SET {column}=?", (value,))

    with pytest.raises(PermissionError, match="durable inconsistente"):
        repository.mutation_proposal(stored.public_id, actor="owner")


@pytest.mark.parametrize(
    ("table", "operation", "trigger", "message"),
    [
        (
            "assistant_autonomy_mutation_proposals",
            "UPDATE assistant_autonomy_mutation_proposals SET actor='other'",
            "autonomy_mutation_proposals_append_only",
            "proposal-update",
        ),
        (
            "assistant_autonomy_mutation_proposals",
            "DELETE FROM assistant_autonomy_mutation_proposals",
            "autonomy_mutation_proposals_append_only",
            "proposal-delete",
        ),
        (
            "assistant_autonomy_mutation_items",
            "UPDATE assistant_autonomy_mutation_items SET ordinal=1",
            "autonomy_mutation_items_append_only",
            "item-update",
        ),
        (
            "assistant_autonomy_mutation_items",
            "DELETE FROM assistant_autonomy_mutation_items",
            "autonomy_mutation_items_append_only",
            "item-delete",
        ),
    ],
    ids=lambda value: str(value),
)
def test_proposal_and_item_rows_are_append_only(
    tmp_path: Path,
    table: str,
    operation: str,
    trigger: str,
    message: str,
) -> None:
    del table, message
    repository, run, database = _run(tmp_path)
    repository.create_mutation_proposal(
        _proposal(run), request_key="immutable", actor="owner"
    )

    with pytest.raises(sqlite3.IntegrityError, match=trigger), database.connect() as connection:
        connection.execute(operation)


def test_proposal_insert_is_atomic_when_an_item_insert_fails(tmp_path: Path) -> None:
    repository, run, database = _run(tmp_path)
    with database.connect() as connection:
        connection.executescript(
            """
            CREATE TRIGGER reject_second_mutation_item
            BEFORE INSERT ON assistant_autonomy_mutation_items
            WHEN NEW.ordinal = 1
            BEGIN SELECT RAISE(ABORT, 'forced_item_failure'); END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced_item_failure"):
        repository.create_mutation_proposal(
            _proposal(run, _create("a.py"), _create("b.py")),
            request_key="atomic",
            actor="owner",
        )

    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_proposals"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM assistant_autonomy_mutation_items"
        ).fetchone()[0] == 0


def test_cross_actor_reads_are_denied(tmp_path: Path) -> None:
    repository, run, _ = _run(tmp_path)
    stored = repository.create_mutation_proposal(
        _proposal(run), request_key="private", actor="owner"
    )

    with pytest.raises(PermissionError):
        repository.mutation_proposal(stored.public_id, actor="intruder")
    with pytest.raises(PermissionError):
        repository.list_mutation_proposals(run.run_id, actor="intruder")


def test_creation_has_no_gate_state_execution_or_content_event(tmp_path: Path) -> None:
    repository, run, database = _run(tmp_path)
    content = b"unique local proposed source 9a2\n"
    before = repository.get(run.run_id)
    repository.create_mutation_proposal(
        _proposal(run, _create(content=content)),
        request_key="no-authority",
        actor="owner",
    )
    after = repository.get(run.run_id)

    assert before is not None and after is not None
    assert after["status"] == before["status"] == "planned"
    assert after["events"] == before["events"]
    assert after["human_gates"] == []
    with database.connect() as connection:
        for table in (
            "assistant_autonomy_execution_reservations",
            "assistant_autonomy_execution_launches",
            "assistant_autonomy_execution_results",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        event_payload = b"".join(
            str(row[0]).encode()
            for row in connection.execute(
                "SELECT payload_json FROM assistant_autonomy_events"
            )
        )
    assert content not in event_payload


def test_root_database_never_stores_proposed_source_bytes(tmp_path: Path) -> None:
    root = _database(tmp_path / "root.sqlite3", role="root")
    repository, run, _ = _run(tmp_path)
    content = b"vault-only proposed source sentinel 9a2\n"
    repository.create_mutation_proposal(
        _proposal(run, _create(content=content)),
        request_key="vault-only",
        actor="owner",
    )

    assert "assistant_autonomy_mutation_items" not in _table_names(root)
    assert content not in root.path.read_bytes()
