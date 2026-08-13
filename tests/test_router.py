from elyndra.router import DeterministicRouter


def test_router_recognizes_memory_command() -> None:
    route = DeterministicRouter().route("Recuerda que uso VS Code")
    assert route.kind == "skill"
    assert route.skill_name == "memory.remember"
    assert route.params["content"] == "uso VS Code"


def test_router_recognizes_system_status() -> None:
    route = DeterministicRouter().route("¿Cómo está el sistema?")
    assert route.skill_name == "system.status"


def test_router_recognizes_project_inspection() -> None:
    route = DeterministicRouter().route("Inspecciona el proyecto elyndra")
    assert route.skill_name == "project.inspect"
    assert route.params["name"] == "elyndra"


def test_router_recognizes_project_text_search() -> None:
    route = DeterministicRouter().route(
        "Busca KnowledgeRepository en el proyecto elyndra"
    )
    assert route.skill_name == "project.search_text"
    assert route.params == {"query": "KnowledgeRepository", "name": "elyndra"}


def test_router_recognizes_file_read() -> None:
    route = DeterministicRouter().route("Lee el archivo /tmp/demo.txt")
    assert route.skill_name == "file.read"
    assert route.params["path"] == "/tmp/demo.txt"


def test_router_recognizes_knowledge_import() -> None:
    route = DeterministicRouter().route("Importa el documento /tmp/manual.md")
    assert route.skill_name == "knowledge.import"
    assert route.params["path"] == "/tmp/manual.md"


def test_router_uses_combined_local_search() -> None:
    route = DeterministicRouter().route("¿Qué sabes de privacidad?")
    assert route.kind == "local_search"
    assert route.query == "privacidad"


def test_router_accepts_what_do_you_know_about() -> None:
    route = DeterministicRouter().route("¿Qué sabes sobre equipos modestos?")
    assert route.kind == "local_search"
    assert route.query == "equipos modestos"


def test_router_falls_back_without_guessing() -> None:
    route = DeterministicRouter().route("Escribe una novela de ciencia ficción")
    assert route.kind == "fallback"
