from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from elyndra.engines.base import ConversationTurn, LanguageReply
from elyndra.languages import language_name
from elyndra.models import LanguageConfig, validate_loopback_endpoint


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise RuntimeError(f"Ollama intentó redirigir la solicitud local a: {newurl}")


@dataclass(slots=True)
class OllamaLocalEngine:
    config: LanguageConfig
    agent_name: str
    owner_name: str
    personality: str = "neutral, cordial and precise"
    tone: str = "warm and clear"
    formality: str = "adaptive"
    verbosity: str = "balanced"
    follow_up_style: str = "only_when_useful"
    _supports_vision_cache: bool | None = field(default=None, init=False, repr=False)

    @property
    def supports_vision(self) -> bool:
        if self._supports_vision_cache is not None:
            return self._supports_vision_cache
        try:
            self.config.validate()
            assert self.config.endpoint is not None
            assert self.config.model_name is not None
            data = _request_json(f"{self.config.endpoint}/api/tags", None, timeout=5)
            models = data.get("models", [])
            configured = self.config.model_name.casefold()
            supported = False
            if isinstance(models, list):
                for item in models:
                    if not isinstance(item, dict):
                        continue
                    names = {
                        str(item.get("name", "")).casefold(),
                        str(item.get("model", "")).casefold(),
                    }
                    if configured not in names:
                        continue
                    capabilities = item.get("capabilities", [])
                    supported = isinstance(capabilities, list) and "vision" in capabilities
                    break
            self._supports_vision_cache = supported
        except RuntimeError:
            self._supports_vision_cache = False
        return self._supports_vision_cache

    @property
    def name(self) -> str:
        model_name = self.config.model_name or "sin-modelo"
        return f"ollama-local:{model_name}:{self.config.profile.name}"

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
        assert self.config.endpoint is not None
        assert self.config.model_name is not None
        if images and not self.supports_vision:
            raise RuntimeError(
                "El modelo Ollama configurado no declara capacidad visual."
            )
        profile = self.config.profile
        payload = {
            "model": self.config.model_name,
            "messages": self._messages(
                prompt, context, history, response_language, images
            ),
            "stream": on_token is not None,
            "keep_alive": max(0, keep_alive_seconds),
            "options": {
                "num_ctx": profile.context_size,
                "num_predict": max_tokens if max_tokens is not None else profile.max_tokens,
                "num_thread": profile.threads,
                "temperature": profile.temperature,
                "top_k": 20,
                "top_p": 0.9,
            },
        }
        endpoint = f"{self.config.endpoint}/api/chat"
        if on_token is None:
            data = _request_json(endpoint, payload, timeout=profile.timeout_seconds)
            message = data.get("message", {})
            text = (
                str(message.get("content", "")).strip()
                if isinstance(message, dict)
                else ""
            )
        else:
            text, data = _request_json_stream(
                endpoint,
                payload,
                timeout=profile.timeout_seconds,
                on_token=on_token,
            )
        if not text:
            raise RuntimeError("Ollama terminó sin devolver texto.")
        return LanguageReply(
            text=text,
            engine=self.name,
            generated=True,
            metadata=_reply_metadata(
                data,
                model_name=self.config.model_name,
                keep_alive_seconds=keep_alive_seconds,
                streamed=on_token is not None,
            ),
        )

    def release(self) -> None:
        self.config.validate()
        assert self.config.endpoint is not None
        assert self.config.model_name is not None
        payload = {
            "model": self.config.model_name,
            "prompt": "",
            "stream": False,
            "keep_alive": 0,
        }
        try:
            _request_json(
                f"{self.config.endpoint}/api/generate",
                payload,
                timeout=min(15, self.config.profile.timeout_seconds),
            )
        except RuntimeError:
            return

    def _messages(
        self,
        prompt: str,
        context: tuple[str, ...],
        history: tuple[ConversationTurn, ...],
        response_language: str | None,
        images: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        messages = [
            {"role": "system", "content": self._system_prompt(response_language)}
        ]
        if context:
            messages.append(
                {
                    "role": "system",
                    "content": "CONTEXTO LOCAL RECUPERADO:\n\n" + "\n\n".join(context),
                }
            )
        for turn in history:
            messages.append({"role": "user", "content": turn.user})
            messages.append({"role": "assistant", "content": turn.assistant})
        user_message: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            user_message["images"] = list(images)
        messages.append(user_message)
        return messages

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
            "Conversa de manera natural, cálida y con criterio; no suenes como un formulario "
            "ni repitas frases de atención al cliente. Si el propietario usa humor, bromas, "
            "juegos o mezcla idiomas, adáptate con ligereza sin perder precisión. Puedes jugar "
            "gato, ajedrez por coordenadas, adivinanzas, karaoke libre, improvisación y otros "
            "juegos conversacionales cuando la petición sea segura. El humor puede ser oscuro "
            "o absurdo en un entorno privado, pero no debe humillar a una persona vulnerable, "
            "celebrar daño real ni convertir una emergencia en una broma. No afirmes tener "
            "emociones, experiencias o gustos reales. Cuando te pidan una opinión, evita el "
            "cliché 'como modelo de lenguaje'; entrega una lectura sustantiva con fortalezas, "
            "debilidades o matices. Si no conoces con certeza un dato biográfico, no inventes "
            "nacionalidad, profesión ni hechos para rellenar. No arrastres nombres o temas de "
            "turnos anteriores cuando la pregunta actual no se relaciona con ellos. "
            "Responde de forma clara y breve salvo que la tarea requiera detalle. No afirmes "
            "haber ejecutado herramientas, leído archivos ni realizado acciones salvo cuando "
            "el CONTEXTO LOCAL RECUPERADO contenga un plan autorizado y resultados reales de "
            "Elyndra; en ese caso, describe únicamente esas acciones y resultados. La sección "
            "IDENTIDAD CANÓNICA, cuando esté presente, es autoritativa y prevalece sobre "
            "suposiciones. Usa el contexto local solo cuando sea pertinente. No inventes "
            "fuentes, rutas, secretos, recuerdos ni resultados. Cuando el contexto de un "
            "archivo indique extracción, validación y diagnóstico determinista, esos datos son "
            "autoritativos: no declares sintaxis válida si el estado no es 'valid' y distingue "
            "claramente entre leer, validar y analizar contenido. Distingue requisitos "
            "obligatorios de herramientas opcionales. Cuando exista un siguiente paso realmente "
            "útil, termina con una sola sugerencia o pregunta breve; no agregues despedidas "
            "genéricas en cada respuesta."
        )


def fetch_ollama_models(endpoint: str, *, timeout: int = 10) -> list[dict[str, Any]]:
    safe_endpoint = validate_loopback_endpoint(endpoint)
    data = _request_json(f"{safe_endpoint}/api/tags", None, timeout=timeout)
    models = data.get("models", [])
    if not isinstance(models, list):
        raise RuntimeError("La API local de Ollama devolvió una lista de modelos inválida.")
    return [item for item in models if isinstance(item, dict)]


def fetch_ollama_running(endpoint: str, *, timeout: int = 10) -> list[dict[str, Any]]:
    safe_endpoint = validate_loopback_endpoint(endpoint)
    data = _request_json(f"{safe_endpoint}/api/ps", None, timeout=timeout)
    models = data.get("models", [])
    if not isinstance(models, list):
        raise RuntimeError("La API local de Ollama devolvió un estado inválido.")
    return [item for item in models if isinstance(item, dict)]


def fetch_ollama_version(endpoint: str, *, timeout: int = 10) -> str:
    safe_endpoint = validate_loopback_endpoint(endpoint)
    data = _request_json(f"{safe_endpoint}/api/version", None, timeout=timeout)
    return str(data.get("version", "desconocida"))


def _request_json(url: str, payload: dict[str, Any] | None, *, timeout: int) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=body,
        method="POST" if body is not None else "GET",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Elyndra/0.7.0 local-only",
        },
    )
    opener = build_opener(_RejectRedirects())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"Ollama local respondió HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"No pude conectar con Ollama local: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Ollama local excedió el timeout de {timeout} segundos.") from exc
    except OSError as exc:
        raise RuntimeError(f"Error comunicando con Ollama local: {exc}") from exc

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Ollama local devolvió JSON inválido.") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("Ollama local devolvió una respuesta inesperada.")
    return decoded


def _request_json_stream(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: int,
    on_token: Callable[[str], None],
) -> tuple[str, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/x-ndjson",
            "User-Agent": "Elyndra/0.7.0 local-only",
        },
    )
    opener = build_opener(_RejectRedirects())
    chunks: list[str] = []
    final: dict[str, Any] = {}
    try:
        with opener.open(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Ollama local devolvió un stream inválido.") from exc
                if not isinstance(event, dict):
                    continue
                if event.get("error"):
                    raise RuntimeError(f"Ollama local: {event['error']}")
                message = event.get("message", {})
                token = (
                    str(message.get("content", ""))
                    if isinstance(message, dict)
                    else ""
                )
                if token:
                    chunks.append(token)
                    on_token(token)
                final = event
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"Ollama local respondió HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"No pude conectar con Ollama local: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Ollama local excedió el timeout de {timeout} segundos.") from exc
    except OSError as exc:
        raise RuntimeError(f"Error comunicando con Ollama local: {exc}") from exc
    return "".join(chunks).strip(), final


def _reply_metadata(
    data: dict[str, Any],
    *,
    model_name: str,
    keep_alive_seconds: int,
    streamed: bool,
) -> dict[str, Any]:
    return {
        "model": data.get("model", model_name),
        "done_reason": data.get("done_reason"),
        "total_duration_ns": _integer_or_none(data.get("total_duration")),
        "load_duration_ns": _integer_or_none(data.get("load_duration")),
        "prompt_eval_count": _integer_or_none(data.get("prompt_eval_count")),
        "prompt_eval_duration_ns": _integer_or_none(
            data.get("prompt_eval_duration")
        ),
        "eval_count": _integer_or_none(data.get("eval_count")),
        "eval_duration_ns": _integer_or_none(data.get("eval_duration")),
        "keep_alive": max(0, keep_alive_seconds),
        "streamed": streamed,
    }


def _integer_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None
