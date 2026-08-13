from __future__ import annotations

from elyndra.chat_ui import command_hint, format_duration, is_exit_command


def test_exit_aliases_and_typo_hint() -> None:
    assert is_exit_command("/exit") is True
    assert is_exit_command("exit") is True
    assert is_exit_command("salir") is True
    assert is_exit_command("exot") is False
    assert "/exit" in (command_hint("exot") or "")


def test_duration_formatting() -> None:
    assert format_duration(0.2) == "200 ms"
    assert format_duration(6.85) == "6.8 s"
    assert format_duration(192) == "3 min 12 s"
