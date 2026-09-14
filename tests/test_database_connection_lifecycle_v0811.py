from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from elyndra.db import Database, _ClosingSQLiteConnection


def _assert_closed(connection: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_connect_context_commits_then_closes(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    with database.connect() as connection:
        assert isinstance(connection, _ClosingSQLiteConnection)
        connection.execute("CREATE TABLE lifecycle(value TEXT NOT NULL)")
        connection.execute("INSERT INTO lifecycle(value) VALUES('committed')")

    _assert_closed(connection)
    direct = database.connect()
    try:
        assert direct.execute("SELECT value FROM lifecycle").fetchone()[0] == "committed"
    finally:
        direct.close()


def test_connect_context_rolls_back_then_closes(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    with database.connect() as setup:
        setup.execute("CREATE TABLE lifecycle(value TEXT NOT NULL)")

    with pytest.raises(RuntimeError, match="abort"), database.connect() as connection:
        connection.execute("INSERT INTO lifecycle(value) VALUES('rolled-back')")
        raise RuntimeError("abort")

    _assert_closed(connection)
    with database.connect() as check:
        assert check.execute("SELECT COUNT(*) FROM lifecycle").fetchone()[0] == 0


def test_mutation_durable_context_closes_and_preserves_full_sync(tmp_path: Path) -> None:
    database = Database(tmp_path / "vault.sqlite3", role="vault")
    with database.connect_mutation_durable() as connection:
        assert isinstance(connection, _ClosingSQLiteConnection)
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        connection.execute("CREATE TABLE durable(value TEXT NOT NULL)")

    _assert_closed(connection)


def test_connect_closes_when_pragma_initialization_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_execute = _ClosingSQLiteConnection.execute
    original_close = _ClosingSQLiteConnection.close
    closed: list[_ClosingSQLiteConnection] = []

    def fail_journal_mode(
        self: _ClosingSQLiteConnection, sql: str, parameters: object = (), /
    ) -> sqlite3.Cursor:
        if sql == "PRAGMA journal_mode = WAL":
            raise sqlite3.OperationalError("forced pragma failure")
        return original_execute(self, sql, parameters)

    def record_close(self: _ClosingSQLiteConnection) -> None:
        closed.append(self)
        original_close(self)

    monkeypatch.setattr(_ClosingSQLiteConnection, "execute", fail_journal_mode)
    monkeypatch.setattr(_ClosingSQLiteConnection, "close", record_close)

    with pytest.raises(sqlite3.OperationalError, match="forced pragma failure"):
        Database(tmp_path / "vault.sqlite3", role="vault").connect()

    assert len(closed) == 1
    _assert_closed(closed[0])


def test_mutation_durable_closes_when_full_sync_initialization_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_execute = _ClosingSQLiteConnection.execute
    original_close = _ClosingSQLiteConnection.close
    closed: list[_ClosingSQLiteConnection] = []

    def fail_full_sync(
        self: _ClosingSQLiteConnection, sql: str, parameters: object = (), /
    ) -> sqlite3.Cursor:
        if sql == "PRAGMA synchronous = FULL":
            raise sqlite3.OperationalError("forced durable pragma failure")
        return original_execute(self, sql, parameters)

    def record_close(self: _ClosingSQLiteConnection) -> None:
        closed.append(self)
        original_close(self)

    monkeypatch.setattr(_ClosingSQLiteConnection, "execute", fail_full_sync)
    monkeypatch.setattr(_ClosingSQLiteConnection, "close", record_close)

    database = Database(tmp_path / "vault.sqlite3", role="vault")
    with pytest.raises(sqlite3.OperationalError, match="forced durable pragma failure"):
        database.connect_mutation_durable()

    assert len(closed) == 1
    _assert_closed(closed[0])
