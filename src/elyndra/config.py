from __future__ import annotations

import getpass
import tomllib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from elyndra.paths import ElyndraPaths


class ConfigError(RuntimeError):
    pass


_DEFAULT_KNOWLEDGE_EXTENSIONS = (
    ".txt",
    ".md",
    ".rst",
    ".py",
    ".php",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".cs",
    ".fs",
    ".vb",
    ".swift",
    ".dart",
    ".csproj",
    ".fsproj",
    ".vbproj",
    ".sln",
    ".slnx",
    ".kt",
    ".kts",
    ".css",
    ".scss",
    ".html",
    ".htm",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".xml",
    ".sql",
    ".sh",
    ".ini",
    ".conf",
    ".properties",
)


@dataclass(frozen=True, slots=True)
class AppConfig:
    agent_name: str
    language: str
    owner_name: str
    system_user: str
    offline: bool
    telemetry: bool
    network_allowed: bool
    max_search_results: int
    command_timeout_seconds: int
    php_tool_timeout_seconds: int
    php_tool_max_output_chars: int
    python_tool_timeout_seconds: int
    python_tool_max_output_chars: int
    java_tool_timeout_seconds: int
    java_tool_max_output_chars: int
    kotlin_tool_timeout_seconds: int
    kotlin_tool_max_output_chars: int
    dotnet_tool_timeout_seconds: int
    dotnet_tool_max_output_chars: int
    native_tool_timeout_seconds: int
    native_tool_max_output_chars: int
    ruby_tool_timeout_seconds: int
    ruby_tool_max_output_chars: int
    go_tool_timeout_seconds: int
    go_tool_max_output_chars: int
    rust_tool_timeout_seconds: int
    rust_tool_max_output_chars: int
    sql_tool_timeout_seconds: int
    sql_tool_max_output_chars: int
    swift_tool_timeout_seconds: int
    swift_tool_max_output_chars: int
    dart_tool_timeout_seconds: int
    dart_tool_max_output_chars: int
    allowed_roots: tuple[Path, ...]
    knowledge_max_file_size_mb: int
    knowledge_chunk_size_chars: int
    knowledge_chunk_overlap_chars: int
    knowledge_allowed_extensions: tuple[str, ...]
    file_read_max_lines: int
    project_scan_max_files: int
    ethical_advice_enabled: bool = True
    ethical_tutor_review_enabled: bool = True

    @classmethod
    def load(cls, paths: ElyndraPaths) -> AppConfig:
        if not paths.config_file.exists():
            raise ConfigError(
                f"No existe {paths.config_file}. Ejecuta: elyndra init --owner TU_NOMBRE"
            )

        with paths.config_file.open("rb") as handle:
            raw = tomllib.load(handle)

        try:
            roots = tuple(
                Path(value).expanduser().resolve()
                for value in raw["filesystem"]["allowed_roots"]
            )
            if not roots:
                raise ValueError("allowed_roots cannot be empty")

            knowledge = raw.get("knowledge", {})
            extensions = tuple(
                _normalize_extension(str(value))
                for value in knowledge.get("allowed_extensions", _DEFAULT_KNOWLEDGE_EXTENSIONS)
            )
            extensions = tuple(dict.fromkeys(value for value in extensions if value))
            if not extensions:
                extensions = _DEFAULT_KNOWLEDGE_EXTENSIONS

            chunk_size = max(400, int(knowledge.get("chunk_size_chars", 1800)))
            overlap = max(0, int(knowledge.get("chunk_overlap_chars", 200)))
            if overlap >= chunk_size:
                raise ValueError(
                    "knowledge.chunk_overlap_chars debe ser menor que chunk_size_chars"
                )

            ethics = raw.get("ethics", {})
            skills = raw.get("skills", {})
            php_skills = skills.get("php", {}) if isinstance(skills, dict) else {}
            python_skills = skills.get("python", {}) if isinstance(skills, dict) else {}
            java_skills = skills.get("java", {}) if isinstance(skills, dict) else {}
            kotlin_skills = skills.get("kotlin", {}) if isinstance(skills, dict) else {}
            dotnet_skills = skills.get("dotnet", {}) if isinstance(skills, dict) else {}
            native_skills = skills.get("native", {}) if isinstance(skills, dict) else {}
            ruby_skills = skills.get("ruby", {}) if isinstance(skills, dict) else {}
            go_skills = skills.get("go", {}) if isinstance(skills, dict) else {}
            rust_skills = skills.get("rust", {}) if isinstance(skills, dict) else {}
            sql_skills = skills.get("sql", {}) if isinstance(skills, dict) else {}
            swift_skills = skills.get("swift", {}) if isinstance(skills, dict) else {}
            dart_skills = skills.get("dart", {}) if isinstance(skills, dict) else {}

            config = cls(
                agent_name=str(raw["agent"]["name"]),
                language=str(raw["agent"].get("language", "es-CL")),
                owner_name=str(raw["owner"]["name"]),
                system_user=str(raw["owner"]["system_user"]),
                offline=bool(raw["privacy"].get("offline", True)),
                telemetry=bool(raw["privacy"].get("telemetry", False)),
                network_allowed=bool(raw["privacy"].get("network_allowed", False)),
                max_search_results=max(1, int(raw["limits"].get("max_search_results", 50))),
                command_timeout_seconds=max(
                    1, int(raw["limits"].get("command_timeout_seconds", 20))
                ),
                php_tool_timeout_seconds=max(
                    5,
                    min(900, int(php_skills.get("timeout_seconds", 120))),
                ),
                php_tool_max_output_chars=max(
                    1000,
                    min(50000, int(php_skills.get("max_output_chars", 12000))),
                ),
                python_tool_timeout_seconds=max(
                    5,
                    min(900, int(python_skills.get("timeout_seconds", 180))),
                ),
                python_tool_max_output_chars=max(
                    1000,
                    min(50000, int(python_skills.get("max_output_chars", 12000))),
                ),
                java_tool_timeout_seconds=max(
                    5,
                    min(900, int(java_skills.get("timeout_seconds", 240))),
                ),
                java_tool_max_output_chars=max(
                    1000,
                    min(50000, int(java_skills.get("max_output_chars", 12000))),
                ),
                kotlin_tool_timeout_seconds=max(
                    5,
                    min(900, int(kotlin_skills.get("timeout_seconds", 240))),
                ),
                kotlin_tool_max_output_chars=max(
                    1000,
                    min(50000, int(kotlin_skills.get("max_output_chars", 12000))),
                ),
                dotnet_tool_timeout_seconds=max(
                    5,
                    min(900, int(dotnet_skills.get("timeout_seconds", 300))),
                ),
                dotnet_tool_max_output_chars=max(
                    1000,
                    min(50000, int(dotnet_skills.get("max_output_chars", 12000))),
                ),
                native_tool_timeout_seconds=max(
                    5,
                    min(900, int(native_skills.get("timeout_seconds", 240))),
                ),
                native_tool_max_output_chars=max(
                    1000,
                    min(50000, int(native_skills.get("max_output_chars", 12000))),
                ),
                ruby_tool_timeout_seconds=max(
                    5,
                    min(900, int(ruby_skills.get("timeout_seconds", 240))),
                ),
                ruby_tool_max_output_chars=max(
                    1000,
                    min(50000, int(ruby_skills.get("max_output_chars", 12000))),
                ),
                go_tool_timeout_seconds=max(
                    5,
                    min(900, int(go_skills.get("timeout_seconds", 240))),
                ),
                go_tool_max_output_chars=max(
                    1000,
                    min(50000, int(go_skills.get("max_output_chars", 12000))),
                ),
                rust_tool_timeout_seconds=max(
                    5,
                    min(900, int(rust_skills.get("timeout_seconds", 300))),
                ),
                rust_tool_max_output_chars=max(
                    1000,
                    min(50000, int(rust_skills.get("max_output_chars", 12000))),
                ),
                sql_tool_timeout_seconds=max(
                    5,
                    min(900, int(sql_skills.get("timeout_seconds", 120))),
                ),
                sql_tool_max_output_chars=max(
                    1000,
                    min(50000, int(sql_skills.get("max_output_chars", 12000))),
                ),
                swift_tool_timeout_seconds=max(
                    5,
                    min(900, int(swift_skills.get("timeout_seconds", 300))),
                ),
                swift_tool_max_output_chars=max(
                    1000,
                    min(50000, int(swift_skills.get("max_output_chars", 12000))),
                ),
                dart_tool_timeout_seconds=max(
                    5,
                    min(900, int(dart_skills.get("timeout_seconds", 300))),
                ),
                dart_tool_max_output_chars=max(
                    1000,
                    min(50000, int(dart_skills.get("max_output_chars", 12000))),
                ),
                allowed_roots=roots,
                knowledge_max_file_size_mb=max(
                    1, int(knowledge.get("max_file_size_mb", 5))
                ),
                knowledge_chunk_size_chars=chunk_size,
                knowledge_chunk_overlap_chars=overlap,
                knowledge_allowed_extensions=extensions,
                file_read_max_lines=max(
                    20, int(raw.get("files", {}).get("read_max_lines", 250))
                ),
                project_scan_max_files=max(
                    100, int(raw.get("projects", {}).get("scan_max_files", 5000))
                ),
                ethical_advice_enabled=bool(
                    ethics.get("proactive_advice", True)
                    if isinstance(ethics, dict)
                    else True
                ),
                ethical_tutor_review_enabled=bool(
                    ethics.get("tutor_review", True)
                    if isinstance(ethics, dict)
                    else True
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"Configuración inválida en {paths.config_file}: {exc}") from exc

        if config.telemetry:
            raise ConfigError("Elyndra no admite telemetría activada.")
        if config.network_allowed:
            raise ConfigError("Elyndra 0.2 todavía no admite acceso de red activado.")
        return config


def default_config_text(owner_name: str, system_user: str | None = None) -> str:
    current_user = system_user or getpass.getuser()
    projects_root = Path.home() / "Proyectos"
    extensions = ",\n  ".join(f'"{value}"' for value in _DEFAULT_KNOWLEDGE_EXTENSIONS)
    return f'''[agent]
name = "Elyn"
language = "es-CL"

[owner]
name = "{_toml_escape(owner_name)}"
system_user = "{_toml_escape(current_user)}"

[privacy]
offline = true
telemetry = false
network_allowed = false

[ethics]
# La constitución de no-daño es inmutable. Esta opción solo controla
# recomendaciones proactivas adicionales.
proactive_advice = true
# Ollama u otro motor local solo revisa casos ambiguos. Nunca puede
# debilitar un bloqueo determinista ni conceder permisos.
tutor_review = true

[limits]
max_search_results = 50
command_timeout_seconds = 20

[filesystem]
allowed_roots = [
  "{_toml_escape(str(projects_root))}",
]

[files]
read_max_lines = 250

[projects]
scan_max_files = 5000

[skills.php]
timeout_seconds = 120
max_output_chars = 12000

[skills.python]
timeout_seconds = 180
max_output_chars = 12000

[skills.java]
timeout_seconds = 240
max_output_chars = 12000

[skills.kotlin]
timeout_seconds = 240
max_output_chars = 12000

[skills.dotnet]
timeout_seconds = 300
max_output_chars = 12000

[skills.native]
timeout_seconds = 240
max_output_chars = 12000

[skills.ruby]
timeout_seconds = 240
max_output_chars = 12000

[skills.go]
timeout_seconds = 240
max_output_chars = 12000

[skills.rust]
timeout_seconds = 300
max_output_chars = 12000

[skills.swift]
timeout_seconds = 300
max_output_chars = 12000

[skills.dart]
timeout_seconds = 300
max_output_chars = 12000

[skills.sql]
timeout_seconds = 120
max_output_chars = 12000

[knowledge]
max_file_size_mb = 5
chunk_size_chars = 1800
chunk_overlap_chars = 200
allowed_extensions = [
  {extensions}
]
'''


def write_default_config(
    paths: ElyndraPaths,
    owner_name: str,
    system_user: str | None = None,
    *,
    force: bool = False,
) -> Path:
    paths.ensure()
    target = paths.config_file
    if target.exists() and not force:
        raise ConfigError(f"La configuración ya existe: {target}")
    target.write_text(default_config_text(owner_name, system_user), encoding="utf-8")
    with suppress(PermissionError):
        target.chmod(0o600)
    return target


def _normalize_extension(value: str) -> str:
    clean = value.strip().casefold()
    if not clean:
        return ""
    return clean if clean.startswith(".") else f".{clean}"


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
