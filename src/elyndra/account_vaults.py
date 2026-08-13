from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from elyndra.db import Database
from elyndra.paths import ElyndraPaths


class AccountVaultManager:
    """Creates one isolated SQLite/data vault per local account."""

    def __init__(self, root_paths: ElyndraPaths, registry: Database) -> None:
        self.root_paths = root_paths
        self.registry = registry

    def ensure(self, account_public_id: str) -> ElyndraPaths:
        account = self._account(account_public_id)
        if account is None:
            raise ValueError("Cuenta local no encontrada para crear su bóveda.")
        vault_paths = self.root_paths.for_account(str(account["public_id"]))
        vault_paths.ensure()
        if not vault_paths.database_file.exists():
            if self._should_clone_legacy(int(account["id"])):
                self._clone_registry(vault_paths.database_file)
                self._sanitize_registry_tables(vault_paths.database_file)
                migrated = 1
            else:
                Database(vault_paths.database_file, role="vault").migrate()
                migrated = 0
            with self.registry.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO account_vaults(
                        account_id, vault_path, status, migrated_from_legacy,
                        created_at, updated_at
                    ) VALUES (?, ?, 'active', ?, ?, ?)
                    ON CONFLICT(account_id) DO UPDATE SET
                        vault_path = excluded.vault_path,
                        status = 'active',
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(account["id"]),
                        str(vault_paths.database_file),
                        migrated,
                        _now(),
                        _now(),
                    ),
                )
        Database(vault_paths.database_file, role="vault").migrate()
        return vault_paths

    def _account(self, public_id: str) -> sqlite3.Row | None:
        with self.registry.connect() as connection:
            return connection.execute(
                "SELECT * FROM local_accounts WHERE public_id = ? AND status = 'active'",
                (public_id,),
            ).fetchone()

    def _should_clone_legacy(self, account_id: int) -> bool:
        with self.registry.connect() as connection:
            oldest = connection.execute(
                "SELECT MIN(id) AS account_id FROM local_accounts WHERE status = 'active'"
            ).fetchone()
            existing = connection.execute(
                "SELECT 1 FROM account_vaults WHERE status = 'active' LIMIT 1"
            ).fetchone()
            if existing is not None or oldest is None:
                return False
            if int(oldest["account_id"] or 0) != account_id:
                return False
            for table in (
                "chats",
                "memories",
                "documents",
                "assistant_organizer_items",
                "assistant_wellbeing_checkins",
            ):
                row = connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                if row is not None:
                    return True
        return False

    @staticmethod
    def _sanitize_registry_tables(vault_path: Path) -> None:
        connection = sqlite3.connect(vault_path)
        try:
            for table in (
                "account_sessions",
                "account_consents",
                "account_mfa_factors",
                "account_exports",
                "account_recovery_settings",
                "account_vaults",
                "local_accounts",
            ):
                connection.execute(f"DELETE FROM {table}")
            connection.execute("DROP TABLE IF EXISTS alexandria_language_pack_sources")
            connection.execute("DROP TABLE IF EXISTS alexandria_language_packs")
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('database_role', 'vault')"
            )
            connection.commit()
        finally:
            connection.close()

    def _clone_registry(self, destination_path: Path) -> None:
        destination_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        source = self.registry.connect()
        destination = sqlite3.connect(destination_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        destination_path.chmod(0o600)


def _now() -> str:
    return datetime.now(UTC).isoformat()
