from __future__ import annotations

from pathlib import Path
from typing import Any

from elyndra.alexandria import plan_alexandria_query
from elyndra.application import ElyndraApplication
from elyndra.engines import LanguageReply
from elyndra.paths import ElyndraPaths


class _CapturingEngine:
    name = "test-capturing"
    supports_vision = False

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def reply(
        self,
        prompt: str,
        *,
        context: tuple[str, ...] = (),
        history: tuple[Any, ...] = (),
        response_language: str | None = None,
        keep_alive_seconds: int = 0,
        images: tuple[str, ...] = (),
        max_tokens: int | None = None,
    ) -> LanguageReply:
        self.calls.append(
            {
                "prompt": prompt,
                "context": context,
                "history": history,
                "response_language": response_language,
                "keep_alive_seconds": keep_alive_seconds,
                "images": images,
                "max_tokens": max_tokens,
            }
        )
        return LanguageReply(
            text="Respuesta técnica basada en [A1].",
            engine=self.name,
            generated=True,
        )

    def release(self) -> None:
        return


class _ExplodingEngine:
    name = "test-exploding"
    supports_vision = False

    def reply(self, *args: object, **kwargs: object) -> LanguageReply:
        raise AssertionError("El motor no debe ejecutarse sin respaldo estricto.")

    def release(self) -> None:
        return


def _import_reviewed_book(
    app: ElyndraApplication,
    *,
    name: str,
    domain: str,
    filename: str,
    content: str,
) -> None:
    library = app.alexandria.create_library(
        name,
        domain=domain,
        language="es",
        version="1.0.0",
        license_id="test",
    )
    source_path = Path.home() / "Proyectos" / filename
    source_path.write_text(content, encoding="utf-8")
    source = app.alexandria.import_file(library["public_id"], source_path)
    app.alexandria.review_source(int(source["id"]))


def test_query_plan_detects_multiple_tasks_and_database_domain() -> None:
    query = (
        "Según Alejandría, revisa esta consulta PDO y separa: hallazgos confirmados, "
        "riesgos posibles y verificaciones pendientes. "
        "¿GROUP_CONCAT implica una inyección SQL? "
        "¿Cuándo necesito una transacción? "
        "¿Cómo evito que dos compras consuman el mismo stock?"
    )

    plan = plan_alexandria_query(query)

    assert plan.strict is True
    assert plan.task_count == 4
    assert plan.max_tokens == 384
    assert plan.domain_prefixes[0] == "programming/php/database"
    assert "Hallazgos confirmados" in plan.instruction
    assert "encabezados numerados" in plan.instruction


def test_query_plan_resolves_architecture_meaning() -> None:
    plan = plan_alexandria_query(
        "Explícame la diferencia entre dominio, aplicación e infraestructura."
    )

    assert plan.should_search is True
    assert plan.domain_prefixes == ("programming/php/architecture",)
    assert plan.max_tokens == 256


def test_repository_prefers_reviewed_units_and_filters_domain(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _import_reviewed_book(
        app,
        name="PHP Database",
        domain="programming/php/database",
        filename="database.md",
        content="# GROUP_CONCAT\n\nGROUP_CONCAT no implica inyección SQL por sí mismo.",
    )
    unreviewed = app.alexandria.create_library(
        "General SQL",
        domain="programming/sql/general",
    )
    source_path = Path.home() / "Proyectos" / "general.md"
    source_path.write_text(
        "GROUP_CONCAT aparece en consultas SQL.",
        encoding="utf-8",
    )
    app.alexandria.import_file(unreviewed["public_id"], source_path)

    results = app.alexandria.search(
        "GROUP_CONCAT inyección SQL",
        domain_prefixes=("programming/php/database",),
        prefer_reviewed=True,
    )

    assert results
    assert all(item["library_domain"] == "programming/php/database" for item in results)
    assert results[0]["review_status"] == "reviewed"
    assert results[0]["matched_terms"] >= 2


def test_strict_mode_uses_reviewed_alexandria_context_and_citations(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _import_reviewed_book(
        app,
        name="PHP — PDO y MariaDB",
        domain="programming/php/database",
        filename="pdo.md",
        content=(
            "# GROUP_CONCAT\n\nGROUP_CONCAT no produce inyección SQL por sí mismo. "
            "El riesgo depende de cómo se construye la consulta.\n\n"
            "# Transacciones\n\nUna transacción agrupa escrituras relacionadas. "
            "Para stock puede usarse UPDATE atómico con stock > 0."
        ),
    )
    engine = _CapturingEngine()
    app.language_engine = engine
    query = (
        "Según Alejandría, revisa esta consulta PDO y separa: hallazgos confirmados, "
        "riesgos posibles y verificaciones pendientes. "
        "¿GROUP_CONCAT implica una inyección SQL? "
        "¿Cuándo necesito una transacción? "
        "¿Cómo evito que dos compras consuman el mismo stock?"
    )

    result = app.ask(query)

    assert result.ok is True
    assert engine.calls == []
    assert result.data["engine"] == "alexandria-evidence"
    assert result.data["generated"] is False
    assert result.data["fast_path"] == "alexandria_evidence"
    assert result.data["alexandria_strict"] is True
    assert result.data["alexandria_task_count"] == 4
    assert result.data["timings"]["generation_ms"] == 0
    assert "No incluiste la consulta PDO" in result.message
    assert "GROUP_CONCAT" in result.message
    assert "transacción" in result.message.casefold()
    assert "stock" in result.message.casefold()
    assert "Fuentes de Alejandría:" in result.message
    assert "PHP — PDO y MariaDB" in result.message


def test_strict_mode_does_not_fall_back_to_general_model_without_sources(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    app.language_engine = _ExplodingEngine()

    result = app.ask("Según Alejandría, ¿qué es un prepared statement?")

    assert result.ok is True
    assert result.data["fast_path"] == "alexandria_no_support"
    assert result.data["generated"] is False
    assert "No encontré respaldo suficiente" in result.message


def test_technical_single_question_gets_medium_output_budget(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _import_reviewed_book(
        app,
        name="PHP Quality",
        domain="programming/php/quality",
        filename="quality.md",
        content=(
            "php -l valida sintaxis. PHPStan realiza análisis estático de tipos. "
            "PHPUnit ejecuta pruebas de comportamiento."
        ),
    )
    engine = _CapturingEngine()
    app.language_engine = engine

    result = app.ask("¿Qué diferencia hay entre php -l, PHPStan y PHPUnit?")

    assert result.ok is True
    assert engine.calls[0]["max_tokens"] == 256
    assert result.data["alexandria_domains"][0] == "programming/php/quality"
    assert "Fuentes de Alejandría:" in result.message


def test_missing_query_is_reported_deterministically_and_removed_from_model_prompt(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _import_reviewed_book(
        app,
        name="PHP Database",
        domain="programming/php/database",
        filename="database-v2.md",
        content=(
            "GROUP_CONCAT no implica inyección SQL por sí mismo. "
            "Las transacciones protegen invariantes. "
            "El stock puede descontarse mediante UPDATE atómico con stock > 0."
        ),
    )
    engine = _CapturingEngine()
    app.language_engine = engine
    query = (
        "Según Alejandría, revisa esta consulta PDO y separa: hallazgos confirmados, "
        "riesgos posibles y verificaciones pendientes. "
        "¿GROUP_CONCAT implica una inyección SQL? "
        "¿Cuándo necesito una transacción? "
        "¿Cómo evito que dos compras consuman el mismo stock?"
    )

    result = app.ask(query)

    assert result.message.startswith("1. Información necesaria")
    assert "No incluiste la consulta PDO" in result.message
    assert engine.calls == []
    assert result.data["engine"] == "alexandria-evidence"
    assert result.data["generated"] is False
    assert result.data["fast_path"] == "alexandria_evidence"
    assert "GROUP_CONCAT" in result.message
    assert "stock" in result.message.casefold()


def test_specialized_unreviewed_library_beats_reviewed_general_fallback(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _import_reviewed_book(
        app,
        name="PHP general",
        domain="programming/php",
        filename="general-reviewed.md",
        content="Una interfaz puede significar distintas cosas en informática.",
    )
    specialized = app.alexandria.create_library(
        "PHP Architecture",
        domain="programming/php/architecture",
    )
    source_path = Path.home() / "Proyectos" / "architecture-unreviewed.md"
    source_path.write_text(
        "Una interface de PHP expresa un contrato entre componentes. "
        "No conviene crearla cuando solo existe una implementación sin frontera estable.",
        encoding="utf-8",
    )
    app.alexandria.import_file(specialized["public_id"], source_path)
    engine = _CapturingEngine()
    app.language_engine = engine

    result = app.ask("¿Cuándo tiene sentido crear una interfaz y cuándo no?")

    assert result.data["alexandria"][0]["library_domain"] == (
        "programming/php/architecture"
    )
    assert result.data["alexandria"][0]["retrieval_domain_exact"] is True
    assert "PHP Architecture" in result.message


def test_query_plan_disambiguates_php_interface_from_visual_interface() -> None:
    code_plan = plan_alexandria_query(
        "En PHP, ¿cuándo tiene sentido crear una interfaz y cuándo no?"
    )
    visual_plan = plan_alexandria_query(
        "¿Cómo diseño una interfaz gráfica para una aplicación móvil?"
    )

    assert code_plan.domain_prefixes[0] == "programming/php/architecture"
    assert "no GUI" in code_plan.instruction
    assert "programming/php/architecture" not in visual_plan.domain_prefixes


def test_alexandria_result_exposes_phase_timings(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _import_reviewed_book(
        app,
        name="PHP Operations",
        domain="programming/php/operations",
        filename="operations.md",
        content="OPcache conserva bytecode compilado y no corrige consultas SQL lentas.",
    )
    engine = _CapturingEngine()
    app.language_engine = engine

    result = app.ask("¿Qué comprueba OPcache y qué problemas no resuelve?")

    assert result.data["timings"]["planning_ms"] >= 0
    assert result.data["timings"]["retrieval_ms"] >= 0
    assert result.data["timings"]["context_ms"] >= 0
    assert result.data["timings"]["generation_ms"] >= 0
    assert result.data["timings"]["total_ms"] >= 0


class _TruncatingEngine:
    name = "test-truncating"
    supports_vision = False

    def __init__(self) -> None:
        self.calls = 0

    def reply(
        self,
        prompt: str,
        *,
        context: tuple[str, ...] = (),
        history: tuple[Any, ...] = (),
        response_language: str | None = None,
        keep_alive_seconds: int = 0,
        images: tuple[str, ...] = (),
        max_tokens: int | None = None,
        on_token: Any = None,
    ) -> LanguageReply:
        del context, history, response_language, keep_alive_seconds, images, max_tokens
        self.calls += 1
        if self.calls == 1:
            text = "Primera parte incompleta."
            if on_token:
                on_token(text)
            return LanguageReply(
                text=text,
                engine=self.name,
                generated=True,
                metadata={"done_reason": "length"},
            )
        assert "Continúa" in prompt
        text = "Segunda parte completa."
        if on_token:
            on_token(text)
        return LanguageReply(
            text=text,
            engine=self.name,
            generated=True,
            metadata={"done_reason": "stop"},
        )

    def release(self) -> None:
        return


def test_truncated_model_reply_gets_one_controlled_continuation(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _import_reviewed_book(
        app,
        name="PHP Operations",
        domain="programming/php/operations",
        filename="opcache-continuation.md",
        content="OPcache conserva bytecode compilado.",
    )
    engine = _TruncatingEngine()
    app.language_engine = engine
    tokens: list[str] = []

    result = app.ask(
        "¿Qué comprueba OPcache?",
        interactive=True,
        on_token=tokens.append,
    )

    assert engine.calls == 2
    assert "Primera parte incompleta" in result.message
    assert "Segunda parte completa" in result.message
    assert result.data["metrics"]["continued"] is True
    assert "\n" in tokens


def test_composite_query_does_not_add_general_fallback_when_exact_unit_is_reused(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _import_reviewed_book(
        app,
        name="PHP general",
        domain="programming/php",
        filename="general-composite.md",
        content="PHP permite construir aplicaciones backend.",
    )
    _import_reviewed_book(
        app,
        name="PHP Database",
        domain="programming/php/database",
        filename="database-composite.md",
        content=(
            "GROUP_CONCAT no implica inyección SQL. Una transacción protege invariantes. "
            "El stock puede descontarse con una actualización atómica."
        ),
    )
    engine = _CapturingEngine()
    app.language_engine = engine

    result = app.ask(
        "Según Alejandría: ¿GROUP_CONCAT implica inyección SQL? "
        "¿Cuándo uso una transacción? ¿Cómo protejo el stock?"
    )

    assert result.data["alexandria"]
    assert {
        item["library_domain"] for item in result.data["alexandria"]
    } == {"programming/php/database"}
    assert result.data["alexandria"][0]["retrieval_task_indices"] == [1, 2, 3]


def test_strict_php_tools_answer_is_grounded_without_model(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _import_reviewed_book(
        app,
        name="PHP Quality",
        domain="programming/php/quality",
        filename="quality-evidence.md",
        content=(
            "# Formato y lint\n\n"
            "Capas distintas: `php -l`: sintaxis; formatter: estilo; "
            "static analysis: tipos; test: comportamiento.\n\n"
            "# Análisis estático\n\nHerramientas: PHPStan; Psalm.\n\n"
            "# PHPUnit y Pest\n\nPHPUnit es una base común para ejecutar pruebas."
        ),
    )
    app.language_engine = _ExplodingEngine()

    result = app.ask(
        "Según Alejandría, ¿qué diferencia hay entre php -l, PHPStan y PHPUnit?"
    )

    assert result.data["engine"] == "alexandria-evidence"
    assert result.data["generated"] is False
    assert result.data["timings"]["generation_ms"] == 0
    assert "php -l" in result.message
    assert "PHPStan" in result.message
    assert "PHPUnit" in result.message
    assert "inyección SQL" not in result.message
    assert {item["library_domain"] for item in result.data["alexandria"]} == {
        "programming/php/quality"
    }


def test_strict_webhook_answer_covers_required_security_evidence(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _import_reviewed_book(
        app,
        name="PHP Security",
        domain="programming/php/security",
        filename="webhook-evidence.md",
        content=(
            "# Webhooks\n\n"
            "Validar firma, timestamp, replay e idempotencia.\n\n"
            "# Pagos\n\n"
            "Verificar monto y moneda esperados y consultar al proveedor "
            "cuando corresponda."
        ),
    )
    app.language_engine = _ExplodingEngine()

    result = app.ask(
        "Según Alejandría, ¿cómo debería procesar un webhook de pago?"
    )
    folded = result.message.casefold()

    assert result.data["engine"] == "alexandria-evidence"
    for term in ("firma", "timestamp", "replay", "idempotencia", "monto", "moneda"):
        assert term in folded
    assert "proveedor" in folded
    assert {item["library_domain"] for item in result.data["alexandria"]} == {
        "programming/php/security"
    }


def test_strict_opcache_answer_does_not_claim_it_validates_syntax(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _import_reviewed_book(
        app,
        name="PHP Operations",
        domain="programming/php/operations",
        filename="opcache-evidence.md",
        content=(
            "# OPcache\n\n"
            "OPcache guarda bytecode compilado y evita recompilar scripts. "
            "No corrige algoritmos lentos, consultas SQL ni operaciones de I/O."
        ),
    )
    app.language_engine = _ExplodingEngine()

    result = app.ask(
        "Según Alejandría, ¿qué comprueba OPcache y qué problemas no resuelve?"
    )
    folded = result.message.casefold()

    assert result.data["engine"] == "alexandria-evidence"
    assert "bytecode" in folded
    assert "consultas sql" in folded
    assert "algoritmos" in folded
    assert "comprueba la sintaxis" not in folded
    assert {item["library_domain"] for item in result.data["alexandria"]} == {
        "programming/php/operations"
    }


def test_strict_interface_answer_uses_architecture_contract(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _import_reviewed_book(
        app,
        name="PHP Architecture",
        domain="programming/php/architecture",
        filename="interface-evidence.md",
        content=(
            "# Fronteras\n\n"
            "Una interface expresa un contrato entre componentes y resulta útil "
            "cuando existen múltiples implementaciones reales. No conviene crearla "
            "por defecto para una única clase sin una frontera estable."
        ),
    )
    app.language_engine = _ExplodingEngine()

    result = app.ask(
        "Según Alejandría, ¿cuándo tiene sentido crear una interfaz y cuándo no?"
    )
    folded = result.message.casefold()

    assert result.data["engine"] == "alexandria-evidence"
    assert "contrato" in folded
    assert "múltiples implementaciones" in folded
    assert "interfaz gráfica" not in folded
    assert {item["library_domain"] for item in result.data["alexandria"]} == {
        "programming/php/architecture"
    }


def test_evidence_synthesizes_distinct_php_tool_roles(
    isolated_home: ElyndraPaths,
) -> None:
    app = ElyndraApplication.load(isolated_home)
    _import_reviewed_book(
        app,
        name="PHP Quality",
        domain="programming/php/quality",
        filename="php-quality-roles.md",
        content=(
            "## Formato y lint\n\n"
            "Capas distintas: php -l: sintaxis; formatter: estilo; "
            "static analysis: tipos; test: comportamiento.\n\n"
            "## Análisis estático\n\n"
            "PHPStan detecta problemas de tipos, nullabilidad y contratos.\n\n"
            "## PHPUnit\n\n"
            "PHPUnit ejecuta pruebas automatizadas de comportamiento."
        ),
    )
    app.language_engine = _ExplodingEngine()

    result = app.ask(
        "Según Alejandría, ¿qué diferencia hay entre php -l, PHPStan y PHPUnit?"
    )

    assert result.ok is True
    assert "`php -l` comprueba la sintaxis" in result.message
    assert "`PHPStan` realiza análisis estático" in result.message
    assert "`PHPUnit` ejecuta pruebas automatizadas" in result.message
