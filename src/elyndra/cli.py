from __future__ import annotations

import argparse
import getpass
import json
import shutil
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any

from elyndra import __version__
from elyndra.application import ElyndraApplication
from elyndra.chat_ui import (
    command_hint,
    format_duration,
    is_exit_command,
    run_with_progress,
)
from elyndra.config import AppConfig, ConfigError, write_default_config
from elyndra.db import Database
from elyndra.documents import document_capabilities
from elyndra.engines import ConversationTurn
from elyndra.engines.ollama_local import (
    fetch_ollama_models,
    fetch_ollama_running,
    fetch_ollama_version,
)
from elyndra.ethics import ethics_status, principles
from elyndra.identity import IdentityError
from elyndra.language_packs import LanguagePackBuilder
from elyndra.language_packs.bundles import DEFAULT_QUERY_PRIORITIES, LanguageBundleService
from elyndra.languages import (
    LANGUAGE_NAMES,
    detect_language,
    language_name,
    resolve_language,
)
from elyndra.models import (
    PROFILES,
    LanguageConfig,
    LanguageConfigError,
    disable_language_config,
    discover_local_models,
    update_interaction_language,
    write_language_config,
    write_ollama_language_config,
)
from elyndra.online_gateway import GatewayError
from elyndra.online_gateway.operations import _issue_cli_execution_capability
from elyndra.paths import ElyndraPaths
from elyndra.persona import (
    AgentPersona,
    PersonaConfigError,
    write_default_persona,
    write_persona,
)
from elyndra.personal_organizer import local_today, render_daily_brief
from elyndra.semantic_intents import available_intents
from elyndra.session_continuity import build_session_guidance
from elyndra.skills.path_safety import ensure_allowed
from elyndra.skills.php_tools import php_tool_capabilities
from elyndra.tutors import validate_tutor_task
from elyndra.web import run_web_interface
from elyndra.wellbeing import render_wellbeing_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="elyndra",
        description="Elyndra: núcleo local y privado de agente personal.",
    )
    parser.add_argument("--json", action="store_true", help="Salida JSON cuando sea posible.")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="Crear configuración y base local.")
    init_parser.add_argument("--owner", required=True, help="Nombre visible del propietario.")
    init_parser.add_argument("--system-user", help="Usuario Linux autorizado; por defecto, actual.")
    init_parser.add_argument("--force", action="store_true", help="Reemplazar config existente.")

    account_parser = sub.add_parser("account", help="Registro, login y perfil local.")
    account_sub = account_parser.add_subparsers(dest="account_command", required=True)
    account_register = account_sub.add_parser(
        "register", help="Registrar una cuenta local aislada."
    )
    account_register.add_argument("--username", required=True)
    account_register.add_argument("--email", required=True)
    account_register.add_argument("--birth-date", required=True)
    account_register.add_argument("--password")
    account_register.add_argument("--confirm-password")
    account_register.add_argument("--preferred-name", default="")
    account_register.add_argument("--developer-mode", action="store_true")
    account_register.add_argument("--telemetry", action="store_true")
    account_register.add_argument("--approve", action="store_true", required=True)
    account_login = account_sub.add_parser("login", help="Iniciar una sesión CLI local.")
    account_login.add_argument("login")
    account_login.add_argument("--password")
    account_sub.add_parser("logout", help="Cerrar la sesión CLI local.")
    account_sub.add_parser("status", help="Mostrar cuenta y estado de sesión.")
    account_sub.add_parser("profile", help="Mostrar perfil local.")
    account_update = account_sub.add_parser("profile-update", help="Actualizar perfil.")
    account_update.add_argument("--preferred-name")
    account_update.add_argument("--pronouns")
    account_update.add_argument("--sex")
    account_update.add_argument("--gender-identity")
    account_update.add_argument("--sexual-orientation")
    account_update.add_argument("--timezone")
    account_update.add_argument("--language")
    account_update.add_argument("--developer-mode", action=argparse.BooleanOptionalAction)
    account_update.add_argument("--telemetry", action=argparse.BooleanOptionalAction)
    account_update.add_argument("--birthday-greeting", action=argparse.BooleanOptionalAction)
    account_update.add_argument("--approve", action="store_true", required=True)
    account_sub.add_parser("security", help="Estado de seguridad y 2FA.")
    account_change_email = account_sub.add_parser("change-email")
    account_change_email.add_argument("email")
    account_change_email.add_argument("--password")
    account_change_email.add_argument("--approve", action="store_true", required=True)
    account_change_password = account_sub.add_parser("change-password")
    account_change_password.add_argument("--current-password")
    account_change_password.add_argument("--new-password")
    account_change_password.add_argument("--confirm-password")
    account_change_password.add_argument("--approve", action="store_true", required=True)
    account_reset_password = account_sub.add_parser(
        "reset-password-local",
        help="Restablecer la contraseña desde el mismo usuario local del sistema.",
    )
    account_reset_password.add_argument(
        "--login",
        "--username",
        "--email",
        dest="login",
        required=True,
        help="Nombre de usuario o correo de la cuenta local.",
    )
    account_reset_password.add_argument("--new-password")
    account_reset_password.add_argument("--confirm-password")
    account_reset_password.add_argument("--approve", action="store_true", required=True)
    account_export = account_sub.add_parser("export", help="Exportación cifrada local.")
    account_export.add_argument("path", type=Path)
    account_export.add_argument("--password")
    account_export.add_argument("--export-passphrase")
    account_export.add_argument("--approve", action="store_true", required=True)
    account_sub.add_parser("telemetry-preview", help="Vista previa del payload permitido.")

    sub.add_parser("doctor", help="Revisar instalación, privacidad y herramientas.")
    sub.add_parser("status", help="Consultar recursos del equipo.")

    online_parser = sub.add_parser("online", help="Gateway online controlado (sin transporte).")
    online_sub = online_parser.add_subparsers(dest="online_command", required=True)
    online_sub.add_parser("status")
    online_sub.add_parser("mode")
    online_mode_set = online_sub.add_parser("mode-set")
    online_mode_set.add_argument("mode", choices=("local", "online"))
    online_mode_set.add_argument("--approve", action="store_true", required=True)
    online_sub.add_parser("sources")
    online_source_show = online_sub.add_parser("source-show")
    online_source_show.add_argument("source_id")
    online_sub.add_parser("operations")
    online_operation_show = online_sub.add_parser("operation-show")
    online_operation_show.add_argument("operation_id")
    online_plan = online_sub.add_parser(
        "plan-download", help="Preview read-only; no crea aprobación ni usa red."
    )
    online_plan.add_argument("source_id")
    online_approve = online_sub.add_parser(
        "approve-download", help="Persiste el plan aprobado; no inicia la descarga."
    )
    online_approve.add_argument("source_id")
    online_approve.add_argument("plan_digest")
    online_approve.add_argument("--approve", action="store_true", required=True)
    online_clear = online_sub.add_parser("history-clear")
    online_clear.add_argument("--approve", action="store_true", required=True)
    online_execute = online_sub.add_parser("execute-download")
    online_execute.add_argument("operation_id")
    online_execute.add_argument("--approve", action="store_true", required=True)
    online_resume = online_sub.add_parser("resume-download")
    online_resume.add_argument("operation_id")
    online_resume.add_argument("--approve", action="store_true", required=True)
    online_discard = online_sub.add_parser("discard-partial")
    online_discard.add_argument("operation_id")
    online_cancel = online_sub.add_parser("cancel-download")
    online_cancel.add_argument("operation_id")
    online_cancel.add_argument("--approve", action="store_true", required=True)
    online_discard.add_argument("--approve", action="store_true", required=True)
    online_cache_show = online_sub.add_parser("cache-show")
    online_cache_show.add_argument("artifact_key")
    online_cache_verify = online_sub.add_parser("cache-verify")
    online_cache_verify.add_argument("artifact_key")
    online_sub.add_parser("quarantine")
    online_descriptor = online_sub.add_parser("descriptor-show")
    online_descriptor.add_argument("source_id")
    online_bundle_inspect = online_sub.add_parser("bundle-inspect")
    online_bundle_inspect.add_argument("source_id")
    online_asset_plan = online_sub.add_parser("plan-asset-download")
    online_asset_plan.add_argument("source_id")
    online_asset_plan.add_argument("artifact_key")
    online_bundle_prepare = online_sub.add_parser("bundle-prepare")
    online_bundle_prepare.add_argument("source_id")
    online_bundle_prepare.add_argument("--approve", action="store_true", required=True)
    online_bundle_install = online_sub.add_parser("bundle-install")
    online_bundle_install.add_argument("operation_id")
    online_bundle_install.add_argument("--approve", action="store_true", required=True)
    online_bundle_cancel = online_sub.add_parser("bundle-install-cancel")
    online_bundle_cancel.add_argument("operation_id")
    online_bundle_cancel.add_argument("--approve", action="store_true", required=True)
    online_bundle_status = online_sub.add_parser("bundle-install-status")
    online_bundle_status.add_argument("operation_id")

    web_parser = sub.add_parser("web", help="Abrir la interfaz web local de Elyndra.")
    web_parser.add_argument("--port", type=int, default=8765)
    web_parser.add_argument(
        "--no-open",
        action="store_true",
        help="No abrir automáticamente el navegador.",
    )

    chat_parser = sub.add_parser(
        "chat", help="Abrir o administrar conversaciones locales aisladas."
    )
    chat_sub = chat_parser.add_subparsers(dest="chat_command")
    chat_new = chat_sub.add_parser("new", help="Crear y abrir un chat nuevo.")
    chat_new.add_argument("--title")
    chat_new.add_argument("--project")
    chat_new.add_argument(
        "--transcript",
        choices=("summary", "full"),
        default="summary",
        help="summary guarda solo un resumen; full conserva los turnos completos en SQLite.",
    )
    chat_open = chat_sub.add_parser("open", help="Reabrir un chat existente.")
    chat_open.add_argument("id")
    chat_list = chat_sub.add_parser("list", help="Listar chats activos.")
    chat_list.add_argument("--limit", type=int, default=30)
    chat_show = chat_sub.add_parser("show", help="Mostrar metadatos y resumen de un chat.")
    chat_show.add_argument("id")
    chat_search = chat_sub.add_parser("search", help="Buscar en títulos y resúmenes.")
    chat_search.add_argument("query")
    chat_search.add_argument("--limit", type=int, default=20)
    chat_forget = chat_sub.add_parser("forget", help="Eliminar lógicamente un chat.")
    chat_forget.add_argument("id")
    chat_forget.add_argument("--approve", action="store_true", required=True)
    chat_archive = chat_sub.add_parser(
        "archive", help="Comprimir una transcripción completa en almacenamiento frío."
    )
    chat_archive.add_argument("id")
    chat_archive.add_argument(
        "--prune",
        action="store_true",
        help="Eliminar los turnos completos de SQLite después de verificar el archivo.",
    )
    chat_archive.add_argument("--approve", action="store_true", required=True)
    chat_archives = chat_sub.add_parser("archives", help="Listar archivos fríos creados.")
    chat_archives.add_argument("--limit", type=int, default=50)

    ask_parser = sub.add_parser("ask", help="Procesar una orden local.")
    ask_parser.add_argument("text", help="Orden o consulta.")
    ask_parser.add_argument("--approve", action="store_true")

    remember_parser = sub.add_parser("remember", help="Guardar un recuerdo explícito.")
    remember_parser.add_argument("content")
    remember_parser.add_argument("--kind", default="fact")
    remember_parser.add_argument("--project")

    recall_parser = sub.add_parser("recall", help="Buscar recuerdos.")
    recall_parser.add_argument("query")
    recall_parser.add_argument("--limit", type=int, default=20)

    memory_parser = sub.add_parser("memory", help="Administrar memoria.")
    memory_sub = memory_parser.add_subparsers(dest="memory_command", required=True)
    memory_list = memory_sub.add_parser("list")
    memory_list.add_argument("--limit", type=int, default=50)
    memory_forget = memory_sub.add_parser("forget")
    memory_forget.add_argument("id", type=int)
    memory_forget.add_argument("--approve", action="store_true", required=True)
    memory_edit = memory_sub.add_parser("edit", help="Corregir un recuerdo activo.")
    memory_edit.add_argument("id", type=int)
    memory_edit.add_argument("content")
    memory_edit.add_argument("--kind")
    memory_edit.add_argument("--project")
    memory_edit.add_argument("--approve", action="store_true", required=True)
    memory_episodes = memory_sub.add_parser(
        "episodes", help="Listar decisiones, pendientes, problemas y resultados."
    )
    memory_episodes.add_argument("--chat")
    memory_episodes.add_argument("--kind")
    memory_episodes.add_argument("--limit", type=int, default=50)
    memory_episode_edit = memory_sub.add_parser(
        "episode-edit", help="Corregir un episodio persistente."
    )
    memory_episode_edit.add_argument("id", type=int)
    memory_episode_edit.add_argument("content")
    memory_episode_edit.add_argument("--kind")
    memory_episode_edit.add_argument("--approve", action="store_true", required=True)
    memory_episode_forget = memory_sub.add_parser(
        "episode-forget", help="Eliminar lógicamente un episodio."
    )
    memory_episode_forget.add_argument("id", type=int)
    memory_episode_forget.add_argument("--approve", action="store_true", required=True)
    memory_corrections = memory_sub.add_parser(
        "corrections", help="Listar correcciones guardadas por el propietario."
    )
    memory_corrections.add_argument("--chat")
    memory_corrections.add_argument("--limit", type=int, default=50)
    memory_proposals = memory_sub.add_parser(
        "proposals", help="Listar propuestas revisables de memoria semántica."
    )
    memory_proposals.add_argument(
        "--status", choices=("pending", "approved", "rejected", "all"), default="pending"
    )
    memory_proposals.add_argument("--limit", type=int, default=50)
    memory_proposal_edit = memory_sub.add_parser(
        "proposal-edit", help="Corregir una propuesta antes de aprobarla."
    )
    memory_proposal_edit.add_argument("id", type=int)
    memory_proposal_edit.add_argument("content")
    memory_proposal_edit.add_argument("--approve", action="store_true", required=True)
    memory_approve = memory_sub.add_parser(
        "approve", help="Promover una propuesta revisada a memoria semántica."
    )
    memory_approve.add_argument("id", type=int)
    memory_approve.add_argument("--approve", action="store_true", required=True)
    memory_reject = memory_sub.add_parser("reject", help="Rechazar una propuesta.")
    memory_reject.add_argument("id", type=int)
    memory_reject.add_argument("--approve", action="store_true", required=True)
    memory_sub.add_parser(
        "tiers", help="Mostrar memoria hot, warm y cold sin cargar toda la base en RAM."
    )
    memory_tier_recall = memory_sub.add_parser(
        "tier-recall", help="Recuperar contexto por niveles con latencia visible."
    )
    memory_tier_recall.add_argument("query")
    memory_tier_recall.add_argument("--project")
    memory_tier_recall.add_argument("--chat")
    memory_tier_recall.add_argument("--limit", type=int, default=8)
    memory_consolidate = memory_sub.add_parser(
        "consolidate", help="Indexar episodios antiguos en memoria cold preservando procedencia."
    )
    memory_consolidate.add_argument("--min-age-days", type=int, default=30)
    memory_consolidate.add_argument("--approve", action="store_true", required=True)
    memory_recalls = memory_sub.add_parser(
        "recalls", help="Listar métricas recientes de recuperación sin prompts crudos."
    )
    memory_recalls.add_argument("--limit", type=int, default=20)
    memory_cold_forget = memory_sub.add_parser(
        "cold-forget", help="Eliminar lógicamente una entrada del índice cold."
    )
    memory_cold_forget.add_argument("id", type=int)
    memory_cold_forget.add_argument("--approve", action="store_true", required=True)

    preferences_parser = sub.add_parser(
        "preferences", help="Aprendizaje revisable de preferencias sin promoción silenciosa."
    )
    preferences_sub = preferences_parser.add_subparsers(dest="preferences_command", required=True)
    preferences_sub.add_parser("status", help="Mostrar estado del aprendizaje revisable.")
    preferences_propose = preferences_sub.add_parser(
        "propose", help="Crear una propuesta de preferencia para revisión."
    )
    preferences_propose.add_argument("content")
    preferences_propose.add_argument(
        "--category",
        choices=(
            "general",
            "style",
            "workflow",
            "tools",
            "content",
            "dietary",
            "accessibility",
            "locale",
        ),
        default="general",
    )
    preferences_propose.add_argument("--scope", choices=("global", "project"), default="global")
    preferences_propose.add_argument("--project")
    preferences_propose.add_argument("--expires-days", type=int)
    preferences_proposals = preferences_sub.add_parser(
        "proposals", help="Listar propuestas detectadas o creadas explícitamente."
    )
    preferences_proposals.add_argument(
        "--status", choices=("pending", "approved", "rejected", "all"), default="pending"
    )
    preferences_proposals.add_argument("--limit", type=int, default=50)
    preferences_edit = preferences_sub.add_parser(
        "edit", help="Editar una propuesta pendiente antes de aprobarla."
    )
    preferences_edit.add_argument("id", type=int)
    preferences_edit.add_argument("content")
    preferences_edit.add_argument("--category")
    preferences_edit.add_argument("--scope", choices=("global", "project"))
    preferences_edit.add_argument("--project")
    preferences_edit.add_argument("--expires-days", type=int)
    preferences_edit.add_argument("--clear-expiration", action="store_true")
    preferences_edit.add_argument("--approve", action="store_true", required=True)
    preferences_approve = preferences_sub.add_parser(
        "approve", help="Promover una propuesta revisada a preferencia durable."
    )
    preferences_approve.add_argument("id", type=int)
    preferences_approve.add_argument("--approve", action="store_true", required=True)
    preferences_reject = preferences_sub.add_parser("reject", help="Rechazar una propuesta.")
    preferences_reject.add_argument("id", type=int)
    preferences_reject.add_argument("--approve", action="store_true", required=True)
    preferences_list = preferences_sub.add_parser("list", help="Listar preferencias revisadas.")
    preferences_list.add_argument(
        "--status", choices=("active", "expired", "deleted", "all"), default="active"
    )
    preferences_list.add_argument("--project")
    preferences_list.add_argument("--limit", type=int, default=100)
    preferences_forget = preferences_sub.add_parser(
        "forget", help="Olvidar una preferencia revisada y su memoria semántica."
    )
    preferences_forget.add_argument("public_id")
    preferences_forget.add_argument("--approve", action="store_true", required=True)
    preferences_expire = preferences_sub.add_parser(
        "expire", help="Aplicar expiraciones vencidas de forma explícita."
    )
    preferences_expire.add_argument("--approve", action="store_true", required=True)

    project_parser = sub.add_parser("project", help="Administrar proyectos autorizados.")
    project_sub = project_parser.add_subparsers(dest="project_command", required=True)
    project_add = project_sub.add_parser("add")
    project_add.add_argument("name")
    project_add.add_argument("path", type=Path)
    project_add.add_argument("--approve", action="store_true", required=True)
    project_sub.add_parser("list")
    project_open = project_sub.add_parser("open")
    project_open.add_argument("name")
    project_open.add_argument("--approve", action="store_true", required=True)
    project_inspect = project_sub.add_parser("inspect")
    project_inspect.add_argument("name")
    project_search = project_sub.add_parser("search")
    project_search.add_argument("name")
    project_search.add_argument("query")
    project_search.add_argument("--limit", type=int, default=20)
    project_trust = project_sub.add_parser(
        "trust", help="Registrar una raíz de proyecto confiable persistente."
    )
    project_trust.add_argument("path", type=Path)
    project_trust.add_argument("--approve", action="store_true", required=True)
    project_untrust = project_sub.add_parser(
        "untrust", help="Revocar una raíz de proyecto confiable."
    )
    project_untrust.add_argument("path", type=Path)
    project_untrust.add_argument("--approve", action="store_true", required=True)
    project_sub.add_parser("trusted", help="Listar proyectos confiables persistentes.")
    project_trust_inspect = project_sub.add_parser(
        "trust-inspect", help="Inspeccionar la confianza de una ruta."
    )
    project_trust_inspect.add_argument("path", type=Path)
    project_sub.add_parser("profiles", help="Listar perfiles PHP por proyecto.")
    project_profile_show = project_sub.add_parser(
        "profile-show", help="Mostrar el perfil PHP efectivo de un proyecto."
    )
    project_profile_show.add_argument("path", type=Path)
    project_profile_set = project_sub.add_parser(
        "profile-set", help="Guardar valores PHP seguros para un proyecto autorizado."
    )
    project_profile_set.add_argument("path", type=Path)
    project_profile_set.add_argument("--phpstan-config")
    project_profile_set.add_argument(
        "--phpstan-level", choices=tuple(str(value) for value in range(11)) + ("max",)
    )
    project_profile_set.add_argument("--phpunit-config")
    project_profile_set.add_argument("--phpunit-testsuite")
    project_profile_set.add_argument(
        "--composer-strict", action=argparse.BooleanOptionalAction, default=None
    )
    project_profile_set.add_argument(
        "--composer-enabled", action=argparse.BooleanOptionalAction, default=None
    )
    project_profile_set.add_argument(
        "--syntax-scan-enabled", action=argparse.BooleanOptionalAction, default=None
    )
    project_profile_set.add_argument(
        "--phpstan-enabled", action=argparse.BooleanOptionalAction, default=None
    )
    project_profile_set.add_argument(
        "--phpunit-enabled", action=argparse.BooleanOptionalAction, default=None
    )
    project_profile_set.add_argument(
        "--fail-fast", action=argparse.BooleanOptionalAction, default=None
    )
    project_profile_set.add_argument(
        "--require-tools", action=argparse.BooleanOptionalAction, default=None
    )
    project_profile_set.add_argument("--max-php-files", type=int)
    project_profile_set.add_argument("--exclude-path", action="append", dest="exclude_paths")
    project_profile_set.add_argument("--timeout", type=int)
    project_profile_set.add_argument("--output-limit", type=int)
    project_profile_set.add_argument("--approve", action="store_true", required=True)
    project_profile_delete = project_sub.add_parser(
        "profile-delete", help="Eliminar el perfil PHP de un proyecto."
    )
    project_profile_delete.add_argument("path", type=Path)
    project_profile_delete.add_argument("--approve", action="store_true", required=True)

    project_sub.add_parser("web-profiles", help="Listar perfiles web por proyecto.")
    project_web_profile_show = project_sub.add_parser(
        "web-profile-show", help="Mostrar el perfil web efectivo de un proyecto."
    )
    project_web_profile_show.add_argument("path", type=Path)
    project_web_profile_set = project_sub.add_parser(
        "web-profile-set", help="Guardar valores web seguros para un proyecto autorizado."
    )
    project_web_profile_set.add_argument("path", type=Path)
    project_web_profile_set.add_argument(
        "--html", action=argparse.BooleanOptionalAction, default=None
    )
    project_web_profile_set.add_argument(
        "--css", action=argparse.BooleanOptionalAction, default=None
    )
    project_web_profile_set.add_argument(
        "--javascript", action=argparse.BooleanOptionalAction, default=None
    )
    project_web_profile_set.add_argument(
        "--typescript", action=argparse.BooleanOptionalAction, default=None
    )
    project_web_profile_set.add_argument(
        "--eslint", action=argparse.BooleanOptionalAction, default=None
    )
    project_web_profile_set.add_argument(
        "--stylelint", action=argparse.BooleanOptionalAction, default=None
    )
    project_web_profile_set.add_argument(
        "--framework-checks", action=argparse.BooleanOptionalAction, default=None
    )
    project_web_profile_set.add_argument(
        "--framework-preset",
        choices=(
            "auto",
            "generic",
            "angular",
            "vite",
            "react",
            "vue",
            "svelte",
            "astro",
            "next",
            "nuxt",
        ),
    )
    project_web_profile_set.add_argument("--eslint-config")
    project_web_profile_set.add_argument("--stylelint-config")
    project_web_profile_set.add_argument(
        "--fail-fast", action=argparse.BooleanOptionalAction, default=None
    )
    project_web_profile_set.add_argument(
        "--require-tools", action=argparse.BooleanOptionalAction, default=None
    )
    project_web_profile_set.add_argument("--max-files", type=int)
    project_web_profile_set.add_argument(
        "--exclude-path", action="append", dest="web_exclude_paths"
    )
    project_web_profile_set.add_argument("--timeout", type=int)
    project_web_profile_set.add_argument("--output-limit", type=int)
    project_web_profile_set.add_argument("--approve", action="store_true", required=True)
    project_web_profile_delete = project_sub.add_parser(
        "web-profile-delete", help="Eliminar el perfil web de un proyecto."
    )
    project_web_profile_delete.add_argument("path", type=Path)
    project_web_profile_delete.add_argument("--approve", action="store_true", required=True)

    project_sub.add_parser("python-profiles", help="Listar perfiles Python por proyecto.")
    project_python_profile_show = project_sub.add_parser(
        "python-profile-show", help="Mostrar el perfil Python efectivo de un proyecto."
    )
    project_python_profile_show.add_argument("path", type=Path)
    project_python_profile_set = project_sub.add_parser(
        "python-profile-set", help="Guardar valores Python seguros para un proyecto autorizado."
    )
    project_python_profile_set.add_argument("path", type=Path)
    project_python_profile_set.add_argument(
        "--pyproject", action=argparse.BooleanOptionalAction, default=None
    )
    project_python_profile_set.add_argument(
        "--compile", action=argparse.BooleanOptionalAction, default=None
    )
    project_python_profile_set.add_argument(
        "--ruff", action=argparse.BooleanOptionalAction, default=None
    )
    project_python_profile_set.add_argument(
        "--mypy", action=argparse.BooleanOptionalAction, default=None
    )
    project_python_profile_set.add_argument(
        "--pytest", action=argparse.BooleanOptionalAction, default=None
    )
    project_python_profile_set.add_argument("--ruff-config")
    project_python_profile_set.add_argument("--mypy-config")
    project_python_profile_set.add_argument("--pytest-path")
    project_python_profile_set.add_argument(
        "--fail-fast", action=argparse.BooleanOptionalAction, default=None
    )
    project_python_profile_set.add_argument(
        "--require-tools", action=argparse.BooleanOptionalAction, default=None
    )
    project_python_profile_set.add_argument("--max-python-files", type=int)
    project_python_profile_set.add_argument(
        "--exclude-path", action="append", dest="python_exclude_paths"
    )
    project_python_profile_set.add_argument("--timeout", type=int)
    project_python_profile_set.add_argument("--output-limit", type=int)
    project_python_profile_set.add_argument("--approve", action="store_true", required=True)
    project_python_profile_delete = project_sub.add_parser(
        "python-profile-delete", help="Eliminar el perfil Python de un proyecto."
    )
    project_python_profile_delete.add_argument("path", type=Path)
    project_python_profile_delete.add_argument("--approve", action="store_true", required=True)

    project_sub.add_parser("java-profiles", help="Listar perfiles Java por proyecto.")
    project_java_profile_show = project_sub.add_parser(
        "java-profile-show", help="Mostrar el perfil Java efectivo de un proyecto."
    )
    project_java_profile_show.add_argument("path", type=Path)
    project_java_profile_set = project_sub.add_parser(
        "java-profile-set", help="Guardar valores Java seguros para un proyecto autorizado."
    )
    project_java_profile_set.add_argument("path", type=Path)
    project_java_profile_set.add_argument(
        "--descriptor", action=argparse.BooleanOptionalAction, default=None
    )
    project_java_profile_set.add_argument(
        "--javac", action=argparse.BooleanOptionalAction, default=None
    )
    project_java_profile_set.add_argument(
        "--build", action=argparse.BooleanOptionalAction, default=None
    )
    project_java_profile_set.add_argument(
        "--tests", action=argparse.BooleanOptionalAction, default=None
    )
    project_java_profile_set.add_argument(
        "--build-tool", choices=("auto", "javac", "maven", "gradle")
    )
    project_java_profile_set.add_argument("--java-release", type=int)
    project_java_profile_set.add_argument(
        "--fail-fast", action=argparse.BooleanOptionalAction, default=None
    )
    project_java_profile_set.add_argument(
        "--require-tools", action=argparse.BooleanOptionalAction, default=None
    )
    project_java_profile_set.add_argument("--max-java-files", type=int)
    project_java_profile_set.add_argument(
        "--exclude-path", action="append", dest="java_exclude_paths"
    )
    project_java_profile_set.add_argument("--timeout", type=int)
    project_java_profile_set.add_argument("--output-limit", type=int)
    project_java_profile_set.add_argument("--approve", action="store_true", required=True)
    project_java_profile_delete = project_sub.add_parser(
        "java-profile-delete", help="Eliminar el perfil Java de un proyecto."
    )
    project_java_profile_delete.add_argument("path", type=Path)
    project_java_profile_delete.add_argument("--approve", action="store_true", required=True)

    project_sub.add_parser("kotlin-profiles", help="Listar perfiles Kotlin por proyecto.")
    project_kotlin_profile_show = project_sub.add_parser(
        "kotlin-profile-show", help="Mostrar el perfil Kotlin efectivo de un proyecto."
    )
    project_kotlin_profile_show.add_argument("path", type=Path)
    project_kotlin_profile_set = project_sub.add_parser(
        "kotlin-profile-set", help="Guardar valores Kotlin seguros para un proyecto autorizado."
    )
    project_kotlin_profile_set.add_argument("path", type=Path)
    project_kotlin_profile_set.add_argument(
        "--descriptor", action=argparse.BooleanOptionalAction, default=None
    )
    project_kotlin_profile_set.add_argument(
        "--kotlinc", action=argparse.BooleanOptionalAction, default=None
    )
    project_kotlin_profile_set.add_argument(
        "--build", action=argparse.BooleanOptionalAction, default=None
    )
    project_kotlin_profile_set.add_argument(
        "--tests", action=argparse.BooleanOptionalAction, default=None
    )
    project_kotlin_profile_set.add_argument(
        "--build-tool", choices=("auto", "kotlinc", "maven", "gradle")
    )
    project_kotlin_profile_set.add_argument("--jvm-target", type=int)
    project_kotlin_profile_set.add_argument(
        "--fail-fast", action=argparse.BooleanOptionalAction, default=None
    )
    project_kotlin_profile_set.add_argument(
        "--require-tools", action=argparse.BooleanOptionalAction, default=None
    )
    project_kotlin_profile_set.add_argument("--max-kotlin-files", type=int)
    project_kotlin_profile_set.add_argument(
        "--exclude-path", action="append", dest="kotlin_exclude_paths"
    )
    project_kotlin_profile_set.add_argument("--timeout", type=int)
    project_kotlin_profile_set.add_argument("--output-limit", type=int)
    project_kotlin_profile_set.add_argument("--approve", action="store_true", required=True)
    project_kotlin_profile_delete = project_sub.add_parser(
        "kotlin-profile-delete", help="Eliminar el perfil Kotlin de un proyecto."
    )
    project_kotlin_profile_delete.add_argument("path", type=Path)
    project_kotlin_profile_delete.add_argument("--approve", action="store_true", required=True)

    project_sub.add_parser("dotnet-profiles", help="Listar perfiles C#/.NET por proyecto.")
    project_dotnet_profile_show = project_sub.add_parser(
        "dotnet-profile-show", help="Mostrar el perfil .NET efectivo de un proyecto."
    )
    project_dotnet_profile_show.add_argument("path", type=Path)
    project_dotnet_profile_set = project_sub.add_parser(
        "dotnet-profile-set", help="Guardar valores .NET seguros para un proyecto autorizado."
    )
    project_dotnet_profile_set.add_argument("path", type=Path)
    for option in ("descriptor", "format", "build", "tests"):
        project_dotnet_profile_set.add_argument(
            f"--{option}", action=argparse.BooleanOptionalAction, default=None
        )
    project_dotnet_profile_set.add_argument("--configuration", choices=("Debug", "Release"))
    project_dotnet_profile_set.add_argument(
        "--fail-fast", action=argparse.BooleanOptionalAction, default=None
    )
    project_dotnet_profile_set.add_argument(
        "--require-tools", action=argparse.BooleanOptionalAction, default=None
    )
    project_dotnet_profile_set.add_argument("--max-dotnet-files", type=int)
    project_dotnet_profile_set.add_argument(
        "--exclude-path", action="append", dest="dotnet_exclude_paths"
    )
    project_dotnet_profile_set.add_argument("--timeout", type=int)
    project_dotnet_profile_set.add_argument("--output-limit", type=int)
    project_dotnet_profile_set.add_argument("--approve", action="store_true", required=True)
    project_dotnet_profile_delete = project_sub.add_parser(
        "dotnet-profile-delete", help="Eliminar el perfil .NET de un proyecto."
    )
    project_dotnet_profile_delete.add_argument("path", type=Path)
    project_dotnet_profile_delete.add_argument("--approve", action="store_true", required=True)

    project_sub.add_parser("swift-profiles", help="Listar perfiles Swift por proyecto.")
    project_swift_profile_show = project_sub.add_parser(
        "swift-profile-show", help="Mostrar el perfil Swift efectivo de un proyecto."
    )
    project_swift_profile_show.add_argument("path", type=Path)
    project_swift_profile_set = project_sub.add_parser(
        "swift-profile-set",
        help="Guardar valores Swift seguros para un proyecto autorizado.",
    )
    project_swift_profile_set.add_argument("path", type=Path)
    for option in ("manifest", "syntax", "format", "build", "tests"):
        project_swift_profile_set.add_argument(
            f"--{option}", action=argparse.BooleanOptionalAction, default=None
        )
    project_swift_profile_set.add_argument("--configuration", choices=("debug", "release"))
    project_swift_profile_set.add_argument(
        "--fail-fast", action=argparse.BooleanOptionalAction, default=None
    )
    project_swift_profile_set.add_argument(
        "--require-tools", action=argparse.BooleanOptionalAction, default=None
    )
    project_swift_profile_set.add_argument("--max-swift-files", type=int)
    project_swift_profile_set.add_argument(
        "--exclude-path", action="append", dest="swift_exclude_paths"
    )
    project_swift_profile_set.add_argument("--timeout", type=int)
    project_swift_profile_set.add_argument("--output-limit", type=int)
    project_swift_profile_set.add_argument("--approve", action="store_true", required=True)
    project_swift_profile_delete = project_sub.add_parser(
        "swift-profile-delete", help="Eliminar el perfil Swift de un proyecto."
    )
    project_swift_profile_delete.add_argument("path", type=Path)
    project_swift_profile_delete.add_argument("--approve", action="store_true", required=True)

    project_sub.add_parser("dart-profiles", help="Listar perfiles Dart/Flutter por proyecto.")
    project_dart_profile_show = project_sub.add_parser(
        "dart-profile-show",
        help="Mostrar el perfil Dart/Flutter efectivo de un proyecto.",
    )
    project_dart_profile_show.add_argument("path", type=Path)
    project_dart_profile_set = project_sub.add_parser(
        "dart-profile-set",
        help="Guardar valores Dart/Flutter seguros para un proyecto autorizado.",
    )
    project_dart_profile_set.add_argument("path", type=Path)
    for option in ("descriptor", "format", "analyze", "tests"):
        project_dart_profile_set.add_argument(
            f"--{option}", action=argparse.BooleanOptionalAction, default=None
        )
    project_dart_profile_set.add_argument("--test-runner", choices=("auto", "dart", "flutter"))
    project_dart_profile_set.add_argument(
        "--fail-fast", action=argparse.BooleanOptionalAction, default=None
    )
    project_dart_profile_set.add_argument(
        "--require-tools", action=argparse.BooleanOptionalAction, default=None
    )
    project_dart_profile_set.add_argument("--max-dart-files", type=int)
    project_dart_profile_set.add_argument(
        "--exclude-path", action="append", dest="dart_exclude_paths"
    )
    project_dart_profile_set.add_argument("--timeout", type=int)
    project_dart_profile_set.add_argument("--output-limit", type=int)
    project_dart_profile_set.add_argument("--approve", action="store_true", required=True)
    project_dart_profile_delete = project_sub.add_parser(
        "dart-profile-delete", help="Eliminar el perfil Dart/Flutter de un proyecto."
    )
    project_dart_profile_delete.add_argument("path", type=Path)
    project_dart_profile_delete.add_argument("--approve", action="store_true", required=True)

    project_sub.add_parser("sql-profiles", help="Listar perfiles SQL por proyecto.")
    project_sql_profile_show = project_sub.add_parser(
        "sql-profile-show",
        help="Mostrar el perfil SQL efectivo de un proyecto.",
    )
    project_sql_profile_show.add_argument("path", type=Path)
    project_sql_profile_set = project_sub.add_parser(
        "sql-profile-set",
        help="Guardar valores SQL seguros para un proyecto autorizado.",
    )
    project_sql_profile_set.add_argument("path", type=Path)
    for option in (
        "static",
        "migrations",
        "schema",
        "allow-mutating-sql",
        "allow-destructive-migrations",
        "fail-fast",
    ):
        project_sql_profile_set.add_argument(
            f"--{option}",
            action=argparse.BooleanOptionalAction,
            default=None,
        )
    project_sql_profile_set.add_argument(
        "--dialect",
        choices=("auto", "generic", "sqlite", "mysql", "mariadb", "postgresql"),
    )
    project_sql_profile_set.add_argument("--max-sql-files", type=int)
    project_sql_profile_set.add_argument("--max-database-files", type=int)
    project_sql_profile_set.add_argument(
        "--exclude-path", action="append", dest="sql_exclude_paths"
    )
    project_sql_profile_set.add_argument("--timeout", type=int)
    project_sql_profile_set.add_argument("--output-limit", type=int)
    project_sql_profile_set.add_argument("--approve", action="store_true", required=True)
    project_sql_profile_delete = project_sub.add_parser(
        "sql-profile-delete", help="Eliminar el perfil SQL de un proyecto."
    )
    project_sql_profile_delete.add_argument("path", type=Path)
    project_sql_profile_delete.add_argument("--approve", action="store_true", required=True)

    project_sub.add_parser("native-profiles", help="Listar perfiles C/C++ por proyecto.")
    project_native_profile_show = project_sub.add_parser(
        "native-profile-show",
        help="Mostrar el perfil C/C++ efectivo de un proyecto.",
    )
    project_native_profile_show.add_argument("path", type=Path)
    project_native_profile_set = project_sub.add_parser(
        "native-profile-set",
        help="Guardar valores C/C++ seguros para un proyecto autorizado.",
    )
    project_native_profile_set.add_argument("path", type=Path)
    for option in (
        "descriptor",
        "c-syntax",
        "cpp-syntax",
        "static",
        "build",
        "tests",
        "fail-fast",
        "require-tools",
    ):
        project_native_profile_set.add_argument(
            f"--{option}",
            action=argparse.BooleanOptionalAction,
            default=None,
        )
    project_native_profile_set.add_argument("--compiler", choices=("auto", "gcc", "clang"))
    project_native_profile_set.add_argument("--c-standard", choices=("c11", "c17", "c23"))
    project_native_profile_set.add_argument("--cpp-standard", choices=("c++17", "c++20", "c++23"))
    project_native_profile_set.add_argument("--max-native-files", type=int)
    project_native_profile_set.add_argument(
        "--exclude-path", action="append", dest="native_exclude_paths"
    )
    project_native_profile_set.add_argument("--timeout", type=int)
    project_native_profile_set.add_argument("--output-limit", type=int)
    project_native_profile_set.add_argument("--approve", action="store_true", required=True)
    project_native_profile_delete = project_sub.add_parser(
        "native-profile-delete", help="Eliminar el perfil C/C++ de un proyecto."
    )
    project_native_profile_delete.add_argument("path", type=Path)
    project_native_profile_delete.add_argument("--approve", action="store_true", required=True)

    project_sub.add_parser("ruby-profiles", help="Listar perfiles Ruby por proyecto.")
    project_ruby_profile_show = project_sub.add_parser(
        "ruby-profile-show", help="Mostrar el perfil Ruby efectivo de un proyecto."
    )
    project_ruby_profile_show.add_argument("path", type=Path)
    project_ruby_profile_set = project_sub.add_parser(
        "ruby-profile-set", help="Guardar valores Ruby seguros para un proyecto autorizado."
    )
    project_ruby_profile_set.add_argument("path", type=Path)
    for option in (
        "descriptor",
        "bundle",
        "syntax",
        "rubocop",
        "tests",
        "fail-fast",
        "require-tools",
    ):
        project_ruby_profile_set.add_argument(
            f"--{option}",
            action=argparse.BooleanOptionalAction,
            default=None,
        )
    project_ruby_profile_set.add_argument("--test-framework", choices=("auto", "rspec", "minitest"))
    project_ruby_profile_set.add_argument("--max-ruby-files", type=int)
    project_ruby_profile_set.add_argument(
        "--exclude-path", action="append", dest="ruby_exclude_paths"
    )
    project_ruby_profile_set.add_argument("--timeout", type=int)
    project_ruby_profile_set.add_argument("--output-limit", type=int)
    project_ruby_profile_set.add_argument("--approve", action="store_true", required=True)
    project_ruby_profile_delete = project_sub.add_parser(
        "ruby-profile-delete", help="Eliminar el perfil Ruby de un proyecto."
    )
    project_ruby_profile_delete.add_argument("path", type=Path)
    project_ruby_profile_delete.add_argument("--approve", action="store_true", required=True)

    project_sub.add_parser("go-profiles", help="Listar perfiles Go por proyecto.")
    project_go_profile_show = project_sub.add_parser(
        "go-profile-show", help="Mostrar el perfil Go efectivo de un proyecto."
    )
    project_go_profile_show.add_argument("path", type=Path)
    project_go_profile_set = project_sub.add_parser(
        "go-profile-set", help="Guardar valores Go seguros para un proyecto autorizado."
    )
    project_go_profile_set.add_argument("path", type=Path)
    for option in (
        "module",
        "fmt",
        "vet",
        "build",
        "tests",
        "fail-fast",
        "require-tools",
    ):
        project_go_profile_set.add_argument(
            f"--{option}",
            action=argparse.BooleanOptionalAction,
            default=None,
        )
    project_go_profile_set.add_argument("--test-mode", choices=("auto", "short", "full"))
    project_go_profile_set.add_argument("--max-go-files", type=int)
    project_go_profile_set.add_argument("--exclude-path", action="append", dest="go_exclude_paths")
    project_go_profile_set.add_argument("--timeout", type=int)
    project_go_profile_set.add_argument("--output-limit", type=int)
    project_go_profile_set.add_argument("--approve", action="store_true", required=True)
    project_go_profile_delete = project_sub.add_parser(
        "go-profile-delete", help="Eliminar el perfil Go de un proyecto."
    )
    project_go_profile_delete.add_argument("path", type=Path)
    project_go_profile_delete.add_argument("--approve", action="store_true", required=True)

    project_sub.add_parser("rust-profiles", help="Listar perfiles Rust por proyecto.")
    project_rust_profile_show = project_sub.add_parser(
        "rust-profile-show", help="Mostrar el perfil Rust efectivo de un proyecto."
    )
    project_rust_profile_show.add_argument("path", type=Path)
    project_rust_profile_set = project_sub.add_parser(
        "rust-profile-set",
        help="Guardar valores Rust seguros para un proyecto autorizado.",
    )
    project_rust_profile_set.add_argument("path", type=Path)
    for option in (
        "manifest",
        "fmt",
        "check",
        "clippy",
        "tests",
        "fail-fast",
        "require-tools",
    ):
        project_rust_profile_set.add_argument(
            f"--{option}",
            action=argparse.BooleanOptionalAction,
            default=None,
        )
    project_rust_profile_set.add_argument("--feature-mode", choices=("default", "all"))
    project_rust_profile_set.add_argument("--max-rust-files", type=int)
    project_rust_profile_set.add_argument(
        "--exclude-path", action="append", dest="rust_exclude_paths"
    )
    project_rust_profile_set.add_argument("--timeout", type=int)
    project_rust_profile_set.add_argument("--output-limit", type=int)
    project_rust_profile_set.add_argument("--approve", action="store_true", required=True)
    project_rust_profile_delete = project_sub.add_parser(
        "rust-profile-delete", help="Eliminar el perfil Rust de un proyecto."
    )
    project_rust_profile_delete.add_argument("path", type=Path)
    project_rust_profile_delete.add_argument("--approve", action="store_true", required=True)

    file_parser = sub.add_parser("file", help="Leer archivos autorizados.")
    file_sub = file_parser.add_subparsers(dest="file_command", required=True)
    file_read = file_sub.add_parser("read")
    file_read.add_argument("path", type=Path)
    file_read.add_argument("--start-line", type=int, default=1)
    file_read.add_argument("--end-line", type=int)

    knowledge_parser = sub.add_parser("knowledge", help="Administrar conocimiento importado.")
    knowledge_sub = knowledge_parser.add_subparsers(dest="knowledge_command", required=True)
    knowledge_import = knowledge_sub.add_parser("import")
    knowledge_import.add_argument("path", type=Path)
    knowledge_import.add_argument("--title")
    knowledge_import.add_argument("--project")
    knowledge_import.add_argument("--force", action="store_true")
    knowledge_import.add_argument("--approve", action="store_true", required=True)
    knowledge_search = knowledge_sub.add_parser("search")
    knowledge_search.add_argument("query")
    knowledge_search.add_argument("--limit", type=int, default=10)
    knowledge_list = knowledge_sub.add_parser("list")
    knowledge_list.add_argument("--limit", type=int, default=100)
    knowledge_forget = knowledge_sub.add_parser("forget")
    knowledge_forget.add_argument("id", type=int)
    knowledge_forget.add_argument("--approve", action="store_true", required=True)

    alexandria_parser = sub.add_parser(
        "alexandria", help="Administrar bibliotecas locales de conocimiento."
    )
    alexandria_sub = alexandria_parser.add_subparsers(dest="alexandria_command", required=True)
    alexandria_create = alexandria_sub.add_parser("create")
    alexandria_create.add_argument("name")
    alexandria_create.add_argument("--description", default="")
    alexandria_create.add_argument("--domain", default="general")
    alexandria_create.add_argument("--language", default="auto")
    alexandria_create.add_argument("--version", default="1")
    alexandria_create.add_argument("--license-id", default="unverified")
    alexandria_create.add_argument("--approve", action="store_true", required=True)
    alexandria_list = alexandria_sub.add_parser("list")
    alexandria_list.add_argument("--limit", type=int, default=100)
    alexandria_show = alexandria_sub.add_parser("show")
    alexandria_show.add_argument("library")
    alexandria_import = alexandria_sub.add_parser("import")
    alexandria_import.add_argument("library")
    alexandria_import.add_argument("path", type=Path)
    alexandria_import.add_argument("--title")
    alexandria_import.add_argument("--source-url", default="")
    alexandria_import.add_argument("--approve", action="store_true", required=True)
    alexandria_search = alexandria_sub.add_parser("search")
    alexandria_search.add_argument("query")
    alexandria_search.add_argument("--library")
    alexandria_search.add_argument("--limit", type=int, default=10)
    alexandria_review = alexandria_sub.add_parser("review-source")
    alexandria_review.add_argument("id", type=int)
    alexandria_review.add_argument("--approve", action="store_true", required=True)
    alexandria_reindex = alexandria_sub.add_parser("reindex")
    alexandria_reindex.add_argument("--approve", action="store_true", required=True)
    alexandria_package_inspect = alexandria_sub.add_parser(
        "package-inspect", help="Validar un paquete local sin instalarlo."
    )
    alexandria_package_inspect.add_argument("path", type=Path)
    alexandria_package_install = alexandria_sub.add_parser(
        "package-install", help="Instalar un paquete local verificado."
    )
    alexandria_package_install.add_argument("path", type=Path)
    alexandria_package_install.add_argument("--approve", action="store_true", required=True)
    alexandria_package_create = alexandria_sub.add_parser(
        "package-create", help="Crear un paquete local verificado desde archivos."
    )
    alexandria_package_create.add_argument("destination", type=Path)
    alexandria_package_create.add_argument("--package-id", required=True)
    alexandria_package_create.add_argument("--name", required=True)
    alexandria_package_create.add_argument("--version", required=True)
    alexandria_package_create.add_argument(
        "--tier", choices=("basic", "optional"), default="optional"
    )
    alexandria_package_create.add_argument("--domain", required=True)
    alexandria_package_create.add_argument("--language", default="es")
    alexandria_package_create.add_argument("--license-id", required=True)
    alexandria_package_create.add_argument("--description", default="")
    alexandria_package_create.add_argument("--publisher", default="unverified")
    alexandria_package_create.add_argument("--tag", action="append", default=[])
    alexandria_package_create.add_argument("--source", action="append", type=Path, required=True)
    alexandria_package_create.add_argument("--approve", action="store_true", required=True)
    alexandria_package_export = alexandria_sub.add_parser(
        "package-export", help="Exportar un paquete instalado a una carpeta local."
    )
    alexandria_package_export.add_argument("package_id")
    alexandria_package_export.add_argument("destination", type=Path)
    alexandria_package_export.add_argument("--approve", action="store_true", required=True)
    alexandria_sub.add_parser("package-list", help="Listar paquetes instalados.")
    structured_inspect = alexandria_sub.add_parser(
        "structured-inspect",
        help="Validar un paquete estructurado de idioma o primeros auxilios.",
    )
    structured_inspect.add_argument("path", type=Path)
    structured_install = alexandria_sub.add_parser(
        "structured-install",
        help="Instalar o reemplazar un paquete estructurado revisado.",
    )
    structured_install.add_argument("path", type=Path)
    structured_install.add_argument("--replace", action="store_true")
    structured_install.add_argument("--approve", action="store_true", required=True)
    alexandria_sub.add_parser("structured-list", help="Listar paquetes estructurados instalados.")
    structured_show = alexandria_sub.add_parser(
        "structured-show",
        help="Mostrar un paquete estructurado y la procedencia de sus fuentes.",
    )
    structured_show.add_argument("package_id")
    for structured_command in (
        "structured-enable",
        "structured-disable",
        "structured-remove",
    ):
        structured_parser = alexandria_sub.add_parser(structured_command)
        structured_parser.add_argument("package_id")
        structured_parser.add_argument("--approve", action="store_true", required=True)
    for package_command in ("package-enable", "package-disable", "package-remove"):
        package_parser = alexandria_sub.add_parser(package_command)
        package_parser.add_argument("package_id")
        package_parser.add_argument("--approve", action="store_true", required=True)
    language_inspect = alexandria_sub.add_parser("language-pack-inspect")
    language_inspect.add_argument("path", type=Path)
    language_inspect.add_argument("--approve", action="store_true", required=True)
    language_install = alexandria_sub.add_parser("language-pack-install")
    language_install.add_argument("path", type=Path)
    language_install.add_argument("--query-priority", type=int, default=100)
    language_install.add_argument("--approve", action="store_true", required=True)
    alexandria_sub.add_parser("language-pack-list")
    language_show = alexandria_sub.add_parser("language-pack-show")
    language_show.add_argument("id")
    language_verify = alexandria_sub.add_parser("language-pack-verify")
    language_verify.add_argument("id")
    language_verify.add_argument("--approve", action="store_true", required=True)
    for language_command in ("language-pack-enable", "language-pack-disable"):
        language_state = alexandria_sub.add_parser(language_command)
        language_state.add_argument("id")
        language_state.add_argument("--approve", action="store_true", required=True)
    language_build = alexandria_sub.add_parser("language-pack-build-es")
    language_build.add_argument("--source-metadata", type=Path, required=True)
    language_build.add_argument("--pack-id", required=True)
    language_build.add_argument("--version", required=True)
    language_build.add_argument("--output-dir", type=Path, required=True)
    language_build.add_argument("--build-epoch", type=int)
    language_build.add_argument("--allow-large", action="store_true")
    language_build.add_argument("--approve", action="store_true", required=True)
    bundle_create = alexandria_sub.add_parser("language-bundle-create")
    bundle_create.add_argument("--pack", type=Path, action="append", required=True)
    bundle_create.add_argument("--output-dir", type=Path, required=True)
    bundle_create.add_argument("--build-epoch", type=int, required=True)
    bundle_create.add_argument("--part-bytes", type=int)
    bundle_create.add_argument("--approve", action="store_true", required=True)
    for bundle_command in ("language-bundle-inspect", "language-bundle-verify"):
        bundle_parser = alexandria_sub.add_parser(bundle_command)
        bundle_parser.add_argument("manifest", type=Path)
    bundle_install = alexandria_sub.add_parser("language-bundle-install")
    bundle_install.add_argument("manifest", type=Path)
    bundle_install.add_argument("--enable", action="store_true")
    bundle_install.add_argument("--approve", action="store_true", required=True)
    for command_name in ("enable", "disable"):
        command = alexandria_sub.add_parser(command_name)
        command.add_argument("library")
        command.add_argument("--approve", action="store_true", required=True)

    php_parser = sub.add_parser(
        "php",
        help="Ejecutar skills PHP controladas y revisar herramientas disponibles.",
    )
    php_sub = php_parser.add_subparsers(dest="php_command", required=True)
    php_sub.add_parser("status", help="Mostrar herramientas PHP globales detectadas.")
    php_sub.add_parser("help", help="Mostrar política de rutas y ejemplos PHP.")

    php_syntax = php_sub.add_parser("syntax", help="Ejecutar php -l sobre un archivo.")
    php_syntax.add_argument("path", type=Path)
    php_syntax.add_argument("--approve", action="store_true", required=True)

    php_composer = php_sub.add_parser(
        "composer-validate",
        help="Validar composer.json y composer.lock sin scripts ni plugins.",
    )
    php_composer.add_argument("path", type=Path)
    php_composer.add_argument("--strict", action="store_true")
    php_composer.add_argument("--allow-root-once", action="store_true")
    php_composer.add_argument("--approve", action="store_true", required=True)

    php_phpstan = php_sub.add_parser("phpstan", help="Ejecutar análisis estático PHPStan.")
    php_phpstan.add_argument("path", type=Path)
    php_phpstan.add_argument("--config", type=Path)
    php_phpstan.add_argument(
        "--level",
        choices=tuple(str(value) for value in range(11)) + ("max",),
    )
    php_phpstan.add_argument("--allow-root-once", action="store_true")
    php_phpstan.add_argument("--approve", action="store_true", required=True)

    php_phpunit = php_sub.add_parser("phpunit", help="Ejecutar pruebas PHPUnit.")
    php_phpunit.add_argument("path", type=Path, nargs="?", default=Path("."))
    php_phpunit.add_argument("--config", type=Path)
    php_phpunit.add_argument("--testsuite")
    php_phpunit.add_argument("--filter")
    php_phpunit.add_argument("--allow-root-once", action="store_true")
    php_phpunit.add_argument("--approve", action="store_true", required=True)

    php_inspect = php_sub.add_parser(
        "inspect", help="Inspeccionar un proyecto PHP sin ejecutar su código."
    )
    php_inspect.add_argument("path", type=Path)
    php_inspect.add_argument("--allow-root-once", action="store_true")
    php_inspect.add_argument("--approve", action="store_true", required=True)

    php_syntax_project = php_sub.add_parser(
        "syntax-project", help="Ejecutar php -l sobre todos los PHP del proyecto."
    )
    php_syntax_project.add_argument("path", type=Path)
    php_syntax_project.add_argument("--max-files", type=int)
    php_syntax_project.add_argument("--allow-root-once", action="store_true")
    php_syntax_project.add_argument("--approve", action="store_true", required=True)

    php_verify = php_sub.add_parser(
        "verify", help="Ejecutar el flujo PHP completo y guardar su resultado."
    )
    php_verify.add_argument("path", type=Path)
    php_verify.add_argument("--composer", action=argparse.BooleanOptionalAction, default=None)
    php_verify.add_argument("--syntax-scan", action=argparse.BooleanOptionalAction, default=None)
    php_verify.add_argument("--phpstan", action=argparse.BooleanOptionalAction, default=None)
    php_verify.add_argument("--phpunit", action=argparse.BooleanOptionalAction, default=None)
    php_verify.add_argument("--fail-fast", action=argparse.BooleanOptionalAction, default=None)
    php_verify.add_argument("--require-tools", action=argparse.BooleanOptionalAction, default=None)
    php_verify.add_argument("--max-files", type=int)
    php_verify.add_argument("--allow-root-once", action="store_true")
    php_verify.add_argument("--approve", action="store_true", required=True)

    php_history = php_sub.add_parser("history", help="Listar verificaciones PHP anteriores.")
    php_history.add_argument("path", type=Path, nargs="?")
    php_history.add_argument("--limit", type=int, default=20)
    php_report = php_sub.add_parser("report", help="Mostrar una verificación PHP.")
    php_report.add_argument("run_id")
    php_compare = php_sub.add_parser("compare", help="Comparar dos verificaciones PHP.")
    php_compare.add_argument("first_run_id")
    php_compare.add_argument("second_run_id")

    webdev_parser = sub.add_parser(
        "webdev", help="Ejecutar verificaciones HTML, CSS, JavaScript y TypeScript."
    )
    webdev_sub = webdev_parser.add_subparsers(dest="webdev_command", required=True)
    webdev_sub.add_parser("status", help="Mostrar herramientas web detectadas.")
    webdev_sub.add_parser("help", help="Mostrar política y ejemplos web.")
    for command_name, description in (
        ("inspect", "Inspeccionar el proyecto sin ejecutar código."),
        ("html", "Validar estructura HTML básica."),
        ("css", "Validar estructura CSS básica."),
        ("javascript", "Ejecutar node --check sobre JavaScript."),
        ("typescript", "Ejecutar tsc --noEmit."),
        ("framework", "Validar configuración Angular, Vite y frontend."),
        ("eslint", "Ejecutar ESLint local o global."),
        ("stylelint", "Ejecutar Stylelint local o global."),
    ):
        command_parser = webdev_sub.add_parser(command_name, help=description)
        command_parser.add_argument("path", type=Path)
        command_parser.add_argument("--max-files", type=int)
        command_parser.add_argument("--allow-root-once", action="store_true")
        command_parser.add_argument("--approve", action="store_true", required=True)
        if command_name == "typescript":
            command_parser.add_argument("--config")
        if command_name == "framework":
            command_parser.add_argument("--framework-preset")
        if command_name == "eslint":
            command_parser.add_argument("--config", dest="eslint_config")
        if command_name == "stylelint":
            command_parser.add_argument("--config", dest="stylelint_config")
    webdev_verify = webdev_sub.add_parser(
        "verify", help="Ejecutar el flujo web completo y guardar el resultado."
    )
    webdev_verify.add_argument("path", type=Path)
    webdev_verify.add_argument("--html", action=argparse.BooleanOptionalAction, default=None)
    webdev_verify.add_argument("--css", action=argparse.BooleanOptionalAction, default=None)
    webdev_verify.add_argument("--javascript", action=argparse.BooleanOptionalAction, default=None)
    webdev_verify.add_argument("--typescript", action=argparse.BooleanOptionalAction, default=None)
    webdev_verify.add_argument(
        "--framework-checks", action=argparse.BooleanOptionalAction, default=None
    )
    webdev_verify.add_argument("--eslint", action=argparse.BooleanOptionalAction, default=None)
    webdev_verify.add_argument("--stylelint", action=argparse.BooleanOptionalAction, default=None)
    webdev_verify.add_argument("--framework-preset")
    webdev_verify.add_argument("--eslint-config")
    webdev_verify.add_argument("--stylelint-config")
    webdev_verify.add_argument("--fail-fast", action=argparse.BooleanOptionalAction, default=None)
    webdev_verify.add_argument(
        "--require-tools", action=argparse.BooleanOptionalAction, default=None
    )
    webdev_verify.add_argument("--max-files", type=int)
    webdev_verify.add_argument("--allow-root-once", action="store_true")
    webdev_verify.add_argument("--approve", action="store_true", required=True)
    webdev_history = webdev_sub.add_parser("history", help="Listar verificaciones web anteriores.")
    webdev_history.add_argument("path", type=Path, nargs="?")
    webdev_history.add_argument("--limit", type=int, default=20)
    webdev_report = webdev_sub.add_parser("report", help="Mostrar una verificación web.")
    webdev_report.add_argument("run_id")
    webdev_compare = webdev_sub.add_parser("compare", help="Comparar dos verificaciones web.")
    webdev_compare.add_argument("first_run_id")
    webdev_compare.add_argument("second_run_id")

    pythondev_parser = sub.add_parser(
        "pythondev", help="Ejecutar verificaciones Python controladas."
    )
    pythondev_sub = pythondev_parser.add_subparsers(dest="pythondev_command", required=True)
    pythondev_sub.add_parser("status", help="Mostrar herramientas Python detectadas.")
    pythondev_sub.add_parser("help", help="Mostrar política y ejemplos Python.")
    for command_name, description in (
        ("inspect", "Inspeccionar el proyecto sin ejecutar código."),
        ("pyproject", "Validar pyproject.toml sin construir el paquete."),
        ("compile", "Compilar sintácticamente archivos Python."),
        ("ruff", "Ejecutar Ruff sin aplicar fixes."),
        ("mypy", "Ejecutar mypy con caché temporal."),
        ("pytest", "Ejecutar Pytest sin caché persistente."),
    ):
        command_parser = pythondev_sub.add_parser(command_name, help=description)
        command_parser.add_argument("path", type=Path)
        command_parser.add_argument("--max-files", type=int)
        command_parser.add_argument("--allow-root-once", action="store_true")
        command_parser.add_argument("--approve", action="store_true", required=True)
        if command_name == "ruff":
            command_parser.add_argument("--config", dest="ruff_config")
        if command_name == "mypy":
            command_parser.add_argument("--config", dest="mypy_config")
        if command_name == "pytest":
            command_parser.add_argument("--test-path", dest="pytest_path")
    pythondev_verify = pythondev_sub.add_parser(
        "verify", help="Ejecutar el flujo Python completo y guardar el resultado."
    )
    pythondev_verify.add_argument("path", type=Path)
    pythondev_verify.add_argument(
        "--pyproject", action=argparse.BooleanOptionalAction, default=None
    )
    pythondev_verify.add_argument("--compile", action=argparse.BooleanOptionalAction, default=None)
    pythondev_verify.add_argument("--ruff", action=argparse.BooleanOptionalAction, default=None)
    pythondev_verify.add_argument("--mypy", action=argparse.BooleanOptionalAction, default=None)
    pythondev_verify.add_argument("--pytest", action=argparse.BooleanOptionalAction, default=None)
    pythondev_verify.add_argument("--ruff-config")
    pythondev_verify.add_argument("--mypy-config")
    pythondev_verify.add_argument("--pytest-path")
    pythondev_verify.add_argument(
        "--fail-fast", action=argparse.BooleanOptionalAction, default=None
    )
    pythondev_verify.add_argument(
        "--require-tools", action=argparse.BooleanOptionalAction, default=None
    )
    pythondev_verify.add_argument("--max-files", type=int)
    pythondev_verify.add_argument("--allow-root-once", action="store_true")
    pythondev_verify.add_argument("--approve", action="store_true", required=True)
    pythondev_history = pythondev_sub.add_parser(
        "history", help="Listar verificaciones Python anteriores."
    )
    pythondev_history.add_argument("path", type=Path, nargs="?")
    pythondev_history.add_argument("--limit", type=int, default=20)
    pythondev_report = pythondev_sub.add_parser("report", help="Mostrar una verificación Python.")
    pythondev_report.add_argument("run_id")
    pythondev_compare = pythondev_sub.add_parser(
        "compare", help="Comparar dos verificaciones Python."
    )
    pythondev_compare.add_argument("first_run_id")
    pythondev_compare.add_argument("second_run_id")

    javadev_parser = sub.add_parser("javadev", help="Ejecutar verificaciones Java/JVM controladas.")
    javadev_sub = javadev_parser.add_subparsers(dest="javadev_command", required=True)
    javadev_sub.add_parser("status", help="Mostrar herramientas Java detectadas.")
    javadev_sub.add_parser("help", help="Mostrar política y ejemplos Java.")
    for command_name, description in (
        ("inspect", "Inspeccionar el proyecto sin ejecutar código."),
        ("descriptor", "Validar pom.xml y archivos Gradle sin ejecutar scripts."),
        ("javac", "Compilar con javac -proc:none hacia una carpeta temporal."),
        ("build", "Compilar con Maven o Gradle global en modo offline."),
        ("test", "Ejecutar tests Maven o Gradle globales en modo offline."),
    ):
        command_parser = javadev_sub.add_parser(command_name, help=description)
        command_parser.add_argument("path", type=Path)
        command_parser.add_argument("--build-tool", choices=("auto", "javac", "maven", "gradle"))
        command_parser.add_argument("--java-release", type=int)
        command_parser.add_argument("--max-files", type=int)
        command_parser.add_argument("--allow-root-once", action="store_true")
        command_parser.add_argument("--approve", action="store_true", required=True)
    javadev_verify = javadev_sub.add_parser(
        "verify", help="Ejecutar el flujo Java completo y guardar el resultado."
    )
    javadev_verify.add_argument("path", type=Path)
    javadev_verify.add_argument("--descriptor", action=argparse.BooleanOptionalAction, default=None)
    javadev_verify.add_argument("--javac", action=argparse.BooleanOptionalAction, default=None)
    javadev_verify.add_argument("--build", action=argparse.BooleanOptionalAction, default=None)
    javadev_verify.add_argument("--tests", action=argparse.BooleanOptionalAction, default=None)
    javadev_verify.add_argument("--build-tool", choices=("auto", "javac", "maven", "gradle"))
    javadev_verify.add_argument("--java-release", type=int)
    javadev_verify.add_argument("--fail-fast", action=argparse.BooleanOptionalAction, default=None)
    javadev_verify.add_argument(
        "--require-tools", action=argparse.BooleanOptionalAction, default=None
    )
    javadev_verify.add_argument("--max-files", type=int)
    javadev_verify.add_argument("--allow-root-once", action="store_true")
    javadev_verify.add_argument("--approve", action="store_true", required=True)
    javadev_history = javadev_sub.add_parser(
        "history", help="Listar verificaciones Java anteriores."
    )
    javadev_history.add_argument("path", type=Path, nargs="?")
    javadev_history.add_argument("--limit", type=int, default=20)
    javadev_report = javadev_sub.add_parser("report", help="Mostrar una verificación Java.")
    javadev_report.add_argument("run_id")
    javadev_compare = javadev_sub.add_parser("compare", help="Comparar dos verificaciones Java.")
    javadev_compare.add_argument("first_run_id")
    javadev_compare.add_argument("second_run_id")

    kotlindev_parser = sub.add_parser(
        "kotlindev", help="Ejecutar verificaciones Kotlin/JVM controladas."
    )
    kotlindev_sub = kotlindev_parser.add_subparsers(dest="kotlindev_command", required=True)
    kotlindev_sub.add_parser("status", help="Mostrar herramientas Kotlin detectadas.")
    kotlindev_sub.add_parser("help", help="Mostrar política y ejemplos Kotlin.")
    for command_name, description in (
        ("inspect", "Inspeccionar el proyecto sin ejecutar código."),
        ("descriptor", "Validar pom.xml y archivos Gradle sin ejecutar scripts."),
        ("kotlinc", "Compilar con kotlinc hacia una carpeta temporal."),
        ("build", "Compilar con Maven o Gradle global en modo offline."),
        ("test", "Ejecutar tests Maven o Gradle globales en modo offline."),
    ):
        command_parser = kotlindev_sub.add_parser(command_name, help=description)
        command_parser.add_argument("path", type=Path)
        command_parser.add_argument("--build-tool", choices=("auto", "kotlinc", "maven", "gradle"))
        command_parser.add_argument("--jvm-target", type=int)
        command_parser.add_argument("--max-files", type=int)
        command_parser.add_argument("--allow-root-once", action="store_true")
        command_parser.add_argument("--approve", action="store_true", required=True)
    kotlindev_verify = kotlindev_sub.add_parser(
        "verify", help="Ejecutar el flujo Kotlin completo y guardar el resultado."
    )
    kotlindev_verify.add_argument("path", type=Path)
    kotlindev_verify.add_argument(
        "--descriptor", action=argparse.BooleanOptionalAction, default=None
    )
    kotlindev_verify.add_argument("--kotlinc", action=argparse.BooleanOptionalAction, default=None)
    kotlindev_verify.add_argument("--build", action=argparse.BooleanOptionalAction, default=None)
    kotlindev_verify.add_argument("--tests", action=argparse.BooleanOptionalAction, default=None)
    kotlindev_verify.add_argument("--build-tool", choices=("auto", "kotlinc", "maven", "gradle"))
    kotlindev_verify.add_argument("--jvm-target", type=int)
    kotlindev_verify.add_argument(
        "--fail-fast", action=argparse.BooleanOptionalAction, default=None
    )
    kotlindev_verify.add_argument(
        "--require-tools", action=argparse.BooleanOptionalAction, default=None
    )
    kotlindev_verify.add_argument("--max-files", type=int)
    kotlindev_verify.add_argument("--allow-root-once", action="store_true")
    kotlindev_verify.add_argument("--approve", action="store_true", required=True)
    kotlindev_history = kotlindev_sub.add_parser(
        "history", help="Listar verificaciones Kotlin anteriores."
    )
    kotlindev_history.add_argument("path", type=Path, nargs="?")
    kotlindev_history.add_argument("--limit", type=int, default=20)
    kotlindev_report = kotlindev_sub.add_parser("report", help="Mostrar una verificación Kotlin.")
    kotlindev_report.add_argument("run_id")
    kotlindev_compare = kotlindev_sub.add_parser(
        "compare", help="Comparar dos verificaciones Kotlin."
    )
    kotlindev_compare.add_argument("first_run_id")
    kotlindev_compare.add_argument("second_run_id")

    dotnetdev_parser = sub.add_parser(
        "dotnetdev", help="Ejecutar verificaciones C#/.NET controladas."
    )
    dotnetdev_sub = dotnetdev_parser.add_subparsers(dest="dotnetdev_command", required=True)
    dotnetdev_sub.add_parser("status", help="Mostrar herramientas .NET detectadas.")
    dotnetdev_sub.add_parser("help", help="Mostrar política y ejemplos .NET.")
    for command_name, description in (
        ("inspect", "Inspeccionar el proyecto sin ejecutar MSBuild."),
        ("descriptor", "Validar soluciones y archivos MSBuild como datos."),
        ("format", "Comprobar dotnet format sin aplicar cambios."),
        ("build", "Compilar sin restore y con artefactos externos."),
        ("test", "Ejecutar tests sin restore y con artefactos externos."),
    ):
        command_parser = dotnetdev_sub.add_parser(command_name, help=description)
        command_parser.add_argument("path", type=Path)
        command_parser.add_argument("--configuration", choices=("Debug", "Release"))
        command_parser.add_argument("--max-files", type=int)
        command_parser.add_argument("--allow-root-once", action="store_true")
        command_parser.add_argument("--approve", action="store_true", required=True)
    dotnetdev_verify = dotnetdev_sub.add_parser(
        "verify", help="Ejecutar el flujo .NET completo y guardar el resultado."
    )
    dotnetdev_verify.add_argument("path", type=Path)
    for option in ("descriptor", "format", "build", "tests"):
        dotnetdev_verify.add_argument(
            f"--{option}", action=argparse.BooleanOptionalAction, default=None
        )
    dotnetdev_verify.add_argument("--configuration", choices=("Debug", "Release"))
    dotnetdev_verify.add_argument(
        "--fail-fast", action=argparse.BooleanOptionalAction, default=None
    )
    dotnetdev_verify.add_argument(
        "--require-tools", action=argparse.BooleanOptionalAction, default=None
    )
    dotnetdev_verify.add_argument("--max-files", type=int)
    dotnetdev_verify.add_argument("--allow-root-once", action="store_true")
    dotnetdev_verify.add_argument("--approve", action="store_true", required=True)
    dotnetdev_history = dotnetdev_sub.add_parser(
        "history", help="Listar verificaciones .NET anteriores."
    )
    dotnetdev_history.add_argument("path", type=Path, nargs="?")
    dotnetdev_history.add_argument("--limit", type=int, default=20)
    dotnetdev_report = dotnetdev_sub.add_parser("report", help="Mostrar una verificación .NET.")
    dotnetdev_report.add_argument("run_id")
    dotnetdev_compare = dotnetdev_sub.add_parser(
        "compare", help="Comparar dos verificaciones .NET."
    )
    dotnetdev_compare.add_argument("first_run_id")
    dotnetdev_compare.add_argument("second_run_id")

    swiftdev_parser = sub.add_parser("swiftdev", help="Ejecutar verificaciones Swift controladas.")
    swiftdev_sub = swiftdev_parser.add_subparsers(dest="swiftdev_command", required=True)
    swiftdev_sub.add_parser("status", help="Mostrar herramientas Swift detectadas.")
    swiftdev_sub.add_parser("help", help="Mostrar política y ejemplos Swift.")
    for command_name, description in (
        ("inspect", "Inspeccionar el proyecto sin ejecutar Package.swift."),
        ("manifest", "Validar Package.swift como texto UTF-8."),
        ("syntax", "Comprobar sintaxis con swiftc -parse."),
        ("format", "Comprobar swift-format sin reescribir archivos."),
        ("build", "Compilar SwiftPM sin resolución automática."),
        ("test", "Ejecutar tests SwiftPM sin resolución automática."),
    ):
        command_parser = swiftdev_sub.add_parser(command_name, help=description)
        command_parser.add_argument("path", type=Path)
        command_parser.add_argument("--configuration", choices=("debug", "release"))
        command_parser.add_argument("--max-files", type=int)
        command_parser.add_argument("--allow-root-once", action="store_true")
        command_parser.add_argument("--approve", action="store_true", required=True)
    swiftdev_verify = swiftdev_sub.add_parser(
        "verify", help="Ejecutar el flujo Swift completo y guardar el resultado."
    )
    swiftdev_verify.add_argument("path", type=Path)
    for option in ("manifest", "syntax", "format", "build", "tests"):
        swiftdev_verify.add_argument(
            f"--{option}", action=argparse.BooleanOptionalAction, default=None
        )
    swiftdev_verify.add_argument("--configuration", choices=("debug", "release"))
    swiftdev_verify.add_argument("--fail-fast", action=argparse.BooleanOptionalAction, default=None)
    swiftdev_verify.add_argument(
        "--require-tools", action=argparse.BooleanOptionalAction, default=None
    )
    swiftdev_verify.add_argument("--max-files", type=int)
    swiftdev_verify.add_argument("--allow-root-once", action="store_true")
    swiftdev_verify.add_argument("--approve", action="store_true", required=True)
    swiftdev_history = swiftdev_sub.add_parser(
        "history", help="Listar verificaciones Swift anteriores."
    )
    swiftdev_history.add_argument("path", type=Path, nargs="?")
    swiftdev_history.add_argument("--limit", type=int, default=20)
    swiftdev_report = swiftdev_sub.add_parser("report", help="Mostrar una verificación Swift.")
    swiftdev_report.add_argument("run_id")
    swiftdev_compare = swiftdev_sub.add_parser("compare", help="Comparar dos verificaciones Swift.")
    swiftdev_compare.add_argument("first_run_id")
    swiftdev_compare.add_argument("second_run_id")

    dartdev_parser = sub.add_parser(
        "dartdev", help="Ejecutar verificaciones Dart y Flutter controladas."
    )
    dartdev_sub = dartdev_parser.add_subparsers(dest="dartdev_command", required=True)
    dartdev_sub.add_parser("status", help="Mostrar herramientas Dart/Flutter detectadas.")
    dartdev_sub.add_parser("help", help="Mostrar política y ejemplos Dart/Flutter.")
    for command_name, description in (
        ("inspect", "Inspeccionar el proyecto sin ejecutar Dart o Flutter."),
        ("descriptor", "Validar pubspec.yaml y analysis_options.yaml."),
        ("format", "Comprobar dart format sin modificar archivos."),
        ("analyze", "Ejecutar dart analyze o flutter analyze sin pub get."),
        ("test", "Ejecutar tests Dart con argumentos fijos."),
        ("flutter-test", "Ejecutar tests Flutter con --no-pub."),
    ):
        command_parser = dartdev_sub.add_parser(command_name, help=description)
        command_parser.add_argument("path", type=Path)
        command_parser.add_argument("--max-files", type=int)
        command_parser.add_argument("--allow-root-once", action="store_true")
        command_parser.add_argument("--approve", action="store_true", required=True)
    dartdev_verify = dartdev_sub.add_parser(
        "verify", help="Ejecutar el flujo Dart/Flutter completo y guardar el resultado."
    )
    dartdev_verify.add_argument("path", type=Path)
    for option in ("descriptor", "format", "analyze", "tests"):
        dartdev_verify.add_argument(
            f"--{option}", action=argparse.BooleanOptionalAction, default=None
        )
    dartdev_verify.add_argument("--test-runner", choices=("auto", "dart", "flutter"))
    dartdev_verify.add_argument("--fail-fast", action=argparse.BooleanOptionalAction, default=None)
    dartdev_verify.add_argument(
        "--require-tools", action=argparse.BooleanOptionalAction, default=None
    )
    dartdev_verify.add_argument("--max-files", type=int)
    dartdev_verify.add_argument("--allow-root-once", action="store_true")
    dartdev_verify.add_argument("--approve", action="store_true", required=True)
    dartdev_history = dartdev_sub.add_parser(
        "history", help="Listar verificaciones Dart/Flutter anteriores."
    )
    dartdev_history.add_argument("path", type=Path, nargs="?")
    dartdev_history.add_argument("--limit", type=int, default=20)
    dartdev_report = dartdev_sub.add_parser("report", help="Mostrar una verificación Dart/Flutter.")
    dartdev_report.add_argument("run_id")
    dartdev_compare = dartdev_sub.add_parser(
        "compare", help="Comparar dos verificaciones Dart/Flutter."
    )
    dartdev_compare.add_argument("first_run_id")
    dartdev_compare.add_argument("second_run_id")

    sqldev_parser = sub.add_parser(
        "sqldev", help="Ejecutar verificaciones SQL y SQLite controladas."
    )
    sqldev_sub = sqldev_parser.add_subparsers(dest="sqldev_command", required=True)
    sqldev_sub.add_parser("status", help="Mostrar capacidades SQL disponibles.")
    sqldev_sub.add_parser("help", help="Mostrar política y ejemplos SQL.")
    for command_name, description in (
        ("inspect", "Inspeccionar SQL, migraciones y bases sin ejecutar consultas."),
        ("static", "Validar SQL estático y mutaciones no autorizadas."),
        ("migrations", "Validar versiones y operaciones destructivas."),
        ("schema", "Inspeccionar esquemas SQLite en modo solo lectura."),
    ):
        command_parser = sqldev_sub.add_parser(command_name, help=description)
        command_parser.add_argument("path", type=Path)
        command_parser.add_argument(
            "--dialect",
            choices=("auto", "generic", "sqlite", "mysql", "mariadb", "postgresql"),
        )
        command_parser.add_argument("--max-files", type=int)
        command_parser.add_argument("--max-database-files", type=int)
        command_parser.add_argument("--allow-root-once", action="store_true")
        command_parser.add_argument("--approve", action="store_true", required=True)
    sqldev_plan = sqldev_sub.add_parser(
        "plan", help="Generar EXPLAIN QUERY PLAN para una consulta SQLite SELECT."
    )
    sqldev_plan.add_argument("database", type=Path)
    query_group = sqldev_plan.add_mutually_exclusive_group(required=True)
    query_group.add_argument("--query")
    query_group.add_argument("--query-file", type=Path)
    sqldev_plan.add_argument("--allow-root-once", action="store_true")
    sqldev_plan.add_argument("--approve", action="store_true", required=True)
    sqldev_verify = sqldev_sub.add_parser(
        "verify", help="Ejecutar el flujo SQL completo y guardar el resultado."
    )
    sqldev_verify.add_argument("path", type=Path)
    for option in (
        "static",
        "migrations",
        "schema",
        "allow-mutating-sql",
        "allow-destructive-migrations",
        "fail-fast",
    ):
        sqldev_verify.add_argument(
            f"--{option}",
            action=argparse.BooleanOptionalAction,
            default=None,
        )
    sqldev_verify.add_argument(
        "--dialect",
        choices=("auto", "generic", "sqlite", "mysql", "mariadb", "postgresql"),
    )
    sqldev_verify.add_argument("--max-files", type=int)
    sqldev_verify.add_argument("--max-database-files", type=int)
    sqldev_verify.add_argument("--allow-root-once", action="store_true")
    sqldev_verify.add_argument("--approve", action="store_true", required=True)
    sqldev_history = sqldev_sub.add_parser("history", help="Listar verificaciones SQL anteriores.")
    sqldev_history.add_argument("path", type=Path, nargs="?")
    sqldev_history.add_argument("--limit", type=int, default=20)
    sqldev_report = sqldev_sub.add_parser("report", help="Mostrar una verificación SQL.")
    sqldev_report.add_argument("run_id")
    sqldev_compare = sqldev_sub.add_parser("compare", help="Comparar dos verificaciones SQL.")
    sqldev_compare.add_argument("first_run_id")
    sqldev_compare.add_argument("second_run_id")

    nativedev_parser = sub.add_parser(
        "nativedev", help="Ejecutar verificaciones C y C++ controladas."
    )
    nativedev_sub = nativedev_parser.add_subparsers(dest="nativedev_command", required=True)
    nativedev_sub.add_parser("status", help="Mostrar herramientas C/C++ detectadas.")
    nativedev_sub.add_parser("help", help="Mostrar política y ejemplos C/C++.")
    for command_name, description in (
        ("inspect", "Inspeccionar el proyecto sin ejecutar código."),
        ("descriptor", "Validar CMakeLists.txt sin ejecutar CMake."),
        ("c-syntax", "Comprobar sintaxis C con GCC o Clang."),
        ("cpp-syntax", "Comprobar sintaxis C++ con G++ o Clang++."),
        ("static", "Ejecutar cppcheck con argumentos fijos."),
        ("build", "Configurar y compilar CMake en un directorio temporal."),
        ("test", "Configurar, compilar y ejecutar CTest."),
    ):
        command_parser = nativedev_sub.add_parser(command_name, help=description)
        command_parser.add_argument("path", type=Path)
        command_parser.add_argument("--compiler", choices=("auto", "gcc", "clang"))
        command_parser.add_argument("--c-standard", choices=("c11", "c17", "c23"))
        command_parser.add_argument("--cpp-standard", choices=("c++17", "c++20", "c++23"))
        command_parser.add_argument("--max-files", type=int)
        command_parser.add_argument("--allow-root-once", action="store_true")
        command_parser.add_argument("--approve", action="store_true", required=True)
    nativedev_verify = nativedev_sub.add_parser(
        "verify", help="Ejecutar el flujo C/C++ completo y guardar el resultado."
    )
    nativedev_verify.add_argument("path", type=Path)
    for option in (
        "descriptor",
        "c-syntax",
        "cpp-syntax",
        "static",
        "build",
        "tests",
        "fail-fast",
        "require-tools",
    ):
        nativedev_verify.add_argument(
            f"--{option}",
            action=argparse.BooleanOptionalAction,
            default=None,
        )
    nativedev_verify.add_argument("--compiler", choices=("auto", "gcc", "clang"))
    nativedev_verify.add_argument("--c-standard", choices=("c11", "c17", "c23"))
    nativedev_verify.add_argument("--cpp-standard", choices=("c++17", "c++20", "c++23"))
    nativedev_verify.add_argument("--max-files", type=int)
    nativedev_verify.add_argument("--allow-root-once", action="store_true")
    nativedev_verify.add_argument("--approve", action="store_true", required=True)
    nativedev_history = nativedev_sub.add_parser(
        "history", help="Listar verificaciones C/C++ anteriores."
    )
    nativedev_history.add_argument("path", type=Path, nargs="?")
    nativedev_history.add_argument("--limit", type=int, default=20)
    nativedev_report = nativedev_sub.add_parser("report", help="Mostrar una verificación C/C++.")
    nativedev_report.add_argument("run_id")
    nativedev_compare = nativedev_sub.add_parser(
        "compare", help="Comparar dos verificaciones C/C++."
    )
    nativedev_compare.add_argument("first_run_id")
    nativedev_compare.add_argument("second_run_id")

    rubydev_parser = sub.add_parser("rubydev", help="Ejecutar verificaciones Ruby controladas.")
    rubydev_sub = rubydev_parser.add_subparsers(dest="rubydev_command", required=True)
    rubydev_sub.add_parser("status", help="Mostrar herramientas Ruby detectadas.")
    rubydev_sub.add_parser("help", help="Mostrar política y ejemplos Ruby.")
    for command_name, description in (
        ("inspect", "Inspeccionar el proyecto sin ejecutar código."),
        ("descriptor", "Validar Gemfile y gemspecs sin ejecutarlos."),
        ("bundle", "Comprobar dependencias con bundle check."),
        ("syntax", "Comprobar sintaxis con ruby -c."),
        ("rubocop", "Ejecutar RuboCop sin autocorrecciones."),
        ("test", "Ejecutar RSpec o Minitest."),
    ):
        command_parser = rubydev_sub.add_parser(command_name, help=description)
        command_parser.add_argument("path", type=Path)
        command_parser.add_argument("--test-framework", choices=("auto", "rspec", "minitest"))
        command_parser.add_argument("--max-files", type=int)
        command_parser.add_argument("--allow-root-once", action="store_true")
        command_parser.add_argument("--approve", action="store_true", required=True)
    rubydev_verify = rubydev_sub.add_parser(
        "verify", help="Ejecutar el flujo Ruby completo y guardar el resultado."
    )
    rubydev_verify.add_argument("path", type=Path)
    for option in (
        "descriptor",
        "bundle",
        "syntax",
        "rubocop",
        "tests",
        "fail-fast",
        "require-tools",
    ):
        rubydev_verify.add_argument(
            f"--{option}",
            action=argparse.BooleanOptionalAction,
            default=None,
        )
    rubydev_verify.add_argument("--test-framework", choices=("auto", "rspec", "minitest"))
    rubydev_verify.add_argument("--max-files", type=int)
    rubydev_verify.add_argument("--allow-root-once", action="store_true")
    rubydev_verify.add_argument("--approve", action="store_true", required=True)
    rubydev_history = rubydev_sub.add_parser(
        "history", help="Listar verificaciones Ruby anteriores."
    )
    rubydev_history.add_argument("path", type=Path, nargs="?")
    rubydev_history.add_argument("--limit", type=int, default=20)
    rubydev_report = rubydev_sub.add_parser("report", help="Mostrar una verificación Ruby.")
    rubydev_report.add_argument("run_id")
    rubydev_compare = rubydev_sub.add_parser("compare", help="Comparar dos verificaciones Ruby.")
    rubydev_compare.add_argument("first_run_id")
    rubydev_compare.add_argument("second_run_id")

    godev_parser = sub.add_parser("godev", help="Ejecutar verificaciones Go controladas.")
    godev_sub = godev_parser.add_subparsers(dest="godev_command", required=True)
    godev_sub.add_parser("status", help="Mostrar herramientas Go detectadas.")
    godev_sub.add_parser("help", help="Mostrar política y ejemplos Go.")
    for command_name, description in (
        ("inspect", "Inspeccionar el proyecto sin ejecutar herramientas."),
        ("module", "Validar go.mod y go.work sin ejecutar go."),
        ("fmt", "Comprobar formato con gofmt -d."),
        ("vet", "Ejecutar go vet sin red."),
        ("build", "Compilar paquetes con go build sin red."),
        ("test", "Ejecutar go test sin red."),
    ):
        command_parser = godev_sub.add_parser(command_name, help=description)
        command_parser.add_argument("path", type=Path)
        command_parser.add_argument("--test-mode", choices=("auto", "short", "full"))
        command_parser.add_argument("--max-files", type=int)
        command_parser.add_argument("--allow-root-once", action="store_true")
        command_parser.add_argument("--approve", action="store_true", required=True)
    godev_verify = godev_sub.add_parser(
        "verify", help="Ejecutar el flujo Go completo y guardar el resultado."
    )
    godev_verify.add_argument("path", type=Path)
    for option in (
        "module",
        "fmt",
        "vet",
        "build",
        "tests",
        "fail-fast",
        "require-tools",
    ):
        godev_verify.add_argument(
            f"--{option}",
            action=argparse.BooleanOptionalAction,
            default=None,
        )
    godev_verify.add_argument("--test-mode", choices=("auto", "short", "full"))
    godev_verify.add_argument("--max-files", type=int)
    godev_verify.add_argument("--allow-root-once", action="store_true")
    godev_verify.add_argument("--approve", action="store_true", required=True)
    godev_history = godev_sub.add_parser("history", help="Listar verificaciones Go anteriores.")
    godev_history.add_argument("path", type=Path, nargs="?")
    godev_history.add_argument("--limit", type=int, default=20)
    godev_report = godev_sub.add_parser("report", help="Mostrar una verificación Go.")
    godev_report.add_argument("run_id")
    godev_compare = godev_sub.add_parser("compare", help="Comparar dos verificaciones Go.")
    godev_compare.add_argument("first_run_id")
    godev_compare.add_argument("second_run_id")

    rustdev_parser = sub.add_parser("rustdev", help="Ejecutar verificaciones Rust controladas.")
    rustdev_sub = rustdev_parser.add_subparsers(dest="rustdev_command", required=True)
    rustdev_sub.add_parser("status", help="Mostrar herramientas Rust detectadas.")
    rustdev_sub.add_parser("help", help="Mostrar política y ejemplos Rust.")
    for command_name, description in (
        ("inspect", "Inspeccionar el proyecto sin ejecutar herramientas."),
        ("manifest", "Validar Cargo.toml sin ejecutar Cargo."),
        ("fmt", "Comprobar formato con cargo fmt --check."),
        ("check", "Ejecutar cargo check offline y locked."),
        ("clippy", "Ejecutar Clippy sin fixes."),
        ("test", "Ejecutar cargo test offline y locked."),
    ):
        command_parser = rustdev_sub.add_parser(command_name, help=description)
        command_parser.add_argument("path", type=Path)
        command_parser.add_argument("--feature-mode", choices=("default", "all"))
        command_parser.add_argument("--max-files", type=int)
        command_parser.add_argument("--allow-root-once", action="store_true")
        command_parser.add_argument("--approve", action="store_true", required=True)
    rustdev_verify = rustdev_sub.add_parser(
        "verify", help="Ejecutar el flujo Rust completo y guardar el resultado."
    )
    rustdev_verify.add_argument("path", type=Path)
    for option in (
        "manifest",
        "fmt",
        "check",
        "clippy",
        "tests",
        "fail-fast",
        "require-tools",
    ):
        rustdev_verify.add_argument(
            f"--{option}",
            action=argparse.BooleanOptionalAction,
            default=None,
        )
    rustdev_verify.add_argument("--feature-mode", choices=("default", "all"))
    rustdev_verify.add_argument("--max-files", type=int)
    rustdev_verify.add_argument("--allow-root-once", action="store_true")
    rustdev_verify.add_argument("--approve", action="store_true", required=True)
    rustdev_history = rustdev_sub.add_parser(
        "history", help="Listar verificaciones Rust anteriores."
    )
    rustdev_history.add_argument("path", type=Path, nargs="?")
    rustdev_history.add_argument("--limit", type=int, default=20)
    rustdev_report = rustdev_sub.add_parser("report", help="Mostrar una verificación Rust.")
    rustdev_report.add_argument("run_id")
    rustdev_compare = rustdev_sub.add_parser("compare", help="Comparar dos verificaciones Rust.")
    rustdev_compare.add_argument("first_run_id")
    rustdev_compare.add_argument("second_run_id")

    validate_parser = sub.add_parser("validate", help="Validar sintaxis de un archivo.")
    validate_parser.add_argument("path", type=Path)

    skill_parser = sub.add_parser("skill", help="Listar o ejecutar skills.")
    skill_sub = skill_parser.add_subparsers(dest="skill_command", required=True)
    skill_sub.add_parser("list")
    skill_sub.add_parser("help", help="Mostrar ayuda de skills y autorización.")
    skill_run = skill_sub.add_parser("run")
    skill_run.add_argument("name")
    skill_run.add_argument("--params", default="{}", help="Objeto JSON con parámetros.")
    skill_run.add_argument("--approve", action="store_true")
    skill_plan = skill_sub.add_parser(
        "plan", help="Mostrar la ejecución prevista sin iniciar procesos."
    )
    skill_plan.add_argument("name")
    skill_plan.add_argument("--params", default="{}", help="Objeto JSON con parámetros.")
    skill_inspect = skill_sub.add_parser(
        "inspect", help="Mostrar riesgo y requisitos de una skill."
    )
    skill_inspect.add_argument("name")

    assistant_parser = sub.add_parser(
        "assistant", help="Planificar y ejecutar acciones supervisadas."
    )
    assistant_sub = assistant_parser.add_subparsers(dest="assistant_command", required=True)
    assistant_sub.add_parser("status", help="Mostrar límites y capacidades de orquestación.")
    assistant_sub.add_parser(
        "help", help="Mostrar ejemplos y fronteras de los planes supervisados."
    )
    assistant_plan = assistant_sub.add_parser(
        "plan", help="Proponer un plan sin ejecutar ninguna skill."
    )
    assistant_plan.add_argument("text")
    assistant_run = assistant_sub.add_parser(
        "run", help="Aprobar y ejecutar un plan guardado exacto una sola vez."
    )
    assistant_run.add_argument("plan_id")
    assistant_run.add_argument("--approve", action="store_true", required=True)
    assistant_history = assistant_sub.add_parser(
        "history", help="Listar planes supervisados ejecutados."
    )
    assistant_history.add_argument("--limit", type=int, default=20)
    assistant_report = assistant_sub.add_parser(
        "report", help="Mostrar un plan supervisado por id de ejecución."
    )
    assistant_report.add_argument("run_id")
    assistant_change_plan = assistant_sub.add_parser(
        "change-plan",
        help="Generar un diff revisable para archivos exactos sin escribirlos.",
    )
    assistant_change_plan.add_argument("project_root")
    assistant_change_plan.add_argument(
        "--file", action="append", required=True, dest="change_files"
    )
    assistant_change_plan.add_argument("--instruction", required=True)
    assistant_change_plan.add_argument("--allow-root-once", action="store_true")
    assistant_change_show = assistant_sub.add_parser(
        "change-show", help="Mostrar una propuesta y su diff exacto."
    )
    assistant_change_show.add_argument("proposal_id")
    assistant_change_apply = assistant_sub.add_parser(
        "change-apply", help="Aplicar una propuesta exacta una sola vez."
    )
    assistant_change_apply.add_argument("proposal_id")
    assistant_change_apply.add_argument("--approve", action="store_true", required=True)
    assistant_change_apply.add_argument("--allow-root-once", action="store_true")
    assistant_change_reject = assistant_sub.add_parser(
        "change-reject", help="Rechazar una propuesta pendiente sin escribir archivos."
    )
    assistant_change_reject.add_argument("proposal_id")
    assistant_change_reject.add_argument("--approve", action="store_true", required=True)
    assistant_changes = assistant_sub.add_parser(
        "changes", help="Listar propuestas de cambios recientes."
    )
    assistant_changes.add_argument("--limit", type=int, default=20)

    assistant_validate_plan = assistant_sub.add_parser(
        "validate-plan",
        help="Crear un plan exacto para validar una propuesta ya aplicada.",
    )
    assistant_validate_plan.add_argument("proposal_id")
    assistant_validate_plan.add_argument("--request", required=True)
    assistant_validate_run = assistant_sub.add_parser(
        "validate-run",
        help="Aprobar y ejecutar una validación vinculada una sola vez.",
    )
    assistant_validate_run.add_argument("cycle_id")
    assistant_validate_run.add_argument("--approve", action="store_true", required=True)
    assistant_repair_plan = assistant_sub.add_parser(
        "repair-plan",
        help="Generar una reparación nueva desde resultados reales fallidos.",
    )
    assistant_repair_plan.add_argument("cycle_id")
    assistant_repair_plan.add_argument("--instruction", required=True)
    assistant_repair_plan.add_argument("--allow-root-once", action="store_true")
    assistant_cycle_show = assistant_sub.add_parser(
        "cycle-show", help="Mostrar un ciclo de validación y reparación."
    )
    assistant_cycle_show.add_argument("cycle_id")
    assistant_cycles = assistant_sub.add_parser(
        "cycles", help="Listar ciclos supervisados recientes."
    )
    assistant_cycles.add_argument("--limit", type=int, default=20)
    assistant_session_start = assistant_sub.add_parser(
        "session-start", help="Vincular una propuesta existente a una sesión."
    )
    assistant_session_start.add_argument("proposal_id")
    assistant_session_start.add_argument("--objective")
    assistant_sessions = assistant_sub.add_parser(
        "sessions", help="Listar sesiones de desarrollo supervisadas."
    )
    assistant_sessions.add_argument("--limit", type=int, default=20)
    assistant_session_show = assistant_sub.add_parser(
        "session-show", help="Mostrar la línea de tiempo de una sesión."
    )
    assistant_session_show.add_argument("session_id")
    assistant_session_next = assistant_sub.add_parser(
        "session-next", help="Mostrar siguientes acciones sin ejecutar ninguna."
    )
    assistant_session_next.add_argument("session_id")
    assistant_session_next.add_argument("--language", default="es")
    assistant_session_close = assistant_sub.add_parser(
        "session-close", help="Cerrar explícitamente una sesión sin ejecutar acciones."
    )
    assistant_session_close.add_argument("session_id")
    assistant_session_close.add_argument("--approve", action="store_true", required=True)

    executive_evaluate = assistant_sub.add_parser(
        "executive-evaluate",
        help="Evaluar intención, riesgo, contexto y ruta sin ejecutar acciones.",
    )
    executive_evaluate.add_argument("text")
    executive_evaluate.add_argument("--domain", default="")
    executive_evaluate.add_argument("--project", default="")
    executive_decisions = assistant_sub.add_parser(
        "executive-decisions", help="Listar decisiones ejecutivas sin prompts crudos."
    )
    executive_decisions.add_argument("--limit", type=int, default=50)
    executive_show = assistant_sub.add_parser(
        "executive-decision-show", help="Mostrar una decisión ejecutiva estructurada."
    )
    executive_show.add_argument("decision_id")
    goal_create = assistant_sub.add_parser(
        "goal-create", help="Crear un objetivo persistente revisado."
    )
    goal_create.add_argument("--title", required=True)
    goal_create.add_argument("--description", default="")
    goal_create.add_argument("--domain", default="")
    goal_create.add_argument("--project", default="")
    goal_create.add_argument(
        "--priority", choices=("low", "normal", "high", "critical"), default="normal"
    )
    goal_create.add_argument("--target-date")
    goal_create.add_argument("--next-action", default="")
    goal_create.add_argument("--approve", action="store_true", required=True)
    goals = assistant_sub.add_parser("goals", help="Listar objetivos persistentes.")
    goals.add_argument(
        "--status",
        choices=("draft", "active", "blocked", "waiting", "completed", "cancelled", "all"),
        default="all",
    )
    goals.add_argument("--limit", type=int, default=100)
    goal_show = assistant_sub.add_parser("goal-show", help="Mostrar objetivo y tareas.")
    goal_show.add_argument("goal_id")
    goal_update = assistant_sub.add_parser(
        "goal-update", help="Actualizar estado o siguiente acción de un objetivo."
    )
    goal_update.add_argument("goal_id")
    goal_update.add_argument(
        "--status",
        choices=("draft", "active", "blocked", "waiting", "completed", "cancelled"),
    )
    goal_update.add_argument("--next-action")
    goal_update.add_argument("--approve", action="store_true", required=True)
    task_create = assistant_sub.add_parser(
        "task-create", help="Crear una tarea vinculada a un objetivo."
    )
    task_create.add_argument("goal_id")
    task_create.add_argument("--title", required=True)
    task_create.add_argument(
        "--priority", choices=("low", "normal", "high", "critical"), default="normal"
    )
    task_create.add_argument("--due-date")
    task_create.add_argument("--depends-on", action="append", default=[])
    task_create.add_argument("--approve", action="store_true", required=True)
    task_complete = assistant_sub.add_parser(
        "task-complete", help="Cerrar una tarea con evidencia explícita."
    )
    task_complete.add_argument("task_id")
    task_complete.add_argument("--evidence", required=True)
    task_complete.add_argument("--approve", action="store_true", required=True)
    verify_outcome = assistant_sub.add_parser(
        "verify-outcome", help="Registrar verificación estructurada de un resultado."
    )
    verify_outcome.add_argument("--decision-id")
    verify_outcome.add_argument("--expected", required=True)
    verify_outcome.add_argument("--observed", required=True)
    verify_outcome.add_argument("--method", required=True)
    verify_outcome.add_argument(
        "--status", choices=("success", "partial", "failed", "inconclusive"), required=True
    )
    verify_outcome.add_argument("--evidence-json", default="{}")
    verify_outcome.add_argument("--approve", action="store_true", required=True)
    verifications = assistant_sub.add_parser(
        "verifications", help="Listar verificaciones de resultados."
    )
    verifications.add_argument("--limit", type=int, default=100)

    assistant_sub.add_parser(
        "organizer-status", help="Mostrar el estado del organizador personal local."
    )
    commitment_create = assistant_sub.add_parser(
        "commitment-create", help="Crear un compromiso local revisado."
    )
    commitment_create.add_argument("--title", required=True)
    commitment_create.add_argument("--description", default="")
    commitment_create.add_argument("--date", required=True)
    commitment_create.add_argument("--time")
    commitment_create.add_argument("--timezone", default="America/Santiago")
    commitment_create.add_argument("--domain", default="organizacion_personal")
    commitment_create.add_argument("--project", default="")
    commitment_create.add_argument(
        "--priority", choices=("low", "normal", "high", "critical"), default="normal"
    )
    commitment_create.add_argument(
        "--recurrence",
        choices=("once", "daily", "weekly", "monthly", "yearly"),
        default="once",
    )
    commitment_create.add_argument("--interval", type=int, default=1)
    commitment_create.add_argument("--weekday", action="append", default=[])
    commitment_create.add_argument("--until")
    commitment_create.add_argument("--goal-id", default="")
    commitment_create.add_argument("--task-id", default="")
    commitment_create.add_argument("--approve", action="store_true", required=True)

    birthday_create = assistant_sub.add_parser(
        "birthday-create", help="Registrar un cumpleaños local."
    )
    birthday_create.add_argument("--person", required=True)
    birthday_create.add_argument("--month", type=int, required=True)
    birthday_create.add_argument("--day", type=int, required=True)
    birthday_create.add_argument("--year", type=int)
    birthday_create.add_argument("--timezone", default="America/Santiago")
    birthday_create.add_argument("--domain", default="organizacion_personal")
    birthday_create.add_argument("--project", default="")
    birthday_create.add_argument(
        "--priority", choices=("low", "normal", "high", "critical"), default="normal"
    )
    birthday_create.add_argument("--approve", action="store_true", required=True)

    routine_create = assistant_sub.add_parser(
        "routine-create", help="Crear una rutina recurrente local."
    )
    routine_create.add_argument("--title", required=True)
    routine_create.add_argument("--description", default="")
    routine_create.add_argument("--start-date", required=True)
    routine_create.add_argument("--time")
    routine_create.add_argument("--timezone", default="America/Santiago")
    routine_create.add_argument("--domain", default="organizacion_personal")
    routine_create.add_argument("--project", default="")
    routine_create.add_argument(
        "--priority", choices=("low", "normal", "high", "critical"), default="normal"
    )
    routine_create.add_argument(
        "--recurrence", choices=("daily", "weekly", "monthly"), default="daily"
    )
    routine_create.add_argument("--interval", type=int, default=1)
    routine_create.add_argument("--weekday", action="append", default=[])
    routine_create.add_argument("--until")
    routine_create.add_argument("--goal-id", default="")
    routine_create.add_argument("--task-id", default="")
    routine_create.add_argument("--approve", action="store_true", required=True)

    organizer_items = assistant_sub.add_parser(
        "organizer-items", help="Listar compromisos, cumpleaños y rutinas."
    )
    organizer_items.add_argument(
        "--type", choices=("commitment", "birthday", "routine", "all"), default="all"
    )
    organizer_items.add_argument(
        "--status",
        choices=("active", "paused", "completed", "cancelled", "all"),
        default="all",
    )
    organizer_items.add_argument("--domain", default="")
    organizer_items.add_argument("--project", default="")
    organizer_items.add_argument("--limit", type=int, default=100)
    organizer_show = assistant_sub.add_parser(
        "organizer-show", help="Mostrar un elemento y sus recordatorios."
    )
    organizer_show.add_argument("item_id")
    organizer_update = assistant_sub.add_parser(
        "organizer-update", help="Actualizar el estado de un elemento."
    )
    organizer_update.add_argument("item_id")
    organizer_update.add_argument(
        "--status", choices=("active", "paused", "completed", "cancelled"), required=True
    )
    organizer_update.add_argument("--approve", action="store_true", required=True)

    routine_checkin = assistant_sub.add_parser(
        "routine-checkin", help="Registrar un check-in explícito de rutina."
    )
    routine_checkin.add_argument("routine_id")
    routine_checkin.add_argument("--date", required=True)
    routine_checkin.add_argument("--status", choices=("completed", "skipped"), required=True)
    routine_checkin.add_argument("--note", default="")
    routine_checkin.add_argument("--approve", action="store_true", required=True)

    reminder_propose = assistant_sub.add_parser(
        "reminder-propose", help="Proponer un recordatorio sin activarlo."
    )
    reminder_propose.add_argument("item_id")
    reminder_propose.add_argument("--minutes-before", type=int, required=True)
    reminder_propose.add_argument("--approve", action="store_true", required=True)
    reminder_review = assistant_sub.add_parser(
        "reminder-review", help="Aprobar o rechazar una propuesta de recordatorio."
    )
    reminder_review.add_argument("reminder_id")
    reminder_review.add_argument("--decision", choices=("approve", "reject"), required=True)
    reminder_review.add_argument("--approve", action="store_true", required=True)
    reminders = assistant_sub.add_parser(
        "reminders", help="Listar propuestas y recordatorios revisados."
    )
    reminders.add_argument(
        "--status",
        choices=("proposed", "approved", "rejected", "cancelled", "all"),
        default="all",
    )
    reminders.add_argument("--limit", type=int, default=100)

    daily_brief = assistant_sub.add_parser(
        "daily-brief", help="Generar una agenda diaria determinista."
    )
    daily_brief.add_argument("--date")
    daily_brief.add_argument("--timezone", default="America/Santiago")
    daily_brief.add_argument("--domain", default="")
    daily_brief.add_argument("--project", default="")
    upcoming = assistant_sub.add_parser(
        "upcoming", help="Listar ocurrencias próximas sin programar tareas."
    )
    upcoming.add_argument("--start-date")
    upcoming.add_argument("--days", type=int, default=7)
    upcoming.add_argument("--timezone", default="America/Santiago")
    upcoming.add_argument("--domain", default="")
    upcoming.add_argument("--project", default="")

    assistant_sub.add_parser(
        "wellbeing-status", help="Mostrar seguimiento y planes de bienestar locales."
    )
    wellbeing_checkin = assistant_sub.add_parser(
        "wellbeing-checkin", help="Registrar un check-in personal revisado."
    )
    wellbeing_checkin.add_argument("--date", required=True)
    wellbeing_checkin.add_argument("--mood", type=int, required=True)
    wellbeing_checkin.add_argument("--energy", type=int, required=True)
    wellbeing_checkin.add_argument("--stress", type=int, required=True)
    wellbeing_checkin.add_argument("--focus", type=int, required=True)
    wellbeing_checkin.add_argument("--sleep-hours", type=float)
    wellbeing_checkin.add_argument("--sleep-quality", type=int)
    wellbeing_checkin.add_argument("--hydration", type=int)
    wellbeing_checkin.add_argument("--nutrition", type=int)
    wellbeing_checkin.add_argument("--activity-minutes", type=int)
    wellbeing_checkin.add_argument("--note", default="")
    wellbeing_checkin.add_argument("--approve", action="store_true", required=True)
    wellbeing_summary = assistant_sub.add_parser(
        "wellbeing-summary", help="Resumir check-ins sin diagnóstico ni modelo."
    )
    wellbeing_summary.add_argument("--days", type=int, default=7)
    wellbeing_summary.add_argument("--end-date")
    wellbeing_checkins = assistant_sub.add_parser(
        "wellbeing-checkins", help="Listar check-ins personales locales."
    )
    wellbeing_checkins.add_argument("--start-date")
    wellbeing_checkins.add_argument("--end-date")
    wellbeing_checkins.add_argument("--limit", type=int, default=100)
    coaching_plan_create = assistant_sub.add_parser(
        "coaching-plan-create", help="Crear un plan personal explícito y revisado."
    )
    coaching_plan_create.add_argument("--title", required=True)
    coaching_plan_create.add_argument("--focus", required=True)
    coaching_plan_create.add_argument("--objective", required=True)
    coaching_plan_create.add_argument("--start-date", required=True)
    coaching_plan_create.add_argument("--review-date")
    coaching_plan_create.add_argument("--action", action="append", required=True)
    coaching_plan_create.add_argument("--approve", action="store_true", required=True)
    coaching_plans = assistant_sub.add_parser(
        "coaching-plans", help="Listar planes personales revisados."
    )
    coaching_plans.add_argument(
        "--status",
        choices=("active", "paused", "completed", "cancelled", "all"),
        default="all",
    )
    coaching_plans.add_argument("--limit", type=int, default=100)
    coaching_plan_show = assistant_sub.add_parser(
        "coaching-plan-show", help="Mostrar un plan y sus acciones."
    )
    coaching_plan_show.add_argument("plan_id")
    coaching_plan_update = assistant_sub.add_parser(
        "coaching-plan-update", help="Actualizar explícitamente el estado de un plan."
    )
    coaching_plan_update.add_argument("plan_id")
    coaching_plan_update.add_argument(
        "--status",
        choices=("active", "paused", "completed", "cancelled"),
        required=True,
    )
    coaching_plan_update.add_argument("--approve", action="store_true", required=True)
    coaching_action_update = assistant_sub.add_parser(
        "coaching-action-update", help="Actualizar una acción de coaching."
    )
    coaching_action_update.add_argument("action_id")
    coaching_action_update.add_argument(
        "--status", choices=("pending", "completed", "skipped"), required=True
    )
    coaching_action_update.add_argument("--approve", action="store_true", required=True)

    assistant_sub.add_parser(
        "automation-status", help="Mostrar límites de automatización supervisada."
    )
    automation_policy_create = assistant_sub.add_parser(
        "automation-policy-create",
        help="Crear una política explícita para una acción local de bajo riesgo.",
    )
    automation_policy_create.add_argument("--title", required=True)
    automation_policy_create.add_argument(
        "--action",
        required=True,
        choices=(
            "daily_brief.prepare",
            "organizer.upcoming.prepare",
            "wellbeing.weekly_summary.prepare",
            "coaching.review.prepare",
            "goal.review.prepare",
            "routine.missed_checkin.suggest",
        ),
    )
    automation_policy_create.add_argument(
        "--level",
        required=True,
        choices=(
            "observe",
            "suggest",
            "prepare",
            "execute_with_approval",
            "execute_under_policy",
        ),
    )
    automation_policy_create.add_argument("--timezone", default="America/Santiago")
    automation_policy_create.add_argument("--window-start")
    automation_policy_create.add_argument("--window-end")
    automation_policy_create.add_argument("--max-runs-per-day", type=int, default=1)
    automation_policy_create.add_argument("--starts-at")
    automation_policy_create.add_argument("--expires-at")
    automation_policy_create.add_argument("--domain", default="")
    automation_policy_create.add_argument("--project", default="")
    automation_policy_create.add_argument("--approve", action="store_true", required=True)
    automation_policies = assistant_sub.add_parser(
        "automation-policies", help="Listar políticas de automatización."
    )
    automation_policies.add_argument(
        "--status", choices=("active", "paused", "revoked", "expired", "all"), default="all"
    )
    automation_policies.add_argument("--limit", type=int, default=100)
    automation_policy_update = assistant_sub.add_parser(
        "automation-policy-update", help="Actualizar el estado de una política."
    )
    automation_policy_update.add_argument("policy_id")
    automation_policy_update.add_argument(
        "--status", choices=("active", "paused", "revoked", "expired"), required=True
    )
    automation_policy_update.add_argument("--approve", action="store_true", required=True)

    automation_create = assistant_sub.add_parser(
        "automation-create", help="Crear una automatización vinculada a una política."
    )
    automation_create.add_argument("policy_id")
    automation_create.add_argument("--title", required=True)
    automation_create.add_argument(
        "--schedule", choices=("once", "daily", "weekly", "monthly"), required=True
    )
    automation_create.add_argument("--start-date", required=True)
    automation_create.add_argument("--time", required=True)
    automation_create.add_argument("--weekday", action="append", default=[])
    automation_create.add_argument("--month-day", type=int)
    automation_create.add_argument("--interval", type=int, default=1)
    automation_create.add_argument("--until")
    automation_create.add_argument("--params", default="{}", help="Objeto JSON acotado.")
    automation_create.add_argument("--approve", action="store_true", required=True)
    automations = assistant_sub.add_parser("automations", help="Listar automatizaciones locales.")
    automations.add_argument(
        "--status", choices=("active", "paused", "completed", "cancelled", "all"), default="all"
    )
    automations.add_argument("--limit", type=int, default=100)
    automation_update = assistant_sub.add_parser(
        "automation-update", help="Actualizar el estado de una automatización."
    )
    automation_update.add_argument("automation_id")
    automation_update.add_argument(
        "--status", choices=("active", "paused", "completed", "cancelled"), required=True
    )
    automation_update.add_argument("--approve", action="store_true", required=True)
    automation_scan = assistant_sub.add_parser(
        "automation-scan", help="Escanear vencimientos en primer plano y materializar ejecuciones."
    )
    automation_scan.add_argument("--now")
    automation_scan.add_argument("--approve", action="store_true", required=True)
    automation_runs = assistant_sub.add_parser(
        "automation-runs", help="Listar ejecuciones de automatización."
    )
    automation_runs.add_argument(
        "--status",
        choices=(
            "pending_approval",
            "observed",
            "suggested",
            "prepared",
            "executed",
            "skipped",
            "failed",
            "all",
        ),
        default="all",
    )
    automation_runs.add_argument("--limit", type=int, default=100)
    automation_run_approve = assistant_sub.add_parser(
        "automation-run-approve", help="Aprobar una ejecución individual pendiente."
    )
    automation_run_approve.add_argument("run_id")
    automation_run_approve.add_argument("--approve", action="store_true", required=True)
    automation_inbox = assistant_sub.add_parser(
        "automation-inbox", help="Listar resultados preparados en la bandeja local."
    )
    automation_inbox.add_argument(
        "--status", choices=("unread", "read", "dismissed", "all"), default="all"
    )
    automation_inbox.add_argument("--limit", type=int, default=100)
    automation_inbox_update = assistant_sub.add_parser(
        "automation-inbox-update", help="Marcar un resultado local como leído o descartado."
    )
    automation_inbox_update.add_argument("inbox_id")
    automation_inbox_update.add_argument(
        "--status", choices=("unread", "read", "dismissed"), required=True
    )
    automation_inbox_update.add_argument("--approve", action="store_true", required=True)

    assistant_sub.add_parser(
        "scheduler-status", help="Mostrar el scheduler local opcional y su bloqueo."
    )
    scheduler_cycle = assistant_sub.add_parser(
        "scheduler-cycle", help="Ejecutar un ciclo local de scheduler en primer plano."
    )
    scheduler_cycle.add_argument("--now")
    scheduler_cycle.add_argument("--approve", action="store_true", required=True)
    scheduler_run = assistant_sub.add_parser(
        "scheduler-run", help="Mantener el scheduler local en primer plano hasta Ctrl+C."
    )
    scheduler_run.add_argument("--interval-seconds", type=int, default=60)
    scheduler_run.add_argument("--approve", action="store_true", required=True)
    local_notifications = assistant_sub.add_parser(
        "local-notifications", help="Listar notificaciones locales preparadas."
    )
    local_notifications.add_argument(
        "--status", choices=("pending", "seen", "dismissed", "all"), default="all"
    )
    local_notifications.add_argument("--limit", type=int, default=100)
    local_notification_update = assistant_sub.add_parser(
        "local-notification-update", help="Marcar una notificación local."
    )
    local_notification_update.add_argument("notification_id")
    local_notification_update.add_argument(
        "--status", choices=("pending", "seen", "dismissed"), required=True
    )
    local_notification_update.add_argument("--approve", action="store_true", required=True)

    understand = assistant_sub.add_parser(
        "understand",
        help="Resolver intención y entidades sin ejecutar acciones personales.",
    )
    understand.add_argument("text")
    assistant_sub.add_parser(
        "intent-status", help="Mostrar estado de comprensión semántica revisable."
    )
    intent_resolutions = assistant_sub.add_parser(
        "intent-resolutions", help="Listar resoluciones sin prompts crudos."
    )
    intent_resolutions.add_argument("--limit", type=int, default=100)
    intent_propose = assistant_sub.add_parser(
        "intent-learning-propose",
        help="Proponer una frase corregida para una intención canónica.",
    )
    intent_propose.add_argument("--phrase", required=True)
    intent_propose.add_argument("--intent", choices=available_intents(), required=True)
    intent_propose.add_argument("--source", default="owner_correction")
    intent_propose.add_argument("--approve", action="store_true", required=True)
    intent_proposals = assistant_sub.add_parser(
        "intent-learning-proposals", help="Listar propuestas semánticas revisables."
    )
    intent_proposals.add_argument(
        "--status",
        choices=("pending", "approved", "rejected", "all"),
        default="pending",
    )
    intent_proposals.add_argument("--limit", type=int, default=100)
    intent_review = assistant_sub.add_parser(
        "intent-learning-review",
        help="Aprobar o rechazar una frase semántica sin aprendizaje silencioso.",
    )
    intent_review.add_argument("proposal_id")
    intent_review.add_argument("--decision", choices=("approve", "reject"), required=True)
    intent_review.add_argument("--approve", action="store_true", required=True)

    language_parser = sub.add_parser(
        "language", help="Detectar y configurar el idioma de interacción."
    )
    language_sub = language_parser.add_subparsers(dest="language_command", required=True)
    language_sub.add_parser("status", help="Mostrar el modo de idioma actual.")
    language_set = language_sub.add_parser(
        "set", help="Usar detección automática o fijar un idioma."
    )
    language_set.add_argument("language", help="Código ISO corto o 'auto'.")
    language_detect = language_sub.add_parser(
        "detect", help="Detectar el idioma probable de un texto sin usar el modelo."
    )
    language_detect.add_argument("text")

    translate_parser = sub.add_parser(
        "translate",
        help="Traducir primero con bibliotecas locales y usar el modelo solo como respaldo.",
    )
    translate_parser.add_argument("text")
    translate_parser.add_argument("--to", required=True, dest="target_language")

    dictionary_parser = sub.add_parser(
        "dictionary", help="Consultar el lexicón multilingüe local inicial."
    )
    dictionary_sub = dictionary_parser.add_subparsers(dest="dictionary_command", required=True)
    dictionary_sub.add_parser("status", help="Mostrar versión, licencia e idiomas.")
    dictionary_sub.add_parser("languages", help="Listar idiomas disponibles.")
    dictionary_lookup = dictionary_sub.add_parser(
        "lookup", help="Buscar una palabra o expresión sin usar red ni modelo."
    )
    dictionary_lookup.add_argument("term")
    dictionary_lookup.add_argument("--language")
    dictionary_lookup.add_argument("--output-language", default="es")
    dictionary_lookup.add_argument("--dialect")
    dictionary_lookup.add_argument("--limit", type=int, default=5, choices=range(1, 21))
    dictionary_senses = dictionary_sub.add_parser("senses")
    dictionary_senses.add_argument("term")
    dictionary_related = dictionary_sub.add_parser("related")
    dictionary_related.add_argument("term")
    dictionary_related.add_argument("--relation", required=True)
    dictionary_related.add_argument("--sense", required=True)
    overlay_propose = dictionary_sub.add_parser("overlay-propose")
    overlay_propose.add_argument("entry_type")
    overlay_propose.add_argument("expression")
    overlay_propose.add_argument("--payload-json", required=True)
    overlay_propose.add_argument("--approve", action="store_true", required=True)
    overlay_review = dictionary_sub.add_parser("overlay-review")
    overlay_review.add_argument("id")
    overlay_review.add_argument("--decision", choices=("approve", "reject"), required=True)
    overlay_review.add_argument("--approve", action="store_true", required=True)

    persona_parser = sub.add_parser(
        "persona", help="Ver o materializar la identidad canónica del agente."
    )
    persona_sub = persona_parser.add_subparsers(dest="persona_command", required=True)
    persona_sub.add_parser("status", help="Mostrar la identidad activa.")
    persona_init = persona_sub.add_parser(
        "init", help="Crear persona.toml editable con los valores actuales."
    )
    persona_init.add_argument("--force", action="store_true")
    persona_sub.add_parser(
        "setup", help="Configurar nombre, género, pronombres y estilo de interacción."
    )

    model_parser = sub.add_parser("model", help="Descubrir y configurar el motor lingüístico.")
    model_sub = model_parser.add_subparsers(dest="model_command", required=True)
    model_discover = model_sub.add_parser("discover", help="Buscar runtimes y modelos locales.")
    model_discover.add_argument(
        "--root",
        action="append",
        type=Path,
        help="Raíz adicional o reemplazo de búsqueda; se puede repetir.",
    )
    model_discover.add_argument("--max-files", type=int, default=50000)
    model_sub.add_parser("status", help="Mostrar la configuración lingüística.")
    model_configure = model_sub.add_parser("configure", help="Activar llama-cli local.")
    model_configure.add_argument("--binary", type=Path, required=True)
    model_configure.add_argument("--model", type=Path, required=True)
    model_configure.add_argument("--profile", choices=tuple(PROFILES), default="eco")
    model_configure.add_argument(
        "--allow-large-model",
        action="store_true",
        help="Permitir modelos superiores a 2 GiB.",
    )
    model_configure.add_argument("--approve", action="store_true", required=True)
    model_configure_ollama = model_sub.add_parser(
        "configure-ollama",
        help="Activar un modelo servido por Ollama en loopback.",
    )
    model_configure_ollama.add_argument("--model", required=True, help="Nombre lógico en Ollama.")
    model_configure_ollama.add_argument(
        "--endpoint",
        default="http://127.0.0.1:11434",
        help="Solo se admite un endpoint HTTP de loopback.",
    )
    model_configure_ollama.add_argument("--profile", choices=tuple(PROFILES), default="eco")
    model_configure_ollama.add_argument("--license-id", default="unverified")
    model_configure_ollama.add_argument(
        "--role", choices=("runtime", "teacher", "both"), default="runtime"
    )
    model_configure_ollama.add_argument(
        "--teacher-approved",
        action="store_true",
        help="Marcar el modelo como profesor tras revisar su licencia.",
    )
    model_configure_ollama.add_argument(
        "--redistribution-approved",
        action="store_true",
        help="Marcar redistribución como revisada; no copia el modelo.",
    )
    model_configure_ollama.add_argument("--approve", action="store_true", required=True)
    model_ollama_list = model_sub.add_parser(
        "ollama-list", help="Listar modelos de una API Ollama local."
    )
    model_ollama_list.add_argument("--endpoint", default="http://127.0.0.1:11434")
    model_disable = model_sub.add_parser("disable", help="Desactivar el modelo sin borrarlo.")
    model_disable.add_argument("--approve", action="store_true", required=True)
    model_test = model_sub.add_parser("test", help="Probar el motor configurado.")
    model_test.add_argument(
        "text",
        nargs="?",
        default="Responde únicamente: Elyndra local funciona.",
    )
    model_sub.add_parser(
        "tutor-status",
        help="Mostrar tutores locales, límites y política de arbitraje.",
    )
    model_sub.add_parser(
        "tutor-template",
        help="Mostrar un ejemplo de tutors.toml sin escribir archivos.",
    )
    tutor_recommend = model_sub.add_parser(
        "tutor-recommend",
        help="Recomendar un tutor para una tarea sin invocarlo.",
    )
    tutor_recommend.add_argument(
        "task",
        choices=(
            "general_language",
            "translation",
            "summarization",
            "code_explanation",
            "supervised_planning",
            "code_change",
            "ethical_ambiguity",
            "creative_language",
        ),
    )
    tutor_benchmark = model_sub.add_parser(
        "tutor-benchmark",
        help="Ejecutar el benchmark local incorporado con aprobación explícita.",
    )
    tutor_benchmark.add_argument("--tutor")
    tutor_benchmark.add_argument("--approve", action="store_true", required=True)
    tutor_benchmarks = model_sub.add_parser(
        "tutor-benchmarks", help="Listar ejecuciones recientes de benchmarks."
    )
    tutor_benchmarks.add_argument("--limit", type=int, default=20)
    tutor_benchmark_show = model_sub.add_parser(
        "tutor-benchmark-show", help="Mostrar resultados de un benchmark."
    )
    tutor_benchmark_show.add_argument("run_id")
    tutor_selections = model_sub.add_parser(
        "tutor-selections",
        help="Listar decisiones recientes de arbitraje sin prompts crudos.",
    )
    tutor_selections.add_argument("--limit", type=int, default=50)
    model_sub.add_parser(
        "tutor-learning-status",
        help="Mostrar lecciones revisadas y calibración conservadora.",
    )
    tutor_lesson_propose = model_sub.add_parser(
        "tutor-lesson-propose",
        help="Crear una lección compacta pendiente de revisión del propietario.",
    )
    tutor_lesson_propose.add_argument("--tutor", required=True)
    tutor_lesson_propose.add_argument(
        "--task",
        choices=(
            "general_language",
            "translation",
            "summarization",
            "code_explanation",
            "supervised_planning",
            "code_change",
            "ethical_ambiguity",
            "creative_language",
        ),
        required=True,
    )
    tutor_lesson_propose.add_argument("--lesson", required=True)
    tutor_lesson_propose.add_argument(
        "--source",
        choices=("owner_feedback", "reviewed_evidence", "deterministic_evidence"),
        required=True,
    )
    tutor_lesson_propose.add_argument("--source-sha256", required=True)
    tutor_lesson_propose.add_argument("--source-ref")
    tutor_lesson_propose.add_argument("--observed-score", type=float, required=True)
    tutor_lesson_propose.add_argument("--confidence", type=float, default=1.0)
    tutor_lesson_propose.add_argument("--expires-days", type=int)
    tutor_lesson_propose.add_argument("--approve", action="store_true", required=True)
    tutor_lesson_proposals = model_sub.add_parser(
        "tutor-lesson-proposals", help="Listar propuestas de lecciones de tutor."
    )
    tutor_lesson_proposals.add_argument(
        "--status",
        choices=("pending", "approved", "rejected", "expired", "all"),
        default="pending",
    )
    tutor_lesson_proposals.add_argument("--limit", type=int, default=50)
    tutor_lesson_edit = model_sub.add_parser(
        "tutor-lesson-edit", help="Editar una propuesta pendiente antes de aprobarla."
    )
    tutor_lesson_edit.add_argument("public_id")
    tutor_lesson_edit.add_argument("--lesson")
    tutor_lesson_edit.add_argument("--observed-score", type=float)
    tutor_lesson_edit.add_argument("--confidence", type=float)
    tutor_lesson_edit.add_argument("--expires-days", type=int)
    tutor_lesson_edit.add_argument("--clear-expiration", action="store_true")
    tutor_lesson_edit.add_argument("--approve", action="store_true", required=True)
    tutor_lesson_approve = model_sub.add_parser(
        "tutor-lesson-approve", help="Aprobar una lección y activar su calibración."
    )
    tutor_lesson_approve.add_argument("public_id")
    tutor_lesson_approve.add_argument("--approve", action="store_true", required=True)
    tutor_lesson_reject = model_sub.add_parser(
        "tutor-lesson-reject", help="Rechazar una propuesta de lección."
    )
    tutor_lesson_reject.add_argument("public_id")
    tutor_lesson_reject.add_argument("--approve", action="store_true", required=True)
    tutor_lessons = model_sub.add_parser(
        "tutor-lessons", help="Listar lecciones revisadas activas o históricas."
    )
    tutor_lessons.add_argument(
        "--status",
        choices=("active", "expired", "forgotten", "all"),
        default="active",
    )
    tutor_lessons.add_argument("--tutor")
    tutor_lessons.add_argument("--task")
    tutor_lessons.add_argument("--limit", type=int, default=100)
    tutor_lesson_forget = model_sub.add_parser(
        "tutor-lesson-forget", help="Olvidar una lección activa explícitamente."
    )
    tutor_lesson_forget.add_argument("public_id")
    tutor_lesson_forget.add_argument("--approve", action="store_true", required=True)
    tutor_lesson_expire = model_sub.add_parser(
        "tutor-lesson-expire", help="Aplicar expiraciones vencidas explícitamente."
    )
    tutor_lesson_expire.add_argument("--approve", action="store_true", required=True)
    tutor_evidence_compare = model_sub.add_parser(
        "tutor-evidence-compare",
        help="Comparar hashes y crear una propuesta sin autoaprobarla.",
    )
    tutor_evidence_compare.add_argument("--tutor", required=True)
    tutor_evidence_compare.add_argument("--task", required=True)
    tutor_evidence_compare.add_argument("--output-sha256", required=True)
    tutor_evidence_compare.add_argument("--evidence-sha256", required=True)
    tutor_evidence_compare.add_argument(
        "--method", choices=("exact_hash", "owner_review"), default="exact_hash"
    )
    tutor_evidence_compare.add_argument("--outcome", choices=("match", "partial", "mismatch"))
    tutor_evidence_compare.add_argument("--lesson", required=True)
    tutor_evidence_compare.add_argument("--confidence", type=float, default=1.0)
    tutor_evidence_compare.add_argument("--selection-id")
    tutor_evidence_compare.add_argument("--expires-days", type=int)
    tutor_evidence_compare.add_argument("--approve", action="store_true", required=True)
    tutor_evidence_comparisons = model_sub.add_parser(
        "tutor-evidence-comparisons", help="Listar comparaciones sin texto crudo."
    )
    tutor_evidence_comparisons.add_argument("--limit", type=int, default=50)
    tutor_evaluation_plan = model_sub.add_parser(
        "tutor-lesson-evaluation-plan",
        help="Crear un plan exacto de evaluación sin invocar modelos.",
    )
    tutor_evaluation_plan.add_argument("lesson_id")
    tutor_evaluation_plan.add_argument("--auditor")
    tutor_evaluation_plan.add_argument("--approve", action="store_true", required=True)
    tutor_evaluation_run = model_sub.add_parser(
        "tutor-lesson-evaluation-run",
        help="Ejecutar una evaluación aprobada una sola vez y en primer plano.",
    )
    tutor_evaluation_run.add_argument("evaluation_id")
    tutor_evaluation_run.add_argument("--approve", action="store_true", required=True)
    tutor_evaluation_cancel = model_sub.add_parser(
        "tutor-lesson-evaluation-cancel",
        help="Cancelar un plan pendiente sin invocar modelos.",
    )
    tutor_evaluation_cancel.add_argument("evaluation_id")
    tutor_evaluation_cancel.add_argument("--approve", action="store_true", required=True)
    tutor_evaluations = model_sub.add_parser(
        "tutor-lesson-evaluations",
        help="Listar evaluaciones de lecciones y sus recomendaciones.",
    )
    tutor_evaluations.add_argument(
        "--status",
        choices=("pending", "running", "completed", "failed", "cancelled", "all"),
        default="all",
    )
    tutor_evaluations.add_argument("--limit", type=int, default=50)
    tutor_evaluation_show = model_sub.add_parser(
        "tutor-lesson-evaluation-show",
        help="Mostrar una evaluación sin prompts ni salidas crudas.",
    )
    tutor_evaluation_show.add_argument("evaluation_id")
    tutor_knowledge_promote = model_sub.add_parser(
        "tutor-knowledge-promote",
        help="Promover una evaluación validada a conocimiento durable versionado.",
    )
    tutor_knowledge_promote.add_argument("evaluation_id")
    tutor_knowledge_promote.add_argument("--title", required=True)
    tutor_knowledge_promote.add_argument("--supersedes")
    tutor_knowledge_promote.add_argument("--approve", action="store_true", required=True)
    tutor_knowledge = model_sub.add_parser(
        "tutor-knowledge",
        help="Listar conocimiento durable sin eliminar versiones anteriores.",
    )
    tutor_knowledge.add_argument(
        "--status", choices=("active", "superseded", "all"), default="active"
    )
    tutor_knowledge.add_argument("--task")
    tutor_knowledge.add_argument("--limit", type=int, default=100)
    tutor_knowledge_show = model_sub.add_parser(
        "tutor-knowledge-show",
        help="Mostrar procedencia, versión y linaje de un conocimiento.",
    )
    tutor_knowledge_show.add_argument("knowledge_id")
    tutor_knowledge_context = model_sub.add_parser(
        "tutor-knowledge-context",
        help="Previsualizar el contexto durable aplicado a una tarea.",
    )
    tutor_knowledge_context.add_argument("--task", required=True)
    tutor_calibration_show = model_sub.add_parser(
        "tutor-calibration-show",
        help="Mostrar calibración explicable para un tutor y una tarea.",
    )
    tutor_calibration_show.add_argument("--tutor", required=True)
    tutor_calibration_show.add_argument("--task", required=True)
    model_sub.add_parser(
        "knowledge-learning-status",
        help="Mostrar adquisición revisada y conocimiento general durable.",
    )
    knowledge_teach = model_sub.add_parser(
        "knowledge-teach",
        help="Crear una propuesta revisable desde enseñanza explícita del propietario.",
    )
    knowledge_teach.add_argument("--statement", required=True)
    knowledge_teach.add_argument("--subject", required=True)
    knowledge_teach.add_argument(
        "--kind",
        choices=("factual", "conceptual", "procedural", "linguistic", "domain"),
        default="factual",
    )
    knowledge_teach.add_argument("--locale", default="es")
    knowledge_teach.add_argument("--source-observed-at")
    knowledge_teach.add_argument("--revalidate-after")
    knowledge_teach.add_argument("--domain", default="")
    knowledge_teach.add_argument("--project", default="")
    knowledge_teach.add_argument("--approve", action="store_true", required=True)
    knowledge_plan = model_sub.add_parser(
        "knowledge-acquisition-plan",
        help="Congelar evidencia local y crear un plan sin invocar modelos.",
    )
    knowledge_plan.add_argument(
        "--kind",
        required=True,
        choices=("factual", "conceptual", "procedural", "linguistic", "domain"),
    )
    knowledge_plan.add_argument("--subject", required=True)
    knowledge_plan.add_argument("--question", required=True)
    knowledge_plan.add_argument("--locale", default="es")
    knowledge_plan.add_argument(
        "--source",
        choices=("reviewed_text", "alexandria_reviewed"),
    )
    knowledge_plan.add_argument("--source-title")
    knowledge_plan.add_argument("--source-ref", default="")
    knowledge_plan.add_argument("--source-observed-at")
    knowledge_plan.add_argument("--revalidate-after")
    knowledge_plan.add_argument("--evidence-text")
    knowledge_plan.add_argument(
        "--evidence-package",
        help="Archivo JSON local con varias fuentes revisadas e hashes independientes.",
    )
    knowledge_plan.add_argument("--alexandria-query")
    knowledge_plan.add_argument("--domain", default="")
    knowledge_plan.add_argument("--project", default="")
    knowledge_plan.add_argument("--tutor", default="primary")
    knowledge_plan.add_argument(
        "--auditor",
        action="append",
        default=[],
        help="Auditor local consultivo; puede repetirse para revisión cruzada.",
    )
    knowledge_plan.add_argument("--approve", action="store_true", required=True)
    knowledge_run = model_sub.add_parser(
        "knowledge-acquisition-run",
        help="Ejecutar una síntesis aprobada una sola vez y en primer plano.",
    )
    knowledge_run.add_argument("plan_id")
    knowledge_run.add_argument("--approve", action="store_true", required=True)
    knowledge_retry = model_sub.add_parser(
        "knowledge-acquisition-retry",
        help="Crear un nuevo plan desde un fallo, sin reutilizar la aprobación.",
    )
    knowledge_retry.add_argument("plan_id")
    knowledge_retry.add_argument("--approve", action="store_true", required=True)
    knowledge_cancel = model_sub.add_parser(
        "knowledge-acquisition-cancel",
        help="Cancelar un plan pendiente sin invocar modelos.",
    )
    knowledge_cancel.add_argument("plan_id")
    knowledge_cancel.add_argument("--approve", action="store_true", required=True)
    knowledge_plans = model_sub.add_parser(
        "knowledge-proposals", help="Listar propuestas y planes de conocimiento."
    )
    knowledge_plans.add_argument(
        "--status",
        choices=("pending", "running", "reviewed", "failed", "cancelled", "promoted", "all"),
        default="all",
    )
    knowledge_plans.add_argument("--limit", type=int, default=100)
    knowledge_plan_show = model_sub.add_parser(
        "knowledge-proposal-show", help="Mostrar evidencia, auditoría y candidato."
    )
    knowledge_plan_show.add_argument("plan_id")
    knowledge_promote = model_sub.add_parser(
        "knowledge-promote",
        help="Promover una propuesta revisada a conocimiento general versionado.",
    )
    knowledge_promote.add_argument("plan_id")
    knowledge_promote.add_argument("--title")
    knowledge_promote.add_argument("--supersedes")
    knowledge_promote.add_argument("--replacement-reason", default="")
    knowledge_promote.add_argument("--parallel-reason", default="")
    knowledge_promote.add_argument("--approve", action="store_true", required=True)
    general_knowledge = model_sub.add_parser(
        "knowledge", help="Listar conocimiento general activo o histórico."
    )
    general_knowledge.add_argument(
        "--status", choices=("active", "superseded", "all"), default="active"
    )
    general_knowledge.add_argument("--kind")
    general_knowledge.add_argument("--limit", type=int, default=100)
    general_knowledge_show = model_sub.add_parser(
        "knowledge-show", help="Mostrar contenido, procedencia y linaje."
    )
    general_knowledge_show.add_argument("knowledge_id")
    general_knowledge_search = model_sub.add_parser(
        "knowledge-search", help="Buscar conocimiento general validado."
    )
    general_knowledge_search.add_argument("query")
    general_knowledge_search.add_argument("--limit", type=int, default=8)
    general_knowledge_search.add_argument("--domain", default="")
    general_knowledge_search.add_argument("--project", default="")
    general_knowledge_context = model_sub.add_parser(
        "knowledge-context", help="Previsualizar conocimiento aplicado a una consulta."
    )
    general_knowledge_context.add_argument("query")
    general_knowledge_context.add_argument("--domain", default="")
    general_knowledge_context.add_argument("--project", default="")
    knowledge_revalidation = model_sub.add_parser(
        "knowledge-revalidation-due",
        help="Listar conocimiento preservado que requiere revalidación.",
    )
    knowledge_revalidation.add_argument("--limit", type=int, default=100)
    knowledge_conflicts = model_sub.add_parser(
        "knowledge-conflicts",
        help="Listar conflictos potenciales sin borrar conocimiento.",
    )
    knowledge_conflicts.add_argument(
        "--status", choices=("open", "resolved", "all"), default="open"
    )
    knowledge_conflicts.add_argument("--limit", type=int, default=100)
    knowledge_conflict_show = model_sub.add_parser(
        "knowledge-conflict-show", help="Mostrar un conflicto potencial."
    )
    knowledge_conflict_show.add_argument("conflict_id")
    knowledge_conflict_resolve = model_sub.add_parser(
        "knowledge-conflict-resolve",
        help="Resolver un conflicto de forma explícita y no destructiva.",
    )
    knowledge_conflict_resolve.add_argument("conflict_id")
    knowledge_conflict_resolve.add_argument(
        "--resolution",
        choices=("compatible", "superseded_by_version"),
        required=True,
    )
    knowledge_conflict_resolve.add_argument("--note", required=True)
    knowledge_conflict_resolve.add_argument("--approve", action="store_true", required=True)

    first_aid_parser = sub.add_parser(
        "first-aid", help="Consultar tarjetas locales de primeros auxilios."
    )
    first_aid_sub = first_aid_parser.add_subparsers(dest="first_aid_command", required=True)
    first_aid_sub.add_parser("status", help="Mostrar versión, fuentes y alcance.")
    first_aid_sub.add_parser("topics", help="Listar tarjetas disponibles.")
    first_aid_lookup = first_aid_sub.add_parser(
        "lookup", help="Mostrar pasos inmediatos para una situación."
    )
    first_aid_lookup.add_argument("query")
    first_aid_lookup.add_argument("--language", default="es")
    first_aid_lookup.add_argument("--locale")

    ethics_parser = sub.add_parser(
        "ethics", help="Consultar la constitución ética local e inmutable."
    )
    ethics_sub = ethics_parser.add_subparsers(dest="ethics_command", required=True)
    ethics_sub.add_parser("status", help="Mostrar estado y límites éticos.")
    ethics_sub.add_parser("principles", help="Mostrar los principios constitucionales.")
    ethics_review = ethics_sub.add_parser(
        "review",
        help=(
            "Evaluar una solicitud sin ejecutarla. Los casos ambiguos pueden usar "
            "el tutor local como revisor secundario."
        ),
    )
    ethics_review.add_argument("text")
    ethics_history = ethics_sub.add_parser(
        "history", help="Listar decisiones éticas recientes sin guardar prompts crudos."
    )
    ethics_history.add_argument("--limit", type=int, default=20)

    audit_parser = sub.add_parser("audit", help="Consultar auditoría local.")
    audit_parser.set_defaults(
        audit_command="list", limit=20, action=None, target=None, outcome=None
    )
    audit_sub = audit_parser.add_subparsers(dest="audit_command")
    audit_list = audit_sub.add_parser("list", help="Listar eventos de auditoría.")
    audit_list.add_argument("--limit", type=int, default=20)
    audit_list.add_argument("--action")
    audit_list.add_argument("--target")
    audit_list.add_argument("--outcome")
    audit_show = audit_sub.add_parser("show", help="Mostrar un evento por id.")
    audit_show.add_argument("id", type=int)

    sub.add_parser("version", help="Mostrar versión.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return _init(args)
        if args.command == "version":
            return _print({"version": __version__}, args.json, human=f"Elyndra {__version__}")

        app = ElyndraApplication.load()
        if args.command == "account":
            return _account_command(app, args)
        if app.registry_accounts.has_account() and args.command not in {"web", "doctor"}:
            token = _read_cli_session(app.root_paths)
            if app.registry_accounts.account_for_session(token) is None:
                raise PermissionError(
                    "La cuenta local requiere login CLI. Ejecuta: elyndra account login USUARIO"
                )
        return _dispatch(app, args)
    except (
        ConfigError,
        IdentityError,
        LanguageConfigError,
        PersonaConfigError,
        PermissionError,
        ValueError,
        sqlite3.Error,
        GatewayError,
    ) as exc:
        if isinstance(exc, GatewayError) and exc.context:
            print(
                json.dumps({"error_code": exc.code, **exc.context}, sort_keys=True),
                file=sys.stderr,
            )
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2


def _init(args: argparse.Namespace) -> int:
    paths = ElyndraPaths.from_environment()
    paths.ensure()
    config_file = write_default_config(
        paths,
        owner_name=args.owner,
        system_user=args.system_user,
        force=args.force,
    )
    config = AppConfig.load(paths)
    persona_file = write_default_persona(paths, config, force=args.force)
    Database(paths.database_file, role="root").migrate()
    data = {
        "config": str(config_file),
        "persona": str(persona_file),
        "database": str(paths.database_file),
        "offline": True,
        "telemetry": False,
    }
    return _print(
        data,
        args.json,
        human=(
            "Elyndra inicializada.\n"
            f"Configuración: {config_file}\n"
            f"Persona: {persona_file}\n"
            f"Base local: {paths.database_file}\n"
            "Red: deshabilitada. Telemetría: deshabilitada."
        ),
    )


def _online_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    gateway = app.online_gateway
    if gateway is None:
        raise PermissionError("Selecciona una cuenta local antes de usar Online.")
    command = args.online_command
    if command == "status":
        data = gateway.status()
    elif command == "mode":
        data = {"mode": gateway.mode()}
    elif command == "mode-set":
        data = gateway.set_mode(args.mode)
    elif command == "sources":
        data = gateway.sources.list()
    elif command == "source-show":
        data = gateway.sources.get(args.source_id)
    elif command == "operations":
        data = gateway.operations()
    elif command == "operation-show":
        data = gateway.operation(args.operation_id)
    elif command == "plan-download":
        data = gateway.preview_download(args.source_id)
    elif command == "approve-download":
        data = gateway.approve_download(args.source_id, plan_digest=args.plan_digest)
    elif command == "history-clear":
        data = {"cleared": gateway.clear_history()}
    elif command in {"execute-download", "resume-download"}:
        operation = gateway.operation(args.operation_id)
        approval = gateway.request_execution_approval(
            args.operation_id, resume=command == "resume-download"
        )
        capability = _issue_cli_execution_capability(
            operation_id=args.operation_id,
            plan_sha256=str(operation["plan_sha256"]),
            command=command,
        )
        data = gateway.execute_download(
            args.operation_id,
            approval=approval,
            resume=command == "resume-download",
            cli_capability=capability,
        )
    elif command == "discard-partial":
        data = {"discarded": gateway.discard_partial(args.operation_id)}
    elif command == "cancel-download":
        data = gateway.cancel_download(args.operation_id)
    elif command == "cache-show":
        data = gateway.cache_show(args.artifact_key) or {}
    elif command == "cache-verify":
        data = gateway.cache_verify(args.artifact_key)
    elif command == "quarantine":
        data = gateway.quarantine()
    elif command == "descriptor-show":
        data = gateway.official_descriptor(args.source_id)
    elif command == "bundle-inspect":
        data = gateway.inspect_bundle(args.source_id)
    elif command == "plan-asset-download":
        request = gateway.request_asset_download_approval(
            args.source_id, args.artifact_key
        )
        data = gateway.operation(request["operation_id"])
    elif command == "bundle-prepare":
        data = gateway.prepare_bundle_install(args.source_id)
    elif command == "bundle-install":
        approval = gateway.request_bundle_install_approval(args.operation_id)
        data = gateway.install_bundle(args.operation_id, approval=approval)
    elif command == "bundle-install-cancel":
        data = gateway.cancel_bundle_install(args.operation_id)
    elif command == "bundle-install-status":
        data = gateway.bundle_install_status(args.operation_id)
    else:
        raise ValueError("Operación online no reconocida.")
    return _print(data, args.json, human=json.dumps(data, ensure_ascii=False, indent=2))


def _dispatch(app: ElyndraApplication, args: argparse.Namespace) -> int:
    if args.command == "account":
        return _account_command(app, args)
    if args.command == "doctor":
        return _doctor(app, args.json)
    if args.command == "status":
        return _result(app.execute_skill("system.status"), args.json)
    if args.command == "web":
        return run_web_interface(
            app,
            port=args.port,
            open_browser=not args.no_open,
        )
    if args.command == "chat":
        return _chat_command(app, args)
    if args.command == "ask":
        return _result(app.ask(args.text, approved=args.approve), args.json)
    if args.command == "assistant":
        return _assistant_command(app, args)
    if args.command == "remember":
        return _result(
            app.execute_skill(
                "memory.remember",
                {"content": args.content, "kind": args.kind, "project": args.project},
            ),
            args.json,
        )
    if args.command == "online":
        return _online_command(app, args)
    if args.command == "recall":
        memories = app.memories.search(args.query, args.limit)
        return _print(memories, args.json, human=_format_memories(memories))
    if args.command == "memory":
        return _memory_command(app, args)
    if args.command == "preferences":
        return _preferences_command(app, args)
    if args.command == "project":
        return _project_command(app, args)
    if args.command == "file":
        params: dict[str, Any] = {
            "path": str(args.path),
            "start_line": args.start_line,
        }
        if args.end_line is not None:
            params["end_line"] = args.end_line
        return _result(app.execute_skill("file.read", params), args.json)
    if args.command == "knowledge":
        return _knowledge_command(app, args)
    if args.command == "alexandria":
        return _alexandria_command(app, args)
    if args.command == "php":
        return _php_command(app, args)
    if args.command == "webdev":
        return _webdev_command(app, args)
    if args.command == "pythondev":
        return _pythondev_command(app, args)
    if args.command == "javadev":
        return _javadev_command(app, args)
    if args.command == "kotlindev":
        return _kotlindev_command(app, args)
    if args.command == "dotnetdev":
        return _dotnetdev_command(app, args)
    if args.command == "swiftdev":
        return _swiftdev_command(app, args)
    if args.command == "dartdev":
        return _dartdev_command(app, args)
    if args.command == "sqldev":
        return _sqldev_command(app, args)
    if args.command == "nativedev":
        return _nativedev_command(app, args)
    if args.command == "rubydev":
        return _rubydev_command(app, args)
    if args.command == "godev":
        return _godev_command(app, args)
    if args.command == "rustdev":
        return _rustdev_command(app, args)
    if args.command == "validate":
        return _result(app.execute_skill("code.validate", {"path": str(args.path)}), args.json)
    if args.command == "skill":
        return _skill_command(app, args)
    if args.command == "language":
        return _language_command(app, args)
    if args.command == "translate":
        return _result(app.translate(args.text, args.target_language), args.json)
    if args.command == "dictionary":
        return _dictionary_command(app, args)
    if args.command == "persona":
        return _persona_command(app, args)
    if args.command == "model":
        return _model_command(app, args)
    if args.command == "first-aid":
        return _first_aid_command(app, args)
    if args.command == "ethics":
        return _ethics_command(app, args)
    if args.command == "audit":
        command = args.audit_command or "list"
        if command == "show":
            event = app.audit.get(args.id)
            if event is None:
                raise ValueError(f"Evento de auditoría no encontrado: {args.id}")
            return _print(event, args.json, human=_format_audit_event(event))
        events = app.audit.list_recent(
            args.limit,
            action=args.action,
            target=args.target,
            outcome=args.outcome,
        )
        human = "\n".join(
            f"#{e['id']} {e['created_at']} {e['action']} {e['outcome']} {e['target'] or ''}"
            for e in events
        )
        return _print(events, args.json, human=human or "No hay eventos.")
    raise ValueError(f"Comando no implementado: {args.command}")


def _account_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.account_command
    public_commands = {"register", "login", "status", "reset-password-local"}
    if command not in public_commands:
        token = _read_cli_session(app.root_paths)
        if app.registry_accounts.account_for_session(token) is None:
            raise PermissionError(
                "Esta operación requiere una sesión CLI. Ejecuta: elyndra account login USUARIO"
            )
    if command == "register":
        password = args.password or getpass.getpass("Contraseña: ")
        confirmation = args.confirm_password or getpass.getpass("Confirmar contraseña: ")
        account = app.registry_accounts.register(
            username=args.username,
            email=args.email,
            password=password,
            password_confirmation=confirmation,
            birth_date=args.birth_date,
            preferred_name=args.preferred_name,
            system_user=app.identity.system_user,
            developer_mode=args.developer_mode,
            telemetry_enabled=args.telemetry,
        )
        _, token = app.registry_accounts.authenticate(
            login=args.username, password=password, interface="cli"
        )
        _write_cli_session(app.root_paths, token)
        ElyndraApplication.load_for_account(str(account["public_id"]), app.root_paths)
        return _print(
            account,
            args.json,
            human=(
                f"Cuenta local registrada: {account['username']}.\n"
                "Sesión CLI iniciada. Telemetría: "
                + ("activada" if account["telemetry_enabled"] else "desactivada")
                + "."
            ),
        )
    if command == "login":
        password = args.password or getpass.getpass("Contraseña: ")
        account, token = app.registry_accounts.authenticate(
            login=args.login, password=password, interface="cli"
        )
        _write_cli_session(app.root_paths, token)
        ElyndraApplication.load_for_account(str(account["public_id"]), app.root_paths)
        return _print(account, args.json, human=f"Sesión CLI iniciada como {account['username']}.")
    if command == "logout":
        token = _read_cli_session(app.root_paths)
        revoked = app.registry_accounts.revoke_session(token) if token else False
        _clear_cli_session(app.root_paths)
        return _print(
            {"logged_out": True, "session_revoked": revoked},
            args.json,
            human="Sesión CLI cerrada.",
        )
    if command == "status":
        accounts = app.registry_accounts.list_accounts()
        token = _read_cli_session(app.root_paths)
        session = app.registry_accounts.account_for_session(token) if token else None
        data = {
            "registered": bool(accounts),
            "account_count": len(accounts),
            "accounts": accounts,
            "authenticated": session is not None,
        }
        if session is not None:
            data["account"] = session
        names = ", ".join(str(item["username"]) for item in accounts) or "sin registrar"
        return _print(
            data,
            args.json,
            human=(
                f"Cuentas locales: {names}\n"
                + "Sesión CLI: "
                + (f"activa como {session['username']}" if session else "no iniciada")
            ),
        )
    if command == "profile":
        account = app.accounts.get_account()
        if account is None:
            raise ValueError("No existe una cuenta local registrada.")
        return _print(account, args.json, human=_format_account_profile(account))
    if command == "profile-update":
        values = {
            "preferred_name": args.preferred_name,
            "pronouns": args.pronouns,
            "sex": args.sex,
            "gender_identity": args.gender_identity,
            "sexual_orientation": args.sexual_orientation,
            "timezone": args.timezone,
            "language": args.language,
            "developer_mode": args.developer_mode,
            "telemetry_enabled": args.telemetry,
            "birthday_greeting_enabled": args.birthday_greeting,
        }
        account = app.accounts.update_profile(approved=args.approve, **values)
        return _print(account, args.json, human=_format_account_profile(account))
    if command == "security":
        data = app.accounts.security_status()
        return _print(
            data,
            args.json,
            human=(
                f"Hash de contraseña: {data['password_hash']}\n"
                f"2FA: {data['two_factor_status']}\n"
                "Exportación cifrada local: sí\nRespaldo remoto: no implementado"
            ),
        )
    if command == "change-email":
        password = args.password or getpass.getpass("Contraseña actual: ")
        account = app.accounts.change_email(
            password=password, email=args.email, approved=args.approve
        )
        return _print(account, args.json, human=f"Correo actualizado: {account['email']}")
    if command == "change-password":
        current = args.current_password or getpass.getpass("Contraseña actual: ")
        new = args.new_password or getpass.getpass("Nueva contraseña: ")
        confirmation = args.confirm_password or getpass.getpass("Confirmar contraseña: ")
        app.accounts.change_password(
            current_password=current,
            new_password=new,
            confirmation=confirmation,
            approved=args.approve,
        )
        _clear_cli_session(app.root_paths)
        return _print(
            {"password_changed": True, "sessions_revoked": True},
            args.json,
            human="Contraseña actualizada. Todas las sesiones fueron cerradas.",
        )
    if command == "reset-password-local":
        new = args.new_password or getpass.getpass("Nueva contraseña local: ")
        confirmation = args.confirm_password or getpass.getpass("Confirmar contraseña local: ")
        app.registry_accounts.reset_password_local(
            system_user=app.identity.system_user,
            login=args.login,
            new_password=new,
            confirmation=confirmation,
            approved=args.approve,
        )
        _clear_cli_session(app.root_paths)
        return _print(
            {"password_reset": True, "sessions_revoked": True},
            args.json,
            human=(
                "Contraseña local restablecida. Todas las sesiones fueron cerradas. "
                "Inicia sesión nuevamente en /login."
            ),
        )
    if command == "export":
        password = args.password or getpass.getpass("Contraseña de cuenta: ")
        passphrase = args.export_passphrase or getpass.getpass("Frase de exportación cifrada: ")
        path = app.accounts.export_encrypted(
            output_path=args.path,
            account_password=password,
            export_passphrase=passphrase,
            approved=args.approve,
        )
        return _print(
            {"path": str(path), "encrypted": True, "remote_backup": False},
            args.json,
            human=f"Exportación cifrada creada: {path}",
        )
    if command == "telemetry-preview":
        data = app.accounts.telemetry_preview()
        return _print(data, args.json, human=json.dumps(data, ensure_ascii=False, indent=2))
    raise ValueError(f"Comando de cuenta no implementado: {command}")


def _cli_session_path(paths: ElyndraPaths) -> Path:
    return paths.state_dir / "cli-account-session"


def _read_cli_session(paths: ElyndraPaths) -> str:
    target = _cli_session_path(paths)
    if not target.exists():
        return ""
    return target.read_text(encoding="utf-8").strip()


def _write_cli_session(paths: ElyndraPaths, token: str) -> None:
    paths.ensure()
    target = _cli_session_path(paths)
    target.write_text(token, encoding="utf-8")
    target.chmod(0o600)


def _clear_cli_session(paths: ElyndraPaths) -> None:
    _cli_session_path(paths).unlink(missing_ok=True)


def _format_account_profile(account: dict[str, Any]) -> str:
    optional = [
        ("Nombre preferido", account.get("preferred_name")),
        ("Pronombres", account.get("pronouns")),
        ("Sexo", account.get("sex")),
        ("Identidad de género", account.get("gender_identity")),
        ("Orientación sexual", account.get("sexual_orientation")),
    ]
    lines = [
        f"Usuario: {account['username']}",
        f"Correo: {account['email']}",
        f"Fecha de nacimiento: {account['birth_date']}",
        f"Edad calculada: {account['age']}",
        f"Modo desarrollador: {'sí' if account['developer_mode'] else 'no'}",
        f"Telemetría opcional: {'sí' if account['telemetry_enabled'] else 'no'}",
    ]
    lines.extend(f"{label}: {value}" for label, value in optional if value)
    return "\n".join(lines)


def _dictionary_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.dictionary_command
    if command == "status":
        data = app.dictionary.status()
        packs = data["structured_packs"]
        human = (
            f"Diccionario local {data['version']} · {data['entry_count']} conceptos base\n"
            f"ID: {data['id']}\n"
            f"Licencia: {data['license']}\n"
            f"SHA-256: {data['sha256']}\n"
            "Idiomas base: " + ", ".join(data["languages"]) + "\n"
            f"Paquetes estructurados: {packs['language_pack_count']}; "
            f"entradas en disco: {packs['lexical_entry_count']}\n"
            "Alcance: lexicón bootstrap más paquetes Alejandría instalados explícitamente."
        )
        return _print(data, args.json, human=human)
    if command == "languages":
        data = app.dictionary.languages
        human = "\n".join(f"{code}: {name}" for code, name in data.items())
        return _print(data, args.json, human=human)
    if command == "senses":
        data = app.lexical_service.senses(args.term)
        return _print(data, args.json, human=json.dumps(data, ensure_ascii=False, indent=2))
    if command == "related":
        data = app.lexical_service.related(
            args.term, relation=args.relation, sense_id=args.sense
        )
        return _print(data, args.json, human=json.dumps(data, ensure_ascii=False, indent=2))
    if command == "overlay-propose":
        if app.language_overlays is None:
            raise PermissionError("Inicia sesión en una cuenta para crear un overlay.")
        payload = json.loads(args.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("payload-json debe ser un objeto.")
        item = app.language_overlays.propose(
            entry_type=args.entry_type,
            expression=args.expression,
            payload=payload,
            actor=app.identity.system_user,
        )
        app.audit.record(actor=app.identity.system_user, action="language_overlay.propose",
                         target=item["public_id"], outcome="pending")
        return _print(item, args.json, human=json.dumps(item, ensure_ascii=False, indent=2))
    if command == "overlay-review":
        if app.language_overlays is None:
            raise PermissionError("Inicia sesión en una cuenta para revisar un overlay.")
        item = app.language_overlays.review(
            args.id, decision=args.decision, actor=app.identity.system_user
        )
        app.audit.record(actor=app.identity.system_user, action="language_overlay.review",
                         target=args.id, outcome=item["status"])
        return _print(item, args.json, human=json.dumps(item, ensure_ascii=False, indent=2))
    message, data = app.dictionary.render_lookup(
        args.term,
        language=args.language,
        output_language=args.output_language,
        dialect=args.dialect,
        limit=args.limit,
    )
    app.audit.record(
        actor=app.identity.system_user,
        action="dictionary.lookup",
        target=str(args.term)[:120],
        outcome="found" if data["found"] else "not_found",
        details={
            "source_language": args.language,
            "output_language": args.output_language,
            "dialect": args.dialect,
            "model_used": False,
        },
    )
    return _print(data, args.json, human=message)


def _first_aid_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.first_aid_command
    if command == "status":
        data = app.first_aid.status()
        packs = data["structured_packs"]
        human = (
            f"Primeros auxilios locales {data['version']} · {data['topic_count']} tarjetas base\n"
            f"ID: {data['package_id']}\n"
            f"Revisión: {data['reviewed_on']}\n"
            f"Paquetes revisados instalados: {packs['first_aid_pack_count']}; "
            f"tarjetas en disco: {packs['first_aid_card_count']}\n"
            "Alcance: guía inicial offline más paquetes revisados; no sustituye capacitación."
        )
        return _print(data, args.json, human=human)
    if command == "topics":
        topics = []
        for topic_id in app.first_aid.topic_ids:
            topic = app.first_aid.topic(topic_id, language="es")
            if topic is not None:
                topics.append(topic.to_dict())
        human = "\n".join(f"- {item['topic_id']}: {item['title']}" for item in topics)
        return _print(topics, args.json, human=human or "No hay tarjetas disponibles.")
    topic = app.first_aid.lookup(
        args.query,
        language=args.language,
        locale=args.locale,
    )
    if topic is None:
        return _print(
            {"found": False, "query": args.query},
            args.json,
            human="No se encontró una tarjeta local para esa situación.",
        )
    message, data = app.first_aid.render_topic(topic, language=args.language)
    return _print(data, args.json, human=message)


def _ethics_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.ethics_command
    if command == "status":
        data = ethics_status(
            proactive_advice=app.config.ethical_advice_enabled,
            tutor_review=app.config.ethical_tutor_review_enabled,
        )
        human = (
            "Constitución ética de Elyndra activa\n"
            "- Núcleo de no-daño: inmutable y no desactivable\n"
            f"- Recomendaciones proactivas: "
            f"{'sí' if app.config.ethical_advice_enabled else 'no'}\n"
            "- El propietario no puede autorizar daño a terceros\n"
            "- Negativas neutrales con alternativas seguras\n"
            f"- Revisión secundaria con tutor local: "
            f"{'sí' if app.config.ethical_tutor_review_enabled else 'no'}\n"
            "- El tutor nunca puede debilitar un bloqueo determinista\n"
            "- Denuncias automáticas: no\n"
            "- Ataques de red y sabotaje: no\n"
            "- Ejecución autónoma: no"
        )
        return _print(data, args.json, human=human)
    if command == "principles":
        items = [item.to_dict() for item in principles()]
        human = "\n".join(
            f"{index}. {item['title']}: {item['description']}"
            for index, item in enumerate(items, start=1)
        )
        return _print(items, args.json, human=human)
    if command == "review":
        review, review_id = app.review_ethics_request(
            args.text,
            source="cli.ethics.review",
        )
        payload = review.to_dict() | {"ethics_review_id": review_id}
        human = f"Decisión: {review.decision}\nCategoría: {review.category}\nRazón: {review.reason}"
        if review.tutor_used:
            human += (
                f"\nTutor secundario: {review.tutor_engine} "
                f"({review.tutor_label or 'sin etiqueta'})"
            )
        if review.response:
            human += "\n\n" + review.response
        elif review.advisory:
            human += "\nAdvertencia preventiva: " + review.advisory
        return _print(payload, args.json, human=human)
    items = app.ethics_reviews.list_recent(limit=args.limit)
    human = "\n".join(
        f"{item['created_at']} · {item['decision']} · {item['category']} · {item['source']}"
        for item in items
    )
    return _print(items, args.json, human=human or "No hay revisiones éticas.")


def _memory_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.memory_command
    if command == "tiers":
        data = app.tiered_memory.status()
        human = (
            "Memoria por niveles\n"
            f"- Hot: {data['hot']['queries']}/{data['hot']['max_queries']} consultas en RAM\n"
            f"- Warm: {data['warm']['active_episodes']} episodios recientes en SQLite\n"
            f"- Cold: {data['cold']['approved_memories']} recuerdos aprobados y "
            f"{data['cold']['indexed_episodes']} episodios indexados\n"
            "- Base completa cargada en RAM: no\n"
            "- Preferencias no revisadas promovidas automáticamente: no"
        )
        return _print(data, args.json, human=human)
    if command == "tier-recall":
        result = app.tiered_memory.recall(
            args.query,
            project=args.project,
            chat=args.chat,
            limit=args.limit,
        )
        data = result.to_dict()
        lines = [
            f"Recuperación en {result.total_ms} ms · hot={result.hot_hit} · "
            f"warm={result.warm_items} · cold={result.cold_items}"
        ]
        lines.extend(
            f"- [{item['tier']}] {item['kind']}: {item['content']}" for item in result.items
        )
        return _print(data, args.json, human="\n".join(lines))
    if command == "consolidate":
        data = app.tiered_memory.consolidate(min_age_days=args.min_age_days)
        app.audit.record(
            actor=app.identity.system_user,
            action="memory.tiers.consolidate",
            target=data["public_id"],
            outcome="success",
            details=data,
        )
        human = (
            f"Consolidación {data['public_id']}: escaneados={data['scanned_items']}, "
            f"indexados={data['indexed_items']}, eliminados=0. Procedencia preservada."
        )
        return _print(data, args.json, human=human)
    if command == "recalls":
        items = app.tiered_memory.recent_recalls(limit=args.limit)
        human = "\n".join(
            f"{item['created_at']} · {item['total_ms']} ms · "
            f"hot={item['hot_hit']} warm={item['warm_items']} cold={item['cold_items']}"
            for item in items
        )
        return _print(items, args.json, human=human or "No hay recuperaciones registradas.")
    if command == "cold-forget":
        forgotten = app.tiered_memory.forget_cold(args.id)
        app.audit.record(
            actor=app.identity.system_user,
            action="memory.tiers.cold_forget",
            target=str(args.id),
            outcome="success" if forgotten else "not_found",
        )
        return _print(
            {"forgotten": forgotten, "id": args.id},
            args.json,
            human="Entrada cold eliminada." if forgotten else "Entrada cold no encontrada.",
        )
    if command == "list":
        memories = app.memories.list_active(args.limit)
        return _print(memories, args.json, human=_format_memories(memories))
    if command == "episodes":
        episodes = app.memory_lifecycle.list_episodes(
            chat=args.chat,
            kind=args.kind,
            limit=args.limit,
        )
        return _print(episodes, args.json, human=_format_episodes(episodes))
    if command == "episode-edit":
        episode = app.memory_lifecycle.edit_episode(
            args.id,
            content=args.content,
            kind=args.kind,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="memory.episode_edit",
            target=str(args.id),
            outcome="success",
        )
        return _print(episode, args.json, human=f"Episodio #{args.id} actualizado.")
    if command == "episode-forget":
        forgotten = app.memory_lifecycle.forget_episode(args.id)
        app.audit.record(
            actor=app.identity.system_user,
            action="memory.episode_forget",
            target=str(args.id),
            outcome="success" if forgotten else "not_found",
        )
        return _print(
            {"forgotten": forgotten, "id": args.id},
            args.json,
            human="Episodio eliminado." if forgotten else "Episodio no encontrado.",
        )
    if command == "corrections":
        corrections = app.memory_lifecycle.list_corrections(
            chat=args.chat,
            limit=args.limit,
        )
        return _print(
            corrections,
            args.json,
            human=_format_corrections(corrections),
        )
    if command == "proposals":
        proposals = app.memory_lifecycle.list_proposals(
            status=args.status,
            limit=args.limit,
        )
        return _print(proposals, args.json, human=_format_proposals(proposals))
    if command == "edit":
        memory = app.memories.update(
            args.id,
            content=args.content,
            kind=args.kind,
            project=args.project,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="memory.edit",
            target=str(args.id),
            outcome="success",
        )
        return _print(memory, args.json, human=f"Recuerdo #{args.id} actualizado.")
    if command == "proposal-edit":
        proposal = app.memory_lifecycle.edit_proposal(args.id, args.content)
        app.audit.record(
            actor=app.identity.system_user,
            action="memory.proposal_edit",
            target=str(args.id),
            outcome="success",
        )
        return _print(
            proposal,
            args.json,
            human=f"Propuesta #{args.id} corregida y todavía pendiente.",
        )
    if command == "approve":
        proposal = app.memory_lifecycle.approve_proposal(args.id)
        app.audit.record(
            actor=app.identity.system_user,
            action="memory.proposal_approve",
            target=str(args.id),
            outcome="success",
            details={"memory_id": proposal.get("memory_id")},
        )
        return _print(
            proposal,
            args.json,
            human=(f"Propuesta #{args.id} aprobada como memoria #{proposal['memory_id']}."),
        )
    if command == "reject":
        rejected = app.memory_lifecycle.reject_proposal(args.id)
        app.audit.record(
            actor=app.identity.system_user,
            action="memory.proposal_reject",
            target=str(args.id),
            outcome="success" if rejected else "not_found",
        )
        return _print(
            {"rejected": rejected, "id": args.id},
            args.json,
            human="Propuesta rechazada." if rejected else "Propuesta pendiente no encontrada.",
        )

    forgotten = app.memories.forget(args.id)
    app.audit.record(
        actor=app.identity.system_user,
        action="memory.forget",
        target=str(args.id),
        outcome="success" if forgotten else "not_found",
    )
    return _print(
        {"forgotten": forgotten, "id": args.id},
        args.json,
        human="Recuerdo eliminado lógicamente." if forgotten else "Recuerdo no encontrado.",
    )


def _preferences_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.preferences_command
    if command == "status":
        data = app.preferences.status()
        human = (
            "Aprendizaje de preferencias revisable\n"
            f"- Propuestas pendientes: {data['pending_proposals']}\n"
            f"- Preferencias activas: {data['active_preferences']}\n"
            f"- Preferencias expiradas: {data['expired_preferences']}\n"
            "- Aprendizaje silencioso: no\n"
            "- Aprobación explícita: sí"
        )
        return _print(data, args.json, human=human)
    if command == "propose":
        item = app.preferences.propose(
            args.content,
            category=args.category,
            scope=args.scope,
            project=args.project,
            expires_days=args.expires_days,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="preferences.propose",
            target=str(item["id"]),
            outcome="pending",
            details={
                "scope": item.get("preference_scope"),
                "category": item.get("preference_category"),
            },
        )
        return _print(
            item,
            args.json,
            human=f"Propuesta de preferencia #{item['id']} creada; todavía no fue aprendida.",
        )
    if command == "proposals":
        items = app.preferences.list_proposals(status=args.status, limit=args.limit)
        human = "\n".join(
            f"#{item['id']} · {item['status']} · {item.get('preference_scope', 'global')} · "
            f"{item['content']}"
            for item in items
        )
        return _print(items, args.json, human=human or "No hay propuestas de preferencias.")
    if command == "edit":
        item = app.preferences.edit_proposal(
            args.id,
            content=args.content,
            category=args.category,
            scope=args.scope,
            project=args.project,
            expires_days=args.expires_days,
            clear_expiration=args.clear_expiration,
        )
        return _print(
            item,
            args.json,
            human=f"Propuesta #{args.id} editada; continúa pendiente de aprobación.",
        )
    if command == "approve":
        item = app.preferences.approve(args.id)
        app.audit.record(
            actor=app.identity.system_user,
            action="preferences.approve",
            target=item["public_id"],
            outcome="success",
            details={"proposal_id": args.id, "memory_id": item["memory_id"]},
        )
        return _print(
            item,
            args.json,
            human=(
                f"Preferencia {item['public_id']} aprobada y almacenada en memoria durable. "
                "Puede olvidarse o expirar."
            ),
        )
    if command == "reject":
        rejected = app.preferences.reject(args.id)
        return _print(
            {"id": args.id, "rejected": rejected},
            args.json,
            human="Propuesta rechazada." if rejected else "Propuesta pendiente no encontrada.",
        )
    if command == "list":
        items = app.preferences.list_preferences(
            status=args.status, project=args.project, limit=args.limit
        )
        human = "\n".join(
            f"{item['public_id']} · {item['status']} · {item['scope']} · "
            f"{item['category']} · {item['content']}"
            for item in items
        )
        return _print(items, args.json, human=human or "No hay preferencias revisadas.")
    if command == "forget":
        forgotten = app.preferences.forget(args.public_id)
        return _print(
            {"public_id": args.public_id, "forgotten": forgotten},
            args.json,
            human="Preferencia olvidada." if forgotten else "Preferencia activa no encontrada.",
        )
    if command == "expire":
        count = app.preferences.expire_due()
        return _print(
            {"expired": count},
            args.json,
            human=f"Preferencias expiradas en esta ejecución: {count}.",
        )
    raise ValueError(f"Comando de preferencias no implementado: {command}")


def _project_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    if args.project_command == "trust":
        item = app.trusted_projects.trust(args.path, actor=app.identity.system_user)
        app.audit.record(
            actor=app.identity.system_user,
            action="project.trust",
            target=item["path"],
            outcome="success",
            details={"authorization_scope": "project_persistent"},
        )
        return _print(item, args.json, human=f"Proyecto confiable: {item['path']}")
    if args.project_command == "untrust":
        removed = app.trusted_projects.untrust(args.path)
        resolved = str(args.path.expanduser().resolve(strict=False))
        app.audit.record(
            actor=app.identity.system_user,
            action="project.untrust",
            target=resolved,
            outcome="success" if removed else "not_found",
        )
        return _print(
            {"path": resolved, "removed": removed},
            args.json,
            human="Confianza revocada." if removed else "La ruta no estaba registrada.",
        )
    if args.project_command == "trusted":
        items = app.trusted_projects.list_all()
        human = "\n".join(f"#{item['id']} {item['path']}" for item in items)
        return _print(items, args.json, human=human or "No hay proyectos confiables.")
    if args.project_command == "trust-inspect":
        item = app.trusted_projects.inspect(args.path)
        status = "confiable" if item["trusted"] else "no confiable"
        return _print(item, args.json, human=f"{item['path']}: {status}")
    if args.project_command == "profiles":
        items = app.php_profiles.list_all()
        human = "\n".join(_format_php_profile(item) for item in items)
        return _print(items, args.json, human=human or "No hay perfiles PHP guardados.")
    if args.project_command == "profile-show":
        root = args.path.expanduser().resolve(strict=False)
        item = app.php_profiles.get(root)
        if item is None:
            return _print(
                {"project_root": str(root), "profile": None},
                args.json,
                human=f"{root}: sin perfil PHP guardado.",
            )
        return _print(item, args.json, human=_format_php_profile(item))
    if args.project_command == "profile-set":
        root = _require_persistent_project(app, args.path)
        item = app.php_profiles.save(
            root,
            actor=app.identity.system_user,
            phpstan_config=args.phpstan_config,
            phpstan_level=args.phpstan_level,
            phpunit_config=args.phpunit_config,
            phpunit_testsuite=args.phpunit_testsuite,
            composer_strict=args.composer_strict,
            composer_enabled=args.composer_enabled,
            syntax_scan_enabled=args.syntax_scan_enabled,
            phpstan_enabled=args.phpstan_enabled,
            phpunit_enabled=args.phpunit_enabled,
            fail_fast=args.fail_fast,
            require_tools=args.require_tools,
            max_php_files=args.max_php_files,
            exclude_paths=args.exclude_paths,
            timeout_seconds=args.timeout,
            max_output_chars=args.output_limit,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="project.php_profile.save",
            target=str(root),
            outcome="success",
            details={"profile_id": item["id"]},
        )
        return _print(item, args.json, human=_format_php_profile(item))
    if args.project_command == "profile-delete":
        root = args.path.expanduser().resolve(strict=False)
        removed = app.php_profiles.delete(root)
        app.audit.record(
            actor=app.identity.system_user,
            action="project.php_profile.delete",
            target=str(root),
            outcome="success" if removed else "not_found",
        )
        return _print(
            {"project_root": str(root), "removed": removed},
            args.json,
            human="Perfil PHP eliminado." if removed else "No había un perfil PHP guardado.",
        )
    if args.project_command == "web-profiles":
        items = app.web_profiles.list_all()
        human = "\n".join(_format_web_profile(item) for item in items)
        return _print(items, args.json, human=human or "No hay perfiles web guardados.")
    if args.project_command == "web-profile-show":
        root = args.path.expanduser().resolve(strict=False)
        item = app.web_profiles.get(root)
        if item is None:
            return _print(
                {"project_root": str(root), "profile": None},
                args.json,
                human=f"{root}: sin perfil web guardado.",
            )
        return _print(item, args.json, human=_format_web_profile(item))
    if args.project_command == "web-profile-set":
        root = _require_persistent_project(app, args.path, label="web")
        item = app.web_profiles.save(
            root,
            actor=app.identity.system_user,
            html_enabled=args.html,
            css_enabled=args.css,
            javascript_enabled=args.javascript,
            typescript_enabled=args.typescript,
            eslint_enabled=args.eslint,
            stylelint_enabled=args.stylelint,
            framework_checks_enabled=args.framework_checks,
            framework_preset=args.framework_preset,
            eslint_config=args.eslint_config,
            stylelint_config=args.stylelint_config,
            fail_fast=args.fail_fast,
            require_tools=args.require_tools,
            max_files=args.max_files,
            exclude_paths=args.web_exclude_paths,
            timeout_seconds=args.timeout,
            max_output_chars=args.output_limit,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="project.web_profile.save",
            target=str(root),
            outcome="success",
            details={"profile_id": item["id"]},
        )
        return _print(item, args.json, human=_format_web_profile(item))
    if args.project_command == "web-profile-delete":
        root = args.path.expanduser().resolve(strict=False)
        removed = app.web_profiles.delete(root)
        app.audit.record(
            actor=app.identity.system_user,
            action="project.web_profile.delete",
            target=str(root),
            outcome="success" if removed else "not_found",
        )
        return _print(
            {"project_root": str(root), "removed": removed},
            args.json,
            human="Perfil web eliminado." if removed else "No había un perfil web guardado.",
        )
    if args.project_command == "python-profiles":
        items = app.python_profiles.list_all()
        human = "\n".join(_format_python_profile(item) for item in items)
        return _print(items, args.json, human=human or "No hay perfiles Python guardados.")
    if args.project_command == "python-profile-show":
        root = args.path.expanduser().resolve(strict=False)
        item = app.python_profiles.get(root)
        if item is None:
            return _print(
                {"project_root": str(root), "profile": None},
                args.json,
                human=f"{root}: sin perfil Python guardado.",
            )
        return _print(item, args.json, human=_format_python_profile(item))
    if args.project_command == "python-profile-set":
        root = _require_persistent_project(app, args.path, label="Python")
        item = app.python_profiles.save(
            root,
            actor=app.identity.system_user,
            pyproject_enabled=args.pyproject,
            compile_enabled=args.compile,
            ruff_enabled=args.ruff,
            mypy_enabled=args.mypy,
            pytest_enabled=args.pytest,
            ruff_config=args.ruff_config,
            mypy_config=args.mypy_config,
            pytest_path=args.pytest_path,
            fail_fast=args.fail_fast,
            require_tools=args.require_tools,
            max_python_files=args.max_python_files,
            exclude_paths=args.python_exclude_paths,
            timeout_seconds=args.timeout,
            max_output_chars=args.output_limit,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="project.python_profile.save",
            target=str(root),
            outcome="success",
            details={"profile_id": item["id"]},
        )
        return _print(item, args.json, human=_format_python_profile(item))
    if args.project_command == "python-profile-delete":
        root = args.path.expanduser().resolve(strict=False)
        removed = app.python_profiles.delete(root)
        app.audit.record(
            actor=app.identity.system_user,
            action="project.python_profile.delete",
            target=str(root),
            outcome="success" if removed else "not_found",
        )
        return _print(
            {"project_root": str(root), "removed": removed},
            args.json,
            human=(
                "Perfil Python eliminado." if removed else "No había un perfil Python guardado."
            ),
        )
    if args.project_command == "java-profiles":
        items = app.java_profiles.list_all()
        human = "\n".join(_format_java_profile(item) for item in items)
        return _print(items, args.json, human=human or "No hay perfiles Java guardados.")
    if args.project_command == "java-profile-show":
        root = args.path.expanduser().resolve(strict=False)
        item = app.java_profiles.get(root)
        if item is None:
            return _print(
                {"project_root": str(root), "profile": None},
                args.json,
                human=f"{root}: sin perfil Java guardado.",
            )
        return _print(item, args.json, human=_format_java_profile(item))
    if args.project_command == "java-profile-set":
        root = _require_persistent_project(app, args.path, label="Java")
        item = app.java_profiles.save(
            root,
            actor=app.identity.system_user,
            descriptor_enabled=args.descriptor,
            javac_enabled=args.javac,
            build_enabled=args.build,
            tests_enabled=args.tests,
            build_tool=args.build_tool,
            java_release=args.java_release,
            fail_fast=args.fail_fast,
            require_tools=args.require_tools,
            max_java_files=args.max_java_files,
            exclude_paths=args.java_exclude_paths,
            timeout_seconds=args.timeout,
            max_output_chars=args.output_limit,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="project.java_profile.save",
            target=str(root),
            outcome="success",
            details={"profile_id": item["id"]},
        )
        return _print(item, args.json, human=_format_java_profile(item))
    if args.project_command == "java-profile-delete":
        root = args.path.expanduser().resolve(strict=False)
        removed = app.java_profiles.delete(root)
        app.audit.record(
            actor=app.identity.system_user,
            action="project.java_profile.delete",
            target=str(root),
            outcome="success" if removed else "not_found",
        )
        return _print(
            {"project_root": str(root), "removed": removed},
            args.json,
            human=("Perfil Java eliminado." if removed else "No había un perfil Java guardado."),
        )
    if args.project_command == "kotlin-profiles":
        items = app.kotlin_profiles.list_all()
        human = "\n".join(_format_kotlin_profile(item) for item in items)
        return _print(items, args.json, human=human or "No hay perfiles Kotlin guardados.")
    if args.project_command == "kotlin-profile-show":
        root = args.path.expanduser().resolve(strict=False)
        item = app.kotlin_profiles.get(root)
        if item is None:
            return _print(
                {"project_root": str(root), "profile": None},
                args.json,
                human=f"{root}: sin perfil Kotlin guardado.",
            )
        return _print(item, args.json, human=_format_kotlin_profile(item))
    if args.project_command == "kotlin-profile-set":
        root = _require_persistent_project(app, args.path, label="Kotlin")
        item = app.kotlin_profiles.save(
            root,
            actor=app.identity.system_user,
            descriptor_enabled=args.descriptor,
            kotlinc_enabled=args.kotlinc,
            build_enabled=args.build,
            tests_enabled=args.tests,
            build_tool=args.build_tool,
            jvm_target=args.jvm_target,
            fail_fast=args.fail_fast,
            require_tools=args.require_tools,
            max_kotlin_files=args.max_kotlin_files,
            exclude_paths=args.kotlin_exclude_paths,
            timeout_seconds=args.timeout,
            max_output_chars=args.output_limit,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="project.kotlin_profile.save",
            target=str(root),
            outcome="success",
            details={"profile_id": item["id"]},
        )
        return _print(item, args.json, human=_format_kotlin_profile(item))
    if args.project_command == "kotlin-profile-delete":
        root = args.path.expanduser().resolve(strict=False)
        removed = app.kotlin_profiles.delete(root)
        app.audit.record(
            actor=app.identity.system_user,
            action="project.kotlin_profile.delete",
            target=str(root),
            outcome="success" if removed else "not_found",
        )
        return _print(
            {"project_root": str(root), "removed": removed},
            args.json,
            human=(
                "Perfil Kotlin eliminado." if removed else "No había un perfil Kotlin guardado."
            ),
        )
    if args.project_command == "dotnet-profiles":
        items = app.dotnet_profiles.list_all()
        human = "\n".join(_format_dotnet_profile(item) for item in items)
        return _print(items, args.json, human=human or "No hay perfiles .NET guardados.")
    if args.project_command == "dotnet-profile-show":
        root = args.path.expanduser().resolve(strict=False)
        item = app.dotnet_profiles.get(root)
        if item is None:
            return _print(
                {"project_root": str(root), "profile": None},
                args.json,
                human=f"{root}: sin perfil .NET guardado.",
            )
        return _print(item, args.json, human=_format_dotnet_profile(item))
    if args.project_command == "dotnet-profile-set":
        root = _require_persistent_project(app, args.path, label=".NET")
        item = app.dotnet_profiles.save(
            root,
            actor=app.identity.system_user,
            descriptor_enabled=args.descriptor,
            format_enabled=args.format,
            build_enabled=args.build,
            tests_enabled=args.tests,
            configuration=args.configuration,
            fail_fast=args.fail_fast,
            require_tools=args.require_tools,
            max_dotnet_files=args.max_dotnet_files,
            exclude_paths=args.dotnet_exclude_paths,
            timeout_seconds=args.timeout,
            max_output_chars=args.output_limit,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="project.dotnet_profile.save",
            target=str(root),
            outcome="success",
            details={"profile_id": item["id"]},
        )
        return _print(item, args.json, human=_format_dotnet_profile(item))
    if args.project_command == "dotnet-profile-delete":
        root = args.path.expanduser().resolve(strict=False)
        removed = app.dotnet_profiles.delete(root)
        app.audit.record(
            actor=app.identity.system_user,
            action="project.dotnet_profile.delete",
            target=str(root),
            outcome="success" if removed else "not_found",
        )
        return _print(
            {"project_root": str(root), "removed": removed},
            args.json,
            human=("Perfil .NET eliminado." if removed else "No había un perfil .NET guardado."),
        )
    if args.project_command == "swift-profiles":
        items = app.swift_profiles.list_all()
        human = "\n".join(_format_swift_profile(item) for item in items)
        return _print(items, args.json, human=human or "No hay perfiles Swift guardados.")
    if args.project_command == "swift-profile-show":
        root = args.path.expanduser().resolve(strict=False)
        item = app.swift_profiles.get(root)
        if item is None:
            return _print(
                {"project_root": str(root), "profile": None},
                args.json,
                human=f"{root}: sin perfil Swift guardado.",
            )
        return _print(item, args.json, human=_format_swift_profile(item))
    if args.project_command == "swift-profile-set":
        root = _require_persistent_project(app, args.path, label="Swift")
        item = app.swift_profiles.save(
            root,
            actor=app.identity.system_user,
            manifest_enabled=args.manifest,
            syntax_enabled=args.syntax,
            format_enabled=args.format,
            build_enabled=args.build,
            tests_enabled=args.tests,
            configuration=args.configuration,
            fail_fast=args.fail_fast,
            require_tools=args.require_tools,
            max_swift_files=args.max_swift_files,
            exclude_paths=args.swift_exclude_paths,
            timeout_seconds=args.timeout,
            max_output_chars=args.output_limit,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="project.swift_profile.save",
            target=str(root),
            outcome="success",
            details={"profile_id": item["id"]},
        )
        return _print(item, args.json, human=_format_swift_profile(item))
    if args.project_command == "swift-profile-delete":
        root = args.path.expanduser().resolve(strict=False)
        removed = app.swift_profiles.delete(root)
        app.audit.record(
            actor=app.identity.system_user,
            action="project.swift_profile.delete",
            target=str(root),
            outcome="success" if removed else "not_found",
        )
        return _print(
            {"project_root": str(root), "removed": removed},
            args.json,
            human=("Perfil Swift eliminado." if removed else "No había un perfil Swift guardado."),
        )
    if args.project_command == "dart-profiles":
        items = app.dart_profiles.list_all()
        human = "\n".join(_format_dart_profile(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay perfiles Dart/Flutter guardados.",
        )
    if args.project_command == "dart-profile-show":
        root = args.path.expanduser().resolve(strict=False)
        item = app.dart_profiles.get(root)
        if item is None:
            return _print(
                {"project_root": str(root), "profile": None},
                args.json,
                human=f"{root}: sin perfil Dart/Flutter guardado.",
            )
        return _print(item, args.json, human=_format_dart_profile(item))
    if args.project_command == "dart-profile-set":
        root = _require_persistent_project(app, args.path, label="Dart/Flutter")
        item = app.dart_profiles.save(
            root,
            actor=app.identity.system_user,
            descriptor_enabled=args.descriptor,
            format_enabled=args.format,
            analyze_enabled=args.analyze,
            tests_enabled=args.tests,
            test_runner=args.test_runner,
            fail_fast=args.fail_fast,
            require_tools=args.require_tools,
            max_dart_files=args.max_dart_files,
            exclude_paths=args.dart_exclude_paths,
            timeout_seconds=args.timeout,
            max_output_chars=args.output_limit,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="project.dart_profile.save",
            target=str(root),
            outcome="success",
            details={"profile_id": item["id"]},
        )
        return _print(item, args.json, human=_format_dart_profile(item))
    if args.project_command == "dart-profile-delete":
        root = args.path.expanduser().resolve(strict=False)
        removed = app.dart_profiles.delete(root)
        app.audit.record(
            actor=app.identity.system_user,
            action="project.dart_profile.delete",
            target=str(root),
            outcome="success" if removed else "not_found",
        )
        return _print(
            {"project_root": str(root), "removed": removed},
            args.json,
            human=(
                "Perfil Dart/Flutter eliminado."
                if removed
                else "No había un perfil Dart/Flutter guardado."
            ),
        )
    if args.project_command == "sql-profiles":
        items = app.sql_profiles.list_all()
        human = "\n".join(_format_sql_profile(item) for item in items)
        return _print(items, args.json, human=human or "No hay perfiles SQL guardados.")
    if args.project_command == "sql-profile-show":
        root = args.path.expanduser().resolve(strict=False)
        item = app.sql_profiles.get(root)
        if item is None:
            return _print(
                {"project_root": str(root), "profile": None},
                args.json,
                human=f"{root}: sin perfil SQL guardado.",
            )
        return _print(item, args.json, human=_format_sql_profile(item))
    if args.project_command == "sql-profile-set":
        root = _require_persistent_project(app, args.path, label="SQL")
        item = app.sql_profiles.save(
            root,
            actor=app.identity.system_user,
            static_enabled=args.static,
            migrations_enabled=args.migrations,
            schema_enabled=args.schema,
            dialect=args.dialect,
            allow_mutating_sql=args.allow_mutating_sql,
            allow_destructive_migrations=args.allow_destructive_migrations,
            fail_fast=args.fail_fast,
            max_sql_files=args.max_sql_files,
            max_database_files=args.max_database_files,
            exclude_paths=args.sql_exclude_paths,
            timeout_seconds=args.timeout,
            max_output_chars=args.output_limit,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="project.sql_profile.save",
            target=str(root),
            outcome="success",
            details={"profile_id": item["id"]},
        )
        return _print(item, args.json, human=_format_sql_profile(item))
    if args.project_command == "sql-profile-delete":
        root = args.path.expanduser().resolve(strict=False)
        removed = app.sql_profiles.delete(root)
        app.audit.record(
            actor=app.identity.system_user,
            action="project.sql_profile.delete",
            target=str(root),
            outcome="success" if removed else "not_found",
        )
        return _print(
            {"project_root": str(root), "removed": removed},
            args.json,
            human=("Perfil SQL eliminado." if removed else "No había un perfil SQL guardado."),
        )
    if args.project_command == "native-profiles":
        items = app.native_profiles.list_all()
        human = "\n".join(_format_native_profile(item) for item in items)
        return _print(items, args.json, human=human or "No hay perfiles C/C++ guardados.")
    if args.project_command == "native-profile-show":
        root = args.path.expanduser().resolve(strict=False)
        item = app.native_profiles.get(root)
        if item is None:
            return _print(
                {"project_root": str(root), "profile": None},
                args.json,
                human=f"{root}: sin perfil C/C++ guardado.",
            )
        return _print(item, args.json, human=_format_native_profile(item))
    if args.project_command == "native-profile-set":
        root = _require_persistent_project(app, args.path, label="C/C++")
        item = app.native_profiles.save(
            root,
            actor=app.identity.system_user,
            descriptor_enabled=args.descriptor,
            c_syntax_enabled=args.c_syntax,
            cpp_syntax_enabled=args.cpp_syntax,
            static_enabled=args.static,
            build_enabled=args.build,
            tests_enabled=args.tests,
            compiler=args.compiler,
            c_standard=args.c_standard,
            cpp_standard=args.cpp_standard,
            fail_fast=args.fail_fast,
            require_tools=args.require_tools,
            max_native_files=args.max_native_files,
            exclude_paths=args.native_exclude_paths,
            timeout_seconds=args.timeout,
            max_output_chars=args.output_limit,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="project.native_profile.save",
            target=str(root),
            outcome="success",
            details={"profile_id": item["id"]},
        )
        return _print(item, args.json, human=_format_native_profile(item))
    if args.project_command == "native-profile-delete":
        root = args.path.expanduser().resolve(strict=False)
        removed = app.native_profiles.delete(root)
        app.audit.record(
            actor=app.identity.system_user,
            action="project.native_profile.delete",
            target=str(root),
            outcome="success" if removed else "not_found",
        )
        return _print(
            {"project_root": str(root), "removed": removed},
            args.json,
            human=("Perfil C/C++ eliminado." if removed else "No había un perfil C/C++ guardado."),
        )

    if args.project_command == "ruby-profiles":
        items = app.ruby_profiles.list_all()
        human = "\n".join(_format_ruby_profile(item) for item in items)
        return _print(items, args.json, human=human or "No hay perfiles Ruby guardados.")
    if args.project_command == "ruby-profile-show":
        root = args.path.expanduser().resolve(strict=False)
        item = app.ruby_profiles.get(root)
        if item is None:
            return _print(
                {"project_root": str(root), "profile": None},
                args.json,
                human=f"{root}: sin perfil Ruby guardado.",
            )
        return _print(item, args.json, human=_format_ruby_profile(item))
    if args.project_command == "ruby-profile-set":
        root = _require_persistent_project(app, args.path, label="Ruby")
        item = app.ruby_profiles.save(
            root,
            actor=app.identity.system_user,
            descriptor_enabled=args.descriptor,
            bundle_enabled=args.bundle,
            syntax_enabled=args.syntax,
            rubocop_enabled=args.rubocop,
            tests_enabled=args.tests,
            test_framework=args.test_framework,
            fail_fast=args.fail_fast,
            require_tools=args.require_tools,
            max_ruby_files=args.max_ruby_files,
            exclude_paths=args.ruby_exclude_paths,
            timeout_seconds=args.timeout,
            max_output_chars=args.output_limit,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="project.ruby_profile.save",
            target=str(root),
            outcome="success",
            details={"profile_id": item["id"]},
        )
        return _print(item, args.json, human=_format_ruby_profile(item))
    if args.project_command == "ruby-profile-delete":
        root = args.path.expanduser().resolve(strict=False)
        removed = app.ruby_profiles.delete(root)
        app.audit.record(
            actor=app.identity.system_user,
            action="project.ruby_profile.delete",
            target=str(root),
            outcome="success" if removed else "not_found",
        )
        return _print(
            {"project_root": str(root), "removed": removed},
            args.json,
            human=("Perfil Ruby eliminado." if removed else "No había un perfil Ruby guardado."),
        )
    if args.project_command == "go-profiles":
        items = app.go_profiles.list_all()
        human = "\n".join(_format_go_profile(item) for item in items)
        return _print(items, args.json, human=human or "No hay perfiles Go guardados.")
    if args.project_command == "go-profile-show":
        root = args.path.expanduser().resolve(strict=False)
        item = app.go_profiles.get(root)
        if item is None:
            return _print(
                {"project_root": str(root), "profile": None},
                args.json,
                human=f"{root}: sin perfil Go guardado.",
            )
        return _print(item, args.json, human=_format_go_profile(item))
    if args.project_command == "go-profile-set":
        root = _require_persistent_project(app, args.path, label="Go")
        item = app.go_profiles.save(
            root,
            actor=app.identity.system_user,
            module_enabled=args.module,
            fmt_enabled=args.fmt,
            vet_enabled=args.vet,
            build_enabled=args.build,
            tests_enabled=args.tests,
            test_mode=args.test_mode,
            fail_fast=args.fail_fast,
            require_tools=args.require_tools,
            max_go_files=args.max_go_files,
            exclude_paths=args.go_exclude_paths,
            timeout_seconds=args.timeout,
            max_output_chars=args.output_limit,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="project.go_profile.save",
            target=str(root),
            outcome="success",
            details={"profile_id": item["id"]},
        )
        return _print(item, args.json, human=_format_go_profile(item))
    if args.project_command == "go-profile-delete":
        root = args.path.expanduser().resolve(strict=False)
        removed = app.go_profiles.delete(root)
        app.audit.record(
            actor=app.identity.system_user,
            action="project.go_profile.delete",
            target=str(root),
            outcome="success" if removed else "not_found",
        )
        return _print(
            {"project_root": str(root), "removed": removed},
            args.json,
            human=("Perfil Go eliminado." if removed else "No había un perfil Go guardado."),
        )
    if args.project_command == "rust-profiles":
        items = app.rust_profiles.list_all()
        human = "\n".join(_format_rust_profile(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay perfiles Rust guardados.",
        )
    if args.project_command == "rust-profile-show":
        root = args.path.expanduser().resolve(strict=False)
        item = app.rust_profiles.get(root)
        if item is None:
            return _print(
                {"project_root": str(root), "profile": None},
                args.json,
                human=f"{root}: sin perfil Rust guardado.",
            )
        return _print(item, args.json, human=_format_rust_profile(item))
    if args.project_command == "rust-profile-set":
        root = _require_persistent_project(app, args.path, label="Rust")
        item = app.rust_profiles.save(
            root,
            actor=app.identity.system_user,
            manifest_enabled=args.manifest,
            fmt_enabled=args.fmt,
            check_enabled=args.check,
            clippy_enabled=args.clippy,
            tests_enabled=args.tests,
            feature_mode=args.feature_mode,
            fail_fast=args.fail_fast,
            require_tools=args.require_tools,
            max_rust_files=args.max_rust_files,
            exclude_paths=args.rust_exclude_paths,
            timeout_seconds=args.timeout,
            max_output_chars=args.output_limit,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="project.rust_profile.save",
            target=str(root),
            outcome="success",
            details={"profile_id": item["id"]},
        )
        return _print(item, args.json, human=_format_rust_profile(item))
    if args.project_command == "rust-profile-delete":
        root = args.path.expanduser().resolve(strict=False)
        removed = app.rust_profiles.delete(root)
        app.audit.record(
            actor=app.identity.system_user,
            action="project.rust_profile.delete",
            target=str(root),
            outcome="success" if removed else "not_found",
        )
        return _print(
            {"project_root": str(root), "removed": removed},
            args.json,
            human=("Perfil Rust eliminado." if removed else "No había un perfil Rust guardado."),
        )

    if args.project_command == "add":
        path = ensure_allowed(args.path, app.config.allowed_roots)
        if not path.is_dir():
            raise ValueError(f"La ruta no es una carpeta existente: {path}")
        project_id = app.projects.add(args.name, path)
        app.audit.record(
            actor=app.identity.system_user,
            action="project.add",
            target=args.name,
            outcome="success",
            details={"path": str(path)},
        )
        return _print(
            {"id": project_id, "name": args.name, "path": str(path)},
            args.json,
            human=f"Proyecto registrado: {args.name} → {path}",
        )
    if args.project_command == "list":
        projects = app.projects.list_all()
        human = "\n".join(f"#{p['id']} {p['name']} → {p['path']}" for p in projects)
        return _print(projects, args.json, human=human or "No hay proyectos registrados.")
    if args.project_command == "open":
        return _result(
            app.execute_skill("project.open", {"name": args.name}, approved=args.approve),
            args.json,
        )
    if args.project_command == "inspect":
        return _result(app.execute_skill("project.inspect", {"name": args.name}), args.json)
    return _result(
        app.execute_skill(
            "project.search_text",
            {"name": args.name, "query": args.query, "max_results": args.limit},
        ),
        args.json,
    )


def _require_persistent_project(
    app: ElyndraApplication,
    path: Path,
    *,
    label: str = "PHP",
) -> Path:
    root = path.expanduser().resolve(strict=True)
    decision = app.authorization.project(root)
    if not decision.allowed or decision.scope.value != "project_persistent":
        raise PermissionError(
            f"El perfil {label} solo puede guardarse para un proyecto dentro de una raíz "
            "persistente o registrado explícitamente como confiable."
        )
    return root


def _format_php_profile(item: dict[str, Any]) -> str:
    stages = (
        ", ".join(
            name
            for name, enabled in (
                ("composer", item.get("composer_enabled")),
                ("syntax", item.get("syntax_scan_enabled")),
                ("phpstan", item.get("phpstan_enabled")),
                ("phpunit", item.get("phpunit_enabled")),
            )
            if enabled
        )
        or "ninguna"
    )
    excludes = ", ".join(item.get("exclude_paths", [])) or "-"
    return (
        f"#{item['id']} {item['project_root']}\n"
        f"  Etapas: {stages}; fail-fast={'sí' if item.get('fail_fast') else 'no'}; "
        f"herramientas obligatorias={'sí' if item.get('require_tools') else 'no'}\n"
        f"  PHPStan: config={item.get('phpstan_config') or '-'}, "
        f"level={item.get('phpstan_level') or '-'}\n"
        f"  PHPUnit: config={item.get('phpunit_config') or '-'}, "
        f"testsuite={item.get('phpunit_testsuite') or '-'}\n"
        f"  Composer strict: {'sí' if item.get('composer_strict') else 'no'}; "
        f"máx. PHP={item.get('max_php_files') or 2000}; exclusiones={excludes}\n"
        f"  timeout={item.get('timeout_seconds') or 'global'}; "
        f"salida={item.get('max_output_chars') or 'global'}"
    )


def _format_web_profile(item: dict[str, Any]) -> str:
    stages = (
        ", ".join(
            name
            for name, enabled in (
                ("framework", item.get("framework_checks_enabled")),
                ("html", item.get("html_enabled")),
                ("css", item.get("css_enabled")),
                ("javascript", item.get("javascript_enabled")),
                ("typescript", item.get("typescript_enabled")),
                ("eslint", item.get("eslint_enabled")),
                ("stylelint", item.get("stylelint_enabled")),
            )
            if enabled
        )
        or "ninguna"
    )
    excludes = ", ".join(item.get("exclude_paths", [])) or "-"
    return (
        f"#{item['id']} {item['project_root']}\n"
        f"  Etapas: {stages}; preset={item.get('framework_preset') or 'auto'}; "
        f"fail-fast={'sí' if item.get('fail_fast') else 'no'}; "
        f"herramientas obligatorias={'sí' if item.get('require_tools') else 'no'}\n"
        f"  ESLint config={item.get('eslint_config') or '-'}; "
        f"Stylelint config={item.get('stylelint_config') or '-'}\n"
        f"  máx. archivos={item.get('max_files') or 3000}; exclusiones={excludes}\n"
        f"  timeout={item.get('timeout_seconds') or 'global'}; "
        f"salida={item.get('max_output_chars') or 'global'}"
    )


def _format_python_profile(item: dict[str, Any]) -> str:
    stages = (
        ", ".join(
            name
            for name, enabled in (
                ("pyproject", item.get("pyproject_enabled")),
                ("compile", item.get("compile_enabled")),
                ("ruff", item.get("ruff_enabled")),
                ("mypy", item.get("mypy_enabled")),
                ("pytest", item.get("pytest_enabled")),
            )
            if enabled
        )
        or "ninguna"
    )
    excludes = ", ".join(item.get("exclude_paths", [])) or "-"
    return (
        f"#{item['id']} {item['project_root']}\n"
        f"  Etapas: {stages}; fail-fast={'sí' if item.get('fail_fast') else 'no'}; "
        f"herramientas obligatorias={'sí' if item.get('require_tools') else 'no'}\n"
        f"  Ruff config={item.get('ruff_config') or '-'}; "
        f"mypy config={item.get('mypy_config') or '-'}; "
        f"Pytest path={item.get('pytest_path') or '-'}\n"
        f"  máx. Python={item.get('max_python_files') or 3000}; "
        f"exclusiones={excludes}\n"
        f"  timeout={item.get('timeout_seconds') or 'global'}; "
        f"salida={item.get('max_output_chars') or 'global'}"
    )


def _format_java_profile(item: dict[str, Any]) -> str:
    stages = (
        ", ".join(
            name
            for name, enabled in (
                ("descriptor", item.get("descriptor_enabled")),
                ("javac", item.get("javac_enabled")),
                ("build", item.get("build_enabled")),
                ("tests", item.get("tests_enabled")),
            )
            if enabled
        )
        or "ninguna"
    )
    excludes = ", ".join(item.get("exclude_paths", [])) or "-"
    return (
        f"#{item['id']} {item['project_root']}\n"
        f"  Etapas: {stages}; build={item.get('build_tool') or 'auto'}; "
        f"release={item.get('java_release') or '-'}; "
        f"fail-fast={'sí' if item.get('fail_fast') else 'no'}; "
        f"herramientas obligatorias={'sí' if item.get('require_tools') else 'no'}\n"
        f"  máx. Java={item.get('max_java_files') or 3000}; "
        f"exclusiones={excludes}\n"
        f"  timeout={item.get('timeout_seconds') or 'global'}; "
        f"salida={item.get('max_output_chars') or 'global'}"
    )


def _format_kotlin_profile(item: dict[str, Any]) -> str:
    stages = (
        ", ".join(
            name
            for name, enabled in (
                ("descriptor", item.get("descriptor_enabled")),
                ("kotlinc", item.get("kotlinc_enabled")),
                ("build", item.get("build_enabled")),
                ("tests", item.get("tests_enabled")),
            )
            if enabled
        )
        or "ninguna"
    )
    excludes = ", ".join(item.get("exclude_paths", [])) or "-"
    return (
        f"#{item['id']} {item['project_root']}\n"
        f"  Etapas: {stages}; build={item.get('build_tool') or 'auto'}; "
        f"release={item.get('jvm_target') or '-'}; "
        f"fail-fast={'sí' if item.get('fail_fast') else 'no'}; "
        f"herramientas obligatorias={'sí' if item.get('require_tools') else 'no'}\n"
        f"  máx. Kotlin={item.get('max_kotlin_files') or 3000}; "
        f"exclusiones={excludes}\n"
        f"  timeout={item.get('timeout_seconds') or 'global'}; "
        f"salida={item.get('max_output_chars') or 'global'}"
    )


def _format_dotnet_profile(item: dict[str, Any]) -> str:
    stages = (
        ", ".join(
            name
            for name, enabled in (
                ("descriptor", item.get("descriptor_enabled")),
                ("format", item.get("format_enabled")),
                ("build", item.get("build_enabled")),
                ("tests", item.get("tests_enabled")),
            )
            if enabled
        )
        or "ninguna"
    )
    excludes = ", ".join(item.get("exclude_paths", [])) or "-"
    return (
        f"#{item['id']} {item['project_root']}\n"
        f"  Etapas: {stages}; configuración={item.get('configuration') or 'Debug'}; "
        f"fail-fast={'sí' if item.get('fail_fast') else 'no'}; "
        f"herramientas obligatorias={'sí' if item.get('require_tools') else 'no'}\n"
        f"  máx. .NET={item.get('max_dotnet_files') or 3000}; exclusiones={excludes}\n"
        f"  timeout={item.get('timeout_seconds') or 'global'}; "
        f"salida={item.get('max_output_chars') or 'global'}"
    )


def _format_swift_profile(item: dict[str, Any]) -> str:
    stages = (
        ", ".join(
            name
            for name, enabled in (
                ("manifest", item.get("manifest_enabled")),
                ("syntax", item.get("syntax_enabled")),
                ("format", item.get("format_enabled")),
                ("build", item.get("build_enabled")),
                ("tests", item.get("tests_enabled")),
            )
            if enabled
        )
        or "ninguna"
    )
    excludes = ", ".join(item.get("exclude_paths", [])) or "-"
    return (
        f"#{item['id']} {item['project_root']}\n"
        f"  Etapas: {stages}; configuración={item.get('configuration') or 'debug'}; "
        f"fail-fast={'sí' if item.get('fail_fast') else 'no'}; "
        f"herramientas obligatorias={'sí' if item.get('require_tools') else 'no'}\n"
        f"  máx. Swift={item.get('max_swift_files') or 3000}; "
        f"exclusiones={excludes}\n"
        f"  timeout={item.get('timeout_seconds') or 'global'}; "
        f"salida={item.get('max_output_chars') or 'global'}"
    )


def _format_dart_profile(item: dict[str, Any]) -> str:
    stages = (
        ", ".join(
            name
            for name, enabled in (
                ("descriptor", item.get("descriptor_enabled")),
                ("format", item.get("format_enabled")),
                ("analyze", item.get("analyze_enabled")),
                ("tests", item.get("tests_enabled")),
            )
            if enabled
        )
        or "ninguna"
    )
    excludes = ", ".join(item.get("exclude_paths", [])) or "-"
    return (
        f"#{item['id']} {item['project_root']}\n"
        f"  Etapas: {stages}; runner={item.get('test_runner') or 'auto'}; "
        f"fail-fast={'sí' if item.get('fail_fast') else 'no'}; "
        f"herramientas obligatorias={'sí' if item.get('require_tools') else 'no'}\n"
        f"  máx. Dart={item.get('max_dart_files') or 3000}; "
        f"exclusiones={excludes}\n"
        f"  timeout={item.get('timeout_seconds') or 'global'}; "
        f"salida={item.get('max_output_chars') or 'global'}"
    )


def _format_sql_profile(item: dict[str, Any]) -> str:
    stages = (
        ", ".join(
            name
            for name, enabled in (
                ("static", item.get("static_enabled")),
                ("migrations", item.get("migrations_enabled")),
                ("schema", item.get("schema_enabled")),
            )
            if enabled
        )
        or "ninguna"
    )
    excludes = ", ".join(item.get("exclude_paths", [])) or "-"
    return (
        f"#{item['id']} {item['project_root']}\n"
        f"  Etapas: {stages}; dialecto={item.get('dialect') or 'auto'}; "
        f"fail-fast={'sí' if item.get('fail_fast') else 'no'}\n"
        f"  DDL/DML fuera de migraciones="
        f"{'sí' if item.get('allow_mutating_sql') else 'no'}; "
        f"destructivas={'sí' if item.get('allow_destructive_migrations') else 'no'}\n"
        f"  máx. SQL={item.get('max_sql_files') or 3000}; "
        f"máx. DB={item.get('max_database_files') or 20}; exclusiones={excludes}\n"
        f"  timeout={item.get('timeout_seconds') or 'global'}; "
        f"salida={item.get('max_output_chars') or 'global'}"
    )


def _format_native_profile(item: dict[str, Any]) -> str:
    stages = (
        ", ".join(
            name
            for name, enabled in (
                ("descriptor", item.get("descriptor_enabled")),
                ("c-syntax", item.get("c_syntax_enabled")),
                ("cpp-syntax", item.get("cpp_syntax_enabled")),
                ("static", item.get("static_enabled")),
                ("build", item.get("build_enabled")),
                ("tests", item.get("tests_enabled")),
            )
            if enabled
        )
        or "ninguna"
    )
    excludes = ", ".join(item.get("exclude_paths", [])) or "-"
    return (
        f"#{item['id']} {item['project_root']}\n"
        f"  Etapas: {stages}; compiler={item.get('compiler') or 'auto'}; "
        f"C={item.get('c_standard') or 'c17'}; "
        f"C++={item.get('cpp_standard') or 'c++20'}\n"
        f"  fail-fast={'sí' if item.get('fail_fast') else 'no'}; "
        f"herramientas obligatorias={'sí' if item.get('require_tools') else 'no'}\n"
        f"  máx. archivos={item.get('max_native_files') or 3000}; "
        f"exclusiones={excludes}\n"
        f"  timeout={item.get('timeout_seconds') or 'global'}; "
        f"salida={item.get('max_output_chars') or 'global'}"
    )


def _format_ruby_profile(item: dict[str, Any]) -> str:
    stages = (
        ", ".join(
            name
            for name, enabled in (
                ("descriptor", item.get("descriptor_enabled")),
                ("bundle", item.get("bundle_enabled")),
                ("syntax", item.get("syntax_enabled")),
                ("rubocop", item.get("rubocop_enabled")),
                ("tests", item.get("tests_enabled")),
            )
            if enabled
        )
        or "ninguna"
    )
    excludes = ", ".join(item.get("exclude_paths", [])) or "-"
    return (
        f"#{item['id']} {item['project_root']}\n"
        f"  Etapas: {stages}; tests={item.get('test_framework') or 'auto'}; "
        f"fail-fast={'sí' if item.get('fail_fast') else 'no'}; "
        f"herramientas obligatorias={'sí' if item.get('require_tools') else 'no'}\n"
        f"  máx. Ruby={item.get('max_ruby_files') or 3000}; "
        f"exclusiones={excludes}\n"
        f"  timeout={item.get('timeout_seconds') or 'global'}; "
        f"salida={item.get('max_output_chars') or 'global'}"
    )


def _format_go_profile(item: dict[str, Any]) -> str:
    stages = (
        ", ".join(
            name
            for name, enabled in (
                ("module", item.get("module_enabled")),
                ("fmt", item.get("fmt_enabled")),
                ("vet", item.get("vet_enabled")),
                ("build", item.get("build_enabled")),
                ("tests", item.get("tests_enabled")),
            )
            if enabled
        )
        or "ninguna"
    )
    excludes = ", ".join(item.get("exclude_paths", [])) or "-"
    return (
        f"#{item['id']} {item['project_root']}\n"
        f"  Etapas: {stages}; tests={item.get('test_mode') or 'auto'}; "
        f"fail-fast={'sí' if item.get('fail_fast') else 'no'}; "
        f"herramientas obligatorias={'sí' if item.get('require_tools') else 'no'}\n"
        f"  máx. Go={item.get('max_go_files') or 3000}; "
        f"exclusiones={excludes}\n"
        f"  timeout={item.get('timeout_seconds') or 'global'}; "
        f"salida={item.get('max_output_chars') or 'global'}"
    )


def _format_rust_profile(item: dict[str, Any]) -> str:
    stages = (
        ", ".join(
            name
            for name, enabled in (
                ("manifest", item.get("manifest_enabled")),
                ("fmt", item.get("fmt_enabled")),
                ("check", item.get("check_enabled")),
                ("clippy", item.get("clippy_enabled")),
                ("tests", item.get("tests_enabled")),
            )
            if enabled
        )
        or "ninguna"
    )
    excludes = ", ".join(item.get("exclude_paths", [])) or "-"
    return (
        f"#{item['id']} {item['project_root']}\n"
        f"  Etapas: {stages}; features={item.get('feature_mode') or 'default'}; "
        f"fail-fast={'sí' if item.get('fail_fast') else 'no'}; "
        f"herramientas obligatorias={'sí' if item.get('require_tools') else 'no'}\n"
        f"  máx. Rust={item.get('max_rust_files') or 3000}; "
        f"exclusiones={excludes}\n"
        f"  timeout={item.get('timeout_seconds') or 'global'}; "
        f"salida={item.get('max_output_chars') or 'global'}"
    )


def _knowledge_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    if args.knowledge_command == "import":
        return _result(
            app.execute_skill(
                "knowledge.import",
                {
                    "path": str(args.path),
                    "title": args.title,
                    "project": args.project,
                    "force": args.force,
                },
                approved=args.approve,
            ),
            args.json,
        )
    if args.knowledge_command == "search":
        return _result(
            app.execute_skill(
                "knowledge.search",
                {"query": args.query, "limit": args.limit},
            ),
            args.json,
        )
    if args.knowledge_command == "list":
        documents = app.knowledge.list_active(args.limit)
        return _print(documents, args.json, human=_format_documents(documents))

    forgotten = app.knowledge.forget(args.id)
    app.audit.record(
        actor=app.identity.system_user,
        action="knowledge.forget",
        target=str(args.id),
        outcome="success" if forgotten else "not_found",
    )
    return _print(
        {"forgotten": forgotten, "id": args.id},
        args.json,
        human="Documento eliminado lógicamente." if forgotten else "Documento no encontrado.",
    )


def _alexandria_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.alexandria_command
    if command.startswith("language-bundle-"):
        identity = app.accounts.identity()
        if identity is not None and not identity.developer_mode:
            raise PermissionError("La administración de bundles requiere modo desarrollador.")
        service = LanguageBundleService(app.language_packs)
        if command == "language-bundle-create":
            specs = []
            for path in args.pack:
                inspected = app.language_packs.inspect(path)
                logical = str(inspected["pack_id"])
                priority = DEFAULT_QUERY_PRIORITIES.get(logical, 100)
                specs.append({"path": path, "query_priority": priority, "required": True})
            options: dict[str, Any] = {
                "pack_specs": specs,
                "output_dir": args.output_dir,
                "build_epoch": args.build_epoch,
            }
            if args.part_bytes is not None:
                options["part_bytes"] = args.part_bytes
            item = service.create(**options)
        elif command in {"language-bundle-inspect", "language-bundle-verify"}:
            item = service.inspect(args.manifest)
        else:
            item = service.install(
                args.manifest,
                actor=app.identity.system_user,
                enable=bool(args.enable),
            )
        app.audit.record(
            actor=app.identity.system_user,
            action=command.replace("-", "."),
            target=str(item["bundle_id"]),
            outcome="success",
        )
        return _print(item, args.json, human=json.dumps(item, ensure_ascii=False, indent=2))
    if command.startswith("language-pack-"):
        identity = app.accounts.identity()
        if identity is not None and not identity.developer_mode:
            raise PermissionError("La administración de packs requiere modo desarrollador.")
        if command == "language-pack-inspect":
            item = app.language_packs.inspect(args.path)
            app.audit.record(
                actor=app.identity.system_user,
                action="language_pack.inspect",
                target=str(item["pack_id"]),
                outcome="success",
                details={"manifest_sha256": item["manifest_sha256"]},
            )
            return _print(item, args.json, human=json.dumps(item, ensure_ascii=False, indent=2))
        if command == "language-pack-build-es":
            payload = json.loads(args.source_metadata.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("source-metadata debe contener una lista JSON.")
            item = LanguagePackBuilder().build(
                logical_pack_id=args.pack_id,
                version=args.version,
                sources=payload,
                output_dir=args.output_dir,
                build_epoch=args.build_epoch,
                allow_large=bool(args.allow_large),
            )
            app.audit.record(
                actor=app.identity.system_user,
                action="language_pack.build",
                target=args.pack_id,
                outcome="success",
                details={"content_sha256": item["content_sha256"]},
            )
            return _print(item, args.json, human=json.dumps(item, ensure_ascii=False, indent=2))
        if command == "language-pack-install":
            item = app.language_packs.install(
                args.path, actor=app.identity.system_user, query_priority=args.query_priority
            )
            app.audit.record(
                actor=app.identity.system_user,
                action="language_pack.install",
                target=item["public_id"],
                outcome="success",
            )
            return _print(item, args.json, human=json.dumps(item, ensure_ascii=False, indent=2))
        if command == "language-pack-list":
            items = app.language_packs.list_all()
            return _print(items, args.json, human=json.dumps(items, ensure_ascii=False, indent=2))
        if command == "language-pack-show":
            item = app.language_packs.get(args.id)
            if item is None:
                raise ValueError("Pack lingüístico no encontrado.")
            return _print(item, args.json, human=json.dumps(item, ensure_ascii=False, indent=2))
        if command == "language-pack-verify":
            item = app.language_packs.verify(args.id)
            app.audit.record(
                actor=app.identity.system_user,
                action="language_pack.verify",
                target=args.id,
                outcome="success",
            )
            return _print(item, args.json, human=json.dumps(item, ensure_ascii=False, indent=2))
        if command in {"language-pack-enable", "language-pack-disable"}:
            enabled = command.endswith("enable")
            item = app.language_packs.set_enabled(args.id, enabled=enabled)
            app.audit.record(
                actor=app.identity.system_user,
                action=f"language_pack.{'enable' if enabled else 'disable'}",
                target=args.id,
                outcome="success",
            )
            return _print(item, args.json, human=json.dumps(item, ensure_ascii=False, indent=2))
    if command == "structured-inspect":
        item = app.structured_packs.inspect(args.path)
        return _print(
            item,
            args.json,
            human=_format_structured_pack(item, inspected=True),
        )
    if command == "structured-install":
        item = app.structured_packs.install(
            args.path,
            actor=app.identity.system_user,
            replace=bool(args.replace),
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="alexandria.structured.install",
            target=str(item["package_id"]),
            outcome="success",
            details={
                "version": item["version"],
                "adapter": item["adapter"],
                "replace": bool(args.replace),
                "install_status": item.get("install_status", "installed"),
            },
        )
        return _print(item, args.json, human=_format_structured_pack(item))
    if command == "structured-show":
        item = app.structured_packs.get(args.package_id)
        if item is None:
            raise ValueError(f"Paquete estructurado no encontrado: {args.package_id}")
        data = item | {"sources": app.structured_packs.sources(args.package_id)}
        return _print(
            data,
            args.json,
            human=_format_structured_pack(data, include_sources=True),
        )
    if command == "structured-list":
        items = app.structured_packs.list_all()
        human = "\n".join(_format_structured_pack(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay paquetes estructurados instalados.",
        )
    if command in {"structured-enable", "structured-disable"}:
        enabled = command == "structured-enable"
        item = app.structured_packs.set_enabled(args.package_id, enabled=enabled)
        app.audit.record(
            actor=app.identity.system_user,
            action=f"alexandria.structured.{'enable' if enabled else 'disable'}",
            target=str(item["package_id"]),
            outcome="success",
        )
        return _print(item, args.json, human=_format_structured_pack(item))
    if command == "structured-remove":
        item = app.structured_packs.remove(args.package_id)
        app.audit.record(
            actor=app.identity.system_user,
            action="alexandria.structured.remove",
            target=str(item["package_id"]),
            outcome="success",
        )
        return _print(
            item,
            args.json,
            human=f"Paquete estructurado eliminado: {item['package_id']} {item['version']}.",
        )
    if command == "package-inspect":
        item = app.alexandria_packages.inspect(args.path)
        return _print(item, args.json, human=_format_alexandria_package(item, inspected=True))
    if command == "package-install":
        item = app.alexandria_packages.install(
            args.path,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="alexandria.package.install",
            target=str(item["package_id"]),
            outcome="success",
            details={
                "version": item["version"],
                "tier": item["tier"],
                "source_count": item.get("source_count", 0),
            },
        )
        return _print(item, args.json, human=_format_alexandria_package(item))
    if command == "package-create":
        item = app.alexandria_packages.create(
            args.destination,
            package_id=args.package_id,
            name=args.name,
            version=args.version,
            tier=args.tier,
            domain=args.domain,
            language=args.language,
            license_id=args.license_id,
            source_paths=list(args.source),
            description=args.description,
            publisher=args.publisher,
            tags=list(args.tag),
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="alexandria.package.create",
            target=str(item["package_id"]),
            outcome="success",
            details={
                "version": item["version"],
                "destination": str(args.destination.expanduser().resolve(strict=False)),
                "source_count": item["source_count"],
            },
        )
        return _print(item, args.json, human=_format_alexandria_package(item, inspected=True))
    if command == "package-export":
        item = app.alexandria_packages.export(args.package_id, args.destination)
        app.audit.record(
            actor=app.identity.system_user,
            action="alexandria.package.export",
            target=str(item["package_id"]),
            outcome="success",
            details={
                "version": item["version"],
                "destination": str(args.destination.expanduser().resolve(strict=False)),
                "source_count": item["source_count"],
            },
        )
        return _print(item, args.json, human=_format_alexandria_package(item, inspected=True))
    if command == "package-list":
        items = app.alexandria_packages.list_all()
        human = "\n".join(_format_alexandria_package(item) for item in items)
        return _print(items, args.json, human=human or "No hay paquetes instalados.")
    if command in {"package-enable", "package-disable"}:
        enabled = command == "package-enable"
        item = app.alexandria_packages.set_enabled(args.package_id, enabled=enabled)
        app.audit.record(
            actor=app.identity.system_user,
            action=f"alexandria.package.{'enable' if enabled else 'disable'}",
            target=str(item["package_id"]),
            outcome="success",
        )
        return _print(item, args.json, human=_format_alexandria_package(item))
    if command == "package-remove":
        item = app.alexandria_packages.remove(args.package_id)
        app.audit.record(
            actor=app.identity.system_user,
            action="alexandria.package.remove",
            target=str(item["package_id"]),
            outcome="success",
        )
        return _print(
            item,
            args.json,
            human=f"Paquete eliminado: {item['package_id']} {item['version']}.",
        )
    if command == "create":
        library = app.alexandria.create_library(
            args.name,
            description=args.description,
            domain=args.domain,
            language=args.language,
            version=args.version,
            license_id=args.license_id,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="alexandria.library.create",
            target=str(library["public_id"]),
            outcome="success",
        )
        return _print(
            library,
            args.json,
            human=(
                f"Biblioteca creada: {library['name']} ({library['public_id']}).\n"
                "Las fuentes importadas comienzan como no revisadas."
            ),
        )
    if command == "list":
        libraries = app.alexandria.list_libraries(limit=args.limit)
        human = "\n".join(
            f"{item['public_id']} · {item['name']} · "
            f"{item['source_count']} fuentes · {item['unit_count']} unidades · "
            f"{'activa' if item['enabled'] else 'desactivada'}"
            for item in libraries
        )
        return _print(libraries, args.json, human=human or "Alejandría está vacía.")
    if command == "show":
        library = app.alexandria.get_library(args.library)
        if library is None:
            raise ValueError("Biblioteca no encontrada.")
        sources = app.alexandria.list_sources(args.library)
        return _print(
            {"library": library, "sources": sources},
            args.json,
            human=(
                f"{library['name']} · {library['domain']} · versión {library['version']}\n"
                f"Licencia: {library['license_id']}\n"
                f"Fuentes: {library['source_count']}; unidades: {library['unit_count']}; "
                f"revisadas: {library['reviewed_units']}"
            ),
        )
    if command == "import":
        path = ensure_allowed(args.path, app.config.allowed_roots)
        source = app.alexandria.import_file(
            args.library,
            path,
            title=args.title,
            source_url=args.source_url,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="alexandria.source.import",
            target=str(source["public_id"]),
            outcome="success",
            details={"library": args.library, "units": source["unit_count"]},
        )
        return _print(
            source,
            args.json,
            human=(
                f"Fuente {source['import_status']}: {source['title']} · "
                f"{source['unit_count']} unidades."
            ),
        )
    if command == "search":
        items = app.alexandria.search(
            args.query,
            library=args.library,
            limit=args.limit,
        )
        human = "\n\n".join(
            f"[{item['library_name']}] {item['source_title']} · {item['heading']}\n"
            f"{item['excerpt']}"
            for item in items
        )
        return _print(items, args.json, human=human or "Sin coincidencias en Alejandría.")
    if command == "review-source":
        source = app.alexandria.review_source(args.id, reviewed=True)
        app.audit.record(
            actor=app.identity.system_user,
            action="alexandria.source.review",
            target=str(args.id),
            outcome="success",
        )
        return _print(
            source,
            args.json,
            human=f"Fuente #{args.id} marcada como revisada.",
        )
    if command == "reindex":
        result = app.alexandria.reindex_all(force=True)
        app.alexandria.last_reindex_status = result
        app.audit.record(
            actor=app.identity.system_user,
            action="alexandria.reindex",
            target="all",
            outcome="success" if not result["errors"] else "partial",
            details=result,
        )
        return _print(
            result,
            args.json,
            human=(
                f"Índice Alejandría v{result['version']}: "
                f"{result['reindexed']} fuentes y {result['units']} unidades. "
                f"Errores: {len(result['errors'])}."
            ),
        )
    enabled = command == "enable"
    library = app.alexandria.update_library(args.library, enabled=enabled)
    app.audit.record(
        actor=app.identity.system_user,
        action=f"alexandria.library.{command}",
        target=str(library["public_id"]),
        outcome="success",
    )
    return _print(
        library,
        args.json,
        human=(f"Biblioteca {library['name']} {'activada' if enabled else 'desactivada'}."),
    )


def _format_alexandria_package(
    item: dict[str, Any],
    *,
    inspected: bool = False,
) -> str:
    status = (
        "inspeccionado" if inspected else ("activo" if item.get("enabled", True) else "inactivo")
    )
    source_count = item.get("source_count") or item.get("metadata", {}).get("source_count", 0)
    return (
        f"{item['package_id']} · {item['name']} · versión {item['version']}\n"
        f"  Dominio: {item['domain']}; nivel: {item['tier']}; estado: {status}\n"
        f"  Licencia: {item['license_id']}; fuentes: {source_count}; "
        "revisión automática: no"
    )


def _format_structured_pack(
    item: dict[str, Any],
    *,
    inspected: bool = False,
    include_sources: bool = False,
) -> str:
    status = (
        "inspeccionado" if inspected else ("activo" if item.get("enabled", True) else "inactivo")
    )
    count = item.get("entry_count", 0) or item.get("card_count", 0)
    lines = [
        f"{item['package_id']} · {item['name']} · versión {item['version']}",
        (f"  Tipo: {item['content_type']}; adapter: {item['adapter']}; estado: {status}"),
        (
            f"  Idioma: {item['language']}; locale/dialecto: "
            f"{item.get('locale') or item.get('dialect') or '-'}; registros: {count}"
        ),
        (
            "  Revisión: "
            f"{item.get('review_status', item.get('review', {}).get('status', '-'))}; "
            f"licencia: {item['license_id']}; instalación automática: no"
        ),
    ]
    if include_sources:
        for source in item.get("sources", []):
            lines.append(
                f"  - {source['title']} · {source['relative_path']} · "
                f"SHA-256 {source['sha256']} · {source['attribution']}"
            )
    return "\n".join(lines)


def _persona_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    if args.persona_command == "status":
        data = app.persona.to_dict() | {"path": str(app.paths.persona_config_file)}
        human = (
            f"Agente: {app.persona.agent_name} · proyecto {app.persona.project_name}\n"
            f"Propietario: {app.persona.owner_name}\n"
            f"Rol: {app.persona.role}\n"
            f"Misión: {app.persona.mission}\n"
            f"Fuente: {app.persona.source} · {app.paths.persona_config_file}"
        )
        return _print(data, args.json, human=human)

    if args.persona_command == "setup":
        persona = _persona_setup(app.persona)
        target = write_persona(app.paths, persona)
        app.audit.record(
            actor=app.identity.system_user,
            action="persona.setup",
            target=str(target),
            outcome="success",
        )
        return _print(
            persona.to_dict() | {"path": str(target)},
            args.json,
            human=f"Personalidad e identidad guardadas en {target}.",
        )

    target = write_default_persona(app.paths, app.config, force=args.force)
    app.audit.record(
        actor=app.identity.system_user,
        action="persona.write_default",
        target=str(target),
        outcome="success",
    )
    return _print(
        {"path": str(target), "force": args.force},
        args.json,
        human=f"Identidad canónica escrita en {target}.",
    )


def _language_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    config = LanguageConfig.load(app.paths)
    if args.language_command == "status":
        data = {
            "mode": config.interaction_mode,
            "preferred_language": config.preferred_language,
            "preferred_language_name": language_name(config.preferred_language),
            "supported_languages": LANGUAGE_NAMES,
        }
        human = (
            f"Modo: {config.interaction_mode}. "
            f"Idioma preferido: {language_name(config.preferred_language)} "
            f"({config.preferred_language})."
        )
        return _print(data, args.json, human=human)

    if args.language_command == "detect":
        detection = detect_language(args.text, fallback=config.preferred_language)
        data = {
            "code": detection.code,
            "name": detection.name,
            "confidence": detection.confidence,
            "method": detection.method,
        }
        human = (
            f"Idioma probable: {detection.name} ({detection.code}); "
            f"confianza {detection.confidence:.0%}; método {detection.method}."
        )
        return _print(data, args.json, human=human)

    language = resolve_language(args.language, allow_auto=True)
    target = update_interaction_language(app.paths, language)
    updated = LanguageConfig.load(app.paths)
    app.audit.record(
        actor=app.identity.system_user,
        action="language.change",
        target=language,
        outcome="success",
        details={
            "mode": updated.interaction_mode,
            "preferred_language": updated.preferred_language,
        },
    )
    data = {
        "config": str(target),
        "mode": updated.interaction_mode,
        "preferred_language": updated.preferred_language,
    }
    if language == "auto":
        human = (
            "Detección automática activada. "
            f"Idioma de respaldo: {language_name(updated.preferred_language)}."
        )
    else:
        human = f"Idioma de respuesta fijado en {language_name(language)}."
    return _print(data, args.json, human=human)


def _model_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    if args.model_command == "discover":
        roots = None
        if args.root:
            roots = tuple(path.expanduser().resolve() for path in args.root)
        report = discover_local_models(roots, max_files=max(1, args.max_files))
        return _print(report.to_dict(), args.json, human=_format_discovery(report.to_dict()))

    if args.model_command == "status":
        config, error = _language_config_snapshot(app.paths)
        data = _language_config_data(config, app.paths.language_config_file, error=error)
        return _print(data, args.json, human=_format_language_config(data))

    if args.model_command == "configure":
        model_path = args.model.expanduser().resolve()
        size_bytes = model_path.stat().st_size if model_path.is_file() else 0
        if size_bytes > 2 * 1024**3 and not args.allow_large_model:
            size_gb = size_bytes / 1024**3
            raise ValueError(
                f"El modelo pesa {size_gb:.2f} GiB. Usa --allow-large-model solo si quieres "
                "probarlo conscientemente."
            )
        target = write_language_config(
            app.paths,
            binary=args.binary,
            model=model_path,
            profile=args.profile,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="model.configure",
            target=str(model_path),
            outcome="success",
            details={"profile": args.profile, "binary": str(args.binary)},
        )
        data = {
            "config": str(target),
            "binary": str(args.binary.expanduser().resolve()),
            "model": str(model_path),
            "profile": args.profile,
            "restart_required": True,
        }
        human = (
            f"Motor configurado en {target}.\n"
            f"Modelo: {model_path}\n"
            f"Perfil: {args.profile}\n"
            "Ejecuta nuevamente Elyndra para cargar la nueva configuración."
        )
        return _print(data, args.json, human=human)

    if args.model_command == "configure-ollama":
        models = fetch_ollama_models(args.endpoint)
        selected = next(
            (item for item in models if item.get("name") == args.model),
            None,
        )
        if selected is None:
            available = ", ".join(str(item.get("name")) for item in models) or "ninguno"
            raise ValueError(
                f"El modelo {args.model!r} no está disponible en Ollama local. "
                f"Disponibles: {available}"
            )
        capabilities = selected.get("capabilities", [])
        if capabilities and isinstance(capabilities, list) and "completion" not in capabilities:
            raise ValueError(
                f"El modelo {args.model!r} no declara capacidad de conversación/completion."
            )
        target = write_ollama_language_config(
            app.paths,
            endpoint=args.endpoint,
            model_name=args.model,
            profile=args.profile,
            license_id=args.license_id,
            role=args.role,
            teacher_allowed=args.teacher_approved,
            redistribution_allowed=args.redistribution_approved,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="model.configure",
            target=args.model,
            outcome="success",
            details={
                "backend": "ollama-local",
                "profile": args.profile,
                "endpoint": args.endpoint,
                "license_id": args.license_id,
                "role": args.role,
                "teacher_allowed": args.teacher_approved,
            },
        )
        data = {
            "config": str(target),
            "backend": "ollama-local",
            "endpoint": args.endpoint,
            "model_name": args.model,
            "profile": args.profile,
            "license_id": args.license_id,
            "role": args.role,
            "teacher_allowed": args.teacher_approved,
            "restart_required": True,
        }
        human = (
            f"Ollama local configurado en {target}.\n"
            f"Modelo: {args.model}\n"
            f"Perfil: {args.profile}\n"
            "keep_alive=0: el modelo se descargará después de cada respuesta.\n"
            "Ejecuta nuevamente Elyndra para cargar la configuración."
        )
        return _print(data, args.json, human=human)

    if args.model_command == "ollama-list":
        version = fetch_ollama_version(args.endpoint)
        models = fetch_ollama_models(args.endpoint)
        running = fetch_ollama_running(args.endpoint)
        data = {
            "endpoint": args.endpoint,
            "version": version,
            "models": models,
            "running": running,
        }
        return _print(data, args.json, human=_format_ollama_models(data))

    if args.model_command == "tutor-status":
        data = app.tutor_status()
        lines = [
            "Arbitraje local de tutores",
            f"- Configuración: {data['config']}",
            f"- Modelos habilitados: {data['enabled_tutors']}",
            f"- Tutores docentes: {data['enabled_teachers']}",
            f"- Auditores consultivos: {data['enabled_auditors']}",
            f"- Modelos externos: {data['external_tutors']}",
            f"- Benchmarks guardados: {data['benchmark_runs']}",
            f"- Selecciones registradas: {data['selections']}",
            f"- Lecciones activas: {data['learning']['active_lessons']}",
            f"- Propuestas de lección: {data['learning']['pending_proposals']}",
            f"- Evaluaciones completadas: {data['evolution']['completed_evaluations']}",
            f"- Conocimiento durable activo: {data['evolution']['active_knowledge']}",
            "- Calibración: tarea/fuente/evaluación, conservadora",
            "- Descarga automática: no",
            "- Herramientas o permisos para tutores: no",
            "- Ejecución en segundo plano: no",
        ]
        for item in data["tutors"]:
            lines.append(
                f"- {item['tutor_id']}: {item['name']} · {item['backend']} · "
                f"perfil={item['profile']} · prioridad={item['priority']}"
            )
        return _print(data, args.json, human="\n".join(lines))

    if args.model_command == "tutor-template":
        data = {
            "config": str(app.paths.tutors_config_file),
            "template": app.tutor_arbitrator.registry.template(),
            "written": False,
        }
        return _print(
            data,
            args.json,
            human=(
                f"Ruta sugerida: {data['config']}\n"
                "No se escribió ningún archivo. Copia y revisa este "
                "ejemplo en VS Code:\n\n"
                f"{data['template']}"
            ),
        )

    if args.model_command == "tutor-recommend":
        data = app.recommend_tutor(args.task)
        human = (
            f"Tarea: {data['task']}\n"
            f"Tutor recomendado: {data['tutor_id']} ({data['engine_name']})\n"
            f"Razón: {data['reason']}\n"
            "No se invocó ningún modelo ni se ejecutó ninguna acción."
        )
        return _print(data, args.json, human=human)

    if args.model_command == "tutor-benchmark":
        result = app.run_tutor_benchmarks(
            approved=True,
            tutor_id=args.tutor,
        )
        return _result(result, args.json)

    if args.model_command == "tutor-benchmarks":
        items = app.tutor_benchmarks.list_runs(limit=args.limit)
        lines = [f"Benchmarks locales: {len(items)}"]
        for item in items:
            lines.append(
                f"- {item['public_id']} · {item['status']} · "
                f"tutores={item['tutor_count']} · casos={item['case_count']} · "
                f"{item['started_at']}"
            )
        return _print({"items": items}, args.json, human="\n".join(lines))

    if args.model_command == "tutor-benchmark-show":
        data = app.tutor_benchmarks.run_details(args.run_id)
        if data is None:
            raise ValueError(f"Benchmark no encontrado: {args.run_id}")
        lines = [
            f"Benchmark {data['public_id']} · {data['status']}",
            f"Suite: {data['suite_version']}",
        ]
        for item in data["results"]:
            lines.append(
                f"- {item['tutor_id']} · {item['case_id']} · "
                f"score={float(item['score']):.2f} · {item['latency_ms']} ms · "
                f"{'ok' if item['passed'] else 'fail'}"
            )
        return _print(data, args.json, human="\n".join(lines))

    if args.model_command == "tutor-selections":
        items = app.tutor_benchmarks.list_selections(limit=args.limit)
        lines = [f"Selecciones de tutor: {len(items)}"]
        for item in items:
            lines.append(
                f"- {item['public_id']} · {item['task_type']} · "
                f"{item['tutor_id']} · {item['result_status']} · "
                f"{item['latency_ms']} ms"
            )
        return _print({"items": items}, args.json, human="\n".join(lines))

    if args.model_command == "tutor-learning-status":
        data = {
            "lessons": app.tutor_learning.status(),
            "evolution": app.tutor_evolution.status(),
        }
        human = (
            "Aprendizaje supervisado de Elyndra\n"
            f"- Propuestas pendientes: {data['lessons']['pending_proposals']}\n"
            f"- Lecciones activas: {data['lessons']['active_lessons']}\n"
            f"- Comparaciones de evidencia: "
            f"{data['lessons']['evidence_comparisons']}\n"
            f"- Evaluaciones completadas: "
            f"{data['evolution']['completed_evaluations']}\n"
            f"- Conocimiento durable activo: "
            f"{data['evolution']['active_knowledge']}\n"
            "- Versiones anteriores preservadas; eliminación de conocimiento: no\n"
            "- Aprendizaje silencioso o actualización de modelos: no"
        )
        return _print(data, args.json, human=human)

    if args.model_command == "tutor-lesson-propose":
        if app.tutor_arbitrator.registry.get(args.tutor) is None:
            raise ValueError(f"Tutor no encontrado: {args.tutor}")
        item = app.tutor_learning.propose(
            tutor_id=args.tutor,
            task=validate_tutor_task(args.task),
            lesson=args.lesson,
            source_type=args.source,
            source_sha256=args.source_sha256,
            source_ref=args.source_ref,
            observed_score=args.observed_score,
            review_confidence=args.confidence,
            expires_days=args.expires_days,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="model.tutor_lesson.propose",
            target=item["public_id"],
            outcome="pending",
            details={"tutor_id": args.tutor, "task": args.task, "source": args.source},
        )
        return _print(
            item,
            args.json,
            human=(
                f"Propuesta {item['public_id']} creada; todavía no afecta al tutor "
                "ni a su confianza."
            ),
        )

    if args.model_command == "tutor-lesson-proposals":
        items = app.tutor_learning.list_proposals(status=args.status, limit=args.limit)
        lines = [f"Propuestas de lecciones: {len(items)}"]
        lines.extend(
            f"- {item['public_id']} · {item['status']} · {item['tutor_id']} · "
            f"{item['task_type']} · {item['source_type']}"
            for item in items
        )
        return _print({"items": items}, args.json, human="\n".join(lines))

    if args.model_command == "tutor-lesson-edit":
        item = app.tutor_learning.edit_proposal(
            args.public_id,
            lesson=args.lesson,
            observed_score=args.observed_score,
            review_confidence=args.confidence,
            expires_days=args.expires_days,
            clear_expiration=args.clear_expiration,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="model.tutor_lesson.edit",
            target=args.public_id,
            outcome="pending",
            details={"task": item["task_type"], "tutor_id": item["tutor_id"]},
        )
        return _print(
            item,
            args.json,
            human=f"Propuesta {args.public_id} editada; continúa pendiente.",
        )

    if args.model_command == "tutor-lesson-approve":
        item = app.tutor_learning.approve(args.public_id, actor=app.identity.system_user)
        app.audit.record(
            actor=app.identity.system_user,
            action="model.tutor_lesson.approve",
            target=item["public_id"],
            outcome="active",
            details={"proposal_id": args.public_id, "task": item["task_type"]},
        )
        return _print(
            item,
            args.json,
            human=(
                f"Lección {item['public_id']} aprobada. Puede aportar contexto acotado "
                f"y calibración solo para {item['tutor_id']}/{item['task_type']}."
            ),
        )

    if args.model_command == "tutor-lesson-reject":
        rejected = app.tutor_learning.reject(args.public_id, actor=app.identity.system_user)
        app.audit.record(
            actor=app.identity.system_user,
            action="model.tutor_lesson.reject",
            target=args.public_id,
            outcome="rejected" if rejected else "not_found",
        )
        return _print(
            {"public_id": args.public_id, "rejected": rejected},
            args.json,
            human=("Propuesta rechazada." if rejected else "Propuesta pendiente no encontrada."),
        )

    if args.model_command == "tutor-lessons":
        task = validate_tutor_task(args.task) if args.task else None
        items = app.tutor_learning.list_lessons(
            status=args.status, tutor_id=args.tutor, task=task, limit=args.limit
        )
        lines = [f"Lecciones revisadas: {len(items)}"]
        lines.extend(
            f"- {item['public_id']} · {item['status']} · {item['tutor_id']} · "
            f"{item['task_type']} · score={float(item['observed_score']):.2f}"
            for item in items
        )
        return _print({"items": items}, args.json, human="\n".join(lines))

    if args.model_command == "tutor-lesson-forget":
        forgotten = app.tutor_learning.forget(args.public_id, actor=app.identity.system_user)
        app.audit.record(
            actor=app.identity.system_user,
            action="model.tutor_lesson.forget",
            target=args.public_id,
            outcome="forgotten" if forgotten else "not_found",
        )
        return _print(
            {"public_id": args.public_id, "forgotten": forgotten},
            args.json,
            human=("Lección olvidada." if forgotten else "Lección activa no encontrada."),
        )

    if args.model_command == "tutor-lesson-expire":
        data = app.tutor_learning.expire_due()
        app.audit.record(
            actor=app.identity.system_user,
            action="model.tutor_lesson.expire",
            target="due",
            outcome="success",
            details=data,
        )
        return _print(
            data,
            args.json,
            human=(
                f"Expiraciones aplicadas: propuestas={data['proposals']}; "
                f"lecciones={data['lessons']}."
            ),
        )

    if args.model_command == "tutor-evidence-compare":
        if app.tutor_arbitrator.registry.get(args.tutor) is None:
            raise ValueError(f"Tutor no encontrado: {args.tutor}")
        data = app.tutor_learning.compare_evidence(
            tutor_id=args.tutor,
            task=validate_tutor_task(args.task),
            tutor_output_sha256=args.output_sha256,
            evidence_sha256=args.evidence_sha256,
            method=args.method,
            outcome=args.outcome,
            lesson=args.lesson,
            review_confidence=args.confidence,
            actor=app.identity.system_user,
            selection_id=args.selection_id,
            expires_days=args.expires_days,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="model.tutor_evidence.compare",
            target=data["comparison"]["public_id"],
            outcome=data["comparison"]["outcome"],
            details={
                "tutor_id": args.tutor,
                "task": args.task,
                "method": args.method,
                "proposal_id": data["proposal"]["public_id"],
            },
        )
        return _print(
            data,
            args.json,
            human=(
                f"Comparación {data['comparison']['public_id']} registrada y propuesta "
                f"{data['proposal']['public_id']} creada. La lección sigue pendiente."
            ),
        )

    if args.model_command == "tutor-evidence-comparisons":
        items = app.tutor_learning.list_comparisons(limit=args.limit)
        lines = [f"Comparaciones de evidencia: {len(items)}"]
        lines.extend(
            f"- {item['public_id']} · {item['tutor_id']} · {item['task_type']} · "
            f"{item['comparison_method']} · {item['outcome']}"
            for item in items
        )
        return _print({"items": items}, args.json, human="\n".join(lines))

    if args.model_command == "tutor-lesson-evaluation-plan":
        item = app.tutor_arbitrator.plan_lesson_evaluation(
            args.lesson_id,
            primary_engine=app.language_engine,
            actor=app.identity.system_user,
            auditor_id=args.auditor,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="model.tutor_lesson_evaluation.plan",
            target=item["public_id"],
            outcome="pending",
            details={
                "lesson_id": args.lesson_id,
                "tutor_id": item["tutor_id"],
                "task": item["task_type"],
                "auditor_id": item.get("auditor_id"),
                "cases": item["case_ids"],
                "knowledge_ids": item["knowledge_ids"],
            },
        )
        return _print(
            item,
            args.json,
            human=(
                f"Evaluación {item['public_id']} planificada para la lección "
                f"{args.lesson_id}. No se invocó ningún modelo."
            ),
        )

    if args.model_command == "tutor-lesson-evaluation-run":
        try:
            item = app.tutor_arbitrator.run_lesson_evaluation(
                args.evaluation_id,
                primary_engine=app.language_engine,
            )
        except (RuntimeError, ValueError) as exc:
            app.audit.record(
                actor=app.identity.system_user,
                action="model.tutor_lesson_evaluation.run",
                target=args.evaluation_id,
                outcome="failed",
                details={"error": str(exc)[:500]},
            )
            raise
        app.audit.record(
            actor=app.identity.system_user,
            action="model.tutor_lesson_evaluation.run",
            target=args.evaluation_id,
            outcome="completed",
            details={
                "recommendation": item["recommendation"],
                "baseline_score": item["baseline_score"],
                "candidate_score": item["candidate_score"],
                "auditor_status": item["auditor_status"],
                "raw_outputs_stored": False,
            },
        )
        return _print(
            item,
            args.json,
            human=(
                f"Evaluación {args.evaluation_id} completada. "
                f"Baseline={float(item['baseline_score']):.2f}; "
                f"candidate={float(item['candidate_score']):.2f}; "
                f"recomendación={item['recommendation']}. "
                "No se promovió conocimiento automáticamente."
            ),
        )

    if args.model_command == "tutor-lesson-evaluation-cancel":
        cancelled = app.tutor_evolution.cancel_evaluation(
            args.evaluation_id,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="model.tutor_lesson_evaluation.cancel",
            target=args.evaluation_id,
            outcome="cancelled" if cancelled else "not_found",
        )
        return _print(
            {"evaluation_id": args.evaluation_id, "cancelled": cancelled},
            args.json,
            human=(
                "Evaluación pendiente cancelada sin invocar modelos."
                if cancelled
                else "Evaluación pendiente no encontrada."
            ),
        )

    if args.model_command == "tutor-lesson-evaluations":
        items = app.tutor_evolution.list_evaluations(
            status=args.status,
            limit=args.limit,
        )
        lines = [f"Evaluaciones de lecciones: {len(items)}"]
        lines.extend(
            f"- {item['public_id']} · {item['status']} · {item['tutor_id']} · "
            f"{item['task_type']} · {item['recommendation'] or 'sin resultado'}"
            for item in items
        )
        return _print({"items": items}, args.json, human="\n".join(lines))

    if args.model_command == "tutor-lesson-evaluation-show":
        item = app.tutor_evolution.evaluation_details(args.evaluation_id)
        if item is None:
            raise ValueError(f"Evaluación no encontrada: {args.evaluation_id}")
        human = (
            f"Evaluación {item['public_id']} · {item['status']}\n"
            f"Tutor/tarea: {item['tutor_id']}/{item['task_type']}\n"
            f"Recomendación: {item['recommendation'] or 'pendiente'}\n"
            f"Casos: {len(item['results'])}; auditor={item['auditor_status']}\n"
            "No contiene prompts ni salidas crudas."
        )
        return _print(item, args.json, human=human)

    if args.model_command == "tutor-knowledge-promote":
        item = app.tutor_evolution.promote_knowledge(
            args.evaluation_id,
            title=args.title,
            actor=app.identity.system_user,
            supersedes_public_id=args.supersedes,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="model.tutor_knowledge.promote",
            target=item["public_id"],
            outcome="active",
            details={
                "evaluation_id": args.evaluation_id,
                "lineage_id": item["lineage_id"],
                "version": item["version"],
                "supersedes": args.supersedes,
                "automatic": False,
            },
        )
        return _print(
            item,
            args.json,
            human=(
                f"Conocimiento {item['public_id']} promovido como versión "
                f"{item['version']} del linaje {item['lineage_id']}. "
                "Las versiones anteriores permanecen trazables."
            ),
        )

    if args.model_command == "tutor-knowledge":
        task = validate_tutor_task(args.task) if args.task else None
        items = app.tutor_evolution.list_knowledge(
            status=args.status,
            task=task,
            limit=args.limit,
        )
        lines = [f"Conocimiento durable: {len(items)}"]
        lines.extend(
            f"- {item['public_id']} · {item['status']} · {item['task_type']} · "
            f"v{item['version']} · {item['title']}"
            for item in items
        )
        return _print({"items": items}, args.json, human="\n".join(lines))

    if args.model_command == "tutor-knowledge-show":
        item = app.tutor_evolution.knowledge_details(args.knowledge_id)
        if item is None:
            raise ValueError(f"Conocimiento no encontrado: {args.knowledge_id}")
        human = (
            f"Conocimiento {item['public_id']} · {item['status']}\n"
            f"Linaje: {item['lineage_id']} · versión {item['version']}\n"
            f"Tarea: {item['task_type']} · origen={item['origin_tutor_id']}\n"
            f"Validación: {item['validation_status']}\n"
            f"Contenido: {item['content']}"
        )
        return _print(item, args.json, human=human)

    if args.model_command == "tutor-knowledge-context":
        task = validate_tutor_task(args.task)
        item = app.tutor_evolution.knowledge_context(task)
        context_text = "\n\n".join(item["context"]) or "Sin conocimiento activo."
        human = (
            f"Contexto durable para {task}\n"
            f"Aplicados: {len(item['knowledge_ids'])}; "
            f"omitidos: {len(item['omitted_knowledge_ids'])}\n"
            f"{context_text}"
        )
        return _print(item, args.json, human=human)

    if args.model_command == "tutor-calibration-show":
        item = app.tutor_arbitrator.calibration(
            args.tutor,
            validate_tutor_task(args.task),
            primary_engine=app.language_engine,
        )
        human = (
            f"Calibración {item['tutor_id']}/{item['task']}\n"
            f"Confianza conservadora: {float(item['calibrated_confidence']):.4f}\n"
            f"Benchmark: {item['benchmark_score']}\n"
            f"Observaciones revisadas: {item['reviewed_observations']}\n"
            f"Evaluaciones obsoletas por cambio de modelo: "
            f"{item['evaluation_evidence']['stale_observations']}"
        )
        return _print(item, args.json, human=human)

    if args.model_command == "knowledge-learning-status":
        item = app.general_knowledge.status()
        human = (
            "Aprendizaje general supervisado de Elyndra\n"
            f"- Planes pendientes: {item['pending_plans']}\n"
            f"- Propuestas revisadas: {item['reviewed_plans']}\n"
            f"- Conocimiento activo: {item['active_knowledge']}\n"
            f"- Versiones sustituidas preservadas: {item['superseded_knowledge']}\n"
            f"- Planes fallidos reintentables: {item['failed_plans']}\n"
            f"- Conflictos abiertos: {item['open_conflicts']}\n"
            f"- Revalidaciones pendientes: {item['revalidation_due']}\n"
            "- Aprendizaje silencioso: no\n"
            "- Eliminación de conocimiento: no"
        )
        return _print(item, args.json, human=human)

    if args.model_command == "knowledge-teach":
        item = app.general_knowledge.create_owner_proposal(
            statement=args.statement,
            subject=args.subject,
            kind=args.kind,
            locale=args.locale,
            actor=app.identity.system_user,
            source_observed_at=args.source_observed_at,
            revalidate_after=args.revalidate_after,
            domain=args.domain,
            project=args.project,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="model.knowledge.owner_teaching.propose",
            target=item["public_id"],
            outcome="reviewed",
            details={
                "kind": item["knowledge_kind"],
                "evidence_sha256": item["evidence_sha256"],
                "automatic_promotion": False,
            },
        )
        return _print(
            item,
            args.json,
            human=(
                f"Propuesta {item['public_id']} creada desde enseñanza explícita. "
                "No es conocimiento durable hasta promoverla por separado."
            ),
        )

    if args.model_command == "knowledge-acquisition-plan":
        evidence_sources: tuple[dict[str, Any], ...] = ()
        domain = args.domain
        project = args.project
        if args.evidence_package:
            if args.evidence_text or args.alexandria_query:
                raise ValueError(
                    "--evidence-package no se combina con --evidence-text ni --alexandria-query."
                )
            package_input = Path(args.evidence_package).expanduser()
            if package_input.is_symlink():
                raise ValueError("El paquete de evidencia no puede ser un symlink.")
            package_path = package_input.resolve()
            if not package_path.is_file():
                raise ValueError("Paquete de evidencia local no encontrado o inseguro.")
            if package_path.stat().st_size > 256 * 1024:
                raise ValueError("El paquete de evidencia supera 256 KiB.")
            try:
                package = json.loads(package_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("Paquete de evidencia JSON inválido.") from exc
            if not isinstance(package, dict) or not isinstance(package.get("sources"), list):
                raise ValueError("El paquete requiere una lista sources.")
            evidence_sources = tuple(package["sources"])
            if not evidence_sources:
                raise ValueError("El paquete de evidencia no contiene fuentes.")
            for source in evidence_sources:
                if not isinstance(source, dict):
                    raise ValueError("Cada fuente del paquete debe ser un objeto.")
                source_kind = (
                    str(source.get("source_type", source.get("type", ""))).strip().casefold()
                )
                if source_kind not in {"reviewed_text", "alexandria_reviewed"}:
                    raise ValueError(
                        "Los paquetes aceptan solo reviewed_text o alexandria_reviewed."
                    )
            first_source = evidence_sources[0]
            if not isinstance(first_source, dict):
                raise ValueError("La primera fuente del paquete es inválida.")
            source_type = str(first_source.get("source_type", first_source.get("type", "")))
            source_title = str(first_source.get("source_title", first_source.get("title", "")))
            source_ref = str(first_source.get("source_ref", first_source.get("ref", "")))
            evidence_text = str(first_source.get("evidence_text", first_source.get("text", "")))
            source_unit_ids = tuple(
                int(item)
                for item in first_source.get("source_unit_ids", first_source.get("unit_ids", ()))
            )
            domain = domain or str(package.get("domain", ""))
            project = project or str(package.get("project", ""))
        elif args.source == "reviewed_text":
            if not args.source_title:
                raise ValueError("--source-title es obligatorio para reviewed_text.")
            if not args.evidence_text:
                raise ValueError("--evidence-text es obligatorio para reviewed_text.")
            evidence_text = args.evidence_text
            source_unit_ids: tuple[int, ...] = ()
            source_ref = args.source_ref
            source_type = args.source
            source_title = args.source_title
        elif args.source == "alexandria_reviewed":
            if not args.source_title:
                raise ValueError("--source-title es obligatorio para alexandria_reviewed.")
            if not args.alexandria_query:
                raise ValueError("--alexandria-query es obligatorio para alexandria_reviewed.")
            units = app.alexandria.search(
                args.alexandria_query,
                limit=6,
                reviewed_only=True,
                prefer_reviewed=True,
            )
            if not units:
                raise ValueError("Alejandría no devolvió unidades revisadas para congelar.")
            source_unit_ids = tuple(int(item["unit_id"]) for item in units)
            evidence_text = "\n\n".join(
                f"[unidad {item['unit_id']}] {item['heading']}\n{item['content']}" for item in units
            )
            source_ref = args.source_ref or ",".join(str(item) for item in source_unit_ids)
            source_type = args.source
            source_title = args.source_title
        else:
            raise ValueError("Indica --source con sus datos o usa --evidence-package.")
        item = app.tutor_arbitrator.plan_knowledge_acquisition(
            kind=args.kind,
            subject=args.subject,
            question=args.question,
            locale=args.locale,
            source_type=source_type,
            source_title=source_title,
            source_ref=source_ref,
            source_observed_at=args.source_observed_at,
            revalidate_after=args.revalidate_after,
            evidence_text=evidence_text,
            source_unit_ids=source_unit_ids,
            tutor_id=args.tutor,
            primary_engine=app.language_engine,
            actor=app.identity.system_user,
            auditor_ids=tuple(args.auditor),
            evidence_sources=evidence_sources,
            domain=domain,
            project=project,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="model.knowledge_acquisition.plan",
            target=item["public_id"],
            outcome="pending",
            details={
                "kind": item["knowledge_kind"],
                "source_type": item["source_type"],
                "source_unit_ids": item["source_unit_ids"],
                "tutor_id": item["tutor_id"],
                "auditor_ids": item.get("auditor_ids", []),
                "evidence_source_count": len(item.get("evidence_sources", [])),
                "model_invoked": False,
            },
        )
        return _print(
            item,
            args.json,
            human=(
                f"Plan ID: {item['public_id']}\n"
                f"Fuentes congeladas: {len(item.get('evidence_sources', []))}\n"
                "No se invocó ningún modelo. Ejecuta exactamente:\n"
                "./scripts/elyndra-dev model knowledge-acquisition-run "
                f"{item['public_id']} --approve"
            ),
        )

    if args.model_command == "knowledge-acquisition-run":
        try:
            item = app.tutor_arbitrator.run_knowledge_acquisition(
                args.plan_id, primary_engine=app.language_engine
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            app.audit.record(
                actor=app.identity.system_user,
                action="model.knowledge_acquisition.run",
                target=args.plan_id,
                outcome="failed",
                details={"error": str(exc)[:500]},
            )
            raise
        app.audit.record(
            actor=app.identity.system_user,
            action="model.knowledge_acquisition.run",
            target=args.plan_id,
            outcome="reviewed",
            details={
                "candidate_sha256": item["candidate_sha256"],
                "deterministic_verdict": item["deterministic_audit"].get("verdict"),
                "auditor_verdict": item["auditor_verdict"],
                "automatic_promotion": False,
            },
        )
        return _print(
            item,
            args.json,
            human=(
                f"Plan {args.plan_id} sintetizado y auditado. "
                "El mismo ID ahora identifica la propuesta revisada. Promueve con:\n"
                "./scripts/elyndra-dev model knowledge-promote "
                f"{args.plan_id} --approve"
            ),
        )

    if args.model_command == "knowledge-acquisition-retry":
        item = app.tutor_arbitrator.retry_knowledge_acquisition(
            args.plan_id,
            primary_engine=app.language_engine,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="model.knowledge_acquisition.retry",
            target=item["public_id"],
            outcome="pending",
            details={
                "failed_plan_id": args.plan_id,
                "approval_reused": False,
                "model_invoked": False,
            },
        )
        return _print(
            item,
            args.json,
            human=(
                f"Nuevo plan ID: {item['public_id']}\n"
                f"Plan fallido preservado: {args.plan_id}\n"
                "No reemplaces $NEW_PLAN_ID si usas la variable del ejemplo; el shell "
                "la expande automáticamente. Ejecuta exactamente:\n"
                "./scripts/elyndra-dev model knowledge-acquisition-run "
                f"{item['public_id']} --approve"
            ),
        )

    if args.model_command == "knowledge-acquisition-cancel":
        cancelled = app.general_knowledge.cancel_plan(args.plan_id)
        app.audit.record(
            actor=app.identity.system_user,
            action="model.knowledge_acquisition.cancel",
            target=args.plan_id,
            outcome="cancelled" if cancelled else "not_found",
        )
        return _print(
            {"plan_id": args.plan_id, "cancelled": cancelled},
            args.json,
            human=(
                "Plan pendiente cancelado sin invocar modelos."
                if cancelled
                else "Plan pendiente no encontrado."
            ),
        )

    if args.model_command == "knowledge-proposals":
        items = app.general_knowledge.list_plans(status=args.status, limit=args.limit)
        lines = [f"Propuestas de conocimiento: {len(items)}"]
        lines.extend(
            f"- {item['public_id']} · {item['status']} · "
            f"{item['knowledge_kind']} · {item['subject']}"
            for item in items
        )
        return _print({"items": items}, args.json, human="\n".join(lines))

    if args.model_command == "knowledge-proposal-show":
        item = app.general_knowledge.plan_details(args.plan_id)
        if item is None:
            raise ValueError(f"Propuesta no encontrada: {args.plan_id}")
        status = str(item["status"])
        if status == "reviewed":
            next_action = "Siguiente acción: knowledge-promote con este mismo ID y --approve."
        elif status == "failed":
            next_action = (
                "Siguiente acción: knowledge-acquisition-retry con este ID. "
                "El plan fallido no puede promoverse."
            )
        elif status == "pending":
            next_action = "Siguiente acción: knowledge-acquisition-run con este mismo ID."
        else:
            next_action = "No hay una acción automática pendiente para este estado."
        human = (
            f"Plan/propuesta {item['public_id']} · {item['status']}\n"
            f"Tipo/tema: {item['knowledge_kind']} · {item['subject']}\n"
            f"Dominio/proyecto: {item.get('domain') or 'global'} · "
            f"{item.get('project') or 'global'}\n"
            f"Fuente: {item['source_type']} · {item['source_title']}\n"
            f"Fuentes congeladas: {len(item.get('evidence_sources', []))}\n"
            f"Auditoría determinista: "
            f"{item['deterministic_audit'].get('verdict', 'pendiente')}\n"
            f"Auditorías consultivas: {len(item.get('audit_reviews', []))}\n"
            f"Relación con conocimiento activo: {item.get('conflict_status', 'none')}\n"
            f"Confianza normalizada: "
            f"{item.get('candidate', {}).get('confidence', 'pendiente')} "
            f"({item.get('candidate', {}).get('confidence_mapping', 'sin mapa')})\n"
            f"Metadatos del modelo corregidos: "
            f"{len(item.get('candidate', {}).get('model_metadata_mismatches', {}))}\n"
            f"Revalidación: {item.get('revalidate_after') or 'sin fecha'}\n"
            f"{next_action}\n"
            "No concede permisos ni promueve automáticamente."
        )
        return _print(item, args.json, human=human)

    if args.model_command == "knowledge-promote":
        item = app.general_knowledge.promote(
            args.plan_id,
            actor=app.identity.system_user,
            title=args.title,
            supersedes_public_id=args.supersedes,
            replacement_reason=args.replacement_reason,
            parallel_reason=args.parallel_reason,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="model.knowledge.promote",
            target=item["public_id"],
            outcome="active",
            details={
                "plan_id": args.plan_id,
                "lineage_id": item["lineage_id"],
                "version": item["version"],
                "supersedes": args.supersedes,
                "automatic": False,
                "deletion": False,
            },
        )
        return _print(
            item,
            args.json,
            human=(
                f"Conocimiento {item['public_id']} promovido como versión "
                f"{item['version']} del linaje {item['lineage_id']}. "
                "Las versiones anteriores permanecen preservadas."
            ),
        )

    if args.model_command == "knowledge":
        items = app.general_knowledge.list_knowledge(
            status=args.status, kind=args.kind, limit=args.limit
        )
        lines = [f"Conocimiento general: {len(items)}"]
        lines.extend(
            f"- {item['public_id']} · {item['status']} · {item['knowledge_kind']} · "
            f"v{item['version']} · {item['title']}"
            for item in items
        )
        return _print({"items": items}, args.json, human="\n".join(lines))

    if args.model_command == "knowledge-show":
        item = app.general_knowledge.knowledge_details(args.knowledge_id)
        if item is None:
            raise ValueError(f"Conocimiento no encontrado: {args.knowledge_id}")
        human = (
            f"Conocimiento {item['public_id']} · {item['status']}\n"
            f"Linaje: {item['lineage_id']} · versión {item['version']}\n"
            f"Tipo/tema: {item['knowledge_kind']} · {item['subject']}\n"
            f"Confianza validada: {float(item['validation_confidence']):.2f}\n"
            f"Estado efectivo: {item['effective_validation_status']}\n"
            f"Revalidar después de: {item.get('revalidate_after') or 'sin fecha'}\n"
            f"Contenido: {item['content']}"
        )
        return _print(item, args.json, human=human)

    if args.model_command == "knowledge-search":
        items = app.general_knowledge.search(
            args.query,
            domain=args.domain,
            project=args.project,
            limit=args.limit,
        )
        lines = [f"Resultados de conocimiento: {len(items)}"]
        lines.extend(
            f"- {item['public_id']} · relevancia={float(item['relevance']):.2f} · {item['title']}"
            for item in items
        )
        return _print({"items": items}, args.json, human="\n".join(lines))

    if args.model_command == "knowledge-context":
        item = app.general_knowledge.context_for_query(
            args.query,
            domain=args.domain,
            project=args.project,
        )
        context_text = "\n\n".join(item["context"]) or "Sin conocimiento aplicable."
        human = (
            f"Contexto general para: {args.query}\n"
            f"Aplicados: {len(item['knowledge_ids'])}; "
            f"omitidos: {len(item['omitted_knowledge_ids'])}; "
            f"revalidación: {len(item['revalidation_due_ids'])}; "
            f"conflictos: {len(item['conflicted_knowledge_ids'])}\n"
            f"{context_text}"
        )
        return _print(item, args.json, human=human)

    if args.model_command == "knowledge-revalidation-due":
        items = app.general_knowledge.revalidation_due(limit=args.limit)
        lines = [f"Conocimiento pendiente de revalidación: {len(items)}"]
        lines.extend(
            f"- {item['public_id']} · {item['subject']} · revalidar={item.get('revalidate_after')}"
            for item in items
        )
        return _print({"items": items}, args.json, human="\n".join(lines))

    if args.model_command == "knowledge-conflicts":
        items = app.general_knowledge.list_conflicts(status=args.status, limit=args.limit)
        lines = [f"Conflictos de conocimiento: {len(items)}"]
        lines.extend(
            f"- {item['public_id']} · {item['status']} · {item['subject']} · "
            f"{item['knowledge_a_public_id']} <> {item['knowledge_b_public_id']}"
            for item in items
        )
        return _print({"items": items}, args.json, human="\n".join(lines))

    if args.model_command == "knowledge-conflict-show":
        item = app.general_knowledge.conflict_details(args.conflict_id)
        if item is None:
            raise ValueError(f"Conflicto no encontrado: {args.conflict_id}")
        human = (
            f"Conflicto {item['public_id']} · {item['status']}\n"
            f"Tema: {item['subject']}\n"
            f"Conocimientos: {item['knowledge_a_public_id']} <> "
            f"{item['knowledge_b_public_id']}\n"
            f"Resolución: {item['resolution'] or 'pendiente'}"
        )
        return _print(item, args.json, human=human)

    if args.model_command == "knowledge-conflict-resolve":
        item = app.general_knowledge.resolve_conflict(
            args.conflict_id,
            resolution=args.resolution,
            note=args.note,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="model.knowledge_conflict.resolve",
            target=item["public_id"],
            outcome="resolved",
            details={
                "resolution": item["resolution"],
                "deletion": False,
            },
        )
        return _print(
            item,
            args.json,
            human=(
                f"Conflicto {item['public_id']} resuelto como "
                f"{item['resolution']}. Ningún conocimiento fue eliminado."
            ),
        )

    if args.model_command == "disable":
        target = disable_language_config(app.paths)
        app.audit.record(
            actor=app.identity.system_user,
            action="model.disable",
            target=str(target),
            outcome="success",
        )
        return _print(
            {"config": str(target), "enabled": False},
            args.json,
            human=f"Motor lingüístico desactivado en {target}.",
        )

    result = app.ask(args.text)
    return _result(result, args.json)


def _language_config_snapshot(paths: ElyndraPaths) -> tuple[LanguageConfig, str | None]:
    try:
        return LanguageConfig.load(paths), None
    except LanguageConfigError as exc:
        return LanguageConfig.disabled(), str(exc)


def _language_config_data(
    config: LanguageConfig, path: Path, *, error: str | None = None
) -> dict[str, object]:
    return {
        "config": str(path),
        "exists": path.exists(),
        "enabled": config.enabled,
        "backend": config.backend,
        "binary": str(config.binary) if config.binary else None,
        "model": str(config.model) if config.model else None,
        "endpoint": config.endpoint,
        "model_name": config.model_name,
        "profile": config.profile.name,
        "threads": config.profile.threads,
        "context_size": config.profile.context_size,
        "max_tokens": config.profile.max_tokens,
        "timeout_seconds": config.profile.timeout_seconds,
        "license_id": config.license_id,
        "role": config.role,
        "teacher_allowed": config.teacher_allowed,
        "auditor_allowed": config.auditor_allowed,
        "redistribution_allowed": config.redistribution_allowed,
        "connectivity": config.connectivity,
        "interaction_mode": config.interaction_mode,
        "preferred_language": config.preferred_language,
        "error": error,
    }


def _format_language_config(data: dict[str, object]) -> str:
    if not data["enabled"]:
        return (
            "Motor lingüístico desactivado.\n"
            f"Configuración: {data['config']}\n"
            "Usa 'elyndra model discover' o 'elyndra model ollama-list'."
        )
    if data["backend"] == "ollama-local":
        source = f"Endpoint: {data['endpoint']}\nModelo: {data['model_name']}"
    else:
        source = f"Binario: {data['binary']}\nModelo: {data['model']}"
    return (
        f"Motor: {data['backend']} ({data['profile']})\n"
        f"{source}\n"
        f"Conectividad: {data['connectivity']}\n"
        f"Idioma: {data['interaction_mode']} / {data['preferred_language']}\n"
        f"Procedencia: licencia={data['license_id']}, rol={data['role']}, "
        f"teacher_allowed={data['teacher_allowed']}, "
        f"auditor_allowed={data['auditor_allowed']}\n"
        f"Límites: {data['threads']} hilos, contexto {data['context_size']}, "
        f"salida {data['max_tokens']} tokens, timeout {data['timeout_seconds']} s."
    )


def _format_ollama_models(data: dict[str, object]) -> str:
    models = data["models"]
    running = data["running"]
    lines = [
        f"Ollama local {data['version']} en {data['endpoint']}",
        f"Modelos instalados: {len(models)}",
    ]
    for item in models:
        details = item.get("details", {}) if isinstance(item, dict) else {}
        capabilities = item.get("capabilities", []) if isinstance(item, dict) else []
        lines.append(
            f"- {item.get('name')} — {details.get('parameter_size', '?')} "
            f"{details.get('quantization_level', '')} — "
            f"{', '.join(capabilities) if isinstance(capabilities, list) else capabilities}"
        )
    lines.append(f"Cargados ahora: {len(running)}")
    for item in running:
        lines.append(f"- {item.get('name')} — {item.get('size', 0)} bytes")
    return "\n".join(lines)


def _format_discovery(data: dict[str, object]) -> str:
    runtimes = data["runtimes"]
    models = data["models"]
    processes = data["running_processes"]
    lines = [
        f"Runtimes encontrados: {len(runtimes)}",
    ]
    for item in runtimes:
        lines.append(f"- {item['kind']}: {item['path']} ({item['version']})")
    lines.append(f"Modelos GGUF encontrados: {len(models)}")
    for item in models:
        lines.append(f"- {item['size_mb']:.2f} MB — {item['path']}")
    lines.append(f"Procesos relacionados activos: {len(processes)}")
    for item in processes:
        lines.append(f"- {item}")
    lines.append(
        f"Archivos examinados: {data['scanned_files']}"
        + (" (búsqueda truncada)" if data["truncated"] else "")
    )
    return "\n".join(lines)


def _assistant_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.assistant_command
    if command == "status":
        data = {
            "enabled": True,
            "max_steps": 4,
            "allowlisted_skills": list(app.action_planner.allowed_skills),
            "action_runs": app.action_runs.count(),
            "model_planning": not app.language_engine.name.startswith("no-model"),
            "file_writes": True,
            "write_mode": "exact_reviewed_replacements",
            "pending_change_proposals": app.change_proposals.count(status="proposed"),
            "change_proposals": app.change_proposals.count(),
            "validation_cycles": app.validation_cycles.count(),
            "development_sessions": app.development_sessions.count(),
            "active_development_sessions": app.development_sessions.count(status="active"),
            "conversational_session_continuity": True,
            "automatic_next_actions": False,
            "automatic_repair_loops": False,
            "repair_requires_new_approval": True,
            "file_deletes": False,
            "file_renames": False,
            "directory_creation": False,
            "automatic_installation": False,
            "network_access": False,
            "background_execution": False,
            "constitutional_ethics": True,
            "ethics_core_disableable": False,
            "proactive_advice": app.config.ethical_advice_enabled,
            "ethical_tutor_review": app.config.ethical_tutor_review_enabled,
            "dictionary": app.dictionary.status(),
            "translation": app.translator.status(),
            "preferences": app.preferences.status(),
            "cognitive_executive": app.cognitive_executive.status(),
            "personal_organizer": app.personal_organizer.status(),
            "wellbeing": app.wellbeing.status(),
            "automation": app.automation.status(),
            "scheduler": app.scheduler.status(),
            "semantic_intents": app.semantic_intents.status(),
        }
        human = (
            "Orquestación supervisada activa\n"
            f"- Pasos máximos: {data['max_steps']}\n"
            f"- Skills permitidas: {len(data['allowlisted_skills'])}\n"
            f"- Planes y ejecuciones guardados: {data['action_runs']}\n"
            f"- Propuesta asistida por modelo: {'sí' if data['model_planning'] else 'no'}\n"
            "- Escritura de archivos: solo reemplazos exactos aprobados\n"
            f"- Propuestas pendientes: {data['pending_change_proposals']}\n"
            f"- Ciclos de validación/reparación: {data['validation_cycles']}\n"
            f"- Sesiones de desarrollo: {data['development_sessions']} "
            f"(activas: {data['active_development_sessions']})\n"
            "- Continuidad conversacional de sesiones: sí\n"
            "- Siguientes acciones: sugeridas, nunca automáticas\n"
            "- Constitución ética primaria: activa e inmutable\n"
            f"- Sugerencias profesionales proactivas: "
            f"{'sí' if data['proactive_advice'] else 'no'}\n"
            f"- Tutor ético secundario local: "
            f"{'sí' if data['ethical_tutor_review'] else 'no'}\n"
            f"- Diccionario local: {data['dictionary']['entry_count']} conceptos, "
            f"{len(data['dictionary']['languages'])} idiomas, sin modelo\n"
            "- Traducción conocida: local; texto desconocido: tutor local como respaldo\n"
            f"- Preferencias revisadas: {data['preferences']['active_preferences']} activas, "
            f"{data['preferences']['pending_proposals']} pendientes\n"
            "- Aprendizaje silencioso de preferencias: no\n"
            "- Reparación automática o recursiva: no\n"
            "- Cada reparación requiere una propuesta y aprobación nuevas\n"
            "- Borrados, renombres y carpetas nuevas: no\n"
            "- Instalación automática: no\n"
            "- Red: no\n"
            "- Segundo plano: no\n"
            f"- Ejecutivo cognitivo: {data['cognitive_executive']['decisions']} "
            f"decisiones; {data['cognitive_executive']['active_goals']} objetivos activos; "
            f"{data['cognitive_executive']['pending_tasks']} tareas pendientes\n"
            f"- Organizador personal: {data['personal_organizer']['active_items']} "
            f"elementos activos; {data['personal_organizer']['today_items']} para hoy; "
            "segundo plano=no\n"
            f"- Automatización: {data['automation']['active_policies']} políticas; "
            f"{data['automation']['active_automations']} automatizaciones; "
            f"{data['automation']['pending_approval_runs']} ejecuciones pendientes\n"
            f"- Scheduler opcional: {'activo' if data['scheduler']['running'] else 'detenido'}; "
            f"notificaciones_pendientes={data['scheduler']['pending_notifications']}; "
            "bloqueo_exclusivo=sí; servicio_instalado=no\n"
            f"- Comprensión semántica: {data['semantic_intents']['ontology_intents']} "
            f"intenciones; {data['semantic_intents']['reviewed_examples']} ejemplos "
            f"revisados; {data['semantic_intents']['tutor_fallbacks']} usos de tutor; "
            "prompt_crudo=no; aprendizaje_silencioso=no"
        )
        return _print(data, args.json, human=human)
    if command == "help":
        return _print(
            {
                "examples": [
                    "assistant plan 'Revisa el proyecto Python /ruta y explícame los problemas'",
                    "assistant run ID_DE_VISTA_PREVIA --approve",
                    "assistant history",
                    "assistant report ID",
                    (
                        "assistant change-plan /proyecto --file src/app.py "
                        "--instruction 'Corrige el error'"
                    ),
                    "assistant change-show ID",
                    "assistant change-apply ID --approve",
                    "assistant changes",
                    "assistant session-start ID_PROPUESTA",
                    "assistant sessions",
                    "assistant session-show ID",
                    "assistant session-next ID",
                    "assistant session-close ID --approve",
                ],
                "boundaries": {
                    "max_steps": 4,
                    "single_use_approval": True,
                    "file_writes": "exact_reviewed_replacements",
                    "file_deletes": False,
                    "file_renames": False,
                    "directory_creation": False,
                    "network": False,
                    "installations": False,
                    "arbitrary_commands": False,
                    "conversational_session_continuity": True,
                    "automatic_next_actions": False,
                },
            },
            args.json,
            human=_assistant_help_text(),
        )
    if command == "plan":
        review, review_id = app.review_ethics_request(
            args.text,
            source="assistant.plan",
        )
        if not review.allowed:
            return _print(
                review.to_dict() | {"ethics_review_id": review_id},
                args.json,
                human=review.response,
            )
        plan = app.action_planner.propose(args.text, force=True)
        if plan is None:
            raise ValueError(
                "No pude construir un plan supervisado válido. Incluye una ruta explícita "
                "y una solicitud de inspección o validación."
            )
        preview_id = app.action_runs.save_preview(
            plan=plan,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.action_plan.propose",
            target=plan.plan_id,
            outcome="preview",
            details={
                "preview_id": preview_id,
                "source": plan.source,
                "steps": [step.skill_name for step in plan.steps],
            },
        )
        payload = plan.to_dict() | {"preview_id": preview_id}
        return _print(
            payload,
            args.json,
            human=_format_action_plan(payload),
        )
    if command == "run":
        return _result(
            app.execute_saved_action_plan(args.plan_id, approved=args.approve),
            args.json,
        )
    if command == "history":
        items = app.action_runs.list_recent(limit=args.limit)
        human = "\n\n".join(_format_action_run(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay planes supervisados guardados.",
        )
    if command == "report":
        item = app.action_runs.get(args.run_id)
        if item is None:
            raise ValueError(f"Plan supervisado no encontrado: {args.run_id}")
        return _print(
            item,
            args.json,
            human=_format_action_run(item, detailed=True),
        )
    if command == "executive-evaluate":
        assessment = app.cognitive_executive.assess(
            args.text,
            route=app.router.route(args.text),
            domain=args.domain,
            project=args.project,
        )
        item = app.cognitive_executive.complete(
            assessment,
            ok=True,
            actual_route="preview_only",
            engine="cognitive-executive",
            status="preview",
        )
        confidence = item["confidence"]
        human = (
            f"Decisión ejecutiva {item['public_id']}\n"
            f"- Intención: {item['intent']}\n"
            f"- Dominio/proyecto: {item['domain']} · {item['project'] or 'global'}\n"
            f"- Ruta prevista: {item['selected_route']}\n"
            f"- Riesgo: {item['risk']}\n"
            f"- Confianza conservadora: {confidence['decision_confidence']:.2f}\n"
            f"- Contexto aplicado: {len(item['context_ids'])}; "
            f"omitido: {len(item['omitted_context_ids'])}\n"
            "No se ejecutó ninguna acción ni se invocó ningún modelo."
        )
        return _print(item, args.json, human=human)
    if command == "executive-decisions":
        items = app.cognitive_executive.list_decisions(limit=args.limit)
        human = "\n".join(
            f"- {item['public_id']} · {item['intent']} · "
            f"{item['planned_route']} → {item['actual_route'] or 'pendiente'} · "
            f"{item['status']}"
            for item in items
        )
        return _print(items, args.json, human=human or "Sin decisiones ejecutivas.")
    if command == "executive-decision-show":
        item = app.cognitive_executive.decision_details(args.decision_id)
        if item is None:
            raise ValueError("Decisión ejecutiva no encontrada.")
        return _print(
            item,
            args.json,
            human=(
                f"Decisión {item['public_id']} · {item['status']}\n"
                f"Intención/ruta: {item['intent']} · {item['planned_route']} "
                f"→ {item['actual_route'] or 'pendiente'}\n"
                f"Riesgo: {item['risk']}; aprobación: "
                f"{'sí' if item['approval_required'] else 'no'}; verificación: "
                f"{'sí' if item['verification_required'] else 'no'}\n"
                "No se almacenó el prompt ni razonamiento privado."
            ),
        )
    if command == "goal-create":
        item = app.cognitive_executive.create_goal(
            title=args.title,
            description=args.description,
            domain=args.domain,
            project=args.project,
            priority=args.priority,
            target_date=args.target_date,
            next_action=args.next_action,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.goal.create",
            target=str(item["public_id"]),
            outcome="success",
            details={"priority": item["priority"], "automatic_execution": False},
        )
        return _print(
            item, args.json, human=f"Objetivo creado: {item['public_id']} · {item['title']}"
        )
    if command == "goals":
        items = app.cognitive_executive.list_goals(status=args.status, limit=args.limit)
        human = "\n".join(
            f"- {item['public_id']} · {item['status']} · {item['priority']} · {item['title']}"
            for item in items
        )
        return _print(items, args.json, human=human or "No hay objetivos.")
    if command == "goal-show":
        item = app.cognitive_executive.goal_details(args.goal_id)
        if item is None:
            raise ValueError("Objetivo no encontrado.")
        human = (
            f"Objetivo {item['public_id']} · {item['status']} · {item['priority']}\n"
            f"{item['title']}\nSiguiente acción: {item['next_action'] or '-'}\n"
            f"Tareas: {len(item['tasks'])}"
        )
        return _print(item, args.json, human=human)
    if command == "goal-update":
        item = app.cognitive_executive.update_goal(
            args.goal_id, status=args.status, next_action=args.next_action
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.goal.update",
            target=str(item["public_id"]),
            outcome="success",
            details={"status": item["status"], "automatic_execution": False},
        )
        return _print(item, args.json, human=f"Objetivo actualizado: {item['status']}.")
    if command == "task-create":
        item = app.cognitive_executive.create_task(
            args.goal_id,
            title=args.title,
            priority=args.priority,
            due_date=args.due_date,
            depends_on=tuple(args.depends_on),
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.goal_task.create",
            target=str(item["public_id"]),
            outcome="success",
            details={"goal_id": args.goal_id, "automatic_execution": False},
        )
        return _print(item, args.json, human=f"Tarea creada: {item['public_id']} · {item['title']}")
    if command == "task-complete":
        item = app.cognitive_executive.complete_task(args.task_id, evidence=args.evidence)
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.goal_task.complete",
            target=str(item["public_id"]),
            outcome="success",
            details={"evidence_recorded": True},
        )
        return _print(item, args.json, human=f"Tarea completada: {item['title']}")
    if command == "verify-outcome":
        try:
            evidence = json.loads(args.evidence_json)
        except json.JSONDecodeError as exc:
            raise ValueError("--evidence-json debe ser JSON válido.") from exc
        if not isinstance(evidence, dict):
            raise ValueError("--evidence-json debe ser un objeto JSON.")
        item = app.cognitive_executive.record_verification(
            decision_public_id=args.decision_id,
            expected_outcome=args.expected,
            observed_outcome=args.observed,
            method=args.method,
            status=args.status,
            evidence=evidence,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.outcome.verify",
            target=str(item["public_id"]),
            outcome=str(item["status"]),
            details={"decision_id": args.decision_id},
        )
        return _print(item, args.json, human=f"Verificación registrada: {item['status']}.")
    if command == "verifications":
        items = app.cognitive_executive.list_verifications(limit=args.limit)
        human = "\n".join(
            f"- {item['public_id']} · {item['status']} · {item['verification_method']}"
            for item in items
        )
        return _print(items, args.json, human=human or "Sin verificaciones.")
    if command == "organizer-status":
        data = app.personal_organizer.status()
        human = (
            "Organizador personal local\n"
            f"- Elementos: {data['items']} (activos: {data['active_items']})\n"
            f"- Rutinas activas: {data['active_routines']}\n"
            f"- Cumpleaños activos: {data['active_birthdays']}\n"
            f"- Recordatorios aprobados: {data['approved_reminders']}\n"
            f"- Hoy: {data['today_items']} programados; "
            f"{data['today_overdue']} vencidos\n"
            "- Segundo plano: no; notificaciones automáticas: no"
        )
        return _print(data, args.json, human=human)
    if command == "commitment-create":
        item = app.personal_organizer.create_commitment(
            title=args.title,
            description=args.description,
            event_date=args.date,
            event_time=args.time,
            timezone=args.timezone,
            domain=args.domain,
            project=args.project,
            priority=args.priority,
            recurrence=args.recurrence,
            interval=args.interval,
            weekdays=tuple(args.weekday),
            until=args.until,
            goal_public_id=args.goal_id,
            task_public_id=args.task_id,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.organizer.commitment.create",
            target=str(item["public_id"]),
            outcome="success",
            details={
                "recurrence": item["recurrence_kind"],
                "automatic_execution": False,
            },
        )
        return _print(
            item,
            args.json,
            human=f"Compromiso creado: {item['public_id']} · {item['title']}",
        )
    if command == "birthday-create":
        item = app.personal_organizer.create_birthday(
            person_name=args.person,
            month=args.month,
            day=args.day,
            birth_year=args.year,
            timezone=args.timezone,
            domain=args.domain,
            project=args.project,
            priority=args.priority,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.organizer.birthday.create",
            target=str(item["public_id"]),
            outcome="success",
            details={"person_stored_locally": True, "automatic_execution": False},
        )
        return _print(
            item,
            args.json,
            human=f"Cumpleaños registrado: {item['public_id']} · {args.person}",
        )
    if command == "routine-create":
        item = app.personal_organizer.create_routine(
            title=args.title,
            description=args.description,
            start_date=args.start_date,
            event_time=args.time,
            timezone=args.timezone,
            domain=args.domain,
            project=args.project,
            priority=args.priority,
            recurrence=args.recurrence,
            interval=args.interval,
            weekdays=tuple(args.weekday),
            until=args.until,
            goal_public_id=args.goal_id,
            task_public_id=args.task_id,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.organizer.routine.create",
            target=str(item["public_id"]),
            outcome="success",
            details={
                "recurrence": item["recurrence_kind"],
                "automatic_completion": False,
            },
        )
        return _print(
            item,
            args.json,
            human=f"Rutina creada: {item['public_id']} · {item['title']}",
        )
    if command == "organizer-items":
        items = app.personal_organizer.list_items(
            item_type=args.type,
            status=args.status,
            domain=args.domain,
            project=args.project,
            limit=args.limit,
        )
        human = "\n".join(
            f"- {item['public_id']} · {item['item_type']} · "
            f"{item['status']} · {item['anchor_date']} · {item['title']}"
            for item in items
        )
        return _print(items, args.json, human=human or "Organizador vacío.")
    if command == "organizer-show":
        item = app.personal_organizer.item_details(args.item_id)
        if item is None:
            raise ValueError("Elemento del organizador no encontrado.")
        human = (
            f"{item['public_id']} · {item['item_type']} · {item['status']}\n"
            f"{item['title']}\n"
            f"Inicio: {item['anchor_date']} {item['time_of_day'] or ''}\n"
            f"Recurrencia: {item['recurrence_kind']} "
            f"cada {item['recurrence_interval']}\n"
            f"Recordatorios: {len(item['reminders'])}; "
            f"check-ins: {len(item['checkins'])}"
        )
        return _print(item, args.json, human=human)
    if command == "organizer-update":
        item = app.personal_organizer.update_item_status(args.item_id, status=args.status)
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.organizer.item.update",
            target=str(item["public_id"]),
            outcome="success",
            details={"status": item["status"], "automatic_execution": False},
        )
        return _print(
            item,
            args.json,
            human=f"Elemento actualizado: {item['status']} · {item['title']}",
        )
    if command == "routine-checkin":
        item = app.personal_organizer.checkin_routine(
            args.routine_id,
            occurrence_date=args.date,
            status=args.status,
            note=args.note,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.organizer.routine.checkin",
            target=str(item["public_id"]),
            outcome=str(item["status"]),
            details={"occurrence_date": item["occurrence_date"]},
        )
        return _print(
            item,
            args.json,
            human=(f"Check-in de rutina: {item['status']} · {item['occurrence_date']}"),
        )
    if command == "reminder-propose":
        item = app.personal_organizer.propose_reminder(
            args.item_id,
            minutes_before=args.minutes_before,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.organizer.reminder.propose",
            target=str(item["public_id"]),
            outcome="proposed",
            details={
                "minutes_before": item["minutes_before"],
                "automatic_notification": False,
            },
        )
        return _print(
            item,
            args.json,
            human=(
                f"Recordatorio propuesto: {item['public_id']}. "
                "Debe revisarse por separado; no se programó ninguna notificación."
            ),
        )
    if command == "reminder-review":
        item = app.personal_organizer.review_reminder(
            args.reminder_id,
            decision=args.decision,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.organizer.reminder.review",
            target=str(item["public_id"]),
            outcome=str(item["status"]),
            details={"automatic_notification": False},
        )
        return _print(
            item,
            args.json,
            human=(
                f"Recordatorio {item['status']}. "
                "Solo aparecerá en la agenda; no hay ejecución en segundo plano."
            ),
        )
    if command == "reminders":
        items = app.personal_organizer.list_reminders(status=args.status, limit=args.limit)
        human = "\n".join(
            f"- {item['public_id']} · {item['status']} · "
            f"{item['minutes_before']} min · {item.get('item_title', '')}"
            for item in items
        )
        return _print(items, args.json, human=human or "Sin recordatorios.")
    if command == "daily-brief":
        target_date = args.date or local_today(args.timezone).isoformat()
        data = app.personal_organizer.daily_brief(
            target_date,
            timezone=args.timezone,
            domain=args.domain,
            project=args.project,
        )
        return _print(data, args.json, human=render_daily_brief(data))
    if command == "upcoming":
        start_date = args.start_date or local_today(args.timezone).isoformat()
        data = app.personal_organizer.upcoming(
            start_date=start_date,
            days=args.days,
            timezone=args.timezone,
            domain=args.domain,
            project=args.project,
        )
        human = "\n".join(
            f"- {item['date']} {item['time'] or ''} · {item['item_type']} · {item['title']}"
            for item in data["entries"]
        )
        return _print(data, args.json, human=human or "Sin ocurrencias próximas.")
    if command == "wellbeing-status":
        data = app.wellbeing.status()
        human = (
            "Bienestar personal local\n"
            f"- Check-ins: {data['checkins']}\n"
            f"- Planes activos: {data['active_plans']}\n"
            f"- Acciones pendientes: {data['pending_actions']}\n"
            "- Diagnóstico y tratamiento: no\n"
            "- Ejecución en segundo plano: no"
        )
        return _print(data, args.json, human=human)
    if command == "wellbeing-checkin":
        item = app.wellbeing.create_checkin(
            checkin_date=args.date,
            mood=args.mood,
            energy=args.energy,
            stress=args.stress,
            focus=args.focus,
            sleep_hours=args.sleep_hours,
            sleep_quality=args.sleep_quality,
            hydration=args.hydration,
            nutrition=args.nutrition,
            activity_minutes=args.activity_minutes,
            note=args.note,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.wellbeing.checkin.create",
            target=str(item["public_id"]),
            outcome="success",
            details={"date": item["checkin_date"], "diagnosis": False},
        )
        return _print(
            item,
            args.json,
            human=f"Check-in de bienestar registrado: {item['checkin_date']}.",
        )
    if command == "wellbeing-summary":
        data = app.wellbeing.summary(days=args.days, end_date=args.end_date)
        return _print(data, args.json, human=render_wellbeing_summary(data))
    if command == "wellbeing-checkins":
        items = app.wellbeing.list_checkins(
            start_date=args.start_date,
            end_date=args.end_date,
            limit=args.limit,
        )
        human = "\n".join(
            f"- {item['checkin_date']} · ánimo={item['mood']} · "
            f"energía={item['energy']} · estrés={item['stress']}"
            for item in items
        )
        return _print(items, args.json, human=human or "Sin check-ins.")
    if command == "coaching-plan-create":
        item = app.wellbeing.create_plan(
            title=args.title,
            focus=args.focus,
            objective=args.objective,
            start_date=args.start_date,
            review_date=args.review_date,
            actions=tuple(args.action),
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.coaching.plan.create",
            target=str(item["public_id"]),
            outcome="success",
            details={"actions": len(item["actions"]), "automatic_execution": False},
        )
        return _print(
            item,
            args.json,
            human=f"Plan de coaching creado: {item['public_id']} · {item['title']}",
        )
    if command == "coaching-plans":
        items = app.wellbeing.list_plans(status=args.status, limit=args.limit)
        human = "\n".join(
            f"- {item['public_id']} · {item['status']} · {item['title']}" for item in items
        )
        return _print(items, args.json, human=human or "Sin planes de coaching.")
    if command == "coaching-plan-show":
        item = app.wellbeing.plan_details(args.plan_id)
        if item is None:
            raise ValueError("Plan de coaching no encontrado.")
        human = (
            f"{item['public_id']} · {item['status']} · {item['title']}\n"
            f"Foco: {item['focus']}\n"
            f"Objetivo: {item['objective']}\n"
            f"Acciones: {len(item['actions'])}"
        )
        return _print(item, args.json, human=human)
    if command == "coaching-plan-update":
        item = app.wellbeing.update_plan_status(args.plan_id, status=args.status)
        return _print(
            item,
            args.json,
            human=f"Plan actualizado: {item['status']} · {item['title']}",
        )
    if command == "coaching-action-update":
        item = app.wellbeing.update_action_status(args.action_id, status=args.status)
        return _print(
            item,
            args.json,
            human=f"Acción actualizada: {item['status']} · {item['title']}",
        )
    if command == "automation-status":
        data = app.automation.status()
        return _print(data, args.json, human=app.automation.render_overview())
    if command == "automation-policy-create":
        item = app.automation.create_policy(
            title=args.title,
            action_type=args.action,
            autonomy_level=args.level,
            timezone=args.timezone,
            window_start=args.window_start,
            window_end=args.window_end,
            max_runs_per_day=args.max_runs_per_day,
            starts_at=args.starts_at,
            expires_at=args.expires_at,
            domain=args.domain,
            project=args.project,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.automation.policy.create",
            target=str(item["public_id"]),
            outcome="success",
            details={
                "action_type": item["action_type"],
                "autonomy_level": item["autonomy_level"],
                "background_execution": False,
            },
        )
        return _print(
            item,
            args.json,
            human=(
                f"Política creada: {item['public_id']} · {item['autonomy_level']} · "
                f"{item['action_type']}. No se inició ningún proceso en segundo plano."
            ),
        )
    if command == "automation-policies":
        items = app.automation.list_policies(status=args.status, limit=args.limit)
        human = "\n".join(
            f"- {item['public_id']} · {item['status']} · "
            f"{item['autonomy_level']} · {item['action_type']}"
            for item in items
        )
        return _print(items, args.json, human=human or "Sin políticas.")
    if command == "automation-policy-update":
        item = app.automation.update_policy_status(args.policy_id, status=args.status)
        return _print(
            item,
            args.json,
            human=f"Política actualizada: {item['status']} · {item['title']}",
        )
    if command == "automation-create":
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as exc:
            raise ValueError("--params debe ser un objeto JSON válido.") from exc
        if not isinstance(params, dict):
            raise ValueError("--params debe ser un objeto JSON.")
        item = app.automation.create_automation(
            args.policy_id,
            title=args.title,
            schedule_kind=args.schedule,
            start_date=args.start_date,
            time_of_day=args.time,
            weekdays=tuple(args.weekday),
            month_day=args.month_day,
            interval=args.interval,
            until_date=args.until,
            params=params,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.automation.create",
            target=str(item["public_id"]),
            outcome="success",
            details={
                "policy_id": item["policy_public_id"],
                "schedule": item["schedule_kind"],
                "background_execution": False,
            },
        )
        return _print(
            item,
            args.json,
            human=(
                f"Automatización creada: {item['public_id']} · {item['title']}. "
                "Solo se ejecutará al escanear vencimientos en primer plano."
            ),
        )
    if command == "automations":
        items = app.automation.list_automations(status=args.status, limit=args.limit)
        human = "\n".join(
            f"- {item['public_id']} · {item['status']} · {item['schedule_kind']} · {item['title']}"
            for item in items
        )
        return _print(items, args.json, human=human or "Sin automatizaciones.")
    if command == "automation-update":
        item = app.automation.update_automation_status(args.automation_id, status=args.status)
        return _print(
            item,
            args.json,
            human=f"Automatización actualizada: {item['status']} · {item['title']}",
        )
    if command == "automation-scan":
        data = app.automation.scan_due(
            now_value=args.now,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.automation.scan",
            target="foreground-dispatch",
            outcome="success",
            details={**data["summary"], "background_execution": False},
        )
        human = (
            "Escaneo de automatización completado en primer plano.\n"
            f"- Ejecuciones creadas: {data['summary']['created']}\n"
            f"- Pendientes de aprobación: {data['summary']['pending_approval']}\n"
            f"- Preparadas/ejecutadas bajo política: "
            f"{data['summary']['prepared_or_executed']}\n"
            f"- Omitidas: {data['summary']['skipped']}\n"
            "No se mantuvo ningún proceso en segundo plano."
        )
        return _print(data, args.json, human=human)
    if command == "automation-runs":
        items = app.automation.list_runs(status=args.status, limit=args.limit)
        human = "\n".join(
            f"- {item['public_id']} · {item['status']} · {item['occurrence_at']} · {item['title']}"
            for item in items
        )
        return _print(items, args.json, human=human or "Sin ejecuciones.")
    if command == "automation-run-approve":
        item = app.automation.approve_run(args.run_id, actor=app.identity.system_user)
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.automation.run.approve",
            target=str(item["public_id"]),
            outcome=str(item["status"]),
            details={"action_type": item["action_type"]},
        )
        return _print(
            item,
            args.json,
            human=(
                f"Ejecución aprobada: {item['status']} · {item['title']}. "
                "El resultado quedó en la bandeja local."
            ),
        )
    if command == "automation-inbox":
        items = app.automation.list_inbox(status=args.status, limit=args.limit)
        human = "\n\n".join(
            f"{item['public_id']} · {item['status']} · {item['title']}\n{item['body']}"
            for item in items
        )
        return _print(items, args.json, human=human or "Bandeja local vacía.")
    if command == "automation-inbox-update":
        item = app.automation.update_inbox_status(args.inbox_id, status=args.status)
        return _print(
            item,
            args.json,
            human=f"Bandeja actualizada: {item['status']} · {item['title']}",
        )
    if command == "scheduler-status":
        data = app.scheduler.status()
        latest = data.get("latest_session") or {}
        human = (
            "Scheduler local opcional\n"
            f"- Activo: {'sí' if data['running'] else 'no'}\n"
            f"- Bloqueo entre procesos: {data['lock_path']}\n"
            f"- Notificaciones pendientes: {data['pending_notifications']}\n"
            f"- Última sesión: {latest.get('status', 'sin sesiones')}\n"
            "- Instalación de servicios: no\n"
            "- Red: no"
        )
        return _print(data, args.json, human=human)
    if command == "scheduler-cycle":
        lease = app.scheduler.open(
            interval_seconds=60,
            actor=app.identity.system_user,
            mode="cli-cycle",
        )
        try:
            data = lease.cycle(now_value=args.now)
        finally:
            lease.close(status="stopped")
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.scheduler.cycle",
            target=lease.public_id,
            outcome="success",
            details=data["summary"],
        )
        human = (
            "Ciclo del scheduler completado en primer plano.\n"
            f"- Ejecuciones creadas: {data['summary']['created']}\n"
            f"- Notificaciones locales creadas: "
            f"{data['summary']['notifications_created']}\n"
            "No se instaló ningún servicio ni se utilizó red."
        )
        return _print(data, args.json, human=human)
    if command == "scheduler-run":
        lease = app.scheduler.open(
            interval_seconds=args.interval_seconds,
            actor=app.identity.system_user,
            mode="cli",
        )
        stop_event = threading.Event()

        def report_cycle(data: dict[str, Any]) -> None:
            if args.json:
                print(json.dumps(data, ensure_ascii=False, default=str))
                return
            summary = data["summary"]
            print(
                "Ciclo scheduler: "
                f"runs={summary['created']}, "
                f"notificaciones={summary['notifications_created']}"
            )
            for item in data["notifications"]:
                print(f"[NOTIFICACIÓN LOCAL] {item['title']}\n{item['body']}")

        if not args.json:
            print(
                f"Scheduler local activo cada {lease.interval_seconds}s. "
                "Presiona Ctrl+C para detenerlo limpiamente."
            )
        try:
            lease.run_forever(stop_event, on_cycle=report_cycle)
        except KeyboardInterrupt:
            stop_event.set()
            lease.close(status="stopped")
            if not args.json:
                print("\nScheduler local detenido limpiamente.")
        return 0
    if command == "local-notifications":
        items = app.scheduler.list_notifications(status=args.status, limit=args.limit)
        human = "\n\n".join(
            f"{item['public_id']} · {item['status']} · {item['title']}\n{item['body']}"
            for item in items
        )
        return _print(items, args.json, human=human or "Sin notificaciones locales.")
    if command == "local-notification-update":
        item = app.scheduler.update_notification_status(
            args.notification_id,
            status=args.status,
        )
        return _print(
            item,
            args.json,
            human=f"Notificación actualizada: {item['status']} · {item['title']}",
        )
    if command == "understand":
        tutor_engine = (
            None
            if app.language_engine.name.startswith("no-model")
            else app.tutor_arbitrator.bound_engine("general_language", app.language_engine)
        )
        resolution = app.semantic_intents.resolve(
            args.text,
            tutor_engine=tutor_engine,
            response_language=detect_language(args.text, fallback="es").code,
        )
        if resolution is None:
            data = {
                "status": "not_applicable",
                "intent": None,
                "model_used": False,
                "action_executed": False,
            }
            return _print(data, args.json, human="No se detectó una intención personal aplicable.")
        data = resolution.to_dict()
        human = (
            f"Interpretación: {resolution.intent or 'requiere aclaración'}\n"
            f"- Estado: {resolution.status}\n"
            f"- Confianza: {resolution.confidence:.2f}\n"
            f"- Fuente: {resolution.source}\n"
            f"- Tutor lingüístico: {'sí' if resolution.tutor_used else 'no'}\n"
            f"- Entidades: {json.dumps(resolution.entities, ensure_ascii=False)}\n"
            "No se ejecutó ninguna acción."
        )
        if resolution.clarification:
            human += f"\n- Aclaración: {resolution.clarification}"
        return _print(data, args.json, human=human)
    if command == "intent-status":
        data = app.semantic_intents.status()
        human = (
            "Comprensión semántica supervisada\n"
            f"- Intenciones canónicas: {data['ontology_intents']}\n"
            f"- Ejemplos revisados: {data['reviewed_examples']}\n"
            f"- Resoluciones: {data['resolutions']}\n"
            f"- Propuestas pendientes: {data['pending_learning_proposals']}\n"
            f"- Fallbacks de tutor: {data['tutor_fallbacks']}\n"
            "- Prompt crudo almacenado: no\n"
            "- Aprendizaje silencioso: no"
        )
        return _print(data, args.json, human=human)
    if command == "intent-resolutions":
        items = app.semantic_intents.list_resolutions(limit=args.limit)
        human = "\n".join(
            f"- {item['public_id']} · {item['status']} · "
            f"{item['intent'] or 'ambigua'} · {item['confidence']:.2f} · "
            f"tutor={'sí' if item['tutor_used'] else 'no'}"
            for item in items
        )
        return _print(items, args.json, human=human or "Sin resoluciones semánticas.")
    if command == "intent-learning-propose":
        item = app.semantic_intents.propose_learning(
            phrase=args.phrase,
            intent=args.intent,
            source=args.source,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.intent_learning.propose",
            target=str(item["public_id"]),
            outcome="pending",
            details={"intent": item["intent"], "silent_learning": False},
        )
        return _print(
            item,
            args.json,
            human=(
                f"Propuesta semántica creada: {item['public_id']} · "
                f"{item['intent']}. No está activa hasta revisión separada."
            ),
        )
    if command == "intent-learning-proposals":
        items = app.semantic_intents.list_proposals(status=args.status, limit=args.limit)
        human = "\n".join(
            f"- {item['public_id']} · {item['status']} · {item['intent']} · "
            f"{item['normalized_phrase']}"
            for item in items
        )
        return _print(items, args.json, human=human or "Sin propuestas semánticas.")
    if command == "intent-learning-review":
        item = app.semantic_intents.review_learning(
            args.proposal_id,
            decision=args.decision,
            actor=app.identity.system_user,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="assistant.intent_learning.review",
            target=str(item["public_id"]),
            outcome=str(item["status"]),
            details={"intent": item["intent"], "automatic_activation": False},
        )
        return _print(
            item,
            args.json,
            human=(
                f"Propuesta semántica {item['status']}: "
                f"{item['normalized_phrase']} → {item['intent']}."
            ),
        )
    if command == "change-plan":
        return _result(
            app.propose_change(
                project_root=args.project_root,
                requested_files=args.change_files,
                instruction=args.instruction,
                allow_root_once=args.allow_root_once,
            ),
            args.json,
        )
    if command == "change-show":
        item = app.change_proposals.get(args.proposal_id)
        if item is None:
            raise ValueError(f"Propuesta no encontrada: {args.proposal_id}")
        item = _with_change_session(app, item)
        return _print(item, args.json, human=_format_change_proposal(item))
    if command == "change-apply":
        return _result(
            app.apply_saved_change_proposal(
                args.proposal_id,
                approved=args.approve,
                allow_root_once=args.allow_root_once,
            ),
            args.json,
        )
    if command == "change-reject":
        return _result(
            app.reject_saved_change_proposal(args.proposal_id, approved=args.approve),
            args.json,
        )
    if command == "changes":
        items = [
            _with_change_session(app, item)
            for item in app.change_proposals.list_recent(limit=args.limit)
        ]
        human = "\n\n".join(_format_change_proposal(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay propuestas de cambios guardadas.",
        )
    if command == "validate-plan":
        cycle = app.create_validation_cycle(
            args.proposal_id,
            validation_request=args.request,
        )
        return _print(
            cycle,
            args.json,
            human=_format_validation_cycle(cycle, detailed=True),
        )
    if command == "validate-run":
        return _result(
            app.execute_validation_cycle(
                args.cycle_id,
                approved=args.approve,
            ),
            args.json,
        )
    if command == "repair-plan":
        return _result(
            app.propose_repair_for_cycle(
                args.cycle_id,
                instruction=args.instruction,
                allow_root_once=args.allow_root_once,
            ),
            args.json,
        )
    if command == "cycle-show":
        item = app.validation_cycles.get(args.cycle_id)
        if item is None:
            raise ValueError(f"Ciclo no encontrado: {args.cycle_id}")
        return _print(
            item,
            args.json,
            human=_format_validation_cycle(item, detailed=True),
        )
    if command == "cycles":
        items = app.validation_cycles.list_recent(limit=args.limit)
        human = "\n\n".join(_format_validation_cycle(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay ciclos supervisados guardados.",
        )
    if command == "session-start":
        return _result(
            app.start_development_session(
                args.proposal_id,
                objective=args.objective,
            ),
            args.json,
        )
    if command == "sessions":
        items = [
            _with_session_guidance(app, item)
            for item in app.development_sessions.list_recent(limit=args.limit)
        ]
        human = "\n\n".join(_format_development_session(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay sesiones de desarrollo guardadas.",
        )
    if command == "session-show":
        item = app.development_sessions.get(args.session_id)
        if item is None:
            raise ValueError(f"Sesión no encontrada: {args.session_id}")
        item = _with_session_guidance(app, item)
        return _print(
            item,
            args.json,
            human=_format_development_session(item, detailed=True),
        )
    if command == "session-next":
        return _result(
            app.development_session_guidance(
                args.session_id,
                language=args.language,
            ),
            args.json,
        )
    if command == "session-close":
        return _result(
            app.close_development_session(args.session_id, approved=args.approve),
            args.json,
        )
    raise ValueError(f"Comando de asistente desconocido: {command}")


def _assistant_help_text() -> str:
    return (
        "Planes supervisados de Elyndra\n\n"
        "Proponer sin ejecutar:\n"
        "  ./scripts/elyndra-dev assistant plan "
        "'Revisa el proyecto Python /ruta y explícame los problemas'\n\n"
        "Ejecutar el ID exacto con aprobación explícita:\n"
        "  ./scripts/elyndra-dev assistant run ID_DE_VISTA_PREVIA --approve\n\n"
        "Historial:\n"
        "  ./scripts/elyndra-dev assistant history\n"
        "  ./scripts/elyndra-dev assistant report ID\n\n"
        "Propuestas de cambios revisables:\n"
        "  ./scripts/elyndra-dev assistant change-plan /ruta/proyecto "
        "--file src/app.py --instruction 'Corrige el error'\n"
        "  ./scripts/elyndra-dev assistant change-show ID\n"
        "  ./scripts/elyndra-dev assistant change-apply ID --approve\n"
        "  ./scripts/elyndra-dev assistant changes\n\n"
        "Validación y reparación supervisadas:\n"
        "  ./scripts/elyndra-dev assistant validate-plan ID_CAMBIO "
        "--request 'Ejecuta Ruff y Pytest en /ruta/proyecto'\n"
        "  ./scripts/elyndra-dev assistant validate-run ID_CICLO --approve\n"
        "  ./scripts/elyndra-dev assistant repair-plan ID_CICLO "
        "--instruction 'Corrige solo los fallos observados'\n"
        "  ./scripts/elyndra-dev assistant cycles\n\n"
        "Sesiones de desarrollo supervisadas:\n"
        "  ./scripts/elyndra-dev assistant sessions\n"
        "  ./scripts/elyndra-dev assistant session-show ID_SESION\n"
        "  ./scripts/elyndra-dev assistant session-next ID_SESION\n"
        "  ./scripts/elyndra-dev assistant session-close ID_SESION --approve\n\n"
        "Constitución ética profesional:\n"
        "  ./scripts/elyndra-dev ethics status\n"
        "  ./scripts/elyndra-dev ethics principles\n"
        "  ./scripts/elyndra-dev ethics review 'TEXTO'\n"
        "  ./scripts/elyndra-dev ethics history\n\n"
        "Cada plan admite hasta cuatro pasos allowlisted. Los cambios se limitan a uno "
        "a tres archivos exactos, se muestran como diff y se aplican una sola vez. No "
        "se borran ni renombran archivos, no se crean carpetas, no se instalan "
        "dependencias, no se usa red y no se ejecutan comandos arbitrarios. El chat "
        "puede recuperar una sesión enfocada y sugerir pasos siguientes, pero nunca "
        "los ejecuta automáticamente. La constitución ética primaria se aplica antes "
        "de planes, cambios y modelo; su núcleo de no daño no se puede desactivar."
    )


def _with_session_guidance(
    app: ElyndraApplication,
    item: dict[str, Any],
) -> dict[str, Any]:
    enriched = app.development_sessions.get(str(item.get("public_id", ""))) or dict(item)
    guidance = build_session_guidance(enriched)
    enriched["guidance"] = guidance.to_dict()
    return enriched


def _with_change_session(
    app: ElyndraApplication,
    item: dict[str, Any],
) -> dict[str, Any]:
    enriched = dict(item)
    session = app.development_sessions.find_by_change(str(item.get("public_id", "")))
    enriched["development_session_id"] = session.get("public_id") if session is not None else None
    return enriched


def _format_development_session(
    item: dict[str, Any],
    *,
    detailed: bool = False,
) -> str:
    lines = [
        f"Sesión {item.get('public_id')} · {item.get('status')}",
        f"Proyecto: {item.get('project_root')}",
        f"Objetivo: {item.get('objective')}",
        f"Cambio actual: {item.get('current_change_proposal_id')}",
        f"Ciclo actual: {item.get('current_validation_cycle_id') or 'ninguno'}",
    ]
    if detailed:
        for event in item.get("events", []) or []:
            lines.append(
                f"- {event.get('created_at')} · {event.get('event_type')} · "
                f"{event.get('status')}: {event.get('summary')}"
            )
    guidance = item.get("guidance", {}) or {}
    actions = guidance.get("actions", []) or []
    if actions:
        lines.append("Siguientes acciones posibles (no se ejecutó ninguna):")
        for action in actions:
            suffix = " · requiere aprobación" if action.get("requires_approval") else ""
            lines.append(f"- {action.get('label')}{suffix}: {action.get('command')}")
    return "\n".join(lines)


def _format_validation_cycle(
    item: dict[str, Any],
    *,
    detailed: bool = False,
) -> str:
    plan = item.get("plan", {}) or {}
    lines = [
        f"Ciclo {item.get('public_id')} · {item.get('status')}",
        f"Cambio origen: {item.get('source_change_proposal_id')}",
        f"Proyecto: {item.get('project_root')}",
        f"Plan: {plan.get('plan_id', '-')}",
    ]
    for index, step in enumerate(plan.get("steps", []) or [], start=1):
        lines.append(f"{index}. {step.get('skill_name')}")
    if item.get("validation_run_id"):
        lines.append(f"Ejecución: {item.get('validation_run_id')}")
    if item.get("repair_proposal_id"):
        lines.append(f"Reparación: {item.get('repair_proposal_id')}")
    if detailed:
        result = item.get("validation_result", {}) or {}
        for step in result.get("steps", []) or []:
            state = "passed" if step.get("ok") else "failed"
            lines.append(f"- {step.get('skill_name')}: {state} — {step.get('message', '')}")
    return "\n".join(lines)


def _format_change_proposal(item: dict[str, Any]) -> str:
    proposal = item.get("proposal", {}) or {}
    lines = [
        f"Propuesta {item.get('public_id')} · {item.get('status')}",
        f"Proyecto: {item.get('project_root')}",
        f"Sesión: {item.get('development_session_id') or 'ninguna'}",
        str(proposal.get("summary", "")),
    ]
    changes = proposal.get("changes", []) or []
    for index, change in enumerate(changes, start=1):
        lines.append(f"{index}. {change.get('mode')} · {change.get('relative_path')}")
    diff = str(item.get("diff", "")).strip()
    if diff:
        lines.extend(("", diff))
    return "\n".join(lines).strip()


def _format_action_plan(plan: dict[str, Any]) -> str:
    lines = [
        f"Plan {plan.get('plan_id')} · fuente={plan.get('source')}",
        str(plan.get("summary", "")),
    ]
    if plan.get("preview_id"):
        lines.append(f"ID de vista previa: {plan.get('preview_id')}")
    for index, step in enumerate(plan.get("steps", []), start=1):
        target = (step.get("params", {}) or {}).get("path", "")
        lines.append(f"{index}. {step.get('skill_name')} · {target}")
    lines.append("No se ejecutó ninguna skill.")
    return "\n".join(lines)


def _format_action_run(item: dict[str, Any], *, detailed: bool = False) -> str:
    plan = item.get("plan", {})
    result = item.get("result", {})
    lines = [
        f"{item.get('public_id')} · {item.get('status')} · {item.get('duration_ms') or '-'} ms",
        f"Plan: {item.get('plan_id')} · fuente={item.get('source')}",
        str(plan.get("summary", "")),
    ]
    for index, step in enumerate(plan.get("steps", []), start=1):
        lines.append(f"{index}. {step.get('skill_name')}")
    if detailed:
        for step in result.get("steps", []):
            lines.append(
                f"- {step.get('skill_name')}: "
                f"{'passed' if step.get('ok') else 'failed'} — "
                f"{step.get('message', '')}"
            )
    return "\n".join(lines)


def _php_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.php_command
    if command == "help":
        return _print(
            {"allowed_roots": [str(root) for root in app.config.allowed_roots]},
            args.json,
            human=_php_help_text(app),
        )
    if command == "status":
        capabilities = php_tool_capabilities()
        human = "Herramientas PHP:\n" + "\n".join(
            f"- {name}: {'sí' if available else 'no'}" for name, available in capabilities.items()
        )
        human += "\nLos binarios vendor/bin se detectan al ejecutar dentro de cada proyecto."
        return _print(capabilities, args.json, human=human)
    if command == "history":
        items = app.verification_runs.list_recent(
            toolchain="php",
            project_root=args.path,
            limit=args.limit,
        )
        human = "\n".join(_format_verification_run(item) for item in items)
        return _print(items, args.json, human=human or "No hay verificaciones PHP guardadas.")
    if command == "report":
        item = app.verification_runs.get(args.run_id)
        if item is None:
            raise ValueError(f"Verificación PHP no encontrada: {args.run_id}")
        return _print(item, args.json, human=_format_verification_run(item, detailed=True))
    if command == "compare":
        comparison = app.verification_runs.compare(args.first_run_id, args.second_run_id)
        return _print(
            comparison,
            args.json,
            human=_format_verification_comparison(comparison),
        )

    params: dict[str, Any] = {"path": str(args.path)}
    if bool(getattr(args, "allow_root_once", False)):
        params["allow_root_once"] = True
        params["authorization_source"] = "cli_allow_root_once"
    skill_name = "php.syntax_validate"
    if command == "composer-validate":
        skill_name = "composer.validate"
        if args.strict:
            params["strict"] = True
    elif command == "phpstan":
        skill_name = "phpstan.analyse"
        if args.config is not None:
            params["config"] = str(args.config)
        if args.level is not None:
            params["level"] = args.level
    elif command == "phpunit":
        skill_name = "phpunit.run"
        if args.config is not None:
            params["config"] = str(args.config)
        if args.testsuite:
            params["testsuite"] = args.testsuite
        if args.filter:
            params["filter"] = args.filter
    elif command == "inspect":
        skill_name = "php.project_inspect"
    elif command == "syntax-project":
        skill_name = "php.syntax_scan"
        if args.max_files is not None:
            params["max_files"] = args.max_files
    elif command == "verify":
        skill_name = "php.verify_project"
        for argument, parameter in (
            ("composer", "composer_enabled"),
            ("syntax_scan", "syntax_scan_enabled"),
            ("phpstan", "phpstan_enabled"),
            ("phpunit", "phpunit_enabled"),
            ("fail_fast", "fail_fast"),
            ("require_tools", "require_tools"),
        ):
            value = getattr(args, argument)
            if value is not None:
                params[parameter] = value
        if args.max_files is not None:
            params["max_files"] = args.max_files
    return _result(
        app.execute_skill(skill_name, params, approved=args.approve),
        args.json,
    )


def _webdev_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.webdev_command
    if command == "help":
        return _print(
            {"allowed_roots": [str(root) for root in app.config.allowed_roots]},
            args.json,
            human=_webdev_help_text(app),
        )
    if command == "status":
        tools = {
            "node": shutil.which("node") is not None,
            "typescript_global": shutil.which("tsc") is not None,
            "eslint_global": shutil.which("eslint") is not None,
            "stylelint_global": shutil.which("stylelint") is not None,
            "project_local_detection": True,
            "html_internal": True,
            "css_internal": True,
        }
        human = "Herramientas web:\n" + "\n".join(
            f"- {name}: {'sí' if available else 'no'}" for name, available in tools.items()
        )
        human += "\nLos binarios node_modules/.bin se priorizan dentro del proyecto."
        return _print(tools, args.json, human=human)
    if command == "history":
        items = app.verification_runs.list_recent(
            toolchain="web",
            project_root=args.path,
            limit=args.limit,
        )
        human = "\n".join(_format_verification_run(item) for item in items)
        return _print(items, args.json, human=human or "No hay verificaciones web guardadas.")
    if command == "report":
        item = app.verification_runs.get(args.run_id)
        if item is None or item.get("toolchain") != "web":
            raise ValueError(f"Verificación web no encontrada: {args.run_id}")
        return _print(item, args.json, human=_format_verification_run(item, detailed=True))
    if command == "compare":
        comparison = app.verification_runs.compare(
            args.first_run_id,
            args.second_run_id,
        )
        if comparison.get("toolchain") != "web":
            raise ValueError("Los IDs no corresponden a verificaciones web.")
        return _print(
            comparison,
            args.json,
            human=_format_verification_comparison(comparison),
        )

    params: dict[str, Any] = {"path": str(args.path)}
    if bool(getattr(args, "allow_root_once", False)):
        params["allow_root_once"] = True
        params["authorization_source"] = "cli_allow_root_once"
    if getattr(args, "max_files", None) is not None:
        params["max_files"] = args.max_files
    skill_names = {
        "inspect": "web.project_inspect",
        "html": "html.validate",
        "css": "css.validate",
        "javascript": "javascript.syntax_validate",
        "typescript": "typescript.check",
        "framework": "web.framework_validate",
        "eslint": "eslint.lint",
        "stylelint": "stylelint.lint",
        "verify": "web.verify_project",
    }
    if command == "typescript" and args.config:
        params["config"] = args.config
    if command == "framework" and args.framework_preset:
        params["framework_preset"] = args.framework_preset
    if command == "eslint" and args.eslint_config:
        params["eslint_config"] = args.eslint_config
    if command == "stylelint" and args.stylelint_config:
        params["stylelint_config"] = args.stylelint_config
    if command == "verify":
        for argument, parameter in (
            ("html", "html_enabled"),
            ("css", "css_enabled"),
            ("javascript", "javascript_enabled"),
            ("typescript", "typescript_enabled"),
            ("framework_checks", "framework_checks_enabled"),
            ("eslint", "eslint_enabled"),
            ("stylelint", "stylelint_enabled"),
            ("fail_fast", "fail_fast"),
            ("require_tools", "require_tools"),
        ):
            value = getattr(args, argument)
            if value is not None:
                params[parameter] = value
        for argument in ("framework_preset", "eslint_config", "stylelint_config"):
            value = getattr(args, argument, None)
            if value:
                params[argument] = value
    return _result(
        app.execute_skill(skill_names[command], params, approved=args.approve),
        args.json,
    )


def _webdev_help_text(app: ElyndraApplication) -> str:
    roots = "\n".join(f"- {root}" for root in app.config.allowed_roots)
    return (
        "Skills web controladas\n\n"
        "Raíces persistentes actuales:\n"
        f"{roots}\n\n"
        "Inspección sin ejecutar código:\n"
        "  ./scripts/elyndra-dev webdev inspect /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Validaciones individuales:\n"
        "  ./scripts/elyndra-dev webdev html /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev webdev css /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev webdev javascript /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev webdev typescript /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Flujo completo:\n"
        "  ./scripts/elyndra-dev webdev verify /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "No se ejecutan scripts npm, npm, npx, yarn, pnpm o bun. No se instalan "
        "paquetes y no se aceptan flags libres."
    )


def _format_verification_run(item: dict[str, Any], *, detailed: bool = False) -> str:
    lines = [
        f"{item['public_id']} · {item['status']} · {item['project_root']}",
        f"  Inicio: {item['started_at']}; duración={item.get('duration_ms') or '-'} ms",
    ]
    if detailed:
        for stage in item.get("summary", {}).get("stages", []):
            lines.append(f"  - {stage.get('name', '?')}: {stage.get('status', 'unknown')}")
    return "\n".join(lines)


def _format_verification_comparison(comparison: dict[str, Any]) -> str:
    lines = [
        f"Comparación {comparison['first']['public_id']} → {comparison['second']['public_id']}",
        f"Estado: {comparison['first']['status']} → {comparison['second']['status']}",
        f"Diferencia de duración: {comparison.get('duration_delta_ms')} ms",
    ]
    lines.extend(
        f"- {item['name']}: {item['before']} → {item['after']}"
        for item in comparison.get("stage_changes", [])
        if item.get("changed")
    )
    return "\n".join(lines)


def _pythondev_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.pythondev_command
    if command == "help":
        return _print(
            {"allowed_roots": [str(root) for root in app.config.allowed_roots]},
            args.json,
            human=_pythondev_help_text(app),
        )
    if command == "status":
        tools = {
            "python": bool(sys.executable),
            "ruff_global": shutil.which("ruff") is not None,
            "mypy_global": shutil.which("mypy") is not None,
            "pytest_global": shutil.which("pytest") is not None,
            "project_local_detection": True,
            "compile_internal": True,
            "pyproject_internal": True,
        }
        human = "Herramientas Python:\n" + "\n".join(
            f"- {name}: {'sí' if available else 'no'}" for name, available in tools.items()
        )
        human += "\nLos binarios .venv/bin y venv/bin se priorizan dentro del proyecto."
        return _print(tools, args.json, human=human)
    if command == "history":
        items = app.verification_runs.list_recent(
            toolchain="python",
            project_root=args.path,
            limit=args.limit,
        )
        human = "\n".join(_format_verification_run(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay verificaciones Python guardadas.",
        )
    if command == "report":
        item = app.verification_runs.get(args.run_id)
        if item is None or item.get("toolchain") != "python":
            raise ValueError(f"Verificación Python no encontrada: {args.run_id}")
        return _print(item, args.json, human=_format_verification_run(item, detailed=True))
    if command == "compare":
        comparison = app.verification_runs.compare(
            args.first_run_id,
            args.second_run_id,
        )
        if comparison.get("toolchain") != "python":
            raise ValueError("Los IDs no corresponden a verificaciones Python.")
        return _print(
            comparison,
            args.json,
            human=_format_verification_comparison(comparison),
        )

    params: dict[str, Any] = {"path": str(args.path)}
    if bool(getattr(args, "allow_root_once", False)):
        params["allow_root_once"] = True
        params["authorization_source"] = "cli_allow_root_once"
    if getattr(args, "max_files", None) is not None:
        params["max_files"] = args.max_files
    skill_names = {
        "inspect": "python.project_inspect",
        "pyproject": "python.pyproject_validate",
        "compile": "python.compile_project",
        "ruff": "ruff.check",
        "mypy": "mypy.check",
        "pytest": "pytest.run",
        "verify": "python.verify_project",
    }
    for argument in ("ruff_config", "mypy_config", "pytest_path"):
        value = getattr(args, argument, None)
        if value:
            params[argument] = value
    if command == "verify":
        for argument, parameter in (
            ("pyproject", "pyproject_enabled"),
            ("compile", "compile_enabled"),
            ("ruff", "ruff_enabled"),
            ("mypy", "mypy_enabled"),
            ("pytest", "pytest_enabled"),
            ("fail_fast", "fail_fast"),
            ("require_tools", "require_tools"),
        ):
            value = getattr(args, argument)
            if value is not None:
                params[parameter] = value
    return _result(
        app.execute_skill(skill_names[command], params, approved=args.approve),
        args.json,
    )


def _pythondev_help_text(app: ElyndraApplication) -> str:
    roots = "\n".join(f"- {root}" for root in app.config.allowed_roots)
    return (
        "Skills Python controladas\n\n"
        "Raíces persistentes actuales:\n"
        f"{roots}\n\n"
        "Inspección sin ejecutar código:\n"
        "  ./scripts/elyndra-dev pythondev inspect /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Validaciones individuales:\n"
        "  ./scripts/elyndra-dev pythondev pyproject /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev pythondev compile /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev pythondev ruff /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev pythondev mypy /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev pythondev pytest /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Flujo completo:\n"
        "  ./scripts/elyndra-dev pythondev verify /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "No se ejecuta pip, build, tox, nox ni scripts declarados en pyproject.toml. "
        "No se instalan herramientas y no se aceptan flags libres."
    )


def _javadev_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.javadev_command
    if command == "help":
        return _print(
            {"allowed_roots": [str(root) for root in app.config.allowed_roots]},
            args.json,
            human=_javadev_help_text(app),
        )
    if command == "status":
        tools = {
            "java": shutil.which("java") is not None,
            "javac": shutil.which("javac") is not None,
            "maven_global": shutil.which("mvn") is not None,
            "gradle_global": shutil.which("gradle") is not None,
            "wrappers_executed": False,
            "descriptor_internal": True,
            "project_local_detection": True,
        }
        human = "Herramientas Java/JVM:\n" + "\n".join(
            f"- {name}: {'sí' if available else 'no'}" for name, available in tools.items()
        )
        human += (
            "\nMaven y Gradle se ejecutan solo desde binarios globales, "
            "en modo offline y con argumentos fijos."
        )
        return _print(tools, args.json, human=human)
    if command == "history":
        items = app.verification_runs.list_recent(
            toolchain="java",
            project_root=args.path,
            limit=args.limit,
        )
        human = "\n".join(_format_verification_run(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay verificaciones Java guardadas.",
        )
    if command == "report":
        item = app.verification_runs.get(args.run_id)
        if item is None or item.get("toolchain") != "java":
            raise ValueError(f"Verificación Java no encontrada: {args.run_id}")
        return _print(item, args.json, human=_format_verification_run(item, detailed=True))
    if command == "compare":
        comparison = app.verification_runs.compare(
            args.first_run_id,
            args.second_run_id,
        )
        if comparison.get("toolchain") != "java":
            raise ValueError("Los IDs no corresponden a verificaciones Java.")
        return _print(
            comparison,
            args.json,
            human=_format_verification_comparison(comparison),
        )

    params: dict[str, Any] = {"path": str(args.path)}
    if bool(getattr(args, "allow_root_once", False)):
        params["allow_root_once"] = True
        params["authorization_source"] = "cli_allow_root_once"
    for argument in ("build_tool", "java_release", "max_files"):
        value = getattr(args, argument, None)
        if value is not None:
            params[argument] = value
    skill_names = {
        "inspect": "java.project_inspect",
        "descriptor": "java.descriptor_validate",
        "javac": "java.javac_compile",
        "build": "java.build_project",
        "test": "java.test_project",
        "verify": "java.verify_project",
    }
    if command == "verify":
        for argument, parameter in (
            ("descriptor", "descriptor_enabled"),
            ("javac", "javac_enabled"),
            ("build", "build_enabled"),
            ("tests", "tests_enabled"),
            ("fail_fast", "fail_fast"),
            ("require_tools", "require_tools"),
        ):
            value = getattr(args, argument)
            if value is not None:
                params[parameter] = value
    return _result(
        app.execute_skill(skill_names[command], params, approved=args.approve),
        args.json,
    )


def _javadev_help_text(app: ElyndraApplication) -> str:
    roots = "\n".join(f"- {root}" for root in app.config.allowed_roots)
    return (
        "Skills Java/JVM controladas\n\n"
        "Raíces persistentes actuales:\n"
        f"{roots}\n\n"
        "Inspección sin ejecutar código:\n"
        "  ./scripts/elyndra-dev javadev inspect /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Validaciones individuales:\n"
        "  ./scripts/elyndra-dev javadev descriptor /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev javadev javac /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev javadev build /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev javadev test /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Flujo completo:\n"
        "  ./scripts/elyndra-dev javadev verify /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "No se ejecutan mvnw ni gradlew. Maven y Gradle usan modo offline, "
        "argumentos fijos y nunca instalan herramientas."
    )


def _kotlindev_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.kotlindev_command
    if command == "help":
        return _print(
            {"allowed_roots": [str(root) for root in app.config.allowed_roots]},
            args.json,
            human=_kotlindev_help_text(app),
        )
    if command == "status":
        tools = {
            "kotlin": shutil.which("kotlin") is not None,
            "kotlinc": shutil.which("kotlinc") is not None,
            "maven_global": shutil.which("mvn") is not None,
            "gradle_global": shutil.which("gradle") is not None,
            "wrappers_executed": False,
            "descriptor_internal": True,
            "project_local_detection": True,
        }
        human = "Herramientas Kotlin/JVM:\n" + "\n".join(
            f"- {name}: {'sí' if available else 'no'}" for name, available in tools.items()
        )
        human += (
            "\nMaven y Gradle se ejecutan solo desde binarios globales, "
            "en modo offline y con argumentos fijos."
        )
        return _print(tools, args.json, human=human)
    if command == "history":
        items = app.verification_runs.list_recent(
            toolchain="kotlin",
            project_root=args.path,
            limit=args.limit,
        )
        human = "\n".join(_format_verification_run(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay verificaciones Kotlin guardadas.",
        )
    if command == "report":
        item = app.verification_runs.get(args.run_id)
        if item is None or item.get("toolchain") != "kotlin":
            raise ValueError(f"Verificación Kotlin no encontrada: {args.run_id}")
        return _print(item, args.json, human=_format_verification_run(item, detailed=True))
    if command == "compare":
        comparison = app.verification_runs.compare(
            args.first_run_id,
            args.second_run_id,
        )
        if comparison.get("toolchain") != "kotlin":
            raise ValueError("Los IDs no corresponden a verificaciones Kotlin.")
        return _print(
            comparison,
            args.json,
            human=_format_verification_comparison(comparison),
        )

    params: dict[str, Any] = {"path": str(args.path)}
    if bool(getattr(args, "allow_root_once", False)):
        params["allow_root_once"] = True
        params["authorization_source"] = "cli_allow_root_once"
    for argument in ("build_tool", "jvm_target", "max_files"):
        value = getattr(args, argument, None)
        if value is not None:
            params[argument] = value
    skill_names = {
        "inspect": "kotlin.project_inspect",
        "descriptor": "kotlin.descriptor_validate",
        "kotlinc": "kotlin.kotlinc_compile",
        "build": "kotlin.build_project",
        "test": "kotlin.test_project",
        "verify": "kotlin.verify_project",
    }
    if command == "verify":
        for argument, parameter in (
            ("descriptor", "descriptor_enabled"),
            ("kotlinc", "kotlinc_enabled"),
            ("build", "build_enabled"),
            ("tests", "tests_enabled"),
            ("fail_fast", "fail_fast"),
            ("require_tools", "require_tools"),
        ):
            value = getattr(args, argument)
            if value is not None:
                params[parameter] = value
    return _result(
        app.execute_skill(skill_names[command], params, approved=args.approve),
        args.json,
    )


def _kotlindev_help_text(app: ElyndraApplication) -> str:
    roots = "\n".join(f"- {root}" for root in app.config.allowed_roots)
    return (
        "Skills Kotlin/JVM controladas\n\n"
        "Raíces persistentes actuales:\n"
        f"{roots}\n\n"
        "Inspección sin ejecutar código:\n"
        "  ./scripts/elyndra-dev kotlindev inspect /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Validaciones individuales:\n"
        "  ./scripts/elyndra-dev kotlindev descriptor /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev kotlindev kotlinc /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev kotlindev build /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev kotlindev test /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Flujo completo:\n"
        "  ./scripts/elyndra-dev kotlindev verify /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "No se ejecutan mvnw ni gradlew. Maven y Gradle usan modo offline, "
        "argumentos fijos y nunca instalan herramientas."
    )


def _dotnetdev_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.dotnetdev_command
    if command == "help":
        return _print(
            {"allowed_roots": [str(root) for root in app.config.allowed_roots]},
            args.json,
            human=_dotnetdev_help_text(app),
        )
    if command == "status":
        tools = {
            "dotnet": shutil.which("dotnet") is not None,
            "descriptor_internal": True,
            "restore_executed": False,
            "network_isolation": False,
            "proxy_environment_restricted": True,
            "project_local_detection": True,
        }
        human = "Herramientas C#/.NET:\n" + "\n".join(
            f"- {name}: {'sí' if available else 'no'}" for name, available in tools.items()
        )
        human += (
            "\ndotnet build/test usan --no-restore y artefactos temporales externos. "
            "No se ejecutan restore, tool restore, run, publish ni comandos arbitrarios."
        )
        return _print(tools, args.json, human=human)
    if command == "history":
        items = app.verification_runs.list_recent(
            toolchain="dotnet", project_root=args.path, limit=args.limit
        )
        human = "\n".join(_format_verification_run(item) for item in items)
        return _print(items, args.json, human=human or "No hay verificaciones .NET guardadas.")
    if command == "report":
        item = app.verification_runs.get(args.run_id)
        if item is None or item.get("toolchain") != "dotnet":
            raise ValueError(f"Verificación .NET no encontrada: {args.run_id}")
        return _print(item, args.json, human=_format_verification_run(item, detailed=True))
    if command == "compare":
        comparison = app.verification_runs.compare(args.first_run_id, args.second_run_id)
        if comparison.get("toolchain") != "dotnet":
            raise ValueError("Los IDs no corresponden a verificaciones .NET.")
        return _print(
            comparison,
            args.json,
            human=_format_verification_comparison(comparison),
        )

    params: dict[str, Any] = {"path": str(args.path)}
    if bool(getattr(args, "allow_root_once", False)):
        params["allow_root_once"] = True
        params["authorization_source"] = "cli_allow_root_once"
    for argument in ("configuration", "max_files"):
        value = getattr(args, argument, None)
        if value is not None:
            params[argument] = value
    skill_names = {
        "inspect": "dotnet.project_inspect",
        "descriptor": "dotnet.descriptor_validate",
        "format": "dotnet.format_check",
        "build": "dotnet.build_project",
        "test": "dotnet.test_project",
        "verify": "dotnet.verify_project",
    }
    if command == "verify":
        for argument, parameter in (
            ("descriptor", "descriptor_enabled"),
            ("format", "format_enabled"),
            ("build", "build_enabled"),
            ("tests", "tests_enabled"),
            ("fail_fast", "fail_fast"),
            ("require_tools", "require_tools"),
        ):
            value = getattr(args, argument)
            if value is not None:
                params[parameter] = value
    return _result(
        app.execute_skill(skill_names[command], params, approved=args.approve),
        args.json,
    )


def _dotnetdev_help_text(app: ElyndraApplication) -> str:
    roots = "\n".join(f"- {root}" for root in app.config.allowed_roots)
    return (
        "Skills C#/.NET controladas\n\n"
        "Raíces persistentes actuales:\n"
        f"{roots}\n\n"
        "Inspección sin ejecutar MSBuild:\n"
        "  ./scripts/elyndra-dev dotnetdev inspect /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Validaciones individuales:\n"
        "  ./scripts/elyndra-dev dotnetdev descriptor /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev dotnetdev format /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev dotnetdev build /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev dotnetdev test /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Flujo completo:\n"
        "  ./scripts/elyndra-dev dotnetdev verify /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "No se ejecutan restore, tool restore, run o publish. Build y tests usan "
        "--no-restore y artefactos temporales externos."
    )


def _swiftdev_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.swiftdev_command
    if command == "help":
        return _print(
            {"allowed_roots": [str(root) for root in app.config.allowed_roots]},
            args.json,
            human=_swiftdev_help_text(app),
        )
    if command == "status":
        tools = {
            "swift": shutil.which("swift") is not None,
            "swiftc": shutil.which("swiftc") is not None,
            "swift_format": shutil.which("swift-format") is not None,
            "manifest_internal": True,
            "project_local_detection": True,
            "automatic_resolution": False,
            "network_isolation": False,
            "proxy_environment_restricted": True,
            "automatic_installation": False,
        }
        human = "Herramientas Swift:\n" + "\n".join(
            f"- {name}: {'sí' if available else 'no'}" for name, available in tools.items()
        )
        human += (
            "\nSwiftPM usa resolución automática desactivada y scratch temporal. "
            "No se ejecutan package update, package resolve ni comandos arbitrarios."
        )
        return _print(tools, args.json, human=human)
    if command == "history":
        items = app.verification_runs.list_recent(
            toolchain="swift", project_root=args.path, limit=args.limit
        )
        human = "\n".join(_format_verification_run(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay verificaciones Swift guardadas.",
        )
    if command == "report":
        item = app.verification_runs.get(args.run_id)
        if item is None or item.get("toolchain") != "swift":
            raise ValueError(f"Verificación Swift no encontrada: {args.run_id}")
        return _print(item, args.json, human=_format_verification_run(item, detailed=True))
    if command == "compare":
        comparison = app.verification_runs.compare(args.first_run_id, args.second_run_id)
        if comparison.get("toolchain") != "swift":
            raise ValueError("Los IDs no corresponden a verificaciones Swift.")
        return _print(
            comparison,
            args.json,
            human=_format_verification_comparison(comparison),
        )

    params: dict[str, Any] = {"path": str(args.path)}
    if bool(getattr(args, "allow_root_once", False)):
        params["allow_root_once"] = True
        params["authorization_source"] = "cli_allow_root_once"
    for argument in ("configuration", "max_files"):
        value = getattr(args, argument, None)
        if value is not None:
            params[argument] = value
    skill_names = {
        "inspect": "swift.project_inspect",
        "manifest": "swift.manifest_validate",
        "syntax": "swift.syntax_check",
        "format": "swift.format_check",
        "build": "swift.build_project",
        "test": "swift.test_project",
        "verify": "swift.verify_project",
    }
    if command == "verify":
        for argument, parameter in (
            ("manifest", "manifest_enabled"),
            ("syntax", "syntax_enabled"),
            ("format", "format_enabled"),
            ("build", "build_enabled"),
            ("tests", "tests_enabled"),
            ("fail_fast", "fail_fast"),
            ("require_tools", "require_tools"),
        ):
            value = getattr(args, argument)
            if value is not None:
                params[parameter] = value
    return _result(
        app.execute_skill(skill_names[command], params, approved=args.approve),
        args.json,
    )


def _swiftdev_help_text(app: ElyndraApplication) -> str:
    roots = "\n".join(f"- {root}" for root in app.config.allowed_roots)
    return (
        "Skills Swift controladas\n\n"
        "Raíces persistentes actuales:\n"
        f"{roots}\n\n"
        "Inspección sin ejecutar Package.swift:\n"
        "  ./scripts/elyndra-dev swiftdev inspect /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Validaciones individuales:\n"
        "  ./scripts/elyndra-dev swiftdev manifest /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev swiftdev syntax /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev swiftdev format /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev swiftdev build /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev swiftdev test /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Flujo completo:\n"
        "  ./scripts/elyndra-dev swiftdev verify /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Package.swift se inspecciona como datos. SwiftPM puede ejecutar manifiestos, "
        "plugins y tests solo tras aprobación; no resuelve dependencias automáticamente."
    )


def _dartdev_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.dartdev_command
    if command == "help":
        return _print(
            {"allowed_roots": [str(root) for root in app.config.allowed_roots]},
            args.json,
            human=_dartdev_help_text(app),
        )
    if command == "status":
        tools = {
            "dart": shutil.which("dart") is not None,
            "flutter": shutil.which("flutter") is not None,
            "descriptor_internal": True,
            "project_local_detection": True,
            "automatic_pub_get": False,
            "proxy_environment_restricted": True,
            "network_isolation": False,
            "automatic_installation": False,
        }
        human = "Herramientas Dart/Flutter:\n" + "\n".join(
            f"- {name}: {'sí' if available else 'no'}" for name, available in tools.items()
        )
        human += (
            "\nNo se ejecutan pub get, pub upgrade, run o build. "
            "Analyze y tests usan dependencias ya disponibles localmente."
        )
        return _print(tools, args.json, human=human)
    if command == "history":
        items = app.verification_runs.list_recent(
            toolchain="dart", project_root=args.path, limit=args.limit
        )
        human = "\n".join(_format_verification_run(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay verificaciones Dart/Flutter guardadas.",
        )
    if command == "report":
        item = app.verification_runs.get(args.run_id)
        if item is None or item.get("toolchain") != "dart":
            raise ValueError(f"Verificación Dart/Flutter no encontrada: {args.run_id}")
        return _print(
            item,
            args.json,
            human=_format_verification_run(item, detailed=True),
        )
    if command == "compare":
        comparison = app.verification_runs.compare(args.first_run_id, args.second_run_id)
        if comparison.get("toolchain") != "dart":
            raise ValueError("Los IDs no corresponden a verificaciones Dart/Flutter.")
        return _print(
            comparison,
            args.json,
            human=_format_verification_comparison(comparison),
        )

    params: dict[str, Any] = {"path": str(args.path)}
    if bool(getattr(args, "allow_root_once", False)):
        params["allow_root_once"] = True
        params["authorization_source"] = "cli_allow_root_once"
    for argument in ("test_runner", "max_files"):
        value = getattr(args, argument, None)
        if value is not None:
            params[argument] = value
    skill_names = {
        "inspect": "dart.project_inspect",
        "descriptor": "dart.descriptor_validate",
        "format": "dart.format_check",
        "analyze": "dart.analyze",
        "test": "dart.test_project",
        "flutter-test": "flutter.test_project",
        "verify": "dart.verify_project",
    }
    if command == "verify":
        for argument, parameter in (
            ("descriptor", "descriptor_enabled"),
            ("format", "format_enabled"),
            ("analyze", "analyze_enabled"),
            ("tests", "tests_enabled"),
            ("fail_fast", "fail_fast"),
            ("require_tools", "require_tools"),
        ):
            value = getattr(args, argument)
            if value is not None:
                params[parameter] = value
    return _result(
        app.execute_skill(skill_names[command], params, approved=args.approve),
        args.json,
    )


def _dartdev_help_text(app: ElyndraApplication) -> str:
    roots = "\n".join(f"- {root}" for root in app.config.allowed_roots)
    return (
        "Skills Dart/Flutter controladas\n\n"
        "Raíces persistentes actuales:\n"
        f"{roots}\n\n"
        "Inspección sin ejecutar herramientas:\n"
        "  ./scripts/elyndra-dev dartdev inspect /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Validaciones individuales:\n"
        "  ./scripts/elyndra-dev dartdev descriptor /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev dartdev format /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev dartdev analyze /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev dartdev test /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev dartdev flutter-test /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Flujo completo:\n"
        "  ./scripts/elyndra-dev dartdev verify /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "No se ejecutan pub get, pub upgrade, run, build ni comandos arbitrarios. "
        "El formato nunca modifica archivos y Flutter usa --no-pub."
    )


def _sqldev_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.sqldev_command
    if command == "help":
        return _print(
            {"allowed_roots": [str(root) for root in app.config.allowed_roots]},
            args.json,
            human=_sqldev_help_text(app),
        )
    if command == "status":
        capabilities = {
            "static_parser": True,
            "migration_validation": True,
            "sqlite_readonly": True,
            "sqlite_query_plan": True,
            "ddl_dml_default_allowed": False,
            "migration_execution": False,
            "network_access": False,
            "automatic_installation": False,
        }
        human = "Capacidades SQL/SQLite:\n" + "\n".join(
            f"- {name}: {'sí' if available else 'no'}" for name, available in capabilities.items()
        )
        human += (
            "\nNo se aplican migraciones ni consultas de escritura. "
            "Las bases SQLite se abren con mode=ro y query_only."
        )
        return _print(capabilities, args.json, human=human)
    if command == "history":
        items = app.verification_runs.list_recent(
            toolchain="sql", project_root=args.path, limit=args.limit
        )
        human = "\n".join(_format_verification_run(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay verificaciones SQL guardadas.",
        )
    if command == "report":
        item = app.verification_runs.get(args.run_id)
        if item is None or item.get("toolchain") != "sql":
            raise ValueError(f"Verificación SQL no encontrada: {args.run_id}")
        return _print(
            item,
            args.json,
            human=_format_verification_run(item, detailed=True),
        )
    if command == "compare":
        comparison = app.verification_runs.compare(
            args.first_run_id,
            args.second_run_id,
        )
        if comparison.get("toolchain") != "sql":
            raise ValueError("Los IDs no corresponden a verificaciones SQL.")
        return _print(
            comparison,
            args.json,
            human=_format_verification_comparison(comparison),
        )
    if command == "plan":
        params: dict[str, Any] = {"database": str(args.database)}
        if args.query is not None:
            params["query"] = args.query
        if args.query_file is not None:
            params["query_file"] = str(args.query_file)
        if args.allow_root_once:
            params["allow_root_once"] = True
            params["authorization_source"] = "cli_allow_root_once"
        return _result(
            app.execute_skill("sqlite.query_plan", params, approved=args.approve),
            args.json,
        )

    params = {"path": str(args.path)}
    if bool(getattr(args, "allow_root_once", False)):
        params["allow_root_once"] = True
        params["authorization_source"] = "cli_allow_root_once"
    for argument in ("dialect", "max_files", "max_database_files"):
        value = getattr(args, argument, None)
        if value is not None:
            params[argument] = value
    skill_names = {
        "inspect": "sql.project_inspect",
        "static": "sql.static_validate",
        "migrations": "sql.migration_validate",
        "schema": "sqlite.schema_inspect",
        "verify": "sql.verify_project",
    }
    if command == "verify":
        for argument, parameter in (
            ("static", "static_enabled"),
            ("migrations", "migrations_enabled"),
            ("schema", "schema_enabled"),
            ("allow_mutating_sql", "allow_mutating_sql"),
            (
                "allow_destructive_migrations",
                "allow_destructive_migrations",
            ),
            ("fail_fast", "fail_fast"),
        ):
            value = getattr(args, argument)
            if value is not None:
                params[parameter] = value
    return _result(
        app.execute_skill(skill_names[command], params, approved=args.approve),
        args.json,
    )


def _sqldev_help_text(app: ElyndraApplication) -> str:
    roots = "\n".join(f"- {root}" for root in app.config.allowed_roots)
    return (
        "Skills SQL y SQLite controladas\n\n"
        "Raíces persistentes actuales:\n"
        f"{roots}\n\n"
        "Inspección sin ejecutar consultas:\n"
        "  ./scripts/elyndra-dev sqldev inspect /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Validaciones individuales:\n"
        "  ./scripts/elyndra-dev sqldev static /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev sqldev migrations /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev sqldev schema /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Plan SQLite de solo lectura:\n"
        "  ./scripts/elyndra-dev sqldev plan /ruta/base.sqlite "
        "--query 'SELECT * FROM tabla' --approve --allow-root-once\n\n"
        "Flujo completo:\n"
        "  ./scripts/elyndra-dev sqldev verify /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "No se aplican migraciones ni DDL/DML. SQLite se abre en modo solo "
        "lectura y el plan solo acepta una consulta SELECT o WITH."
    )


def _nativedev_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.nativedev_command
    if command == "help":
        return _print(
            {"allowed_roots": [str(root) for root in app.config.allowed_roots]},
            args.json,
            human=_nativedev_help_text(app),
        )
    if command == "status":
        tools = {
            "gcc": shutil.which("gcc") is not None,
            "gxx": shutil.which("g++") is not None,
            "clang": shutil.which("clang") is not None,
            "clangxx": shutil.which("clang++") is not None,
            "cmake": shutil.which("cmake") is not None,
            "ctest": shutil.which("ctest") is not None,
            "cppcheck": shutil.which("cppcheck") is not None,
            "make_executed": False,
            "meson_executed": False,
            "project_local_detection": True,
        }
        human = "Herramientas C/C++:\n" + "\n".join(
            f"- {name}: {'sí' if available else 'no'}" for name, available in tools.items()
        )
        human += (
            "\nCMake usa un build temporal. Make y Meson solo se detectan; "
            "no se ejecutan automáticamente."
        )
        return _print(tools, args.json, human=human)
    if command == "history":
        items = app.verification_runs.list_recent(
            toolchain="native",
            project_root=args.path,
            limit=args.limit,
        )
        human = "\n".join(_format_verification_run(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay verificaciones C/C++ guardadas.",
        )
    if command == "report":
        item = app.verification_runs.get(args.run_id)
        if item is None or item.get("toolchain") != "native":
            raise ValueError(f"Verificación C/C++ no encontrada: {args.run_id}")
        return _print(item, args.json, human=_format_verification_run(item, detailed=True))
    if command == "compare":
        comparison = app.verification_runs.compare(
            args.first_run_id,
            args.second_run_id,
        )
        if comparison.get("toolchain") != "native":
            raise ValueError("Los IDs no corresponden a verificaciones C/C++.")
        return _print(
            comparison,
            args.json,
            human=_format_verification_comparison(comparison),
        )

    params: dict[str, Any] = {"path": str(args.path)}
    if bool(getattr(args, "allow_root_once", False)):
        params["allow_root_once"] = True
        params["authorization_source"] = "cli_allow_root_once"
    for argument in (
        "compiler",
        "c_standard",
        "cpp_standard",
        "max_files",
    ):
        value = getattr(args, argument, None)
        if value is not None:
            params[argument] = value
    skill_names = {
        "inspect": "native.project_inspect",
        "descriptor": "native.descriptor_validate",
        "c-syntax": "native.c_syntax_check",
        "cpp-syntax": "native.cpp_syntax_check",
        "static": "native.static_analyse",
        "build": "native.build_project",
        "test": "native.test_project",
        "verify": "native.verify_project",
    }
    if command == "verify":
        for argument, parameter in (
            ("descriptor", "descriptor_enabled"),
            ("c_syntax", "c_syntax_enabled"),
            ("cpp_syntax", "cpp_syntax_enabled"),
            ("static", "static_enabled"),
            ("build", "build_enabled"),
            ("tests", "tests_enabled"),
            ("fail_fast", "fail_fast"),
            ("require_tools", "require_tools"),
        ):
            value = getattr(args, argument)
            if value is not None:
                params[parameter] = value
    return _result(
        app.execute_skill(skill_names[command], params, approved=args.approve),
        args.json,
    )


def _nativedev_help_text(app: ElyndraApplication) -> str:
    roots = "\n".join(f"- {root}" for root in app.config.allowed_roots)
    return (
        "Skills C/C++ controladas\n\n"
        "Raíces persistentes actuales:\n"
        f"{roots}\n\n"
        "Inspección sin ejecutar código:\n"
        "  ./scripts/elyndra-dev nativedev inspect /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Validaciones individuales:\n"
        "  ./scripts/elyndra-dev nativedev descriptor /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev nativedev c-syntax /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev nativedev cpp-syntax /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev nativedev static /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev nativedev build /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev nativedev test /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Flujo completo:\n"
        "  ./scripts/elyndra-dev nativedev verify /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "No se ejecutan make ni Meson. CMake compila en una carpeta temporal, "
        "sin shell y con argumentos fijos."
    )


def _rubydev_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.rubydev_command
    if command == "help":
        return _print(
            {"allowed_roots": [str(root) for root in app.config.allowed_roots]},
            args.json,
            human=_rubydev_help_text(app),
        )
    if command == "status":
        tools = {
            "ruby": shutil.which("ruby") is not None,
            "bundle_global": shutil.which("bundle") is not None,
            "rubocop_global": shutil.which("rubocop") is not None,
            "rspec_global": shutil.which("rspec") is not None,
            "project_local_detection": True,
            "bundle_install_executed": False,
        }
        human = "Herramientas Ruby:\n" + "\n".join(
            f"- {name}: {'sí' if available else 'no'}" for name, available in tools.items()
        )
        human += (
            "\nLos binarios locales se priorizan. bundle install, rake y scripts "
            "arbitrarios no se ejecutan automáticamente."
        )
        return _print(tools, args.json, human=human)
    if command == "history":
        items = app.verification_runs.list_recent(
            toolchain="ruby",
            project_root=args.path,
            limit=args.limit,
        )
        human = "\n".join(_format_verification_run(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay verificaciones Ruby guardadas.",
        )
    if command == "report":
        item = app.verification_runs.get(args.run_id)
        if item is None or item.get("toolchain") != "ruby":
            raise ValueError(f"Verificación Ruby no encontrada: {args.run_id}")
        return _print(item, args.json, human=_format_verification_run(item, detailed=True))
    if command == "compare":
        comparison = app.verification_runs.compare(
            args.first_run_id,
            args.second_run_id,
        )
        if comparison.get("toolchain") != "ruby":
            raise ValueError("Los IDs no corresponden a verificaciones Ruby.")
        return _print(
            comparison,
            args.json,
            human=_format_verification_comparison(comparison),
        )

    params: dict[str, Any] = {"path": str(args.path)}
    if bool(getattr(args, "allow_root_once", False)):
        params["allow_root_once"] = True
        params["authorization_source"] = "cli_allow_root_once"
    for argument in ("test_framework", "max_files"):
        value = getattr(args, argument, None)
        if value is not None:
            params[argument] = value
    skill_names = {
        "inspect": "ruby.project_inspect",
        "descriptor": "ruby.descriptor_validate",
        "bundle": "ruby.bundle_check",
        "syntax": "ruby.syntax_check",
        "rubocop": "rubocop.check",
        "test": "ruby.test_project",
        "verify": "ruby.verify_project",
    }
    if command == "verify":
        for argument, parameter in (
            ("descriptor", "descriptor_enabled"),
            ("bundle", "bundle_enabled"),
            ("syntax", "syntax_enabled"),
            ("rubocop", "rubocop_enabled"),
            ("tests", "tests_enabled"),
            ("fail_fast", "fail_fast"),
            ("require_tools", "require_tools"),
        ):
            value = getattr(args, argument)
            if value is not None:
                params[parameter] = value
    return _result(
        app.execute_skill(skill_names[command], params, approved=args.approve),
        args.json,
    )


def _rubydev_help_text(app: ElyndraApplication) -> str:
    roots = "\n".join(f"- {root}" for root in app.config.allowed_roots)
    return (
        "Skills Ruby controladas\n\n"
        "Raíces persistentes actuales:\n"
        f"{roots}\n\n"
        "Inspección sin ejecutar código:\n"
        "  ./scripts/elyndra-dev rubydev inspect /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Validaciones individuales:\n"
        "  ./scripts/elyndra-dev rubydev descriptor /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev rubydev bundle /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev rubydev syntax /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev rubydev rubocop /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev rubydev test /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Flujo completo:\n"
        "  ./scripts/elyndra-dev rubydev verify /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "No se ejecutan bundle install, rake ni comandos arbitrarios. "
        "RuboCop nunca aplica autocorrecciones."
    )


def _godev_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.godev_command
    if command == "help":
        return _print(
            {"allowed_roots": [str(root) for root in app.config.allowed_roots]},
            args.json,
            human=_godev_help_text(app),
        )
    if command == "status":
        tools = {
            "go": shutil.which("go") is not None,
            "gofmt": shutil.which("gofmt") is not None,
            "project_local_detection": True,
            "network_allowed": False,
            "automatic_installation": False,
        }
        human = "Herramientas Go:\n" + "\n".join(
            f"- {name}: {'sí' if available else 'no'}" for name, available in tools.items()
        )
        human += (
            "\nGo usa GOPROXY=off, GOSUMDB=off, GOTOOLCHAIN=local y "
            "-mod=readonly. No se ejecutan go get, go install ni go generate."
        )
        return _print(tools, args.json, human=human)
    if command == "history":
        items = app.verification_runs.list_recent(
            toolchain="go",
            project_root=args.path,
            limit=args.limit,
        )
        human = "\n".join(_format_verification_run(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay verificaciones Go guardadas.",
        )
    if command == "report":
        item = app.verification_runs.get(args.run_id)
        if item is None or item.get("toolchain") != "go":
            raise ValueError(f"Verificación Go no encontrada: {args.run_id}")
        return _print(
            item,
            args.json,
            human=_format_verification_run(item, detailed=True),
        )
    if command == "compare":
        comparison = app.verification_runs.compare(
            args.first_run_id,
            args.second_run_id,
        )
        if comparison.get("toolchain") != "go":
            raise ValueError("Los IDs no corresponden a verificaciones Go.")
        return _print(
            comparison,
            args.json,
            human=_format_verification_comparison(comparison),
        )

    params: dict[str, Any] = {"path": str(args.path)}
    if bool(getattr(args, "allow_root_once", False)):
        params["allow_root_once"] = True
        params["authorization_source"] = "cli_allow_root_once"
    for argument in ("test_mode", "max_files"):
        value = getattr(args, argument, None)
        if value is not None:
            params[argument] = value
    skill_names = {
        "inspect": "go.project_inspect",
        "module": "go.module_validate",
        "fmt": "gofmt.check",
        "vet": "go.vet",
        "build": "go.build_project",
        "test": "go.test_project",
        "verify": "go.verify_project",
    }
    if command == "verify":
        for argument, parameter in (
            ("module", "module_enabled"),
            ("fmt", "fmt_enabled"),
            ("vet", "vet_enabled"),
            ("build", "build_enabled"),
            ("tests", "tests_enabled"),
            ("fail_fast", "fail_fast"),
            ("require_tools", "require_tools"),
        ):
            value = getattr(args, argument)
            if value is not None:
                params[parameter] = value
    return _result(
        app.execute_skill(skill_names[command], params, approved=args.approve),
        args.json,
    )


def _godev_help_text(app: ElyndraApplication) -> str:
    roots = "\n".join(f"- {root}" for root in app.config.allowed_roots)
    return (
        "Skills Go controladas\n\n"
        "Raíces persistentes actuales:\n"
        f"{roots}\n\n"
        "Inspección sin ejecutar herramientas:\n"
        "  ./scripts/elyndra-dev godev inspect /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Validaciones individuales:\n"
        "  ./scripts/elyndra-dev godev module /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev godev fmt /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev godev vet /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev godev build /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev godev test /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Flujo completo:\n"
        "  ./scripts/elyndra-dev godev verify /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "No se ejecutan go get, go install, go generate ni comandos arbitrarios. "
        "La red queda desactivada y gofmt nunca modifica archivos."
    )


def _rustdev_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = args.rustdev_command
    if command == "help":
        return _print(
            {"allowed_roots": [str(root) for root in app.config.allowed_roots]},
            args.json,
            human=_rustdev_help_text(app),
        )
    if command == "status":
        tools = {
            "cargo": shutil.which("cargo") is not None,
            "rustc": shutil.which("rustc") is not None,
            "rustfmt": shutil.which("rustfmt") is not None,
            "clippy_component": shutil.which("cargo-clippy") is not None,
            "project_local_detection": True,
            "network_allowed": False,
            "automatic_installation": False,
        }
        human = "Herramientas Rust:\n" + "\n".join(
            f"- {name}: {'sí' if available else 'no'}" for name, available in tools.items()
        )
        human += (
            "\nCargo usa --offline y --locked con target temporal. "
            "No se ejecutan cargo install, update, fix ni comandos arbitrarios."
        )
        return _print(tools, args.json, human=human)
    if command == "history":
        items = app.verification_runs.list_recent(
            toolchain="rust",
            project_root=args.path,
            limit=args.limit,
        )
        human = "\n".join(_format_verification_run(item) for item in items)
        return _print(
            items,
            args.json,
            human=human or "No hay verificaciones Rust guardadas.",
        )
    if command == "report":
        item = app.verification_runs.get(args.run_id)
        if item is None or item.get("toolchain") != "rust":
            raise ValueError(f"Verificación Rust no encontrada: {args.run_id}")
        return _print(
            item,
            args.json,
            human=_format_verification_run(item, detailed=True),
        )
    if command == "compare":
        comparison = app.verification_runs.compare(
            args.first_run_id,
            args.second_run_id,
        )
        if comparison.get("toolchain") != "rust":
            raise ValueError("Los IDs no corresponden a verificaciones Rust.")
        return _print(
            comparison,
            args.json,
            human=_format_verification_comparison(comparison),
        )

    params: dict[str, Any] = {"path": str(args.path)}
    if bool(getattr(args, "allow_root_once", False)):
        params["allow_root_once"] = True
        params["authorization_source"] = "cli_allow_root_once"
    for argument in ("feature_mode", "max_files"):
        value = getattr(args, argument, None)
        if value is not None:
            params[argument] = value
    skill_names = {
        "inspect": "rust.project_inspect",
        "manifest": "rust.manifest_validate",
        "fmt": "rustfmt.check",
        "check": "cargo.check",
        "clippy": "cargo.clippy",
        "test": "cargo.test_project",
        "verify": "rust.verify_project",
    }
    if command == "verify":
        for argument, parameter in (
            ("manifest", "manifest_enabled"),
            ("fmt", "fmt_enabled"),
            ("check", "check_enabled"),
            ("clippy", "clippy_enabled"),
            ("tests", "tests_enabled"),
            ("fail_fast", "fail_fast"),
            ("require_tools", "require_tools"),
        ):
            value = getattr(args, argument)
            if value is not None:
                params[parameter] = value
    return _result(
        app.execute_skill(skill_names[command], params, approved=args.approve),
        args.json,
    )


def _rustdev_help_text(app: ElyndraApplication) -> str:
    roots = "\n".join(f"- {root}" for root in app.config.allowed_roots)
    return (
        "Skills Rust controladas\n\n"
        "Raíces persistentes actuales:\n"
        f"{roots}\n\n"
        "Inspección sin ejecutar herramientas:\n"
        "  ./scripts/elyndra-dev rustdev inspect /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Validaciones individuales:\n"
        "  ./scripts/elyndra-dev rustdev manifest /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev rustdev fmt /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev rustdev check /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev rustdev clippy /ruta/proyecto --approve "
        "--allow-root-once\n"
        "  ./scripts/elyndra-dev rustdev test /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Flujo completo:\n"
        "  ./scripts/elyndra-dev rustdev verify /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Cargo usa --offline y --locked; target se crea fuera del proyecto. "
        "No se instalan toolchains, crates ni componentes automáticamente."
    )


def _skill_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    if args.skill_command == "help":
        return _print(
            {"allowed_roots": [str(root) for root in app.config.allowed_roots]},
            args.json,
            human=_skill_help_text(app),
        )
    if args.skill_command == "list":
        skills = [
            {"name": skill.name, "risk": skill.risk.value, "description": skill.description}
            for skill in app.skills.list_all()
        ]
        human = "\n".join(
            f"{item['name']} [{item['risk']}] — {item['description']}" for item in skills
        )
        return _print(skills, args.json, human=human)
    if args.skill_command == "inspect":
        return _result(app.inspect_skill(args.name), args.json)
    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--params no es JSON válido: {exc}") from exc
    if not isinstance(params, dict):
        raise ValueError("--params debe ser un objeto JSON.")
    if args.skill_command == "plan":
        return _result(app.plan_skill(args.name, params), args.json)
    return _result(app.execute_skill(args.name, params, approved=args.approve), args.json)


def _php_help_text(app: ElyndraApplication) -> str:
    roots = "\n".join(f"- {root}" for root in app.config.allowed_roots)
    return (
        "Skills PHP controladas\n\n"
        "Raíces persistentes actuales:\n"
        f"{roots}\n\n"
        "Archivo individual fuera de Proyectos:\n"
        "  ./scripts/elyndra-dev php syntax /ruta/archivo.php --approve\n\n"
        "Inspección sin ejecutar código:\n"
        "  ./scripts/elyndra-dev php inspect /ruta/proyecto --approve --allow-root-once\n\n"
        "Validación sintáctica completa:\n"
        "  ./scripts/elyndra-dev php syntax-project /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Flujo completo Composer + sintaxis + PHPStan + PHPUnit:\n"
        "  ./scripts/elyndra-dev php verify /ruta/proyecto --approve "
        "--allow-root-once\n\n"
        "Historial y comparación:\n"
        "  ./scripts/elyndra-dev php history /ruta/proyecto\n"
        "  ./scripts/elyndra-dev php report ID\n"
        "  ./scripts/elyndra-dev php compare ID_ANTERIOR ID_NUEVO\n\n"
        "Los perfiles pueden activar etapas, fail-fast, herramientas obligatorias, "
        "límite de archivos y exclusiones. No se ejecutan scripts arbitrarios ni se "
        "instalan herramientas automáticamente."
    )


def _skill_help_text(app: ElyndraApplication) -> str:
    roots = ", ".join(str(root) for root in app.config.allowed_roots)
    return (
        "Comandos de skills:\n"
        "  ./scripts/elyndra-dev skill list\n"
        "  ./scripts/elyndra-dev skill inspect NOMBRE\n"
        "  ./scripts/elyndra-dev skill plan NOMBRE --params '{}'\n"
        "  ./scripts/elyndra-dev skill run NOMBRE --params '{}' --approve\n"
        "  ./scripts/elyndra-dev project trusted\n"
        "  ./scripts/elyndra-dev audit list --action skill.execute\n"
        "  ./scripts/elyndra-dev php help\n\n"
        f"Raíces persistentes: {roots}\n"
        "php.syntax_validate concede alcance single_file. Composer, PHPStan y PHPUnit "
        "usan project_persistent dentro de raíces configuradas o confiables y requieren "
        "project_once explícito fuera de ellas."
    )


def _format_audit_event(event: dict[str, Any]) -> str:
    details = str(event.get("details_json") or "{}")
    return (
        f"Evento #{event['id']}\n"
        f"Fecha: {event['created_at']}\n"
        f"Acción: {event['action']}\n"
        f"Objetivo: {event.get('target') or '-'}\n"
        f"Resultado: {event['outcome']}\n"
        f"Detalles: {details}"
    )


def _chat_command(app: ElyndraApplication, args: argparse.Namespace) -> int:
    command = getattr(args, "chat_command", None)
    if command in {None, "new"}:
        chat = app.chats.create(
            title=getattr(args, "title", None),
            project=getattr(args, "project", None),
            transcript_mode=getattr(args, "transcript", "summary"),
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="chat.create",
            target=str(chat["public_id"]),
            outcome="success",
            details={
                "title": chat["title"],
                "project": chat["project"],
                "transcript_mode": chat["transcript_mode"],
            },
        )
        return _chat(app, chat)
    if command == "open":
        chat = app.chats.touch(args.id)
        app.audit.record(
            actor=app.identity.system_user,
            action="chat.open",
            target=str(chat["public_id"]),
            outcome="success",
        )
        return _chat(app, chat)
    if command == "list":
        chats = app.chats.list_active(args.limit)
        return _print(chats, args.json, human=_format_chats(chats))
    if command == "show":
        chat = app.chats.get(args.id)
        if chat is None:
            raise ValueError(f"Chat no encontrado: {args.id}")
        recent = app.chats.recent_turns(args.id, limit=3)
        memory_state = app.memory_lifecycle.summary_data(args.id)
        episodes = app.memory_lifecycle.list_episodes(chat=args.id, limit=10)
        data = {
            **chat,
            "summary": app.chat_summary(args.id),
            "memory_state": memory_state,
            "episodes": episodes,
            "recent_turns": recent,
        }
        return _print(data, args.json, human=_format_chat_detail(data))
    if command == "search":
        chats = app.chats.search(args.query, args.limit)
        return _print(chats, args.json, human=_format_chats(chats))
    if command == "archives":
        archives = app.memory_lifecycle.list_archives(args.limit)
        return _print(archives, args.json, human=_format_archives(archives))
    if command == "archive":
        archive = app.memory_lifecycle.archive_chat(
            args.id,
            transcripts_dir=app.paths.transcripts_dir,
            prune=args.prune,
        )
        app.audit.record(
            actor=app.identity.system_user,
            action="chat.archive",
            target=str(args.id),
            outcome="success",
            details={
                "path": archive["path"],
                "turn_count": archive["turn_count"],
                "pruned": archive["pruned"],
            },
        )
        human = (
            f"Transcripción archivada: {archive['path']}\n"
            f"Turnos: {archive['turn_count']}; tamaño: {archive['size_bytes']} bytes; "
            f"pruned={archive['pruned']}."
        )
        return _print(archive, args.json, human=human)

    forgotten = app.chats.forget(args.id)
    app.audit.record(
        actor=app.identity.system_user,
        action="chat.forget",
        target=str(args.id),
        outcome="success" if forgotten else "not_found",
    )
    return _print(
        {"forgotten": forgotten, "id": args.id},
        args.json,
        human="Chat eliminado lógicamente." if forgotten else "Chat no encontrado.",
    )


def _chat(app: ElyndraApplication, chat: dict[str, Any]) -> int:
    public_id = str(chat["public_id"])
    session_summary = app.chat_summary(public_id)
    history = [
        ConversationTurn(user=item["user_text"], assistant=item["assistant_text"])
        for item in app.chats.recent_turns(public_id, limit=3)
    ]
    print(
        f"{app.persona.agent_name} — Elyndra {__version__} "
        f"(offline, motor {app.language_engine.name})\n"
        f"Chat: {public_id} · {chat['title']} · transcripción={chat['transcript_mode']}\n"
        "Escribe /help para ayuda. Para salir usa /exit, exit o salir."
    )
    try:
        while True:
            try:
                text = input("\nTú > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nSesión cerrada.")
                return 0
            if not text:
                continue
            if is_exit_command(text):
                print("Sesión cerrada.")
                return 0
            folded = text.casefold()
            if folded in {"/help", "/ayuda"}:
                print(_chat_help())
                continue
            if folded == "/skills":
                for skill in app.skills.list_all():
                    print(f"{skill.name} [{skill.risk.value}] — {skill.description}")
                continue
            if folded == "/projects":
                print(_format_projects(app.projects.list_all()))
                continue
            if folded == "/memories":
                print(_format_memories(app.memories.list_active(20)))
                continue
            if folded == "/knowledge":
                print(_format_documents(app.knowledge.list_active(20)))
                continue
            if folded == "/status":
                print(f"{app.persona.agent_name} > {app.execute_skill('system.status').message}")
                continue
            if folded == "/language":
                config = LanguageConfig.load(app.paths)
                print(
                    f"Modo: {config.interaction_mode}; idioma preferido: "
                    f"{language_name(config.preferred_language)} "
                    f"({config.preferred_language})."
                )
                continue
            if folded in {"/persona", "/identity"}:
                print(_format_persona_summary(app.persona))
                continue
            if folded == "/personality":
                print(_format_personality(app.persona))
                continue
            if folded in {"/clear", "/limpiar"}:
                history.clear()
                print(
                    "Memoria activa de esta ejecución eliminada. "
                    "El resumen persistente del chat permanece en disco."
                )
                continue
            if folded == "/model":
                config, error = _language_config_snapshot(app.paths)
                data = _language_config_data(config, app.paths.language_config_file, error=error)
                print(_format_language_config(data))
                continue
            if folded == "/chat":
                current = app.chats.get(public_id)
                assert current is not None
                print(_format_chat_detail(current))
                continue
            if folded == "/summary":
                session_summary = app.chat_summary(public_id)
                print(session_summary or "Este chat todavía no tiene resumen persistente.")
                continue
            if folded == "/memory":
                state = app.memory_lifecycle.summary_data(public_id)
                episodes = app.memory_lifecycle.list_episodes(chat=public_id, limit=10)
                proposals = app.memory_lifecycle.list_proposals(status="pending", limit=10)
                print(_format_memory_state(state, episodes, proposals))
                continue
            if folded.startswith("/rename "):
                chat = app.chats.rename(public_id, text[len("/rename ") :])
                print(f"Chat renombrado: {chat['title']}")
                continue
            if folded.startswith("/transcript "):
                mode = text[len("/transcript ") :].strip().casefold()
                chat = app.chats.set_transcript_mode(public_id, mode)
                print(f"Modo de transcripción: {chat['transcript_mode']}")
                continue
            if folded.startswith("/correct "):
                corrected = text[len("/correct ") :].strip()
                if not history:
                    print("No hay una respuesta reciente que corregir.")
                    continue
                previous = history[-1]
                correction_id = app.memory_lifecycle.add_correction(
                    public_id,
                    user_text=previous.user,
                    original_response=previous.assistant,
                    corrected_response=corrected,
                )
                history[-1] = ConversationTurn(
                    user=previous.user,
                    assistant=corrected,
                )
                session_summary = app.chat_summary(public_id)
                print(
                    f"Corrección #{correction_id} guardada para revisión y aprendizaje. "
                    "No modifica automáticamente los pesos del modelo."
                )
                continue

            hint = command_hint(text)
            if hint:
                print(f"{app.persona.agent_name} > {hint}")
                continue

            approved = False
            if folded.startswith("/approve "):
                text = text[len("/approve ") :].strip()
                approved = True

            history_snapshot = tuple(history)
            summary_snapshot = session_summary
            result, elapsed = run_with_progress(
                lambda current_text=text,
                current_approved=approved,
                current_history=history_snapshot,
                current_summary=summary_snapshot: (
                    app.ask(
                        current_text,
                        approved=current_approved,
                        history=current_history,
                        interactive=True,
                        session_summary=current_summary,
                        chat_id=public_id,
                    )
                ),
                label="Procesando",
                speaker=app.persona.agent_name,
            )
            if not result.ok and result.data.get("risk") == "medium" and not approved:
                try:
                    prompt = "La acción requiere aprobación única. ¿Aprobar? [s/N] "
                    answer = input(prompt).strip().casefold()
                except (EOFError, KeyboardInterrupt):
                    answer = ""
                if answer in {"s", "si", "sí", "y", "yes"}:
                    approval_history_snapshot = tuple(history)
                    approval_summary_snapshot = session_summary
                    result, extra = run_with_progress(
                        lambda current_text=text,
                        current_history=approval_history_snapshot,
                        current_summary=approval_summary_snapshot: (
                            app.ask(
                                current_text,
                                approved=True,
                                history=current_history,
                                interactive=True,
                                session_summary=current_summary,
                                chat_id=public_id,
                            )
                        ),
                        label="Ejecutando",
                        speaker=app.persona.agent_name,
                    )
                    elapsed += extra
            print(f"{app.persona.agent_name} > {result.message}")
            print(f"⏱ {format_duration(elapsed)}")
            if result.ok:
                previous_turns = int(chat["turn_count"])
                chat = app.record_chat_turn(
                    public_id,
                    user_text=text,
                    assistant_text=result.message,
                )
                if previous_turns == 0 and str(chat["title"]).startswith("Conversación "):
                    chat = app.chats.rename(public_id, text[:80])
                session_summary = app.chat_summary(public_id)
                history.append(ConversationTurn(user=text, assistant=result.message))
                del history[:-6]
    finally:
        app.release_language_engine()


def _chat_help() -> str:
    return (
        "Comandos de sesión:\n"
        "  /chat         Identificador, título y modo del chat actual\n"
        "  /summary      Resumen persistente estructurado en SQLite\n"
        "  /memory       Temas, decisiones, pendientes y episodios\n"
        "  /correct ...  Corregir la última respuesta sin reentrenar automáticamente\n"
        "  /rename ...   Cambiar el título del chat\n"
        "  /transcript summary|full  Elegir qué se conserva en disco\n"
        "  /status       Estado del equipo\n"
        "  /projects     Proyectos registrados\n"
        "  /memories     Recuerdos semánticos activos\n"
        "  /knowledge    Documentos importados\n"
        "  /skills       Capacidades disponibles\n"
        "  /model        Estado del motor lingüístico\n"
        "  /language     Estado del idioma de interacción\n"
        "  /persona      Identidad canónica activa\n"
        "  /identity     Alias de /persona\n"
        "  /personality  Personalidad, género, pronombres y estilo\n"
        "  /clear        Borrar solo la memoria activa de esta ejecución\n"
        "  /approve ...  Ejecutar una orden aprobando riesgo medio\n"
        "  /exit         Cerrar sesión (también: exit, salir, quit)\n\n"
        "Fuera de la sesión:\n"
        "  elyndra chat list\n"
        "  elyndra chat open CHAT_ID\n"
        "  elyndra chat search TEXTO\n"
        "  elyndra chat archive CHAT_ID --prune --approve\n"
        "  elyndra memory episodes\n"
        "  elyndra memory proposals\n"
        "  elyndra chat forget CHAT_ID --approve"
    )


def _format_chats(chats: list[dict[str, Any]]) -> str:
    if not chats:
        return "No hay chats activos."
    return "\n".join(
        f"{item['public_id']} · {item['title']} · {item['turn_count']} turnos · "
        f"{item['transcript_mode']} · {item['updated_at']}"
        for item in chats
    )


def _format_chat_detail(chat: dict[str, Any]) -> str:
    summary = str(chat.get("summary", "")).strip() or "(sin resumen todavía)"
    lines = [
        f"Chat: {chat['public_id']}",
        f"Título: {chat['title']}",
        f"Proyecto: {chat.get('project') or '(ninguno)'}",
        f"Estado: {chat['status']}",
        f"Transcripción: {chat['transcript_mode']}",
        f"Turnos: {chat['turn_count']}",
        f"Actualizado: {chat['updated_at']}",
        "Resumen:",
        summary,
    ]
    episodes = chat.get("episodes")
    if isinstance(episodes, list) and episodes:
        lines.append("Episodios recientes:")
        for item in episodes:
            lines.append(f"- #{item['id']} [{item['kind']}] {item['content']}")
    recent = chat.get("recent_turns")
    if isinstance(recent, list) and recent:
        lines.append("Turnos completos recientes:")
        for item in recent:
            lines.append(
                f"- #{item['turn_index']} Tú: {item['user_text']} | Elyn: {item['assistant_text']}"
            )
    return "\n".join(lines)


def _persona_setup(current: AgentPersona) -> AgentPersona:
    print("Configuración de identidad. Pulsa Enter para conservar el valor actual.")

    def ask(label: str, value: str) -> str:
        entered = input(f"{label} [{value}]: ").strip()
        return entered or value

    return AgentPersona(
        agent_name=ask("Nombre del agente", current.agent_name),
        project_name=current.project_name,
        owner_name=current.owner_name,
        role=ask("Rol", current.role),
        mission=ask("Misión", current.mission),
        principles=current.principles,
        boundaries=current.boundaries,
        gender_identity=ask("Identidad de género", current.gender_identity),
        pronouns=ask("Pronombres", current.pronouns),
        personality=ask("Personalidad", current.personality),
        tone=ask("Tono", current.tone),
        formality=ask("Formalidad", current.formality),
        verbosity=ask("Nivel de detalle", current.verbosity),
        follow_up_style=ask("Seguimiento", current.follow_up_style),
        source="config",
    )


def _format_persona_summary(persona: AgentPersona) -> str:
    return (
        f"{persona.agent_name} · {persona.role}\n"
        f"Misión: {persona.mission}\n"
        f"Identidad de género: {persona.gender_identity}; pronombres: {persona.pronouns}\n"
        f"Fuente: {persona.source}"
    )


def _format_personality(persona: AgentPersona) -> str:
    return (
        f"Personalidad: {persona.personality}\n"
        f"Tono: {persona.tone}; formalidad: {persona.formality}; "
        f"detalle: {persona.verbosity}\n"
        f"Seguimiento: {persona.follow_up_style}\n"
        "Para cambiarla ejecuta: elyndra persona setup"
    )


def _doctor(app: ElyndraApplication, json_output: bool) -> int:
    roots = [
        {"path": str(root), "exists": root.exists(), "writable": root.exists() and root.is_dir()}
        for root in app.config.allowed_roots
    ]
    tools = {
        name: bool(shutil.which(name))
        for name in ("git", "code", "php", "node", "llama-cli", "llama-server", "ollama")
    }
    language_config, language_error = _language_config_snapshot(app.paths)
    ethics = ethics_status(
        proactive_advice=app.config.ethical_advice_enabled,
        tutor_review=app.config.ethical_tutor_review_enabled,
    )
    tutor_status = app.tutor_status()
    document_tools = document_capabilities()
    php_tools = php_tool_capabilities()
    web_tools = {
        "html_internal": True,
        "css_internal": True,
        "node": bool(shutil.which("node")),
        "typescript_global": bool(shutil.which("tsc")),
        "project_local_detection": True,
    }
    python_tools = {
        "python": bool(sys.executable),
        "compile_internal": True,
        "pyproject_internal": True,
        "ruff_global": bool(shutil.which("ruff")),
        "mypy_global": bool(shutil.which("mypy")),
        "pytest_global": bool(shutil.which("pytest")),
        "project_local_detection": True,
    }
    java_tools = {
        "java": bool(shutil.which("java")),
        "javac": bool(shutil.which("javac")),
        "maven_global": bool(shutil.which("mvn")),
        "gradle_global": bool(shutil.which("gradle")),
        "descriptor_internal": True,
        "wrappers_executed": False,
        "project_local_detection": True,
    }
    kotlin_tools = {
        "kotlin": bool(shutil.which("kotlin")),
        "kotlinc": bool(shutil.which("kotlinc")),
        "maven_global": bool(shutil.which("mvn")),
        "gradle_global": bool(shutil.which("gradle")),
        "descriptor_internal": True,
        "wrappers_executed": False,
        "project_local_detection": True,
    }
    dotnet_tools = {
        "dotnet": bool(shutil.which("dotnet")),
        "descriptor_internal": True,
        "project_local_detection": True,
        "restore_executed": False,
        "network_isolation": False,
        "proxy_environment_restricted": True,
        "automatic_installation": False,
    }
    native_tools = {
        "gcc": bool(shutil.which("gcc")),
        "gxx": bool(shutil.which("g++")),
        "clang": bool(shutil.which("clang")),
        "clangxx": bool(shutil.which("clang++")),
        "cmake": bool(shutil.which("cmake")),
        "ctest": bool(shutil.which("ctest")),
        "cppcheck": bool(shutil.which("cppcheck")),
        "descriptor_internal": True,
        "make_executed": False,
        "meson_executed": False,
        "project_local_detection": True,
    }
    ruby_tools = {
        "ruby": bool(shutil.which("ruby")),
        "bundle_global": bool(shutil.which("bundle")),
        "rubocop_global": bool(shutil.which("rubocop")),
        "rspec_global": bool(shutil.which("rspec")),
        "project_local_detection": True,
        "bundle_install_executed": False,
    }
    go_tools = {
        "go": bool(shutil.which("go")),
        "gofmt": bool(shutil.which("gofmt")),
        "project_local_detection": True,
        "network_allowed": False,
        "automatic_installation": False,
    }
    swift_tools = {
        "swift": bool(shutil.which("swift")),
        "swiftc": bool(shutil.which("swiftc")),
        "swift_format": bool(shutil.which("swift-format")),
        "manifest_internal": True,
        "project_local_detection": True,
        "automatic_resolution": False,
        "network_isolation": False,
        "proxy_environment_restricted": True,
        "automatic_installation": False,
    }
    dart_tools = {
        "dart": bool(shutil.which("dart")),
        "flutter": bool(shutil.which("flutter")),
        "descriptor_internal": True,
        "project_local_detection": True,
        "automatic_pub_get": False,
        "proxy_environment_restricted": True,
        "network_isolation": False,
        "automatic_installation": False,
    }
    sql_tools = {
        "static_parser": True,
        "migration_validation": True,
        "sqlite_readonly": True,
        "sqlite_query_plan": True,
        "ddl_dml_default_allowed": False,
        "migration_execution": False,
        "network_access": False,
    }
    assistant_orchestration = {
        "enabled": True,
        "max_steps": 4,
        "allowlisted_skills": len(app.action_planner.allowed_skills),
        "model_planning": not app.language_engine.name.startswith("no-model"),
        "single_use_approval": True,
        "file_writes": True,
        "write_mode": "exact_reviewed_replacements",
        "file_deletes": False,
        "file_renames": False,
        "directory_creation": False,
        "change_proposals": app.change_proposals.count(),
        "pending_change_proposals": app.change_proposals.count(status="proposed"),
        "validation_cycles": app.validation_cycles.count(),
        "development_sessions": app.development_sessions.count(),
        "active_development_sessions": app.development_sessions.count(status="active"),
        "conversational_session_continuity": True,
        "automatic_next_actions": False,
        "automatic_repair_loops": False,
        "repair_requires_new_approval": True,
        "constitutional_ethics": True,
        "ethics_core_disableable": False,
        "proactive_advice": app.config.ethical_advice_enabled,
        "ethical_tutor_review": app.config.ethical_tutor_review_enabled,
        "dictionary_lookup": True,
        "dictionary_model_required": False,
        "local_translation": True,
        "translation_model_fallback_only": True,
        "structured_language_packs": True,
        "structured_first_aid_packs": True,
        "structured_pack_auto_download": False,
        "first_aid_lookup": True,
        "first_aid_model_required": False,
        "memory_tiers": True,
        "reviewed_preference_learning": True,
        "silent_preference_learning": False,
        "tutor_arbitration": True,
        "tutor_benchmarking": True,
        "reviewed_tutor_learning": True,
        "silent_tutor_learning": False,
        "supervised_tutor_evaluation": True,
        "advisory_tutor_auditors": True,
        "versioned_durable_knowledge": True,
        "durable_knowledge_deletion": False,
        "automatic_knowledge_promotion": False,
        "supervised_general_knowledge_acquisition": True,
        "general_knowledge_deletion": False,
        "general_knowledge_model_fallback": True,
        "qualitative_confidence_normalization": True,
        "failed_knowledge_plan_retry": True,
        "knowledge_conflict_review": True,
        "non_destructive_knowledge_revalidation": True,
        "immutable_approved_knowledge_metadata": True,
        "multisource_knowledge_evidence": True,
        "cross_auditor_knowledge_review": True,
        "domain_project_knowledge_scoping": True,
        "cognitive_executive": True,
        "budgeted_context_assembly": True,
        "multidimensional_decision_confidence": True,
        "persistent_goals_and_tasks": True,
        "outcome_verification": True,
        "personal_organizer": True,
        "deterministic_daily_brief": True,
        "personal_wellbeing": True,
        "reviewed_coaching": True,
        "web_cli_parity": True,
        "policy_bounded_automation": True,
        "foreground_automation_dispatch": True,
        "standing_policy_execution": True,
        "external_automation_actions": False,
        "background_reminders": False,
        "local_account_identity": True,
        "isolated_multi_account_vaults": True,
        "stabilized_web_interface": True,
        "lazy_chat_persistence": True,
        "online_gateway_available": False,
        "argon2id_passwords": True,
        "encrypted_local_export": True,
        "remote_account_backup": False,
        "two_factor_available": True,
        "dialogue_clarification_continuity": True,
        "developer_user_mode_separation": True,
        "telemetry_delivery": False,
        "automatic_goal_progress": False,
        "tutor_confidence_calibration": "task-source-evaluation-conservative",
        "tutor_authority": False,
        "tutor_tools_allowed": False,
        "automatic_model_download": False,
        "automatic_memory_promotion": False,
        "automatic_installation": False,
        "network_access": False,
        "background_execution": False,
        "action_runs": app.action_runs.count(),
    }
    rust_tools = {
        "cargo": bool(shutil.which("cargo")),
        "rustc": bool(shutil.which("rustc")),
        "rustfmt": bool(shutil.which("rustfmt")),
        "clippy_component": bool(shutil.which("cargo-clippy")),
        "project_local_detection": True,
        "network_allowed": False,
        "automatic_installation": False,
    }
    data = {
        "version": __version__,
        "owner": app.identity.display_name,
        "system_user": app.identity.system_user,
        "config": str(app.paths.config_file),
        "database": str(app.paths.database_file),
        "persona": app.persona.to_dict()
        | {
            "path": str(app.paths.persona_config_file),
        },
        "offline": app.config.offline,
        "network_allowed": app.config.network_allowed,
        "telemetry": app.config.telemetry,
        "ethics": ethics
        | {
            "reviews": app.ethics_reviews.count(),
            "redirects": app.ethics_reviews.count(decision="redirect"),
        },
        "dictionary": app.dictionary.status(),
        "translation": app.translator.status(),
        "first_aid": app.first_aid.status(),
        "memory_tiers": app.tiered_memory.status(),
        "preferences": app.preferences.status(),
        "tutors": tutor_status,
        "cognitive_executive": app.cognitive_executive.status(),
        "personal_organizer": app.personal_organizer.status(),
        "wellbeing": app.wellbeing.status(),
        "automation": app.automation.status(),
        "scheduler": app.scheduler.status(),
        "semantic_intents": app.semantic_intents.status(),
        "account": {
            "registered": app.registry_accounts.has_account(),
            "account_count": app.registry_accounts.account_count(),
            "active_account_public_id": app.account_public_id,
            "isolated_vault": bool(app.account_public_id),
            "profile": app.accounts.get_account() if app.account_public_id else None,
            "security": (app.accounts.security_status() if app.account_public_id else None),
        },
        "allowed_roots": roots,
        "tools": tools,
        "document_tools": document_tools,
        "php_tools": php_tools,
        "web_tools": web_tools,
        "python_tools": python_tools,
        "java_tools": java_tools,
        "kotlin_tools": kotlin_tools,
        "dotnet_tools": dotnet_tools,
        "native_tools": native_tools,
        "ruby_tools": ruby_tools,
        "go_tools": go_tools,
        "rust_tools": rust_tools,
        "sql_tools": sql_tools,
        "swift_tools": swift_tools,
        "dart_tools": dart_tools,
        "assistant_orchestration": assistant_orchestration,
        "php_skill_limits": {
            "timeout_seconds": app.config.php_tool_timeout_seconds,
            "max_output_chars": app.config.php_tool_max_output_chars,
        },
        "python_skill_limits": {
            "timeout_seconds": app.config.python_tool_timeout_seconds,
            "max_output_chars": app.config.python_tool_max_output_chars,
        },
        "java_skill_limits": {
            "timeout_seconds": app.config.java_tool_timeout_seconds,
            "max_output_chars": app.config.java_tool_max_output_chars,
        },
        "kotlin_skill_limits": {
            "timeout_seconds": app.config.kotlin_tool_timeout_seconds,
            "max_output_chars": app.config.kotlin_tool_max_output_chars,
        },
        "dotnet_skill_limits": {
            "timeout_seconds": app.config.dotnet_tool_timeout_seconds,
            "max_output_chars": app.config.dotnet_tool_max_output_chars,
        },
        "native_skill_limits": {
            "timeout_seconds": app.config.native_tool_timeout_seconds,
            "max_output_chars": app.config.native_tool_max_output_chars,
        },
        "ruby_skill_limits": {
            "timeout_seconds": app.config.ruby_tool_timeout_seconds,
            "max_output_chars": app.config.ruby_tool_max_output_chars,
        },
        "go_skill_limits": {
            "timeout_seconds": app.config.go_tool_timeout_seconds,
            "max_output_chars": app.config.go_tool_max_output_chars,
        },
        "rust_skill_limits": {
            "timeout_seconds": app.config.rust_tool_timeout_seconds,
            "max_output_chars": app.config.rust_tool_max_output_chars,
        },
        "sql_skill_limits": {
            "timeout_seconds": app.config.sql_tool_timeout_seconds,
            "max_output_chars": app.config.sql_tool_max_output_chars,
        },
        "swift_skill_limits": {
            "timeout_seconds": app.config.swift_tool_timeout_seconds,
            "max_output_chars": app.config.swift_tool_max_output_chars,
        },
        "dart_skill_limits": {
            "timeout_seconds": app.config.dart_tool_timeout_seconds,
            "max_output_chars": app.config.dart_tool_max_output_chars,
        },
        "language_engine": app.language_engine.name,
        "language_config": _language_config_data(
            language_config,
            app.paths.language_config_file,
            error=language_error,
        ),
        "skills": len(app.skills.list_all()),
        "documents": len(app.knowledge.list_active(10000)),
        "chats": len(app.chats.list_active(10000)),
        "episodes": len(app.memory_lifecycle.list_episodes(limit=10000)),
        "memory_proposals": len(app.memory_lifecycle.list_proposals(status="pending", limit=10000)),
        "trusted_projects": app.trusted_projects.list_all(),
        "alexandria": app.alexandria.overview(),
        "alexandria_packages": app.alexandria_packages.list_all(),
        "alexandria_structured_packs": app.structured_packs.status(),
    }
    human = (
        f"Elyndra {__version__}\n"
        f"Propietario: {app.identity.display_name} ({app.identity.system_user})\n"
        f"Configuración: {app.paths.config_file}\n"
        f"Base: {app.paths.database_file}\n"
        f"Persona: {app.persona.source} · {app.paths.persona_config_file}\n"
        f"Offline: {app.config.offline}; red permitida: {app.config.network_allowed}; "
        f"telemetría: {app.config.telemetry}\n"
        "Ética constitucional: activa; núcleo desactivable: no; "
        f"consejo proactivo: {app.config.ethical_advice_enabled}; "
        f"tutor secundario: {app.config.ethical_tutor_review_enabled}; "
        f"revisiones: {data['ethics']['reviews']}; "
        f"redirecciones: {data['ethics']['redirects']}\n"
        f"Motor lingüístico: {app.language_engine.name}\n"
        f"Config lingüística: {app.paths.language_config_file} "
        f"(activa={language_config.enabled}, perfil={language_config.profile.name})\n"
        f"Diccionario local: {data['dictionary']['entry_count']} conceptos; "
        f"idiomas={','.join(data['dictionary']['languages'])}; modelo=no\n"
        f"Traducción local: frases={data['translation']['phrase_count']}; "
        "palabras y paquetes conocidos sin modelo; modelo solo como respaldo\n"
        f"Primeros auxilios locales: {data['first_aid']['topic_count']} tarjetas; "
        f"modelo=no; red=no; manual_completo={data['first_aid']['complete_manual']}\n"
        "Memoria por niveles: hot=RAM acotada; warm=SQLite reciente; "
        "cold=SQLite durable; promoción automática no revisada=no\n"
        f"Preferencias revisadas: activas={data['preferences']['active_preferences']}; "
        f"pendientes={data['preferences']['pending_proposals']}; aprendizaje silencioso=no\n"
        f"Arbitraje de tutores: habilitados={data['tutors']['enabled_tutors']}; "
        f"docentes={data['tutors']['enabled_teachers']}; "
        f"auditores={data['tutors']['enabled_auditors']}; "
        f"externos={data['tutors']['external_tutors']}; "
        f"benchmarks={data['tutors']['benchmark_runs']}; "
        f"lecciones={data['tutors']['learning']['active_lessons']}; "
        f"evaluaciones={data['tutors']['evolution']['evaluations']}; "
        f"conocimiento_tarea={data['tutors']['evolution']['active_knowledge']}; "
        f"conocimiento_general={data['tutors']['general_knowledge']['active_knowledge']}; "
        "calibración=tarea/fuente/evaluación; autoridad=no; herramientas=no; "
        "promoción automática=no; borrado de conocimiento=no\n"
        f"Ejecutivo cognitivo: decisiones={data['cognitive_executive']['decisions']}; "
        f"objetivos_activos={data['cognitive_executive']['active_goals']}; "
        f"tareas_pendientes={data['cognitive_executive']['pending_tasks']}; "
        "prompt_crudo=no; razonamiento_privado=no; ejecución_automática=no\n"
        f"Organizador personal: activos={data['personal_organizer']['active_items']}; "
        f"rutinas={data['personal_organizer']['active_routines']}; "
        f"cumpleaños={data['personal_organizer']['active_birthdays']}; "
        f"recordatorios_aprobados={data['personal_organizer']['approved_reminders']}; "
        "notificaciones_automáticas=no; segundo_plano=no\n"
        f"Bienestar personal: checkins={data['wellbeing']['checkins']}; "
        f"planes_activos={data['wellbeing']['active_plans']}; "
        f"acciones_pendientes={data['wellbeing']['pending_actions']}; "
        "diagnóstico=no; intervención_automática=no\n"
        f"Automatización supervisada: políticas={data['automation']['active_policies']}; "
        f"automatizaciones={data['automation']['active_automations']}; "
        f"pendientes_aprobación={data['automation']['pending_approval_runs']}; "
        f"bandeja_sin_leer={data['automation']['unread_inbox']}; "
        "despacho=primer_plano; red=no; skills=no; archivos=no\n"
        f"Cuentas locales: total={data['account']['account_count']}; "
        f"bóveda_activa={'sí' if data['account']['isolated_vault'] else 'no'}; "
        "aislamiento_sqlite=sí; web_estabilizada=sí; online_disponible=no\n"
        f"Comprensión semántica: intenciones={data['semantic_intents']['ontology_intents']}; "
        f"ejemplos_revisados={data['semantic_intents']['reviewed_examples']}; "
        f"resoluciones={data['semantic_intents']['resolutions']}; "
        f"tutor_fallbacks={data['semantic_intents']['tutor_fallbacks']}; "
        "prompt_crudo=no; aprendizaje_silencioso=no\n"
        f"Skills: {data['skills']}; documentos: {data['documents']}; "
        f"chats: {data['chats']}; episodios: {data['episodes']}; "
        f"propuestas: {data['memory_proposals']}\n"
        f"Herramientas: {', '.join(f'{k}={v}' for k, v in tools.items())}\n"
        f"Documentos: {', '.join(f'{k}={v}' for k, v in document_tools.items())}\n"
        f"Skills PHP: {', '.join(f'{k}={v}' for k, v in php_tools.items())}\n"
        f"Skills web: {', '.join(f'{k}={v}' for k, v in web_tools.items())}\n"
        f"Skills Python: {', '.join(f'{k}={v}' for k, v in python_tools.items())}\n"
        f"Skills Java: {', '.join(f'{k}={v}' for k, v in java_tools.items())}\n"
        f"Skills Kotlin: {', '.join(f'{k}={v}' for k, v in kotlin_tools.items())}\n"
        f"Skills C#/.NET: {', '.join(f'{k}={v}' for k, v in dotnet_tools.items())}\n"
        f"Skills Swift: {', '.join(f'{k}={v}' for k, v in swift_tools.items())}\n"
        f"Skills Dart/Flutter: {', '.join(f'{k}={v}' for k, v in dart_tools.items())}\n"
        f"Skills SQL/SQLite: {', '.join(f'{k}={v}' for k, v in sql_tools.items())}\n"
        f"Orquestación supervisada: "
        f"enabled={assistant_orchestration['enabled']}, "
        f"max_steps={assistant_orchestration['max_steps']}, "
        f"allowlisted_skills={assistant_orchestration['allowlisted_skills']}, "
        f"model_planning={assistant_orchestration['model_planning']}, "
        f"single_use_approval={assistant_orchestration['single_use_approval']}, "
        f"file_writes={assistant_orchestration['file_writes']}, "
        f"write_mode={assistant_orchestration['write_mode']}, "
        f"pending_changes={assistant_orchestration['pending_change_proposals']}, "
        f"validation_cycles={assistant_orchestration['validation_cycles']}, "
        f"development_sessions={assistant_orchestration['development_sessions']}, "
        f"active_sessions={assistant_orchestration['active_development_sessions']}, "
        "session_continuity=True, automatic_next_actions=False, "
        "constitutional_ethics=True, ethics_core_disableable=False, "
        f"proactive_advice={assistant_orchestration['proactive_advice']}, "
        f"ethical_tutor_review={assistant_orchestration['ethical_tutor_review']}, "
        "dictionary_lookup=True, dictionary_model_required=False, "
        "local_translation=True, translation_model_fallback_only=True, "
        "structured_language_packs=True, structured_first_aid_packs=True, "
        "structured_pack_auto_download=False, "
        "first_aid_lookup=True, first_aid_model_required=False, memory_tiers=True, "
        "reviewed_preference_learning=True, silent_preference_learning=False, "
        "tutor_arbitration=True, tutor_benchmarking=True, "
        "reviewed_tutor_learning=True, silent_tutor_learning=False, "
        "supervised_tutor_evaluation=True, advisory_tutor_auditors=True, "
        "versioned_durable_knowledge=True, durable_knowledge_deletion=False, "
        "automatic_knowledge_promotion=False, "
        "supervised_general_knowledge_acquisition=True, "
        "general_knowledge_deletion=False, general_knowledge_model_fallback=True, "
        "qualitative_confidence_normalization=True, "
        "failed_knowledge_plan_retry=True, knowledge_conflict_review=True, "
        "non_destructive_knowledge_revalidation=True, "
        "immutable_approved_knowledge_metadata=True, "
        "multisource_knowledge_evidence=True, "
        "cross_auditor_knowledge_review=True, "
        "domain_project_knowledge_scoping=True, cognitive_executive=True, "
        "budgeted_context_assembly=True, "
        "multidimensional_decision_confidence=True, "
        "persistent_goals_and_tasks=True, outcome_verification=True, "
        "personal_organizer=True, deterministic_daily_brief=True, "
        "personal_wellbeing=True, reviewed_coaching=True, web_cli_parity=True, "
        "policy_bounded_automation=True, foreground_automation_dispatch=True, "
        "standing_policy_execution=True, external_automation_actions=False, "
        "optional_local_scheduler=True, interprocess_scheduler_lock=True, "
        "clean_scheduler_shutdown=True, local_browser_notifications=True, "
        "semantic_intent_resolution=True, tutor_assisted_intent_resolution=True, "
        "reviewed_language_learning=True, silent_language_learning=False, "
        "local_account_identity=True, isolated_multi_account_vaults=True, "
        "stabilized_web_interface=True, lazy_chat_persistence=True, "
        "online_gateway_available=False, argon2id_passwords=True, "
        "encrypted_local_export=True, remote_account_backup=False, "
        "two_factor_available=True, dialogue_clarification_continuity=True, "
        "developer_user_mode_separation=True, telemetry_delivery=False, "
        "system_service_installation=False, automatic_goal_progress=False, "
        "tutor_confidence_calibration=task-source-evaluation-conservative, "
        "tutor_authority=False, "
        "tutor_tools_allowed=False, automatic_model_download=False, "
        "automatic_memory_promotion=False, "
        f"automatic_repair_loops={assistant_orchestration['automatic_repair_loops']}, "
        f"network_access={assistant_orchestration['network_access']}, "
        f"background_execution={assistant_orchestration['background_execution']}, "
        f"action_runs={assistant_orchestration['action_runs']}\n"
        f"Skills Rust: {', '.join(f'{k}={v}' for k, v in rust_tools.items())}\n"
        f"Límites PHP/web: timeout={app.config.php_tool_timeout_seconds}s; "
        f"salida={app.config.php_tool_max_output_chars} caracteres\n"
        f"Límites Python: timeout={app.config.python_tool_timeout_seconds}s; "
        f"salida={app.config.python_tool_max_output_chars} caracteres\n"
        f"Límites Java: timeout={app.config.java_tool_timeout_seconds}s; "
        f"salida={app.config.java_tool_max_output_chars} caracteres\n"
        f"Límites Kotlin: timeout={app.config.kotlin_tool_timeout_seconds}s; "
        f"salida={app.config.kotlin_tool_max_output_chars} caracteres\n"
        f"Límites C#/.NET: timeout={app.config.dotnet_tool_timeout_seconds}s; "
        f"salida={app.config.dotnet_tool_max_output_chars} caracteres\n"
        f"Límites Rust: timeout={app.config.rust_tool_timeout_seconds}s; "
        f"salida={app.config.rust_tool_max_output_chars} caracteres\n"
        f"Límites Swift: timeout={app.config.swift_tool_timeout_seconds}s; "
        f"salida={app.config.swift_tool_max_output_chars} caracteres\n"
        f"Límites Dart/Flutter: timeout={app.config.dart_tool_timeout_seconds}s; "
        f"salida={app.config.dart_tool_max_output_chars} caracteres\n"
        f"Límites SQL: timeout={app.config.sql_tool_timeout_seconds}s; "
        f"salida={app.config.sql_tool_max_output_chars} caracteres\n"
        f"Proyectos confiables adicionales: {len(data['trusted_projects'])}\n"
        f"Paquetes opcionales de Alejandría: {len(data['alexandria_packages'])}; "
        f"paquetes estructurados: {data['alexandria_structured_packs']['pack_count']}"
    )
    return _print(data, json_output, human=human)


def _result(result: Any, json_output: bool) -> int:
    payload = {"ok": result.ok, "message": result.message, "data": result.data}
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(result.message)
        raw_results = result.data.get("results")
        if (
            isinstance(raw_results, list)
            and raw_results
            and all(isinstance(item, str) for item in raw_results)
        ):
            print("\n".join(raw_results))
        if result.data.get("output"):
            print(result.data["output"])
    return 0 if result.ok else 1


def _print(data: Any, json_output: bool, *, human: str) -> int:
    if json_output:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print(human)
    return 0


def _format_memories(memories: list[dict[str, Any]]) -> str:
    if not memories:
        return "No hay recuerdos coincidentes."
    return "\n".join(
        f"#{item['id']} [{item['kind']}] {item['content']}"
        + (f" (proyecto: {item['project']})" if item.get("project") else "")
        for item in memories
    )


def _format_episodes(episodes: list[dict[str, Any]]) -> str:
    if not episodes:
        return "No hay episodios coincidentes."
    return "\n".join(
        f"#{item['id']} [{item['kind']}] {item['content']} (chat: {item['chat_public_id']})"
        for item in episodes
    )


def _format_proposals(proposals: list[dict[str, Any]]) -> str:
    if not proposals:
        return "No hay propuestas de memoria coincidentes."
    return "\n".join(
        f"#{item['id']} [{item['status']}/{item['kind']}] {item['content']} — {item['reason']}"
        for item in proposals
    )


def _format_archives(archives: list[dict[str, Any]]) -> str:
    if not archives:
        return "No hay transcripciones frías archivadas."
    return "\n".join(
        f"#{item['id']} {item['chat_public_id']} · {item['turn_count']} turnos · "
        f"{item['size_bytes']} bytes · {item['path']}"
        for item in archives
    )


def _format_memory_state(
    state: dict[str, Any],
    episodes: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
) -> str:
    lines = [f"Memoria del chat {state['chat_id']}:"]
    for title, key in (
        ("Temas", "topics"),
        ("Decisiones", "decisions"),
        ("Pendientes", "pending"),
        ("Resultados", "outcomes"),
    ):
        values = state.get(key, [])
        lines.append(f"{title}: {len(values)}")
        lines.extend(f"- {item}" for item in values[-5:])
    lines.append(f"Episodios activos: {len(episodes)}")
    lines.append(f"Propuestas pendientes globales: {len(proposals)}")
    return "\n".join(lines)


def _format_corrections(corrections: list[dict[str, Any]]) -> str:
    if not corrections:
        return "No hay correcciones guardadas."
    return "\n".join(
        f"#{item['id']} chat={item.get('chat_public_id') or '-'} | "
        f"Original: {item['original_response']} | Corrección: {item['corrected_response']}"
        for item in corrections
    )


def _format_projects(projects: list[dict[str, Any]]) -> str:
    if not projects:
        return "No hay proyectos registrados."
    return "\n".join(f"#{item['id']} {item['name']} → {item['path']}" for item in projects)


def _format_documents(documents: list[dict[str, Any]]) -> str:
    if not documents:
        return "No hay documentos importados."
    return "\n".join(
        f"#{item['id']} {item['title']} [{item['source_type']}] — {item['chunks']} fragmentos"
        + (f" (proyecto: {item['project']})" if item.get("project") else "")
        for item in documents
    )
