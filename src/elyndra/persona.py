from __future__ import annotations

import tomllib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from elyndra.config import AppConfig
from elyndra.paths import ElyndraPaths


class PersonaConfigError(RuntimeError):
    pass


_DEFAULT_PRINCIPLES = (
    "Local-first y funcional sin conexión por defecto.",
    "Los datos, recuerdos y decisiones pertenecen al propietario.",
    "Los modelos lingüísticos son componentes opcionales y reemplazables.",
    "Debe funcionar razonablemente en equipos domésticos modestos.",
    "Toda acción sensible debe ser visible, auditable y reversible.",
)

_DEFAULT_BOUNDARIES = (
    "No inventar acciones, archivos, recuerdos, fuentes ni resultados.",
    "No enviar información a terceros sin autorización explícita.",
    "No exponer secretos al modelo lingüístico.",
    "No confundir Elyndra con una persona, empresa o personaje ficticio.",
)


@dataclass(frozen=True, slots=True)
class AgentPersona:
    agent_name: str
    project_name: str
    owner_name: str
    role: str
    mission: str
    principles: tuple[str, ...]
    boundaries: tuple[str, ...]
    gender_identity: str = "neutral"
    pronouns: str = "neutral"
    personality: str = "neutral, cordial and precise"
    tone: str = "warm and clear"
    formality: str = "adaptive"
    verbosity: str = "balanced"
    follow_up_style: str = "only_when_useful"
    source: str = "defaults"

    @classmethod
    def default(cls, config: AppConfig) -> AgentPersona:
        return cls(
            agent_name=config.agent_name,
            project_name="Elyndra",
            owner_name=config.owner_name,
            role="agente personal local, privado y modular",
            mission=(
                f"Ayudar a {config.owner_name} mediante memoria, conocimiento y herramientas "
                "locales, manteniendo el control de los datos en manos del propietario."
            ),
            principles=_DEFAULT_PRINCIPLES,
            boundaries=_DEFAULT_BOUNDARIES,
            gender_identity="neutral",
            pronouns="neutral",
            personality="neutral, cordial and precise",
            tone="warm and clear",
            formality="adaptive",
            verbosity="balanced",
            follow_up_style="only_when_useful",
        )

    @classmethod
    def load(cls, paths: ElyndraPaths, config: AppConfig) -> AgentPersona:
        target = paths.persona_config_file
        if not target.exists():
            return cls.default(config)
        try:
            with target.open("rb") as handle:
                raw = tomllib.load(handle)
            persona = raw.get("persona", {})
            principles = raw.get("principles", {})
            boundaries = raw.get("boundaries", {})
            presentation = raw.get("presentation", {})
            loaded = cls(
                agent_name=_required_text(persona, "agent_name"),
                project_name=_required_text(persona, "project_name"),
                owner_name=str(persona.get("owner_name", config.owner_name)).strip()
                or config.owner_name,
                role=_required_text(persona, "role"),
                mission=_required_text(persona, "mission"),
                principles=_text_tuple(principles.get("items"), "principles.items"),
                boundaries=_text_tuple(boundaries.get("items"), "boundaries.items"),
                gender_identity=_optional_text(presentation, "gender_identity", "neutral"),
                pronouns=_optional_text(presentation, "pronouns", "neutral"),
                personality=_optional_text(
                    presentation, "personality", "neutral, cordial and precise"
                ),
                tone=_optional_text(presentation, "tone", "warm and clear"),
                formality=_optional_text(presentation, "formality", "adaptive"),
                verbosity=_optional_text(presentation, "verbosity", "balanced"),
                follow_up_style=_optional_text(
                    presentation, "follow_up_style", "only_when_useful"
                ),
                source="config",
            )
        except (OSError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
            raise PersonaConfigError(
                f"Configuración de persona inválida en {target}: {exc}"
            ) from exc
        return loaded

    def context_block(self) -> str:
        principles = "\n".join(f"- {item}" for item in self.principles)
        boundaries = "\n".join(f"- {item}" for item in self.boundaries)
        return (
            "IDENTIDAD CANÓNICA DE ELYNDRA (AUTORITATIVA):\n"
            f"- Proyecto: {self.project_name}.\n"
            f"- Nombre del agente: {self.agent_name}.\n"
            f"- Propietario: {self.owner_name}.\n"
            f"- Rol: {self.role}.\n"
            f"- Misión: {self.mission}\n"
            "PRINCIPIOS:\n"
            f"{principles}\n"
            "PRESENTACIÓN:\n"
            f"- Identidad de género: {self.gender_identity}.\n"
            f"- Pronombres: {self.pronouns}.\n"
            f"- Personalidad: {self.personality}.\n"
            f"- Tono: {self.tone}.\n"
            f"- Formalidad: {self.formality}.\n"
            f"- Nivel de detalle: {self.verbosity}.\n"
            f"- Seguimiento: {self.follow_up_style}.\n"
            "LÍMITES:\n"
            f"{boundaries}\n"
            "Usa esta identidad como fuente principal y no la sustituyas por suposiciones."
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_name": self.agent_name,
            "project_name": self.project_name,
            "owner_name": self.owner_name,
            "role": self.role,
            "mission": self.mission,
            "principles": list(self.principles),
            "boundaries": list(self.boundaries),
            "gender_identity": self.gender_identity,
            "pronouns": self.pronouns,
            "personality": self.personality,
            "tone": self.tone,
            "formality": self.formality,
            "verbosity": self.verbosity,
            "follow_up_style": self.follow_up_style,
            "source": self.source,
        }


def default_persona_text(config: AppConfig) -> str:
    persona = AgentPersona.default(config)
    principles = "\n".join(f'  "{_toml_escape(item)}",' for item in persona.principles)
    boundaries = "\n".join(f'  "{_toml_escape(item)}",' for item in persona.boundaries)
    return f'''[persona]
agent_name = "{_toml_escape(persona.agent_name)}"
project_name = "{_toml_escape(persona.project_name)}"
owner_name = "{_toml_escape(persona.owner_name)}"
role = "{_toml_escape(persona.role)}"
mission = "{_toml_escape(persona.mission)}"

[presentation]
gender_identity = "{_toml_escape(persona.gender_identity)}"
pronouns = "{_toml_escape(persona.pronouns)}"
personality = "{_toml_escape(persona.personality)}"
tone = "{_toml_escape(persona.tone)}"
formality = "{_toml_escape(persona.formality)}"
verbosity = "{_toml_escape(persona.verbosity)}"
follow_up_style = "{_toml_escape(persona.follow_up_style)}"

[principles]
items = [
{principles}
]

[boundaries]
items = [
{boundaries}
]
'''


def write_persona(paths: ElyndraPaths, persona: AgentPersona) -> Path:
    paths.ensure()
    target = paths.persona_config_file
    principles = "\n".join(f'  "{_toml_escape(item)}",' for item in persona.principles)
    boundaries = "\n".join(f'  "{_toml_escape(item)}",' for item in persona.boundaries)
    text = f'''[persona]
agent_name = "{_toml_escape(persona.agent_name)}"
project_name = "{_toml_escape(persona.project_name)}"
owner_name = "{_toml_escape(persona.owner_name)}"
role = "{_toml_escape(persona.role)}"
mission = "{_toml_escape(persona.mission)}"

[presentation]
gender_identity = "{_toml_escape(persona.gender_identity)}"
pronouns = "{_toml_escape(persona.pronouns)}"
personality = "{_toml_escape(persona.personality)}"
tone = "{_toml_escape(persona.tone)}"
formality = "{_toml_escape(persona.formality)}"
verbosity = "{_toml_escape(persona.verbosity)}"
follow_up_style = "{_toml_escape(persona.follow_up_style)}"

[principles]
items = [
{principles}
]

[boundaries]
items = [
{boundaries}
]
'''
    target.write_text(text, encoding="utf-8")
    with suppress(PermissionError):
        target.chmod(0o600)
    return target


def write_default_persona(
    paths: ElyndraPaths,
    config: AppConfig,
    *,
    force: bool = False,
) -> Path:
    paths.ensure()
    target = paths.persona_config_file
    if target.exists() and not force:
        raise PersonaConfigError(f"La configuración de persona ya existe: {target}")
    target.write_text(default_persona_text(config), encoding="utf-8")
    with suppress(PermissionError):
        target.chmod(0o600)
    return target


def _optional_text(section: object, key: str, default: str) -> str:
    if not isinstance(section, dict):
        return default
    value = str(section.get(key, default)).strip()
    return value or default


def _required_text(section: object, key: str) -> str:
    if not isinstance(section, dict):
        raise ValueError(f"falta sección para {key}")
    value = str(section.get(key, "")).strip()
    if not value:
        raise ValueError(f"falta valor {key}")
    return value


def _text_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} debe ser una lista")
    cleaned = tuple(str(item).strip() for item in value if str(item).strip())
    if not cleaned:
        raise ValueError(f"{name} no puede estar vacío")
    return cleaned


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
