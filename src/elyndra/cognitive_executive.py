from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from elyndra.db import Database
from elyndra.knowledge_acquisition import GeneralKnowledgeRepository
from elyndra.router import Route
from elyndra.skills import SkillRegistry

_GOAL_STATUSES = ("draft", "active", "blocked", "waiting", "completed", "cancelled")
_TASK_STATUSES = ("pending", "active", "blocked", "completed", "cancelled")
_VERIFICATION_STATUSES = ("success", "partial", "failed", "inconclusive")
_PRIORITIES = ("low", "normal", "high", "critical")
_ACTION_TERMS = {
    "analiza",
    "analizar",
    "ejecuta",
    "ejecutar",
    "revisa",
    "revisar",
    "verifica",
    "verificar",
    "crea",
    "crear",
    "modifica",
    "modificar",
    "envia",
    "enviar",
}
_DECISION_TERMS = {
    "decide",
    "decidir",
    "elige",
    "elegir",
    "conviene",
    "recomienda",
    "recomendar",
    "alternativa",
    "opcion",
    "opciones",
}
_PLAN_TERMS = {
    "plan",
    "planifica",
    "planificar",
    "organiza",
    "organizar",
    "objetivo",
    "meta",
    "rutina",
}
_DOMAIN_TERMS: dict[str, set[str]] = {
    "salud": {
        "salud",
        "medico",
        "medica",
        "dolor",
        "sintoma",
        "sintomas",
        "terapia",
        "psicologia",
        "ansiedad",
        "depresion",
    },
    "nutricion": {
        "nutricion",
        "comida",
        "alimento",
        "calorias",
        "proteina",
        "dieta",
        "peso",
        "macro",
        "macros",
    },
    "software": {
        "codigo",
        "proyecto",
        "python",
        "php",
        "java",
        "javascript",
        "servidor",
        "base",
        "datos",
    },
    "organizacion_personal": {
        "cita",
        "calendario",
        "cumpleanos",
        "recordatorio",
        "rutina",
        "tarea",
        "objetivo",
        "agenda",
    },
}


@dataclass(frozen=True, slots=True)
class ExecutiveAssessment:
    public_id: str
    request_sha256: str
    intent: str
    domain: str
    project: str
    goal: str
    risk: str
    candidate_routes: tuple[str, ...]
    selected_route: str
    confidence: dict[str, float]
    approval_required: bool
    verification_required: bool
    context_ids: tuple[str, ...]
    omitted_context_ids: tuple[str, ...]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "public_id": self.public_id,
            "request_sha256": self.request_sha256,
            "intent": self.intent,
            "domain": self.domain,
            "project": self.project,
            "goal": self.goal,
            "risk": self.risk,
            "candidate_routes": list(self.candidate_routes),
            "selected_route": self.selected_route,
            "confidence": dict(self.confidence),
            "approval_required": self.approval_required,
            "verification_required": self.verification_required,
            "context_ids": list(self.context_ids),
            "omitted_context_ids": list(self.omitted_context_ids),
            "created_at": self.created_at,
            "prompt_text_stored": False,
            "chain_of_thought_stored": False,
            "automatic_execution": False,
        }


class CognitiveExecutiveRepository:
    """Deterministic executive state, goals and outcome verification."""

    def __init__(
        self,
        database: Database,
        knowledge: GeneralKnowledgeRepository,
        skills: SkillRegistry,
    ) -> None:
        self.database = database
        self.knowledge = knowledge
        self.skills = skills

    def status(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            decisions = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_executive_decisions"
                ).fetchone()[0]
            )
            active_goals = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_goals WHERE status = 'active'"
                ).fetchone()[0]
            )
            pending_tasks = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_goal_tasks "
                    "WHERE status IN ('pending', 'active', 'blocked')"
                ).fetchone()[0]
            )
            verifications = int(
                connection.execute(
                    "SELECT COUNT(*) FROM assistant_outcome_verifications"
                ).fetchone()[0]
            )
        return {
            "enabled": True,
            "decisions": decisions,
            "active_goals": active_goals,
            "pending_tasks": pending_tasks,
            "verifications": verifications,
            "deterministic_assessment": True,
            "prompt_text_stored": False,
            "chain_of_thought_stored": False,
            "automatic_execution": False,
            "automatic_goal_progress": False,
            "single_use_approval": True,
            "context_budgeted": True,
            "multidimensional_confidence": True,
            "outcome_verification": True,
        }

    def assess(
        self,
        text: str,
        *,
        route: Route,
        domain: str = "",
        project: str = "",
    ) -> ExecutiveAssessment:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("La solicitud no puede estar vacía.")
        knowledge = self.knowledge.context_for_query(
            clean_text,
            domain=domain,
            project=project,
            min_relevance=0.25,
        )
        matches = list(knowledge.get("matches", []))
        inferred_domain = _bounded(domain, 100) or _infer_domain(clean_text, matches)
        inferred_project = _bounded(project, 160) or _infer_project(matches)
        intent = _intent(clean_text, route)
        candidate_routes = _candidate_routes(route, knowledge, intent)
        selected_route = _selected_route(route, knowledge, intent)
        risk = _risk(route, selected_route, self.skills)
        confidence = _confidence(selected_route, knowledge)
        approval_required = risk in {"medium", "high"} or selected_route in {
            "supervised_plan",
            "skill",
        }
        verification_required = selected_route in {
            "skill",
            "supervised_plan",
            "decision_support",
        }
        public_id = uuid.uuid4().hex
        created_at = _now()
        assessment = ExecutiveAssessment(
            public_id=public_id,
            request_sha256=hashlib.sha256(clean_text.encode()).hexdigest(),
            intent=intent,
            domain=inferred_domain,
            project=inferred_project,
            goal=_goal(intent, selected_route),
            risk=risk,
            candidate_routes=candidate_routes,
            selected_route=selected_route,
            confidence=confidence,
            approval_required=approval_required,
            verification_required=verification_required,
            context_ids=tuple(knowledge.get("knowledge_ids", [])),
            omitted_context_ids=tuple(knowledge.get("omitted_knowledge_ids", [])),
            created_at=created_at,
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_executive_decisions(
                    public_id, request_sha256, intent, domain, project,
                    goal_summary, risk, candidate_routes_json, planned_route,
                    actual_route, confidence_json, approval_required,
                    verification_required, context_ids_json,
                    omitted_context_ids_json, status, result_engine,
                    result_ok, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?,
                          'assessed', '', NULL, ?, NULL)
                """,
                (
                    assessment.public_id,
                    assessment.request_sha256,
                    assessment.intent,
                    assessment.domain,
                    assessment.project,
                    assessment.goal,
                    assessment.risk,
                    json.dumps(assessment.candidate_routes, ensure_ascii=False),
                    assessment.selected_route,
                    json.dumps(assessment.confidence, sort_keys=True),
                    int(assessment.approval_required),
                    int(assessment.verification_required),
                    json.dumps(assessment.context_ids, ensure_ascii=False),
                    json.dumps(assessment.omitted_context_ids, ensure_ascii=False),
                    assessment.created_at,
                ),
            )
        return assessment

    def complete(
        self,
        assessment: ExecutiveAssessment,
        *,
        ok: bool,
        actual_route: str,
        engine: str,
        context_ids: tuple[str, ...] = (),
        omitted_context_ids: tuple[str, ...] = (),
        status: str | None = None,
    ) -> dict[str, Any]:
        completed_at = _now()
        final_status = status or ("completed" if ok else "failed")
        if final_status not in {
            "completed",
            "failed",
            "blocked",
            "awaiting_approval",
            "preview",
        }:
            raise ValueError("Estado ejecutivo final inválido.")
        final_context_ids = context_ids or assessment.context_ids
        final_omitted_ids = omitted_context_ids or assessment.omitted_context_ids
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE assistant_executive_decisions SET actual_route = ?, "
                "context_ids_json = ?, omitted_context_ids_json = ?, status = ?, "
                "result_engine = ?, result_ok = ?, completed_at = ? "
                "WHERE public_id = ?",
                (
                    _bounded(actual_route, 80),
                    json.dumps(final_context_ids, ensure_ascii=False),
                    json.dumps(final_omitted_ids, ensure_ascii=False),
                    final_status,
                    _bounded(engine, 160),
                    int(ok),
                    completed_at,
                    assessment.public_id,
                ),
            )
        return {
            **assessment.to_dict(),
            "actual_route": actual_route,
            "result_engine": engine,
            "result_ok": ok,
            "status": final_status,
            "context_ids": list(final_context_ids),
            "omitted_context_ids": list(final_omitted_ids),
            "completed_at": completed_at,
        }

    def fail(self, assessment: ExecutiveAssessment, error: Exception) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE assistant_executive_decisions SET status = 'failed', "
                "actual_route = 'exception', result_engine = ?, result_ok = 0, "
                "completed_at = ? WHERE public_id = ?",
                (_bounded(type(error).__name__, 160), _now(), assessment.public_id),
            )

    def context_block(self, assessment: ExecutiveAssessment) -> str:
        confidence = assessment.confidence["decision_confidence"]
        return (
            "[EJECUTIVO COGNITIVO DE ELYNDRA]\n"
            f"Intención: {assessment.intent}. Ruta prevista: "
            f"{assessment.selected_route}. Riesgo: {assessment.risk}.\n"
            f"Objetivo operativo: {assessment.goal}. "
            f"Confianza conservadora: {confidence:.2f}.\n"
            "No concede herramientas, permisos ni autoridad. "
            "Expón incertidumbre cuando la evidencia no sea suficiente."
        )

    def list_decisions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assistant_executive_decisions "
                "ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [_decision_row(row) for row in rows]

    def decision_details(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_executive_decisions WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
        return _decision_row(row) if row else None

    def create_goal(
        self,
        *,
        title: str,
        description: str,
        domain: str,
        project: str,
        priority: str,
        target_date: str | None,
        next_action: str,
        actor: str,
    ) -> dict[str, Any]:
        now = _now()
        values = (
            uuid.uuid4().hex,
            _required(title, "título", 200),
            _bounded(description, 2_000),
            _bounded(domain, 100),
            _bounded(project, 160),
            _priority(priority),
            "active",
            _optional_date(target_date),
            _bounded(next_action, 500),
            _required(actor, "actor", 120),
            now,
            now,
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_goals(
                    public_id, title, description, domain, project, priority,
                    status, target_date, next_action, created_by, created_at,
                    updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                values,
            )
            row = connection.execute(
                "SELECT * FROM assistant_goals WHERE public_id = ?",
                (values[0],),
            ).fetchone()
        assert row is not None
        return dict(row)

    def update_goal(
        self,
        public_id: str,
        *,
        status: str | None,
        next_action: str | None,
    ) -> dict[str, Any]:
        goal = self.goal_details(public_id)
        if goal is None:
            raise ValueError("Objetivo no encontrado.")
        clean_status = str(goal["status"]) if status is None else _goal_status(status)
        clean_next = (
            str(goal["next_action"])
            if next_action is None
            else _bounded(next_action, 500)
        )
        completed_at = _now() if clean_status == "completed" else None
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE assistant_goals SET status = ?, next_action = ?, "
                "updated_at = ?, completed_at = ? WHERE public_id = ?",
                (clean_status, clean_next, _now(), completed_at, public_id.strip()),
            )
        updated = self.goal_details(public_id)
        assert updated is not None
        return updated

    def list_goals(
        self,
        *,
        status: str = "all",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clean = status.strip().casefold()
        if clean != "all" and clean not in _GOAL_STATUSES:
            raise ValueError("Estado de objetivo inválido.")
        where = "" if clean == "all" else "WHERE status = ?"
        params: list[Any] = [] if clean == "all" else [clean]
        params.append(max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM assistant_goals {where} "
                "ORDER BY updated_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def goal_details(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_goals WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
            if row is None:
                return None
            tasks = connection.execute(
                "SELECT * FROM assistant_goal_tasks WHERE goal_id = ? "
                "ORDER BY id ASC",
                (int(row["id"]),),
            ).fetchall()
        return {**dict(row), "tasks": [_task_row(item) for item in tasks]}

    def create_task(
        self,
        goal_public_id: str,
        *,
        title: str,
        priority: str,
        due_date: str | None,
        depends_on: tuple[str, ...],
        actor: str,
    ) -> dict[str, Any]:
        goal = self.goal_details(goal_public_id)
        if goal is None:
            raise ValueError("Objetivo no encontrado.")
        dependencies = tuple(dict.fromkeys(item.strip() for item in depends_on if item.strip()))
        if len(dependencies) > 12:
            raise ValueError("Una tarea admite como máximo doce dependencias.")
        if dependencies:
            with self.database.connect() as connection:
                rows = connection.execute(
                    "SELECT public_id, goal_id FROM assistant_goal_tasks "
                    "WHERE public_id IN ("
                    + ",".join("?" for _ in dependencies)
                    + ")",
                    dependencies,
                ).fetchall()
            if {str(row["public_id"]) for row in rows} != set(dependencies):
                raise ValueError("Una dependencia no existe.")
            if any(int(row["goal_id"]) != int(goal["id"]) for row in rows):
                raise ValueError("Las dependencias deben pertenecer al mismo objetivo.")
        now = _now()
        public_id = uuid.uuid4().hex
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_goal_tasks(
                    public_id, goal_id, title, status, priority,
                    dependency_ids_json, due_date, completion_evidence_json,
                    created_by, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, 'pending', ?, ?, ?, '{}', ?, ?, ?, NULL)
                """,
                (
                    public_id,
                    int(goal["id"]),
                    _required(title, "título", 240),
                    _priority(priority),
                    json.dumps(dependencies, ensure_ascii=False),
                    _optional_date(due_date),
                    _required(actor, "actor", 120),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM assistant_goal_tasks WHERE public_id = ?",
                (public_id,),
            ).fetchone()
        assert row is not None
        return _task_row(row)

    def complete_task(
        self,
        public_id: str,
        *,
        evidence: str,
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_goal_tasks WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
            if row is None:
                raise ValueError("Tarea no encontrada.")
            if str(row["status"]) in {"completed", "cancelled"}:
                raise ValueError("La tarea ya está cerrada.")
            dependencies = json.loads(str(row["dependency_ids_json"] or "[]"))
            if dependencies:
                placeholders = ",".join("?" for _ in dependencies)
                pending = connection.execute(
                    "SELECT COUNT(*) FROM assistant_goal_tasks "
                    f"WHERE public_id IN ({placeholders}) AND status != 'completed'",
                    dependencies,
                ).fetchone()[0]
                if int(pending):
                    raise ValueError("La tarea tiene dependencias sin completar.")
            now = _now()
            evidence_json = json.dumps(
                {"summary": _required(evidence, "evidencia", 1_000)},
                ensure_ascii=False,
            )
            connection.execute(
                "UPDATE assistant_goal_tasks SET status = 'completed', "
                "completion_evidence_json = ?, updated_at = ?, completed_at = ? "
                "WHERE id = ?",
                (evidence_json, now, now, int(row["id"])),
            )
            updated = connection.execute(
                "SELECT * FROM assistant_goal_tasks WHERE id = ?",
                (int(row["id"]),),
            ).fetchone()
        assert updated is not None
        return _task_row(updated)

    def record_verification(
        self,
        *,
        decision_public_id: str | None,
        expected_outcome: str,
        observed_outcome: str,
        method: str,
        status: str,
        evidence: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        clean_status = status.strip().casefold()
        if clean_status not in _VERIFICATION_STATUSES:
            raise ValueError("Estado de verificación inválido.")
        decision_id: int | None = None
        if decision_public_id:
            decision = self.decision_details(decision_public_id)
            if decision is None:
                raise ValueError("Decisión ejecutiva no encontrada.")
            decision_id = int(decision["id"])
        evidence_json = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        if len(evidence_json) > 4_000:
            raise ValueError("La evidencia estructurada supera 4000 caracteres.")
        public_id = uuid.uuid4().hex
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_outcome_verifications(
                    public_id, decision_id, expected_outcome, observed_outcome,
                    verification_method, status, evidence_json, created_by,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    public_id,
                    decision_id,
                    _required(expected_outcome, "resultado esperado", 1_000),
                    _required(observed_outcome, "resultado observado", 1_000),
                    _required(method, "método", 240),
                    clean_status,
                    evidence_json,
                    _required(actor, "actor", 120),
                    _now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM assistant_outcome_verifications "
                "WHERE public_id = ?",
                (public_id,),
            ).fetchone()
        assert row is not None
        return _verification_row(row)

    def list_verifications(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assistant_outcome_verifications "
                "ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [_verification_row(row) for row in rows]


def result_context_ids(data: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    knowledge = data.get("knowledge")
    if isinstance(knowledge, dict) and knowledge.get("public_id"):
        return (str(knowledge["public_id"]),), ()
    metrics = data.get("metrics")
    if not isinstance(metrics, dict):
        return (), ()
    selection = metrics.get("tutor_selection")
    if not isinstance(selection, dict):
        return (), ()
    general = tuple(str(item) for item in selection.get("general_knowledge_ids", []))
    durable = tuple(str(item) for item in selection.get("durable_knowledge_ids", []))
    omitted = tuple(str(item) for item in selection.get("omitted_knowledge_ids", []))
    return general + durable, omitted


def actual_route(data: dict[str, Any]) -> str:
    fast_path = str(data.get("fast_path") or "").strip()
    if fast_path:
        return fast_path
    engine = str(data.get("engine") or "").strip()
    if engine:
        return engine
    if data.get("skill_name"):
        return "skill"
    return "unknown"


def _candidate_routes(
    route: Route,
    knowledge: dict[str, Any],
    intent: str,
) -> tuple[str, ...]:
    values: list[str] = []
    if route.kind != "fallback":
        values.append(_route_name(route))
    if knowledge.get("knowledge_ids"):
        values.append("durable_knowledge")
    if intent in {"information", "decision_support", "planning", "conversation"}:
        values.extend(("alexandria", "memory", "language_model"))
    if intent in {"action", "planning"}:
        values.append("supervised_plan")
    return tuple(dict.fromkeys(values or ("language_model",)))


def _selected_route(
    route: Route,
    knowledge: dict[str, Any],
    intent: str,
) -> str:
    if route.kind != "fallback":
        return _route_name(route)
    matches = list(knowledge.get("matches", []))
    if matches:
        top = matches[0]
        if (
            float(top.get("relevance", 0.0)) >= 0.6
            and float(top.get("validation_confidence", 0.0)) >= 0.75
        ):
            return "durable_knowledge"
    if intent == "action":
        return "supervised_plan"
    if intent == "decision_support":
        return "decision_support"
    return "language_model"


def _route_name(route: Route) -> str:
    if route.kind == "skill":
        return "skill"
    return route.kind


def _intent(text: str, route: Route) -> str:
    tokens = _tokens(text)
    if route.kind == "skill":
        if route.skill_name == "memory.remember":
            return "memory_update"
        return "action"
    if route.kind == "clarification":
        return "clarification"
    if route.kind == "organizer":
        return "information"
    if tokens & _DECISION_TERMS:
        return "decision_support"
    if tokens & _PLAN_TERMS:
        return "planning"
    if tokens & _ACTION_TERMS:
        return "action"
    normalized = _normalize(text)
    if "?" in text or normalized.startswith(
        (
            "que ",
            "qué ",
            "como ",
            "cómo ",
            "por que ",
            "explica ",
            "explicame ",
            "dime ",
            "relaciona ",
            "compara ",
            "analiza ",
            "what ",
            "how ",
            "why ",
            "explain ",
        )
    ):
        return "information"
    return "conversation"


def _risk(route: Route, selected_route: str, skills: SkillRegistry) -> str:
    if route.kind == "skill" and route.skill_name:
        skill = skills.get(route.skill_name)
        if skill is not None:
            return str(skill.risk.value)
    if selected_route == "supervised_plan":
        return "medium"
    if selected_route in {"decision_support", "language_model"}:
        return "low"
    return "low"


def _confidence(selected_route: str, knowledge: dict[str, Any]) -> dict[str, float]:
    matches = list(knowledge.get("matches", []))
    top = matches[0] if matches else {}
    source = float(top.get("validation_confidence", 0.0))
    retrieval = float(top.get("relevance", 0.0))
    consistency = 0.5 if knowledge.get("conflicted_knowledge_ids") else 1.0
    freshness = 0.6 if knowledge.get("revalidation_due_ids") else 1.0
    if selected_route == "durable_knowledge":
        model = 0.0
        decision = min(source, retrieval, consistency, freshness)
    elif selected_route in {
        "skill",
        "clarification",
        "language_change",
        "organizer",
    }:
        model = 0.0
        decision = 0.95
    elif selected_route == "supervised_plan":
        model = 0.65
        decision = 0.7
    else:
        model = 0.55
        decision = 0.55
        if source and retrieval:
            decision = min(0.75, 0.4 * model + 0.3 * source + 0.3 * retrieval)
    return {
        "model_confidence": round(model, 4),
        "source_confidence": round(source, 4),
        "retrieval_confidence": round(retrieval, 4),
        "consistency_confidence": round(consistency, 4),
        "freshness_confidence": round(freshness, 4),
        "decision_confidence": round(max(0.0, min(1.0, decision)), 4),
    }


def _infer_domain(text: str, matches: list[dict[str, Any]]) -> str:
    exact_domains = {
        str(item.get("domain") or "").strip()
        for item in matches
        if str(item.get("domain") or "").strip()
        and float(item.get("relevance", 0.0)) >= 0.5
    }
    if len(exact_domains) == 1:
        return next(iter(exact_domains))
    tokens = _tokens(text)
    scores = {
        domain: len(tokens & terms)
        for domain, terms in _DOMAIN_TERMS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] else "general"


def _infer_project(matches: list[dict[str, Any]]) -> str:
    projects = {
        str(item.get("project") or "").strip()
        for item in matches
        if str(item.get("project") or "").strip()
        and float(item.get("relevance", 0.0)) >= 0.5
    }
    return next(iter(projects)) if len(projects) == 1 else ""


def _goal(intent: str, selected_route: str) -> str:
    goals = {
        "information": "Responder con evidencia y contexto relevante.",
        "decision_support": "Comparar alternativas y recomendar sin ejecutar.",
        "planning": "Proponer un plan revisable sin ejecutar automáticamente.",
        "action": "Preparar o ejecutar una acción bajo política y aprobación.",
        "memory_update": "Registrar memoria explícita bajo la política vigente.",
        "clarification": "Obtener el dato mínimo necesario para continuar.",
        "conversation": "Responder de forma útil y coherente con el contexto.",
    }
    return goals.get(intent, f"Resolver mediante la ruta {selected_route}.")


def _decision_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["candidate_routes"] = json.loads(str(item.pop("candidate_routes_json")))
    item["confidence"] = json.loads(str(item.pop("confidence_json")))
    item["context_ids"] = json.loads(str(item.pop("context_ids_json")))
    item["omitted_context_ids"] = json.loads(
        str(item.pop("omitted_context_ids_json"))
    )
    item["approval_required"] = bool(item["approval_required"])
    item["verification_required"] = bool(item["verification_required"])
    item["result_ok"] = (
        None if item["result_ok"] is None else bool(item["result_ok"])
    )
    item["prompt_text_stored"] = False
    item["chain_of_thought_stored"] = False
    return item


def _task_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["dependency_ids"] = json.loads(str(item.pop("dependency_ids_json")))
    item["completion_evidence"] = json.loads(
        str(item.pop("completion_evidence_json"))
    )
    return item


def _verification_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["evidence"] = json.loads(str(item.pop("evidence_json")))
    return item


def _goal_status(value: str) -> str:
    clean = value.strip().casefold()
    if clean not in _GOAL_STATUSES:
        raise ValueError("Estado de objetivo inválido.")
    return clean


def _priority(value: str) -> str:
    clean = value.strip().casefold()
    if clean not in _PRIORITIES:
        raise ValueError("Prioridad inválida.")
    return clean


def _optional_date(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    clean = value.strip()
    try:
        datetime.strptime(clean, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("La fecha debe usar YYYY-MM-DD.") from exc
    return clean


def _required(value: str, label: str, limit: int) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{label.capitalize()} no puede estar vacío.")
    if len(clean) > limit:
        raise ValueError(f"{label.capitalize()} excede {limit} caracteres.")
    return clean


def _bounded(value: str, limit: int) -> str:
    clean = value.strip()
    if len(clean) > limit:
        raise ValueError(f"El valor excede {limit} caracteres.")
    return clean


def _normalize(value: str) -> str:
    lowered = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(item for item in lowered if not unicodedata.combining(item)).split()
    )


def _tokens(value: str) -> set[str]:
    return {
        item
        for item in re.findall(r"[a-z0-9_áéíóúüñ]+", _normalize(value))
        if len(item) >= 2
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
