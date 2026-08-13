from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from elyndra.application import ElyndraApplication
from elyndra.engines.base import LanguageReply
from elyndra.paths import ElyndraPaths
from elyndra.personal_organizer import local_today
from elyndra.web.server import (
    ElyndraWebService,
    _handler_factory,
    _LocalThreadingHTTPServer,
)


class IntentTutorEngine:
    name = "intent-tutor"
    supports_vision = False

    def __init__(self, intent: str = "wellbeing.current") -> None:
        self.intent = intent
        self.calls: list[dict] = []

    def reply(
        self,
        prompt: str,
        *,
        context=(),
        history=(),
        response_language=None,
        keep_alive_seconds=0,
        images=(),
        max_tokens=None,
        on_token=None,
    ) -> LanguageReply:
        self.calls.append(
            {
                "prompt": prompt,
                "context": context,
                "history": history,
                "response_language": response_language,
            }
        )
        payload = {
            "intent": self.intent,
            "confidence": 0.91,
            "entities": {"period": "today", "metric": "mood"},
            "alternatives": [],
            "clarification": "",
        }
        return LanguageReply(json.dumps(payload), self.name, True, {})

    def release(self) -> None:
        return None


def _checkin_today(app: ElyndraApplication) -> dict:
    return app.wellbeing.create_checkin(
        checkin_date=date.today().isoformat(),
        mood=3,
        energy=4,
        stress=2,
        focus=4,
        sleep_hours=7,
        sleep_quality=4,
        hydration=3,
        nutrition=3,
        activity_minutes=30,
        note="Registro local.",
        actor="owner",
    )


def _commitment_tomorrow(app: ElyndraApplication) -> dict:
    target = local_today("America/Santiago") + timedelta(days=1)
    return app.personal_organizer.create_commitment(
        title="Revisión semántica",
        description="",
        event_date=target.isoformat(),
        event_time="09:30",
        timezone="America/Santiago",
        domain="organizacion_personal",
        project="",
        priority="normal",
        recurrence="once",
        interval=1,
        weekdays=(),
        until=None,
        goal_public_id="",
        task_public_id="",
        actor="owner",
    )


def _post_json(base: str, token: str, path: str, payload: dict) -> tuple[int, dict]:
    request = Request(
        f"{base}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Elyndra-Token": token},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_natural_wellbeing_variants_use_local_data_without_ollama(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _checkin_today(app)

    mood = app.ask("como esta mi animo hoy")
    checkin = app.ask("como esta el check de bienestar")
    short = app.ask("como ando hoy")

    for reply in (mood, checkin, short):
        assert reply.ok is True
        assert reply.data["engine"] == "local-personal-wellbeing"
        assert reply.data["model_used"] is False
        assert reply.data["semantic"]["intent"] == "wellbeing.current"
    assert "Ánimo: 3.0/5" in mood.message
    assert "Energía: 4.0/5" in checkin.message
    assert "Estrés: 2.0/5" in checkin.message


def test_natural_organizer_and_ambiguous_request_are_not_generic_model_replies(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _commitment_tomorrow(app)

    tomorrow = app.ask("que toca mañana")
    ambiguous = app.ask("como voy")

    assert tomorrow.data["engine"] == "local-personal-organizer"
    assert tomorrow.data["model_used"] is False
    assert "Revisión semántica" in tomorrow.message
    assert ambiguous.data["engine"] == "local-semantic-clarification"
    assert ambiguous.data["clarification_required"] is True
    assert "¿Quieres revisar" in ambiguous.message


def test_tutor_only_interprets_uncertain_intent_and_never_receives_tools(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _checkin_today(app)
    tutor = IntentTutorEngine()
    app.language_engine = tutor

    reply = app.ask("que onda con mi registro vital reciente")

    assert reply.data["engine"] == "local-personal-wellbeing"
    assert reply.data["semantic"]["source"] == "tutor_assisted"
    assert reply.data["semantic"]["tutor_used"] is True
    assert "Ánimo: 3.0/5" in reply.message
    assert len(tutor.calls) == 1
    call = tutor.calls[0]
    assert call["history"] == ()
    assert "No tienes herramientas, SQLite, memoria, permisos ni autoridad" in call[
        "context"
    ][-1]
    assert "Registro local" not in call["prompt"]
    assert "Registro local" not in "\n".join(call["context"])


def test_reviewed_language_learning_is_pending_until_owner_approval(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    phrase = "muéstrame mi termómetro emocional"
    tutor = IntentTutorEngine()

    before = app.semantic_intents.resolve(
        phrase,
        tutor_engine=tutor,
        response_language="es",
    )
    proposal = app.semantic_intents.propose_learning(
        phrase=phrase,
        intent="wellbeing.current",
        source="owner_correction",
        actor="owner",
    )
    pending = app.semantic_intents.status()
    reviewed = app.semantic_intents.review_learning(
        proposal["public_id"], decision="approve", actor="owner"
    )
    local = app.semantic_intents.resolve(
        phrase,
        tutor_engine=None,
        response_language="es",
    )

    assert before is not None and before.tutor_used is True
    assert proposal["status"] == "pending"
    assert pending["reviewed_examples"] == 0
    assert reviewed["status"] == "approved"
    assert local is not None
    assert local.intent == "wellbeing.current"
    assert local.source == "semantic_local"
    assert local.tutor_used is False
    assert app.semantic_intents.status()["silent_learning"] is False


def test_repeated_tutor_resolution_only_creates_pending_learning_proposal(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    phrase = "que onda con mi registro vital recurrente"
    tutor = IntentTutorEngine()

    for _ in range(3):
        result = app.semantic_intents.resolve(
            phrase,
            tutor_engine=tutor,
            response_language="es",
        )
        assert result is not None and result.intent == "wellbeing.current"

    proposals = app.semantic_intents.list_proposals(status="pending")
    assert len(proposals) == 1
    assert proposals[0]["intent"] == "wellbeing.current"
    assert app.semantic_intents.status()["reviewed_examples"] == 0


def test_web_and_cli_share_semantic_runtime_and_review_approval(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _checkin_today(app)
    service = ElyndraWebService(app)
    chat = service.create_chat(title="Semántica", transcript_mode="full")
    direct = app.ask("como esta mi animo hoy")
    web = service.send_message(chat["chat"]["id"], "como esta mi animo hoy")

    assert web["message"] == direct.message
    assert web["meta"]["engine"] == "local-personal-wellbeing"
    assert web["meta"]["semantic"]["intent"] == "wellbeing.current"
    assert web["meta"]["shared_application_runtime"] is True

    token = "semantic-parity-token"
    server = _LocalThreadingHTTPServer(
        ("127.0.0.1", 0), _handler_factory(service, token)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        code, denied = _post_json(
            base,
            token,
            "/api/personal/intents/proposals",
            {
                "phrase": "revisa mi batería humana",
                "intent": "wellbeing.current",
            },
        )
        assert code == 400
        assert "confirmación explícita" in denied["error"]

        code, created = _post_json(
            base,
            token,
            "/api/personal/intents/proposals",
            {
                "phrase": "revisa mi batería humana",
                "intent": "wellbeing.current",
                "source": "owner_correction",
                "approved": True,
            },
        )
        assert code == 201
        proposal_id = created["item"]["public_id"]
        assert created["item"]["status"] == "pending"

        code, approved = _post_json(
            base,
            token,
            "/api/personal/intents/proposals/review",
            {"proposal_id": proposal_id, "decision": "approve", "approved": True},
        )
        assert code == 200
        assert approved["item"]["status"] == "approved"

        with urlopen(f"{base}/personal", timeout=5) as response:
            page = response.read().decode()
        assert 'id="personal-intent-status"' in page
        assert 'id="personal-intent-propose-form"' in page
        assert 'id="personal-intent-review-form"' in page
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()


def test_semantic_resolution_persists_hashes_not_raw_messages(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    secret_phrase = "como esta mi animo hoy codigo privado 8472"

    result = app.semantic_intents.resolve(
        secret_phrase,
        tutor_engine=None,
        response_language="es",
    )

    assert result is not None
    with app.database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM assistant_intent_resolutions ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert "codigo privado" not in json.dumps(dict(row), ensure_ascii=False)
    assert len(str(row["message_sha256"])) == 64
