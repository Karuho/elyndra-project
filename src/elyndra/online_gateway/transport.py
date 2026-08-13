from __future__ import annotations

import re
import socket
import ssl
import time
from dataclasses import dataclass
from types import TracebackType
from typing import BinaryIO, Protocol
from urllib.parse import urljoin, urlsplit

from elyndra.online_gateway.errors import GatewayError
from elyndra.online_gateway.models import GatewayLimits
from elyndra.online_gateway.resolver import ProductionResolver, ResolvedTarget, Resolver

_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_REDIRECTS = {301, 302, 303, 307, 308}


class Connector(Protocol):
    def connect(
        self,
        target: ResolvedTarget,
        *,
        hostname: str,
        timeout: float,
        context: ssl.SSLContext,
    ) -> BinaryIO: ...


class SocketConnector:
    def connect(
        self,
        target: ResolvedTarget,
        *,
        hostname: str,
        timeout: float,
        context: ssl.SSLContext,
    ) -> BinaryIO:
        last_error: OSError | None = None
        for address in target.addresses:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            raw = socket.socket(family, socket.SOCK_STREAM)
            raw.settimeout(timeout)
            try:
                raw.connect((address, target.port))
                tls = context.wrap_socket(raw, server_hostname=hostname)
                peer = str(tls.getpeername()[0])
                if peer not in target.addresses:
                    tls.close()
                    raise GatewayError("dns_rebinding_detected")
                return tls.makefile("rwb", buffering=0)
            except GatewayError:
                raw.close()
                raise
            except (OSError, ssl.SSLError) as exc:
                raw.close()
                last_error = exc
        if isinstance(last_error, TimeoutError):
            raise GatewayError("transport_connect_timeout") from last_error
        raise GatewayError("tls_validation_failed") from last_error


@dataclass(frozen=True, slots=True)
class TransportRequest:
    url: str
    method: str = "GET"
    range_start: int | None = None
    strong_etag: str | None = None
    allowed_redirect_hosts: tuple[str, ...] = ()


class TransportResponse:
    def __init__(
        self,
        stream: BinaryIO,
        status: int,
        headers: dict[str, str],
        url: str,
        *,
        final_host: str | None = None,
        redirect_count: int = 0,
        redirect_hosts: tuple[str, ...] = (),
    ) -> None:
        self._stream = stream
        self.status = status
        self.headers = headers
        self.url = url
        self.final_host = final_host or str(urlsplit(url).hostname or "").casefold()
        self.redirect_count = redirect_count
        self.redirect_hosts = redirect_hosts

    def read(self, size: int) -> bytes:
        try:
            return self._stream.read(size)
        except TimeoutError as exc:
            raise GatewayError("transport_read_timeout") from exc

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> TransportResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class GatewayTransport:
    """Pinned HTTPS/1.1 transport. No proxy, cookie jar or ambient session exists."""

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        connector: Connector | None = None,
        ssl_context: ssl.SSLContext | None = None,
        limits: GatewayLimits | None = None,
        monotonic: object = time.monotonic,
    ) -> None:
        self.resolver = resolver or ProductionResolver()
        self.connector = connector or SocketConnector()
        self.limits = limits or GatewayLimits()
        self.context = ssl_context or ssl.create_default_context()
        self.context.check_hostname = True
        self.context.verify_mode = ssl.CERT_REQUIRED
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._monotonic = monotonic

    def request(self, request: TransportRequest) -> TransportResponse:
        if request.method not in {"HEAD", "GET"}:
            raise GatewayError("response_status_rejected")
        deadline = self._monotonic() + self.limits.operation_timeout_seconds
        current = request.url
        allowed = {host.casefold() for host in request.allowed_redirect_hosts}
        redirect_hosts: list[str] = []
        for redirect_count in range(self.limits.redirects + 1):
            response = self._single(current, request, deadline)
            if response.status not in _REDIRECTS:
                response.redirect_count = redirect_count
                response.redirect_hosts = tuple(redirect_hosts)
                response.final_host = str(urlsplit(current).hostname or "").casefold()
                return response
            location = response.headers.get("location")
            response.close()
            if redirect_count >= self.limits.redirects:
                raise GatewayError("redirect_limit_exceeded")
            if not location:
                raise GatewayError("response_header_invalid")
            current = urljoin(current, location)
            parsed = self._validate_url(current)
            host = str(parsed.hostname).casefold()
            if host not in allowed:
                raise GatewayError("redirect_host_rejected")
            if host not in redirect_hosts:
                redirect_hosts.append(host)
        raise GatewayError("redirect_limit_exceeded")

    def _single(self, url: str, request: TransportRequest, deadline: float) -> TransportResponse:
        parsed = self._validate_url(url)
        if self._monotonic() >= deadline:
            raise GatewayError("transport_operation_timeout")
        target = self.resolver.resolve(str(parsed.hostname), 443)
        stream = self.connector.connect(
            target,
            hostname=str(parsed.hostname),
            timeout=min(
                self.limits.connect_timeout_seconds,
                max(0.001, deadline - self._monotonic()),
            ),
            context=self.context,
        )
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        headers = [
            f"Host: {parsed.hostname}",
            "User-Agent: Elyndra-Online-Gateway/0.8.9",
            "Accept: application/octet-stream, application/json",
            "Accept-Encoding: identity",
            "Connection: close",
        ]
        if request.range_start is not None:
            if not request.strong_etag or request.strong_etag.startswith("W/"):
                stream.close()
                raise GatewayError("resume_etag_required")
            headers.extend(
                (f"Range: bytes={request.range_start}-", f"If-Range: {request.strong_etag}")
            )
        payload = f"{request.method} {path} HTTP/1.1\r\n" + "\r\n".join(headers) + "\r\n\r\n"
        stream.write(payload.encode("ascii"))
        status, received = self._read_headers(stream)
        self._validate_response_headers(received)
        return TransportResponse(stream, status, received, url)

    @staticmethod
    def _validate_url(url: str):
        parsed = urlsplit(url)
        if parsed.scheme != "https":
            raise GatewayError("url_scheme_rejected")
        if parsed.username or parsed.password:
            raise GatewayError("url_credentials_rejected")
        if not parsed.hostname:
            raise GatewayError("url_host_rejected")
        try:
            port = parsed.port
        except ValueError as exc:
            raise GatewayError("url_port_rejected") from exc
        if port not in {None, 443}:
            raise GatewayError("url_port_rejected")
        return parsed

    def _read_headers(self, stream: BinaryIO) -> tuple[int, dict[str, str]]:
        data = bytearray()
        while not data.endswith(b"\r\n\r\n"):
            chunk = stream.read(1)
            if not chunk:
                raise GatewayError("response_header_invalid")
            data.extend(chunk)
            if len(data) > self.limits.total_header_bytes:
                raise GatewayError("response_headers_too_large")
        lines = bytes(data[:-4]).split(b"\r\n")
        if not lines or len(lines) - 1 > self.limits.header_count:
            raise GatewayError("response_headers_too_large")
        try:
            status = int(lines[0].split(b" ", 2)[1])
        except (IndexError, ValueError) as exc:
            raise GatewayError("response_header_invalid") from exc
        headers: dict[str, str] = {}
        for raw in lines[1:]:
            if len(raw) > self.limits.header_bytes or raw.startswith((b" ", b"\t")):
                raise GatewayError("response_header_invalid")
            try:
                name, value = raw.decode("iso-8859-1").split(":", 1)
            except ValueError as exc:
                raise GatewayError("response_header_invalid") from exc
            if not _HEADER_NAME.fullmatch(name):
                raise GatewayError("response_header_invalid")
            key = name.casefold()
            if key != "set-cookie":
                headers[key] = value.strip()
        return status, headers

    @staticmethod
    def _validate_response_headers(headers: dict[str, str]) -> None:
        if "transfer-encoding" in headers:
            raise GatewayError("transfer_encoding_rejected")
        if headers.get("content-encoding", "identity").casefold() != "identity":
            raise GatewayError("content_encoding_rejected")
