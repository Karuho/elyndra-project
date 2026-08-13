from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from importlib.resources import files
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from elyndra.alexandria.structured_packs import StructuredPackRepository
    from elyndra.language_packs.repository import SpanishLexicalService

_SUPPORTED_LANGUAGES = ("es", "en", "ja", "zh", "it", "fr", "pt", "de")
_QUERY_PATTERNS = (
    re.compile(
        r"^(?:que significa|qué significa|define|definicion de|definición de)"
        r"\s+(.+?)\??$",
        re.I,
    ),
    re.compile(r"^(?:what does|define)\s+(.+?)(?:\s+mean)?\??$", re.I),
    re.compile(r"^(?:diccionario|dictionary)\s*[:：]?\s*(.+?)\??$", re.I),
    re.compile(
        r"^(?:que quiere decir|qué quiere decir|cu[aá]les son los sentidos de)\s+(.+?)\??$",
        re.I,
    ),
    re.compile(r"^(?:qu[eé] expresa(?: el emoji)?)\s+(.+?)\??$", re.I),
)

_RELATION_QUERY = re.compile(
    r"^(sin[oó]nimos|ant[oó]nimos|palabras relacionadas)\s+(?:de|con)\s+(.+?)\??$",
    re.I,
)

_FORM_QUESTION = re.compile(
    r"^¿?(.+?)\s+(?:viene de|es una forma de)(?:l verbo)?\s+(.+?)\??$", re.I
)


@dataclass(frozen=True, slots=True)
class DictionaryMatch:
    concept_id: str
    part_of_speech: str
    matched_language: str
    matched_form: str
    gloss_language: str
    gloss: str
    translations: dict[str, tuple[str, ...]]
    source: str = "core"
    package_id: str = ""
    package_name: str = ""
    review_status: str = "reviewed"
    reviewed_on: str = ""
    license_id: str = "CC0-1.0"
    locale: str = ""
    dialect: str = ""
    morphology: dict[str, Any] | None = None
    pronunciation: dict[str, Any] | None = None
    examples: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    attribution: tuple[str, ...] = ()
    source_relative_path: str = ""
    source_title: str = ""
    source_sha256: str = ""
    source_url: str = ""
    source_attribution: str = ""
    source_ref: str = ""
    query_expression: str = ""
    match_type: str = ""
    canonical_lemma: str = ""
    form_features: dict[str, Any] | None = None
    package_version: str = ""
    source_id: str = ""
    source_type: str = "external_dataset"
    external_dataset: bool = True
    deterministic: bool = False
    lexical_details: dict[str, Any] | None = None
    lexical_relation: str = ""
    relation_target: str = ""
    variant_form: str = ""
    is_lexical_variant: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "part_of_speech": self.part_of_speech,
            "matched_language": self.matched_language,
            "matched_form": self.matched_form,
            "gloss_language": self.gloss_language,
            "gloss": self.gloss,
            "translations": {
                language: list(values) for language, values in self.translations.items()
            },
            "source": self.source,
            "package_id": self.package_id,
            "package_name": self.package_name,
            "review_status": self.review_status,
            "reviewed_on": self.reviewed_on,
            "license_id": self.license_id,
            "locale": self.locale,
            "dialect": self.dialect,
            "morphology": dict(self.morphology or {}),
            "pronunciation": dict(self.pronunciation or {}),
            "examples": list(self.examples),
            "limitations": list(self.limitations),
            "attribution": list(self.attribution),
            "source_relative_path": self.source_relative_path,
            "source_title": self.source_title,
            "source_sha256": self.source_sha256,
            "source_url": self.source_url,
            "source_attribution": self.source_attribution,
            "source_ref": self.source_ref,
            "query_expression": self.query_expression,
            "match_type": self.match_type,
            "canonical_lemma": self.canonical_lemma,
            "form_features": dict(self.form_features or {}),
            "package_version": self.package_version,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "external_dataset": self.external_dataset,
            "deterministic": self.deterministic,
            "lexical_details": dict(self.lexical_details or {}),
            "lexical_relation": self.lexical_relation,
            "relation_target": self.relation_target,
            "variant_form": self.variant_form,
            "is_lexical_variant": self.is_lexical_variant,
        }


class LocalDictionary:
    def __init__(
        self,
        structured_packs: StructuredPackRepository | None = None,
        *,
        lexical_service: SpanishLexicalService | None = None,
    ) -> None:
        self._structured_packs = structured_packs
        self._lexical_service = lexical_service
        resource = files("elyndra.resources").joinpath("dictionary_core_v1.json")
        raw = resource.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("El diccionario local tiene un formato inválido.")
        entries = payload.get("entries", [])
        languages = payload.get("languages", {})
        if not isinstance(entries, list) or not isinstance(languages, dict):
            raise RuntimeError("El diccionario local no contiene entradas o idiomas válidos.")
        self._payload = payload
        self._entries = tuple(item for item in entries if isinstance(item, dict))
        self._languages = {
            str(code): str(name)
            for code, name in languages.items()
            if str(code) in _SUPPORTED_LANGUAGES
        }
        self._sha256 = hashlib.sha256(raw).hexdigest()
        self._index: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for entry in self._entries:
            forms = entry.get("forms", {})
            if not isinstance(forms, dict):
                continue
            for language, values in forms.items():
                if language not in self._languages or not isinstance(values, list):
                    continue
                for value in values:
                    normalized = normalize_dictionary_term(str(value))
                    if normalized:
                        self._index.setdefault((language, normalized), []).append(entry)

    @property
    def languages(self) -> dict[str, str]:
        return dict(self._languages)

    def status(self) -> dict[str, Any]:
        return {
            "id": str(self._payload.get("id", "")),
            "version": str(self._payload.get("version", "")),
            "license": str(self._payload.get("license", "")),
            "description": str(self._payload.get("description", "")),
            "sha256": self._sha256,
            "languages": dict(self._languages),
            "entry_count": len(self._entries),
            "offline": True,
            "model_required": False,
            "complete_dictionary": False,
            "starter_lexicon": True,
            "structured_packs": (
                self._structured_packs.status()
                if self._structured_packs is not None
                else {
                    "pack_count": 0,
                    "language_pack_count": 0,
                    "lexical_entry_count": 0,
                    "disk_backed": True,
                }
            ),
            "bounded_cache": True,
        }

    def lookup(
        self,
        term: str,
        *,
        language: str | None = None,
        output_language: str = "es",
        dialect: str | None = None,
        limit: int = 5,
    ) -> list[DictionaryMatch]:
        clean = normalize_dictionary_term(term)
        if not clean:
            raise ValueError("Debes indicar una palabra o expresión breve.")
        source_language = _normalize_language(language) if language else None
        target_language = _normalize_language(output_language)
        if (source_language or "es").split("-", 1)[0] == "es" and self._lexical_service:
            lexical_matches: list[DictionaryMatch] = []
            for item in self._lexical_service.lookup(term, limit=max(1, min(limit, 50))):
                payload = item.get("payload", {})
                gloss = str(
                    item.get("definition")
                    or payload.get("definition")
                    or item.get("expansion")
                    or payload.get("expansion")
                    or item.get("short_name")
                    or ""
                )
                if item.get("source") == "emoji_annotation":
                    keywords = item.get("keywords", [])
                    if isinstance(keywords, list) and keywords:
                        gloss = f"{gloss}. Palabras clave: {', '.join(map(str, keywords))}."
                    notes = str(item.get("ambiguity_notes", "")).strip()
                    if notes:
                        gloss = f"{gloss} {notes}"
                is_heuristic = item.get("source") == "informal_heuristic"
                lexical_matches.append(
                    DictionaryMatch(
                        concept_id=str(item.get("sense_id") or item.get("id") or "overlay"),
                        part_of_speech=str(item.get("part_of_speech") or "unknown"),
                        matched_language="es",
                        matched_form=str(item.get("matched_form") or item.get("lemma") or term),
                        gloss_language="es",
                        gloss=gloss,
                        translations={},
                        source=str(item.get("source", "language-pack")),
                        package_id=str(item.get("pack_id", "")),
                        license_id="" if is_heuristic else str(item.get("license_id", "")),
                        attribution=(
                            ("Regla heurística local de Elyndra; sin fuente externa.",)
                            if is_heuristic
                            else ((str(item["attribution"]),) if item.get("attribution") else ())
                        ),
                        source_url=str(item.get("source_url", "")),
                        query_expression=str(item.get("query_expression", term.strip())),
                        match_type=str(item.get("match_type", item.get("source", ""))),
                        canonical_lemma=str(item.get("canonical_lemma", "")),
                        form_features=(
                            item.get("form_features")
                            if isinstance(item.get("form_features"), dict) else {}
                        ),
                        package_version=str(item.get("pack_version", "")),
                        source_id=str(item.get("source_id", "")),
                        source_type="local_heuristic" if is_heuristic else "external_dataset",
                        external_dataset=not is_heuristic,
                        deterministic=is_heuristic,
                        lexical_details=dict(item),
                        lexical_relation=str(item.get("lexical_relation", "")),
                        relation_target=str(item.get("relation_target", "")),
                        variant_form=str(item.get("variant_form", "")),
                        is_lexical_variant=bool(item.get("is_lexical_variant", False)),
                    )
                )
            if lexical_matches:
                return lexical_matches
        candidates: list[tuple[str, dict[str, Any]]] = []
        core_source_language = source_language.split("-", 1)[0] if source_language else None
        if source_language:
            languages = (core_source_language,) if core_source_language in self._languages else ()
        else:
            languages = tuple(self._languages)
        for current_language in languages:
            for entry in self._index.get((current_language, clean), []):
                candidates.append((current_language, entry))
        matches: list[DictionaryMatch] = []
        seen: set[tuple[str, str]] = set()
        for matched_language, entry in candidates:
            concept_id = str(entry.get("id", ""))
            key = (concept_id, matched_language)
            if key in seen:
                continue
            seen.add(key)
            forms = entry.get("forms", {})
            glosses = entry.get("glosses", {})
            if not isinstance(forms, dict) or not isinstance(glosses, dict):
                continue
            matched_values = forms.get(matched_language, [])
            matched_form = next(
                (
                    str(value)
                    for value in matched_values
                    if normalize_dictionary_term(str(value)) == clean
                ),
                term.strip(),
            )
            gloss_language = target_language if target_language in glosses else "es"
            if gloss_language not in glosses:
                gloss_language = "en"
            translations = {
                code: tuple(str(value) for value in values)
                for code, values in forms.items()
                if code in self._languages and isinstance(values, list)
            }
            matches.append(
                DictionaryMatch(
                    concept_id=concept_id,
                    part_of_speech=str(entry.get("pos", "unknown")),
                    matched_language=matched_language,
                    matched_form=matched_form,
                    gloss_language=gloss_language,
                    gloss=str(glosses.get(gloss_language, "")),
                    translations=translations,
                )
            )
        if self._structured_packs is not None:
            structured = self._structured_packs.lookup_dictionary(
                term,
                language=source_language,
                output_language=target_language,
                dialect=dialect,
                limit=max(1, 12 - len(matches)),
            )
            for item in structured:
                translations_raw = item.get("translations", {})
                translations = {
                    str(code): tuple(str(value) for value in values)
                    for code, values in translations_raw.items()
                    if isinstance(values, list)
                }
                candidate = DictionaryMatch(
                    concept_id=str(item.get("entry_key", "")),
                    part_of_speech=str(item.get("part_of_speech", "unknown")),
                    matched_language=str(item.get("matched_language", "")),
                    matched_form=str(item.get("matched_form", term.strip())),
                    gloss_language=str(item.get("gloss_language", target_language)),
                    gloss=str(item.get("gloss", "")),
                    translations=translations,
                    source=str(item.get("source", "alexandria-structured-pack")),
                    package_id=str(item.get("package_id", "")),
                    package_name=str(item.get("package_name", "")),
                    review_status=str(item.get("review_status", "unreviewed")),
                    reviewed_on=str(item.get("reviewed_on", "")),
                    license_id=str(item.get("license_id", "")),
                    locale=str(item.get("matched_locale", item.get("package_locale", ""))),
                    dialect=str(item.get("package_dialect", "")),
                    morphology=(
                        item.get("morphology") if isinstance(item.get("morphology"), dict) else {}
                    ),
                    pronunciation=(
                        item.get("pronunciation")
                        if isinstance(item.get("pronunciation"), dict)
                        else {}
                    ),
                    examples=tuple(str(value) for value in item.get("examples", [])),
                    limitations=tuple(str(value) for value in item.get("limitations", [])),
                    attribution=tuple(str(value) for value in item.get("attribution", [])),
                    source_relative_path=str(item.get("source_relative_path", "")),
                    source_title=str(item.get("source_title", "")),
                    source_sha256=str(item.get("source_sha256", "")),
                    source_url=str(item.get("source_url", "")),
                    source_attribution=str(item.get("source_attribution", "")),
                    source_ref=str(item.get("source_ref", "")),
                )
                key = (
                    f"{candidate.package_id}:{candidate.concept_id}",
                    candidate.matched_language,
                )
                if key not in seen:
                    seen.add(key)
                    matches.append(candidate)
        return matches[:max(1, min(limit, 50))]

    def render_lookup(
        self,
        term: str,
        *,
        language: str | None = None,
        output_language: str = "es",
        dialect: str | None = None,
        limit: int = 5,
    ) -> tuple[str, dict[str, Any]]:
        per_group = max(1, min(limit, 20))
        matches = self.lookup(
            term,
            language=language,
            output_language=output_language,
            dialect=dialect,
            limit=min(per_group * 3, 50),
        )
        if not matches:
            return (
                f"No encontré «{term.strip()}» en el lexicón local inicial.",
                {
                    "term": term.strip(),
                    "found": False,
                    "matches": [],
                    "status": self.status(),
                },
            )
        message, visible = render_dictionary_matches(
            matches, term=term, per_group_limit=per_group, group_limit=3
        )
        return (
            message,
            {
                "term": term.strip(),
                "found": True,
                "matches": [item.to_dict() for item in matches],
                "visible_groups": visible,
                "status": self.status(),
            },
        )


def render_dictionary_matches(
    matches: list[DictionaryMatch], *, term: str, per_group_limit: int = 5,
    group_limit: int = 3,
) -> tuple[str, list[dict[str, Any]]]:
    groups: dict[tuple[str, str, str], list[DictionaryMatch]] = {}
    for match in matches:
        lemma = match.canonical_lemma or match.matched_form or term.strip()
        label = _human_pos(match)
        key = (normalize_dictionary_term(lemma), match.match_type, label)
        groups.setdefault(key, []).append(match)
    blocks: list[str] = []
    visible: list[dict[str, Any]] = []
    sources: list[str] = []
    omitted_groups = max(0, len(groups) - group_limit)
    for (_normalized, match_type, pos_label), items in list(groups.items())[:group_limit]:
        first = items[0]
        lemma = first.canonical_lemma or first.matched_form or term.strip()
        variants = list(dict.fromkeys(
            item.variant_form for item in items
            if item.is_lexical_variant and item.variant_form
        ))
        direct_variant = next(
            (variant for variant in variants if variant.casefold() == term.strip().casefold()), None
        )
        heading = f"{lemma} · {pos_label}"
        if match_type == "exact_form" and first.canonical_lemma:
            heading = f"{first.query_expression} — forma de {first.canonical_lemma} · {pos_label}"
        elif direct_variant:
            heading = f"{direct_variant} — variante ortográfica de {lemma} · {pos_label}"
        body: list[str] = [heading]
        if first.source == "emoji_annotation":
            body.extend(_render_emoji(first))
        elif first.source == "informal_heuristic":
            body.extend(_render_heuristic(first))
        else:
            definitions = list(dict.fromkeys(
                " ".join(item.gloss.split()) for item in items
                if item.gloss.strip() and not item.is_lexical_variant
            ))
            for index, definition in enumerate(definitions[:per_group_limit], 1):
                body.append(f"{index}. {definition}")
            translations = list(dict.fromkeys(
                f"{code}: {value}"
                for item in items
                for code, values in item.translations.items()
                for value in values if value.strip()
            ))
            if translations:
                body.append("Equivalencias: " + "; ".join(translations))
            if len(definitions) > per_group_limit:
                remaining = len(definitions) - per_group_limit
                body.append(f"Hay {remaining} {'sentido' if remaining == 1 else 'sentidos'} más.")
            if variants and not direct_variant:
                body.append(
                    "Variante ortográfica:\n"
                    + "\n".join(f"- {variant}" for variant in variants)
                )
        blocks.append("\n\n".join((body[0], "\n".join(body[1:]))).rstrip())
        visible.append({
            "lemma": lemma, "match_type": match_type, "part_of_speech": pos_label,
            "sense_count": min(len(items), per_group_limit),
        })
        for item in items:
            label = _human_source(item)
            if label and label not in sources:
                sources.append(label)
    if omitted_groups:
        blocks.append(f"Hay {omitted_groups} grupos más.")
    if sources:
        blocks.append("Fuentes:\n" + "\n".join(f"- {source}" for source in sources))
    return "\n\n".join(blocks), visible


def _human_pos(match: DictionaryMatch) -> str:
    technical = match.part_of_speech.casefold().strip()
    labels = {
        "noun": "sustantivo", "n": "sustantivo", "verb": "verbo", "v": "verbo",
        "adjective": "adjetivo", "adj": "adjetivo", "a": "adjetivo",
        "s": "adjetivo", "adverb": "adverbio", "adv": "adverbio", "r": "adverbio",
        "pronoun": "pronombre", "preposition": "preposición",
        "conjunction": "conjunción", "interjection": "interjección",
        "abbreviation": "abreviatura", "abbrev": "abreviatura",
    }
    if technical in labels:
        return labels[technical]
    details = match.lexical_details or {}
    if match.source == "emoji_annotation":
        return "emoji"
    if match.source == "informal_curated":
        return "expresión informal"
    if details.get("category") == "possible_laughter":
        return "posible risa escrita"
    if details.get("category") == "laughter":
        return "risa escrita"
    if details.get("category") == "keyboard_smash":
        return "secuencia de teclado"
    if match.source == "informal_heuristic":
        return "expresión paralingüística"
    return "categoría no determinada"


def _render_emoji(match: DictionaryMatch) -> list[str]:
    details = match.lexical_details or {}
    short_name = str(details.get("short_name") or match.gloss.split(".", 1)[0]).strip()
    keywords = details.get("keywords", [])
    if not isinstance(keywords, list):
        keywords = []
    normalized_name = normalize_dictionary_term(short_name)
    unique = list(dict.fromkeys(
        str(word).strip() for word in keywords
        if str(word).strip()
        and normalize_dictionary_term(str(word)) not in {"cara", normalized_name}
    ))
    lines = [short_name[:1].upper() + short_name[1:] + "."] if short_name else []
    if unique:
        lines.append("Palabras clave:\n" + ", ".join(unique) + ".")
    lines.append(
        "Puede expresar tristeza, llanto, emoción intensa, humor o exageración según el "
        "contexto. Por sí solo no permite inferir el estado emocional real."
    )
    return lines


def _render_heuristic(match: DictionaryMatch) -> list[str]:
    details = match.lexical_details or {}
    category = details.get("category")
    if category == "possible_laughter":
        confidence = float(details.get("confidence", 0.0))
        return [
            "La secuencia puede representar risa o una expresión paralingüística.",
            f"Confianza limitada: {confidence:.2f}.".replace(".", ",", 1),
        ]
    if category == "keyboard_smash":
        return ["Secuencia de teclado o expresión desconocida. No se clasifica como risa segura."]
    return [match.gloss] if match.gloss else []


def _human_source(match: DictionaryMatch) -> str:
    if match.source_type == "local_heuristic":
        return "Regla heurística local de Elyndra; sin fuente externa."
    names = {
        "elyndra-es-wiktionary": "Wikcionario",
        "elyndra-es-mcr-omw": "MCR/OMW",
        "elyndra-es-cldr": f"Unicode CLDR {match.package_version or '48.2'}",
        "elyndra-es-informal": "Registro informal de Elyndra",
    }
    name = names.get(match.package_id, match.package_name or match.package_id)
    licenses = {
        "CC-BY-SA-4.0 AND GFDL-1.3-or-later": "CC BY-SA 4.0 / GFDL",
        "CC-BY-3.0": "CC BY 3.0",
        "Unicode-3.0": "Unicode License v3",
    }
    license_label = licenses.get(match.license_id, match.license_id)
    return " — ".join(value for value in (name, license_label) if value)


def extract_dictionary_query(text: str) -> str | None:
    clean = " ".join(text.strip().strip("¿? ").split())
    if not clean or len(clean) > 120:
        return None
    for pattern in _QUERY_PATTERNS:
        match = pattern.match(clean)
        if match is None:
            continue
        term = match.group(1).strip(" \t\n\r\"'¿?.,:;")
        if 0 < len(term) <= 60 and len(term.split()) <= 5:
            return term
    return None


def extract_form_question(text: str) -> tuple[str, str] | None:
    clean = " ".join(text.strip().split())
    if len(clean) > 120:
        return None
    match = _FORM_QUESTION.match(clean)
    if match is None:
        return None
    form = match.group(1).strip(" ¿?.,:;\"'")
    lemma = match.group(2).strip(" ¿?.,:;\"'")
    if not form or not lemma or len(form) > 60 or len(lemma) > 60:
        return None
    return form, lemma


def extract_dictionary_relation(text: str) -> tuple[str, str] | None:
    clean = " ".join(text.strip().strip("¿? ").split())
    if len(clean) > 120:
        return None
    match = _RELATION_QUERY.match(clean)
    if match is None:
        return None
    relation = "related"
    if match.group(1).casefold().startswith("sin"):
        relation = "synonym"
    elif match.group(1).casefold().startswith("ant"):
        relation = "antonym"
    term = match.group(2).strip(" ¿?.,:;\"'")
    return (relation, term) if term and len(term) <= 60 else None


def normalize_dictionary_term(value: str) -> str:
    clean = unicodedata.normalize("NFKC", value).casefold().strip()
    if any("CJK" in unicodedata.name(char, "") for char in clean) or any(
        "HIRAGANA" in unicodedata.name(char, "") or "KATAKANA" in unicodedata.name(char, "")
        for char in clean
    ):
        return "".join(clean.split())
    decomposed = unicodedata.normalize("NFKD", clean)
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_marks.split())


def _normalize_language(value: str) -> str:
    clean = value.strip().casefold().replace("_", "-")
    aliases = {
        "spanish": "es",
        "español": "es",
        "english": "en",
        "inglés": "en",
        "ingles": "en",
        "japanese": "ja",
        "japonés": "ja",
        "japones": "ja",
        "chinese": "zh",
        "chino": "zh",
        "italian": "it",
        "italiano": "it",
        "french": "fr",
        "francés": "fr",
        "frances": "fr",
        "portuguese": "pt",
        "portugués": "pt",
        "portugues": "pt",
        "german": "de",
        "alemán": "de",
        "aleman": "de",
    }
    clean = aliases.get(clean, clean)
    if re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", clean):
        return clean
    raise ValueError(f"Etiqueta de idioma de diccionario no válida: {value}")
