from __future__ import annotations

import fcntl
import hashlib
import hmac
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.db import Database
from elyndra.online_gateway.approvals import NetworkPermit
from elyndra.online_gateway.errors import GatewayError
from elyndra.online_gateway.models import GatewayLimits, RemoteArtifactDescriptor
from elyndra.online_gateway.storage import GatewayStorage
from elyndra.online_gateway.transport import GatewayTransport, TransportRequest


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DownloadManager:
    """Foreground coordinator. Transport is private and never leaves this composition."""

    def __init__(
        self,
        *,
        database: Database,
        storage: GatewayStorage,
        transport: GatewayTransport,
        limits: GatewayLimits | None = None,
        monotonic: object = time.monotonic,
    ) -> None:
        self.database = database
        self.storage = storage
        self._transport = transport
        self.limits = limits or GatewayLimits()
        self._monotonic = monotonic
        self.lock_path = storage.root / "download.lock"
        self.recover_interrupted()

    def recover_interrupted(self) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE online_gateway_download_jobs
                SET state='interrupted', error_code='download_interrupted', updated_at=?
                WHERE state IN ('connecting','downloading','verifying')""",
                (_now(),),
            )

    def execute(
        self,
        *,
        job_id: str,
        account_id: str,
        operation_id: str,
        plan_sha256: str,
        permit: NetworkPermit,
        descriptor: RemoteArtifactDescriptor,
        allowed_redirect_hosts: tuple[str, ...],
        resume: bool = False,
    ) -> dict[str, Any]:
        permit.consume(account_id=account_id, operation_id=operation_id, plan_sha256=plan_sha256)
        if descriptor.expected_size > self.limits.artifact_bytes:
            raise GatewayError("artifact_size_limit_exceeded")
        if descriptor.expected_size > self.limits.operation_bytes:
            raise GatewayError("operation_size_limit_exceeded")
        with self._global_lock():
            return self._stream(
                job_id=job_id,
                descriptor=descriptor,
                allowed_redirect_hosts=allowed_redirect_hosts,
                resume=resume,
            )

    def _stream(
        self,
        *,
        job_id: str,
        descriptor: RemoteArtifactDescriptor,
        allowed_redirect_hosts: tuple[str, ...],
        resume: bool,
    ) -> dict[str, Any]:
        partial = self.storage.partial_path(descriptor.artifact_key)
        offset = 0
        etag: str | None = None
        digest = hashlib.sha256()
        if resume:
            metadata = self.storage.read_metadata(descriptor.artifact_key)
            self._validate_metadata(metadata, descriptor)
            info = self.storage.safe_existing(partial)
            offset = int(metadata["bytes_written"])
            if info.st_size != offset:
                raise GatewayError("resume_range_rejected")
            etag = str(metadata.get("strong_etag") or "")
            if not etag or etag.startswith("W/"):
                raise GatewayError("resume_etag_required")
            self._rehash(partial, digest)
            handle = self.storage.open_append(partial)
        else:
            if partial.exists() or partial.is_symlink():
                raise GatewayError("storage_unsafe")
            handle = self.storage.open_exclusive(partial)
        pending = descriptor.expected_size - offset
        if self.storage.available_bytes() < pending + self.limits.reserve_bytes:
            handle.close()
            raise GatewayError("disk_reserve_insufficient")
        self._set_job(job_id, "connecting", bytes_written=offset)
        request = TransportRequest(
            descriptor.manifest_url,
            range_start=offset if resume else None,
            strong_etag=etag,
            allowed_redirect_hosts=allowed_redirect_hosts,
        )
        started = self._monotonic()
        response_etag: str | None = etag
        try:
            with self._transport.request(request) as response, handle:
                response_etag = response.headers.get("etag")
                content_length = self._content_length(response.headers)
                if resume:
                    self._validate_resume(response, offset, pending, descriptor.expected_size, etag)
                elif response.status != 200:
                    raise GatewayError(
                        "response_status_rejected",
                        context={
                            "http_status": response.status,
                            "final_host": response.final_host,
                            "redirect_count": response.redirect_count,
                            "redirect_hosts": response.redirect_hosts,
                            "resumable": False,
                        },
                    )
                if content_length != pending:
                    raise GatewayError("content_length_mismatch")
                self.storage.write_metadata(
                    descriptor.artifact_key,
                    {
                        "artifact_key": descriptor.artifact_key,
                        "descriptor_sha256": descriptor.descriptor_sha256,
                        "expected_sha256": descriptor.expected_sha256,
                        "expected_size": descriptor.expected_size,
                        "bytes_written": offset,
                        "strong_etag": (
                            response_etag
                            if response_etag and not response_etag.startswith("W/")
                            else None
                        ),
                        "source_hostname": descriptor.hostname,
                        "updated_at": _now(),
                    },
                )
                self._set_job(job_id, "downloading", bytes_written=offset, etag=response_etag)
                written = offset
                while True:
                    if self._cancel_requested(job_id):
                        raise GatewayError("download_cancelled")
                    if self._monotonic() - started > self.limits.operation_timeout_seconds:
                        raise GatewayError("transport_operation_timeout")
                    read_size = min(
                        self.limits.chunk_bytes,
                        pending - (written - offset) + 1,
                    )
                    chunk = response.read(read_size)
                    if not chunk:
                        break
                    if written + len(chunk) > descriptor.expected_size:
                        raise GatewayError("content_length_mismatch")
                    handle.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    self._set_job(job_id, "downloading", bytes_written=written)
                handle.flush()
                os.fsync(handle.fileno())
            if written != descriptor.expected_size:
                raise GatewayError("content_length_mismatch")
            self._set_job(job_id, "verifying", bytes_written=written)
            observed = digest.hexdigest()
            if not hmac.compare_digest(observed, descriptor.expected_sha256):
                quarantine = self.storage.quarantine_path(descriptor.artifact_key)
                self.storage.promote(partial, quarantine)
                self._set_job(job_id, "quarantined", bytes_written=written, error="hash_mismatch")
                raise GatewayError("hash_mismatch")
            final = self.storage.cache_path(descriptor.artifact_key)
            self.storage.promote(partial, final)
            self._upsert_cache(descriptor, final, observed)
            self._set_job(job_id, "verified", bytes_written=written, completed=True)
            return {"state": "verified", "bytes_written": written, "sha256": observed}
        except GatewayError as exc:
            with suppress(OSError):
                if not handle.closed:
                    handle.flush()
                    os.fsync(handle.fileno())
                    handle.close()
            state = "cancelled" if exc.code == "download_cancelled" else "interrupted"
            if exc.code.startswith("resume_"):
                state = "resume_rejected"
            if exc.code == "hash_mismatch":
                state = "quarantined"
            if exc.code == "response_status_rejected":
                state = "failed"
                self._discard_empty_rejected_partial(
                    partial, descriptor.artifact_key, response_etag
                )
            if partial.exists() and state in {"interrupted", "cancelled", "resume_rejected"}:
                partial_size = partial.stat().st_size
                self.storage.write_metadata(
                    descriptor.artifact_key,
                    {
                        "artifact_key": descriptor.artifact_key,
                        "descriptor_sha256": descriptor.descriptor_sha256,
                        "expected_sha256": descriptor.expected_sha256,
                        "expected_size": descriptor.expected_size,
                        "bytes_written": partial_size,
                        "strong_etag": (
                            response_etag
                            if response_etag and not response_etag.startswith("W/")
                            else None
                        ),
                        "source_hostname": descriptor.hostname,
                        "updated_at": _now(),
                    },
                )
            self._set_job(
                job_id,
                state,
                bytes_written=partial.stat().st_size if partial.exists() else 0,
                error=exc.code,
                completed=state in {"cancelled", "quarantined", "failed"},
            )
            raise

    def _discard_empty_rejected_partial(
        self, partial: Path, artifact_key: str, response_etag: str | None
    ) -> None:
        if response_etag and not response_etag.startswith("W/"):
            return
        if partial.exists() or partial.is_symlink():
            info = self.storage.safe_existing(partial)
            if info.st_size != 0:
                return
            partial.unlink()
            self.storage.fsync_directory(partial.parent)
        metadata = self.storage.metadata_path(artifact_key)
        if metadata.exists() or metadata.is_symlink():
            self.storage.safe_existing(metadata)
            metadata.unlink()
            self.storage.fsync_directory(metadata.parent)

    def verify_cache(self, descriptor: RemoteArtifactDescriptor) -> dict[str, Any]:
        path = self.storage.cache_path(descriptor.artifact_key)
        try:
            info = self.storage.safe_existing(path)
            if info.st_size != descriptor.expected_size:
                raise GatewayError("cache_corrupt")
            digest = hashlib.sha256()
            self._rehash(path, digest)
            if not hmac.compare_digest(digest.hexdigest(), descriptor.expected_sha256):
                raise GatewayError("cache_corrupt")
        except GatewayError:
            if path.exists() and not path.is_symlink():
                destination = self.storage.quarantine_path(descriptor.artifact_key, "cache")
                self.storage.promote(path, destination)
            raise
        return {"state": "verified", "size": info.st_size, "sha256": digest.hexdigest()}

    def request_cancel(self, job_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE online_gateway_download_jobs
                SET cancel_requested_at=?, updated_at=? WHERE public_id=?""",
                (_now(), _now(), job_id),
            )

    def cache_entry(self, artifact_key: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM online_gateway_cache_entries WHERE artifact_key=?",
                (artifact_key,),
            ).fetchone()
        return dict(row) if row is not None else None

    def quarantine_entries(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM online_gateway_cache_entries
                WHERE cache_state='quarantined' ORDER BY updated_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def discard_partial(self, descriptor: RemoteArtifactDescriptor) -> bool:
        path = self.storage.partial_path(descriptor.artifact_key)
        if not path.exists():
            return False
        self.storage.safe_existing(path)
        path.unlink()
        with suppress(FileNotFoundError):
            self.storage.metadata_path(descriptor.artifact_key).unlink()
        self.storage.fsync_directory(self.storage.partial)
        return True

    @contextmanager
    def _global_lock(self) -> Iterator[None]:
        self.lock_path.touch(mode=0o600, exist_ok=True)
        self.lock_path.chmod(0o600)
        with self.lock_path.open("r+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise GatewayError("gateway_download_busy") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _content_length(headers: dict[str, str]) -> int:
        value = headers.get("content-length")
        if value is None:
            raise GatewayError("content_length_missing")
        try:
            length = int(value)
        except ValueError as exc:
            raise GatewayError("content_length_mismatch") from exc
        if length < 0:
            raise GatewayError("content_length_mismatch")
        return length

    @staticmethod
    def _validate_resume(
        response: Any, offset: int, pending: int, total: int, etag: str | None
    ) -> None:
        if response.status != 206:
            raise GatewayError("resume_range_rejected")
        if response.headers.get("etag") != etag:
            raise GatewayError("resume_validator_changed")
        if response.headers.get("content-range") != f"bytes {offset}-{total - 1}/{total}":
            raise GatewayError("resume_range_rejected")
        if DownloadManager._content_length(response.headers) != pending:
            raise GatewayError("content_length_mismatch")

    @staticmethod
    def _validate_metadata(metadata: dict[str, Any], descriptor: RemoteArtifactDescriptor) -> None:
        expected = {
            "artifact_key": descriptor.artifact_key,
            "descriptor_sha256": descriptor.descriptor_sha256,
            "expected_sha256": descriptor.expected_sha256,
            "expected_size": descriptor.expected_size,
            "source_hostname": descriptor.hostname,
        }
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise GatewayError("resume_range_rejected")

    @staticmethod
    def _rehash(path: Path, digest: Any) -> None:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)

    def _cancel_requested(self, job_id: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested_at FROM online_gateway_download_jobs WHERE public_id=?",
                (job_id,),
            ).fetchone()
        return row is not None and row[0] is not None

    def _set_job(
        self,
        job_id: str,
        state: str,
        *,
        bytes_written: int,
        etag: str | None = None,
        error: str | None = None,
        completed: bool = False,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE online_gateway_download_jobs SET state=?, bytes_written=?,
                etag=COALESCE(?, etag), error_code=?, updated_at=?, completed_at=?
                WHERE public_id=?""",
                (state, bytes_written, etag, error, _now(), _now() if completed else None, job_id),
            )

    def _upsert_cache(
        self, descriptor: RemoteArtifactDescriptor, path: Path, observed: str
    ) -> None:
        now = _now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO online_gateway_cache_entries(
                artifact_key, source_id, artifact_name, expected_sha256, observed_sha256,
                expected_size, observed_size, cache_state, storage_relpath, verified_at,
                last_accessed_at, descriptor_sha256, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'verified', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_key) DO UPDATE SET observed_sha256=excluded.observed_sha256,
                observed_size=excluded.observed_size, cache_state='verified',
                storage_relpath=excluded.storage_relpath, verified_at=excluded.verified_at,
                last_accessed_at=excluded.last_accessed_at, updated_at=excluded.updated_at""",
                (
                    descriptor.artifact_key,
                    descriptor.source_id,
                    descriptor.artifact_name,
                    descriptor.expected_sha256,
                    observed,
                    descriptor.expected_size,
                    descriptor.expected_size,
                    self.storage.relative(path),
                    now,
                    now,
                    descriptor.descriptor_sha256,
                    now,
                    now,
                ),
            )
