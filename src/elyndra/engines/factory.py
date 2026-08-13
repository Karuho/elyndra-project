from __future__ import annotations

from elyndra.config import AppConfig
from elyndra.engines.base import LanguageEngine
from elyndra.engines.llama_cli import LlamaCliEngine
from elyndra.engines.no_model import NoModelEngine
from elyndra.engines.ollama_local import OllamaLocalEngine
from elyndra.models import LanguageConfig, LanguageConfigError
from elyndra.paths import ElyndraPaths
from elyndra.persona import AgentPersona


def build_language_engine(
    paths: ElyndraPaths,
    app_config: AppConfig,
    persona: AgentPersona | None = None,
) -> LanguageEngine:
    try:
        config = LanguageConfig.load(paths)
    except LanguageConfigError as exc:
        return NoModelEngine(reason=str(exc), name="no-model:config-error")
    if not config.enabled:
        return NoModelEngine()

    presentation = _presentation(persona)
    if config.backend == "ollama-local":
        return OllamaLocalEngine(
            config,
            app_config.agent_name,
            app_config.owner_name,
            **presentation,
        )
    if config.backend == "llama-cli":
        return LlamaCliEngine(
            config,
            app_config.agent_name,
            app_config.owner_name,
            **presentation,
        )
    return NoModelEngine(
        reason=f"Backend lingüístico no implementado: {config.backend}",
        name="no-model:backend-error",
    )


def _presentation(persona: AgentPersona | None) -> dict[str, str]:
    if persona is None:
        return {}
    return {
        "personality": persona.personality,
        "tone": persona.tone,
        "formality": persona.formality,
        "verbosity": persona.verbosity,
        "follow_up_style": persona.follow_up_style,
    }
