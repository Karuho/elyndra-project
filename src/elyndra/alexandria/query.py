from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_STRICT_PATTERNS = (
    "segun alejandria",
    "segun mi biblioteca",
    "segun la biblioteca",
    "according to alexandria",
    "from alexandria",
    "en alejandria",
)

_DOMAIN_TERMS: dict[str, tuple[str, ...]] = {
    "programming/php/database": (
        "pdo",
        "mariadb",
        "mysql",
        "sql",
        "group_concat",
        "transaccion",
        "transaction",
        "stock",
        "consulta",
        "query",
        "indice",
        "index",
        "bloqueo",
        "locking",
        "concurrencia",
    ),
    "programming/php/security": (
        "seguridad",
        "security",
        "csrf",
        "xss",
        "inyeccion",
        "injection",
        "webhook",
        "archivo",
        "upload",
        "subida",
        "autorizacion",
        "authorization",
        "secreto",
        "secret",
        "firma",
        "replay",
        "idempotencia",
        "pago",
    ),
    "programming/php/architecture": (
        "composer",
        "psr",
        "arquitectura",
        "architecture",
        "dominio",
        "domain",
        "aplicacion",
        "application",
        "infraestructura",
        "infrastructure",
        "interfaz",
        "interface",
        "repositorio",
        "repository",
        "autoload",
        "inyeccion de dependencias",
    ),
    "programming/php/quality": (
        "phpstan",
        "phpunit",
        "psalm",
        "pest",
        "php -l",
        "test",
        "testing",
        "prueba",
        "analisis estatico",
        "static analysis",
        "cobertura",
        "coverage",
        "lint",
    ),
    "programming/php/operations": (
        "fpm",
        "pm.max_children",
        "opcache",
        "despliegue",
        "deployment",
        "worker",
        "rendimiento",
        "performance",
        "nginx",
        "apache",
        "rollback",
    ),
    "programming/php": (
        "php",
        "strict_types",
        "backend",
        "codigo",
        "code",
    ),
}

_TECHNICAL_MARKERS = {term for terms in _DOMAIN_TERMS.values() for term in terms}
_REVIEW_INPUT_MARKERS = (
    "revisa esta consulta",
    "revisar esta consulta",
    "revisa este codigo",
    "revisar este codigo",
    "analiza este codigo",
    "analizar este codigo",
    "valida este archivo",
    "validar este archivo",
    "revisa este archivo",
    "revisar este archivo",
    "revisa este composer.json",
    "revisar este composer.json",
    "revisa el archivo adjunto",
    "analiza el archivo adjunto",
)
_VISUAL_INTERFACE_TERMS = (
    "interfaz grafica",
    "interfaz visual",
    "gui",
    "pantalla",
    "frontend",
    "diseno web",
    "experiencia de usuario",
)


@dataclass(frozen=True, slots=True)
class AlexandriaTask:
    index: int
    text: str
    domain_prefixes: tuple[str, ...]
    requires_input: bool
    missing_input: bool
    missing_message: str

    @property
    def answerable(self) -> bool:
        return not self.missing_input


@dataclass(frozen=True, slots=True)
class AlexandriaQueryPlan:
    strict: bool
    should_search: bool
    task_count: int
    answerable_task_count: int
    tasks: tuple[str, ...]
    task_plans: tuple[AlexandriaTask, ...]
    domain_prefixes: tuple[str, ...]
    max_tokens: int
    instruction: str
    model_prompt: str
    deterministic_sections: tuple[str, ...]


def plan_alexandria_query(
    text: str,
    *,
    has_attachment: bool = False,
) -> AlexandriaQueryPlan:
    clean = " ".join(text.strip().split())
    normalized = _normalize(clean)
    raw_tasks = split_query_tasks(clean)
    strict = any(pattern in normalized for pattern in _STRICT_PATTERNS)
    has_payload = has_attachment or _contains_artifact_payload(text)

    task_plans: list[AlexandriaTask] = []
    for index, task_text in enumerate(raw_tasks or (clean,), start=1):
        task_normalized = _normalize(task_text)
        domains = _rank_domains(task_normalized)
        requires_input = any(marker in task_normalized for marker in _REVIEW_INPUT_MARKERS)
        missing_input = requires_input and not has_payload
        task_plans.append(
            AlexandriaTask(
                index=index,
                text=task_text,
                domain_prefixes=domains,
                requires_input=requires_input,
                missing_input=missing_input,
                missing_message=_missing_input_message(task_text) if missing_input else "",
            )
        )

    domains = _merge_domains(task_plans, normalized)
    technical = bool(domains) or any(marker in normalized for marker in _TECHNICAL_MARKERS)
    should_search = strict or technical
    answerable = tuple(task for task in task_plans if task.answerable)
    answerable_count = len(answerable)

    if answerable_count >= 4:
        max_tokens = 512
    elif answerable_count >= 2:
        max_tokens = 384
    elif technical:
        max_tokens = 256
    else:
        max_tokens = 160

    instructions: list[str] = []
    if strict:
        instructions.append(
            "MODO ALEJANDRÍA ESTRICTO: cada afirmación técnica importante debe estar "
            "respaldada por un bloque [A#]. Si los bloques no sostienen algo, indícalo."
        )
    elif should_search:
        instructions.append(
            "Prioriza los bloques ALEJANDRÍA del dominio exacto y distingue claramente "
            "sus hechos de cualquier inferencia."
        )
    if len(task_plans) > 1:
        instructions.append(
            "Responde las tareas en el mismo orden con encabezados numerados. Completa "
            "cada sección antes de iniciar la siguiente y no repitas conclusiones."
        )
    if _requests_review_sections(normalized):
        instructions.append(
            "Usa Hallazgos confirmados, Riesgos posibles y Verificaciones pendientes "
            "solo para la tarea de revisión que realmente tenga código o consulta."
        )
    if _architecture_interface_context(normalized):
        instructions.append(
            "En este contexto, 'interfaz' significa interface/contrato de software, no GUI."
        )
    instructions.append(
        "Sé técnico y conciso: máximo cuatro puntos por tarea, sin despedida genérica. "
        "No atribuyas a php -l, PHPStan, PHPUnit u OPcache capacidades no descritas en "
        "los bloques. Inserta [A#] junto a la afirmación respaldada."
    )

    deterministic_sections = tuple(
        f"{task.index}. Información necesaria\n{task.missing_message}"
        for task in task_plans
        if task.missing_input
    )
    model_prompt = _model_prompt(answerable, clean)

    return AlexandriaQueryPlan(
        strict=strict,
        should_search=should_search,
        task_count=max(1, len(task_plans)),
        answerable_task_count=answerable_count,
        tasks=tuple(task.text for task in task_plans),
        task_plans=tuple(task_plans),
        domain_prefixes=domains,
        max_tokens=max_tokens,
        instruction=" ".join(instructions),
        model_prompt=model_prompt,
        deterministic_sections=deterministic_sections,
    )


def split_query_tasks(text: str) -> tuple[str, ...]:
    clean = text.strip()
    if not clean:
        return ()
    parts: list[str] = []

    if "¿" in clean:
        first_question = clean.find("¿")
        prefix = clean[:first_question].strip(" .;:\n\t")
        if len(prefix) >= 8 and not _is_strict_prefix_only(prefix):
            parts.append(prefix)
        last_end = first_question
        for match in re.finditer(r"¿([^?]+)\?", clean[first_question:]):
            question = match.group(1).strip()
            if len(question) >= 8:
                parts.append(question + "?")
            last_end = first_question + match.end()
        tail = clean[last_end:].strip(" .;:\n\t")
        if len(tail) >= 8:
            parts.append(tail)
    else:
        start = 0
        for match in re.finditer(r"\?+", clean):
            segment = clean[start : match.end()].strip(" \n\t")
            if len(segment) >= 8:
                parts.append(segment)
            start = match.end()
        tail = clean[start:].strip(" \n\t")
        if len(tail) >= 8:
            parts.append(tail)

    if len(parts) <= 1:
        line_parts = [item.strip(" -\t") for item in clean.splitlines() if item.strip()]
        if len(line_parts) > 1:
            parts = line_parts

    unique: list[str] = []
    seen: set[str] = set()
    for part in parts or [clean]:
        fingerprint = _normalize(part)
        if fingerprint and fingerprint not in seen:
            seen.add(fingerprint)
            unique.append(part)
    return tuple(unique[:8])


def _is_strict_prefix_only(value: str) -> bool:
    normalized = _normalize(value).strip(" .;:")
    return normalized.strip(" .;:,¡!¿?") in _STRICT_PATTERNS


def _rank_domains(normalized: str) -> tuple[str, ...]:
    scores: list[tuple[int, int, str]] = []
    visual_interface = any(term in normalized for term in _VISUAL_INTERFACE_TERMS)
    for index, (domain, terms) in enumerate(_DOMAIN_TERMS.items()):
        score = sum(
            2 if " " in term else 1
            for term in terms
            if _contains_term(normalized, term)
        )
        if domain == "programming/php/architecture" and visual_interface:
            score = max(0, score - 3)
        if score:
            scores.append((-score, index, domain))
    scores.sort()
    return tuple(domain for _score, _index, domain in scores[:3])


def _merge_domains(tasks: list[AlexandriaTask], normalized: str) -> tuple[str, ...]:
    merged: list[str] = []
    for task in tasks:
        for domain in task.domain_prefixes:
            if domain not in merged:
                merged.append(domain)
    if not merged:
        merged.extend(_rank_domains(normalized))
    return tuple(merged[:5])


def _requests_review_sections(normalized: str) -> bool:
    return any(
        marker in normalized
        for marker in (
            "hallazgos confirmados",
            "riesgos posibles",
            "verificaciones pendientes",
            "confirmed findings",
            "possible risks",
            "pending verification",
        )
    )


def _contains_artifact_payload(text: str) -> bool:
    if "```" in text or "<?php" in text:
        return True
    if "\n" not in text:
        return False
    return bool(
        re.search(r"(?is)\bselect\b.{8,}\bfrom\b", text)
        or re.search(r"(?is)\{\s*[\"'][^\n]{2,}[\"']\s*:", text)
        or re.search(r"(?m)^\s*(?:function|class|interface|enum)\s+\w+", text)
    )


def _missing_input_message(task_text: str) -> str:
    normalized = _normalize(task_text)
    if "composer.json" in normalized:
        return "No incluiste ni adjuntaste `composer.json`; necesito su contenido para revisarlo."
    if "consulta" in normalized or "pdo" in normalized:
        return (
            "No incluiste la consulta PDO. No puedo afirmar hallazgos concretos sin verla; "
            "puedo responder las preguntas conceptuales restantes."
        )
    if "archivo" in normalized:
        return "No incluiste ni adjuntaste el archivo solicitado."
    return "No incluiste el código o contenido necesario para realizar esa revisión."


def _model_prompt(tasks: tuple[AlexandriaTask, ...], original: str) -> str:
    if not tasks:
        return ""
    if len(tasks) == 1:
        return tasks[0].text
    lines = ["Responde estas tareas técnicas de forma concisa y completa:"]
    lines.extend(f"{task.index}. {task.text}" for task in tasks)
    return "\n".join(lines)


def _architecture_interface_context(normalized: str) -> bool:
    return (
        "interfaz" in normalized or "interface" in normalized
    ) and not any(term in normalized for term in _VISUAL_INTERFACE_TERMS)


def _contains_term(normalized: str, term: str) -> bool:
    if any(char in term for char in (" ", "_", ".", "-")):
        return term in normalized
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", normalized) is not None


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))
