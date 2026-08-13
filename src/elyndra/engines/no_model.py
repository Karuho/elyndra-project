from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from elyndra.engines.base import ConversationTurn, LanguageReply


@dataclass(slots=True)
class NoModelEngine:
    reason: str | None = None
    name: str = "no-model"
    supports_vision: bool = False

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
        del (
            prompt, context, history, response_language, keep_alive_seconds, images,
            max_tokens, on_token
        )
        reason = f" Motivo: {self.reason}" if self.reason else ""
        return LanguageReply(
            text=(
                "Todavía no tengo un motor lingüístico activado, así que no inventaré "
                "una respuesta. "
                "Puedo revisar el sistema, recordar datos, inspeccionar proyectos, leer archivos, "
                "buscar dentro del código e importar conocimiento local. Usa 'elyndra chat' o "
                f"'elyndra skill list' para ver las capacidades disponibles.{reason}"
            ),
            engine=self.name,
            generated=False,
        )

    def release(self) -> None:
        return None
