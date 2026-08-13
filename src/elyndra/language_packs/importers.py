from __future__ import annotations

import gzip
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from elyndra.language_packs.constants import MAX_LINE_BYTES


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("rb") as handle:
        for number, raw in enumerate(handle, 1):
            if len(raw) > MAX_LINE_BYTES:
                raise ValueError(f"{path.name}:{number} supera 1 MiB por línea.")
            if not raw.strip():
                continue
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"{path.name}:{number} no es JSONL UTF-8 válido.") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path.name}:{number} debe contener un objeto.")
            yield value


def iter_wiktextract_jsonl(
    path: Path, *, stats: dict[str, int] | None = None
) -> Iterator[dict[str, Any]]:
    """Stream Kaikki raw Wiktextract JSONL into Elyndra's narrow canonical model."""
    counters = stats if stats is not None else {}
    for key in (
        "entries_read", "spanish_entries", "other_languages", "redirects",
        "invalid_records", "senses_without_definition", "forms", "relations",
        "examples_accepted", "examples_rejected_ambiguous_source",
        "examples_rejected_invalid",
        "examples_rejected_form_entry",
        "unresolved_form_entries",
    ):
        counters.setdefault(key, 0)
    try:
        with (
            gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb")
        ) as handle:
            number = 0
            while True:
                raw = handle.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    break
                number += 1
                counters["entries_read"] += 1
                if len(raw) > MAX_LINE_BYTES:
                    raise ValueError(f"{path.name}:{number} supera 1 MiB por línea.")
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    counters["invalid_records"] += 1
                    raise ValueError(
                        f"{path.name}:{number} no es JSONL UTF-8 válido."
                    ) from exc
                if not isinstance(value, dict):
                    counters["invalid_records"] += 1
                    raise ValueError(f"{path.name}:{number} debe contener un objeto.")
                if value.get("redirect") or value.get("redirects"):
                    counters["redirects"] += 1
                    continue
                if value.get("lang_code") != "es" or value.get("lang") != "Español":
                    counters["other_languages"] += 1
                    continue
                counters["spanish_entries"] += 1
                word = str(value.get("word", "")).strip()
                pos = str(value.get("pos", "unknown")).strip() or "unknown"
                if not word:
                    counters["invalid_records"] += 1
                    continue
                source_key = f"line:{number}:{word}:{pos}"
                senses = value.get("senses", [])
                if not isinstance(senses, list):
                    counters["invalid_records"] += 1
                    continue
                form_targets: list[str] = []
                accepted_examples_in_record = 0
                canonical_senses: list[dict[str, Any]] = []
                for order, sense in enumerate(senses):
                    if not isinstance(sense, dict):
                        counters["invalid_records"] += 1
                        continue
                    for field in ("form_of", "alt_of"):
                        links = sense.get(field, [])
                        if isinstance(links, list):
                            form_targets.extend(
                                str(item.get("word", "")).strip()
                                for item in links if isinstance(item, dict) and item.get("word")
                            )
                    glosses = sense.get("glosses", [])
                    definition = "\n".join(
                        str(item).strip() for item in glosses
                        if isinstance(item, str) and item.strip()
                    )
                    if not definition:
                        counters["senses_without_definition"] += 1
                    sense_index = str(sense.get("sense_index", order + 1))
                    examples: list[str] = []
                    for example in sense.get("examples", [])[:32]:
                        if not isinstance(example, dict) or not str(
                            example.get("text", "")
                        ).strip():
                            counters["examples_rejected_invalid"] += 1
                        elif example.get("ref"):
                            counters["examples_rejected_ambiguous_source"] += 1
                        else:
                            text = str(example["text"]).strip()
                            if len(text) <= 4096:
                                examples.append(text)
                                counters["examples_accepted"] += 1
                                accepted_examples_in_record += 1
                            else:
                                counters["examples_rejected_invalid"] += 1
                    relations: list[dict[str, str]] = []
                    for field, relation_type in (
                        ("synonyms", "synonym"), ("antonyms", "antonym"),
                        ("hypernyms", "hypernym"), ("hyponyms", "hyponym"),
                        ("related", "related"),
                    ):
                        for relation in value.get(field, []) or []:
                            if not isinstance(relation, dict):
                                continue
                            if str(relation.get("sense_index", sense_index)) != sense_index:
                                continue
                            target = str(relation.get("word", "")).strip()
                            if target:
                                relations.append({"type": relation_type, "target_term": target})
                                counters["relations"] += 1
                    canonical_senses.append({
                        "id": f"{source_key}:local-sense:{sense_index}:order:{order}",
                        "definition": definition,
                        "examples": examples,
                        "relations": relations,
                        "labels": list(sense.get("tags", []))[:32],
                    })
                if form_targets:
                    counters["examples_accepted"] -= accepted_examples_in_record
                    counters["examples_rejected_form_entry"] += accepted_examples_in_record
                    for target in dict.fromkeys(form_targets):
                        yield {
                            "type": "form-link", "id": f"{source_key}:form-of:{target}",
                            "form": word, "lemma": target, "features": {"source_pos": pos},
                        }
                        counters["forms"] += 1
                    continue
                if "form-of" in value.get("tags", []):
                    counters["examples_accepted"] -= accepted_examples_in_record
                    counters["examples_rejected_form_entry"] += accepted_examples_in_record
                    counters["unresolved_form_entries"] += 1
                    continue
                forms = []
                for item in value.get("forms", []) or []:
                    if not isinstance(item, dict):
                        continue
                    form = str(item.get("form", "")).strip()
                    if form and form != word:
                        forms.append({"form": form, "features": {"tags": item.get("tags", [])}})
                        counters["forms"] += 1
                yield {
                    "type": "lexeme", "id": source_key, "lemma": word, "pos": pos,
                    "forms": forms, "senses": canonical_senses,
                    "features": {"tags": value.get("tags", [])},
                }
    except (EOFError, gzip.BadGzipFile) as exc:
        raise ValueError(f"{path.name} es un gzip truncado o inválido.") from exc


def iter_wordnet_lmf(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("rb") as probe:
        header = probe.read(64 * 1024).upper()
    if b"<!DOCTYPE" in header or b"<!ENTITY" in header:
        raise ValueError("WordNet-LMF no puede contener DTD ni entidades XML.")
    try:
        for _event, element in ET.iterparse(path, events=("end",)):
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "LexicalEntry":
                lemma = next(
                    (child for child in element.iter() if child.tag.rsplit("}", 1)[-1] == "Lemma"),
                    None,
                )
                if lemma is not None:
                    yield {
                        "type": "lexeme",
                        "id": element.attrib.get("id", ""),
                        "lemma": lemma.attrib.get("writtenForm", ""),
                        "pos": lemma.attrib.get("partOfSpeech", "unknown"),
                        "forms": [
                            child.attrib.get("writtenForm", "")
                            for child in element.iter()
                            if child.tag.rsplit("}", 1)[-1] == "FormRepresentation"
                        ],
                        "senses": [
                            {
                                "id": child.attrib.get("id", ""),
                                "synset": child.attrib.get("synset", ""),
                            }
                            for child in element.iter()
                            if child.tag.rsplit("}", 1)[-1] == "Sense"
                        ],
                    }
                element.clear()
            elif tag == "Synset":
                definitions = [
                    (child.text or "").strip()
                    for child in element.iter()
                    if child.tag.rsplit("}", 1)[-1] == "Definition" and (child.text or "").strip()
                ]
                yield {
                    "type": "synset",
                    "id": element.attrib.get("id", ""),
                    "pos": element.attrib.get("partOfSpeech", "unknown"),
                    "definitions": definitions,
                    "relations": [
                        {
                            "type": child.attrib.get("relType", "related"),
                            "target": child.attrib.get("target", ""),
                        }
                        for child in element.iter()
                        if child.tag.rsplit("}", 1)[-1] == "SynsetRelation"
                    ],
                }
                element.clear()
    except ET.ParseError as exc:
        raise ValueError(f"WordNet-LMF XML inválido: {exc}") from exc


def iter_omw_spanish_tab(path: Path) -> Iterator[dict[str, Any]]:
    """Stream the official OMW 1.0 Spanish MCR tab format.

    The MCR artifact groups records by record type rather than by synset. Lemmas can
    therefore be emitted immediately, while adjacent definitions for the same synset
    are combined into one canonical synset record. Examples are intentionally omitted:
    the source only identifies their synset, while Elyndra stores examples by sense.
    """
    header_seen = False
    pending_synset = ""
    pending_pos = "unknown"
    pending_definitions: list[str] = []

    def flush_definitions() -> dict[str, Any] | None:
        if not pending_synset:
            return None
        return {
            "type": "synset",
            "id": pending_synset,
            "pos": pending_pos,
            "definitions": list(pending_definitions),
            "relations": [],
        }

    with path.open("rb") as handle:
        for number, raw in enumerate(handle, 1):
            if len(raw) > MAX_LINE_BYTES:
                raise ValueError(f"{path.name}:{number} supera 1 MiB por línea.")
            try:
                line = raw.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError as exc:
                raise ValueError(f"{path.name}:{number} no es UTF-8 válido.") from exc
            if not line:
                continue
            if line.startswith("#"):
                fields = line[1:].strip().split("\t")
                if len(fields) < 4 or fields[1].strip() != "spa":
                    raise ValueError("El TSV OMW debe declarar el módulo español spa.")
                if "CC BY 3.0" not in fields[3].upper():
                    raise ValueError("El TSV MCR español debe declarar CC BY 3.0.")
                header_seen = True
                continue
            if not header_seen:
                raise ValueError("El TSV OMW carece de cabecera de procedencia.")
            fields = line.split("\t")
            if len(fields) < 3:
                raise ValueError(f"{path.name}:{number} tiene un registro incompleto.")
            synset, record_type = fields[0].strip(), fields[1].strip()
            if not re.fullmatch(r"\d{8}-[nvars]", synset):
                raise ValueError(f"{path.name}:{number} tiene un synset inválido.")
            pos = "a" if synset.endswith("-s") else synset[-1]
            if record_type == "spa:lemma":
                lemma = fields[2].strip()
                if not lemma:
                    raise ValueError(f"{path.name}:{number} contiene un lema vacío.")
                yield {
                    "type": "lexeme",
                    "id": f"{synset}:lemma:{number}",
                    "lemma": lemma,
                    "pos": pos,
                    "forms": [],
                    "senses": [{"id": f"{synset}:sense:{number}", "synset": synset}],
                }
            elif record_type == "spa:def":
                definition = "\t".join(fields[3:] if len(fields) > 3 else fields[2:]).strip()
                if not definition:
                    continue
                if pending_synset and pending_synset != synset:
                    record = flush_definitions()
                    if record is not None:
                        yield record
                    pending_definitions.clear()
                pending_synset = synset
                pending_pos = pos
                pending_definitions.append(definition)
            elif record_type == "spa:exe":
                continue
            else:
                raise ValueError(f"{path.name}:{number} usa {record_type!r}, no autorizado.")
    record = flush_definitions()
    if record is not None:
        yield record


def iter_cldr_annotations(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("rb") as probe:
        header = probe.read(64 * 1024).upper()
    official_doctype = b'<!DOCTYPE LDML SYSTEM "../../COMMON/DTD/LDML.DTD">'
    doctypes = header.count(b"<!DOCTYPE")
    if b"<!ENTITY" in header or doctypes > 1 or (doctypes == 1 and official_doctype not in header):
        raise ValueError("CLDR no puede contener DTD ni entidades XML.")
    try:
        for _event, element in ET.iterparse(path, events=("end",)):
            if element.tag.rsplit("}", 1)[-1] != "annotation":
                continue
            sequence = element.attrib.get("cp", "")
            text = (element.text or "").strip()
            if sequence and text:
                is_name = element.attrib.get("type") == "tts"
                yield {
                    "type": "emoji",
                    "id": f"cldr-{sequence.encode().hex()}-{'name' if is_name else 'keywords'}",
                    "emoji": sequence,
                    "short_name": text if is_name else text.split("|")[0].strip(),
                    "keywords": [item.strip() for item in text.split("|") if item.strip()][:50],
                    "categories": ["cldr_annotation"],
                    "ambiguity_notes": "Anotación léxica; no determina un estado emocional.",
                }
            element.clear()
    except ET.ParseError as exc:
        raise ValueError(f"CLDR XML inválido: {exc}") from exc


def normalize_term(value: str) -> str:
    import unicodedata

    clean = unicodedata.normalize("NFKC", value).casefold().strip()
    decomposed = unicodedata.normalize("NFKD", clean)
    folded = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9ñü\s'-]", " ", folded).split())
