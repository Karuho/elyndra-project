from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from elyndra.dictionary import LocalDictionary
from elyndra.engines import ConversationTurn
from elyndra.languages import language_name, resolve_language

_LANGUAGE_ALIASES = {
    "es": "es",
    "espanol": "es",
    "español": "es",
    "spanish": "es",
    "en": "en",
    "ingles": "en",
    "inglés": "en",
    "english": "en",
    "ja": "ja",
    "japones": "ja",
    "japonés": "ja",
    "japanese": "ja",
    "zh": "zh",
    "chino": "zh",
    "chinese": "zh",
    "mandarin": "zh",
    "mandarin chino": "zh",
    "it": "it",
    "italiano": "it",
    "italian": "it",
    "fr": "fr",
    "frances": "fr",
    "francés": "fr",
    "french": "fr",
    "pt": "pt",
    "portugues": "pt",
    "portugués": "pt",
    "portuguese": "pt",
    "de": "de",
    "aleman": "de",
    "alemán": "de",
    "german": "de",
}

_TRANSLATION_PATTERNS = (
    re.compile(
        r"^(?:y\s+)?(?:como|cómo)\s+(?:se\s+dice|digo|puedo\s+decir)\s+(.+?)\s+"
        r"(?:en|al|a)\s+([\wáéíóúüñ-]+)\??$",
        re.I,
    ),
    re.compile(r"^traduce\s+(.+?)\s+(?:al|a|en)\s+([\wáéíóúüñ-]+)\??$", re.I),
    re.compile(r"^translate\s+(.+?)\s+(?:to|into)\s+([\w-]+)\??$", re.I),
)

_CAPABILITY_PATTERNS = (
    "puedes traducir cualquier palabra",
    "puedes traducir cualquier frase",
    "dependes del modelo",
    "usas la libreria para traducir",
    "usas la biblioteca para traducir",
    "can you translate any word",
)

_PRONUNCIATION_PATTERNS = (
    "como puedo pronunciar eso",
    "cómo puedo pronunciar eso",
    "como se pronuncia eso",
    "cómo se pronuncia eso",
    "no se leer chino",
    "no sé leer chino",
    "how do i pronounce that",
)


@dataclass(frozen=True, slots=True)
class TranslationRequest:
    text: str
    target_language: str


@dataclass(frozen=True, slots=True)
class LocalTranslation:
    source_text: str
    target_language: str
    translated_text: str
    pronunciation: str = ""
    source: str = "local-phrasebook"
    exact: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_text": self.source_text,
            "target_language": self.target_language,
            "translated_text": self.translated_text,
            "pronunciation": self.pronunciation,
            "source": self.source,
            "exact": self.exact,
        }


class LocalTranslationService:
    def __init__(self, dictionary: LocalDictionary) -> None:
        self.dictionary = dictionary
        resource = files("elyndra.resources").joinpath("translation_core_v1.json")
        payload = json.loads(resource.read_text(encoding="utf-8"))
        self._payload = payload
        self._exact: dict[str, dict[str, dict[str, str]]] = {}
        for entry in payload.get("phrases", []):
            if not isinstance(entry, dict):
                continue
            source_forms = entry.get("source_forms", {})
            translations = entry.get("translations", {})
            if not isinstance(source_forms, dict) or not isinstance(translations, dict):
                continue
            for language, forms in source_forms.items():
                for form in forms if isinstance(forms, list) else []:
                    clean = normalize_phrase(str(form))
                    if clean:
                        self._exact.setdefault(str(language), {})[clean] = {
                            str(code): str(value)
                            for code, value in translations.items()
                            if isinstance(value, str)
                        } | {
                            f"pronunciation:{code}": str(value)
                            for code, value in entry.get("pronunciation", {}).items()
                            if isinstance(value, str)
                        }

    def status(self) -> dict[str, Any]:
        structured = self.dictionary.status().get("structured_packs", {})
        return {
            "package_id": self._payload.get("id", ""),
            "version": self._payload.get("version", ""),
            "offline": True,
            "model_required_for_known_entries": False,
            "complete_translation_engine": False,
            "phrase_count": sum(len(items) for items in self._exact.values()),
            "dictionary_entries": self.dictionary.status().get("entry_count", 0),
            "structured_language_packs": structured.get("language_pack_count", 0),
            "fallback_model_available": True,
        }

    def translate(
        self,
        text: str,
        target_language: str,
        *,
        source_language: str | None = None,
    ) -> LocalTranslation | None:
        clean_text = " ".join(text.strip().strip('"\'“”').split())
        if not clean_text:
            return None
        target = resolve_language(target_language)
        normalized = normalize_phrase(clean_text)
        source_candidates = (source_language,) if source_language else tuple(self._exact)
        for source in source_candidates:
            if not source:
                continue
            data = self._exact.get(source.split("-", 1)[0], {}).get(normalized)
            if data and target in data:
                return LocalTranslation(
                    source_text=clean_text,
                    target_language=target,
                    translated_text=data[target],
                    pronunciation=data.get(f"pronunciation:{target}", ""),
                )

        template = self._translate_name_template(clean_text, target)
        if template is not None:
            return template

        if len(clean_text.split()) <= 5:
            matches = self.dictionary.lookup(
                clean_text,
                language=source_language,
                output_language=target,
            )
            values: list[str] = []
            pronunciation = ""
            for match in matches:
                values.extend(match.translations.get(target, ()))
                if match.pronunciation:
                    pronunciation = str(
                        match.pronunciation.get(target)
                        or match.pronunciation.get("romanization")
                        or pronunciation
                    )
            values = list(dict.fromkeys(value for value in values if value))
            if values:
                return LocalTranslation(
                    source_text=clean_text,
                    target_language=target,
                    translated_text=", ".join(values),
                    pronunciation=pronunciation,
                    source="local-dictionary",
                )
        return None

    def render(self, result: LocalTranslation, *, response_language: str = "es") -> str:
        if response_language.startswith("en"):
            lines = [f"In {language_name(result.target_language)}: {result.translated_text}"]
            if result.pronunciation:
                lines.append(f"Pronunciation: {result.pronunciation}")
            return "\n".join(lines)
        lines = [f"En {language_name(result.target_language)}: {result.translated_text}"]
        if result.pronunciation:
            lines.append(f"Pronunciación aproximada: {result.pronunciation}")
        return "\n".join(lines)

    def capability_message(self, *, response_language: str = "es") -> str:
        if response_language.startswith("en"):
            return (
                "Elyndra translates known words, phrases and installed language packs locally "
                "without a model. It does not yet contain every word or full grammar; unknown "
                "or complex text can use the configured local model as a fallback."
            )
        return (
            "Elyndra traduce localmente palabras y frases conocidas, además de los paquetes "
            "lingüísticos instalados en Alejandría, sin usar el modelo. Aún no contiene todas "
            "las palabras ni una gramática completa; el texto desconocido o complejo puede "
            "usar el modelo local configurado como respaldo."
        )

    def pronunciation_from_history(
        self,
        history: tuple[ConversationTurn, ...],
        *,
        response_language: str = "es",
    ) -> str | None:
        for turn in reversed(history[-4:]):
            match = re.search(
                r"(?:Pronunciación aproximada|Pronunciation):\s*(.+)",
                turn.assistant,
                re.I,
            )
            if match is not None:
                value = match.group(1).strip()
                if response_language.startswith("en"):
                    return f"Pronunciation: {value}"
                return f"Pronunciación aproximada: {value}"
        return None

    @staticmethod
    def _translate_name_template(text: str, target: str) -> LocalTranslation | None:
        match = re.fullmatch(
            r"hola[,. ]+(?:yo )?me llamo\s+([\wÀ-ÖØ-öø-ÿĀ-ž'-]{1,60})",
            text.strip(),
            re.I,
        )
        if match is None:
            return None
        name = match.group(1)
        translations = {
            "es": f"Hola, me llamo {name}.",
            "en": f"Hello, my name is {name}.",
            "ja": f"こんにちは、私は{name}です。",
            "zh": f"你好，我叫{name}。",
            "it": f"Ciao, mi chiamo {name}.",
            "fr": f"Bonjour, je m'appelle {name}.",
            "pt": f"Olá, meu nome é {name}.",
            "de": f"Hallo, ich heiße {name}.",
        }
        pronunciations = {
            "ja": f"Konnichiwa, watashi wa {name} desu.",
            "zh": f"Nǐ hǎo, wǒ jiào {name}.",
            "de": f"Hallo, ich hai-se {name}.",
        }
        if target not in translations:
            return None
        return LocalTranslation(
            source_text=text,
            target_language=target,
            translated_text=translations[target],
            pronunciation=pronunciations.get(target, ""),
            source="local-template",
        )


def extract_translation_request(text: str) -> TranslationRequest | None:
    clean = " ".join(text.strip().strip("¿? ").split())
    if not clean or len(clean) > 300:
        return None
    for pattern in _TRANSLATION_PATTERNS:
        match = pattern.match(clean)
        if match is None:
            continue
        source_text = match.group(1).strip(" \t\n\r\"'¿?.,:;")
        target_raw = normalize_phrase(match.group(2))
        target = _LANGUAGE_ALIASES.get(target_raw)
        if target and source_text:
            return TranslationRequest(source_text, target)
    return None


def asks_translation_capability(text: str) -> bool:
    normalized = normalize_phrase(text)
    return any(marker in normalized for marker in _CAPABILITY_PATTERNS)


def asks_pronunciation_followup(text: str) -> bool:
    normalized = normalize_phrase(text)
    return any(marker in normalized for marker in _PRONUNCIATION_PATTERNS)


def normalize_phrase(value: str) -> str:
    clean = unicodedata.normalize("NFKD", value.casefold())
    clean = "".join(char for char in clean if not unicodedata.combining(char))
    clean = re.sub(r"[^\w\s'-]", " ", clean, flags=re.UNICODE)
    return " ".join(clean.split())
