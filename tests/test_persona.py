from __future__ import annotations

from elyndra.config import AppConfig
from elyndra.paths import ElyndraPaths
from elyndra.persona import AgentPersona, write_default_persona, write_persona


def test_default_persona_is_available_without_extra_file(
    isolated_home: ElyndraPaths,
) -> None:
    config = AppConfig.load(isolated_home)
    persona = AgentPersona.load(isolated_home, config)

    assert persona.source == "defaults"
    assert persona.project_name == "Elyndra"
    assert "local" in persona.role
    assert config.owner_name in persona.mission
    assert "IDENTIDAD CANÓNICA" in persona.context_block()
    assert persona.gender_identity == "neutral"
    assert persona.follow_up_style == "only_when_useful"


def test_persona_file_round_trip(isolated_home: ElyndraPaths) -> None:
    config = AppConfig.load(isolated_home)
    target = write_default_persona(isolated_home, config)
    persona = AgentPersona.load(isolated_home, config)

    assert target == isolated_home.persona_config_file
    assert persona.source == "config"
    assert persona.agent_name == config.agent_name
    assert persona.owner_name == config.owner_name
    assert persona.personality == "neutral, cordial and precise"


def test_custom_presentation_round_trip(isolated_home: ElyndraPaths) -> None:
    config = AppConfig.load(isolated_home)
    current = AgentPersona.default(config)
    custom = AgentPersona(
        agent_name="Ari",
        project_name=current.project_name,
        owner_name=current.owner_name,
        role=current.role,
        mission=current.mission,
        principles=current.principles,
        boundaries=current.boundaries,
        gender_identity="non-binary",
        pronouns="they/them",
        personality="curious and precise",
        tone="warm",
        formality="adaptive",
        verbosity="concise",
        follow_up_style="only_when_useful",
        source="config",
    )

    write_persona(isolated_home, custom)
    loaded = AgentPersona.load(isolated_home, config)

    assert loaded.agent_name == "Ari"
    assert loaded.gender_identity == "non-binary"
    assert loaded.pronouns == "they/them"
