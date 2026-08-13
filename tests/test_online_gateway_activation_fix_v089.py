from __future__ import annotations

import inspect
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from elyndra.audit import AuditRepository
from elyndra.config import AppConfig, ConfigError, write_default_config
from elyndra.db import Database
from elyndra.online_gateway.approvals import NetworkPermit
from elyndra.online_gateway.audit import GatewayAudit
from elyndra.online_gateway.errors import GatewayError
from elyndra.online_gateway.operations import (
    OnlineGatewayService,
    _issue_cli_execution_capability,
)
from elyndra.online_gateway.storage import GatewayStorage
from elyndra.paths import ElyndraPaths
from elyndra.web import server as web_server


@pytest.fixture(autouse=True)
def _deny_public_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("activation-fix tests must not use DNS or sockets")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket, "socket", denied)


class _InjectedDownloads:
    def __init__(self, storage: GatewayStorage) -> None:
        self.storage = storage
        self.calls = 0

    def execute(
        self,
        *,
        permit: NetworkPermit,
        account_id: str,
        operation_id: str,
        plan_sha256: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        permit.consume(
            account_id=account_id,
            operation_id=operation_id,
            plan_sha256=plan_sha256,
        )
        self.calls += 1
        return {"state": "verified", "bytes_written": 5009}


def _service(tmp_path: Path) -> tuple[OnlineGatewayService, _InjectedDownloads]:
    paths = ElyndraPaths(
        tmp_path / "config", tmp_path / "data", tmp_path / "state", tmp_path / "cache"
    )
    paths.ensure()
    root = Database(tmp_path / "root.sqlite3", role="root")
    vault = Database(tmp_path / "vault.sqlite3", role="vault")
    root.migrate()
    vault.migrate()
    downloads = _InjectedDownloads(GatewayStorage(paths))
    service = OnlineGatewayService(
        root_database=root,
        vault_database=vault,
        account_id="isolated-account",
        global_enabled=False,
        audit=GatewayAudit(AuditRepository(vault)),
        downloads=downloads,  # type: ignore[arg-type]
    )
    service.set_mode("online")
    return service, downloads


def test_preview_is_deterministic_read_only_and_offline(tmp_path: Path) -> None:
    service, downloads = _service(tmp_path)
    before = service.operations()
    audit_before = service.audit.list()
    first = service.preview_download("elyndra-official-language-packs")
    second = service.preview_download("elyndra-official-language-packs")

    assert first == second
    assert first["method"] == "GET"
    assert first["expected_size"] == 5009
    assert first["expected_sha256"] == (
        "b45b0aecb2c32ff5c94ad04143f90c43ff46698e6dbccbca31645c1c60f009db"
    )
    assert service.operations() == before
    assert service.audit.list() == audit_before
    assert downloads.calls == 0
    assert list(downloads.storage.cache.iterdir()) == []
    assert list(downloads.storage.partial.iterdir()) == []
    assert list(downloads.storage.quarantine.iterdir()) == []
    assert "approval" not in first
    assert "token" not in json.dumps(first)


def test_approval_requires_exact_preview_and_does_not_download(tmp_path: Path) -> None:
    service, downloads = _service(tmp_path)
    preview = service.preview_download("elyndra-official-language-packs")
    with pytest.raises(GatewayError, match="gateway_preview_changed"):
        service.approve_download(
            "elyndra-official-language-packs", plan_digest="0" * 64
        )
    operation = service.approve_download(
        "elyndra-official-language-packs", plan_digest=preview["plan_digest"]
    )
    assert operation["operation_state"] == "planned"
    assert operation["plan"]["preview_digest"] == preview["plan_digest"]
    assert downloads.calls == 0


def test_descriptor_change_after_preview_requires_a_new_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, downloads = _service(tmp_path)
    preview = service.preview_download("elyndra-official-language-packs")
    original_get = service.sources.get

    def changed(source_id: str) -> dict[str, Any]:
        return original_get(source_id) | {"manifest_size": 5010}

    monkeypatch.setattr(service.sources, "get", changed)
    with pytest.raises(GatewayError, match="gateway_preview_changed"):
        service.approve_download(
            "elyndra-official-language-packs", plan_digest=preview["plan_digest"]
        )
    assert service.operations() == []
    assert downloads.calls == 0


def test_only_exact_cli_capability_enables_one_execution(tmp_path: Path) -> None:
    service, downloads = _service(tmp_path)
    preview = service.preview_download("elyndra-official-language-packs")
    operation = service.approve_download(
        "elyndra-official-language-packs", plan_digest=preview["plan_digest"]
    )
    approval = service.request_execution_approval(operation["public_id"])
    with pytest.raises(GatewayError, match="gateway_disabled_global"):
        service.execute_download(operation["public_id"], approval=approval)
    assert downloads.calls == 0

    approval = service.request_execution_approval(operation["public_id"])
    capability = _issue_cli_execution_capability(
        operation_id=operation["public_id"],
        plan_sha256=operation["plan_sha256"],
        command="execute-download",
    )
    result = service.execute_download(
        operation["public_id"], approval=approval, cli_capability=capability
    )
    assert result["state"] == "verified"
    assert downloads.calls == 1
    with pytest.raises(GatewayError, match="gateway_operation_not_planned"):
        service.request_execution_approval(operation["public_id"])


def test_changed_persisted_plan_and_wrong_command_are_rejected(tmp_path: Path) -> None:
    service, downloads = _service(tmp_path)
    preview = service.preview_download("elyndra-official-language-packs")
    operation = service.approve_download(
        "elyndra-official-language-packs", plan_digest=preview["plan_digest"]
    )
    with pytest.raises(GatewayError, match="gateway_cli_command_denied"):
        _issue_cli_execution_capability(
            operation_id=operation["public_id"],
            plan_sha256=operation["plan_sha256"],
            command="bundle-install",
        )
    with service.vault_database.connect() as connection:
        connection.execute(
            "UPDATE account_gateway_operations SET immutable_plan_json=? WHERE public_id=?",
            ('{"changed":true}', operation["public_id"]),
        )
    with pytest.raises(GatewayError, match="gateway_plan_changed"):
        service.request_execution_approval(operation["public_id"])
    assert downloads.calls == 0


def test_persistent_network_setting_and_web_execution_remain_blocked(tmp_path: Path) -> None:
    paths = ElyndraPaths(
        tmp_path / "config", tmp_path / "data", tmp_path / "state", tmp_path / "cache"
    )
    paths.ensure()
    config_path = write_default_config(paths, owner_name="Test")
    config_path.write_text(
        config_path.read_text().replace("network_allowed = false", "network_allowed = true")
    )
    with pytest.raises(ConfigError, match="no admite acceso de red"):
        AppConfig.load(paths)

    source = inspect.getsource(web_server.ElyndraWebService.online_write)
    assert "execute_download" not in source
    assert "request_execution_approval" not in source
    assert "plan-download" not in source
