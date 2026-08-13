from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from elyndra.languages import parse_language_change
from elyndra.personal_organizer import organizer_query
from elyndra.wellbeing import wellbeing_query


@dataclass(frozen=True, slots=True)
class Route:
    kind: str
    skill_name: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    query: str | None = None


class DeterministicRouter:
    """Router español pequeño e inspeccionable. No pretende ser un modelo lingüístico."""

    def route(self, text: str) -> Route:
        original = text.strip()
        normalized = _normalize(original).strip("¿?¡!.,:;")
        if not normalized:
            return Route("fallback")

        requested_language = parse_language_change(original)
        if requested_language:
            return Route(
                "language_change",
                params={"language": requested_language},
            )

        organizer = organizer_query(original)
        if organizer is not None:
            return Route("organizer", params=organizer)

        wellbeing = wellbeing_query(original)
        if wellbeing is not None:
            return Route("wellbeing", params=wellbeing)

        remember = re.match(r"^(?:recuerda|recordar)(?:\s+que)?\s+(.+)$", original, re.I)
        if remember:
            return Route(
                "skill",
                skill_name="memory.remember",
                params={"content": remember.group(1).strip(), "kind": "fact"},
            )

        local_search_prefixes = (
            "que recuerdas de ",
            "que sabes de ",
            "que sabes sobre ",
            "busca en tu memoria ",
            "busca en tu conocimiento ",
            "consulta tus datos sobre ",
            "recupera el recuerdo ",
        )
        for prefix in local_search_prefixes:
            if normalized.startswith(prefix):
                offset = len(prefix)
                return Route("local_search", query=normalized[offset:].strip(" ?"))

        status_terms = (
            "estado del sistema",
            "estado del pc",
            "estado del equipo",
            "como esta el sistema",
            "como esta el pc",
            "cuanta ram",
            "revisa la ram",
            "uso de ram",
        )
        if any(term in normalized for term in status_terms):
            return Route("skill", skill_name="system.status")

        inspect_project = re.match(
            r"^(?:inspecciona|inspeccionar|analiza|analizar|revisa|revisar)"
            r"(?:\s+el)?\s+proyecto\s+(.+)$",
            normalized,
        )
        if inspect_project and not inspect_project.group(1).strip().casefold().startswith(
            (
                "php ",
                "web ",
                "python ",
                "java ",
                "kotlin ",
                "c# ",
                "csharp ",
                ".net ",
                "dotnet ",
                "ruby ",
                "go ",
                "golang ",
                "rust ",
                "swift ",
                "sql ",
                "sqlite ",
                "database ",
                "base de datos ",
                "dart ",
                "flutter ",
                "c ",
                "c++ ",
                "native ",
            )
        ):
            return Route(
                "skill",
                skill_name="project.inspect",
                params={"name": inspect_project.group(1).strip()},
            )

        open_project = re.match(r"^(?:abre|abrir)(?:\s+el)?\s+proyecto\s+(.+)$", normalized)
        if open_project:
            return Route(
                "skill",
                skill_name="project.open",
                params={"name": open_project.group(1).strip()},
            )

        project_search = re.match(
            r"^(?:busca|buscar)\s+(.+?)\s+(?:en|dentro de)(?:\s+el)?\s+proyecto\s+(.+)$",
            original,
            re.I,
        )
        if project_search:
            return Route(
                "skill",
                skill_name="project.search_text",
                params={
                    "query": _strip_quotes(project_search.group(1).strip()),
                    "name": project_search.group(2).strip(),
                },
            )

        read_file = re.match(
            r"^(?:lee|leer|muestra|mostrar)(?:\s+el)?\s+archivo\s+(.+)$",
            original,
            re.I,
        )
        if read_file:
            return Route(
                "skill",
                skill_name="file.read",
                params={"path": _strip_quotes(read_file.group(1).strip())},
            )

        import_knowledge = re.match(
            r"^(?:importa|importar|aprende|aprender)(?:\s+el)?(?:\s+documento|\s+archivo)?\s+(.+)$",
            original,
            re.I,
        )
        if import_knowledge:
            return Route(
                "skill",
                skill_name="knowledge.import",
                params={"path": _strip_quotes(import_knowledge.group(1).strip())},
            )

        if normalized in {
            "php verify",
            "verify php",
            "verifica php",
            "verificar php",
            "verifica proyecto php",
        }:
            return Route(
                "clarification",
                params={
                    "message": (
                        "Indica la ruta del proyecto PHP. Ejemplo: "
                        "`php verify /home/user/Proyectos/mi-proyecto`."
                    ),
                    "missing_parameter": "path",
                    "intended_skill": "php.verify_project",
                },
            )

        if normalized in {
            "web verify",
            "verify web",
            "verifica web",
            "verificar web",
            "verifica proyecto web",
        }:
            return Route(
                "clarification",
                params={
                    "message": (
                        "Indica la ruta del proyecto web. Ejemplo: "
                        "`web verify /home/user/Proyectos/mi-web`."
                    ),
                    "missing_parameter": "path",
                    "intended_skill": "web.verify_project",
                },
            )

        if normalized in {
            "python verify",
            "verify python",
            "verifica python",
            "verificar python",
            "verifica proyecto python",
        }:
            return Route(
                "clarification",
                params={
                    "message": (
                        "Indica la ruta del proyecto Python. Ejemplo: "
                        "`python verify /home/user/Proyectos/mi-python`."
                    ),
                    "missing_parameter": "path",
                    "intended_skill": "python.verify_project",
                },
            )

        if normalized in {
            "java verify",
            "verify java",
            "verifica java",
            "verificar java",
            "verifica proyecto java",
        }:
            return Route(
                "clarification",
                params={
                    "message": (
                        "Indica la ruta del proyecto Java. Ejemplo: "
                        "`java verify /home/user/Proyectos/mi-java`."
                    ),
                    "missing_parameter": "path",
                    "intended_skill": "java.verify_project",
                },
            )

        if normalized in {
            "kotlin verify",
            "verify kotlin",
            "verifica kotlin",
            "verificar kotlin",
            "verifica proyecto kotlin",
        }:
            return Route(
                "clarification",
                params={
                    "message": (
                        "Indica la ruta del proyecto Kotlin. Ejemplo: "
                        "`kotlin verify /home/user/Proyectos/mi-kotlin`."
                    ),
                    "missing_parameter": "path",
                    "intended_skill": "kotlin.verify_project",
                },
            )


        if normalized in {
            "dotnet verify",
            ".net verify",
            "c# verify",
            "csharp verify",
            "verify dotnet",
            "verify .net",
            "verifica dotnet",
            "verifica .net",
            "verifica c#",
            "verificar dotnet",
            "verifica proyecto dotnet",
            "verifica proyecto .net",
            "verifica proyecto c#",
        }:
            return Route(
                "clarification",
                params={
                    "message": (
                        "Indica la ruta del proyecto C#/.NET. Ejemplo: "
                        "`dotnet verify /home/user/Proyectos/mi-dotnet`."
                    ),
                    "missing_parameter": "path",
                    "intended_skill": "dotnet.verify_project",
                },
            )


        if normalized in {
            "ruby verify",
            "verify ruby",
            "verifica ruby",
            "verificar ruby",
            "verifica proyecto ruby",
        }:
            return Route(
                "clarification",
                params={
                    "message": (
                        "Indica la ruta del proyecto Ruby. Ejemplo: "
                        "`ruby verify /home/user/Proyectos/mi-ruby`."
                    ),
                    "missing_parameter": "path",
                    "intended_skill": "ruby.verify_project",
                },
            )

        if normalized in {
            "go verify",
            "verify go",
            "verify golang",
            "verifica go",
            "verifica golang",
            "verificar go",
            "verificar golang",
            "verifica proyecto go",
            "verifica proyecto golang",
        }:
            return Route(
                "clarification",
                params={
                    "message": (
                        "Indica la ruta del proyecto Go. Ejemplo: "
                        "`go verify /home/user/Proyectos/mi-go`."
                    ),
                    "missing_parameter": "path",
                    "intended_skill": "go.verify_project",
                },
            )

        if normalized in {
            "rust verify",
            "verify rust",
            "verifica rust",
            "verificar rust",
            "verifica proyecto rust",
        }:
            return Route(
                "clarification",
                params={
                    "message": (
                        "Indica la ruta del proyecto Rust. Ejemplo: "
                        "`rust verify /home/user/Proyectos/mi-rust`."
                    ),
                    "missing_parameter": "path",
                    "intended_skill": "rust.verify_project",
                },
            )

        if normalized in {
            "swift verify",
            "verify swift",
            "verifica swift",
            "verificar swift",
            "verifica proyecto swift",
        }:
            return Route(
                "clarification",
                params={
                    "message": (
                        "Indica la ruta del proyecto Swift. Ejemplo: "
                        "`swift verify /home/user/Proyectos/mi-swift`."
                    ),
                    "missing_parameter": "path",
                    "intended_skill": "swift.verify_project",
                },
            )

        if normalized in {
            "dart verify",
            "flutter verify",
            "verify dart",
            "verify flutter",
            "verifica dart",
            "verificar dart",
            "verifica flutter",
            "verificar flutter",
            "verifica proyecto dart",
            "verifica proyecto flutter",
        }:
            return Route(
                "clarification",
                params={
                    "message": (
                        "Indica la ruta del proyecto Dart o Flutter. Ejemplo: "
                        "`dart verify /home/user/Proyectos/mi-app`."
                    ),
                    "missing_parameter": "path",
                    "intended_skill": "dart.verify_project",
                },
            )

        if normalized in {
            "sql verify",
            "sqlite verify",
            "database verify",
            "verify sql",
            "verify sqlite",
            "verifica sql",
            "verificar sql",
            "verifica sqlite",
            "verificar sqlite",
            "verifica proyecto sql",
            "verifica proyecto sqlite",
            "verifica base de datos",
        }:
            return Route(
                "clarification",
                params={
                    "message": (
                        "Indica la ruta del proyecto SQL. Ejemplo: "
                        "`sql verify /home/user/Proyectos/mi-base` ."
                    ),
                    "missing_parameter": "path",
                    "intended_skill": "sql.verify_project",
                },
            )

        if normalized in {
            "c verify",
            "c++ verify",
            "native verify",
            "verifica c",
            "verifica c++",
            "verifica proyecto c",
            "verifica proyecto c++",
        }:
            return Route(
                "clarification",
                params={
                    "message": (
                        "Indica la ruta del proyecto C/C++. Ejemplo: "
                        "`native verify /home/user/Proyectos/mi-native`."
                    ),
                    "missing_parameter": "path",
                    "intended_skill": "native.verify_project",
                },
            )

        knowledge_search = re.match(
            r"^(?:busca|buscar|consulta|consultar)(?:\s+en)?\s+(?:el\s+)?conocimiento\s+(.+)$",
            original,
            re.I,
        )
        if knowledge_search:
            return Route(
                "skill",
                skill_name="knowledge.search",
                params={"query": _strip_quotes(knowledge_search.group(1).strip())},
            )


        ruby_verify = re.match(
            r"^(?:verifica|verificar|valida|validar|revisa|revisar)"
            r"(?:\s+completamente|\s+todo)?(?:\s+el)?\s+proyecto\s+ruby\s+(.+)$",
            original,
            re.I,
        )
        if ruby_verify:
            return Route(
                "skill",
                skill_name="ruby.verify_project",
                params={"path": _strip_quotes(ruby_verify.group(1).strip())},
            )

        ruby_project_inspect = re.match(
            r"^(?:inspecciona|inspeccionar|analiza|analizar)"
            r"(?:\s+el)?\s+proyecto\s+ruby\s+(.+)$",
            original,
            re.I,
        )
        if ruby_project_inspect:
            return Route(
                "skill",
                skill_name="ruby.project_inspect",
                params={"path": _strip_quotes(ruby_project_inspect.group(1).strip())},
            )

        go_verify = re.match(
            r"^(?:verifica|verificar|valida|validar|revisa|revisar)"
            r"(?:\s+completamente|\s+todo)?(?:\s+el)?\s+proyecto\s+"
            r"(?:go|golang)\s+(.+)$",
            original,
            re.I,
        )
        if go_verify:
            return Route(
                "skill",
                skill_name="go.verify_project",
                params={"path": _strip_quotes(go_verify.group(1).strip())},
            )

        go_project_inspect = re.match(
            r"^(?:inspecciona|inspeccionar|analiza|analizar)"
            r"(?:\s+el)?\s+proyecto\s+(?:go|golang)\s+(.+)$",
            original,
            re.I,
        )
        if go_project_inspect:
            return Route(
                "skill",
                skill_name="go.project_inspect",
                params={"path": _strip_quotes(go_project_inspect.group(1).strip())},
            )

        rust_verify = re.match(
            r"^(?:verifica|verificar|valida|validar|revisa|revisar)"
            r"(?:\s+completamente|\s+todo)?(?:\s+el)?\s+proyecto\s+rust\s+(.+)$",
            original,
            re.I,
        )
        if rust_verify:
            return Route(
                "skill",
                skill_name="rust.verify_project",
                params={"path": _strip_quotes(rust_verify.group(1).strip())},
            )

        rust_project_inspect = re.match(
            r"^(?:inspecciona|inspeccionar|analiza|analizar)"
            r"(?:\s+el)?\s+proyecto\s+rust\s+(.+)$",
            original,
            re.I,
        )
        if rust_project_inspect:
            return Route(
                "skill",
                skill_name="rust.project_inspect",
                params={"path": _strip_quotes(rust_project_inspect.group(1).strip())},
            )

        swift_verify = re.match(
            r"^(?:verifica|verificar|valida|validar|revisa|revisar)"
            r"(?:\s+completamente|\s+todo)?(?:\s+el)?\s+proyecto\s+swift\s+(.+)$",
            original,
            re.I,
        )
        if swift_verify:
            return Route(
                "skill",
                skill_name="swift.verify_project",
                params={"path": _strip_quotes(swift_verify.group(1).strip())},
            )

        swift_project_inspect = re.match(
            r"^(?:inspecciona|inspeccionar|analiza|analizar)"
            r"(?:\s+el)?\s+proyecto\s+swift\s+(.+)$",
            original,
            re.I,
        )
        if swift_project_inspect:
            return Route(
                "skill",
                skill_name="swift.project_inspect",
                params={"path": _strip_quotes(swift_project_inspect.group(1).strip())},
            )

        dart_verify = re.match(
            r"^(?:verifica|verificar|valida|validar|revisa|revisar)"
            r"(?:\s+completamente|\s+todo)?(?:\s+el)?\s+proyecto\s+"
            r"(?:dart|flutter)\s+(.+)$",
            original,
            re.I,
        )
        if dart_verify:
            return Route(
                "skill",
                skill_name="dart.verify_project",
                params={"path": _strip_quotes(dart_verify.group(1).strip())},
            )

        dart_project_inspect = re.match(
            r"^(?:inspecciona|inspeccionar|analiza|analizar)"
            r"(?:\s+el)?\s+proyecto\s+(?:dart|flutter)\s+(.+)$",
            original,
            re.I,
        )
        if dart_project_inspect:
            return Route(
                "skill",
                skill_name="dart.project_inspect",
                params={"path": _strip_quotes(dart_project_inspect.group(1).strip())},
            )

        sql_verify = re.match(
            r"^(?:verifica|verificar|valida|validar|revisa|revisar)"
            r"(?:\s+completamente|\s+todo)?(?:\s+el)?\s+proyecto\s+"
            r"(?:sql|sqlite|base\s+de\s+datos)\s+(.+)$",
            original,
            re.I,
        )
        if sql_verify:
            return Route(
                "skill",
                skill_name="sql.verify_project",
                params={"path": _strip_quotes(sql_verify.group(1).strip())},
            )

        sql_project_inspect = re.match(
            r"^(?:inspecciona|inspeccionar|analiza|analizar)"
            r"(?:\s+el)?\s+proyecto\s+(?:sql|sqlite|base\s+de\s+datos)\s+(.+)$",
            original,
            re.I,
        )
        if sql_project_inspect:
            return Route(
                "skill",
                skill_name="sql.project_inspect",
                params={"path": _strip_quotes(sql_project_inspect.group(1).strip())},
            )

        native_verify = re.match(
            r"^(?:verifica|verificar|valida|validar|revisa|revisar)"
            r"(?:\s+completamente|\s+todo)?(?:\s+el)?\s+proyecto\s+"
            r"(?:c/c\+\+|c\+\+|c|native)\s+(.+)$",
            original,
            re.I,
        )
        if native_verify:
            return Route(
                "skill",
                skill_name="native.verify_project",
                params={"path": _strip_quotes(native_verify.group(1).strip())},
            )

        native_inspect = re.match(
            r"^(?:inspecciona|inspeccionar|analiza|analizar)"
            r"(?:\s+el)?\s+proyecto\s+(?:c/c\+\+|c\+\+|c|native)\s+(.+)$",
            original,
            re.I,
        )
        if native_inspect:
            return Route(
                "skill",
                skill_name="native.project_inspect",
                params={"path": _strip_quotes(native_inspect.group(1).strip())},
            )

        dotnet_verify = re.match(
            r"^(?:verifica|verificar|valida|validar|revisa|revisar)"
            r"(?:\s+completamente|\s+todo)?(?:\s+el)?\s+proyecto\s+"
            r"(?:c#|csharp|\.net|dotnet)\s+(.+)$",
            original,
            re.I,
        )
        if dotnet_verify:
            return Route(
                "skill",
                skill_name="dotnet.verify_project",
                params={"path": _strip_quotes(dotnet_verify.group(1).strip())},
            )

        dotnet_project_inspect = re.match(
            r"^(?:inspecciona|inspeccionar|analiza|analizar)"
            r"(?:\s+el)?\s+proyecto\s+(?:c#|csharp|\.net|dotnet)\s+(.+)$",
            original,
            re.I,
        )
        if dotnet_project_inspect:
            return Route(
                "skill",
                skill_name="dotnet.project_inspect",
                params={"path": _strip_quotes(dotnet_project_inspect.group(1).strip())},
            )

        kotlin_verify = re.match(
            r"^(?:verifica|verificar|valida|validar|revisa|revisar)"
            r"(?:\s+completamente|\s+todo)?(?:\s+el)?\s+proyecto\s+kotlin\s+(.+)$",
            original,
            re.I,
        )
        if kotlin_verify:
            return Route(
                "skill",
                skill_name="kotlin.verify_project",
                params={"path": _strip_quotes(kotlin_verify.group(1).strip())},
            )

        kotlin_project_inspect = re.match(
            r"^(?:inspecciona|inspeccionar|analiza|analizar)"
            r"(?:\s+el)?\s+proyecto\s+kotlin\s+(.+)$",
            original,
            re.I,
        )
        if kotlin_project_inspect:
            return Route(
                "skill",
                skill_name="kotlin.project_inspect",
                params={"path": _strip_quotes(kotlin_project_inspect.group(1).strip())},
            )

        java_verify = re.match(
            r"^(?:verifica|verificar|valida|validar|revisa|revisar)"
            r"(?:\s+completamente|\s+todo)?(?:\s+el)?\s+proyecto\s+java\s+(.+)$",
            original,
            re.I,
        )
        if java_verify:
            return Route(
                "skill",
                skill_name="java.verify_project",
                params={"path": _strip_quotes(java_verify.group(1).strip())},
            )

        java_project_inspect = re.match(
            r"^(?:inspecciona|inspeccionar|analiza|analizar)"
            r"(?:\s+el)?\s+proyecto\s+java\s+(.+)$",
            original,
            re.I,
        )
        if java_project_inspect:
            return Route(
                "skill",
                skill_name="java.project_inspect",
                params={"path": _strip_quotes(java_project_inspect.group(1).strip())},
            )

        python_verify = re.match(
            r"^(?:verifica|verificar|valida|validar|revisa|revisar)"
            r"(?:\s+completamente|\s+todo)?(?:\s+el)?\s+proyecto\s+python\s+(.+)$",
            original,
            re.I,
        )
        if python_verify:
            return Route(
                "skill",
                skill_name="python.verify_project",
                params={"path": _strip_quotes(python_verify.group(1).strip())},
            )

        python_project_inspect = re.match(
            r"^(?:inspecciona|inspeccionar|analiza|analizar)"
            r"(?:\s+el)?\s+proyecto\s+python\s+(.+)$",
            original,
            re.I,
        )
        if python_project_inspect:
            return Route(
                "skill",
                skill_name="python.project_inspect",
                params={"path": _strip_quotes(python_project_inspect.group(1).strip())},
            )

        python_compile = re.match(
            r"^(?:compila|compilar|valida|validar|revisa|revisar)"
            r"(?:\s+la)?\s+sintaxis\s+python(?:\s+de|\s+en)?\s+(.+)$",
            original,
            re.I,
        )
        if python_compile:
            return Route(
                "skill",
                skill_name="python.compile_project",
                params={"path": _strip_quotes(python_compile.group(1).strip())},
            )

        for tool_pattern, skill_name in (
            ("ruff", "ruff.check"),
            ("mypy", "mypy.check"),
            ("pytest", "pytest.run"),
        ):
            match = re.match(
                rf"^(?:ejecuta|correr?|corre|valida|validar|revisa|revisar)\s+"
                rf"{tool_pattern}(?:\s+en)?\s+(.+)$",
                original,
                re.I,
            )
            if match:
                return Route(
                    "skill",
                    skill_name=skill_name,
                    params={"path": _strip_quotes(match.group(1).strip())},
                )

        web_verify = re.match(
            r"^(?:verifica|verificar|valida|validar|revisa|revisar)"
            r"(?:\s+completamente|\s+todo)?(?:\s+el)?\s+proyecto\s+web\s+(.+)$",
            original,
            re.I,
        )
        if web_verify:
            return Route(
                "skill",
                skill_name="web.verify_project",
                params={"path": _strip_quotes(web_verify.group(1).strip())},
            )

        web_project_inspect = re.match(
            r"^(?:inspecciona|inspeccionar|analiza|analizar)"
            r"(?:\s+el)?\s+proyecto\s+web\s+(.+)$",
            original,
            re.I,
        )
        if web_project_inspect:
            return Route(
                "skill",
                skill_name="web.project_inspect",
                params={"path": _strip_quotes(web_project_inspect.group(1).strip())},
            )

        web_html = re.match(
            r"^(?:valida|validar|revisa|revisar)(?:\s+la)?\s+estructura\s+html"
            r"(?:\s+de|\s+en)?\s+(.+)$",
            original,
            re.I,
        )
        if web_html:
            return Route(
                "skill",
                skill_name="html.validate",
                params={"path": _strip_quotes(web_html.group(1).strip())},
            )

        web_css = re.match(
            r"^(?:valida|validar|revisa|revisar)(?:\s+la)?\s+sintaxis\s+css"
            r"(?:\s+de|\s+en)?\s+(.+)$",
            original,
            re.I,
        )
        if web_css:
            return Route(
                "skill",
                skill_name="css.validate",
                params={"path": _strip_quotes(web_css.group(1).strip())},
            )

        web_javascript = re.match(
            r"^(?:valida|validar|revisa|revisar)(?:\s+la)?\s+sintaxis\s+"
            r"(?:javascript|js)(?:\s+de|\s+en)?\s+(.+)$",
            original,
            re.I,
        )
        if web_javascript:
            return Route(
                "skill",
                skill_name="javascript.syntax_validate",
                params={"path": _strip_quotes(web_javascript.group(1).strip())},
            )

        web_typescript = re.match(
            r"^(?:ejecuta|correr?|corre|valida|validar|revisa|revisar)\s+"
            r"(?:typescript|tsc)(?:\s+en)?\s+(.+)$",
            original,
            re.I,
        )
        if web_typescript:
            return Route(
                "skill",
                skill_name="typescript.check",
                params={"path": _strip_quotes(web_typescript.group(1).strip())},
            )

        web_framework = re.match(
            r"^(?:valida|validar|revisa|revisar)(?:\s+la)?\s+configuraci[oó]n\s+"
            r"(?:frontend|angular|vite)(?:\s+de|\s+en)?\s+(.+)$",
            original,
            re.I,
        )
        if web_framework:
            return Route(
                "skill",
                skill_name="web.framework_validate",
                params={"path": _strip_quotes(web_framework.group(1).strip())},
            )

        web_eslint = re.match(
            r"^(?:ejecuta|correr?|corre|valida|validar|revisa|revisar)\s+"
            r"eslint(?:\s+en)?\s+(.+)$",
            original,
            re.I,
        )
        if web_eslint:
            return Route(
                "skill",
                skill_name="eslint.lint",
                params={"path": _strip_quotes(web_eslint.group(1).strip())},
            )

        web_stylelint = re.match(
            r"^(?:ejecuta|correr?|corre|valida|validar|revisa|revisar)\s+"
            r"stylelint(?:\s+en)?\s+(.+)$",
            original,
            re.I,
        )
        if web_stylelint:
            return Route(
                "skill",
                skill_name="stylelint.lint",
                params={"path": _strip_quotes(web_stylelint.group(1).strip())},
            )

        php_verify = re.match(
            r"^(?:verifica|verificar|valida|validar|revisa|revisar)"
            r"(?:\s+completamente|\s+todo)?(?:\s+el)?\s+proyecto\s+php\s+(.+)$",
            original,
            re.I,
        )
        if php_verify:
            return Route(
                "skill",
                skill_name="php.verify_project",
                params={"path": _strip_quotes(php_verify.group(1).strip())},
            )

        php_project_inspect = re.match(
            r"^(?:inspecciona|inspeccionar|analiza|analizar)"
            r"(?:\s+el)?\s+proyecto\s+php\s+(.+)$",
            original,
            re.I,
        )
        if php_project_inspect:
            return Route(
                "skill",
                skill_name="php.project_inspect",
                params={"path": _strip_quotes(php_project_inspect.group(1).strip())},
            )

        php_syntax_project = re.match(
            r"^(?:valida|validar|revisa|revisar)"
            r"(?:\s+toda)?(?:\s+la)?\s+sintaxis\s+php"
            r"(?:\s+del)?(?:\s+proyecto)?\s+(.+)$",
            original,
            re.I,
        )
        if php_syntax_project:
            return Route(
                "skill",
                skill_name="php.syntax_scan",
                params={"path": _strip_quotes(php_syntax_project.group(1).strip())},
            )

        php_syntax = re.match(
            r"^(?:(?:ejecuta|correr?|corre)\s+)?(?:php\s+-l|"
            r"valida(?:r)?\s+(?:la\s+)?sintaxis\s+php(?:\s+de)?|"
            r"valida(?:r)?\s+php)\s+(?:en\s+)?(.+)$",
            original,
            re.I,
        )
        if php_syntax:
            return Route(
                "skill",
                skill_name="php.syntax_validate",
                params={"path": _strip_quotes(php_syntax.group(1).strip())},
            )

        composer_validate = re.match(
            r"^(?:(?:ejecuta|correr?|corre)\s+)?(?:composer\s+validate|"
            r"valida(?:r)?\s+(?:el\s+)?composer(?:\.json)?)"
            r"(?:\s+(?:en|de))?\s+(.+)$",
            original,
            re.I,
        )
        if composer_validate:
            return Route(
                "skill",
                skill_name="composer.validate",
                params={"path": _strip_quotes(composer_validate.group(1).strip())},
            )

        phpstan = re.match(
            r"^(?:(?:ejecuta|correr?|corre|analiza|analizar)\s+)"
            r"(?:con\s+)?phpstan(?:\s+en)?\s+(.+)$",
            original,
            re.I,
        )
        if phpstan:
            return Route(
                "skill",
                skill_name="phpstan.analyse",
                params={"path": _strip_quotes(phpstan.group(1).strip())},
            )

        phpunit = re.match(
            r"^(?:(?:ejecuta|correr?|corre|prueba|probar)\s+)"
            r"(?:con\s+)?phpunit(?:\s+en)?\s+(.+)$",
            original,
            re.I,
        )
        if phpunit:
            return Route(
                "skill",
                skill_name="phpunit.run",
                params={"path": _strip_quotes(phpunit.group(1).strip())},
            )

        validate = re.match(
            r"^(?:valida|validar|revisa sintaxis de)\s+(?:el archivo\s+)?(.+)$",
            original,
            re.I,
        )
        if validate:
            return Route(
                "skill",
                skill_name="code.validate",
                params={"path": _strip_quotes(validate.group(1).strip())},
            )

        search = re.match(r"^(?:busca|buscar)\s+(?:el archivo\s+|archivo\s+)?(.+)$", original, re.I)
        if search:
            pattern = _strip_quotes(search.group(1).strip())
            if not any(character in pattern for character in "*?[]"):
                pattern = f"*{pattern}*"
            return Route("skill", skill_name="files.search", params={"pattern": pattern})

        return Route("fallback")


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.split())


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
