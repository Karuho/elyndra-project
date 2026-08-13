from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from elyndra.application import ElyndraApplication
from elyndra.engines import ConversationTurn
from elyndra.engines.ollama_local import (
    OllamaLocalEngine,
    fetch_ollama_models,
    fetch_ollama_running,
    fetch_ollama_version,
)
from elyndra.models import (
    PROFILES,
    LanguageConfig,
    LanguageConfigError,
    validate_loopback_endpoint,
    write_ollama_language_config,
)
from elyndra.paths import ElyndraPaths


class _OllamaHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/version":
            self._reply({"version": "test-1.0"})
            return
        if self.path == "/api/tags":
            self._reply(
                {
                    "models": [
                        {
                            "name": "tiny:latest",
                            "details": {
                                "parameter_size": "0.6B",
                                "quantization_level": "Q4_K_M",
                            },
                            "capabilities": ["completion"],
                        }
                    ]
                }
            )
            return
        if self.path == "/api/ps":
            self._reply({"models": []})
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.requests.append(payload)
        if self.path == "/api/generate":
            self._reply({"model": payload["model"], "done": True})
            return
        if self.path == "/api/chat":
            if payload.get("stream"):
                self._reply_stream(
                    [
                        {
                            "model": payload["model"],
                            "message": {"role": "assistant", "content": "Respuesta "},
                            "done": False,
                        },
                        {
                            "model": payload["model"],
                            "message": {"role": "assistant", "content": "local."},
                            "done": True,
                            "done_reason": "stop",
                            "total_duration": 100,
                            "load_duration": 20,
                            "prompt_eval_count": 12,
                            "prompt_eval_duration": 30,
                            "eval_count": 4,
                            "eval_duration": 80,
                        },
                    ]
                )
                return
            self._reply(
                {
                    "model": payload["model"],
                    "message": {"role": "assistant", "content": "Respuesta local."},
                    "done": True,
                    "done_reason": "stop",
                    "total_duration": 100,
                    "load_duration": 20,
                    "prompt_eval_count": 12,
                    "prompt_eval_duration": 30,
                    "eval_count": 4,
                    "eval_duration": 80,
                }
            )
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _reply(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reply_stream(self, payloads: list[dict[str, Any]]) -> None:
        body = b"".join(
            (json.dumps(payload) + "\n").encode("utf-8") for payload in payloads
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def ollama_endpoint() -> str:
    _OllamaHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_loopback_endpoint_rejects_remote_hosts() -> None:
    with pytest.raises(LanguageConfigError):
        validate_loopback_endpoint("https://example.com:11434")
    with pytest.raises(LanguageConfigError):
        validate_loopback_endpoint("http://192.168.1.8:11434")
    with pytest.raises(LanguageConfigError):
        validate_loopback_endpoint("http://localhost:11434/api")


def test_ollama_config_round_trip(
    isolated_home: ElyndraPaths, ollama_endpoint: str
) -> None:
    target = write_ollama_language_config(
        isolated_home,
        endpoint=ollama_endpoint,
        model_name="tiny:latest",
        profile="eco",
        license_id="Apache-2.0",
        role="runtime",
    )
    config = LanguageConfig.load(isolated_home)

    assert target == isolated_home.language_config_file
    assert config.backend == "ollama-local"
    assert config.endpoint == ollama_endpoint
    assert config.model_name == "tiny:latest"
    assert config.license_id == "Apache-2.0"
    assert config.teacher_allowed is False
    assert config.connectivity == "local-only"


def test_ollama_api_inspection(ollama_endpoint: str) -> None:
    assert fetch_ollama_version(ollama_endpoint) == "test-1.0"
    assert fetch_ollama_models(ollama_endpoint)[0]["name"] == "tiny:latest"
    assert fetch_ollama_running(ollama_endpoint) == []


def test_ollama_engine_uses_limits_and_unloads(ollama_endpoint: str) -> None:
    config = LanguageConfig(
        enabled=True,
        backend="ollama-local",
        binary=None,
        model=None,
        profile=PROFILES["eco"],
        endpoint=ollama_endpoint,
        model_name="tiny:latest",
        license_id="Apache-2.0",
    )
    engine = OllamaLocalEngine(config, "Elyn", "Carlos")

    reply = engine.reply(
        "Saluda",
        context=("IDENTIDAD CANÓNICA: Elyndra es privada",),
        history=(ConversationTurn("¿Quién eres?", "Soy Elyn."),),
        response_language="en",
    )

    assert reply.text == "Respuesta local."
    assert reply.generated is True
    assert reply.metadata["keep_alive"] == 0
    assert reply.metadata["prompt_eval_duration_ns"] == 30
    payload = _OllamaHandler.requests[-1]
    assert payload["keep_alive"] == 0
    assert payload["stream"] is False
    assert payload["options"]["num_ctx"] == 2048
    assert payload["options"]["num_thread"] == 3
    assert payload["messages"][-3:] == [
        {"role": "user", "content": "¿Quién eres?"},
        {"role": "assistant", "content": "Soy Elyn."},
        {"role": "user", "content": "Saluda"},
    ]
    assert "inglés" in payload["messages"][0]["content"]
    assert "IDENTIDAD CANÓNICA" in payload["messages"][1]["content"]



def test_ollama_engine_can_stay_warm_during_chat(ollama_endpoint: str) -> None:
    config = LanguageConfig(
        enabled=True,
        backend="ollama-local",
        binary=None,
        model=None,
        profile=PROFILES["eco"],
        endpoint=ollama_endpoint,
        model_name="tiny:latest",
    )
    engine = OllamaLocalEngine(config, "Elyn", "Carlos")

    reply = engine.reply("Hola", keep_alive_seconds=120)

    assert reply.metadata["keep_alive"] == 120
    assert _OllamaHandler.requests[-1]["keep_alive"] == 120

    engine.release()

    assert _OllamaHandler.requests[-1]["keep_alive"] == 0
    assert _OllamaHandler.requests[-1]["prompt"] == ""




def test_ollama_engine_streams_tokens(ollama_endpoint: str) -> None:
    config = LanguageConfig(
        enabled=True,
        backend="ollama-local",
        binary=None,
        model=None,
        profile=PROFILES["eco"],
        endpoint=ollama_endpoint,
        model_name="tiny:latest",
    )
    engine = OllamaLocalEngine(config, "Elyn", "Carlos")
    tokens: list[str] = []

    reply = engine.reply("Hola", keep_alive_seconds=600, on_token=tokens.append)

    assert tokens == ["Respuesta ", "local."]
    assert reply.text == "Respuesta local."
    assert reply.metadata["keep_alive"] == 600
    assert reply.metadata["streamed"] is True
    assert _OllamaHandler.requests[-1]["stream"] is True

def test_application_loads_ollama_engine(
    isolated_home: ElyndraPaths, ollama_endpoint: str
) -> None:
    write_ollama_language_config(
        isolated_home,
        endpoint=ollama_endpoint,
        model_name="tiny:latest",
        profile="eco",
    )

    app = ElyndraApplication.load(isolated_home)
    result = app.ask("Dime algo nuevo")

    assert result.ok is True
    assert result.message == "Respuesta local."
    assert result.data["generated"] is True
    assert result.data["metrics"]["keep_alive"] == 0
    assert result.data["detected_language"] == "es"
    assert result.data["response_language"] == "es"
    assert result.data["identity_source"] == "defaults"
    payload = _OllamaHandler.requests[-1]
    assert len(payload["messages"]) == 3
    assert "Conversa de manera natural" in payload["messages"][0]["content"]
    assert "CONSTITUCIÓN ÉTICA" in payload["messages"][1]["content"]
    assert payload["messages"][2]["content"] == "Dime algo nuevo"
