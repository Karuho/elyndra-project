from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import socket
import ssl
from pathlib import Path

import pytest

from elyndra.audit import AuditRepository
from elyndra.db import Database
from elyndra.online_gateway.approvals import NetworkPermit
from elyndra.online_gateway.audit import GatewayAudit
from elyndra.online_gateway.downloads import DownloadManager
from elyndra.online_gateway.errors import GatewayError
from elyndra.online_gateway.models import GatewayLimits, RemoteArtifactDescriptor
from elyndra.online_gateway.operations import OnlineGatewayService
from elyndra.online_gateway.policy import OnlineGatewayPolicy
from elyndra.online_gateway.resolver import (
    ProductionResolver,
    ResolvedTarget,
    TestLoopbackResolver,
    is_global_address,
    normalize_hostname,
)
from elyndra.online_gateway.storage import GatewayStorage
from elyndra.online_gateway.transport import (
    GatewayTransport,
    SocketConnector,
    TransportRequest,
    TransportResponse,
)
from elyndra.paths import ElyndraPaths


class Duplex:
    def __init__(self, response: bytes) -> None:
        self.response = io.BytesIO(response)
        self.request = bytearray()

    def read(self, size: int = -1) -> bytes:
        return self.response.read(size)

    def write(self, value: bytes) -> int:
        self.request.extend(value)
        return len(value)

    def close(self) -> None:
        self.response.close()


class FakeConnector:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = responses
        self.streams: list[Duplex] = []

    def connect(self, *args: object, **kwargs: object) -> Duplex:
        stream = Duplex(self.responses.pop(0))
        self.streams.append(stream)
        return stream


class FakeTransport:
    def __init__(self, response: TransportResponse) -> None:
        self.response = response
        self.requests: list[TransportRequest] = []

    def request(self, request: TransportRequest) -> TransportResponse:
        self.requests.append(request)
        return self.response


class UnreadBody(io.BytesIO):
    def read(self, size: int = -1) -> bytes:
        raise AssertionError("a rejected response body was read")


class FakeRawSocket:
    def __init__(self, connect_error: OSError | None = None) -> None:
        self.connect_error = connect_error
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, address: tuple[str, int]) -> None:
        if self.connect_error is not None:
            raise self.connect_error

    def close(self) -> None:
        self.closed = True


class FakeTlsSocket:
    def __init__(self, peer: str) -> None:
        self.peer = peer
        self.closed = False

    def getpeername(self) -> tuple[str, int]:
        return self.peer, 443

    def makefile(self, mode: str, buffering: int = 0) -> io.BytesIO:
        return io.BytesIO()

    def close(self) -> None:
        self.closed = True


def _paths(root: Path) -> ElyndraPaths:
    return ElyndraPaths(root / "config", root / "data", root / "state", root / "cache")


def _database(path: Path, role: str) -> Database:
    database = Database(path, role=role)
    database.migrate()
    return database


def _descriptor(data: bytes, *, sha256: str | None = None) -> RemoteArtifactDescriptor:
    digest = sha256 or hashlib.sha256(data).hexdigest()
    return RemoteArtifactDescriptor(
        source_id="test-source",
        artifact_key=f"test-source:manifest:{digest}",
        artifact_name="manifest.json",
        manifest_url="https://gateway.test/manifest.json?private=absent",
        expected_size=len(data),
        expected_sha256=digest,
        descriptor_sha256="d" * 64,
        hostname="gateway.test",
        metadata={},
    )


def _job(database: Database, descriptor: RemoteArtifactDescriptor, job_id: str = "job") -> None:
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO online_gateway_download_jobs(
            public_id, artifact_key, state, bytes_written, expected_size, updated_at
            ) VALUES (?, ?, 'approved', 0, ?, '2026-08-05')""",
            (job_id, descriptor.artifact_key, descriptor.expected_size),
        )


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "127.0.0.1",
        "10.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "240.0.0.1",
        "::1",
        "fc00::1",
        "::ffff:10.0.0.1",
    ],
)
def test_non_global_addresses_are_rejected(address: str) -> None:
    assert not is_global_address(address)


def test_resolver_rejects_empty_mixed_and_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = ProductionResolver()
    with pytest.raises(GatewayError, match="url_host_rejected"):
        resolver.resolve("127.0.0.1", 443)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [])
    with pytest.raises(GatewayError, match="dns_resolution_failed"):
        resolver.resolve("empty.test", 443)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
        ],
    )
    with pytest.raises(GatewayError, match="dns_non_global_address"):
        resolver.resolve("mixed.test", 443)


def test_hostname_normalization() -> None:
    assert normalize_hostname("ExAmPle.COM.") == "example.com"


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://gateway.test/a", "url_scheme_rejected"),
        ("https://gateway.test:444/a", "url_port_rejected"),
        ("https://user@gateway.test/a", "url_credentials_rejected"),
        ("https:///a", "url_host_rejected"),
        ("https://127.0.0.1/a", "url_host_rejected"),
    ],
)
def test_production_url_policy(url: str, code: str) -> None:
    with pytest.raises(GatewayError, match=code):
        OnlineGatewayPolicy().validate_url(url)


def test_transport_full_response_and_fixed_headers() -> None:
    connector = FakeConnector([b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabc"])
    transport = GatewayTransport(
        resolver=TestLoopbackResolver({"gateway.test": ("127.0.0.1",)}),
        connector=connector,
    )
    with transport.request(TransportRequest("https://gateway.test/a")) as response:
        assert response.status == 200
        assert response.read(3) == b"abc"
    sent = bytes(connector.streams[0].request)
    assert b"Accept-Encoding: identity\r\n" in sent
    assert b"Connection: close\r\n" in sent
    assert b"Proxy" not in sent and b"Cookie" not in sent


@pytest.mark.parametrize("redirect_status", [301, 302, 303, 307, 308])
def test_transport_records_only_sanitized_redirect_hosts(redirect_status: int) -> None:
    connector = FakeConnector(
        [
            (
                f"HTTP/1.1 {redirect_status} Redirect\r\n"
                "Location: https://cdn.test/private?token=secret\r\n\r\n"
            ).encode(),
            b"HTTP/1.1 404 Nope\r\nContent-Length: 6\r\n\r\nsecret",
        ]
    )
    transport = GatewayTransport(
        resolver=TestLoopbackResolver(
            {"gateway.test": ("127.0.0.1",), "cdn.test": ("127.0.0.1",)}
        ),
        connector=connector,
    )
    with transport.request(
        TransportRequest(
            "https://gateway.test/start", allowed_redirect_hosts=("cdn.test",)
        )
    ) as response:
        assert response.final_host == "cdn.test"
        assert response.redirect_count == 1
        assert response.redirect_hosts == ("cdn.test",)
        assert "private" not in repr(response.redirect_hosts)
        assert "127.0.0.1" not in repr(response.redirect_hosts)


@pytest.mark.parametrize("status", [206, 304, 400, 401, 403, 404, 429, 500, 503])
def test_initial_http_rejection_is_terminal_sanitized_and_cleans_empty_partial(
    tmp_path: Path, status: int
) -> None:
    descriptor = _descriptor(b"secret")
    root = _database(tmp_path / "root.sqlite3", "root")
    storage = GatewayStorage(_paths(tmp_path / "runtime"))
    response = TransportResponse(
        UnreadBody(b"secret"),
        status,
        {"content-length": "6", "x-secret": "never-store"},
        descriptor.manifest_url,
        final_host="gateway.test",
        redirect_count=1,
        redirect_hosts=("cdn.test",),
    )
    manager = DownloadManager(
        database=root,
        storage=storage,
        transport=FakeTransport(response),  # type: ignore[arg-type]
    )
    _job(root, descriptor)
    with pytest.raises(GatewayError, match="response_status_rejected") as caught:
        manager.execute(
            job_id="job",
            account_id="a",
            operation_id="o",
            plan_sha256="p",
            permit=NetworkPermit("a", "o", "p"),
            descriptor=descriptor,
            allowed_redirect_hosts=("gateway.test",),
        )
    assert caught.value.context == {
        "http_status": status,
        "http_status_class": f"{status // 100}xx",
        "final_host": "gateway.test",
        "redirect_count": 1,
        "redirect_hosts": ["cdn.test"],
        "resumable": False,
    }
    assert "secret" not in repr(caught.value.context)
    assert not storage.partial_path(descriptor.artifact_key).exists()
    assert not storage.metadata_path(descriptor.artifact_key).exists()
    assert not any(storage.cache.iterdir())
    assert not any(storage.quarantine.iterdir())
    with root.connect() as connection:
        job = connection.execute(
            "SELECT state, bytes_written, completed_at FROM online_gateway_download_jobs "
            "WHERE public_id='job'"
        ).fetchone()
    assert tuple(job[:2]) == ("failed", 0)
    assert job[2] is not None


def test_gateway_error_rejects_unallowlisted_remote_context() -> None:
    error = GatewayError(
        "response_status_rejected",
        context={
            "http_status": 404,
            "final_host": "EXAMPLE.test.",
            "redirect_count": 1,
            "redirect_hosts": ["cdn.test", "cdn.test", "https://bad.test/path"],
            "body": "secret",
            "headers": {"authorization": "secret"},
            "url": "https://example.test/private?token=secret",
            "ip": "127.0.0.1",
            "etag": '"secret"',
        },
    )
    assert error.context == {
        "http_status": 404,
        "http_status_class": "4xx",
        "final_host": "example.test",
        "redirect_count": 1,
        "redirect_hosts": ["cdn.test"],
    }


def test_gateway_audit_persists_only_sanitized_http_context(tmp_path: Path) -> None:
    database = _database(tmp_path / "root.sqlite3", "root")
    audit = GatewayAudit(AuditRepository(database))
    audit.record(
        account_id="account",
        outcome="failed",
        details={
            "operation_id": "operation",
            "error_code": "response_status_rejected",
            "http_status": 503,
            "final_host": "GATEWAY.test.",
            "redirect_count": 1,
            "redirect_hosts": ["cdn.test", "https://bad.test/private"],
            "resumable": False,
            "body": "secret",
            "headers": {"authorization": "secret"},
            "url": "https://gateway.test/private?token=secret",
            "ip": "127.0.0.1",
            "etag": '"secret"',
        },
    )
    details = json.loads(audit.list(1)[0]["details_json"])
    assert details == {
        "error_code": "response_status_rejected",
        "final_host": "gateway.test",
        "http_status": 503,
        "http_status_class": "5xx",
        "operation_id": "operation",
        "redirect_count": 1,
        "redirect_hosts": ["cdn.test"],
        "resumable": False,
    }


def test_connector_accepts_trusted_tls_and_pinned_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = FakeRawSocket()
    tls = FakeTlsSocket("127.0.0.1")

    class TrustedContext:
        def wrap_socket(self, value: object, *, server_hostname: str) -> FakeTlsSocket:
            assert server_hostname == "gateway.test"
            return tls

    monkeypatch.setattr("elyndra.online_gateway.transport.socket.socket", lambda *args: raw)
    stream = SocketConnector().connect(
        ResolvedTarget("gateway.test", 443, ("127.0.0.1",)),
        hostname="gateway.test",
        timeout=10,
        context=TrustedContext(),  # type: ignore[arg-type]
    )
    stream.close()


@pytest.mark.parametrize("reason", ["untrusted certificate", "hostname mismatch"])
def test_connector_rejects_tls_validation_failure(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    raw = FakeRawSocket()

    class RejectingContext:
        def wrap_socket(self, value: object, *, server_hostname: str) -> None:
            raise ssl.SSLCertVerificationError(reason)

    monkeypatch.setattr("elyndra.online_gateway.transport.socket.socket", lambda *args: raw)
    with pytest.raises(GatewayError, match="tls_validation_failed"):
        SocketConnector().connect(
            ResolvedTarget("gateway.test", 443, ("127.0.0.1",)),
            hostname="gateway.test",
            timeout=10,
            context=RejectingContext(),  # type: ignore[arg-type]
        )


def test_connector_rejects_rebinding_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = FakeRawSocket()

    class ReboundContext:
        def wrap_socket(self, value: object, *, server_hostname: str) -> FakeTlsSocket:
            return FakeTlsSocket("127.0.0.2")

    monkeypatch.setattr("elyndra.online_gateway.transport.socket.socket", lambda *args: raw)
    with pytest.raises(GatewayError, match="dns_rebinding_detected"):
        SocketConnector().connect(
            ResolvedTarget("gateway.test", 443, ("127.0.0.1",)),
            hostname="gateway.test",
            timeout=10,
            context=ReboundContext(),  # type: ignore[arg-type]
        )


def test_transport_response_maps_read_timeout() -> None:
    class TimedOutStream(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            raise TimeoutError

    response = TransportResponse(TimedOutStream(), 200, {}, "https://gateway.test/a")
    with pytest.raises(GatewayError, match="transport_read_timeout"):
        response.read(1)


def test_relative_redirect_is_revalidated() -> None:
    connector = FakeConnector(
        [
            b"HTTP/1.1 302 Found\r\nLocation: /final\r\nContent-Length: 0\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n",
        ]
    )
    transport = GatewayTransport(
        resolver=TestLoopbackResolver({"gateway.test": ("127.0.0.1",)}),
        connector=connector,
    )
    response = transport.request(
        TransportRequest("https://gateway.test/start", allowed_redirect_hosts=("gateway.test",))
    )
    assert response.status == 200
    response.close()
    assert b"GET /final HTTP/1.1" in bytes(connector.streams[1].request)


@pytest.mark.parametrize(
    ("location", "code"),
    [
        ("https://other.test/a", "redirect_host_rejected"),
        ("http://gateway.test/a", "url_scheme_rejected"),
        ("https://user@gateway.test/a", "url_credentials_rejected"),
    ],
)
def test_redirect_rejections(location: str, code: str) -> None:
    connector = FakeConnector(
        [f"HTTP/1.1 302 Found\r\nLocation: {location}\r\nContent-Length: 0\r\n\r\n".encode()]
    )
    transport = GatewayTransport(
        resolver=TestLoopbackResolver({"gateway.test": ("127.0.0.1",)}),
        connector=connector,
    )
    with pytest.raises(GatewayError, match=code):
        transport.request(
            TransportRequest("https://gateway.test/start", allowed_redirect_hosts=("gateway.test",))
        )


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        (b"Content-Encoding: gzip\r\nContent-Length: 0", "content_encoding_rejected"),
        (b"Transfer-Encoding: chunked\r\n", "transfer_encoding_rejected"),
        (b" folded: bad\r\nContent-Length: 0", "response_header_invalid"),
        (b"Bad Header: x\r\nContent-Length: 0", "response_header_invalid"),
    ],
)
def test_response_header_rejections(headers: bytes, code: str) -> None:
    connector = FakeConnector([b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n\r\n"])
    transport = GatewayTransport(
        resolver=TestLoopbackResolver({"gateway.test": ("127.0.0.1",)}),
        connector=connector,
    )
    with pytest.raises(GatewayError, match=code):
        transport.request(TransportRequest("https://gateway.test/a"))


def test_header_count_and_size_limits() -> None:
    limits = GatewayLimits(header_count=1, header_bytes=10)
    transport = GatewayTransport(limits=limits)
    with pytest.raises(GatewayError, match="response_headers_too_large"):
        transport._read_headers(io.BytesIO(b"HTTP/1.1 200 OK\r\nA: 1\r\nB: 2\r\n\r\n"))
    with pytest.raises(GatewayError, match="response_header_invalid"):
        transport._read_headers(io.BytesIO(b"HTTP/1.1 200 OK\r\nLong: 123456789\r\n\r\n"))


def test_download_streams_verifies_and_reuses_cache(tmp_path: Path) -> None:
    data = b"abcdef"
    descriptor = _descriptor(data)
    root = _database(tmp_path / "root.sqlite3", "root")
    storage = GatewayStorage(_paths(tmp_path / "runtime"))
    response = TransportResponse(
        io.BytesIO(data),
        200,
        {"content-length": str(len(data)), "etag": '"v1"'},
        descriptor.manifest_url,
    )
    manager = DownloadManager(
        database=root,
        storage=storage,
        transport=FakeTransport(response),  # type: ignore[arg-type]
    )
    _job(root, descriptor)
    result = manager.execute(
        job_id="job",
        account_id="a",
        operation_id="o",
        plan_sha256="p",
        permit=NetworkPermit("a", "o", "p"),
        descriptor=descriptor,
        allowed_redirect_hosts=("gateway.test",),
    )
    assert result["state"] == "verified"
    cache = storage.cache_path(descriptor.artifact_key)
    assert cache.read_bytes() == data
    assert cache.stat().st_mode & 0o777 == 0o600
    assert manager.verify_cache(descriptor)["state"] == "verified"


@pytest.mark.parametrize(
    ("headers", "body", "code"),
    [
        ({}, b"abc", "content_length_missing"),
        ({"content-length": "2"}, b"abc", "content_length_mismatch"),
        ({"content-length": "4"}, b"abc", "content_length_mismatch"),
    ],
)
def test_download_rejects_length_errors(
    tmp_path: Path, headers: dict[str, str], body: bytes, code: str
) -> None:
    descriptor = _descriptor(b"abc")
    root = _database(tmp_path / "root.sqlite3", "root")
    storage = GatewayStorage(_paths(tmp_path / "runtime"))
    manager = DownloadManager(
        database=root,
        storage=storage,
        transport=FakeTransport(
            TransportResponse(io.BytesIO(body), 200, headers, descriptor.manifest_url)
        ),  # type: ignore[arg-type]
    )
    _job(root, descriptor)
    with pytest.raises(GatewayError, match=code):
        manager.execute(
            job_id="job",
            account_id="a",
            operation_id="o",
            plan_sha256="p",
            permit=NetworkPermit("a", "o", "p"),
            descriptor=descriptor,
            allowed_redirect_hosts=("gateway.test",),
        )


def test_hash_mismatch_moves_file_to_quarantine(tmp_path: Path) -> None:
    data = b"wrong"
    descriptor = _descriptor(data, sha256="0" * 64)
    root = _database(tmp_path / "root.sqlite3", "root")
    storage = GatewayStorage(_paths(tmp_path / "runtime"))
    manager = DownloadManager(
        database=root,
        storage=storage,
        transport=FakeTransport(
            TransportResponse(
                io.BytesIO(data), 200, {"content-length": "5"}, descriptor.manifest_url
            )
        ),  # type: ignore[arg-type]
    )
    _job(root, descriptor)
    with pytest.raises(GatewayError, match="hash_mismatch"):
        manager.execute(
            job_id="job",
            account_id="a",
            operation_id="o",
            plan_sha256="p",
            permit=NetworkPermit("a", "o", "p"),
            descriptor=descriptor,
            allowed_redirect_hosts=("gateway.test",),
        )
    assert storage.quarantine_path(descriptor.artifact_key).read_bytes() == data
    assert not storage.partial_path(descriptor.artifact_key).exists()


def test_resume_requires_exact_strong_validator_and_range(tmp_path: Path) -> None:
    data = b"abcdef"
    descriptor = _descriptor(data)
    root = _database(tmp_path / "root.sqlite3", "root")
    storage = GatewayStorage(_paths(tmp_path / "runtime"))
    partial = storage.partial_path(descriptor.artifact_key)
    partial.write_bytes(data[:3])
    partial.chmod(0o600)
    storage.write_metadata(
        descriptor.artifact_key,
        {
            "artifact_key": descriptor.artifact_key,
            "descriptor_sha256": descriptor.descriptor_sha256,
            "expected_sha256": descriptor.expected_sha256,
            "expected_size": 6,
            "bytes_written": 3,
            "strong_etag": '"v1"',
            "source_hostname": "gateway.test",
            "updated_at": "2026-08-05",
        },
    )
    response = TransportResponse(
        io.BytesIO(data[3:]),
        206,
        {"content-length": "3", "content-range": "bytes 3-5/6", "etag": '"v1"'},
        descriptor.manifest_url,
    )
    manager = DownloadManager(
        database=root,
        storage=storage,
        transport=FakeTransport(response),  # type: ignore[arg-type]
    )
    _job(root, descriptor)
    result = manager.execute(
        job_id="job",
        account_id="a",
        operation_id="o",
        plan_sha256="p",
        permit=NetworkPermit("a", "o", "p"),
        descriptor=descriptor,
        allowed_redirect_hosts=("gateway.test",),
        resume=True,
    )
    assert result["state"] == "verified"


def test_storage_rejects_symlink_and_hardlink(tmp_path: Path) -> None:
    storage = GatewayStorage(_paths(tmp_path / "runtime"))
    target = storage.partial / "target"
    target.write_bytes(b"x")
    target.chmod(0o600)
    symlink = storage.partial / "link"
    symlink.symlink_to(target)
    with pytest.raises(GatewayError, match="storage_unsafe"):
        storage.safe_existing(symlink)
    hardlink = storage.partial / "hard"
    os.link(target, hardlink)
    with pytest.raises(GatewayError, match="storage_unsafe"):
        storage.safe_existing(target)


def test_restart_marks_active_job_interrupted_without_transport(tmp_path: Path) -> None:
    descriptor = _descriptor(b"x")
    root = _database(tmp_path / "root.sqlite3", "root")
    with root.connect() as connection:
        connection.execute(
            """INSERT INTO online_gateway_download_jobs(
            public_id, artifact_key, state, bytes_written, expected_size, updated_at
            ) VALUES('job', ?, 'downloading', 0, 1, '2026-08-05')""",
            (descriptor.artifact_key,),
        )

    class NeverTransport:
        def request(self, request: TransportRequest) -> TransportResponse:
            raise AssertionError("startup opened transport")

    DownloadManager(
        database=root,
        storage=GatewayStorage(_paths(tmp_path / "runtime")),
        transport=NeverTransport(),  # type: ignore[arg-type]
    )
    with root.connect() as connection:
        row = connection.execute(
            "SELECT state FROM online_gateway_download_jobs WHERE public_id='job'"
        ).fetchone()
    assert row[0] == "interrupted"


def test_global_lock_is_nonblocking_and_private(tmp_path: Path) -> None:
    root = _database(tmp_path / "root.sqlite3", "root")
    storage = GatewayStorage(_paths(tmp_path / "runtime"))
    manager = DownloadManager(
        database=root,
        storage=storage,
        transport=FakeTransport(TransportResponse(io.BytesIO(), 200, {}, "")),  # type: ignore[arg-type]
    )
    manager.lock_path.touch(mode=0o600)
    with manager.lock_path.open("r+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (
            pytest.raises(GatewayError, match="gateway_download_busy"),
            manager._global_lock(),
        ):
            pass
    assert manager.lock_path.stat().st_mode & 0o777 == 0o600


def test_cancelled_download_never_promotes(tmp_path: Path) -> None:
    data = b"abcdef"
    descriptor = _descriptor(data)
    root = _database(tmp_path / "root.sqlite3", "root")
    storage = GatewayStorage(_paths(tmp_path / "runtime"))
    manager = DownloadManager(
        database=root,
        storage=storage,
        transport=FakeTransport(
            TransportResponse(io.BytesIO(data), 200, {"content-length": "6", "etag": '"v1"'}, "")
        ),  # type: ignore[arg-type]
    )
    _job(root, descriptor)
    manager.request_cancel("job")
    with pytest.raises(GatewayError, match="download_cancelled"):
        manager.execute(
            job_id="job",
            account_id="a",
            operation_id="o",
            plan_sha256="p",
            permit=NetworkPermit("a", "o", "p"),
            descriptor=descriptor,
            allowed_redirect_hosts=("gateway.test",),
        )
    assert not storage.cache_path(descriptor.artifact_key).exists()


def test_corrupt_cache_is_quarantined_without_network(tmp_path: Path) -> None:
    descriptor = _descriptor(b"correct")
    root = _database(tmp_path / "root.sqlite3", "root")
    storage = GatewayStorage(_paths(tmp_path / "runtime"))
    cache = storage.cache_path(descriptor.artifact_key)
    cache.write_bytes(b"corrupt")
    cache.chmod(0o600)
    manager = DownloadManager(
        database=root,
        storage=storage,
        transport=FakeTransport(TransportResponse(io.BytesIO(), 200, {}, "")),  # type: ignore[arg-type]
    )
    with pytest.raises(GatewayError, match="cache_corrupt"):
        manager.verify_cache(descriptor)
    assert storage.quarantine_path(descriptor.artifact_key, "cache").exists()


def test_application_service_does_not_publish_transport_or_manager(tmp_path: Path) -> None:
    root = _database(tmp_path / "root.sqlite3", "root")
    vault = _database(tmp_path / "vault.sqlite3", "vault")
    service = OnlineGatewayService(
        root_database=root,
        vault_database=vault,
        account_id="a",
        global_enabled=False,
        audit=GatewayAudit(AuditRepository(vault)),
    )
    assert "transport" not in dir(service)
    assert "downloads" not in dir(service)
