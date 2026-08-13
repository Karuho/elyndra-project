from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

LANGUAGE_NAMES: dict[str, str] = {
    "ar": "árabe",
    "de": "alemán",
    "en": "inglés",
    "es": "español",
    "fr": "francés",
    "he": "hebreo",
    "hi": "hindi",
    "it": "italiano",
    "ja": "japonés",
    "ko": "coreano",
    "pt": "portugués",
    "ru": "ruso",
    "th": "tailandés",
    "vi": "vietnamita",
    "zh": "chino",
}

_LANGUAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "ar": ("arabe", "arabic", "العربية", "عربي"),
    "de": ("aleman", "german", "deutsch"),
    "en": ("ingles", "english", "英文", "英语", "英語", "영어"),
    "es": (
        "espanol",
        "castellano",
        "spanish",
        "西班牙语",
        "西班牙語",
        "スペイン語",
        "스페인어",
        "испанский",
        "espagnol",
        "spanisch",
        "spagnolo",
    ),
    "fr": ("frances", "french", "francais", "français"),
    "he": ("hebreo", "hebrew", "עברית"),
    "hi": ("hindi", "हिन्दी", "हिंदी"),
    "it": ("italiano", "italian"),
    "ja": ("japones", "japanese", "日本語"),
    "ko": ("coreano", "korean", "한국어"),
    "pt": ("portugues", "portuguese", "português"),
    "ru": ("ruso", "russian", "русский"),
    "th": ("tailandes", "thai", "ไทย"),
    "vi": ("vietnamita", "vietnamese", "tiếng việt"),
    "zh": ("chino", "chinese", "中文", "汉语", "漢語", "普通话", "普通話"),
}

_CHANGE_MARKERS = (
    "cambia",
    "cambiar",
    "responde",
    "habla",
    "switch",
    "change",
    "respond",
    "reply",
    "mude",
    "responda",
    "fale",
    "passe",
    "reponds",
    "réponds",
    "wechsle",
    "antworte",
    "切换",
    "切換",
    "改成",
    "请用",
    "請用",
    "回答",
    "切り替え",
    "答えて",
    "바꿔",
    "변경",
    "대답",
    "переключ",
    "отвечай",
)

_AUTO_ALIASES = (
    "automatico",
    "automático",
    "automatic",
    "auto",
    "detectar idioma",
    "mismo idioma",
    "same language",
    "自动",
    "自動",
)

_LATIN_STOPWORDS: dict[str, frozenset[str]] = {
    "de": frozenset({"der", "die", "das", "und", "ist", "ich", "nicht", "mit", "zu"}),
    "en": frozenset({"the", "and", "is", "are", "you", "this", "that", "with", "to"}),
    "es": frozenset({"el", "la", "los", "las", "y", "es", "que", "para", "con", "una"}),
    "fr": frozenset({"le", "la", "les", "et", "est", "que", "pour", "avec", "une"}),
    "it": frozenset({"il", "la", "gli", "le", "e", "che", "per", "con", "una"}),
    "pt": frozenset({"o", "a", "os", "as", "e", "que", "para", "com", "uma"}),
    "vi": frozenset({"và", "là", "của", "cho", "không", "một", "tôi", "bạn"}),
}


@dataclass(frozen=True, slots=True)
class LanguageDetection:
    code: str
    name: str
    confidence: float
    method: str


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)



def resolve_language(value: str, *, allow_auto: bool = False) -> str:
    normalized = _normalize(value)
    if allow_auto and normalized == "auto":
        return "auto"
    if normalized in LANGUAGE_NAMES:
        return normalized
    for code, aliases in _LANGUAGE_ALIASES.items():
        candidates = (LANGUAGE_NAMES[code], *aliases)
        if any(_normalize(candidate) == normalized for candidate in candidates):
            return code
    available = ", ".join(sorted(LANGUAGE_NAMES))
    raise ValueError(
        f"Idioma no soportado: {value}. Usa un código como es, en o zh. "
        f"Disponibles: {available}, auto"
    )


def validate_language_code(code: str, *, allow_auto: bool = False) -> str:
    normalized = code.strip().casefold()
    if allow_auto and normalized == "auto":
        return normalized
    if normalized not in LANGUAGE_NAMES:
        available = ", ".join(sorted(LANGUAGE_NAMES))
        raise ValueError(f"Idioma no soportado: {code}. Disponibles: {available}, auto")
    return normalized


def detect_language(text: str, *, fallback: str = "es") -> LanguageDetection:
    fallback_code = validate_language_code(fallback)
    sample = text.strip()
    if not sample:
        return LanguageDetection(fallback_code, language_name(fallback_code), 0.0, "fallback")

    script_counts = {
        "ar": _count_range(sample, 0x0600, 0x06FF),
        "he": _count_range(sample, 0x0590, 0x05FF),
        "hi": _count_range(sample, 0x0900, 0x097F),
        "ko": _count_range(sample, 0xAC00, 0xD7AF),
        "ru": _count_range(sample, 0x0400, 0x04FF),
        "th": _count_range(sample, 0x0E00, 0x0E7F),
    }
    kana = _count_range(sample, 0x3040, 0x30FF)
    han = _count_range(sample, 0x4E00, 0x9FFF)
    if kana:
        return LanguageDetection("ja", language_name("ja"), 0.99, "script")
    if han:
        return LanguageDetection("zh", language_name("zh"), 0.96, "script")
    script_code, script_count = max(script_counts.items(), key=lambda item: item[1])
    if script_count:
        return LanguageDetection(script_code, language_name(script_code), 0.98, "script")

    normalized = _normalize(sample)
    words = re.findall(r"[a-zà-ÿ]+", normalized)
    if not words:
        return LanguageDetection(fallback_code, language_name(fallback_code), 0.1, "fallback")

    scores = {
        code: sum(word in stopwords for word in words)
        for code, stopwords in _LATIN_STOPWORDS.items()
    }
    winner, score = max(scores.items(), key=lambda item: item[1])
    if score:
        confidence = min(0.95, 0.45 + score / max(4, len(words)))
        return LanguageDetection(winner, language_name(winner), confidence, "stopwords")

    accent_hints = {
        "es": "ñ¿¡",
        "fr": "àâçéèêëîïôùûüÿœ",
        "pt": "ãõç",
        "de": "äöüß",
        "it": "ìò",
        "vi": "ăâđêôơư",
    }
    accent_scores = {
        code: sum(character in sample.casefold() for character in characters)
        for code, characters in accent_hints.items()
    }
    accent_code, accent_score = max(accent_scores.items(), key=lambda item: item[1])
    if accent_score:
        return LanguageDetection(accent_code, language_name(accent_code), 0.7, "characters")

    return LanguageDetection(fallback_code, language_name(fallback_code), 0.2, "fallback")


def parse_language_change(text: str) -> str | None:
    normalized = _normalize(text)
    if not any(marker in normalized for marker in _CHANGE_MARKERS):
        return None
    if any(alias in normalized for alias in _AUTO_ALIASES):
        return "auto"
    for code, aliases in _LANGUAGE_ALIASES.items():
        if any(_normalize(alias) in normalized for alias in aliases):
            return code
    return None


def _count_range(text: str, start: int, end: int) -> int:
    return sum(start <= ord(character) <= end for character in text)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_accents.split())
