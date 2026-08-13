from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from difflib import get_close_matches
from typing import TypeVar

T = TypeVar("T")

_EXIT_ALIASES = {
    "/exit",
    "/salir",
    "exit",
    "quit",
    "salir",
    "adios",
    "adiós",
    "bye",
}

_COMMANDS = (
    "/exit",
    "/help",
    "/status",
    "/projects",
    "/memories",
    "/knowledge",
    "/skills",
    "/model",
    "/language",
    "/persona",
    "/identity",
    "/personality",
    "/clear",
)


def is_exit_command(text: str) -> bool:
    return text.strip().casefold() in _EXIT_ALIASES


def command_hint(text: str) -> str | None:
    candidate = text.strip().casefold()
    if not candidate or " " in candidate or len(candidate) > 18:
        return None
    normalized = candidate if candidate.startswith("/") else f"/{candidate}"
    match = get_close_matches(normalized, _COMMANDS, n=1, cutoff=0.68)
    if not match:
        return None
    command = match[0]
    if command == normalized:
        return None
    if command == "/exit":
        return "¿Quisiste salir? Escribe /exit, exit o salir."
    return f"¿Quisiste escribir {command}? Usa /help para ver los comandos."


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, remaining = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes} min {remaining:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min {remaining:02d} s"


def run_with_progress(
    operation: Callable[[], T],
    *,
    label: str = "Procesando",
    speaker: str = "Elyn",
    delay_seconds: float = 0.35,
    refresh_seconds: float = 0.25,
) -> tuple[T, float]:
    result: list[T] = []
    error: list[Exception] = []
    finished = threading.Event()
    started = time.perf_counter()

    def worker() -> None:
        try:
            result.append(operation())
        except Exception as exc:  # re-raised on the calling thread
            error.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    if not finished.wait(delay_seconds):
        _render_progress(f"{label}…", started, speaker)
        spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        index = 0
        while not finished.wait(refresh_seconds):
            _render_progress(
                f"{spinner[index % len(spinner)]} {label}…", started, speaker
            )
            index += 1
        _clear_progress()

    thread.join()
    elapsed = time.perf_counter() - started
    if error:
        raise error[0]
    return result[0], elapsed


def _render_progress(label: str, started: float, speaker: str) -> None:
    elapsed = time.perf_counter() - started
    line = f"\r{speaker} > {label} {format_duration(elapsed)}"
    if sys.stdout.isatty():
        sys.stdout.write(line)
        sys.stdout.flush()
    elif elapsed < 0.7:
        print(f"{speaker} > {label}")


def _clear_progress() -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\r\x1b[2K")
        sys.stdout.flush()
