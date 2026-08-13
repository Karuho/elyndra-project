from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from elyndra.language_packs.repository import SpanishLexicalService

from elyndra.db import Database
from elyndra.engines.base import LanguageEngine

_ALLOWED_INTENTS = {
    "wellbeing.current",
    "wellbeing.period_summary",
    "organizer.today",
    "organizer.tomorrow",
    "organizer.upcoming",
    "routine.status",
    "coaching.progress",
    "goal.status",
    "automation.status",
    "automation.last_result",
    "notification.status",
    "scheduler.status",
    "knowledge.lookup",
    "knowledge.explain",
    "memory.recall",
}

_PERSONAL_CUES = {
    "agenda",
    "animo",
    "automatizacion",
    "bienestar",
    "check",
    "checkin",
    "compromiso",
    "cumpleanos",
    "descanso",
    "dormi",
    "energia",
    "estres",
    "hoy",
    "manana",
    "notificacion",
    "objetivo",
    "plan",
    "recordatorio",
    "rutina",
    "scheduler",
    "semana",
    "sueno",
}

_STOPWORDS = {
    "a",
    "al",
    "algo",
    "como",
    "con",
    "cual",
    "de",
    "del",
    "el",
    "en",
    "esta",
    "este",
    "ha",
    "he",
    "la",
    "las",
    "lo",
    "los",
    "me",
    "mi",
    "mis",
    "para",
    "por",
    "que",
    "se",
    "su",
    "tengo",
    "un",
    "una",
    "y",
}

_CONCEPTS: dict[str, str] = {
    "afecto": "mood",
    "animo": "mood",
    "emocional": "mood",
    "humor": "mood",
    "sentimiento": "mood",
    "sentir": "mood",
    "siento": "mood",
    "estado": "status",
    "bienestar": "wellbeing",
    "check": "checkin",
    "checkin": "checkin",
    "chequeo": "checkin",
    "registro": "checkin",
    "energia": "energy",
    "estres": "stress",
    "concentracion": "focus",
    "foco": "focus",
    "dormi": "sleep",
    "dormir": "sleep",
    "descanso": "sleep",
    "sueno": "sleep",
    "hidratacion": "hydration",
    "alimentacion": "nutrition",
    "nutricion": "nutrition",
    "actividad": "activity",
    "ejercicio": "activity",
    "agenda": "organizer",
    "compromiso": "organizer",
    "cita": "organizer",
    "evento": "organizer",
    "cumpleanos": "birthday",
    "rutina": "routine",
    "habito": "routine",
    "objetivo": "goal",
    "meta": "goal",
    "coaching": "coaching",
    "plan": "plan",
    "automatizacion": "automation",
    "automatico": "automation",
    "scheduler": "scheduler",
    "programador": "scheduler",
    "notificacion": "notification",
    "aviso": "notification",
    "recordatorio": "notification",
    "aprendiste": "knowledge",
    "sabes": "knowledge",
    "explica": "explain",
    "recuerda": "memory",
    "recordar": "memory",
    "ando": "wellbeing",
    "voy": "progress",
    "toca": "organizer",
}

_INTENT_EXAMPLES: dict[str, tuple[str, ...]] = {
    "wellbeing.current": (
        "como esta mi animo hoy",
        "como ando de animo",
        "como me siento hoy",
        "que dice mi ultimo check de bienestar",
        "como esta el check de bienestar",
        "revisa mi estado de hoy",
        "que tal dormi",
    ),
    "wellbeing.period_summary": (
        "como he estado esta semana",
        "como va mi bienestar",
        "como me he sentido ultimamente",
        "resumen de bienestar del mes",
        "como vengo esta semana",
    ),
    "organizer.today": (
        "que tengo hoy",
        "como viene mi dia",
        "tengo algo para hoy",
        "que toca hoy",
    ),
    "organizer.tomorrow": (
        "que tengo manana",
        "tengo algo manana",
        "que toca manana",
        "hay algun compromiso para manana",
        "como viene el dia de manana",
    ),
    "organizer.upcoming": (
        "que tengo proximamente",
        "cuales son mis proximos compromisos",
        "que cumpleanos vienen",
        "proximos eventos",
    ),
    "routine.status": (
        "como van mis rutinas",
        "que rutinas tengo pendientes",
        "cumpli mi rutina",
        "estado de mis habitos",
    ),
    "coaching.progress": (
        "como voy con el descanso",
        "como va mi plan de coaching",
        "que acciones tengo pendientes del plan",
        "progreso de mi plan personal",
    ),
    "goal.status": (
        "como van mis objetivos",
        "estado de mis metas",
        "que objetivo tengo activo",
        "como voy con mis metas",
    ),
    "automation.status": (
        "que automatizaciones tengo",
        "como van mis automatizaciones",
        "automatizaciones activas",
    ),
    "automation.last_result": (
        "se ejecuto la agenda",
        "que preparo elyndra",
        "ultimo resultado de automatizacion",
    ),
    "notification.status": (
        "tengo notificaciones pendientes",
        "que avisos tengo",
        "hay recordatorios sin leer",
    ),
    "scheduler.status": (
        "esta activo el scheduler local",
        "como esta el programador local",
        "scheduler funcionando",
    ),
    "knowledge.lookup": (
        "que sabes de fotosintesis",
        "que aprendiste de este tema",
        "busca en tu conocimiento",
    ),
    "knowledge.explain": (
        "explica la fotosintesis",
        "ensename lo que aprendiste",
        "explica usando tu conocimiento",
    ),
    "memory.recall": (
        "que recuerdas de mi",
        "recuerda lo que hablamos",
        "que sabes de mis preferencias",
    ),
}

_INTENT_DOMAINS: dict[str, str] = {
    "wellbeing.current": "bienestar",
    "wellbeing.period_summary": "bienestar",
    "organizer.today": "organizacion_personal",
    "organizer.tomorrow": "organizacion_personal",
    "organizer.upcoming": "organizacion_personal",
    "routine.status": "organizacion_personal",
    "coaching.progress": "bienestar",
    "goal.status": "objetivos",
    "automation.status": "automatizacion",
    "automation.last_result": "automatizacion",
    "notification.status": "automatizacion",
    "scheduler.status": "automatizacion",
    "knowledge.lookup": "conocimiento",
    "knowledge.explain": "conocimiento",
    "memory.recall": "memoria",
}

_AMBIGUOUS_PHRASES = {
    "como voy": ("wellbeing.current", "goal.status", "coaching.progress"),
    "como estoy": ("wellbeing.current", "organizer.today"),
    "que tengo": ("organizer.today", "goal.status"),
    "mi estado": ("wellbeing.current", "goal.status", "automation.status"),
}


@dataclass(frozen=True, slots=True)
class IntentResolution:
    intent: str | None
    status: str
    confidence: float
    entities: dict[str, Any]
    alternatives: tuple[str, ...]
    source: str
    tutor_used: bool
    clarification: str
    resolution_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SemanticIntentRepository:
    """Reviewed semantic resolution without raw prompt or autonomous actions."""

    def __init__(
        self, database: Database, lexical_service: SpanishLexicalService | None = None
    ) -> None:
        self.database = database
        self.lexical_service = lexical_service

    def status(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            examples = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_intent_examples WHERE status = 'active'"
                ).fetchone()[0]
            )
            resolutions = int(
                connection.execute("SELECT COUNT(*) FROM assistant_intent_resolutions").fetchone()[
                    0
                ]
            )
            proposals = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_intent_learning_proposals "
                    "WHERE status = 'pending'"
                ).fetchone()[0]
            )
            tutor_fallbacks = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_semantic_fallback_events WHERE tutor_used = 1"
                ).fetchone()[0]
            )
        return {
            "ontology_intents": len(_ALLOWED_INTENTS),
            "reviewed_examples": examples,
            "resolutions": resolutions,
            "pending_learning_proposals": proposals,
            "tutor_fallbacks": tutor_fallbacks,
            "raw_prompt_stored": False,
            "silent_learning": False,
            "automatic_actions": False,
        }

    def resolve(
        self,
        text: str,
        *,
        tutor_engine: LanguageEngine | None,
        response_language: str = "es",
    ) -> IntentResolution | None:
        normalized = normalize_text(_semantic_surface(text))
        normalized = self._lexical_normalize(normalized)
        if not normalized or not self._should_consider(normalized):
            return None
        if normalized in _AMBIGUOUS_PHRASES:
            alternatives = _AMBIGUOUS_PHRASES[normalized]
            result = IntentResolution(
                intent=None,
                status="clarification",
                confidence=0.45,
                entities=extract_entities(normalized),
                alternatives=alternatives,
                source="deterministic_ambiguity",
                tutor_used=False,
                clarification=_clarification(alternatives),
            )
            return self._record(text, result)

        ranked = self._rank(normalized)
        top_intent, top_score = ranked[0] if ranked else (None, 0.0)
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        entities = extract_entities(normalized)
        clear_personal_shortcut = (
            top_intent == "wellbeing.current"
            and top_score >= 0.8
            and normalized.startswith("como ando")
        )
        if top_intent is not None and (
            clear_personal_shortcut or (top_score >= 0.72 and top_score - second_score >= 0.08)
        ):
            result = IntentResolution(
                intent=top_intent,
                status="resolved",
                confidence=round(top_score, 4),
                entities=_intent_entities(top_intent, entities),
                alternatives=tuple(item[0] for item in ranked[1:3]),
                source="semantic_local",
                tutor_used=False,
                clarification="",
            )
            return self._record(text, result)

        tutor_result = self._tutor_resolution(
            text,
            tutor_engine=tutor_engine,
            response_language=response_language,
            deterministic_candidates=ranked[:3],
        )
        if tutor_result is not None:
            return self._record(text, tutor_result)

        alternatives = tuple(item[0] for item in ranked[:3] if item[1] >= 0.28)
        if alternatives:
            result = IntentResolution(
                intent=None,
                status="clarification",
                confidence=round(top_score, 4),
                entities=entities,
                alternatives=alternatives,
                source="semantic_local",
                tutor_used=False,
                clarification=_clarification(alternatives),
            )
            return self._record(text, result)
        self.record_fallback(
            text,
            candidate_intent=None,
            confidence=0.0,
            tutor_used=False,
            outcome="unresolved",
        )
        return None

    def _lexical_normalize(self, normalized: str) -> str:
        if self.lexical_service is None:
            return normalized
        tokens = normalized.split()
        if len(tokens) > 12:
            return normalized
        expanded: list[str] = []
        additions = 0
        for token in tokens:
            expanded.append(token)
            lexical = self.lexical_service.semantic_expansions(token, limit=2)
            if lexical["ambiguous"] or additions >= 8:
                continue
            for candidate in lexical["terms"][:2]:
                if candidate not in expanded:
                    expanded.append(candidate)
                    additions += 1
        return " ".join(expanded[:24])

    def list_resolutions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assistant_intent_resolutions ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [_resolution_row(row) for row in rows]

    def list_proposals(self, *, status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
        clean = status.strip().casefold()
        if clean not in {"all", "pending", "approved", "rejected"}:
            raise ValueError("Estado de propuesta semántica inválido.")
        where = "" if clean == "all" else "WHERE status = ?"
        params: tuple[Any, ...] = (
            (max(1, min(limit, 500)),) if clean == "all" else (clean, max(1, min(limit, 500)))
        )
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assistant_intent_learning_proposals "
                f"{where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [_proposal_row(row) for row in rows]

    def propose_learning(
        self,
        *,
        phrase: str,
        intent: str,
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        clean_phrase = normalize_text(phrase)
        if not clean_phrase or len(clean_phrase) > 300:
            raise ValueError("La frase revisable debe tener entre 1 y 300 caracteres.")
        clean_intent = _validate_intent(intent)
        now = _now()
        public_id = uuid.uuid4().hex
        phrase_hash = _hash(clean_phrase)
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM assistant_intent_learning_proposals "
                "WHERE phrase_sha256 = ? AND intent = ? AND status = 'pending'",
                (phrase_hash, clean_intent),
            ).fetchone()
            if existing is not None:
                return _proposal_row(existing)
            connection.execute(
                """
                INSERT INTO assistant_intent_learning_proposals(
                    public_id, normalized_phrase, phrase_sha256, intent,
                    source, status, proposed_by, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    public_id,
                    clean_phrase,
                    phrase_hash,
                    clean_intent,
                    source.strip()[:80] or "owner_correction",
                    actor.strip()[:120] or "owner",
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM assistant_intent_learning_proposals WHERE public_id = ?",
                (public_id,),
            ).fetchone()
        return _proposal_row(row)

    def review_learning(
        self,
        public_id: str,
        *,
        decision: str,
        actor: str,
    ) -> dict[str, Any]:
        clean_decision = decision.strip().casefold()
        if clean_decision not in {"approve", "reject"}:
            raise ValueError("La decisión debe ser approve o reject.")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_intent_learning_proposals "
                "WHERE public_id = ? AND status = 'pending'",
                (public_id.strip(),),
            ).fetchone()
            if row is None:
                raise ValueError("Propuesta semántica pendiente no encontrada.")
            status = "approved" if clean_decision == "approve" else "rejected"
            now = _now()
            connection.execute(
                "UPDATE assistant_intent_learning_proposals "
                "SET status = ?, reviewed_by = ?, reviewed_at = ?, updated_at = ? "
                "WHERE id = ?",
                (status, actor.strip()[:120] or "owner", now, now, int(row["id"])),
            )
            if status == "approved":
                connection.execute(
                    """
                    INSERT OR IGNORE INTO assistant_intent_examples(
                        public_id, intent, normalized_phrase, phrase_sha256,
                        source, status, approved_by, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        str(row["intent"]),
                        str(row["normalized_phrase"]),
                        str(row["phrase_sha256"]),
                        "reviewed_owner_correction",
                        actor.strip()[:120] or "owner",
                        now,
                        now,
                    ),
                )
            reviewed = connection.execute(
                "SELECT * FROM assistant_intent_learning_proposals WHERE id = ?",
                (int(row["id"]),),
            ).fetchone()
        return _proposal_row(reviewed)

    def record_fallback(
        self,
        text: str,
        *,
        candidate_intent: str | None,
        confidence: float,
        tutor_used: bool,
        outcome: str,
    ) -> None:
        normalized = normalize_text(text)
        now = _now()
        phrase_hash = _hash(normalized)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_semantic_fallback_events(
                    public_id, message_sha256, candidate_intent, confidence,
                    tutor_used, outcome, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    phrase_hash,
                    candidate_intent,
                    round(max(0.0, min(float(confidence), 1.0)), 4),
                    int(tutor_used),
                    outcome[:40],
                    now,
                ),
            )
            if tutor_used and candidate_intent in _ALLOWED_INTENTS:
                count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM assistant_semantic_fallback_events "
                        "WHERE message_sha256 = ? AND candidate_intent = ? "
                        "AND tutor_used = 1",
                        (phrase_hash, candidate_intent),
                    ).fetchone()[0]
                )
                existing = connection.execute(
                    "SELECT 1 FROM assistant_intent_examples "
                    "WHERE phrase_sha256 = ? AND intent = ? AND status = 'active' "
                    "UNION ALL SELECT 1 FROM assistant_intent_learning_proposals "
                    "WHERE phrase_sha256 = ? AND intent = ? AND status = 'pending' "
                    "LIMIT 1",
                    (phrase_hash, candidate_intent, phrase_hash, candidate_intent),
                ).fetchone()
                if count >= 3 and existing is None:
                    connection.execute(
                        """
                        INSERT INTO assistant_intent_learning_proposals(
                            public_id, normalized_phrase, phrase_sha256, intent,
                            source, status, proposed_by, created_at, updated_at
                        ) VALUES(?, ?, ?, ?, 'repeated_tutor_resolution',
                            'pending', 'elyndra', ?, ?)
                        """,
                        (
                            uuid.uuid4().hex,
                            normalized[:300],
                            phrase_hash,
                            candidate_intent,
                            now,
                            now,
                        ),
                    )

    def _rank(self, normalized: str) -> list[tuple[str, float]]:
        query_tokens = semantic_tokens(normalized)
        examples = {key: list(value) for key, value in _INTENT_EXAMPLES.items()}
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT intent, normalized_phrase FROM assistant_intent_examples "
                "WHERE status = 'active'"
            ).fetchall()
        for row in rows:
            intent = str(row["intent"])
            if intent in examples:
                examples[intent].append(str(row["normalized_phrase"]))
        ranked: list[tuple[str, float]] = []
        for intent, phrases in examples.items():
            score = max(
                (_semantic_similarity(query_tokens, semantic_tokens(item)) for item in phrases),
                default=0.0,
            )
            score = max(score, _intent_rule_score(intent, query_tokens, normalized))
            ranked.append((intent, round(min(score, 1.0), 4)))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked

    def _should_consider(self, normalized: str) -> bool:
        if normalized.startswith(("recuerda que ", "recordar que ")):
            return False
        if normalized in _AMBIGUOUS_PHRASES:
            return True
        words = set(normalized.split())
        strong = {
            "agenda",
            "automatizacion",
            "automatizaciones",
            "bienestar",
            "checkin",
            "chequeo",
            "coaching",
            "compromiso",
            "compromisos",
            "cumpleanos",
            "notificacion",
            "notificaciones",
            "recordatorio",
            "recordatorios",
            "rutina",
            "rutinas",
            "scheduler",
        }
        if words & strong:
            return True
        personal = {
            "ando",
            "dormi",
            "estoy",
            "he",
            "me",
            "mi",
            "mis",
            "siento",
            "tengo",
            "voy",
        }
        wellbeing = {
            "alimentacion",
            "animo",
            "concentracion",
            "descanso",
            "emocional",
            "energia",
            "estres",
            "hidratacion",
            "humor",
            "pulso",
            "registro",
            "sueno",
            "termometro",
            "vital",
        }
        if words & personal and words & wellbeing:
            return True
        if words & {"objetivo", "objetivos", "meta", "metas", "plan"} and words & personal:
            return True
        return any(
            phrase in normalized
            for phrase in (
                "como ando",
                "como he estado",
                "como me siento",
                "como voy",
                "que toca",
                "que tengo hoy",
                "que tengo manana",
                "tengo algo hoy",
                "tengo algo manana",
                "ultimo resultado de automatizacion",
                "que aprendiste",
                "que sabes de",
                "busca en tu conocimiento",
                "explica usando tu conocimiento",
                "que recuerdas de mi",
            )
        )

    def _tutor_resolution(
        self,
        text: str,
        *,
        tutor_engine: LanguageEngine | None,
        response_language: str,
        deterministic_candidates: list[tuple[str, float]],
    ) -> IntentResolution | None:
        if tutor_engine is None:
            return None
        allowed = ", ".join(sorted(_ALLOWED_INTENTS))
        candidates = ", ".join(
            f"{intent}:{score:.2f}" for intent, score in deterministic_candidates
        )
        prompt = (
            "Clasifica la intención del mensaje del propietario. Devuelve SOLO JSON "
            "con claves intent, confidence, entities, alternatives y clarification. "
            f"Intentos permitidos: {allowed}. Usa intent=null si es ambiguo o ajeno. "
            "entities solo puede incluir period, days, metric y query. confidence debe "
            "estar entre 0 y 1. No respondas la consulta ni inventes datos personales.\n"
            f"Candidatos locales: {candidates or 'ninguno'}\n"
            f"Mensaje: {text[:500]}"
        )
        try:
            reply = tutor_engine.reply(
                prompt,
                context=(
                    "[RESOLUCIÓN SEMÁNTICA ACOTADA]\n"
                    "No tienes herramientas, SQLite, memoria, permisos ni autoridad. "
                    "Solo propones una interpretación lingüística en JSON.",
                ),
                history=(),
                response_language=response_language,
                keep_alive_seconds=0,
                max_tokens=220,
            )
            payload = _strict_json(reply.text)
        except (RuntimeError, ValueError, json.JSONDecodeError):
            self.record_fallback(
                text,
                candidate_intent=None,
                confidence=0.0,
                tutor_used=True,
                outcome="tutor_failed",
            )
            return None
        raw_intent = payload.get("intent")
        intent = None if raw_intent is None else str(raw_intent).strip()
        if intent and intent not in _ALLOWED_INTENTS:
            intent = None
        confidence = _confidence(payload.get("confidence"))
        entities = _safe_entities(payload.get("entities"))
        alternatives = tuple(
            item
            for item in payload.get("alternatives", [])
            if isinstance(item, str) and item in _ALLOWED_INTENTS
        )[:3]
        if intent and confidence >= 0.72:
            result = IntentResolution(
                intent=intent,
                status="resolved",
                confidence=confidence,
                entities=_intent_entities(intent, {**extract_entities(text), **entities}),
                alternatives=alternatives,
                source="tutor_assisted",
                tutor_used=True,
                clarification="",
            )
            self.record_fallback(
                text,
                candidate_intent=intent,
                confidence=confidence,
                tutor_used=True,
                outcome="resolved",
            )
            return result
        options = alternatives or ((intent,) if intent else ())
        self.record_fallback(
            text,
            candidate_intent=intent,
            confidence=confidence,
            tutor_used=True,
            outcome="clarification",
        )
        if options:
            return IntentResolution(
                intent=None,
                status="clarification",
                confidence=confidence,
                entities=entities,
                alternatives=tuple(options),
                source="tutor_assisted",
                tutor_used=True,
                clarification=str(payload.get("clarification") or _clarification(options))[:500],
            )
        return None

    def _record(self, text: str, result: IntentResolution) -> IntentResolution:
        public_id = uuid.uuid4().hex
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_intent_resolutions(
                    public_id, message_sha256, intent, status, confidence,
                    entities_json, alternatives_json, source, tutor_used,
                    clarification, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    public_id,
                    _hash(normalize_text(text)),
                    result.intent,
                    result.status,
                    result.confidence,
                    json.dumps(result.entities, ensure_ascii=False, sort_keys=True),
                    json.dumps(result.alternatives, ensure_ascii=False),
                    result.source,
                    int(result.tutor_used),
                    result.clarification[:500],
                    _now(),
                ),
            )
        return IntentResolution(**{**result.to_dict(), "resolution_id": public_id})


def _semantic_surface(text: str) -> str:
    without_urls = re.sub(r"https?://\S+", " ", text, flags=re.IGNORECASE)
    without_windows = re.sub(r"[A-Za-z]:[\\/][^\s,;]+", " ", without_urls)
    return re.sub(r"(?<!\w)/(?:[^\s,;]+)", " ", without_windows)


def normalize_text(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    ascii_text = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


def semantic_tokens(text: str) -> set[str]:
    normalized = normalize_text(text)
    tokens = {token for token in normalized.split() if len(token) > 1 and token not in _STOPWORDS}
    return tokens | {_CONCEPTS[token] for token in tokens if token in _CONCEPTS}


def extract_entities(text: str) -> dict[str, Any]:
    normalized = normalize_text(text)
    tokens = semantic_tokens(normalized)
    entities: dict[str, Any] = {}
    today = date.today()
    if "manana" in normalized:
        entities.update({"period": "tomorrow", "date": (today + timedelta(days=1)).isoformat()})
    elif "ayer" in normalized:
        entities.update({"period": "yesterday", "date": (today - timedelta(days=1)).isoformat()})
    elif "hoy" in normalized or "actual" in tokens or "ultimo" in tokens:
        entities.update({"period": "today", "date": today.isoformat()})
    elif "semana pasada" in normalized:
        entities.update({"period": "previous_week", "days": 7})
    elif "semana" in tokens or "ultimamente" in normalized:
        entities.update({"period": "week", "days": 7})
    elif "mes" in tokens:
        entities.update({"period": "month", "days": 30})
    metrics = (
        "mood",
        "energy",
        "stress",
        "focus",
        "sleep",
        "hydration",
        "nutrition",
        "activity",
    )
    for metric in metrics:
        if metric in tokens:
            entities["metric"] = metric
            break
    return entities


def _intent_entities(intent: str, entities: dict[str, Any]) -> dict[str, Any]:
    result = dict(entities)
    if intent == "organizer.today":
        result.update({"period": "today", "offset_days": 0})
    elif intent == "organizer.tomorrow":
        result.update({"period": "tomorrow", "offset_days": 1})
    elif intent == "organizer.upcoming":
        result.setdefault("days", 60)
    elif intent == "wellbeing.current":
        result.setdefault("period", "today")
        result.setdefault("days", 1)
    elif intent == "wellbeing.period_summary":
        result.setdefault("days", 7)
    return result


def _semantic_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    if not overlap:
        return 0.0
    coverage = overlap / min(len(left), len(right))
    jaccard = overlap / len(left | right)
    return 0.65 * coverage + 0.35 * jaccard


def _intent_rule_score(intent: str, tokens: set[str], normalized: str) -> float:
    score = 0.0
    if intent.startswith("wellbeing") and tokens & {
        "wellbeing",
        "mood",
        "checkin",
        "energy",
        "stress",
        "focus",
        "sleep",
        "hydration",
        "nutrition",
        "activity",
    }:
        score = 0.62
        if intent == "wellbeing.current" and tokens & {"hoy", "ultimo", "actual"}:
            score += 0.2
        if intent == "wellbeing.period_summary" and tokens & {
            "semana",
            "mes",
            "ultimamente",
        }:
            score += 0.22
    elif (
        intent == "organizer.tomorrow"
        and "manana" in tokens
        and tokens
        & {
            "organizer",
            "toca",
        }
        or intent == "organizer.today"
        and "hoy" in tokens
        and tokens
        & {
            "organizer",
            "toca",
        }
    ):
        score = 0.9
    elif intent == "organizer.upcoming" and tokens & {
        "organizer",
        "birthday",
        "proximo",
        "proximos",
    }:
        score = 0.72
    elif intent == "routine.status" and "routine" in tokens:
        score = 0.82
    elif intent == "coaching.progress" and tokens & {"coaching", "plan", "progress"}:
        score = 0.72
    elif intent == "goal.status" and "goal" in tokens:
        score = 0.82
    elif intent.startswith("automation") and "automation" in tokens:
        score = 0.82
        if intent == "automation.last_result" and tokens & {"ultimo", "resultado", "ejecuto"}:
            score = 0.92
    elif intent == "scheduler.status" and "scheduler" in tokens:
        score = 0.95
    elif intent == "notification.status" and "notification" in tokens:
        score = 0.86
    elif intent.startswith("knowledge") and "knowledge" in tokens:
        score = 0.72
        if intent == "knowledge.lookup" and tokens & {"sabes", "aprendiste", "busca"}:
            score = 0.9
        if intent == "knowledge.explain" and "explain" in tokens:
            score = 0.9
    elif intent == "memory.recall" and "memory" in tokens:
        score = 0.82
    if normalized.startswith("como voy") and intent in {
        "wellbeing.current",
        "goal.status",
        "coaching.progress",
    }:
        return min(score, 0.52)
    return score


def _clarification(intents: tuple[str, ...] | list[str]) -> str:
    labels = {
        "wellbeing.current": "tu bienestar actual",
        "wellbeing.period_summary": "tu bienestar de un período",
        "organizer.today": "tu agenda de hoy",
        "organizer.tomorrow": "tu agenda de mañana",
        "organizer.upcoming": "tus próximos compromisos",
        "routine.status": "tus rutinas",
        "coaching.progress": "tu plan de coaching",
        "goal.status": "tus objetivos",
        "automation.status": "tus automatizaciones",
        "automation.last_result": "el último resultado automatizado",
        "notification.status": "tus notificaciones",
        "scheduler.status": "el scheduler local",
        "knowledge.lookup": "el conocimiento de Elyndra",
        "knowledge.explain": "una explicación desde el conocimiento de Elyndra",
        "memory.recall": "la memoria personal",
    }
    choices = [labels.get(item, item) for item in intents[:3]]
    if not choices:
        return "No pude determinar qué información quieres revisar."
    if len(choices) == 1:
        return f"¿Quieres revisar {choices[0]}?"
    return "¿Quieres revisar " + ", ".join(choices[:-1]) + " o " + choices[-1] + "?"


def _strict_json(text: str) -> dict[str, Any]:
    cleaned = text.strip().strip("`\n ")
    if cleaned.casefold().startswith("json"):
        cleaned = cleaned[4:].lstrip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("La interpretación del tutor no es un objeto JSON.")
    return payload


def _safe_entities(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("period", "metric", "query"):
        item = value.get(key)
        if isinstance(item, str) and len(item) <= 160:
            result[key] = item
    days = value.get("days")
    if isinstance(days, int) and 1 <= days <= 90:
        result["days"] = days
    return result


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(number, 1.0)), 4)


def _validate_intent(intent: str) -> str:
    clean = intent.strip()
    if clean not in _ALLOWED_INTENTS:
        raise ValueError("Intención semántica no permitida.")
    return clean


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _resolution_row(row: Any) -> dict[str, Any]:
    return {
        "public_id": str(row["public_id"]),
        "message_sha256": str(row["message_sha256"]),
        "intent": row["intent"],
        "status": str(row["status"]),
        "confidence": float(row["confidence"]),
        "entities": json.loads(str(row["entities_json"])),
        "alternatives": json.loads(str(row["alternatives_json"])),
        "source": str(row["source"]),
        "tutor_used": bool(row["tutor_used"]),
        "clarification": str(row["clarification"]),
        "created_at": str(row["created_at"]),
        "raw_prompt_stored": False,
    }


def _proposal_row(row: Any) -> dict[str, Any]:
    return {
        "public_id": str(row["public_id"]),
        "normalized_phrase": str(row["normalized_phrase"]),
        "phrase_sha256": str(row["phrase_sha256"]),
        "intent": str(row["intent"]),
        "source": str(row["source"]),
        "status": str(row["status"]),
        "proposed_by": str(row["proposed_by"]),
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "automatic_activation": False,
    }


def available_intents() -> tuple[str, ...]:
    return tuple(sorted(_ALLOWED_INTENTS))
