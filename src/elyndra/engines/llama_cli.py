from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar

from elyndra.engines.base import ConversationTurn, LanguageReply
from elyndra.languages import language_name
from elyndra.models import LanguageConfig

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(slots=True)
class LlamaCliEngine:
    supports_vision: ClassVar[bool] = False
    config: LanguageConfig
    agent_name: str
    owner_name: str
    personality: str = "neutral, cordial and precise"
    tone: str = "warm and clear"
    formality: str = "adaptive"
    verbosity: str = "balanced"
    follow_up_style: str = "only_when_useful"
    _help_text: str | None = field(default=None, init=False, repr=False)

    @property
    def name(self) -> str:
        model_name = self.config.model.name if self.config.model else "sin-modelo"
        return f"llama-cli:{model_name}:{self.config.profile.name}"

    def reply(
        self,
        prompt: str,
        *,
        context: tuple[str, ...] = (),
        history: tuple[ConversationTurn, ...] = (),
        response_language: str | None = None,
        keep_alive_seconds: int = 0,
        images: tuple[str, ...] = (),
        max_tokens: int | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> LanguageReply:
        self.config.validate()
        assert self.config.binary is not None
        assert self.config.model is not None
        del keep_alive_seconds, on_token
        if images:
            raise RuntimeError("llama-cli no tiene un adaptador visual configurado.")
        profile = self.config.profile
        system_prompt = self._system_prompt(response_language)
        user_prompt = self._user_prompt(prompt, context, history)
        command = [
            str(self.config.binary),
            "-m",
            str(self.config.model),
            "-t",
            str(profile.threads),
            "-c",
            str(profile.context_size),
            "-n",
            str(max_tokens if max_tokens is not None else profile.max_tokens),
            "--temp",
            str(profile.temperature),
            "--top-k",
            "20",
            "--top-p",
            "0.9",
        ]
        self._add_option(command, "--threads-batch", str(profile.threads_batch))
        self._add_option(command, "--parallel", "1")
        self._add_option(command, "--cache-type-k", "q8_0")
        self._add_option(command, "--cache-type-v", "q8_0")
        self._add_option(command, "--prio", "-1")
        self._add_option(command, "--poll", "0")
        self._add_option(command, "--reasoning", "off")
        self._add_option(command, "--reasoning-budget", "0")
        self._add_flag(command, "--conversation")
        self._add_flag(command, "--single-turn")
        self._add_flag(command, "--simple-io")
        self._add_flag(command, "--no-display-prompt")
        self._add_flag(command, "--no-show-timings")
        self._add_flag(command, "--no-warmup")
        if self._supports("--color"):
            command.extend(("--color", "off"))
        if self._supports("--system-prompt"):
            command.extend(("--system-prompt", system_prompt))
        else:
            user_prompt = f"INSTRUCCIONES DEL SISTEMA:\n{system_prompt}\n\n{user_prompt}"
        command.extend(("--prompt", user_prompt))

        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=profile.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"El modelo excedió el timeout de {profile.timeout_seconds} segundos."
            ) from exc
        except OSError as exc:
            raise RuntimeError(f"No pude ejecutar llama-cli: {exc}") from exc

        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout).strip()
            error = error[-1500:] if error else f"código de salida {completed.returncode}"
            raise RuntimeError(f"llama-cli falló: {error}")

        output = _clean_output(completed.stdout)
        if not output:
            raise RuntimeError("llama-cli terminó sin devolver texto.")
        return LanguageReply(text=output, engine=self.name, generated=True)

    def release(self) -> None:
        return None

    def _system_prompt(self, response_language: str | None) -> str:
        language_instruction = (
            f"Responde exclusivamente en {language_name(response_language)}. "
            if response_language
            else ""
        )
        return (
            f"Eres {self.agent_name}, el asistente local privado de {self.owner_name}. "
            f"{language_instruction}"
            f"Personalidad configurada: {self.personality}. Tono: {self.tone}. "
            f"Formalidad: {self.formality}. Detalle: {self.verbosity}. "
            f"Seguimiento: {self.follow_up_style}. "
            "Conversa de manera natural, cálida y con criterio. Adapta el humor y la mezcla de "
            "idiomas del propietario sin fingir emociones, experiencias ni gustos reales. Cuando "
            "te pidan una opinión, ofrece una lectura razonada en vez de repetir 'como modelo de "
            "lenguaje'. No afirmes haber ejecutado herramientas ni acciones salvo cuando el "
            "CONTEXTO LOCAL RECUPERADO contenga un plan autorizado y resultados reales de "
            "Elyndra; en ese caso, describe únicamente esas acciones y resultados. La "
            "IDENTIDAD CANÓNICA "
            "es autoritativa. Usa el contexto local solo cuando sea pertinente; no inventes rutas, "
            "secretos, resultados ni recuerdos. Cuando el contexto de un archivo incluya un "
            "diagnóstico determinista, respétalo: leer no equivale a validar y solo el estado "
            "'valid' permite afirmar que la sintaxis fue comprobada. Distingue requisitos "
            "obligatorios de herramientas opcionales. Mantén la respuesta breve salvo que la "
            "tarea requiera detalle y evita "
            "despedidas o cortesías genéricas en cada respuesta."
        )

    @staticmethod
    def _user_prompt(
        prompt: str,
        context: tuple[str, ...],
        history: tuple[ConversationTurn, ...],
    ) -> str:
        sections: list[str] = []
        if context:
            sections.append("CONTEXTO LOCAL RECUPERADO:\n" + "\n\n".join(context))
        if history:
            rendered_history = "\n\n".join(
                f"PROPIETARIO: {turn.user}\nASISTENTE: {turn.assistant}" for turn in history
            )
            sections.append("CONVERSACIÓN RECIENTE:\n" + rendered_history)
        sections.append(f"CONSULTA DEL PROPIETARIO:\n/no_think\n{prompt}")
        return "\n\n".join(sections)

    def _supports(self, option: str) -> bool:
        if self._help_text is None:
            self._help_text = self._read_help()
        return option in self._help_text

    def _read_help(self) -> str:
        assert self.config.binary is not None
        try:
            completed = subprocess.run(
                [str(self.config.binary), "--help"],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return f"{completed.stdout}\n{completed.stderr}"

    def _add_option(self, command: list[str], option: str, value: str) -> None:
        if self._supports(option):
            command.extend((option, value))

    def _add_flag(self, command: list[str], option: str) -> None:
        if self._supports(option):
            command.append(option)


def _clean_output(value: str) -> str:
    clean = _ANSI_ESCAPE.sub("", value).replace("\r", "").strip()
    for prefix in ("assistant:", "Assistant:", "Elyn:"):
        if clean.startswith(prefix):
            clean = clean[len(prefix) :].lstrip()
    return clean
