from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

_SESSION_REFERENCE = re.compile(
    r"\b(?:sesion|sesión|session)(?:\s+de\s+desarrollo)?(?:\s+id)?"
    r"\s*[:#-]?\s*([0-9a-f]{32})\b",
    re.IGNORECASE,
)

_GUIDANCE_PHRASES = (
    "que sigue",
    "que continua",
    "como seguimos",
    "siguiente paso",
    "proximo paso",
    "acciones disponibles",
    "que puedo hacer ahora",
    "donde quedamos",
    "retomar la sesion",
    "retomar sesion",
    "continuar la sesion",
    "continuar sesion",
    "estado de la sesion",
    "estado actual de la sesion",
    "what next",
    "next step",
    "available actions",
    "resume the session",
    "resume session",
    "continue the session",
    "session status",
    "where did we leave off",
)


@dataclass(frozen=True, slots=True)
class SessionSuggestedAction:
    kind: str
    label: str
    command: str
    requires_approval: bool
    entity_type: str
    entity_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "command": self.command,
            "requires_approval": self.requires_approval,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
        }


@dataclass(frozen=True, slots=True)
class DevelopmentSessionGuidance:
    session_id: str
    status: str
    project_root: str
    objective: str
    current_change_proposal_id: str
    current_validation_cycle_id: str | None
    last_event_type: str
    last_event_status: str
    last_event_summary: str
    actions: tuple[SessionSuggestedAction, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "project_root": self.project_root,
            "objective": self.objective,
            "current_change_proposal_id": self.current_change_proposal_id,
            "current_validation_cycle_id": self.current_validation_cycle_id,
            "last_event_type": self.last_event_type,
            "last_event_status": self.last_event_status,
            "last_event_summary": self.last_event_summary,
            "actions": [item.to_dict() for item in self.actions],
        }


def extract_session_reference(text: str) -> str | None:
    match = _SESSION_REFERENCE.search(text)
    return match.group(1).lower() if match is not None else None


def asks_for_session_guidance(text: str, *, session_available: bool) -> bool:
    if extract_session_reference(text) is not None:
        return True
    if not session_available:
        return False
    normalized = _normalized(text)
    return any(phrase in normalized for phrase in _GUIDANCE_PHRASES)


def build_session_guidance(session: dict[str, Any]) -> DevelopmentSessionGuidance:
    session_id = str(session.get("public_id", ""))
    status = str(session.get("status", "active"))
    project_root = str(session.get("project_root", ""))
    objective = str(session.get("objective", ""))
    change_id = str(session.get("current_change_proposal_id", ""))
    cycle_raw = session.get("current_validation_cycle_id")
    cycle_id = str(cycle_raw) if cycle_raw else None
    events = session.get("events", []) or []
    last_event = events[-1] if events else {}
    event_type = str(last_event.get("event_type", "change_proposed"))
    event_status = str(last_event.get("status", status))
    event_summary = str(last_event.get("summary", ""))
    actions = _actions_for_state(
        session_id=session_id,
        status=status,
        project_root=project_root,
        change_id=change_id,
        cycle_id=cycle_id,
        event_type=event_type,
    )
    return DevelopmentSessionGuidance(
        session_id=session_id,
        status=status,
        project_root=project_root,
        objective=objective,
        current_change_proposal_id=change_id,
        current_validation_cycle_id=cycle_id,
        last_event_type=event_type,
        last_event_status=event_status,
        last_event_summary=event_summary,
        actions=actions,
    )


def render_session_guidance(
    guidance: DevelopmentSessionGuidance,
    *,
    language: str = "es",
) -> str:
    if language.lower().startswith("en"):
        lines = [
            f"Development session {guidance.session_id}",
            f"Status: {guidance.status}",
            f"Project: {guidance.project_root}",
            f"Objective: {guidance.objective}",
            f"Last event: {guidance.last_event_type} — {guidance.last_event_summary}",
            "",
            "Available next actions (nothing was executed):",
        ]
    else:
        lines = [
            f"Sesión de desarrollo {guidance.session_id}",
            f"Estado: {guidance.status}",
            f"Proyecto: {guidance.project_root}",
            f"Objetivo: {guidance.objective}",
            f"Último evento: {guidance.last_event_type} — {guidance.last_event_summary}",
            "",
            "Siguientes acciones posibles (no se ejecutó ninguna):",
        ]
    if not guidance.actions:
        lines.append(
            "- No additional action is available for this closed session."
            if language.lower().startswith("en")
            else "- No hay una acción adicional disponible para esta sesión cerrada."
        )
    for index, action in enumerate(guidance.actions, start=1):
        approval = " · requires approval" if action.requires_approval else ""
        if not language.lower().startswith("en"):
            approval = " · requiere aprobación" if action.requires_approval else ""
        lines.append(f"{index}. {action.label}{approval}\n   `{action.command}`")
    return "\n".join(lines)


def session_context_block(guidance: DevelopmentSessionGuidance) -> str:
    actions = "\n".join(
        f"- {item.label}: {item.command}" for item in guidance.actions[:4]
    )
    if not actions:
        actions = "- No hay acciones pendientes."
    return (
        "[CONTEXTO LOCAL DE SESIÓN DE DESARROLLO — NO ES AUTORIZACIÓN]\n"
        f"ID: {guidance.session_id}\n"
        f"Estado: {guidance.status}\n"
        f"Proyecto: {guidance.project_root}\n"
        f"Objetivo: {guidance.objective}\n"
        f"Último evento real: {guidance.last_event_type} — "
        f"{guidance.last_event_summary}\n"
        "Acciones supervisadas disponibles:\n"
        f"{actions}\n"
        "No afirmes que una acción fue ejecutada salvo que aparezca como evento real. "
        "No inventes IDs, resultados, cambios ni aprobaciones."
    )


def _actions_for_state(
    *,
    session_id: str,
    status: str,
    project_root: str,
    change_id: str,
    cycle_id: str | None,
    event_type: str,
) -> tuple[SessionSuggestedAction, ...]:
    if status == "closed" or event_type == "session_closed":
        return ()
    if event_type in {"change_proposed", "repair_proposed"}:
        return (
            _action(
                "review_change",
                "Revisar el diff exacto",
                f"./scripts/elyndra-dev assistant change-show {change_id}",
                False,
                "change_proposal",
                change_id,
            ),
            _action(
                "apply_change",
                "Aplicar la propuesta revisada",
                f"./scripts/elyndra-dev assistant change-apply {change_id} --approve",
                True,
                "change_proposal",
                change_id,
            ),
            _action(
                "reject_change",
                "Rechazar la propuesta",
                f"./scripts/elyndra-dev assistant change-reject {change_id} --approve",
                True,
                "change_proposal",
                change_id,
            ),
        )
    if event_type == "change_applied":
        request = f"Ejecuta las validaciones adecuadas en {project_root}"
        return (
            _action(
                "propose_validation",
                "Crear un plan de validación revisable",
                (
                    "./scripts/elyndra-dev assistant validate-plan "
                    f"{change_id} --request '{request}'"
                ),
                False,
                "change_proposal",
                change_id,
            ),
            _session_show_action(session_id),
        )
    if event_type == "validation_proposed" and cycle_id:
        return (
            _action(
                "review_validation",
                "Revisar el plan de validación",
                f"./scripts/elyndra-dev assistant cycle-show {cycle_id}",
                False,
                "validation_cycle",
                cycle_id,
            ),
            _action(
                "run_validation",
                "Ejecutar el plan aprobado",
                f"./scripts/elyndra-dev assistant validate-run {cycle_id} --approve",
                True,
                "validation_cycle",
                cycle_id,
            ),
        )
    if event_type in {"validation_failed", "validation_partial"} and cycle_id:
        return (
            _action(
                "review_validation",
                "Revisar los resultados reales",
                f"./scripts/elyndra-dev assistant cycle-show {cycle_id}",
                False,
                "validation_cycle",
                cycle_id,
            ),
            _action(
                "propose_repair",
                "Proponer una reparación limitada a los fallos reales",
                (
                    "./scripts/elyndra-dev assistant repair-plan "
                    f"{cycle_id} --instruction 'Corrige únicamente los fallos reales observados'"
                ),
                False,
                "validation_cycle",
                cycle_id,
            ),
            _session_close_action(session_id),
        )
    if event_type == "validation_passed" or status == "completed":
        actions: list[SessionSuggestedAction] = []
        if cycle_id:
            actions.append(
                _action(
                    "review_validation",
                    "Revisar la validación aprobada",
                    f"./scripts/elyndra-dev assistant cycle-show {cycle_id}",
                    False,
                    "validation_cycle",
                    cycle_id,
                )
            )
        actions.append(_session_close_action(session_id))
        return tuple(actions)
    if event_type in {"change_rejected", "change_stale", "change_failed"}:
        return (_session_show_action(session_id), _session_close_action(session_id))
    return (_session_show_action(session_id),)


def _action(
    kind: str,
    label: str,
    command: str,
    requires_approval: bool,
    entity_type: str,
    entity_id: str,
) -> SessionSuggestedAction:
    return SessionSuggestedAction(
        kind=kind,
        label=label,
        command=command,
        requires_approval=requires_approval,
        entity_type=entity_type,
        entity_id=entity_id,
    )


def _session_show_action(session_id: str) -> SessionSuggestedAction:
    return _action(
        "show_session",
        "Revisar la línea de tiempo de la sesión",
        f"./scripts/elyndra-dev assistant session-show {session_id}",
        False,
        "development_session",
        session_id,
    )


def _session_close_action(session_id: str) -> SessionSuggestedAction:
    return _action(
        "close_session",
        "Cerrar explícitamente la sesión",
        f"./scripts/elyndra-dev assistant session-close {session_id} --approve",
        True,
        "development_session",
        session_id,
    )


def _normalized(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.split())
