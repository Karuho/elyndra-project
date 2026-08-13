from __future__ import annotations

import json
import re
import sqlite3
from contextlib import suppress
from pathlib import Path
from typing import Any

from elyndra.language_packs.importers import normalize_term
from elyndra.language_packs.overlays import AccountLanguageOverlayRepository
from elyndra.language_packs.registry import LanguagePackRegistry


class SpanishLexicalService:
    def __init__(
        self,
        registry: LanguagePackRegistry,
        overlays: AccountLanguageOverlayRepository | None = None,
    ) -> None:
        self.registry = registry
        self.overlays = overlays

    def lookup(self, term: str, *, limit: int = 20) -> list[dict[str, Any]]:
        bounded = max(1, min(limit, 50))
        results: list[dict[str, Any]] = []
        if self.overlays is not None:
            results.extend(self.overlays.lookup(term, limit=bounded))
        for pack in self.registry.list_all(enabled_only=True):
            results.extend(
                self._lookup_pack(
                    self.registry.database_path(pack), term, bounded, pack, include_informal=True
                )
            )
        exact_sources = {str(item.get("source", "")) for item in results}
        if "exact_form" in exact_sources:
            results = [
                item for item in results
                if item.get("source") in {"overlay", "informal_curated", "exact_form"}
            ]
        elif "exact_lemma" in exact_sources:
            results = [item for item in results if item.get("source") != "fts"]
        _annotate_orthographic_variants(results)
        if not results:
            classification = classify_informal_expression(term)
            if classification is not None:
                results.append(classification)
        return self._deduplicate(results, term)[:bounded]

    @staticmethod
    def _deduplicate(results: list[dict[str, Any]], term: str) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        for item in results:
            definition = " ".join(str(item.get("definition", "")).casefold().split())
            has_visible = any(
                item.get(field) for field in (
                    "definition", "expansion", "keywords_json", "short_name",
                    "ambiguity_notes", "synset_id", "matched_form", "category",
                )
            )
            if not has_visible:
                continue
            canonical = str(
                item.get("canonical_lemma")
                or item.get("lemma")
                or item.get("expression")
                or term
            ).strip()
            pos = normalize_part_of_speech(str(item.get("part_of_speech", "unknown")))
            item["query_expression"] = term.strip()
            item["match_type"] = str(item.get("source", "unknown"))
            item["canonical_lemma"] = canonical
            item["part_of_speech_original"] = str(item.get("part_of_speech", "unknown"))
            item["part_of_speech"] = pos
            if item.get("form_features_json"):
                try:
                    item["form_features"] = json.loads(str(item["form_features_json"]))
                except json.JSONDecodeError:
                    item["form_features"] = {}
            key = (
                str(item.get("pack_id", "")), canonical.casefold(), pos,
                str(item.get("sense_id") or item.get("id") or ""), definition,
                str(item.get("relation", "")),
                normalize_term(str(item.get("target_term", ""))),
            )
            if key in seen:
                continue
            seen.add(key)
            output.append(item)
        return _merge_emoji_results(output)

    def senses(self, term: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return [
            item for item in self.lookup(term, limit=limit)
            if item.get("sense_id") and not item.get("is_lexical_variant")
        ]

    def semantic_expansions(self, term: str, *, limit: int = 2) -> dict[str, Any]:
        matches = self.lookup(term, limit=6)
        informal = [item for item in matches if item.get("source") == "informal_curated"]
        if len(informal) == 1:
            return {
                "terms": normalize_term(str(informal[0].get("expansion", ""))).split()[:limit],
                "ambiguous": bool(informal[0].get("ambiguity_notes")),
                "source": "informal_curated",
            }
        senses = {str(item["sense_id"]): item for item in matches if item.get("sense_id")}
        if len(senses) != 1:
            return {"terms": [], "ambiguous": len(senses) > 1, "source": "language_pack"}
        sense_id = next(iter(senses))
        related = self.related(term, relation="synonym", sense_id=sense_id, limit=limit)
        return {
            "terms": [normalize_term(str(item["lemma"])) for item in related[:limit]],
            "ambiguous": False,
            "source": "language_pack",
        }

    def related(
        self, term: str, *, relation: str, sense_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        if relation not in {
            "synonym", "antonym", "hypernym", "hyponym", "meronym", "holonym", "related"
        }:
            raise ValueError("Relación léxica no soportada.")
        output: list[dict[str, Any]] = []
        for pack in self.registry.list_all(enabled_only=True):
            connection = self._connect(self.registry.database_path(pack))
            try:
                if relation == "synonym":
                    rows = connection.execute(
                        """SELECT DISTINCT l.lemma,l.part_of_speech,s.id AS sense_id
                        FROM synset_members mine JOIN synset_members other
                        ON other.synset_id=mine.synset_id AND other.sense_id<>mine.sense_id
                        JOIN senses s ON s.id=other.sense_id JOIN lexemes l ON l.id=s.lexeme_id
                        WHERE mine.sense_id=? ORDER BY l.normalized_lemma,l.id LIMIT ?""",
                        (sense_id, max(1, min(limit, 50))),
                    ).fetchall()
                    term_rows = self._relation_term_rows(
                        connection, sense_id, relation, limit
                    )
                    rows = list(rows) + list(term_rows)
                else:
                    rows = connection.execute(
                        """SELECT l.lemma,l.part_of_speech,s.id AS sense_id
                        FROM sense_relations r JOIN senses s ON s.id=r.target_sense_id
                        JOIN lexemes l ON l.id=s.lexeme_id
                        WHERE r.source_sense_id=? AND r.relation_type=?
                        ORDER BY l.normalized_lemma,l.id LIMIT ?""",
                        (sense_id, relation, max(1, min(limit, 50))),
                    ).fetchall()
                    term_rows = self._relation_term_rows(
                        connection, sense_id, relation, limit
                    )
                    rows = list(rows) + list(term_rows)
                output.extend(
                    dict(row) | {"relation": relation, "pack_id": pack["logical_pack_id"]}
                    for row in rows
                )
            finally:
                connection.close()
        return output[:limit]

    @staticmethod
    def _relation_term_rows(
        connection: sqlite3.Connection, sense_id: str, relation: str, limit: int
    ) -> list[sqlite3.Row]:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sense_relation_terms'"
        ).fetchone()
        if exists is None:
            return []
        return connection.execute(
            "SELECT target_term AS lemma,'' AS part_of_speech,NULL AS sense_id "
            "FROM sense_relation_terms WHERE source_sense_id=? "
            "AND relation_type=? ORDER BY normalized_target_term LIMIT ?",
            (sense_id, relation, max(1, min(limit, 50))),
        ).fetchall()

    @staticmethod
    def _lookup_pack(
        path: Path, term: str, limit: int, pack: dict[str, Any], *, include_informal: bool
    ) -> list[dict[str, Any]]:
        normalized = normalize_term(term)
        connection = SpanishLexicalService._connect(path)
        try:
            emoji_rows = connection.execute(
                "SELECT e.*,src.license_id,src.attribution,src.source_url "
                "FROM emoji_annotations e JOIN sources src ON src.id=e.source_id "
                "WHERE e.language='es' AND e.emoji_sequence=? ORDER BY e.id LIMIT ?",
                (term.strip(), limit),
            ).fetchall()
            if emoji_rows:
                return [
                    dict(row)
                    | {
                        "source": "emoji_annotation",
                        "pack_id": pack["logical_pack_id"],
                        "pack_version": pack["version"],
                        "attribution_required": True,
                    }
                    for row in emoji_rows
                ]
            if include_informal:
                rows = connection.execute(
                    "SELECT i.*,src.license_id,src.attribution,src.source_url "
                    "FROM informal_entries i JOIN sources src ON src.id=i.source_id "
                    "WHERE i.language='es' AND i.normalized_expression=? "
                    "ORDER BY i.id LIMIT ?",
                    (normalized, limit),
                ).fetchall()
                if rows:
                    return [
                        dict(row)
                        | {
                            "source": "informal_curated",
                            "pack_id": pack["logical_pack_id"],
                            "pack_version": pack["version"],
                        }
                        for row in rows
                    ]
            if not normalized:
                return []
            rows = connection.execute(
                """SELECT l.*,s.id AS sense_id,d.definition,
                (SELECT json_group_array(ul.label) FROM usage_labels ul
                 WHERE ul.owner_type='sense' AND ul.owner_id=s.id) AS sense_labels_json,
                src.license_id,src.attribution,src.source_url
                FROM lexemes l LEFT JOIN senses s ON s.lexeme_id=l.id
                LEFT JOIN sense_definitions d ON d.sense_id=s.id
                JOIN sources src ON src.id=l.source_id
                WHERE l.language='es' AND l.normalized_lemma=?
                ORDER BY l.part_of_speech,s.sense_order,d.display_order LIMIT ?""",
                (normalized, limit),
            ).fetchall()
            source = "exact_lemma"
            if not rows:
                rows = connection.execute(
                    """SELECT l.*,s.id AS sense_id,d.definition,f.form AS matched_form,
                    f.features_json AS form_features_json,
                    src.license_id,src.attribution,src.source_url
                    FROM lexeme_forms f JOIN lexemes l ON l.id=f.lexeme_id
                    LEFT JOIN senses s ON s.lexeme_id=l.id
                    LEFT JOIN sense_definitions d ON d.sense_id=s.id
                    JOIN sources src ON src.id=l.source_id
                    WHERE f.normalized_form=? ORDER BY l.normalized_lemma,s.sense_order LIMIT ?""",
                    (normalized, limit),
                ).fetchall()
                source = "exact_form"
            if not rows and normalized:
                query = " ".join(
                    f'"{token.replace(chr(34), "")}"*' for token in normalized.split()[:8]
                )
                rows = connection.execute(
                    """SELECT l.*,s.id AS sense_id,d.definition,
                    src.license_id,src.attribution,src.source_url
                    FROM lexical_terms_fts f JOIN lexemes l ON l.id=f.record_id
                    LEFT JOIN senses s ON s.lexeme_id=l.id
                    LEFT JOIN sense_definitions d ON d.sense_id=s.id
                    JOIN sources src ON src.id=l.source_id
                    WHERE lexical_terms_fts MATCH ? ORDER BY rank,l.normalized_lemma LIMIT ?""",
                    (query, limit),
                ).fetchall()
                source = "fts"
            return [
                dict(row)
                | {
                    "source": source,
                    "pack_id": pack["logical_pack_id"],
                    "pack_version": pack["version"],
                    "attribution_required": True,
                }
                for row in rows
            ]
        finally:
            connection.close()

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA cache_size=-2048")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.set_progress_handler(lambda: 0, 10_000)
        return connection


def likely_laughter(value: str) -> dict[str, Any] | None:
    clean = value.casefold().strip()
    if re.fullmatch(r"(?:ja|je|ji|jo|ju|js){3,20}", clean):
        return {"category": "laughter", "confidence": 0.82, "ambiguous": True}
    if 8 <= len(clean) <= 40 and set(clean) <= set("jdas") and clean.count("j") >= 2:
        return {"category": "possible_laughter", "confidence": 0.58, "ambiguous": True}
    return None


def classify_informal_expression(value: str) -> dict[str, Any] | None:
    clean = value.casefold().strip()
    if not clean or len(clean) > 64:
        return None
    laughter = likely_laughter(clean)
    if laughter is not None:
        category = str(laughter["category"])
        return laughter | {
            "id": f"heuristic:{category}:{clean}",
            "expression": value.strip(),
            "expansion": (
                "risa escrita" if category == "laughter"
                else "posible risa o expresión paralingüística"
            ),
            "source": "informal_heuristic",
            "pack_id": "elyndra-local-heuristics",
            "ambiguity_notes": "Interpretación contextual; no permite inferir una emoción real.",
        }
    if 6 <= len(clean) <= 32 and re.fullmatch(r"[asdfghjklñ]+", clean):
        return {
            "id": f"heuristic:keyboard-smash:{clean}",
            "expression": value.strip(), "expansion": "keyboard smash o expresión desconocida",
            "category": "keyboard_smash", "confidence": 0.35, "ambiguous": True,
            "source": "informal_heuristic", "pack_id": "elyndra-local-heuristics",
            "ambiguity_notes": "No se clasifica como risa segura ni como estado emocional.",
        }
    return None


def normalize_part_of_speech(value: str) -> str:
    return {
        "n": "noun", "noun": "noun", "v": "verb", "verb": "verb",
        "a": "adjective", "s": "adjective", "adj": "adjective",
        "adjective": "adjective", "r": "adverb", "adv": "adverb",
        "adverb": "adverb",
    }.get(value.casefold().strip(), value or "unknown")


def _annotate_orthographic_variants(results: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in results:
        if item.get("source") != "exact_lemma" or not item.get("lemma"):
            continue
        key = (
            str(item.get("pack_id", "")),
            normalize_term(str(item["lemma"])),
            normalize_part_of_speech(str(item.get("part_of_speech", "unknown"))),
        )
        groups.setdefault(key, []).append(item)
    variant_labels = {"alt-of", "alternative", "obsolete", "form-of"}
    for items in groups.values():
        by_lemma: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            by_lemma.setdefault(str(item["lemma"]), []).append(item)
        if len(by_lemma) < 2:
            continue
        variants: set[str] = set()
        for lemma, lemma_items in by_lemma.items():
            labels = {
                str(label).casefold()
                for item in lemma_items
                for label in _json_string_list(item.get("sense_labels_json"))
            }
            if labels & variant_labels:
                variants.add(lemma)
        canonical = next((lemma for lemma in by_lemma if lemma not in variants), None)
        if canonical is None:
            continue
        for variant in variants:
            for item in by_lemma[variant]:
                item.update({
                    "is_lexical_variant": True,
                    "lexical_relation": "orthographic_variant",
                    "relation_target": canonical,
                    "variant_form": variant,
                    "canonical_lemma": canonical,
                })


def _json_string_list(value: Any) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _merge_emoji_results(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    emoji = [item for item in items if item.get("source") == "emoji_annotation"]
    if not emoji:
        return items
    base = dict(next(
        (item for item in emoji if str(item.get("pack_id", "")).endswith("cldr")),
        emoji[0],
    ))
    keywords: list[str] = []
    names: list[str] = []
    for item in emoji:
        name = str(item.get("short_name", "")).strip()
        if name:
            names.append(name)
        with suppress(json.JSONDecodeError):
            keywords.extend(json.loads(str(item.get("keywords_json", "[]"))))
    base["short_name"] = next((name for name in names if name), "")
    base["keywords"] = list(dict.fromkeys(str(word) for word in keywords if word))
    base["keywords_json"] = json.dumps(base["keywords"], ensure_ascii=False)
    base["contributing_packs"] = list(dict.fromkeys(
        str(item.get("pack_id", "")) for item in emoji if item.get("pack_id")
    ))
    base["ambiguity_notes"] = (
        "Puede ser llanto, tristeza, emoción intensa, humor o exageración según "
        "el contexto; no permite inferir por sí solo el estado emocional real."
    )
    return [base, *(item for item in items if item.get("source") != "emoji_annotation")]
