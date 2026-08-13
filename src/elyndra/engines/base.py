from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    user: str
    assistant: str


@dataclass(frozen=True, slots=True)
class LanguageReply:
    text: str
    engine: str
    generated: bool
    metadata: dict[str, Any] = field(default_factory=dict)


class LanguageEngine(Protocol):
    name: str
    supports_vision: bool

    def reply(
        self,
        prompt: str,
        *,
        context: tuple[str, ...] = (),
        history: tuple[ConversationTurn, ...] = (),
        response_language: str | None = None,
        keep_alive_seconds: int = 0,
        images: tuple[str, ...] = (),
        max_tokens: int | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> LanguageReply:
        """Return language output without receiving tools or secrets."""

    def release(self) -> None:
        """Release runtime resources when the engine supports it."""
