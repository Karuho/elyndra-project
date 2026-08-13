from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from elyndra.online_gateway.errors import GatewayError
from elyndra.online_gateway.models import GatewayLimits


class OnlineGatewayPolicy:
    def __init__(self, limits: GatewayLimits | None = None) -> None:
        self.limits = limits or GatewayLimits()

    def validate_url(self, url: str) -> str:
        parsed = urlsplit(url)
        if parsed.scheme != "https":
            raise GatewayError("url_scheme_rejected")
        if parsed.username or parsed.password:
            raise GatewayError("url_credentials_rejected")
        if not parsed.hostname or parsed.fragment:
            raise GatewayError("url_host_rejected")
        try:
            port = parsed.port
        except ValueError as exc:
            raise GatewayError("url_port_rejected") from exc
        if port not in {None, 443}:
            raise GatewayError("url_port_rejected")
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            raise GatewayError("url_host_rejected")
        if parsed.path.lower().endswith("/latest") or "/latest/" in parsed.path.lower():
            raise GatewayError("gateway_latest_forbidden")
        return parsed.hostname.lower()

    def require_authority(
        self, *, global_enabled: bool, account_enabled: bool, has_plan: bool
    ) -> None:
        if not global_enabled:
            raise GatewayError("gateway_disabled_global")
        if not account_enabled:
            raise GatewayError("gateway_disabled_account")
        if not has_plan:
            raise GatewayError("gateway_plan_required")

    @staticmethod
    def can_resume(
        *, etag: str | None, response_etag: str | None, status: int, range_ok: bool
    ) -> bool:
        return bool(
            etag
            and not etag.startswith("W/")
            and etag == response_etag
            and status == 206
            and range_ok
        )
