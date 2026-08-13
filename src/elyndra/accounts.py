from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from elyndra import __version__
from elyndra.db import Database

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65_536, parallelism=2)
_SESSION_TTL_DAYS = 30
_EXPORT_MAGIC = "elyndra-encrypted-export-v1"


@dataclass(frozen=True, slots=True)
class AccountIdentity:
    public_id: str
    username: str
    email: str
    system_user: str
    preferred_name: str
    birth_date: str
    developer_mode: bool
    telemetry_enabled: bool
    birthday_greeting_enabled: bool
    timezone: str
    language: str

    @property
    def display_name(self) -> str:
        return self.preferred_name or self.username


class AccountRepository:
    def __init__(
        self,
        database: Database,
        *,
        scope_public_id: str = "",
        vault_database: Database | None = None,
    ) -> None:
        self.database = database
        self.scope_public_id = scope_public_id.strip()
        self.vault_database = vault_database

    def scoped(
        self, public_id: str, *, vault_database: Database | None = None
    ) -> AccountRepository:
        return AccountRepository(
            self.database,
            scope_public_id=public_id,
            vault_database=vault_database,
        )

    def account_count(self) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM local_accounts WHERE status = 'active'"
            ).fetchone()
        return int(row["total"]) if row else 0

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM local_accounts WHERE status = 'active' ORDER BY id"
            ).fetchall()
        return [self._public(row) for row in rows]

    def has_account(self) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM local_accounts WHERE status = 'active' LIMIT 1"
            ).fetchone()
        return row is not None

    def register(
        self,
        *,
        username: str,
        email: str,
        password: str,
        password_confirmation: str,
        birth_date: str,
        system_user: str,
        developer_mode: bool = False,
        telemetry_enabled: bool = False,
        preferred_name: str = "",
        timezone: str = "America/Santiago",
        language: str = "es-CL",
    ) -> dict[str, Any]:
        if self.account_count() >= 16:
            raise ValueError("Esta instalación alcanzó el máximo de 16 cuentas locales.")
        clean_username = _validate_username(username)
        clean_email = _validate_email(email)
        _validate_password(password, password_confirmation)
        clean_birth = _validate_adult_birth_date(birth_date)
        clean_preferred = _bounded_text(preferred_name, 80)
        now = _now()
        public_id = uuid.uuid4().hex
        password_hash = _PASSWORD_HASHER.hash(password)
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO local_accounts(
                        public_id, username, email, password_hash, birth_date,
                        preferred_name, system_user, developer_mode,
                        telemetry_enabled, birthday_greeting_enabled,
                        timezone, language, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'active', ?, ?)
                    """,
                    (
                        public_id,
                        clean_username,
                        clean_email,
                        password_hash,
                        clean_birth,
                        clean_preferred,
                        system_user,
                        1 if developer_mode else 0,
                        1 if telemetry_enabled else 0,
                        _bounded_text(timezone, 80) or "UTC",
                        _bounded_text(language, 20) or "es-CL",
                        now,
                        now,
                    ),
                )
                account_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
                connection.execute(
                    """
                    INSERT INTO account_consents(
                        public_id, account_id, consent_type, granted,
                        policy_version, created_at, updated_at
                    ) VALUES (?, ?, 'telemetry', ?, '0.8.8-alpha', ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        account_id,
                        1 if telemetry_enabled else 0,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO account_recovery_settings(
                        account_id, local_export_enabled, remote_backup_enabled,
                        remote_provider, two_factor_status, created_at, updated_at
                    ) VALUES (?, 1, 0, 'none', 'available_not_configured', ?, ?)
                    """,
                    (account_id, now, now),
                )
        except sqlite3.IntegrityError as exc:
            message = str(exc).casefold()
            if "username" in message:
                raise ValueError("Ese nombre de usuario ya está registrado.") from None
            if "email" in message:
                raise ValueError("Ese correo ya está registrado.") from None
            raise ValueError("No fue posible registrar la cuenta local.") from exc
        result = self.get_account(public_id)
        assert result is not None
        return result

    def authenticate(
        self,
        *,
        login: str,
        password: str,
        interface: str,
        user_agent: str = "",
    ) -> tuple[dict[str, Any], str]:
        account = self._account_by_login(login)
        if account is None:
            raise ValueError("Credenciales inválidas.")
        try:
            _PASSWORD_HASHER.verify(str(account["password_hash"]), password)
        except (VerifyMismatchError, InvalidHashError):
            raise ValueError("Credenciales inválidas.") from None
        if _PASSWORD_HASHER.check_needs_rehash(str(account["password_hash"])):
            self._replace_password_hash(int(account["id"]), _PASSWORD_HASHER.hash(password))
        token = secrets.token_urlsafe(48)
        now = datetime.now(UTC)
        expires = now + timedelta(days=_SESSION_TTL_DAYS)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO account_sessions(
                    public_id, account_id, token_sha256, interface,
                    user_agent_sha256, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    int(account["id"]),
                    _sha256(token),
                    _bounded_text(interface, 20) or "local",
                    _sha256(user_agent) if user_agent else "",
                    expires.isoformat(),
                    now.isoformat(),
                ),
            )
        return self._public(account), token

    def account_for_session(self, token: str) -> dict[str, Any] | None:
        clean = token.strip()
        if not clean:
            return None
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT a.* FROM account_sessions s
                JOIN local_accounts a ON a.id = s.account_id
                WHERE s.token_sha256 = ? AND s.revoked_at IS NULL
                  AND s.expires_at > ? AND a.status = 'active'
                LIMIT 1
                """,
                (_sha256(clean), now),
            ).fetchone()
        return self._public(row) if row is not None else None

    def revoke_session(self, token: str) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE account_sessions SET revoked_at = ? "
                "WHERE token_sha256 = ? AND revoked_at IS NULL",
                (_now(), _sha256(token)),
            )
        return cursor.rowcount > 0

    def get_account(self, identifier: str = "") -> dict[str, Any] | None:
        clean = (identifier or self.scope_public_id).strip()
        with self.database.connect() as connection:
            if clean:
                row = connection.execute(
                    """
                    SELECT * FROM local_accounts
                    WHERE status = 'active'
                      AND (public_id = ? OR lower(username) = ? OR lower(email) = ?)
                    LIMIT 1
                    """,
                    (clean, clean.casefold(), clean.casefold()),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM local_accounts WHERE status = 'active' ORDER BY id LIMIT 1"
                ).fetchone()
        return self._public(row) if row is not None else None

    def identity(self) -> AccountIdentity | None:
        row = self._require_account_row(optional=True)
        if row is None:
            return None
        return AccountIdentity(
            public_id=str(row["public_id"]),
            username=str(row["username"]),
            email=str(row["email"]),
            system_user=str(row["system_user"]),
            preferred_name=str(row["preferred_name"]),
            birth_date=str(row["birth_date"]),
            developer_mode=bool(row["developer_mode"]),
            telemetry_enabled=bool(row["telemetry_enabled"]),
            birthday_greeting_enabled=bool(row["birthday_greeting_enabled"]),
            timezone=str(row["timezone"]),
            language=str(row["language"]),
        )

    def update_profile(self, *, approved: bool, **values: Any) -> dict[str, Any]:
        if not approved:
            raise PermissionError("Actualizar el perfil requiere confirmación explícita.")
        account = self._require_account_row()
        allowed = {
            "preferred_name": lambda value: _bounded_text(value, 80),
            "pronouns": lambda value: _bounded_text(value, 40),
            "sex": lambda value: _bounded_text(value, 40),
            "gender_identity": lambda value: _bounded_text(value, 80),
            "sexual_orientation": lambda value: _bounded_text(value, 80),
            "timezone": lambda value: _bounded_text(value, 80),
            "language": lambda value: _bounded_text(value, 20),
            "developer_mode": lambda value: 1 if bool(value) else 0,
            "telemetry_enabled": lambda value: 1 if bool(value) else 0,
            "birthday_greeting_enabled": lambda value: 1 if bool(value) else 0,
        }
        updates: list[str] = []
        params: list[Any] = []
        for key, cleaner in allowed.items():
            if key in values and values[key] is not None:
                updates.append(f"{key} = ?")
                params.append(cleaner(values[key]))
        if not updates:
            current = self.get_account()
            assert current is not None
            return current
        params.extend((_now(), int(account["id"])))
        with self.database.connect() as connection:
            connection.execute(
                f"UPDATE local_accounts SET {', '.join(updates)}, updated_at = ? WHERE id = ?",
                tuple(params),
            )
            if "telemetry_enabled" in values:
                connection.execute(
                    """
                    INSERT INTO account_consents(
                        public_id, account_id, consent_type, granted,
                        policy_version, created_at, updated_at
                    ) VALUES (?, ?, 'telemetry', ?, '0.8.8-alpha', ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        int(account["id"]),
                        1 if bool(values["telemetry_enabled"]) else 0,
                        _now(),
                        _now(),
                    ),
                )
        current = self.get_account()
        assert current is not None
        return current

    def change_email(self, *, password: str, email: str, approved: bool) -> dict[str, Any]:
        if not approved:
            raise PermissionError("Cambiar el correo requiere confirmación explícita.")
        account = self._require_account_row()
        self._verify_password(account, password)
        clean = _validate_email(email)
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE local_accounts SET email = ?, updated_at = ? WHERE id = ?",
                (clean, _now(), int(account["id"])),
            )
        result = self.get_account()
        assert result is not None
        return result

    def change_password(
        self,
        *,
        current_password: str,
        new_password: str,
        confirmation: str,
        approved: bool,
    ) -> None:
        if not approved:
            raise PermissionError("Cambiar la contraseña requiere confirmación explícita.")
        account = self._require_account_row()
        self._verify_password(account, current_password)
        _validate_password(new_password, confirmation)
        self._replace_password_hash(int(account["id"]), _PASSWORD_HASHER.hash(new_password))
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE account_sessions SET revoked_at = ? WHERE account_id = ?",
                (_now(), int(account["id"])),
            )

    def reset_password_local(
        self,
        *,
        system_user: str,
        login: str,
        new_password: str,
        confirmation: str,
        approved: bool,
    ) -> None:
        if not approved:
            raise PermissionError(
                "Restablecer la contraseña local requiere confirmación explícita."
            )
        account = self._account_by_login(login)
        if account is None:
            raise ValueError("No existe una cuenta activa con ese usuario o correo.")
        if str(account["system_user"]) != system_user:
            raise PermissionError(
                "El restablecimiento local solo puede ejecutarlo el usuario del sistema "
                "que creó la cuenta."
            )
        _validate_password(new_password, confirmation)
        self._replace_password_hash(int(account["id"]), _PASSWORD_HASHER.hash(new_password))
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE account_sessions SET revoked_at = ? WHERE account_id = ?",
                (_now(), int(account["id"])),
            )

    def revoke_interface_sessions(self, interface: str) -> int:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE account_sessions SET revoked_at = ?
                WHERE interface = ? AND revoked_at IS NULL
                """,
                (_now(), interface.strip()),
            )
        return cursor.rowcount

    def security_status(self) -> dict[str, Any]:
        account = self._require_account_row()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM account_recovery_settings WHERE account_id = ?",
                (int(account["id"]),),
            ).fetchone()
        return {
            "password_hash": "argon2id",
            "two_factor_status": str(row["two_factor_status"]),
            "two_factor_available": True,
            "two_factor_enabled": str(row["two_factor_status"]) == "enabled",
            "local_export_enabled": bool(row["local_export_enabled"]),
            "remote_backup_enabled": False,
            "remote_provider": "none",
        }

    def export_encrypted(
        self,
        *,
        output_path: Path,
        account_password: str,
        export_passphrase: str,
        approved: bool,
    ) -> Path:
        payload = self.export_encrypted_payload(
            account_password=account_password,
            export_passphrase=export_passphrase,
            approved=approved,
            destination_label=str(output_path.expanduser().resolve()),
        )
        output = output_path.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        output.write_bytes(payload)
        output.chmod(0o600)
        return output

    def export_encrypted_payload(
        self,
        *,
        account_password: str,
        export_passphrase: str,
        approved: bool,
        destination_label: str = "local-web-download",
    ) -> bytes:
        if not approved:
            raise PermissionError("La exportación requiere confirmación explícita.")
        account = self._require_account_row()
        self._verify_password(account, account_password)
        if not 12 <= len(export_passphrase) <= 128:
            raise ValueError("La frase de exportación debe tener entre 12 y 128 caracteres.")
        with tempfile.TemporaryDirectory(prefix="elyndra-export-") as temp_dir:
            backup_path = Path(temp_dir) / "elyndra.db"
            source_database = self.vault_database or self.database
            source = source_database.connect()
            destination = sqlite3.connect(backup_path)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
            database_bytes = backup_path.read_bytes()
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)
        key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(
            export_passphrase.encode()
        )
        metadata = {
            "format": _EXPORT_MAGIC,
            "version": __version__,
            "created_at": _now(),
            "account_public_id": str(account["public_id"]),
            "remote_backup": False,
        }
        aad = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        ciphertext = AESGCM(key).encrypt(nonce, database_bytes, aad)
        envelope = {
            **metadata,
            "kdf": "scrypt-n32768-r8-p1",
            "cipher": "aes-256-gcm",
            "salt": base64.b64encode(salt).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "payload": base64.b64encode(ciphertext).decode(),
        }
        encoded = json.dumps(envelope, ensure_ascii=False).encode()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO account_exports(
                    public_id, account_id, format, destination_sha256,
                    size_bytes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    int(account["id"]),
                    _EXPORT_MAGIC,
                    _sha256(destination_label),
                    len(encoded),
                    _now(),
                ),
            )
        return encoded

    def telemetry_preview(self) -> dict[str, Any]:
        account = self._require_account_row()
        age = _age_on(date.fromisoformat(str(account["birth_date"])), date.today())
        return {
            "enabled": bool(account["telemetry_enabled"]),
            "fields": {
                "elyndra_version": __version__,
                "interface": "cli_or_web",
                "usage_category": "aggregated_only",
                "duration_bucket": "not_collected_yet",
                "age_range": _age_range(age),
            },
            "never_included": [
                "name",
                "email",
                "birth_date",
                "identity_or_orientation",
                "health_or_wellbeing",
                "prompts_or_searches",
                "files_or_secrets",
                "memory_or_knowledge_content",
            ],
            "network_delivery_implemented": False,
        }

    def _account_by_login(self, login: str) -> sqlite3.Row | None:
        clean = login.strip().casefold()
        with self.database.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM local_accounts
                WHERE status = 'active' AND (lower(username) = ? OR lower(email) = ?)
                LIMIT 1
                """,
                (clean, clean),
            ).fetchone()

    def _require_account_row(self, *, optional: bool = False) -> sqlite3.Row | None:
        with self.database.connect() as connection:
            if self.scope_public_id:
                row = connection.execute(
                    """
                    SELECT * FROM local_accounts
                    WHERE public_id = ? AND status = 'active'
                    LIMIT 1
                    """,
                    (self.scope_public_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM local_accounts WHERE status = 'active' ORDER BY id LIMIT 1"
                ).fetchone()
        if row is None and not optional:
            raise ValueError("No existe una cuenta local registrada.")
        return row

    def _verify_password(self, account: sqlite3.Row, password: str) -> None:
        try:
            _PASSWORD_HASHER.verify(str(account["password_hash"]), password)
        except (VerifyMismatchError, InvalidHashError):
            raise ValueError("Contraseña actual inválida.") from None

    def _replace_password_hash(self, account_id: int, password_hash: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE local_accounts SET password_hash = ?, updated_at = ? WHERE id = ?",
                (password_hash, _now(), account_id),
            )

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        birth = date.fromisoformat(str(row["birth_date"]))
        return {
            "public_id": str(row["public_id"]),
            "username": str(row["username"]),
            "email": str(row["email"]),
            "birth_date": birth.isoformat(),
            "age": _age_on(birth, date.today()),
            "preferred_name": str(row["preferred_name"]),
            "pronouns": str(row["pronouns"]),
            "sex": str(row["sex"]),
            "gender_identity": str(row["gender_identity"]),
            "sexual_orientation": str(row["sexual_orientation"]),
            "developer_mode": bool(row["developer_mode"]),
            "telemetry_enabled": bool(row["telemetry_enabled"]),
            "birthday_greeting_enabled": bool(row["birthday_greeting_enabled"]),
            "timezone": str(row["timezone"]),
            "language": str(row["language"]),
            "status": str(row["status"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }


def _validate_username(value: str) -> str:
    clean = value.strip()
    if not _USERNAME_RE.fullmatch(clean):
        raise ValueError(
            "El nombre de usuario debe tener 3–32 caracteres: letras, números, "
            "punto, guion o guion bajo."
        )
    return clean


def _validate_email(value: str) -> str:
    clean = value.strip().casefold()
    if len(clean) > 254 or not _EMAIL_RE.fullmatch(clean):
        raise ValueError("Correo electrónico inválido.")
    return clean


def _validate_password(password: str, confirmation: str) -> None:
    if password != confirmation:
        raise ValueError("Las contraseñas no coinciden.")
    if not 8 <= len(password) <= 64:
        raise ValueError("La contraseña debe tener entre 8 y 64 caracteres.")
    if not any(char.isalpha() for char in password):
        raise ValueError("La contraseña debe incluir al menos una letra.")
    if not any(char.isdigit() for char in password):
        raise ValueError("La contraseña debe incluir al menos un número.")
    if not any(not char.isalnum() for char in password):
        raise ValueError("La contraseña debe incluir al menos un carácter especial.")


def _validate_adult_birth_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError("La fecha de nacimiento debe usar YYYY-MM-DD.") from exc
    today = date.today()
    if parsed >= today:
        raise ValueError("La fecha de nacimiento debe estar en el pasado.")
    if _age_on(parsed, today) < 18:
        raise ValueError("Debes ser mayor de edad para registrar esta instalación.")
    if _age_on(parsed, today) > 120:
        raise ValueError("La fecha de nacimiento no es válida.")
    return parsed.isoformat()


def _age_on(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def _age_range(age: int) -> str:
    if age <= 23:
        return "18-23"
    if age <= 29:
        return "24-29"
    if age <= 39:
        return "30-39"
    if age <= 49:
        return "40-49"
    if age <= 59:
        return "50-59"
    if age <= 75:
        return "60-75"
    if age <= 90:
        return "76-90"
    return "90+"


def _bounded_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()
