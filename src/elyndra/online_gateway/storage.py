from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import suppress
from pathlib import Path
from typing import Any, BinaryIO

from elyndra.online_gateway.errors import GatewayError
from elyndra.paths import ElyndraPaths


class GatewayStorage:
    def __init__(self, paths: ElyndraPaths) -> None:
        self.root = paths.online_gateway_dir
        self.partial = self.root / "partial"
        self.cache = self.root / "cache"
        self.quarantine = self.root / "quarantine"
        self.metadata = self.root / "metadata"
        for directory in (self.root, self.partial, self.cache, self.quarantine, self.metadata):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)

    @staticmethod
    def key_name(artifact_key: str) -> str:
        return hashlib.sha256(artifact_key.encode("utf-8")).hexdigest()

    def partial_path(self, artifact_key: str) -> Path:
        return self.partial / f"{self.key_name(artifact_key)}.part"

    def cache_path(self, artifact_key: str) -> Path:
        return self.cache / f"{self.key_name(artifact_key)}.artifact"

    def quarantine_path(self, artifact_key: str, suffix: str = "bad") -> Path:
        return self.quarantine / f"{self.key_name(artifact_key)}.{suffix}"

    def metadata_path(self, artifact_key: str) -> Path:
        return self.metadata / f"{self.key_name(artifact_key)}.json"

    def relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root.resolve()))
        except ValueError as exc:
            raise GatewayError("storage_unsafe") from exc

    def safe_existing(self, path: Path) -> os.stat_result:
        try:
            info = path.lstat()
        except OSError as exc:
            raise GatewayError("storage_unsafe") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise GatewayError("storage_unsafe")
        try:
            path.resolve().relative_to(self.root.resolve())
        except ValueError as exc:
            raise GatewayError("storage_unsafe") from exc
        return info

    def open_exclusive(self, path: Path) -> BinaryIO:
        if path.parent not in {self.partial, self.metadata}:
            raise GatewayError("storage_unsafe")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise GatewayError("storage_unsafe") from exc
        return os.fdopen(descriptor, "wb")

    def open_append(self, path: Path) -> BinaryIO:
        self.safe_existing(path)
        flags = os.O_WRONLY | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise GatewayError("storage_unsafe") from exc
        return os.fdopen(descriptor, "ab")

    def write_metadata(self, artifact_key: str, payload: dict[str, Any]) -> None:
        final = self.metadata_path(artifact_key)
        temporary = final.with_suffix(".json.new")
        with suppress(FileNotFoundError):
            temporary.unlink()
        with self.open_exclusive(temporary) as handle:
            handle.write(json.dumps(payload, sort_keys=True).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, final)
        final.chmod(0o600)
        self.fsync_directory(self.metadata)

    def read_metadata(self, artifact_key: str) -> dict[str, Any]:
        path = self.metadata_path(artifact_key)
        self.safe_existing(path)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GatewayError("storage_unsafe") from exc
        if not isinstance(value, dict) or value.get("artifact_key") != artifact_key:
            raise GatewayError("storage_unsafe")
        return value

    def promote(self, source: Path, destination: Path) -> None:
        self.safe_existing(source)
        if destination.exists() or destination.is_symlink():
            raise GatewayError("storage_unsafe")
        os.replace(source, destination)
        destination.chmod(0o600)
        self.fsync_directory(destination.parent)

    @staticmethod
    def fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def available_bytes(self) -> int:
        return int(os.statvfs(self.root).f_bavail * os.statvfs(self.root).f_frsize)
