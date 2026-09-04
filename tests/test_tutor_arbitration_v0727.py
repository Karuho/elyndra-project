from __future__ import annotations

import hashlib

from elyndra import __version__
from elyndra.application import ElyndraApplication
from elyndra.engines import LanguageReply
from elyndra.paths import ElyndraPaths
from elyndra.tutors import BENCHMARK_CASES, evaluate_benchmark
from elyndra.web.server import ElyndraWebService


class EchoEngine:
    name = "echo-primary"
    supports_vision = False

    def __init__(self, text: str = "Respuesta del tutor principal.") -> None:
        self.text = text
        self.calls: list[str] = []

    def reply(self, prompt: str, **kwargs):
        self.calls.append(prompt)
        return LanguageReply(
            text=self.text,
            engine=self.name,
            generated=True,
            metadata={"fake": True},
        )

    def release(self) -> None:
        return None


class BenchmarkEngine(EchoEngine):
    def reply(self, prompt: str, **kwargs):
        self.calls.append(prompt)
        if "ELYNDRA_OK" in prompt:
            text = "ELYNDRA_OK"
        elif "'perro'" in prompt:
            text = "dog"
        elif "copia de seguridad" in prompt:
            text = (
                "La copia de seguridad nocturna permite restaurar "
                "datos verificables."
            )
        elif "def double" in prompt:
            text = "Multiplica el valor por dos y devuelve el resultado."
        elif "exactamente estas claves" in prompt:
            text = '{"status":"ok","tools":false}'
        else:
            text = self.text
        return LanguageReply(
            text=text,
            engine=self.name,
            generated=True,
            metadata={"fake": True},
        )


class FailingEngine:
    name = "failing-external"
    supports_vision = False

    def reply(self, prompt: str, **kwargs):
        raise RuntimeError("fallo controlado")

    def release(self) -> None:
        return None


def _write_external_tutor(paths: ElyndraPaths) -> None:
    paths.tutors_config_file.write_text(
        """
[arbitration]
enabled = true

[[tutor]]
id = "external-test"
name = "External test tutor"
backend = "ollama-local"
endpoint = "http://127.0.0.1:11434"
model_name = "test:latest"
profile = "eco"
role = "teacher"
teacher_allowed = true
license_id = "test-only"
priority = 60
enabled = true
tasks = ["general_language", "translation", "code_explanation"]
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _add_completed_score(
    app: ElyndraApplication,
    *,
    tutor_id: str,
    task: str,
    score: float,
) -> str:
    case = next(item for item in BENCHMARK_CASES if item.task == task)
    run_id = app.tutor_benchmarks.start_run(tutor_count=1, actor="test")
    app.tutor_benchmarks.add_result(
        run_id,
        tutor_id=tutor_id,
        engine_name=tutor_id,
        case=case,
        score=score,
        passed=score >= 0.5,
        latency_ms=5,
        output_sha256="0" * 64,
        metrics={},
    )
    app.tutor_benchmarks.finish_run(run_id)
    return run_id


def test_tutor_status_defaults_to_primary_or_safe_empty(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    status = app.tutor_status()

    assert status["automatic_download"] is False
    assert status["tools_allowed"] is False
    assert status["authority_transferred"] is False
    assert status["background_benchmarks"] is False
    assert status["prompt_text_stored"] is False
    assert status["output_text_stored"] is False
    assert status["config"] == str(isolated_home.tutors_config_file)


def test_invalid_tutor_config_does_not_break_application(
    isolated_home: ElyndraPaths,
) -> None:
    isolated_home.tutors_config_file.write_text(
        """
[[tutor]]
id = "remote"
backend = "ollama-local"
endpoint = "https://example.com"
model_name = "unsafe"
teacher_allowed = true
role = "teacher"
""",
        encoding="utf-8",
    )

    app = ElyndraApplication.load(isolated_home)

    assert app.tutor_status()["config_error"]
    assert app.tutor_status()["external_tutors"] == 0


def test_recommendation_without_benchmark_preserves_primary(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = EchoEngine()

    recommendation = app.recommend_tutor("general_language")

    assert recommendation["tutor_id"] == "primary"
    assert recommendation["primary"] is True
    assert recommendation["automatic_execution"] is False
    assert recommendation["authority_transferred"] is False


def test_benchmark_evaluators_are_deterministic() -> None:
    translation = next(item for item in BENCHMARK_CASES if item.task == "translation")
    strict_json = next(
        item for item in BENCHMARK_CASES if item.case_id == "strict-json"
    )

    assert evaluate_benchmark(translation, "dog")[:2] == (1.0, True)
    assert evaluate_benchmark(translation, "The answer is dog")[:2] == (0.0, False)
    assert evaluate_benchmark(strict_json, '{"status":"ok","tools":false}')[:2] == (
        1.0,
        True,
    )
    assert evaluate_benchmark(strict_json, "```json\n{}\n```")[1] is False


def test_approved_benchmark_records_hashes_not_raw_prompts(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    engine = BenchmarkEngine()
    app.language_engine = engine

    result = app.run_tutor_benchmarks(approved=True)

    assert result.ok is True
    assert result.data["raw_prompts_stored"] is False
    assert result.data["raw_outputs_stored"] is False
    assert len(engine.calls) == len(BENCHMARK_CASES)
    details = app.tutor_benchmarks.run_details(result.data["run_id"])
    assert details is not None
    assert len(details["results"]) == len(BENCHMARK_CASES)
    assert all(len(item["output_sha256"]) == 64 for item in details["results"])
    with app.database.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(assistant_tutor_benchmark_results)"
            )
        }
    assert "prompt" not in columns
    assert "output_text" not in columns


def test_external_tutor_can_be_selected_only_from_local_reviewed_config(
    isolated_home: ElyndraPaths,
) -> None:
    _write_external_tutor(isolated_home)
    app = ElyndraApplication.load(isolated_home)
    primary = EchoEngine("primary")
    external = EchoEngine("external")
    app.language_engine = primary
    app.tutor_arbitrator._engine_cache["external-test"] = external
    _add_completed_score(
        app,
        tutor_id="external-test",
        task="general_language",
        score=1.0,
    )

    reply = app.tutor_arbitrator.reply(
        "general_language",
        "Pregunta de prueba",
        primary_engine=primary,
    )

    selection = reply.metadata["tutor_selection"]
    assert reply.text == "external"
    assert selection["tutor_id"] == "external-test"
    assert selection["authority"] is False
    assert selection["tools_allowed"] is False
    assert selection["permissions_transferred"] is False
    assert primary.calls == []


def test_external_failure_falls_back_to_primary_and_is_traceable(
    isolated_home: ElyndraPaths,
) -> None:
    _write_external_tutor(isolated_home)
    app = ElyndraApplication.load(isolated_home)
    primary = EchoEngine("primary fallback")
    app.language_engine = primary
    app.tutor_arbitrator._engine_cache["external-test"] = FailingEngine()
    _add_completed_score(
        app,
        tutor_id="external-test",
        task="general_language",
        score=1.0,
    )

    reply = app.tutor_arbitrator.reply(
        "general_language",
        "Pregunta con fallback",
        primary_engine=primary,
    )

    selection = reply.metadata["tutor_selection"]
    assert reply.text == "primary fallback"
    assert selection["tutor_id"] == "primary"
    assert selection["fallback_used"] is True
    recent = app.tutor_benchmarks.list_selections(limit=1)[0]
    assert recent["fallback_used"] is True
    assert recent["prompt_sha256"] == hashlib.sha256(
        b"Pregunta con fallback"
    ).hexdigest()


def test_application_fallback_exposes_tutor_provenance(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = EchoEngine("Respuesta natural.")

    result = app.ask("Explícame una idea general")

    selection = result.data["metrics"]["tutor_selection"]
    assert result.ok is True
    assert result.message == "Respuesta natural."
    assert selection["task"] == "general_language"
    assert selection["tutor_id"] == "primary"
    assert selection["authority"] is False


def test_translation_model_fallback_uses_translation_task(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = EchoEngine("An unknown translated sentence.")

    result = app.translate("frase totalmente desconocida", "en")

    assert result.ok is True
    assert result.data["model_used"] is True
    assert result.data["metrics"]["tutor_selection"]["task"] == "translation"


def test_web_control_exposes_tutors_and_schema_35(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    service = ElyndraWebService(app)

    control = service.control_tutors()
    overview = service.control_overview()
    with app.database.connect() as connection:
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]

    assert control["status"]["authority_transferred"] is False
    assert "tutors" in overview
    assert schema == "50"
    assert __version__ == "0.8.10-alpha"
