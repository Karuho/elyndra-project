from __future__ import annotations

import re
from typing import Any

_HOSTNAME = re.compile(r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\Z")


class GatewayError(RuntimeError):
    """A stable, user-safe controlled gateway failure."""

    def __init__(self, code: str, *, context: dict[str, Any] | None = None) -> None:
        self.code = code
        self.context = self._safe_context(context or {})
        super().__init__(code)

    @staticmethod
    def _safe_context(context: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        status = context.get("http_status")
        if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
            safe["http_status"] = status
            safe["http_status_class"] = f"{status // 100}xx"
        host = context.get("final_host")
        if isinstance(host, str) and _HOSTNAME.fullmatch(host.casefold().rstrip(".")):
            safe["final_host"] = host.casefold().rstrip(".")
        count = context.get("redirect_count")
        if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 20:
            safe["redirect_count"] = count
        hosts = context.get("redirect_hosts")
        if isinstance(hosts, (list, tuple)):
            clean_hosts: list[str] = []
            for value in hosts[:20]:
                if not isinstance(value, str):
                    continue
                clean = value.casefold().rstrip(".")
                if _HOSTNAME.fullmatch(clean) and clean not in clean_hosts:
                    clean_hosts.append(clean)
            safe["redirect_hosts"] = clean_hosts
        if isinstance(context.get("resumable"), bool):
            safe["resumable"] = context["resumable"]
        return safe
