from __future__ import annotations

from elyndra.cli import build_parser


def test_memory_lifecycle_commands_parse() -> None:
    parser = build_parser()

    episodes = parser.parse_args(["memory", "episodes", "--kind", "decision"])
    proposals = parser.parse_args(["memory", "proposals", "--status", "pending"])
    approve = parser.parse_args(["memory", "approve", "3", "--approve"])
    archive = parser.parse_args(["chat", "archive", "chat_123", "--prune", "--approve"])

    assert episodes.memory_command == "episodes"
    assert proposals.memory_command == "proposals"
    assert approve.id == 3
    assert archive.prune is True
