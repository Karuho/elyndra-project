from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Protocol

from elyndra.online_gateway.errors import GatewayError


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    hostname: str
    port: int
    addresses: tuple[str, ...]


class Resolver(Protocol):
    def resolve(self, hostname: str, port: int) -> ResolvedTarget: ...


def normalize_hostname(hostname: str) -> str:
    clean = hostname.strip().rstrip(".").casefold()
    if not clean or len(clean) > 253 or any(len(label) > 63 for label in clean.split(".")):
        raise GatewayError("url_host_rejected")
    try:
        return clean.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise GatewayError("url_host_rejected") from exc


def is_global_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return bool(
        address.is_global
        and not address.is_unspecified
        and not address.is_loopback
        and not address.is_private
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
    )


class ProductionResolver:
    def resolve(self, hostname: str, port: int) -> ResolvedTarget:
        normalized = normalize_hostname(hostname)
        try:
            ipaddress.ip_address(normalized)
        except ValueError:
            pass
        else:
            raise GatewayError("url_host_rejected")
        try:
            answers = socket.getaddrinfo(
                normalized,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except OSError as exc:
            raise GatewayError("dns_resolution_failed") from exc
        addresses = tuple(dict.fromkeys(str(answer[4][0]) for answer in answers))
        if not addresses:
            raise GatewayError("dns_resolution_failed")
        if not all(is_global_address(address) for address in addresses):
            raise GatewayError("dns_non_global_address")
        return ResolvedTarget(normalized, port, addresses)


class TestLoopbackResolver:
    """Explicit test dependency; application configuration cannot construct it."""

    __test__ = False

    def __init__(self, mapping: dict[str, tuple[str, ...]]) -> None:
        self.mapping = mapping

    def resolve(self, hostname: str, port: int) -> ResolvedTarget:
        normalized = normalize_hostname(hostname)
        addresses = self.mapping.get(normalized, ())
        if not addresses:
            raise GatewayError("dns_resolution_failed")
        return ResolvedTarget(normalized, port, tuple(addresses))
