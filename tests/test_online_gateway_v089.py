from __future__ import annotations

import json
import pickle
from dataclasses import asdict
from pathlib import Path

import pytest

from elyndra.approvals import ApprovalStore
from elyndra.audit import AuditRepository
from elyndra.db import Database
from elyndra.online_gateway.approvals import GatewayApprovalService, NetworkPermit
from elyndra.online_gateway.audit import GatewayAudit
from elyndra.online_gateway.errors import GatewayError
from elyndra.online_gateway.models import GatewayLimits
from elyndra.online_gateway.operations import OnlineGatewayService
from elyndra.online_gateway.policy import OnlineGatewayPolicy
from elyndra.online_gateway.transport import GatewayTransport


def _database(path: Path, role: str) -> Database:
    database = Database(path, role=role)
    database.migrate()
    return database


def _service(
    tmp_path: Path, *, account: str = "acct-1", enabled: bool = True
) -> OnlineGatewayService:
    root = _database(tmp_path / "root.sqlite3", "root")
    vault = _database(tmp_path / f"{account}.sqlite3", "vault")
    return OnlineGatewayService(
        root_database=root,
        vault_database=vault,
        account_id=account,
        global_enabled=enabled,
        audit=GatewayAudit(AuditRepository(vault)),
    )


def test_schema_50_is_role_scoped_and_idempotent(tmp_path: Path) -> None:
    root = _database(tmp_path / "root.sqlite3", "root")
    vault = _database(tmp_path / "vault.sqlite3", "vault")
    root.migrate()
    vault.migrate()
    with root.connect() as connection:
        assert (
            connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            == "50"
        )
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='online_gateway_sources'"
        ).fetchone()
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='account_gateway_operations'"
            ).fetchone()
            is None
        )
    with vault.connect() as connection:
        assert (
            connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            == "50"
        )
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='account_gateway_operations'"
        ).fetchone()
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='online_gateway_download_jobs'"
            ).fetchone()
            is None
        )


@pytest.mark.parametrize("role", ["root", "vault"])
def test_exact_previous_schema_data_is_preserved(tmp_path: Path, role: str) -> None:
    database = _database(tmp_path / f"{role}.sqlite3", role)
    gateway_tables = (
        ("online_gateway_sources", "online_gateway_cache_entries", "online_gateway_download_jobs")
        if role == "root"
        else (
            "account_online_preferences",
            "account_gateway_sources",
            "account_gateway_operations",
        )
    )
    with database.connect() as connection:
        for table in gateway_tables:
            connection.execute(f"DROP TABLE {table}")
        connection.execute("UPDATE schema_meta SET value='49' WHERE key='schema_version'")
        connection.execute(
            """INSERT INTO memories(kind, content, source, created_at, updated_at)
            VALUES('fact', 'preserved', 'owner', '2026-08-05', '2026-08-05')"""
        )
    database.migrate()
    with database.connect() as connection:
        assert (
            connection.execute("SELECT content FROM memories WHERE content='preserved'").fetchone()[
                0
            ]
            == "preserved"
        )
        assert (
            connection.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            == "50"
        )


def test_defaults_are_deny_by_default(tmp_path: Path) -> None:
    service = _service(tmp_path, enabled=False)
    assert service.status() == {
        "global_gateway_enabled": False,
        "account_online_enabled": False,
        "transport_available": False,
        "phase": 2,
    }
    request = service.request_download_approval("elyndra-official-language-packs")
    with pytest.raises(GatewayError, match="gateway_disabled_global"):
        service.plan_download("elyndra-official-language-packs", approval=request["approval"])


def test_account_local_blocks_plan(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = service.request_download_approval("elyndra-official-language-packs")
    with pytest.raises(GatewayError, match="gateway_disabled_account"):
        service.plan_download("elyndra-official-language-packs", approval=request["approval"])


def test_approved_plan_consumes_once_and_never_transports(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.set_mode("online")
    request = service.request_download_approval("elyndra-official-language-packs")
    result = service.plan_download("elyndra-official-language-packs", approval=request["approval"])
    assert result["operation_state"] == "transport_unavailable"
    assert result["error_code"] == "gateway_transport_unavailable"
    with pytest.raises(GatewayError, match="gateway_approval_invalid"):
        service.approvals.consume(
            request["approval"],
            account_id="acct-1",
            operation_id=request["operation_id"],
            plan_sha256=result["plan_sha256"],
        )


def test_approval_is_bound_to_account_operation_and_plan(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = service.request_download_approval("elyndra-official-language-packs")
    operation = service.operation(request["operation_id"])
    for account, operation_id, plan_sha in (
        ("acct-2", request["operation_id"], operation["plan_sha256"]),
        ("acct-1", "gop_other", operation["plan_sha256"]),
        ("acct-1", request["operation_id"], "0" * 64),
    ):
        with pytest.raises(GatewayError, match="gateway_approval_invalid"):
            service.approvals.consume(
                request["approval"],
                account_id=account,
                operation_id=operation_id,
                plan_sha256=plan_sha,
            )


def test_expired_approval_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    store = ApprovalStore(ttl_seconds=30)
    approvals = GatewayApprovalService(store)
    token = approvals.request(account_id="a", operation_id="o", plan_sha256="p")
    monkeypatch.setattr("elyndra.approvals.time.monotonic", lambda: 10**20)
    with pytest.raises(GatewayError, match="gateway_approval_invalid"):
        approvals.consume(token, account_id="a", operation_id="o", plan_sha256="p")


def test_permit_is_opaque_nonserializable_and_single_use() -> None:
    permit = NetworkPermit("a", "o", "p")
    with pytest.raises(TypeError):
        pickle.dumps(permit)
    permit.consume(account_id="a", operation_id="o", plan_sha256="p")
    with pytest.raises(GatewayError, match="gateway_permit_reused"):
        permit.consume(account_id="a", operation_id="o", plan_sha256="p")


def test_permit_does_not_survive_approval_service_reconstruction() -> None:
    first = GatewayApprovalService()
    token = first.request(account_id="a", operation_id="o", plan_sha256="p")
    second = GatewayApprovalService()
    with pytest.raises(GatewayError, match="gateway_approval_invalid"):
        second.consume(token, account_id="a", operation_id="o", plan_sha256="p")


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://example.test/file", "url_scheme_rejected"),
        ("https://user:pass@example.test/file", "url_credentials_rejected"),
        ("https://example.test/latest/file", "gateway_latest_forbidden"),
    ],
)
def test_url_policy_rejects_untrusted_forms(url: str, code: str) -> None:
    with pytest.raises(GatewayError, match=code):
        OnlineGatewayPolicy().validate_url(url)


def test_fixed_limits_and_resume_contract() -> None:
    limits = asdict(GatewayLimits())
    assert limits["manifest_bytes"] == 512 * 1024
    assert limits["artifact_bytes"] == 2 * 1024**3
    assert limits["operation_bytes"] == 4 * 1024**3
    assert limits["active_downloads"] == 1
    policy = OnlineGatewayPolicy()
    assert policy.can_resume(etag='"x"', response_etag='"x"', status=206, range_ok=True)
    assert not policy.can_resume(etag='W/"x"', response_etag='W/"x"', status=206, range_ok=True)


def test_official_source_is_pinned_and_root_metadata_is_immutable(tmp_path: Path) -> None:
    service = _service(tmp_path)
    source = service.sources.get("elyndra-official-language-packs")
    assert source["release_tag"] == "spanish-core-2026.08.01-r1"
    assert "/latest/" not in source["manifest_url"]
    with service.root_database.connect() as connection:
        row = connection.execute("SELECT * FROM online_gateway_sources").fetchone()
    assert row["source_kind"] == "official-pinned"
    assert bool(row["enabled"])


def test_two_accounts_have_isolated_preferences_sources_and_operations(tmp_path: Path) -> None:
    first = _service(tmp_path, account="first")
    second = _service(tmp_path, account="second")
    first.set_mode("online")
    first.request_download_approval("elyndra-official-language-packs")
    assert first.mode() == "online"
    assert second.mode() == "local"
    assert len(first.operations()) == 1
    assert second.operations() == []


def test_user_pinned_sources_are_validated_and_account_isolated(tmp_path: Path) -> None:
    first = _service(tmp_path, account="first")
    second = _service(tmp_path, account="second")
    descriptor = {
        "source_id": "owner-pinned",
        "trust_class": "user-pinned",
        "manifest_url": "https://example.test/release/manifest.json",
        "manifest_size": 42,
        "manifest_sha256": "a" * 64,
    }
    first.sources.add_user_source(descriptor)
    assert first.sources.get("owner-pinned")["trust_class"] == "user-pinned"
    with pytest.raises(GatewayError, match="gateway_source_not_found"):
        second.sources.get("owner-pinned")
    with pytest.raises(GatewayError, match="gateway_user_source_trust_invalid"):
        first.sources.add_user_source(descriptor | {"trust_class": "official-pinned"})


def test_audit_allowlist_omits_query_content_and_tokens(tmp_path: Path) -> None:
    vault = _database(tmp_path / "vault.sqlite3", "vault")
    audit = GatewayAudit(AuditRepository(vault))
    audit.record(
        account_id="a",
        outcome="planned",
        details={
            "operation_id": "o",
            "hostname": "example.test",
            "url": "https://example.test/file?secret=yes",
            "prompt": "private",
            "token": "secret",
        },
    )
    stored = json.loads(audit.list()[0]["details_json"])
    assert stored == {"hostname": "example.test", "operation_id": "o"}


def test_transport_is_hardened_without_opening_a_connection() -> None:
    transport = GatewayTransport()
    assert transport.context.check_hostname is True
    assert transport.context.verify_mode != 0


def test_startup_and_source_lookup_never_open_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("Phase 2 intentó abrir un socket")

    monkeypatch.setattr("socket.socket", forbidden_socket)
    service = _service(tmp_path)
    assert service.status()["transport_available"] is False
    assert service.sources.get("elyndra-official-language-packs")["manifest_size"] == 5009


def test_gateway_service_exposes_no_transport_to_model_facing_objects() -> None:
    public_names = set(dir(OnlineGatewayService))
    assert "transport" not in public_names
    assert "permit" not in public_names
