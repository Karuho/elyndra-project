from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyndra.db import Database
from elyndra.engines import LanguageEngine, NoModelEngine
from elyndra.ethics import constitutional_context_block
from elyndra.router import DeterministicRouter, Route
from elyndra.skills import SkillRegistry

_MAX_PLAN_STEPS = 4
_MAX_RESULT_CHARS = 12_000
_MAX_STEP_MESSAGE_CHARS = 3_000

# 0.7.17-alpha intentionally starts with project inspection and validation only.
# No memory writes, imports, file writes, package installation or arbitrary commands.
_ALLOWED_SKILL_PARAMS: dict[str, frozenset[str]] = {
    "python.project_inspect": frozenset({"path"}),
    "python.pyproject_validate": frozenset({"path"}),
    "python.compile_project": frozenset({"path"}),
    "ruff.check": frozenset({"path"}),
    "mypy.check": frozenset({"path"}),
    "pytest.run": frozenset({"path"}),
    "python.verify_project": frozenset({"path"}),
    "php.project_inspect": frozenset({"path"}),
    "php.syntax_scan": frozenset({"path"}),
    "composer.validate": frozenset({"path"}),
    "phpstan.analyse": frozenset({"path"}),
    "phpunit.run": frozenset({"path"}),
    "php.verify_project": frozenset({"path"}),
    "web.project_inspect": frozenset({"path"}),
    "html.validate": frozenset({"path"}),
    "css.validate": frozenset({"path"}),
    "javascript.syntax_validate": frozenset({"path"}),
    "typescript.check": frozenset({"path"}),
    "web.framework_validate": frozenset({"path"}),
    "eslint.lint": frozenset({"path"}),
    "stylelint.lint": frozenset({"path"}),
    "web.verify_project": frozenset({"path"}),
    "java.project_inspect": frozenset({"path"}),
    "java.descriptor_validate": frozenset({"path"}),
    "java.javac_compile": frozenset({"path"}),
    "java.build_project": frozenset({"path"}),
    "java.test_project": frozenset({"path"}),
    "java.verify_project": frozenset({"path"}),
    "kotlin.project_inspect": frozenset({"path"}),
    "kotlin.descriptor_validate": frozenset({"path"}),
    "kotlin.kotlinc_compile": frozenset({"path"}),
    "kotlin.build_project": frozenset({"path"}),
    "kotlin.test_project": frozenset({"path"}),
    "kotlin.verify_project": frozenset({"path"}),
    "dotnet.project_inspect": frozenset({"path"}),
    "dotnet.descriptor_validate": frozenset({"path"}),
    "dotnet.format_check": frozenset({"path"}),
    "dotnet.build_project": frozenset({"path"}),
    "dotnet.test_project": frozenset({"path"}),
    "dotnet.verify_project": frozenset({"path"}),
    "native.project_inspect": frozenset({"path"}),
    "native.descriptor_validate": frozenset({"path"}),
    "native.c_syntax_check": frozenset({"path"}),
    "native.cpp_syntax_check": frozenset({"path"}),
    "native.static_analyse": frozenset({"path"}),
    "native.build_project": frozenset({"path"}),
    "native.test_project": frozenset({"path"}),
    "native.verify_project": frozenset({"path"}),
    "ruby.project_inspect": frozenset({"path"}),
    "ruby.descriptor_validate": frozenset({"path"}),
    "ruby.bundle_check": frozenset({"path"}),
    "ruby.syntax_check": frozenset({"path"}),
    "rubocop.check": frozenset({"path"}),
    "ruby.test_project": frozenset({"path"}),
    "ruby.verify_project": frozenset({"path"}),
    "go.project_inspect": frozenset({"path"}),
    "go.module_validate": frozenset({"path"}),
    "gofmt.check": frozenset({"path"}),
    "go.vet": frozenset({"path"}),
    "go.build_project": frozenset({"path"}),
    "go.test_project": frozenset({"path"}),
    "go.verify_project": frozenset({"path"}),
    "rust.project_inspect": frozenset({"path"}),
    "rust.manifest_validate": frozenset({"path"}),
    "rustfmt.check": frozenset({"path"}),
    "cargo.check": frozenset({"path"}),
    "cargo.clippy": frozenset({"path"}),
    "cargo.test_project": frozenset({"path"}),
    "rust.verify_project": frozenset({"path"}),
    "swift.project_inspect": frozenset({"path"}),
    "swift.manifest_validate": frozenset({"path"}),
    "swift.syntax_check": frozenset({"path"}),
    "swift.format_check": frozenset({"path"}),
    "swift.build_project": frozenset({"path"}),
    "swift.test_project": frozenset({"path"}),
    "swift.verify_project": frozenset({"path"}),
    "dart.project_inspect": frozenset({"path"}),
    "dart.descriptor_validate": frozenset({"path"}),
    "dart.format_check": frozenset({"path"}),
    "dart.analyze": frozenset({"path"}),
    "dart.test_project": frozenset({"path"}),
    "flutter.test_project": frozenset({"path"}),
    "dart.verify_project": frozenset({"path"}),
    "sql.project_inspect": frozenset({"path"}),
    "sql.static_validate": frozenset({"path"}),
    "sql.migration_validate": frozenset({"path"}),
    "sqlite.schema_inspect": frozenset({"path"}),
    "sql.verify_project": frozenset({"path"}),
}

_VERIFY_SKILLS = {
    "python": "python.verify_project",
    "php": "php.verify_project",
    "web": "web.verify_project",
    "frontend": "web.verify_project",
    "javascript": "web.verify_project",
    "typescript": "web.verify_project",
    "java": "java.verify_project",
    "kotlin": "kotlin.verify_project",
    "dotnet": "dotnet.verify_project",
    ".net": "dotnet.verify_project",
    "c#": "dotnet.verify_project",
    "csharp": "dotnet.verify_project",
    "c": "native.verify_project",
    "c++": "native.verify_project",
    "cpp": "native.verify_project",
    "ruby": "ruby.verify_project",
    "go": "go.verify_project",
    "golang": "go.verify_project",
    "rust": "rust.verify_project",
    "swift": "swift.verify_project",
    "dart": "dart.verify_project",
    "flutter": "dart.verify_project",
    "sql": "sql.verify_project",
    "sqlite": "sql.verify_project",
}

_ACTION_TERMS = (
    "analiza",
    "analizar",
    "inspecciona",
    "inspeccionar",
    "revisa",
    "revisar",
    "verifica",
    "verificar",
    "valida",
    "validar",
    "ejecuta",
    "ejecutar",
    "comprueba",
    "comprobar",
)

_EXPLANATION_TERMS = (
    "explica",
    "explicame",
    "explícame",
    "informe",
    "resumen",
    "que esta mal",
    "qué está mal",
    "problemas",
    "deberia corregir",
    "debería corregir",
    "recomendaciones",
    "conclusiones",
)


@dataclass(frozen=True, slots=True)
class ActionStep:
    skill_name: str
    params: dict[str, Any]
    purpose: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "params": dict(self.params),
            "purpose": self.purpose,
        }


@dataclass(frozen=True, slots=True)
class ActionPlan:
    plan_id: str
    request: str
    source: str
    summary: str
    steps: tuple[ActionStep, ...]
    fail_fast: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "request": self.request,
            "source": self.source,
            "summary": self.summary,
            "fail_fast": self.fail_fast,
            "steps": [step.to_dict() for step in self.steps],
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        registry: SkillRegistry,
        expected_request: str | None = None,
    ) -> ActionPlan:
        if not isinstance(payload, dict):
            raise ValueError("El plan aprobado debe ser un objeto JSON.")
        request = str(payload.get("request", "")).strip()
        if expected_request is not None and request != expected_request.strip():
            raise ValueError("El plan aprobado no corresponde al texto original.")
        source = str(payload.get("source", "approved")).strip()[:40] or "approved"
        summary = str(payload.get("summary", "")).strip()[:1000]
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list):
            raise ValueError("El plan aprobado no contiene pasos válidos.")
        steps = _validate_steps(raw_steps, registry=registry)
        fail_fast = bool(payload.get("fail_fast", True))
        calculated_id = _plan_id(request, steps, fail_fast=fail_fast)
        supplied_id = str(payload.get("plan_id", "")).strip()
        if supplied_id and supplied_id != calculated_id:
            raise ValueError("El identificador del plan aprobado no coincide con sus pasos.")
        return cls(
            plan_id=calculated_id,
            request=request,
            source=source,
            summary=summary or _default_summary(steps),
            steps=steps,
            fail_fast=fail_fast,
        )


class AssistantActionPlanner:
    """Propose bounded action plans without granting the model any tools."""

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        router: DeterministicRouter,
        language_engine: LanguageEngine,
    ) -> None:
        self.registry = registry
        self.router = router
        self.language_engine = language_engine

    @property
    def allowed_skills(self) -> tuple[str, ...]:
        return tuple(sorted(_ALLOWED_SKILL_PARAMS))

    def should_plan(self, text: str, route: Route | None = None) -> bool:
        clean = " ".join(text.casefold().split())
        if not any(term in clean for term in _ACTION_TERMS):
            return False
        if _extract_path(text) is None:
            return False
        routed = route or self.router.route(text)
        if routed.kind == "skill":
            return (
                routed.skill_name in _ALLOWED_SKILL_PARAMS
                and any(term in clean for term in _EXPLANATION_TERMS)
            )
        return True

    def propose(
        self,
        text: str,
        route: Route | None = None,
        *,
        force: bool = False,
    ) -> ActionPlan | None:
        clean_text = text.strip()
        if not clean_text:
            return None
        routed = route or self.router.route(clean_text)
        if not force and not self.should_plan(clean_text, routed):
            return None

        deterministic = self._deterministic_plan(clean_text, routed)
        if deterministic is not None:
            return deterministic
        if isinstance(self.language_engine, NoModelEngine):
            return None
        return self._model_plan(clean_text)

    def _deterministic_plan(self, text: str, route: Route) -> ActionPlan | None:
        path = _extract_path(text)
        if path is None:
            return None
        normalized = " ".join(text.casefold().split())
        requested_tools = _specific_tool_steps(normalized, path)
        if requested_tools:
            return _build_plan(text, "deterministic-tools", requested_tools)

        if route.kind == "skill" and route.skill_name in _ALLOWED_SKILL_PARAMS:
            params = dict(route.params)
            if "path" in _ALLOWED_SKILL_PARAMS[route.skill_name]:
                params["path"] = path
            step = ActionStep(
                skill_name=route.skill_name,
                params=params,
                purpose="Ejecutar la inspección o validación solicitada.",
            )
            return _build_plan(text, "deterministic-router", (step,))

        for term, skill_name in _VERIFY_SKILLS.items():
            if _contains_term(normalized, term):
                step = ActionStep(
                    skill_name=skill_name,
                    params={"path": path},
                    purpose=f"Verificar el proyecto {term} con su toolchain controlada.",
                )
                return _build_plan(text, "deterministic-toolchain", (step,))

        return None

    def _model_plan(self, text: str) -> ActionPlan | None:
        path = _extract_path(text)
        if path is None:
            return None
        allowed = "\n".join(f"- {name}" for name in self.allowed_skills)
        prompt = (
            constitutional_context_block(
                owner_name="propietario local verificado",
                proactive_advice=True,
            )
            + "\n\n"
            + "Actúa solo como planificador JSON para Elyndra. No ejecutes herramientas. "
            "Propón entre 1 y 4 pasos de inspección o validación, exclusivamente con las "
            "skills permitidas. Todos los parámetros path deben ser exactamente la ruta "
            f"proporcionada: {path}\n\n"
            "Devuelve únicamente un objeto JSON con esta forma: "
            '{"summary":"...","steps":[{"skill":"...","params":{"path":"..."},'
            '"purpose":"..."}]}\n\n'
            f"Skills permitidas:\n{allowed}\n\nSolicitud del propietario:\n{text}"
        )
        try:
            reply = self.language_engine.reply(
                prompt,
                response_language="es",
                keep_alive_seconds=0,
                max_tokens=500,
            )
            payload = _extract_json_object(reply.text)
            raw_steps = payload.get("steps")
            if not isinstance(raw_steps, list):
                return None
            normalized_steps: list[dict[str, Any]] = []
            for item in raw_steps:
                if not isinstance(item, dict):
                    raise ValueError("Cada paso propuesto debe ser un objeto.")
                params = item.get("params", {})
                if not isinstance(params, dict):
                    raise ValueError("Los parámetros de un paso deben ser un objeto.")
                normalized_params = dict(params)
                if "path" in normalized_params:
                    if str(normalized_params["path"]).strip() != path:
                        raise ValueError("El modelo intentó cambiar la ruta autorizable.")
                    normalized_params["path"] = path
                normalized_steps.append(
                    {
                        "skill_name": str(item.get("skill", "")),
                        "params": normalized_params,
                        "purpose": str(item.get("purpose", "")),
                    }
                )
            steps = _validate_steps(normalized_steps, registry=self.registry)
        except (RuntimeError, ValueError):
            return None
        return _build_plan(
            text,
            "language-model-proposal",
            steps,
            summary=str(payload.get("summary", "")).strip()[:1000],
        )


class ActionPlanRunRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def save_preview(
        self,
        *,
        plan: ActionPlan,
        actor: str,
        chat_id: str | None = None,
    ) -> str:
        public_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_action_runs(
                    public_id, plan_id, chat_id, source, status, actor,
                    plan_json, result_json, started_at, completed_at, duration_ms
                ) VALUES (?, ?, ?, ?, 'planned', ?, ?, '{}', ?, NULL, NULL)
                """,
                (
                    public_id,
                    plan.plan_id,
                    chat_id,
                    plan.source,
                    actor,
                    json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
        return public_id

    def start(
        self,
        *,
        plan: ActionPlan,
        actor: str,
        chat_id: str | None,
        preview_id: str | None = None,
    ) -> str:
        now = datetime.now(UTC).isoformat()
        if preview_id is not None:
            clean_id = preview_id.strip()
            with self.database.connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE assistant_action_runs
                    SET status = 'running', started_at = ?, completed_at = NULL,
                        duration_ms = NULL
                    WHERE public_id = ? AND plan_id = ? AND actor = ?
                      AND status = 'planned'
                    """,
                    (now, clean_id, plan.plan_id, actor),
                )
            if cursor.rowcount != 1:
                raise ValueError(
                    "El plan guardado no existe, ya fue utilizado o no coincide."
                )
            return clean_id

        public_id = uuid.uuid4().hex
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO assistant_action_runs(
                    public_id, plan_id, chat_id, source, status, actor,
                    plan_json, result_json, started_at, completed_at, duration_ms
                ) VALUES (?, ?, ?, ?, 'running', ?, ?, '{}', ?, NULL, NULL)
                """,
                (
                    public_id,
                    plan.plan_id,
                    chat_id,
                    plan.source,
                    actor,
                    json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
        return public_id

    def complete(
        self,
        public_id: str,
        *,
        status: str,
        result: dict[str, Any],
        duration_ms: int,
    ) -> dict[str, Any]:
        clean_status = status if status in {"passed", "failed", "partial"} else "failed"
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE assistant_action_runs
                SET status = ?, result_json = ?, completed_at = ?, duration_ms = ?
                WHERE public_id = ? AND status = 'running'
                """,
                (
                    clean_status,
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                    max(0, int(duration_ms)),
                    public_id,
                ),
            )
        if cursor.rowcount != 1:
            raise RuntimeError("La ejecución del plan no estaba activa.")
        item = self.get(public_id)
        if item is None:
            raise RuntimeError("No se pudo recuperar la ejecución del plan.")
        return item

    def get(self, public_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assistant_action_runs WHERE public_id = ?",
                (public_id.strip(),),
            ).fetchone()
        return _public_action_run(dict(row)) if row is not None else None

    def list_recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM assistant_action_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [_public_action_run(dict(row)) for row in rows]

    def count(self) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM assistant_action_runs"
            ).fetchone()
        return int(row[0])


def approval_summary(plan: ActionPlan) -> str:
    lines = [
        f"Plan supervisado {plan.plan_id}: {plan.summary}",
        "",
        "Pasos exactos:",
    ]
    for index, step in enumerate(plan.steps, start=1):
        target = step.params.get("path") or step.params.get("name") or ""
        suffix = f" · {target}" if target else ""
        lines.append(f"{index}. {step.skill_name}{suffix}")
    lines.extend(
        (
            "",
            "La aprobación autoriza este plan una sola vez. No permite escribir archivos, "
            "instalar dependencias, usar red ni ejecutar comandos arbitrarios.",
        )
    )
    return "\n".join(lines)


def execution_context(
    plan: ActionPlan,
    step_results: list[dict[str, Any]],
) -> tuple[str, ...]:
    blocks = [
        "PLAN AUTORIZADO Y RESULTADOS REALES. Resume únicamente estos datos. "
        "No afirmes que se modificaron archivos ni que se ejecutaron acciones no listadas.",
        f"Plan: {plan.plan_id}\nSolicitud: {plan.request}\nResumen: {plan.summary}",
    ]
    remaining = _MAX_RESULT_CHARS
    for index, item in enumerate(step_results, start=1):
        message = str(item.get("message", ""))[:_MAX_STEP_MESSAGE_CHARS]
        data = _safe_result_data(item.get("data", {}))
        block = (
            f"Paso {index}: {item.get('skill_name')}\n"
            f"Estado: {'passed' if item.get('ok') else 'failed'}\n"
            f"Mensaje: {message}\n"
            f"Datos: {json.dumps(data, ensure_ascii=False, sort_keys=True)}"
        )
        if len(block) > remaining:
            block = block[: max(0, remaining)]
        if block:
            blocks.append(block)
            remaining -= len(block)
        if remaining <= 0:
            break
    return tuple(blocks)


def deterministic_execution_summary(
    plan: ActionPlan,
    step_results: list[dict[str, Any]],
) -> str:
    passed = sum(1 for item in step_results if item.get("ok"))
    lines = [
        (
            f"Plan supervisado {plan.plan_id} completado: "
            f"{passed}/{len(step_results)} pasos correctos."
        ),
        "",
    ]
    for index, item in enumerate(step_results, start=1):
        status = "OK" if item.get("ok") else "FALLÓ"
        lines.append(f"{index}. {item.get('skill_name')} — {status}")
        message = str(item.get("message", "")).strip()
        if message:
            lines.append(message[:_MAX_STEP_MESSAGE_CHARS])
    return "\n".join(lines).strip()


def action_run_status(step_results: list[dict[str, Any]], planned_steps: int) -> str:
    if not step_results:
        return "failed"
    passed = sum(1 for item in step_results if item.get("ok"))
    if passed == planned_steps:
        return "passed"
    if passed == 0:
        return "failed"
    return "partial"


def elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _build_plan(
    request: str,
    source: str,
    steps: tuple[ActionStep, ...] | list[ActionStep],
    *,
    summary: str = "",
) -> ActionPlan:
    clean_steps = tuple(steps)
    if not clean_steps or len(clean_steps) > _MAX_PLAN_STEPS:
        raise ValueError("El plan debe contener entre 1 y 4 pasos.")
    return ActionPlan(
        plan_id=_plan_id(request.strip(), clean_steps, fail_fast=True),
        request=request.strip(),
        source=source,
        summary=summary[:1000] or _default_summary(clean_steps),
        steps=clean_steps,
    )


def _validate_steps(
    raw_steps: list[dict[str, Any]],
    *,
    registry: SkillRegistry,
) -> tuple[ActionStep, ...]:
    if not 1 <= len(raw_steps) <= _MAX_PLAN_STEPS:
        raise ValueError("El plan debe contener entre 1 y 4 pasos.")
    steps: list[ActionStep] = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            raise ValueError("Cada paso del plan debe ser un objeto.")
        skill_name = str(raw.get("skill_name") or raw.get("skill") or "").strip()
        if skill_name not in _ALLOWED_SKILL_PARAMS:
            raise ValueError(f"Skill no permitida en planes supervisados: {skill_name}")
        if registry.get(skill_name) is None:
            raise ValueError(f"Skill no registrada: {skill_name}")
        params = raw.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("Los parámetros del paso deben ser un objeto.")
        allowed_params = _ALLOWED_SKILL_PARAMS[skill_name]
        extra = set(params) - allowed_params
        if extra:
            raise ValueError(
                f"Parámetros no permitidos para {skill_name}: {', '.join(sorted(extra))}"
            )
        if allowed_params and not allowed_params.issubset(params):
            missing = allowed_params - set(params)
            raise ValueError(
                f"Faltan parámetros para {skill_name}: {', '.join(sorted(missing))}"
            )
        clean_params: dict[str, Any] = {}
        for key, value in params.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"El parámetro {key} debe ser texto no vacío.")
            clean_params[key] = value.strip()
        purpose = str(raw.get("purpose", "")).strip()[:500]
        steps.append(
            ActionStep(
                skill_name=skill_name,
                params=clean_params,
                purpose=purpose or f"Ejecutar {skill_name}.",
            )
        )
    return tuple(steps)


def _specific_tool_steps(normalized: str, path: str) -> tuple[ActionStep, ...]:
    candidates = (
        ("inspeccion", "python.project_inspect"),
        ("pyproject", "python.pyproject_validate"),
        ("compileall", "python.compile_project"),
        ("compila", "python.compile_project"),
        ("ruff", "ruff.check"),
        ("mypy", "mypy.check"),
        ("pytest", "pytest.run"),
    )
    steps: list[ActionStep] = []
    seen: set[str] = set()
    if "python" not in normalized:
        return ()
    for token, skill_name in candidates:
        if token not in normalized or skill_name in seen:
            continue
        seen.add(skill_name)
        steps.append(
            ActionStep(
                skill_name=skill_name,
                params={"path": path},
                purpose=f"Ejecutar {skill_name} como parte de la revisión solicitada.",
            )
        )
        if len(steps) == _MAX_PLAN_STEPS:
            break
    return tuple(steps)


def _extract_path(text: str) -> str | None:
    quoted = re.search(r"[\"']((?:~?/|/)[^\"']+)[\"']", text)
    if quoted:
        return quoted.group(1).strip()
    match = re.search(r"(?<!\w)((?:~?/|/)[^\s,;]+)", text)
    if not match:
        return None
    return match.group(1).rstrip(".?!:)")


def _contains_term(text: str, term: str) -> bool:
    if term in {"c", "go"}:
        return re.search(rf"(?<![\w+#.]){re.escape(term)}(?![\w+#.])", text) is not None
    return term in text


def _plan_id(
    request: str,
    steps: tuple[ActionStep, ...],
    *,
    fail_fast: bool,
) -> str:
    payload = {
        "request": request,
        "fail_fast": fail_fast,
        "steps": [step.to_dict() for step in steps],
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"plan_{digest[:16]}"


def _default_summary(steps: tuple[ActionStep, ...]) -> str:
    names = ", ".join(step.skill_name for step in steps)
    return f"Ejecutar {len(steps)} paso(s) controlado(s): {names}."


def _extract_json_object(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.I)
        clean = re.sub(r"\s*```$", "", clean)
    start = clean.find("{")
    end = clean.rfind("}")
    if start < 0 or end < start:
        raise ValueError("El modelo no devolvió un objeto JSON.")
    payload = json.loads(clean[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("La propuesta del modelo no es un objeto JSON.")
    return payload


def _safe_result_data(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed_keys = {
        "status",
        "returncode",
        "duration_ms",
        "tool",
        "project_root",
        "resolved_path",
        "summary",
        "stages",
        "findings",
        "counts",
        "files",
        "warnings",
        "errors",
        "engine",
        "generated",
        "unavailable",
        "skipped",
    }
    safe: dict[str, Any] = {}
    for key in sorted(set(value) & allowed_keys):
        safe[key] = _bounded_json_value(value[key])
    return safe


def _bounded_json_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return "[truncated]"
    if isinstance(value, dict):
        items = list(value.items())[:50]
        return {
            str(key)[:100]: _bounded_json_value(item, depth=depth + 1)
            for key, item in items
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_json_value(item, depth=depth + 1) for item in list(value)[:50]]
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    return str(value)[:500]


def _public_action_run(item: dict[str, Any]) -> dict[str, Any]:
    item["plan"] = _json_object(item.pop("plan_json", "{}"))
    item["result"] = _json_object(item.pop("result_json", "{}"))
    return item


def _json_object(value: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
