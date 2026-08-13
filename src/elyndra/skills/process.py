from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    command: tuple[str, ...]
    cwd: str
    returncode: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool

    @property
    def output(self) -> str:
        if self.stdout and self.stderr:
            return f"STDOUT:\n{self.stdout}\n\nSTDERR:\n{self.stderr}".strip()
        return self.stdout or self.stderr


def run_controlled_process(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    environment: Mapping[str, str] | None = None,
    max_output_chars: int = 12_000,
) -> ProcessResult:
    """Run one argv-only process with bounded output and process-group cleanup."""

    clean_command = tuple(str(value) for value in command)
    if not clean_command or not clean_command[0]:
        raise ValueError("El comando controlado no puede estar vacío.")

    env = {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}
    env.update(
        {
            "NO_COLOR": "1",
            "TERM": "dumb",
            "COMPOSER_NO_INTERACTION": "1",
            "COMPOSER_DISABLE_NETWORK": "1",
        }
    )
    if environment:
        env.update({str(key): str(value) for key, value in environment.items()})

    started = time.perf_counter()
    timed_out = False
    returncode: int | None = None
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(  # noqa: S603 - argv is constructed by trusted skills.
            clean_command,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            shell=False,
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=max(1, timeout_seconds))
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process_group(process)
            returncode = process.returncode

        stdout, stdout_truncated = _read_tail(stdout_file, max_output_chars)
        stderr, stderr_truncated = _read_tail(stderr_file, max_output_chars)

    return ProcessResult(
        command=clean_command,
        cwd=str(cwd),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=round((time.perf_counter() - started) * 1000),
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=1)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait(timeout=1)


def _read_tail(handle: object, max_chars: int) -> tuple[str, bool]:
    file_handle = handle
    file_handle.seek(0, os.SEEK_END)
    size = file_handle.tell()
    read_size = min(size, max(4096, max_chars * 4))
    file_handle.seek(max(0, size - read_size))
    raw = file_handle.read(read_size)
    text = raw.decode("utf-8", errors="replace")
    text = _ANSI_ESCAPE.sub("", text).replace("\x00", "")
    truncated = len(text) > max_chars
    if truncated:
        text = "… salida truncada …\n" + text[-max_chars:]
    return text.strip(), truncated
