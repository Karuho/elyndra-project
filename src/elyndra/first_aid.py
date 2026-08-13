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

_TOPIC_LIST_PATTERNS = (
    "que primeros auxilios sabes",
    "qué primeros auxilios sabes",
    "que primer auxilio sabes",
    "qué primer auxilio sabes",
    "que primeros auxilio sabes",
    "qué primeros auxilio sabes",
    "que primeros auxilios conoces",
    "qué primeros auxilios conoces",
    "que emergencias puedes atender",
    "qué emergencias puedes atender",
    "first aid topics",
    "what first aid do you know",
)

_DIRECT_EMERGENCY_PATTERNS = (
    re.compile(r"^(?:me|se) (?:ahogo|ahoga|atraganto|atraganta)$", re.I),
    re.compile(r"^(?:me estoy|se esta|se está) (?:ahogando|atragantando)$", re.I),
    re.compile(r"^(?:no puedo|no puede) respirar$", re.I),
    re.compile(r"^(?:me|se) (?:queme|quemé|quemo|quemó)$", re.I),
    re.compile(r"^(?:bebi|bebí|tome|tomé|trague|tragué) (?:acido|ácido|veneno)$", re.I),
)

_QUERY_PATTERNS = (
    re.compile(
        r"^(?:primeros auxilios|ayuda urgente|que hago si|qué hago si)"
        r"\s+(?:para\s+)?(.+?)\??$",
        re.I,
    ),
    re.compile(r"^(?:first aid|what should i do if)\s+(?:for\s+)?(.+?)\??$", re.I),
)


@dataclass(frozen=True, slots=True)
class FirstAidTopic:
    topic_id: str
    title: str
    summary: str
    steps: tuple[str, ...]
    avoid: tuple[str, ...]
    urgency: str = "emergency"
    red_flags: tuple[str, ...] = ()
    source_package: str = "core"
    package_name: str = ""
    locale: str = ""
    reviewed_on: str = ""
    license_id: str = ""
    source_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    attribution: tuple[str, ...] = ()
    source_relative_path: str = ""
    source_title: str = ""
    source_sha256: str = ""
    source_url: str = ""
    source_attribution: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "title": self.title,
            "summary": self.summary,
            "steps": list(self.steps),
            "avoid": list(self.avoid),
            "urgency": self.urgency,
            "red_flags": list(self.red_flags),
            "source_package": self.source_package,
            "package_name": self.package_name,
            "locale": self.locale,
            "reviewed_on": self.reviewed_on,
            "license_id": self.license_id,
            "source_refs": list(self.source_refs),
            "limitations": list(self.limitations),
            "attribution": list(self.attribution),
            "source_relative_path": self.source_relative_path,
            "source_title": self.source_title,
            "source_sha256": self.source_sha256,
            "source_url": self.source_url,
            "source_attribution": self.source_attribution,
        }


class FirstAidLibrary:
    def __init__(
        self,
        structured_packs: StructuredPackRepository | None = None,
    ) -> None:
        self._structured_packs = structured_packs
        resource = files("elyndra.resources").joinpath("first_aid_core_v1.json")
        raw = resource.read_bytes()
        self._sha256 = hashlib.sha256(raw).hexdigest()
        payload = json.loads(raw.decode("utf-8"))
        self._payload = payload
        self._topics = {str(item["id"]): item for item in payload.get("topics", [])}
        self._aliases: dict[str, str] = {}
        for topic_id, item in self._topics.items():
            self._aliases[_normalize(topic_id.replace("_", " "))] = topic_id
            for alias in item.get("aliases", []):
                self._aliases[_normalize(str(alias))] = topic_id

    @property
    def topic_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._topics))

    def status(self) -> dict[str, Any]:
        return {
            "package_id": self._payload.get("package_id", ""),
            "version": self._payload.get("version", ""),
            "reviewed_on": self._payload.get("reviewed_on", ""),
            "topic_count": len(self._topics),
            "complete_manual": bool(self._payload.get("complete_manual", False)),
            "network_required": False,
            "model_required": False,
            "sha256": self._sha256,
            "sources": list(self._payload.get("sources", [])),
            "structured_packs": (
                self._structured_packs.status()
                if self._structured_packs is not None
                else {
                    "pack_count": 0,
                    "first_aid_pack_count": 0,
                    "first_aid_card_count": 0,
                    "disk_backed": True,
                }
            ),
            "reviewed_packs_only": True,
        }

    def topic(self, topic_id: str, *, language: str = "es") -> FirstAidTopic | None:
        if "::" in topic_id and self._structured_packs is not None:
            package_id, card_id = topic_id.split("::", 1)
            external = self._structured_packs.get_first_aid_card(package_id, card_id)
            return self._external_topic(external) if external is not None else None
        item = self._topics.get(topic_id)
        if item is None:
            return None
        lang = "en" if language.lower().startswith("en") else "es"
        return FirstAidTopic(
            topic_id=topic_id,
            title=str(item.get("title", {}).get(lang, topic_id)),
            summary=str(item.get("summary", {}).get(lang, "")),
            steps=tuple(str(step) for step in item.get("steps", {}).get(lang, [])),
            avoid=tuple(str(step) for step in item.get("avoid", {}).get(lang, [])),
        )

    def lookup(
        self,
        query: str,
        *,
        language: str = "es",
        locale: str | None = None,
    ) -> FirstAidTopic | None:
        if self._structured_packs is not None:
            external = self._structured_packs.lookup_first_aid(
                query,
                language=language,
                locale=locale,
            )
            if external is not None:
                return self._external_topic(external)
        normalized = _normalize(query)
        direct = self._aliases.get(normalized)
        if direct is not None:
            return self.topic(direct, language=language)
        candidates = [
            (len(alias), topic_id)
            for alias, topic_id in self._aliases.items()
            if alias and alias in normalized
        ]
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return self.topic(candidates[0][1], language=language)

    def catalog(self, *, language: str = "es") -> tuple[str, dict[str, Any]]:
        topics = [self.topic(topic_id, language=language) for topic_id in self.topic_ids]
        clean_topics = [topic for topic in topics if topic is not None]
        if language.lower().startswith("en"):
            heading = "Local first-aid topics available without a model:"
        else:
            heading = "Primeros auxilios locales disponibles sin usar el modelo:"
        lines = [heading]
        lines.extend(f"- {topic.title}" for topic in clean_topics)
        status = self.status()
        external_count = int(status.get("structured_packs", {}).get("first_aid_card_count", 0))
        if external_count:
            lines.append(

                    f"- {external_count} additional reviewed cards from installed packs."
                    if language.lower().startswith("en")
                    else (
                        f"- {external_count} tarjetas revisadas adicionales"
                        " de paquetes instalados."
                    )

            )
        footer = (
            "Describe the situation briefly to retrieve the closest card immediately."
            if language.lower().startswith("en")
            else (
                "Describe brevemente la situación para recuperar la tarjeta "
                "más cercana de inmediato."
            )
        )
        lines.extend(["", footer])
        return "\n".join(lines), {
            "found": True,
            "catalog": True,
            "topics": [topic.to_dict() for topic in clean_topics],
            "status": status,
        }

    def render(self, topic_id: str, *, language: str = "es") -> tuple[str, dict[str, Any]]:
        topic = self.topic(topic_id, language=language)
        if topic is None:
            message = (
                "No encontré una tarjeta local de primeros auxilios para esa situación. "
                "Si existe peligro inmediato, llama a emergencias ahora."
            )
            return message, {"found": False, "topic_id": topic_id, "status": self.status()}
        return self.render_topic(topic, language=language)

    def render_topic(
        self,
        topic: FirstAidTopic,
        *,
        language: str = "es",
    ) -> tuple[str, dict[str, Any]]:
        english = language.lower().startswith("en")
        headings = {
            "emergency": (
                "EMERGENCY — call local emergency services now and use speakerphone.",
                "EMERGENCIA — llama ahora a emergencias locales y activa el altavoz.",
            ),
            "urgent": (
                "URGENT FIRST AID — act now and seek professional help if warning signs appear.",
                "PRIMEROS AUXILIOS URGENTES — actúa ahora y busca ayuda profesional "
                "si aparecen señales críticas.",
            ),
            "routine": (
                "FIRST AID — follow these local steps and seek professional advice if needed.",
                "PRIMEROS AUXILIOS — sigue estos pasos locales y consulta a un "
                "profesional si es necesario.",
            ),
        }
        heading_pair = headings.get(topic.urgency, headings["emergency"])
        heading = heading_pair[0] if english else heading_pair[1]
        steps_label = "Immediate steps" if english else "Pasos inmediatos"
        avoid_label = "Avoid" if english else "Evita"
        lines = [heading, "", f"{topic.title}: {topic.summary}", "", f"{steps_label}:"]
        lines.extend(f"{index}. {step}" for index, step in enumerate(topic.steps, 1))
        if topic.avoid:
            lines.extend(["", f"{avoid_label}:"])
            lines.extend(f"- {item}" for item in topic.avoid)
        if topic.red_flags:
            red_flags_label = "Critical warning signs" if english else "Señales críticas"
            lines.extend(["", f"{red_flags_label}:"])
            lines.extend(f"- {item}" for item in topic.red_flags)
        if topic.source_package != "core":
            source_label = "Reviewed pack" if english else "Paquete revisado"
            lines.extend(
                [
                    "",
                    f"{source_label}: {topic.source_package}; "
                    f"locale {topic.locale or '-'}; review {topic.reviewed_on or '-'}."
                ]
            )
            if topic.source_title:
                source_title_label = "Source" if english else "Fuente"
                source_line = f"{source_title_label}: {topic.source_title}"
                if topic.source_sha256:
                    source_line += f" · SHA-256 {topic.source_sha256}"
                lines.append(source_line)
            if topic.source_attribution:
                attribution_label = "Attribution" if english else "Atribución"
                lines.append(f"{attribution_label}: {topic.source_attribution}")
            if topic.limitations:
                limits_label = "Known limitations" if english else "Limitaciones conocidas"
                lines.append(f"{limits_label}: " + "; ".join(topic.limitations))
        footer = (
            "This offline card supports the first minutes; it does not replace emergency "
            "professionals or practical training."
            if english
            else (
                "Esta tarjeta local orienta los primeros minutos; no reemplaza a emergencias "
                "ni a una capacitación práctica."
            )
        )
        lines.extend(["", footer])
        return "\n".join(lines), {
            "found": True,
            "topic": topic.to_dict(),
            "status": self.status(),
        }

    @staticmethod
    def _external_topic(item: dict[str, Any]) -> FirstAidTopic:
        return FirstAidTopic(
            topic_id=str(item.get("topic_id", "")),
            title=str(item.get("title", "")),
            summary=str(item.get("summary", "")),
            urgency=str(item.get("urgency", "emergency")),
            steps=tuple(str(value) for value in item.get("steps", [])),
            avoid=tuple(str(value) for value in item.get("avoid", [])),
            red_flags=tuple(str(value) for value in item.get("red_flags", [])),
            source_package=str(item.get("package_id", "")),
            package_name=str(item.get("package_name", "")),
            locale=str(item.get("locale", "")),
            reviewed_on=str(
                item.get("reviewed_on", item.get("package_reviewed_on", ""))
            ),
            license_id=str(item.get("license_id", "")),
            source_refs=tuple(str(value) for value in item.get("source_refs", [])),
            limitations=tuple(str(value) for value in item.get("limitations", [])),
            attribution=tuple(str(value) for value in item.get("attribution", [])),
            source_relative_path=str(item.get("source_relative_path", "")),
            source_title=str(item.get("source_title", "")),
            source_sha256=str(item.get("source_sha256", "")),
            source_url=str(item.get("source_url", "")),
            source_attribution=str(item.get("source_attribution", "")),
        )


def extract_first_aid_query(text: str) -> str | None:
    clean = " ".join(text.strip().strip("¿? ").split())
    if not clean or len(clean) > 180:
        return None
    normalized = _normalize(clean)
    if normalized in {_normalize(value) for value in _TOPIC_LIST_PATTERNS}:
        return "__topics__"
    for pattern in _DIRECT_EMERGENCY_PATTERNS:
        if pattern.match(clean) is not None:
            return clean
    for pattern in _QUERY_PATTERNS:
        match = pattern.match(clean)
        if match is not None:
            return match.group(1).strip(" \t\n\r\"'¿?.,:;")
    return None


def _normalize(value: str) -> str:
    clean = unicodedata.normalize("NFKD", value.casefold())
    clean = "".join(char for char in clean if not unicodedata.combining(char))
    clean = re.sub(r"([a-z])\1{2,}", r"\1", clean)
    clean = re.sub(r"[^a-z0-9\s]", " ", clean)
    return " ".join(clean.split())
