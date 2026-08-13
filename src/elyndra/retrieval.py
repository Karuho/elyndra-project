from __future__ import annotations

import re
import unicodedata

from elyndra.engines.base import ConversationTurn

_STOPWORDS = {
    # Spanish
    "a",
    "al",
    "algo",
    "como",
    "con",
    "cual",
    "cuando",
    "de",
    "del",
    "el",
    "ella",
    "en",
    "es",
    "esta",
    "este",
    "esto",
    "la",
    "las",
    "lo",
    "los",
    "me",
    "para",
    "por",
    "porque",
    "que",
    "qué",
    "se",
    "sobre",
    "su",
    "sus",
    "un",
    "una",
    "y",
    # English
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "why",
    "with",
    # Portuguese / French / Italian common glue
    "do",
    "da",
    "dos",
    "das",
    "et",
    "est",
    "le",
    "les",
    "des",
    "du",
    "il",
    "elle",
    "che",
    "di",
    "gli",
    "per",
}

_CONTEXT_MARKERS = {
    "elyndra",
    "elyn",
    "proyecto",
    "project",
    "archivo",
    "file",
    "documento",
    "document",
    "memoria",
    "memory",
    "recuerdo",
    "remember",
    "recuerdas",
    "recordar",
    "preferencia",
    "preference",
    "privacidad",
    "privacy",
    "local",
    "equipos",
    "hardware",
    "requisitos",
    "requirements",
    "configuracion",
    "configuration",
    "antes",
    "previous",
    "anterior",
    "conversacion",
    "conversation",
    "chat",
    "decidimos",
    "decision",
    "quedamos",
    "hicimos",
    "resumen",
    "resume",
    "retomar",
    "pendiente",
    "pending",
}

_FOLLOW_UP_PREFIXES = (
    "y ",
    "pero ",
    "entonces ",
    "eso ",
    "esa ",
    "ese ",
    "ellos ",
    "ellas ",
    "what about",
    "and ",
    "but ",
    "so ",
    "that ",
)

_TOKEN = re.compile(r"[\wÀ-ÖØ-öø-ÿĀ-ž]+", re.UNICODE)


def retrieval_queries(text: str, *, max_terms: int = 6) -> tuple[str, ...]:
    """Create conservative lookup variants without invoking a language model."""
    original = " ".join(text.strip().split())
    if not original:
        return ()

    terms = _significant_terms(original, max_terms=max_terms, preserve_original=True)
    variants: list[str] = [original]
    if terms:
        joined = " ".join(terms)
        if joined.casefold() != original.casefold():
            variants.append(joined)
        variants.extend(terms)

    unique: list[str] = []
    fingerprints: set[str] = set()
    for item in variants:
        fingerprint = _normalize_token(item)
        if not fingerprint or fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        unique.append(item)
    return tuple(unique)


def should_retrieve_context(text: str) -> bool:
    """Avoid loading personal/project context for unrelated casual conversation."""
    terms = set(_significant_terms(text, max_terms=20))
    return bool(terms & _CONTEXT_MARKERS)


def should_use_session_summary(text: str, summary: str) -> bool:
    """Load a persisted summary only when the current prompt is related to it."""
    if not summary.strip():
        return False
    normalized = _normalize_token(text)
    if normalized.startswith(_FOLLOW_UP_PREFIXES) or should_retrieve_context(text):
        return True
    query_terms = set(_significant_terms(text, max_terms=12))
    summary_terms = set(_significant_terms(summary, max_terms=48))
    return bool(query_terms & summary_terms)


def select_relevant_history(
    text: str,
    history: tuple[ConversationTurn, ...],
    *,
    max_turns: int = 6,
) -> tuple[ConversationTurn, ...]:
    """Keep active history in RAM but send only turns relevant to the current prompt."""
    if not history:
        return ()
    cleaned = tuple(_clean_turn(turn) for turn in history[-max_turns:])
    cleaned = tuple(turn for turn in cleaned if turn is not None)
    if not cleaned:
        return ()

    normalized = _normalize_token(text)
    query_terms = set(_significant_terms(text, max_terms=12))
    follow_up = normalized.startswith(_FOLLOW_UP_PREFIXES)
    selected: list[ConversationTurn] = []
    for index, turn in enumerate(reversed(cleaned)):
        turn_terms = set(
            _significant_terms(
                f"{turn.user} {turn.assistant}",
                max_terms=24,
            )
        )
        is_latest = index == 0
        if query_terms & turn_terms or (follow_up and is_latest):
            selected.append(turn)
        if len(selected) >= 3:
            break
    return tuple(reversed(selected))


def _clean_turn(turn: ConversationTurn) -> ConversationTurn | None:
    user = " ".join(turn.user.strip().split())[:420]
    assistant = " ".join(turn.assistant.strip().split())[:560]
    if not user or not assistant:
        return None
    return ConversationTurn(user=user, assistant=assistant)


def _significant_terms(
    text: str,
    *,
    max_terms: int,
    preserve_original: bool = False,
) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN.findall(text):
        normalized = _normalize_token(raw)
        if len(normalized) < 3 or normalized in _STOPWORDS or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(raw if preserve_original else normalized)
        if len(terms) >= max_terms:
            break
    return terms


def _normalize_token(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))
