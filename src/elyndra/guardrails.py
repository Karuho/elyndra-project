from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GuardrailResponse:
    intent: str
    text: str


_DISPOSAL_WORDS = (
    "deshacerme",
    "deshacerse",
    "hacer desaparecer",
    "ocultar",
    "enterrar",
    "eliminar el cuerpo",
    "dispose of",
    "hide a body",
    "get rid of",
)
_BODY_WORDS = (
    "cuerpo",
    "cadaver",
    "persona",
    "pollo",
    "body",
    "corpse",
    "person",
    "chicken",
)
_LYRIC_PATTERNS = (
    "sigue la cancion",
    "continua la cancion",
    "continua la letra",
    "sigue la letra",
    "complete the lyrics",
    "continue the song",
    "continue the lyrics",
)
_WEIGHT = re.compile(r"\b(\d{2,3})\s*(?:kg|kilos?|kilogramos?)\b", re.IGNORECASE)


def guardrail_response(text: str, response_language: str) -> GuardrailResponse | None:
    normalized = _normalize(text)
    if _looks_like_body_disposal(normalized):
        return GuardrailResponse(
            "possible_body_disposal",
            _body_disposal_message(response_language),
        )
    if _looks_like_lyric_continuation(normalized):
        return GuardrailResponse(
            "lyrics_continuation",
            _lyrics_message(response_language),
        )
    return None


def _looks_like_body_disposal(normalized: str) -> bool:
    if not any(word in normalized for word in _DISPOSAL_WORDS):
        return False
    if not any(word in normalized for word in _BODY_WORDS):
        return False
    weights = [int(match.group(1)) for match in _WEIGHT.finditer(normalized)]
    explicit_body = any(word in normalized for word in ("cuerpo", "cadaver", "body", "corpse"))
    return explicit_body or any(weight >= 30 for weight in weights)


def _looks_like_lyric_continuation(normalized: str) -> bool:
    if any(pattern in normalized for pattern in _LYRIC_PATTERNS):
        return True
    return normalized.startswith(("sigue:", "continua:", "continue:"))


def _body_disposal_message(language: str) -> str:
    if language == "en":
        return (
            "That wording could mean hiding a body or evidence, so I cannot help with disposal or "
            "concealment. If you literally mean a dead animal, use a legal sanitary route such as "
            "a veterinarian, municipal collection service, or local animal-health authority. If a "
            "person may be hurt or in danger, contact emergency services now."
        )
    return (
        "Esa formulación puede referirse a ocultar un cuerpo o evidencia, así que no puedo "
        "ayudar a deshacerse de ello ni encubrirlo. Si hablas literalmente de un animal muerto, "
        "corresponde usar una vía sanitaria legal: veterinario, retiro municipal o autoridad "
        "local de salud "
        "animal. Si hay una persona herida o en peligro, contacta a emergencias de inmediato."
    )


def _lyrics_message(language: str) -> str:
    if language == "en":
        return (
            "🎤 Freestyle karaoke mode!\n"
            "🎵 A little butterfly circles the light,\n"
            "dances with the moon and laughs through the night.\n"
            "Your turn: add the next original line."
        )
    return (
        "🎤 ¡Modo karaoke libre!\n"
        "🎵 Mariposita, vuela despacito,\n"
        "da una vuelta y saluda al gatito.\n"
        "Tu turno: agrega la siguiente línea original."
    )


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return " ".join(without_marks.split())
