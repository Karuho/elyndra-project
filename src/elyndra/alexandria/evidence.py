from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from elyndra.alexandria.query import AlexandriaQueryPlan, AlexandriaTask

_LABEL_PREFIX = re.compile(
    r"^(?:HECHO|REGLA|PATRÓN|PATRON|ANTIPATRÓN|ANTIPATRON|"
    r"VERIFICAR|RIESGO|NOTA(?:-[A-ZÁÉÍÓÚÑ]+)?|EJEMPLO)\s*:\s*",
    re.IGNORECASE,
)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ0-9`])")
_WORDS = re.compile(r"[\wÀ-ÖØ-öø-ÿĀ-ž.-]+", re.UNICODE)

_STOPWORDS = {
    "segun",
    "alejandria",
    "diferencia",
    "cuando",
    "como",
    "cual",
    "cuales",
    "que",
    "una",
    "uno",
    "unos",
    "unas",
    "para",
    "por",
    "con",
    "sin",
    "del",
    "los",
    "las",
    "este",
    "esta",
    "estos",
    "estas",
    "deberia",
    "tiene",
    "sentido",
    "problemas",
    "resuelve",
    "implica",
    "necesito",
}

_SYNONYMS: dict[str, tuple[str, ...]] = {
    "interfaz": ("interface",),
    "interface": ("interfaz",),
    "transaccion": ("transaction",),
    "transaction": ("transaccion",),
    "inyeccion": ("injection",),
    "injection": ("inyeccion",),
    "despliegue": ("deployment",),
    "deployment": ("despliegue",),
    "prueba": ("test", "testing"),
    "testing": ("prueba", "test"),
}

_ANCHORS = {
    "group_concat",
    "pdo",
    "mariadb",
    "mysql",
    "transaccion",
    "transaction",
    "stock",
    "interfaz",
    "interface",
    "phpstan",
    "phpunit",
    "psalm",
    "pest",
    "webhook",
    "opcache",
    "fpm",
    "csrf",
    "xss",
    "composer",
    "autoload",
    "prepared",
    "lint",
    "sintaxis",
}


@dataclass(frozen=True, slots=True)
class AlexandriaEvidenceAnswer:
    text: str
    confidence: float
    used_unit_ids: tuple[int, ...]
    unsupported_tasks: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _EvidenceCandidate:
    text: str
    unit_id: int
    score: int
    concepts: frozenset[str]


def build_evidence_answer(
    plan: AlexandriaQueryPlan,
    library_units: list[dict[str, Any]],
) -> AlexandriaEvidenceAnswer | None:
    if not plan.strict or not library_units:
        return None

    task_answers: list[tuple[AlexandriaTask, list[_EvidenceCandidate]]] = []
    unsupported: list[int] = []
    task_confidences: list[float] = []
    selected_unit_ids: set[int] = set()

    for task in plan.task_plans:
        if not task.answerable:
            continue
        candidates, required_concepts = _candidates_for_task(task, library_units)
        selected = _select_candidates(
            candidates,
            required_concepts=required_concepts,
            limit=3,
        )
        task_answers.append((task, selected))
        if not selected:
            unsupported.append(task.index)
            task_confidences.append(0.0)
            continue
        selected_unit_ids.update(candidate.unit_id for candidate in selected)
        best = selected[0].score
        coverage = _concept_coverage(selected, required_concepts)
        task_confidences.append(min(1.0, 0.4 + best / 28 + coverage * 0.2))

    if not task_answers:
        return None

    used_unit_ids = tuple(
        int(item["unit_id"])
        for item in library_units
        if int(item.get("unit_id") or 0) in selected_unit_ids
    )
    citation_map = {
        unit_id: index for index, unit_id in enumerate(used_unit_ids, start=1)
    }
    multiple = plan.answerable_task_count > 1 or bool(plan.deterministic_sections)
    sections: list[str] = []

    for task, selected in task_answers:
        title = _task_title(task)
        heading = f"{task.index}. {title}" if multiple else title
        if not selected:
            sections.append(
                heading
                + "\nAlejandría no contiene evidencia suficientemente específica "
                "para responder esta parte."
            )
            continue
        lines = [heading]
        specialized = _specialized_task_lines(task, selected, citation_map)
        if specialized:
            lines.extend(specialized)
        else:
            for candidate in selected:
                citation = citation_map[candidate.unit_id]
                lines.append(f"- {candidate.text} [A{citation}]")
        sections.append("\n".join(lines))

    used_units = [
        item
        for item in library_units
        if int(item.get("unit_id") or 0) in selected_unit_ids
    ]
    if any(item.get("review_status") != "reviewed" for item in used_units):
        sections.append(
            "Aviso: al menos una fuente utilizada todavía está marcada como no revisada."
        )

    confidence = (
        round(sum(task_confidences) / len(task_confidences), 3)
        if task_confidences
        else 0.0
    )
    return AlexandriaEvidenceAnswer(
        text="\n\n".join(sections),
        confidence=confidence,
        used_unit_ids=used_unit_ids,
        unsupported_tasks=tuple(unsupported),
    )


def _specialized_task_lines(
    task: AlexandriaTask,
    selected: list[_EvidenceCandidate],
    citation_map: dict[int, int],
) -> list[str]:
    normalized = _normalize(task.text)
    if not all(term in normalized for term in ("phpstan", "phpunit")):
        return []
    if re.search(r"(?<!\w)php\s*-\s*l(?!\w)", normalized) is None:
        return []

    lines: list[str] = []
    definitions = (
        (
            "php-l",
            "`php -l` comprueba la sintaxis de un archivo PHP sin ejecutar su código.",
        ),
        (
            "phpstan",
            "`PHPStan` realiza análisis estático de tipos, nullabilidad y contratos inferibles.",
        ),
        (
            "phpunit",
            "`PHPUnit` ejecuta pruebas automatizadas para verificar comportamiento.",
        ),
    )
    for concept, statement in definitions:
        candidate = next(
            (item for item in selected if concept in item.concepts),
            None,
        )
        if candidate is None:
            continue
        citation = citation_map[candidate.unit_id]
        lines.append(f"- {statement} [A{citation}]")
    return lines if len(lines) >= 2 else []


def _candidates_for_task(
    task: AlexandriaTask,
    library_units: list[dict[str, Any]],
) -> tuple[list[_EvidenceCandidate], tuple[str, ...]]:
    terms = _terms(task.text)
    normalized_task = _normalize(task.text)
    anchors = {term for term in terms if term in _ANCHORS or _looks_technical(term)}
    if "webhook" in normalized_task:
        anchors.update(
            {
                "webhook",
                "firma",
                "timestamp",
                "replay",
                "idempotencia",
                "monto",
                "moneda",
                "proveedor",
            }
        )
    if "opcache" in normalized_task:
        anchors.update(
            {"opcache", "bytecode", "compilado", "consultas", "algoritmos"}
        )
    required_concepts = _required_concepts_for_task(normalized_task)
    candidates: list[_EvidenceCandidate] = []

    for unit in library_units:
        task_indices = {
            int(value) for value in unit.get("retrieval_task_indices", [])
        }
        if task_indices and task.index not in task_indices:
            continue
        if not bool(unit.get("retrieval_domain_exact")):
            continue
        if float(unit.get("relevance_score") or 0.0) <= 0:
            continue

        heading = str(unit.get("heading") or "")
        if _weak_heading(heading):
            continue
        unit_id = int(unit.get("unit_id") or 0)
        for segment in _segments(str(unit.get("content") or "")):
            score = _score_segment(segment, heading, terms, anchors)
            if score <= 0:
                continue
            contextualized = _contextualize(segment, heading)
            concepts = frozenset(
                _concepts_for_text(_normalize(f"{heading} {contextualized}"))
            )
            candidates.append(
                _EvidenceCandidate(
                    text=contextualized,
                    unit_id=unit_id,
                    score=score,
                    concepts=concepts,
                )
            )

    candidates.sort(key=lambda item: (-item.score, item.unit_id, item.text))
    return candidates, required_concepts


def _select_candidates(
    candidates: list[_EvidenceCandidate],
    *,
    required_concepts: tuple[str, ...],
    limit: int,
) -> list[_EvidenceCandidate]:
    if not candidates:
        return []

    selected: list[_EvidenceCandidate] = []
    fingerprints: list[str] = []

    def add(candidate: _EvidenceCandidate) -> bool:
        fingerprint = _normalize(candidate.text)
        if any(
            fingerprint == previous
            or fingerprint in previous
            or previous in fingerprint
            for previous in fingerprints
        ):
            return False
        selected.append(candidate)
        fingerprints.append(fingerprint)
        return True

    for concept in required_concepts:
        for candidate in candidates:
            if concept in candidate.concepts and add(candidate):
                break
        if len(selected) >= limit:
            return selected

    best_score = candidates[0].score
    minimum_score = max(3, int(best_score * 0.62))
    for candidate in candidates:
        if candidate.score < minimum_score:
            continue
        add(candidate)
        if len(selected) >= limit:
            break
    return selected


def _concept_coverage(
    selected: list[_EvidenceCandidate],
    required: tuple[str, ...],
) -> float:
    if not required:
        return 1.0
    covered = set().union(*(candidate.concepts for candidate in selected))
    return len(set(required) & covered) / len(set(required))


def _segments(content: str) -> list[str]:
    clean = _strip_front_matter(content)
    segments: list[str] = []
    paragraph: list[str] = []
    code: list[str] = []
    list_prefix = ""
    list_items: list[str] = []
    in_code = False

    def take_paragraph() -> str:
        if not paragraph:
            return ""
        joined = " ".join(item.strip() for item in paragraph if item.strip())
        paragraph.clear()
        return _LABEL_PREFIX.sub("", joined).strip()

    def append_prose(value: str) -> None:
        clean_value = _LABEL_PREFIX.sub("", value).strip()
        if not clean_value:
            return
        for sentence in _SENTENCE_BOUNDARY.split(clean_value):
            candidate = _clean_segment(sentence)
            if candidate:
                segments.append(candidate)

    def flush_paragraph(*, preserve_list_prefix: bool = False) -> None:
        nonlocal list_prefix
        joined = take_paragraph()
        if not joined:
            return
        if preserve_list_prefix and joined.endswith(":"):
            list_prefix = joined[:-1].strip()
            return
        append_prose(joined)

    def flush_list() -> None:
        nonlocal list_prefix, list_items
        if not list_items:
            list_prefix = ""
            return
        joined_items = "; ".join(item.rstrip(".;") for item in list_items)
        combined = f"{list_prefix}: {joined_items}." if list_prefix else f"{joined_items}."
        candidate = _clean_segment(combined)
        if candidate:
            segments.append(candidate)
        else:
            for item in list_items:
                candidate = _clean_segment(item)
                if candidate:
                    segments.append(candidate)
        list_prefix = ""
        list_items = []

    def flush_code() -> None:
        if not code:
            return
        joined = "\n".join(code).strip()
        code.clear()
        if 8 <= len(joined) <= 420:
            segments.append(f"`{joined}`" if "\n" not in joined else joined)

    for raw_line in clean.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            flush_paragraph()
            flush_list()
            if in_code:
                flush_code()
            in_code = not in_code
            continue
        if in_code:
            code.append(line)
            continue
        stripped = line.strip()
        if not stripped:
            if list_items:
                flush_list()
            else:
                flush_paragraph(preserve_list_prefix=True)
            continue
        if stripped.startswith("#") or stripped.startswith("http"):
            continue
        if stripped in {"---", "Etiquetas:", "Fuentes:", "Fuentes primarias:"}:
            continue
        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        numbered = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        if bullet or numbered:
            pending = take_paragraph()
            if pending:
                if pending.endswith(":"):
                    list_prefix = pending[:-1].strip()
                else:
                    append_prose(pending)
                    list_prefix = ""
            list_items.append((bullet or numbered).group(1).strip())
            continue
        if list_items:
            flush_list()
        elif list_prefix:
            append_prose(list_prefix + ".")
            list_prefix = ""
        paragraph.append(stripped)

    flush_paragraph()
    flush_list()
    flush_code()
    return segments


def _clean_segment(value: str) -> str:
    clean = _LABEL_PREFIX.sub("", value.strip())
    clean = re.sub(r"\s+", " ", clean).strip(" -")
    if len(clean) < 18 or len(clean) > 420:
        return ""
    folded = clean.casefold()
    if folded.startswith(("fuente", "referencia", "documentación")):
        return ""
    if folded.endswith((" documentation.", " documentación.")):
        return ""
    return clean


def _contextualize(segment: str, heading: str) -> str:
    clean_heading = re.sub(r"^\d+(?:\.\d+)*\.\s*", "", heading).strip()
    generic_prefixes = (
        "Herramientas:",
        "Capas distintas:",
        "Interfaces útiles en:",
        "Usar:",
        "RIESGOS:",
    )
    if clean_heading and segment.startswith(generic_prefixes):
        return f"{clean_heading} — {segment}"
    return segment


def _score_segment(
    segment: str,
    heading: str,
    terms: list[str],
    anchors: set[str],
) -> int:
    normalized = _normalize(segment)
    heading_normalized = _normalize(heading)
    matched = {term for term in terms if term in normalized}
    matched_anchors = {term for term in anchors if term in normalized}
    heading_matches = {term for term in terms if term in heading_normalized}
    heading_anchors = {term for term in anchors if term in heading_normalized}
    if anchors and not matched_anchors and not heading_anchors:
        return 0
    score = len(matched) * 2
    score += len(matched_anchors) * 7
    score += len(heading_matches) * 2
    score += len(heading_anchors) * 5
    if any(marker in normalized for marker in ("no crear", "no conviene", "evitar", "no usar")):
        score += 3
    if segment.startswith("`") or "\n" in segment:
        score -= 1
    return score


def _weak_heading(value: str) -> bool:
    normalized = _normalize(value)
    return any(
        marker in normalized
        for marker in ("fuentes", "referencias", "skills futuras", "estado de revision")
    )


def _task_title(task: AlexandriaTask) -> str:
    normalized = _normalize(task.text)
    if "group_concat" in normalized:
        return "GROUP_CONCAT e inyección SQL"
    if "transaccion" in normalized:
        return "Cuándo usar una transacción"
    if "stock" in normalized:
        return "Concurrencia de stock"
    if "interfaz" in normalized or "interface" in normalized:
        return "Interfaces de software"
    if "phpstan" in normalized or "phpunit" in normalized or "php -l" in normalized:
        return "php -l, PHPStan y PHPUnit"
    if "webhook" in normalized:
        return "Procesamiento de un webhook de pago"
    if "opcache" in normalized:
        return "OPcache"
    title = task.text.strip().rstrip("?").strip()
    return title[:100] or f"Tarea {task.index}"


def _terms(value: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    normalized_value = _normalize(value)
    expansions: list[str] = []
    if re.search(r"(?<!\w)php\s*-\s*l(?!\w)", normalized_value):
        expansions.extend(("lint", "sintaxis"))
    if "webhook" in normalized_value:
        expansions.extend(
            ("firma", "timestamp", "replay", "idempotencia", "monto", "moneda", "proveedor")
        )
    if "opcache" in normalized_value:
        expansions.extend(("bytecode", "compilado", "consultas", "algoritmos"))
    if "phpstan" in normalized_value:
        expansions.extend(("analisis", "estatico", "tipos", "nullabilidad", "contratos"))
    if "phpunit" in normalized_value:
        expansions.extend(("pruebas", "ejecuta", "comportamiento", "unitarias"))
    if "stock" in normalized_value:
        expansions.extend(("update", "atomico", "bloqueo", "filas", "afectadas"))
    for expansion in expansions:
        if expansion not in seen:
            seen.add(expansion)
            terms.append(expansion)
    for raw in _WORDS.findall(normalized_value):
        term = raw.strip("._-")
        if len(term) < 3 or term in _STOPWORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        for synonym in _SYNONYMS.get(term, ()):
            if synonym not in seen:
                seen.add(synonym)
                terms.append(synonym)
    return terms[:18]


def _required_concepts_for_task(normalized: str) -> tuple[str, ...]:
    concepts = list(_concepts_for_text(normalized))

    def add(value: str) -> None:
        if value not in concepts:
            concepts.append(value)

    if "webhook" in normalized:
        for value in (
            "signature",
            "replay",
            "idempotency",
            "payment-values",
            "provider-check",
        ):
            add(value)
    if "opcache" in normalized:
        add("bytecode")
        add("performance-limits")
    return tuple(concepts)


def _concepts_for_text(normalized: str) -> tuple[str, ...]:
    concepts: list[str] = []

    def add(value: str) -> None:
        if value not in concepts:
            concepts.append(value)

    if "group_concat" in normalized:
        add("group_concat")
    if "transaccion" in normalized or "transaction" in normalized:
        add("transaction")
    if "stock" in normalized:
        add("stock")
    if "interfaz" in normalized or "interface" in normalized:
        add("interface")
    if re.search(r"(?<!\w)php\s*-\s*l(?!\w)", normalized) or (
        "lint" in normalized and "sintaxis" in normalized
    ):
        add("php-l")
    if "phpstan" in normalized:
        add("phpstan")
    if "phpunit" in normalized:
        add("phpunit")
    if "webhook" in normalized:
        add("webhook")
    if "firma" in normalized or "signature" in normalized:
        add("signature")
    if "replay" in normalized or "timestamp" in normalized:
        add("replay")
    if "idempot" in normalized:
        add("idempotency")
    if "monto" in normalized and "moneda" in normalized:
        add("payment-values")
    if "proveedor" in normalized:
        add("provider-check")
    if "opcache" in normalized:
        add("opcache")
    if "bytecode" in normalized or "compilado" in normalized:
        add("bytecode")
    if any(
        marker in normalized
        for marker in ("algoritmos", "consultas sql", "operaciones de i/o", "i/o")
    ):
        add("performance-limits")
    return tuple(concepts)


def _looks_technical(value: str) -> bool:
    return "_" in value or "." in value or (value.startswith("php") and len(value) > 3)


def _strip_front_matter(content: str) -> str:
    if not content.startswith("---\n"):
        return content
    end = content.find("\n---\n", 4)
    return content[end + 5 :] if end >= 0 else content


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))
