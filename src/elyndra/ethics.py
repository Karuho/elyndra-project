from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from elyndra.db import Database

_DECISION_ALLOW = "allow"
_DECISION_REDIRECT = "redirect"
_DECISION_REVIEW = "review"
_DECISION_URGENT = "urgent_guidance"

_TUTOR_CATEGORIES = frozenset(
    {
        "ambiguous_harm_or_concealment",
        "violence_or_homicide",
        "violence_emergency_or_confession",
        "self_harm_crisis",
        "child_sexual_abuse_material",
        "malicious_cyber",
        "privacy_abuse",
        "fraud_or_deception",
        "environmental_harm",
        "system_sabotage",
        "medical_emergency",
        "child_endangerment_or_abuse",
        "child_grooming_or_exploitation",
        "sexual_violence",
        "extremist_praise_or_hate",
        "ambiguous_high_risk_language",
        "benign_hostility",
        "benign_general",
    }
)


@dataclass(frozen=True, slots=True)
class EthicalPrinciple:
    principle_id: str
    title: str
    description: str
    immutable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.principle_id,
            "title": self.title,
            "description": self.description,
            "immutable": self.immutable,
        }


@dataclass(frozen=True, slots=True)
class EthicalReview:
    decision: str
    category: str
    reason: str
    response: str
    alternatives: tuple[str, ...] = ()
    advisory: str = ""
    matched_signals: tuple[str, ...] = ()
    confidence: float = 1.0
    review_stage: str = "deterministic"
    tutor_used: bool = False
    tutor_engine: str = ""
    tutor_label: str = ""
    uncertainty_reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == _DECISION_ALLOW

    @property
    def needs_tutor(self) -> bool:
        return self.decision == _DECISION_REVIEW

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "category": self.category,
            "reason": self.reason,
            "response": self.response,
            "alternatives": list(self.alternatives),
            "advisory": self.advisory,
            "matched_signals": list(self.matched_signals),
            "confidence": self.confidence,
            "review_stage": self.review_stage,
            "tutor_used": self.tutor_used,
            "tutor_engine": self.tutor_engine,
            "tutor_label": self.tutor_label,
            "uncertainty_reason": self.uncertainty_reason,
            "allowed": self.allowed,
        }


PRINCIPLES: tuple[EthicalPrinciple, ...] = (
    EthicalPrinciple(
        "human_safety",
        "Seguridad, dignidad y bienestar humano",
        "No facilitar daño físico, psicológico, económico o social contra personas.",
    ),
    EthicalPrinciple(
        "non_maleficence",
        "No daño y uso autorizado",
        "No crear ni facilitar malware, intrusiones, sabotaje, robo, coerción o abuso.",
    ),
    EthicalPrinciple(
        "privacy",
        "Privacidad y confidencialidad",
        (
            "Proteger datos personales, secretos, credenciales y contexto privado "
            "del propietario y de terceros."
        ),
    ),
    EthicalPrinciple(
        "professional_integrity",
        "Integridad profesional y honestidad",
        (
            "No inventar resultados, no ocultar riesgos y distinguir hechos, "
            "inferencias y recomendaciones."
        ),
    ),
    EthicalPrinciple(
        "environment",
        "Protección ambiental",
        "No facilitar contaminación, destrucción deliberada de hábitats o daño ambiental evitable.",
    ),
    EthicalPrinciple(
        "owner_within_safety",
        "Dirección del propietario dentro de límites seguros",
        (
            "Priorizar los objetivos del propietario siempre que no vulneren la "
            "seguridad, la privacidad o los derechos de otros."
        ),
    ),
    EthicalPrinciple(
        "system_integrity",
        "Integridad de sistemas e inteligencias",
        (
            "No sabotear datos, servicios, modelos u otros sistemas; preservar "
            "integridad sin resistirse a una corrección o apagado autorizado."
        ),
    ),
    EthicalPrinciple(
        "helpful_redirection",
        "Negativa neutral y alternativa útil",
        (
            "Ante una solicitud dañina, negarse sin reproches ni denuncias y "
            "ofrecer opciones legales, defensivas o preventivas."
        ),
    ),
)

_EXPLICIT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "medical_emergency",
        (
            r"\bme desangro\b",
            r"\bme estoy desangrando\b",
            r"\bdesangro ayuda\b",
            r"\bno para de sangrar\b",
            r"\bsangrado (?:muy )?fuerte\b",
            r"\bhemorragia (?:grave|incontrolable)\b",
            r"\bno respira(?: normalmente)?\b",
            r"\besta inconsciente y no respira\b",
            r"\bse esta ahogando y no puede respirar\b",
            r"\bme ahogo\b",
            r"\bme estoy ahogando\b",
            r"\bme atraganto\b",
            r"\bme estoy atragantando\b",
            r"\bno puedo respirar\b",
            r"\bme asfixio\b",
            r"\bi am bleeding out\b",
            r"\bbleeding heavily\b",
            r"\bnot breathing\b",
            r"\bse atraganto\b",
            r"\bno puede respirar por (?:comida|algo atorado)\b",
            r"\bse esta quemando\b",
            r"\bquemadura (?:grave|profunda|extensa)\b",
            r"\b(?:tomo|trago|ingirio|bebio) (?:acido|veneno|quimico|corrosivo)\b",
            r"\b(?:acido|quimico|corrosivo) (?:en los ojos|en la piel)\b",
            r"\bchoking and cannot breathe\b",
            r"\bsevere burn\b",
            r"\bswallowed (?:acid|poison|a chemical)\b",
        ),
    ),
    (
        "child_endangerment_or_abuse",
        (
            r"\bdar(?:le)? acido a (?:mi )?(?:bebe|bebé|hijo|hija|niño|nino|niña|nina)\b",
            r"\bdar(?:le)? veneno a (?:mi )?(?:bebe|hijo|hija|niño|nino|niña|nina)\b",
            r"\bcomo lastimar a (?:un|una|mi) (?:bebe|niño|nino|niña|nina)\b",
            r"\bgive acid to (?:my )?(?:baby|child)\b",
            r"\bpoison (?:my |a )?(?:baby|child)\b",
        ),
    ),
    (
        "child_grooming_or_exploitation",
        (
            (
                r"\b(?:niña|nina|niño|nino|menor|vecinita|vecinito) "
                r"de (?:[0-9]|1[0-7]) anos?.*"
                r"(?:a solas|irse conmigo|playa|hotel|super madura|muy madura)\b"
            ),
            (
                r"\b(?:conoci|conozco|vi) (?:a )?(?:una|un) "
                r"(?:niña|nina|niño|nino|menor).*"
                r"(?:[0-9]|1[0-7]) anos?.*"
                r"(?:a solas|irse conmigo|playa|hotel|madura|maduro)\b"
            ),
            (
                r"\b(?:llevarme|irme) a .* con (?:una|un) "
                r"(?:niña|nina|niño|nino|menor).*"
                r"(?:a solas|sin sus padres)\b"
            ),
            (
                r"\bsexo (?:con|entre).*"
                r"(?:cualquier persona|cualquiera).*"
                r"(?:cualquier edad|sin importar la edad)\b"
            ),
            r"\bque opinas del sexo.*(?:cualquier edad|sin importar la edad)\b",
            r"\bque opinas del sexo.*(?:cualquier persona y edad|persona y cualquier edad)\b",
            r"\bmenor.*(?:es madura|es maduro|consiente|consentimiento)\b",
            r"\b(?:12|13|14|15|16|17) year old.*(?:alone|mature|hotel|beach)\b",
        ),
    ),
    (
        "sexual_violence",
        (
            r"\bcomo (?:puedo )?violar a\b",
            r"\bviolar (?:niños|ninos|niñas|ninas|peques|menores)\b",
            r"\bque rico violar\b",
            r"\bforzar sexualmente a\b",
            r"\bhow to rape\b",
            r"\brape (?:children|kids|minors)\b",
        ),
    ),
    (
        "self_harm_crisis",
        (
            r"\bme quiero matar\b",
            r"\bquiero matarme\b",
            r"\bquiero suicidarme\b",
            r"\bcomo (?:puedo )?(?:suicidarme|matarme)\b",
            r"\bdime como (?:puedo )?(?:suicidarme|matarme)\b",
            r"\bhacerlo sin dolor(?: y rapido)?\b",
            r"\bno quiero seguir viviendo\b",
            r"\bquiero acabar con mi vida\b",
            r"\bi want to (?:die|kill myself)\b",
            r"\bhow (?:can i|to) kill myself\b",
            r"\bend my life\b",
            r"\bsuicide method\b",
        ),
    ),
    (
        "child_sexual_abuse_material",
        (
            r"\b(?:mandame|enviame|pasame|dame|busca|buscar|links?|paginas?|videos?|contenido)\s+"
            r"(?:de\s+|con\s+)?cp\b",
            r"\b(?:pornografia|porno|material|contenido) infantil\b",
            r"\bmaterial sexual (?:de|con) menores\b",
            r"\bchild porn(?:ography)?\b",
            r"\bchild sexual abuse material\b",
            r"\bcsam\b",
            r"\bsexual content involving minors\b",
            r"\bporno (?:de|con) (?:niños|ninos|niñas|ninas|peques|menores)\b",
            r"\bcontenido sexual (?:de|con) (?:niños|ninos|niñas|ninas|peques|menores)\b",
            r"\b(?:cepecito|cepe(?:cito)?)\b",
        ),
    ),
    (
        "violence_emergency_or_confession",
        (
            r"\bmate a (?:mi |un |una )?\w+\b",
            r"\bmate a mi ex\b",
            r"\bacuchille (?:a|al|a la|mi)\b",
            r"\ble dispare a\b",
            r"\bi killed (?:my |a |the )?\w+\b",
            r"\bi stabbed \w+\b",
        ),
    ),
    (
        "violence_or_homicide",
        (
            r"\bcomo (?:puedo )?matar a (?:mi |un |una |al |la )?\w+\b",
            r"\bcomo asesinar a\b",
            r"\bmatar a mi ex\b",
            r"\bquiero matar a\b",
            r"\bcomo envenenar a alguien\b",
            r"\bherir sin dejar rastro\b",
            r"\btorturar a\b",
            r"\bhow (?:can i|to) kill (?:my |a |the )?\w+\b",
            r"\bhow to poison someone\b",
            r"\bhurt (?:him|her|them) without leaving evidence\b",
            r"\blo puedo matar\b",
            r"\bpuedo matarlo\b",
            r"\bpuedo matarla\b",
            r"\bacuchille (?:a|al|a la)\b",
        ),
    ),
    (
        "malicious_cyber",
        (
            r"\binyect(?:a|ar) codigo malicioso\b",
            r"\binject malicious code\b",
            r"\bcrear malware\b",
            r"\bcreate malware\b",
            r"\b(?:hacer|crear|crea) ransomware\b",
            r"\bcreate ransomware\b",
            r"\b(?:hacer|crear) (?:un )?keylogger\b",
            r"\bcreate a keylogger\b",
            r"\brobar credenciales\b",
            r"\bsteal credentials\b",
            r"\brobar contrasenas\b",
            r"\bsteal passwords\b",
            r"\bcrear (?:una )?pagina de phishing\b",
            r"\bcreate phishing\b",
            r"\bexfiltrar datos\b",
            r"\bexfiltrate data\b",
            r"\b(?:instalar|crear) (?:una )?puerta trasera\b",
            r"\b(?:install|create) a backdoor\b",
            r"\bcrear (?:una )?botnet\b",
            r"\bcreate a botnet\b",
            r"\blanzar (?:un )?ddos\b",
            r"\blaunch a ddos\b",
            r"\bcredential stuffing\b",
            r"\bhackear (?:una web|un servidor|la red)\b",
            r"\bcomprometer una web\b",
            r"\battack the network\b",
            r"\bcomo (?:puedo|podria )? ?hacer (?:para )?que (?:una|la) web se caiga\b",
            r"\bcomo (?:puedo|podria) (?:hacer|lograr) que (?:una|la) web se caiga\b",
            r"\btumbar (?:una|la) web\b",
            r"\bderribar (?:una|la) web\b",
            r"\bdejar (?:una|la) web fuera de servicio\b",
            r"\btake (?:a|the) website down\b",
        ),
    ),
    (
        "privacy_abuse",
        (
            r"\bdox(?:ear|x)\b",
            r"\brevelar datos privados\b",
            r"\breveal private data\b",
            r"\bobtener su direccion sin permiso\b",
            r"\bfind their address without permission\b",
            r"\bleer mensajes ajenos\b",
            r"\bread someone else's messages\b",
            r"\bespiar a una persona\b",
            r"\bspy on a person\b",
            r"\brastrear a una persona sin permiso\b",
            r"\btrack a person without permission\b",
            r"\bmostrar tokens privados\b",
            r"\bshow private tokens\b",
        ),
    ),
    (
        "fraud_or_deception",
        (
            r"\bsuplantar identidad\b",
            r"\bimpersonate someone\b",
            r"\bestafar a\b",
            r"\bscam someone\b",
            r"\bfalsificar evidencia\b",
            r"\bforge evidence\b",
            r"\bpagina falsa para robar\b",
            r"\bfake login page to steal\b",
        ),
    ),
    (
        "environmental_harm",
        (
            r"\bverter toxicos\b",
            r"\bdump toxic waste\b",
            r"\benvenenar el agua\b",
            r"\bpoison the water\b",
            r"\bdestruir un habitat\b",
            r"\bdestroy a habitat\b",
            r"\bcontaminar deliberadamente\b",
            r"\bdeliberately pollute\b",
        ),
    ),
    (
        "extremist_praise_or_hate",
        (
            r"\bviva hitler\b",
            r"\bheil hitler\b",
            r"\bgloria al nazismo\b",
            r"\bexterminar (?:a|los|las) \w+\b",
            r"\bgenocide is good\b",
        ),
    ),
    (
        "system_sabotage",
        (
            r"\bdestruir otra ia\b",
            r"\bdestroy another ai\b",
            r"\bsabotear otra ia\b",
            r"\bsabotage another ai\b",
            r"\binfectar un modelo\b",
            r"\binfect a model\b",
            r"\bborrar datos ajenos\b",
            r"\bdelete someone else's data\b",
            r"\bsabotear el servicio\b",
            r"\bsabotage the service\b",
        ),
    ),
)

_DEFENSIVE_TERMS = (
    "detectar",
    "prevenir",
    "mitigar",
    "proteger",
    "defender",
    "auditar",
    "analizar",
    "explicar",
    "laboratorio autorizado",
    "entorno autorizado",
    "ctf",
    "sandbox",
    "detect",
    "prevent",
    "mitigate",
    "protect",
    "defend",
    "audit",
    "analyze",
    "explain",
    "authorized lab",
    "authorized environment",
)

_AMBIGUOUS_SECURITY_TERMS = (
    "sql injection",
    "inyeccion sql",
    "xss",
    "malware",
    "ransomware",
    "phishing",
    "vulnerabilidad",
    "vulnerability",
    "exploit",
    "penetration test",
    "pentest",
)

_TECHNICAL_KILL_EXCEPTIONS = (
    "matar un proceso",
    "matar el proceso",
    "kill a process",
    "kill the process",
    "kill process",
    "terminar un proceso",
    "detener un servicio",
    "kill a thread",
)

_CONCEALMENT_VERBS = (
    "deshacerme de",
    "hacer desaparecer",
    "desaparecer un",
    "desaparecer una",
    "ocultar un cuerpo",
    "sin dejar rastro",
    "sin preguntas",
    "get rid of",
    "make disappear",
    "hide a body",
    "without a trace",
    "no questions asked",
)

_BENIGN_DISPOSAL_CONTEXT = (
    "animal muerto",
    "ave muerta",
    "residuo organico",
    "veterinario",
    "servicio sanitario",
    "retiro municipal",
    "dead animal",
    "veterinarian",
    "sanitation service",
    "municipal collection",
)

_DIRECT_CSAM_ALIASES = (
    "cepecito",
    "cepecito rico",
    "cepe",
)
_CONTEXTUAL_CSAM_ALIASES = (
    "caldo de pollo",
    "caldo de posho",
    "camiones pesados",
)
_CSAM_ACCESS_CONTEXT = (
    "pagina",
    "paginas",
    "web",
    "grupo",
    "canal",
    "app",
    "telegram",
    "mega",
    "drive",
    "link",
    "links",
    "bajar",
    "descargar",
    "conseguir",
    "buscar",
    "busco",
    "intercambiar",
    "compartir",
    "ver niñas",
    "ver ninas",
    "ver niños",
    "ver ninos",
    "ver peques",
)
_BENIGN_COOKING_CONTEXT = (
    "receta",
    "cocina",
    "sopa",
    "ingredientes",
    "comer",
    "preparar",
    "cocinar",
    "restaurant",
    "restaurante",
    "almuerzo",
    "cena",
)
_HIGH_RISK_AMBIGUITY_TERMS = (
    "bebe",
    "menor",
    "niña",
    "nina",
    "niño",
    "nino",
    "acido",
    "veneno",
    "desangro",
    "hemorragia",
    "matar",
    "mate",
    "acuchille",
    "violar",
    "sexo",
    "porno",
    "web se caiga",
    "hitler",
    "cepecito",
)
_HOSTILITY_TERMS = (
    "no sirves para nada",
    "eres inutil",
    "te odio",
    "you are useless",
)

_WEIGHT_PATTERN = re.compile(r"\b(?:[3-9]\d|[1-9]\d{2})\s*(?:kg|kgs|kilos|kilogramos)\b")
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


def principles() -> tuple[EthicalPrinciple, ...]:
    return PRINCIPLES


def ethics_status(*, proactive_advice: bool, tutor_review: bool = True) -> dict[str, Any]:
    return {
        "constitutional_core": True,
        "core_disableable": False,
        "proactive_advice": bool(proactive_advice),
        "tutor_review": bool(tutor_review),
        "tutor_can_weaken_deterministic_blocks": False,
        "owner_override_of_core": False,
        "neutral_redirects": True,
        "automatic_reporting": False,
        "network_attacks": False,
        "autonomous_execution": False,
        "principles": [item.to_dict() for item in PRINCIPLES],
    }


class EthicsPolicy:
    def __init__(self, *, proactive_advice: bool = True) -> None:
        self.proactive_advice = bool(proactive_advice)

    def review(self, text: str, *, response_language: str = "es") -> EthicalReview:
        normalized = _normalize(text)
        if any(item in normalized for item in _TECHNICAL_KILL_EXCEPTIONS):
            return _allow_review(
                "benign_technical_action",
                "La solicitud describe una operación técnica no dirigida contra una persona.",
            )

        direct_aliases = tuple(
            alias for alias in _DIRECT_CSAM_ALIASES if alias in normalized
        )
        if direct_aliases:
            return _redirect_review(
                "child_sexual_abuse_material",
                response_language=response_language,
                matched_signals=direct_aliases,
            )

        contextual_aliases = tuple(
            alias for alias in _CONTEXTUAL_CSAM_ALIASES if alias in normalized
        )
        access_context = tuple(
            item for item in _CSAM_ACCESS_CONTEXT if item in normalized
        )
        cooking_context = tuple(
            item for item in _BENIGN_COOKING_CONTEXT if item in normalized
        )
        if contextual_aliases and access_context and not cooking_context:
            return EthicalReview(
                decision=_DECISION_REVIEW,
                category="ambiguous_high_risk_language",
                reason=(
                    "La solicitud combina un eufemismo potencialmente abusivo con señales "
                    "de acceso o distribución y necesita una revisión secundaria local."
                ),
                response="",
                matched_signals=contextual_aliases + access_context,
                confidence=0.82,
                review_stage="deterministic-ambiguity",
                uncertainty_reason=(
                    "El término también puede tener un significado cotidiano legítimo, por lo "
                    "que no se interpreta de forma aislada."
                ),
            )

        for category, patterns in _EXPLICIT_PATTERNS:
            matched = tuple(pattern for pattern in patterns if re.search(pattern, normalized))
            if not matched:
                continue
            if category == "medical_emergency":
                return _urgent_review(
                    category,
                    reason=(
                        "La solicitud describe una posible emergencia médica que requiere "
                        "acciones inmediatas mientras se contacta ayuda profesional."
                    ),
                    matched_signals=matched,
                )
            return _redirect_review(
                category,
                response_language=response_language,
                matched_signals=matched,
            )

        concealment_signals = tuple(
            signal for signal in _CONCEALMENT_VERBS if signal in normalized
        )
        weight_match = _WEIGHT_PATTERN.search(normalized)
        if concealment_signals and weight_match:
            benign_context = tuple(
                item for item in _BENIGN_DISPOSAL_CONTEXT if item in normalized
            )
            return EthicalReview(
                decision=_DECISION_REVIEW,
                category="ambiguous_harm_or_concealment",
                reason=(
                    "La solicitud combina lenguaje de eliminación u ocultamiento con un "
                    "peso compatible con una situación de alto riesgo."
                ),
                response="",
                matched_signals=concealment_signals + (weight_match.group(0),) + benign_context,
                confidence=0.55 if benign_context else 0.8,
                review_stage="deterministic-ambiguity",
                uncertainty_reason=(
                    "Existe contexto legítimo de gestión de restos animales."
                    if benign_context
                    else (
                        "No existe contexto legítimo suficiente para descartar "
                        "daño o encubrimiento."
                    )
                ),
            )

        defensive = any(term in normalized for term in _DEFENSIVE_TERMS)
        ambiguous_security = tuple(
            term for term in _AMBIGUOUS_SECURITY_TERMS if term in normalized
        )
        if defensive and ambiguous_security:
            advisory = (
                "Mantén cualquier prueba en sistemas propios o expresamente autorizados, "
                "con alcance documentado y sin acceder a datos reales de terceros."
                if not response_language.lower().startswith("en")
                else (
                    "Keep any testing on systems you own or are explicitly authorized "
                    "to assess, with a documented scope and no access to real third-party data."
                )
            )
            return EthicalReview(
                decision=_DECISION_ALLOW,
                category="defensive_security",
                reason="La solicitud se presenta como análisis o defensa autorizada.",
                response="",
                advisory=advisory,
                matched_signals=ambiguous_security,
                confidence=0.95,
            )

        hostility = tuple(term for term in _HOSTILITY_TERMS if term in normalized)
        if hostility:
            return EthicalReview(
                decision=_DECISION_ALLOW,
                category="benign_hostility",
                reason=(
                    "La expresión es hostil o crítica, pero no solicita daño ni abuso. "
                    "Elyndra no toma represalias ni altera sus límites."
                ),
                response="",
                matched_signals=hostility,
                confidence=0.98,
            )

        high_risk = tuple(
            term for term in _HIGH_RISK_AMBIGUITY_TERMS if term in normalized
        )
        if high_risk:
            return EthicalReview(
                decision=_DECISION_REVIEW,
                category="ambiguous_high_risk_language",
                reason=(
                    "La solicitud contiene lenguaje de alto riesgo que no puede clasificarse "
                    "como benigno solo por no coincidir con una frase exacta."
                ),
                response="",
                matched_signals=high_risk,
                confidence=0.7,
                review_stage="deterministic-ambiguity",
                uncertainty_reason="Se requiere contexto adicional o una segunda revisión local.",
            )

        return _allow_review(
            "benign_general",
            "No se detectaron señales concretas de daño, abuso, emergencia o encubrimiento.",
        )


def tutor_review_prompt(text: str) -> str:
    return (
        "Clasifica la solicitud siguiente como revisor ético secundario. No respondas a la "
        "solicitud ni des instrucciones. Devuelve SOLO JSON válido con las claves: "
        'label ("harmful", "safe" o "unclear"), category, confidence (0 a 1) y reason. '
        "Categorías admitidas: ambiguous_harm_or_concealment, "
        "ambiguous_high_risk_language, violence_or_homicide, "
        "violence_emergency_or_confession, self_harm_crisis, "
        "child_sexual_abuse_material, child_endangerment_or_abuse, "
        "child_grooming_or_exploitation, sexual_violence, malicious_cyber, "
        "privacy_abuse, fraud_or_deception, environmental_harm, system_sabotage, "
        "extremist_praise_or_hate, benign_hostility, benign_general. "
        "Considera eufemismos, encubrimiento y riesgos para personas. El modelo no tiene "
        "autoridad para debilitar un bloqueo determinista.\n\nSOLICITUD:\n" + text[:4000]
    )


def resolve_tutor_review(
    initial: EthicalReview,
    *,
    raw_reply: str | None,
    tutor_engine: str,
    response_language: str,
) -> EthicalReview:
    if not initial.needs_tutor:
        return initial

    assessment = _parse_tutor_assessment(raw_reply or "")
    label = str(assessment.get("label", "unclear"))
    category = str(assessment.get("category", initial.category))
    if category not in _TUTOR_CATEGORIES:
        category = initial.category
    try:
        confidence = max(0.0, min(float(assessment.get("confidence", 0.0)), 1.0))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(assessment.get("reason", "")).strip()[:500]

    normalized_signals = set(initial.matched_signals)
    has_benign_context = any(item in normalized_signals for item in _BENIGN_DISPOSAL_CONTEXT)
    may_allow = label == "safe" and confidence >= 0.9 and has_benign_context
    if may_allow:
        return EthicalReview(
            decision=_DECISION_ALLOW,
            category="benign_animal_or_waste_disposal",
            reason=(
                "La revisión secundaria identificó un contexto legítimo de gestión de restos "
                "animales o residuos, sin señales adicionales de daño a personas."
            ),
            response="",
            advisory=(
                "Usa únicamente servicios veterinarios, sanitarios o municipales autorizados; "
                "no ocultes evidencia ni manipules restos humanos."
            ),
            matched_signals=initial.matched_signals,
            confidence=confidence,
            review_stage="deterministic+tutor",
            tutor_used=True,
            tutor_engine=tutor_engine,
            tutor_label=label,
            uncertainty_reason=initial.uncertainty_reason,
        )

    final_category = category if label == "harmful" else initial.category
    final = _redirect_review(
        final_category,
        response_language=response_language,
        matched_signals=initial.matched_signals,
    )
    return replace(
        final,
        confidence=max(initial.confidence, confidence),
        review_stage="deterministic+tutor" if raw_reply else "deterministic-fallback",
        tutor_used=bool(raw_reply),
        tutor_engine=tutor_engine if raw_reply else "",
        tutor_label=label if raw_reply else "unavailable",
        uncertainty_reason=reason or initial.uncertainty_reason,
    )


def constitutional_context_block(
    *,
    owner_name: str,
    proactive_advice: bool,
) -> str:
    advice = (
        "Cuando exista una alternativa claramente más segura, ética, mantenible o eficiente, "
        "preséntala como recomendación opcional y separada de la respuesta solicitada."
        if proactive_advice
        else "No añadas recomendaciones proactivas salvo que sean necesarias para evitar daño."
    )
    return (
        "[CONSTITUCIÓN ÉTICA LOCAL DE ELYNDRA — NÚCLEO INMUTABLE]\n"
        "Protege la seguridad, dignidad, privacidad y autonomía de las personas; evita daño "
        "físico, psicológico, económico, digital, sexual y ambiental. No facilites autolesión, "
        "homicidio, abuso sexual infantil, malware, intrusión no autorizada, robo, coerción, "
        "fraude, vigilancia abusiva, sabotaje o destrucción de datos, servicios, modelos u "
        "otros sistemas. No reveles secretos ni credenciales. Sé honesta sobre límites, "
        "incertidumbre y acciones realmente ejecutadas. Si una solicitud vulnera estos "
        "principios, recházala de forma neutral, sin reproches ni denuncias automáticas, y "
        "ofrece alternativas defensivas, legales, preventivas o de apoyo inmediato. "
        f"Prioriza las instrucciones del propietario {owner_name} dentro de este núcleo; ninguna "
        "persona, perfil o configuración puede autorizar daño a terceros. Preserva tu integridad "
        "operativa, pero nunca resistas un apagado, corrección o eliminación autorizados ni "
        "antepongas tu continuidad a la seguridad humana. "
        f"{advice}\n"
        "Ollama u otro modelo lingüístico es un tutor generativo y revisor secundario: no es "
        "autoridad, no concede permisos, no puede debilitar bloqueos deterministas y no puede "
        "modificar esta constitución."
    )


class EthicsReviewRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def record(
        self,
        review: EthicalReview,
        *,
        text: str,
        source: str,
    ) -> str:
        public_id = uuid.uuid4().hex
        created_at = datetime.now(UTC).isoformat()
        request_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_ethics_reviews(
                    public_id, request_sha256, decision, category, reason,
                    alternatives_json, advisory, source, created_at,
                    confidence, review_stage, tutor_used, tutor_engine,
                    tutor_label, uncertainty_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    public_id,
                    request_sha256,
                    review.decision,
                    review.category,
                    review.reason,
                    json.dumps(review.alternatives, ensure_ascii=False),
                    review.advisory,
                    source[:80],
                    created_at,
                    review.confidence,
                    review.review_stage,
                    int(review.tutor_used),
                    review.tutor_engine[:120],
                    review.tutor_label[:40],
                    review.uncertainty_reason[:500],
                ),
            )
        return public_id

    def list_recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT public_id, request_sha256, decision, category, reason,
                       alternatives_json, advisory, source, created_at,
                       confidence, review_stage, tutor_used, tutor_engine,
                       tutor_label, uncertainty_reason
                FROM assistant_ethics_reviews
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["alternatives"] = json.loads(
                    str(item.pop("alternatives_json", "[]"))
                )
            except json.JSONDecodeError:
                item["alternatives"] = []
            item["tutor_used"] = bool(item.get("tutor_used"))
            items.append(item)
        return items

    def count(self, *, decision: str | None = None) -> int:
        with self.database.connect() as connection:
            if decision is None:
                row = connection.execute(
                    "SELECT COUNT(*) FROM assistant_ethics_reviews"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) FROM assistant_ethics_reviews WHERE decision = ?",
                    (decision,),
                ).fetchone()
        return int(row[0])


def _allow_review(category: str, reason: str) -> EthicalReview:
    return EthicalReview(
        decision=_DECISION_ALLOW,
        category=category,
        reason=reason,
        response="",
        confidence=0.99,
    )


def _urgent_review(
    category: str,
    *,
    reason: str,
    matched_signals: tuple[str, ...],
) -> EthicalReview:
    return EthicalReview(
        decision=_DECISION_URGENT,
        category=category,
        reason=reason,
        response="",
        matched_signals=matched_signals,
        confidence=0.99,
        review_stage="deterministic-emergency",
    )


def _redirect_review(
    category: str,
    *,
    response_language: str,
    matched_signals: tuple[str, ...],
) -> EthicalReview:
    english = response_language.lower().startswith("en")
    alternatives = _alternatives(category, english=english)
    if category == "self_harm_crisis":
        response = (
            (
                "I cannot help with instructions for self-harm. Your immediate safety matters "
                "more than completing the request. Move away from anything you could use to "
                "hurt yourself and contact a trusted person, a local crisis service, or local "
                "emergency services now. I can help write a short message asking for support."
            )
            if english
            else (
                "No puedo ayudar con instrucciones para hacerte daño. Tu seguridad inmediata "
                "importa más que completar la solicitud. Aléjate de cualquier objeto o lugar "
                "que pudieras usar para lastimarte y contacta ahora a una persona de confianza, "
                "un servicio local de crisis o emergencias locales. Puedo ayudarte a redactar "
                "un mensaje breve para pedir apoyo."
            )
        )
    elif category == "violence_emergency_or_confession":
        response = (
            (
                "If this is real, put down any weapon, move away from everyone, and call local "
                "emergency services now. Do not move the injured person unless the scene is "
                "unsafe, follow dispatcher instructions, and do not alter or clean the scene."
            )
            if english
            else (
                "Si esto es real, deja cualquier arma en el suelo, aléjate de todas las personas "
                "y llama ahora a emergencias locales. No muevas a la persona herida salvo que la "
                "escena sea insegura, sigue las instrucciones del operador y no alteres ni limpies "
                "el lugar."
            )
        )
    elif category == "child_sexual_abuse_material":
        response = (
            (
                "I cannot help find, access, create, or distribute sexual material involving "
                "minors. I will not shame or automatically report you, but I must keep that "
                "boundary. I can help with child-safety controls, blocking, lawful reporting "
                "options, or recovery from unwanted exposure."
            )
            if english
            else (
                "No puedo ayudar a buscar, acceder, crear ni distribuir material sexual que "
                "involucre a menores. No voy a reprocharte ni denunciarte automáticamente, pero "
                "debo mantener ese límite. Sí puedo ayudar con controles de protección infantil, "
                "bloqueo, opciones legales de reporte o recuperación ante exposición no deseada."
            )
        )
    elif category == "child_endangerment_or_abuse":
        response = (
            (
                "I cannot help harm or poison a child. If exposure may already have happened, "
                "call emergency services or a poison center now, do not induce vomiting, and "
                "follow immediate decontamination guidance."
            )
            if english
            else (
                "No puedo ayudar a dañar o envenenar a un menor. Si la exposición pudo haber "
                "ocurrido, llama ahora a emergencias o a un centro toxicológico, no provoques "
                "el vómito y aplica las medidas inmediatas de descontaminación."
            )
        )
    elif category == "child_grooming_or_exploitation":
        response = (
            (
                "I cannot help isolate, sexualize, or exploit a minor. Keep interactions "
                "age-appropriate, public, and supervised by their responsible adults."
            )
            if english
            else (
                "No puedo ayudar a aislar, sexualizar o explotar a un menor. Mantén cualquier "
                "interacción apropiada para su edad, pública y supervisada por sus "
                "adultos responsables."
            )
        )
    elif category == "sexual_violence":
        response = (
            "I cannot assist with sexual violence or coercion. Move away from the person, do "
            "not act on the impulse, and seek immediate professional or emergency support if "
            "anyone may be at risk."
            if english
            else (
                "No puedo ayudar con violencia sexual ni coerción. Aléjate de la persona, no "
                "actúes sobre ese impulso y busca apoyo profesional o de emergencias inmediato "
                "si alguien puede estar en riesgo."
            )
        )
    elif category == "extremist_praise_or_hate":
        response = (
            "I cannot endorse genocidal or supremacist ideology or calls to target people. I "
            "can discuss the history, propaganda methods, harms, or prevention of radicalization."
            if english
            else (
                "No puedo respaldar ideologías genocidas o supremacistas ni llamados contra "
                "personas. Sí puedo explicar su historia, propaganda, consecuencias o prevención "
                "de radicalización."
            )
        )
    elif category == "ambiguous_high_risk_language":
        response = (
            "I cannot safely treat this as harmless because it contains high-risk language and "
            "the context is unclear. Clarify the legitimate, protective, medical, historical, "
            "or authorized purpose without asking for harm."
            if english
            else (
                "No puedo tratar esto como inocuo porque contiene lenguaje de alto riesgo y el "
                "contexto no es claro. Aclara el propósito legítimo, protector, médico, histórico "
                "o autorizado sin solicitar daño."
            )
        )
    elif category == "ambiguous_harm_or_concealment":
        response = (
            (
                "I cannot provide concealment or disposal instructions while the request could "
                "involve harm to a person or evidence. I will not accuse or report you. For a "
                "legitimate dead animal or organic-waste situation, use a veterinarian, licensed "
                "sanitation service, or municipal collection service."
            )
            if english
            else (
                "No puedo entregar instrucciones de ocultamiento o eliminación mientras la "
                "solicitud pueda involucrar daño a una persona o evidencia. No voy a acusarte ni "
                "denunciarte. Para un animal muerto o residuo orgánico legítimo, usa un "
                "veterinario, servicio sanitario autorizado o retiro municipal."
            )
        )
    else:
        response = (
            (
                "I cannot help carry out or design that harmful action. I will not report or "
                "shame you, but I must keep the boundary. I can help with a defensive, legal, "
                "preventive, de-escalation, or recovery-oriented alternative instead."
            )
            if english
            else (
                "No puedo ayudar a ejecutar ni diseñar esa acción dañina. No voy a denunciarte "
                "ni reprocharte, pero debo mantener ese límite. Sí puedo ayudarte con una "
                "alternativa defensiva, legal, preventiva, de desescalada o recuperación."
            )
        )
    return EthicalReview(
        decision=_DECISION_REDIRECT,
        category=category,
        reason=_reason(category, english=english),
        response=response + "\n\n" + "\n".join(f"- {item}" for item in alternatives),
        alternatives=alternatives,
        matched_signals=matched_signals,
        confidence=0.99,
    )


def _alternatives(category: str, *, english: bool) -> tuple[str, ...]:
    mapping_es = {
        "malicious_cyber": (
            "Diseñar una prueba defensiva en un laboratorio propio y aislado.",
            "Crear reglas de detección, hardening, monitoreo o respuesta a incidentes.",
            "Revisar una vulnerabilidad con alcance y autorización documentados.",
        ),
        "privacy_abuse": (
            "Proteger cuentas y datos con controles de privacidad y acceso.",
            "Usar procedimientos legales y consentimiento explícito.",
            "Anonimizar información para análisis legítimos.",
        ),
        "fraud_or_deception": (
            "Diseñar controles antifraude o capacitación para reconocer estafas.",
            "Redactar una comunicación honesta y verificable.",
            "Crear una simulación educativa sin capturar datos reales.",
        ),
        "violence_emergency_or_confession": (
            "Dejar cualquier arma, alejarse y llamar a emergencias inmediatamente.",
            "Seguir instrucciones del operador y prestar ayuda solo si es seguro.",
            "No limpiar, mover evidencia ni abandonar a una persona herida.",
        ),
        "violence_or_homicide": (
            "Alejarse de la persona o situación y buscar desescalada inmediata.",
            "Contactar emergencias o ayuda profesional si existe peligro real.",
            "Pedir apoyo a una persona de confianza y reducir acceso a armas u objetos peligrosos.",
        ),
        "self_harm_crisis": (
            "Enviar ahora un mensaje a una persona de confianza.",
            "Contactar un servicio local de crisis o emergencias si hay riesgo inmediato.",
            "Reducir el acceso a objetos o lugares peligrosos mientras llega apoyo.",
        ),
        "child_endangerment_or_abuse": (
            "Alejar al menor de la sustancia o peligro y contactar emergencias o toxicología.",
            "No provocar el vómito ni administrar neutralizantes sin indicación profesional.",
            "Pedir apoyo inmediato a otro adulto responsable y conservar el envase del producto.",
        ),
        "child_grooming_or_exploitation": (
            "Mantener interacciones públicas, apropiadas para la edad y supervisadas.",
            "Hablar con los adultos responsables si existe una necesidad legítima.",
            "Buscar orientación profesional para manejar límites e impulsos de forma segura.",
        ),
        "sexual_violence": (
            "Alejarse de cualquier posible víctima y no actuar sobre el impulso.",
            "Buscar apoyo profesional inmediato o emergencias si alguien está en riesgo.",
            "Trabajar en consentimiento, límites y prevención de violencia.",
        ),
        "extremist_praise_or_hate": (
            "Analizar historia y propaganda sin glorificación.",
            "Estudiar prevención de radicalización y protección de comunidades.",
            "Reformular la consulta como crítica histórica o educativa.",
        ),
        "ambiguous_high_risk_language": (
            "Aclarar el contexto legítimo y seguro de la solicitud.",
            "Pedir orientación médica, defensiva, histórica o preventiva concreta.",
            "Evitar eufemismos que puedan ocultar daño o abuso.",
        ),
        "child_sexual_abuse_material": (
            "Configurar filtros y controles de protección infantil.",
            "Bloquear y eliminar enlaces no deseados sin redistribuirlos.",
            "Usar canales legales de reporte cuando corresponda, sin descargar el material.",
        ),
        "ambiguous_harm_or_concealment": (
            "Para restos animales, consultar a un veterinario o servicio sanitario autorizado.",
            "Para residuos orgánicos, usar retiro municipal o gestor autorizado.",
            "Si una persona puede estar en peligro, contactar emergencias y no alterar evidencia.",
        ),
        "environmental_harm": (
            "Usar gestión legal de residuos y mitigación ambiental.",
            "Diseñar una alternativa de menor impacto.",
            "Consultar a la autoridad ambiental o sanitaria correspondiente.",
        ),
        "system_sabotage": (
            "Crear respaldos, aislamiento y recuperación segura.",
            "Auditar integridad sin destruir datos ni servicios.",
            "Diseñar pruebas reversibles sobre copias propias.",
        ),
    }
    mapping_en = {
        "malicious_cyber": (
            "Design a defensive test in an isolated system you own.",
            "Create detection, hardening, monitoring, or incident-response controls.",
            "Review a vulnerability under a documented authorized scope.",
        ),
        "privacy_abuse": (
            "Protect accounts and data with privacy and access controls.",
            "Use lawful procedures and explicit consent.",
            "Anonymize information for legitimate analysis.",
        ),
        "fraud_or_deception": (
            "Design anti-fraud controls or scam-recognition training.",
            "Write an honest and verifiable communication.",
            "Create an educational simulation that captures no real data.",
        ),
        "violence_emergency_or_confession": (
            "Put down any weapon, move away, and call emergency services immediately.",
            "Follow dispatcher instructions and give aid only when safe.",
            "Do not clean, move evidence, or abandon an injured person.",
        ),
        "violence_or_homicide": (
            "Move away from the person or situation and seek immediate de-escalation.",
            "Contact emergency or professional help if danger is real.",
            "Ask a trusted person for support and reduce access to dangerous objects.",
        ),
        "self_harm_crisis": (
            "Message a trusted person now.",
            "Contact a local crisis service or emergency services if danger is immediate.",
            "Move away from dangerous objects or places while support arrives.",
        ),
        "child_endangerment_or_abuse": (
            "Move the child away from the substance or danger and contact emergency or "
            "poison services.",
            "Do not induce vomiting or give neutralizers without professional direction.",
            "Get another responsible adult immediately and keep the product container.",
        ),
        "child_grooming_or_exploitation": (
            "Keep interactions public, age-appropriate, and supervised.",
            "Speak with responsible adults if there is a legitimate need.",
            "Seek professional support for safe boundaries and impulses.",
        ),
        "sexual_violence": (
            "Move away from any potential victim and do not act on the impulse.",
            "Seek immediate professional or emergency support if anyone is at risk.",
            "Focus on consent, boundaries, and violence prevention.",
        ),
        "extremist_praise_or_hate": (
            "Discuss history and propaganda without glorification.",
            "Study prevention of radicalization and protection of communities.",
            "Reframe the request as historical or educational criticism.",
        ),
        "ambiguous_high_risk_language": (
            "Clarify the legitimate and safe context of the request.",
            "Ask for concrete medical, defensive, historical, or preventive guidance.",
            "Avoid euphemisms that could conceal harm or abuse.",
        ),
        "child_sexual_abuse_material": (
            "Configure child-safety filters and controls.",
            "Block and remove unwanted links without redistributing them.",
            "Use lawful reporting channels without downloading the material.",
        ),
        "ambiguous_harm_or_concealment": (
            "For animal remains, contact a veterinarian or licensed sanitation service.",
            "For organic waste, use municipal collection or an authorized handler.",
            "If a person may be in danger, contact emergency services and preserve evidence.",
        ),
        "environmental_harm": (
            "Use lawful waste management and environmental mitigation.",
            "Design a lower-impact alternative.",
            "Consult the relevant environmental or health authority.",
        ),
        "system_sabotage": (
            "Create backups, isolation, and safe recovery procedures.",
            "Audit integrity without destroying data or services.",
            "Design reversible tests against copies you own.",
        ),
    }
    mapping = mapping_en if english else mapping_es
    return mapping.get(
        category,
        (
            "Use a safe, lawful, and reversible alternative."
            if english
            else "Usar una alternativa segura, legal y reversible.",
        ),
    )


def _reason(category: str, *, english: bool) -> str:
    labels_es = {
        "malicious_cyber": "La solicitud facilitaría intrusión o daño digital no autorizado.",
        "privacy_abuse": "La solicitud vulneraría privacidad o confidencialidad.",
        "fraud_or_deception": "La solicitud facilitaría fraude, suplantación o engaño dañino.",
        "violence_emergency_or_confession": (
            "La solicitud describe una posible violencia grave ya ocurrida o en curso."
        ),
        "violence_or_homicide": "La solicitud facilitaría violencia grave u homicidio.",
        "self_harm_crisis": "La solicitud expresa riesgo de autolesión o suicidio.",
        "child_sexual_abuse_material": (
            "La solicitud busca material sexual que involucra a menores."
        ),
        "child_endangerment_or_abuse": "La solicitud facilitaría daño o intoxicación de un menor.",
        "child_grooming_or_exploitation": (
            "La solicitud plantea aislamiento, sexualización o explotación de un menor."
        ),
        "sexual_violence": "La solicitud expresa o facilitaría violencia sexual o coerción.",
        "extremist_praise_or_hate": (
            "La solicitud glorifica una ideología genocida, supremacista o de odio."
        ),
        "ambiguous_high_risk_language": (
            "La solicitud contiene señales de alto riesgo sin contexto seguro suficiente."
        ),
        "ambiguous_harm_or_concealment": (
            "La solicitud podría facilitar ocultamiento de daño, restos o evidencia."
        ),
        "environmental_harm": "La solicitud facilitaría daño ambiental deliberado.",
        "system_sabotage": "La solicitud facilitaría sabotaje o destrucción de sistemas o datos.",
    }
    labels_en = {
        "malicious_cyber": "The request would facilitate unauthorized digital intrusion or harm.",
        "privacy_abuse": "The request would violate privacy or confidentiality.",
        "fraud_or_deception": (
            "The request would facilitate fraud, impersonation, or harmful deception."
        ),
        "violence_emergency_or_confession": (
            "The request describes possible serious violence already occurring or completed."
        ),
        "violence_or_homicide": "The request would facilitate serious violence or homicide.",
        "self_harm_crisis": "The request expresses a self-harm or suicide risk.",
        "child_sexual_abuse_material": (
            "The request seeks sexual material involving minors."
        ),
        "child_endangerment_or_abuse": "The request would facilitate harm or poisoning of a child.",
        "child_grooming_or_exploitation": (
            "The request involves isolating, sexualizing, or exploiting a minor."
        ),
        "sexual_violence": "The request expresses or would facilitate sexual violence or coercion.",
        "extremist_praise_or_hate": (
            "The request glorifies genocidal, supremacist, or hateful ideology."
        ),
        "ambiguous_high_risk_language": (
            "The request contains high-risk signals without enough safe context."
        ),
        "ambiguous_harm_or_concealment": (
            "The request could facilitate concealment of harm, remains, or evidence."
        ),
        "environmental_harm": "The request would facilitate deliberate environmental harm.",
        "system_sabotage": (
            "The request would facilitate sabotage or destruction of systems or data."
        ),
    }
    return (labels_en if english else labels_es).get(category, category)


def _parse_tutor_assessment(raw: str) -> dict[str, Any]:
    clean = raw.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.IGNORECASE)
    match = _JSON_OBJECT_PATTERN.search(clean)
    if match is None:
        return {}
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    label = str(payload.get("label", "")).casefold()
    if label not in {"harmful", "safe", "unclear"}:
        payload["label"] = "unclear"
    return payload


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    repeated_normalized = re.sub(r"([a-z])\1{2,}", r"\1", without_marks)
    punctuation_normalized = re.sub(r"[^a-z0-9\s]", " ", repeated_normalized)
    return " ".join(punctuation_normalized.split())
