from __future__ import annotations

import sqlite3
from contextlib import suppress
from pathlib import Path


class Database:
    def __init__(self, path: Path, *, role: str | None = None) -> None:
        self.path = path
        if role not in {None, "root", "vault"}:
            raise ValueError("database_role debe ser root o vault.")
        self.role = role

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            stored_role = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'database_role'"
            ).fetchone()
            stored_schema = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if (
                stored_role is None
                and self.role is not None
                and stored_schema is not None
                and int(stored_schema[0]) >= 49
            ):
                raise RuntimeError("Una base schema 49 sin database_role no es confiable.")
            if stored_role is not None and self.role is not None and stored_role[0] != self.role:
                raise RuntimeError(
                    f"Rol de base incompatible: esperado {self.role}, encontrado {stored_role[0]}."
                )
            effective_role = self.role or (str(stored_role[0]) if stored_role else "vault")
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) VALUES('database_role', ?)",
                (effective_role,),
            )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    project TEXT,
                    source TEXT NOT NULL DEFAULT 'owner',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memories_status_created
                ON memories(status, created_at DESC);

                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trusted_project_roots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_trusted_project_roots_path
                ON trusted_project_roots(path);

                CREATE TABLE IF NOT EXISTS php_project_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_root TEXT NOT NULL UNIQUE,
                    phpstan_config TEXT NOT NULL DEFAULT '',
                    phpstan_level TEXT NOT NULL DEFAULT '',
                    phpunit_config TEXT NOT NULL DEFAULT '',
                    phpunit_testsuite TEXT NOT NULL DEFAULT '',
                    composer_strict INTEGER NOT NULL DEFAULT 0,
                    composer_enabled INTEGER NOT NULL DEFAULT 1,
                    syntax_scan_enabled INTEGER NOT NULL DEFAULT 1,
                    phpstan_enabled INTEGER NOT NULL DEFAULT 1,
                    phpunit_enabled INTEGER NOT NULL DEFAULT 1,
                    fail_fast INTEGER NOT NULL DEFAULT 0,
                    require_tools INTEGER NOT NULL DEFAULT 0,
                    max_php_files INTEGER NOT NULL DEFAULT 2000,
                    exclude_paths_json TEXT NOT NULL DEFAULT '[]',
                    timeout_seconds INTEGER,
                    max_output_chars INTEGER,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_php_project_profiles_root
                ON php_project_profiles(project_root);

                CREATE TABLE IF NOT EXISTS web_project_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_root TEXT NOT NULL UNIQUE,
                    html_enabled INTEGER NOT NULL DEFAULT 1,
                    css_enabled INTEGER NOT NULL DEFAULT 1,
                    javascript_enabled INTEGER NOT NULL DEFAULT 1,
                    typescript_enabled INTEGER NOT NULL DEFAULT 1,
                    eslint_enabled INTEGER NOT NULL DEFAULT 1,
                    stylelint_enabled INTEGER NOT NULL DEFAULT 1,
                    framework_checks_enabled INTEGER NOT NULL DEFAULT 1,
                    framework_preset TEXT NOT NULL DEFAULT 'auto',
                    eslint_config TEXT NOT NULL DEFAULT '',
                    stylelint_config TEXT NOT NULL DEFAULT '',
                    fail_fast INTEGER NOT NULL DEFAULT 0,
                    require_tools INTEGER NOT NULL DEFAULT 0,
                    max_files INTEGER NOT NULL DEFAULT 3000,
                    exclude_paths_json TEXT NOT NULL DEFAULT '[]',
                    timeout_seconds INTEGER,
                    max_output_chars INTEGER,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_web_project_profiles_root
                ON web_project_profiles(project_root);

                CREATE TABLE IF NOT EXISTS python_project_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_root TEXT NOT NULL UNIQUE,
                    pyproject_enabled INTEGER NOT NULL DEFAULT 1,
                    compile_enabled INTEGER NOT NULL DEFAULT 1,
                    ruff_enabled INTEGER NOT NULL DEFAULT 1,
                    mypy_enabled INTEGER NOT NULL DEFAULT 1,
                    pytest_enabled INTEGER NOT NULL DEFAULT 1,
                    ruff_config TEXT NOT NULL DEFAULT '',
                    mypy_config TEXT NOT NULL DEFAULT '',
                    pytest_path TEXT NOT NULL DEFAULT '',
                    fail_fast INTEGER NOT NULL DEFAULT 0,
                    require_tools INTEGER NOT NULL DEFAULT 0,
                    max_python_files INTEGER NOT NULL DEFAULT 3000,
                    exclude_paths_json TEXT NOT NULL DEFAULT '[]',
                    timeout_seconds INTEGER,
                    max_output_chars INTEGER,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_python_project_profiles_root
                ON python_project_profiles(project_root);


                CREATE TABLE IF NOT EXISTS java_project_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_root TEXT NOT NULL UNIQUE,
                    descriptor_enabled INTEGER NOT NULL DEFAULT 1,
                    javac_enabled INTEGER NOT NULL DEFAULT 1,
                    build_enabled INTEGER NOT NULL DEFAULT 1,
                    tests_enabled INTEGER NOT NULL DEFAULT 1,
                    build_tool TEXT NOT NULL DEFAULT 'auto',
                    java_release INTEGER,
                    fail_fast INTEGER NOT NULL DEFAULT 0,
                    require_tools INTEGER NOT NULL DEFAULT 0,
                    max_java_files INTEGER NOT NULL DEFAULT 3000,
                    exclude_paths_json TEXT NOT NULL DEFAULT '[]',
                    timeout_seconds INTEGER,
                    max_output_chars INTEGER,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_java_project_profiles_root
                ON java_project_profiles(project_root);


                CREATE TABLE IF NOT EXISTS kotlin_project_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_root TEXT NOT NULL UNIQUE,
                    descriptor_enabled INTEGER NOT NULL DEFAULT 1,
                    kotlinc_enabled INTEGER NOT NULL DEFAULT 1,
                    build_enabled INTEGER NOT NULL DEFAULT 1,
                    tests_enabled INTEGER NOT NULL DEFAULT 1,
                    build_tool TEXT NOT NULL DEFAULT 'auto',
                    jvm_target INTEGER,
                    fail_fast INTEGER NOT NULL DEFAULT 0,
                    require_tools INTEGER NOT NULL DEFAULT 0,
                    max_kotlin_files INTEGER NOT NULL DEFAULT 3000,
                    exclude_paths_json TEXT NOT NULL DEFAULT '[]',
                    timeout_seconds INTEGER,
                    max_output_chars INTEGER,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_kotlin_project_profiles_root
                ON kotlin_project_profiles(project_root);

                CREATE TABLE IF NOT EXISTS dotnet_project_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_root TEXT NOT NULL UNIQUE,
                    descriptor_enabled INTEGER NOT NULL DEFAULT 1,
                    format_enabled INTEGER NOT NULL DEFAULT 1,
                    build_enabled INTEGER NOT NULL DEFAULT 1,
                    tests_enabled INTEGER NOT NULL DEFAULT 1,
                    configuration TEXT NOT NULL DEFAULT 'Debug',
                    fail_fast INTEGER NOT NULL DEFAULT 0,
                    require_tools INTEGER NOT NULL DEFAULT 0,
                    max_dotnet_files INTEGER NOT NULL DEFAULT 3000,
                    exclude_paths_json TEXT NOT NULL DEFAULT '[]',
                    timeout_seconds INTEGER,
                    max_output_chars INTEGER,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_dotnet_project_profiles_root
                ON dotnet_project_profiles(project_root);

                CREATE TABLE IF NOT EXISTS swift_project_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_root TEXT NOT NULL UNIQUE,
                    manifest_enabled INTEGER NOT NULL DEFAULT 1,
                    syntax_enabled INTEGER NOT NULL DEFAULT 1,
                    format_enabled INTEGER NOT NULL DEFAULT 1,
                    build_enabled INTEGER NOT NULL DEFAULT 1,
                    tests_enabled INTEGER NOT NULL DEFAULT 1,
                    configuration TEXT NOT NULL DEFAULT 'debug',
                    fail_fast INTEGER NOT NULL DEFAULT 0,
                    require_tools INTEGER NOT NULL DEFAULT 0,
                    max_swift_files INTEGER NOT NULL DEFAULT 3000,
                    exclude_paths_json TEXT NOT NULL DEFAULT '[]',
                    timeout_seconds INTEGER,
                    max_output_chars INTEGER,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_swift_project_profiles_root
                ON swift_project_profiles(project_root);

                CREATE TABLE IF NOT EXISTS dart_project_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_root TEXT NOT NULL UNIQUE,
                    descriptor_enabled INTEGER NOT NULL DEFAULT 1,
                    format_enabled INTEGER NOT NULL DEFAULT 1,
                    analyze_enabled INTEGER NOT NULL DEFAULT 1,
                    tests_enabled INTEGER NOT NULL DEFAULT 1,
                    test_runner TEXT NOT NULL DEFAULT 'auto',
                    fail_fast INTEGER NOT NULL DEFAULT 0,
                    require_tools INTEGER NOT NULL DEFAULT 0,
                    max_dart_files INTEGER NOT NULL DEFAULT 3000,
                    exclude_paths_json TEXT NOT NULL DEFAULT '[]',
                    timeout_seconds INTEGER,
                    max_output_chars INTEGER,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_dart_project_profiles_root
                ON dart_project_profiles(project_root);

                CREATE TABLE IF NOT EXISTS sql_project_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_root TEXT NOT NULL UNIQUE,
                    static_enabled INTEGER NOT NULL DEFAULT 1,
                    migrations_enabled INTEGER NOT NULL DEFAULT 1,
                    schema_enabled INTEGER NOT NULL DEFAULT 1,
                    dialect TEXT NOT NULL DEFAULT 'auto',
                    allow_mutating_sql INTEGER NOT NULL DEFAULT 0,
                    allow_destructive_migrations INTEGER NOT NULL DEFAULT 0,
                    fail_fast INTEGER NOT NULL DEFAULT 0,
                    max_sql_files INTEGER NOT NULL DEFAULT 3000,
                    max_database_files INTEGER NOT NULL DEFAULT 20,
                    exclude_paths_json TEXT NOT NULL DEFAULT '[]',
                    timeout_seconds INTEGER,
                    max_output_chars INTEGER,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sql_project_profiles_root
                ON sql_project_profiles(project_root);

                CREATE TABLE IF NOT EXISTS native_project_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_root TEXT NOT NULL UNIQUE,
                    descriptor_enabled INTEGER NOT NULL DEFAULT 1,
                    c_syntax_enabled INTEGER NOT NULL DEFAULT 1,
                    cpp_syntax_enabled INTEGER NOT NULL DEFAULT 1,
                    static_enabled INTEGER NOT NULL DEFAULT 1,
                    build_enabled INTEGER NOT NULL DEFAULT 1,
                    tests_enabled INTEGER NOT NULL DEFAULT 1,
                    compiler TEXT NOT NULL DEFAULT 'auto',
                    c_standard TEXT NOT NULL DEFAULT 'c17',
                    cpp_standard TEXT NOT NULL DEFAULT 'c++20',
                    fail_fast INTEGER NOT NULL DEFAULT 0,
                    require_tools INTEGER NOT NULL DEFAULT 0,
                    max_native_files INTEGER NOT NULL DEFAULT 3000,
                    exclude_paths_json TEXT NOT NULL DEFAULT '[]',
                    timeout_seconds INTEGER,
                    max_output_chars INTEGER,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_native_project_profiles_root
                ON native_project_profiles(project_root);

                CREATE TABLE IF NOT EXISTS ruby_project_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_root TEXT NOT NULL UNIQUE,
                    descriptor_enabled INTEGER NOT NULL DEFAULT 1,
                    bundle_enabled INTEGER NOT NULL DEFAULT 1,
                    syntax_enabled INTEGER NOT NULL DEFAULT 1,
                    rubocop_enabled INTEGER NOT NULL DEFAULT 1,
                    tests_enabled INTEGER NOT NULL DEFAULT 1,
                    test_framework TEXT NOT NULL DEFAULT 'auto',
                    fail_fast INTEGER NOT NULL DEFAULT 0,
                    require_tools INTEGER NOT NULL DEFAULT 0,
                    max_ruby_files INTEGER NOT NULL DEFAULT 3000,
                    exclude_paths_json TEXT NOT NULL DEFAULT '[]',
                    timeout_seconds INTEGER,
                    max_output_chars INTEGER,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_ruby_project_profiles_root
                ON ruby_project_profiles(project_root);

                CREATE TABLE IF NOT EXISTS go_project_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_root TEXT NOT NULL UNIQUE,
                    module_enabled INTEGER NOT NULL DEFAULT 1,
                    fmt_enabled INTEGER NOT NULL DEFAULT 1,
                    vet_enabled INTEGER NOT NULL DEFAULT 1,
                    build_enabled INTEGER NOT NULL DEFAULT 1,
                    tests_enabled INTEGER NOT NULL DEFAULT 1,
                    test_mode TEXT NOT NULL DEFAULT 'auto',
                    fail_fast INTEGER NOT NULL DEFAULT 0,
                    require_tools INTEGER NOT NULL DEFAULT 0,
                    max_go_files INTEGER NOT NULL DEFAULT 3000,
                    exclude_paths_json TEXT NOT NULL DEFAULT '[]',
                    timeout_seconds INTEGER,
                    max_output_chars INTEGER,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_go_project_profiles_root
                ON go_project_profiles(project_root);

                CREATE TABLE IF NOT EXISTS rust_project_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_root TEXT NOT NULL UNIQUE,
                    manifest_enabled INTEGER NOT NULL DEFAULT 1,
                    fmt_enabled INTEGER NOT NULL DEFAULT 1,
                    check_enabled INTEGER NOT NULL DEFAULT 1,
                    clippy_enabled INTEGER NOT NULL DEFAULT 1,
                    tests_enabled INTEGER NOT NULL DEFAULT 1,
                    feature_mode TEXT NOT NULL DEFAULT 'default',
                    fail_fast INTEGER NOT NULL DEFAULT 0,
                    require_tools INTEGER NOT NULL DEFAULT 0,
                    max_rust_files INTEGER NOT NULL DEFAULT 3000,
                    exclude_paths_json TEXT NOT NULL DEFAULT '[]',
                    timeout_seconds INTEGER,
                    max_output_chars INTEGER,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_rust_project_profiles_root
                ON rust_project_profiles(project_root);

                CREATE TABLE IF NOT EXISTS verification_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    toolchain TEXT NOT NULL,
                    project_root TEXT NOT NULL,
                    profile_id INTEGER,
                    status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    duration_ms INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_verification_runs_toolchain_project
                ON verification_runs(toolchain, project_root, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_action_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    plan_id TEXT NOT NULL,
                    chat_id TEXT,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    duration_ms INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_assistant_action_runs_plan
                ON assistant_action_runs(plan_id, id DESC);

                CREATE INDEX IF NOT EXISTS idx_assistant_action_runs_chat
                ON assistant_action_runs(chat_id, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_change_proposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    proposal_id TEXT NOT NULL,
                    chat_id TEXT,
                    project_root TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    proposal_json TEXT NOT NULL DEFAULT '{}',
                    diff_text TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    applied_at TEXT,
                    rejected_at TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_assistant_change_proposals_status
                ON assistant_change_proposals(status, id DESC);

                CREATE INDEX IF NOT EXISTS idx_assistant_change_proposals_project
                ON assistant_change_proposals(project_root, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_validation_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    source_change_proposal_id TEXT NOT NULL,
                    repair_proposal_id TEXT,
                    validation_run_id TEXT,
                    chat_id TEXT,
                    project_root TEXT NOT NULL,
                    status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    validation_request TEXT NOT NULL,
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    validation_result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_assistant_validation_cycles_status
                ON assistant_validation_cycles(status, id DESC);

                CREATE INDEX IF NOT EXISTS idx_assistant_validation_cycles_change
                ON assistant_validation_cycles(source_change_proposal_id, id DESC);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_validation_cycles_repair
                ON assistant_validation_cycles(repair_proposal_id)
                WHERE repair_proposal_id IS NOT NULL;

                CREATE TABLE IF NOT EXISTS assistant_development_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    root_change_proposal_id TEXT NOT NULL UNIQUE,
                    current_change_proposal_id TEXT NOT NULL,
                    current_validation_cycle_id TEXT,
                    chat_id TEXT,
                    project_root TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    closed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_assistant_development_sessions_status
                ON assistant_development_sessions(status, id DESC);

                CREATE INDEX IF NOT EXISTS idx_assistant_development_sessions_project
                ON assistant_development_sessions(project_root, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_development_session_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_session_event_unique
                ON assistant_development_session_events(
                    session_id, event_type, entity_type, entity_id
                );

                CREATE INDEX IF NOT EXISTS idx_assistant_session_events_session
                ON assistant_development_session_events(session_id, id ASC);

                CREATE TABLE IF NOT EXISTS assistant_chat_session_focus (
                    chat_id TEXT PRIMARY KEY,
                    development_session_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    selected_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_assistant_chat_session_focus_session
                ON assistant_chat_session_focus(development_session_id);

                CREATE TABLE IF NOT EXISTS assistant_ethics_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    category TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    alternatives_json TEXT NOT NULL DEFAULT '[]',
                    advisory TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    review_stage TEXT NOT NULL DEFAULT 'deterministic',
                    tutor_used INTEGER NOT NULL DEFAULT 0,
                    tutor_engine TEXT NOT NULL DEFAULT '',
                    tutor_label TEXT NOT NULL DEFAULT '',
                    uncertainty_reason TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_assistant_ethics_reviews_decision
                ON assistant_ethics_reviews(decision, id DESC);

                CREATE INDEX IF NOT EXISTS idx_assistant_ethics_reviews_category
                ON assistant_ethics_reviews(category, id DESC);

                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    source_path TEXT NOT NULL UNIQUE,
                    source_type TEXT NOT NULL,
                    project TEXT,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    imported_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_documents_status_updated
                ON documents(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS document_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    char_start INTEGER NOT NULL,
                    char_end INTEGER NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
                    UNIQUE(document_id, chunk_index)
                );

                CREATE INDEX IF NOT EXISTS idx_document_chunks_document
                ON document_chunks(document_id, chunk_index);

                CREATE TABLE IF NOT EXISTS chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    project TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    transcript_mode TEXT NOT NULL DEFAULT 'summary',
                    pinned INTEGER NOT NULL DEFAULT 0,
                    turn_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_opened_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_chats_status_updated
                ON chats(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS chat_summaries (
                    chat_id INTEGER PRIMARY KEY,
                    summary TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS chat_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    turn_index INTEGER NOT NULL,
                    user_text TEXT NOT NULL,
                    assistant_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE,
                    UNIQUE(chat_id, turn_index)
                );

                CREATE INDEX IF NOT EXISTS idx_chat_turns_chat_turn
                ON chat_turns(chat_id, turn_index DESC);

                CREATE TABLE IF NOT EXISTS chat_memory_state (
                    chat_id INTEGER PRIMARY KEY,
                    topics_json TEXT NOT NULL DEFAULT '[]',
                    decisions_json TEXT NOT NULL DEFAULT '[]',
                    pending_json TEXT NOT NULL DEFAULT '[]',
                    outcomes_json TEXT NOT NULL DEFAULT '[]',
                    recent_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS chat_episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    project TEXT,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    source_turn_index INTEGER,
                    importance INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE,
                    UNIQUE(chat_id, kind, content_hash)
                );

                CREATE INDEX IF NOT EXISTS idx_chat_episodes_chat_kind
                ON chat_episodes(chat_id, kind, status, id DESC);

                CREATE TABLE IF NOT EXISTS memory_proposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    project TEXT,
                    reason TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    memory_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE SET NULL,
                    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_proposals_status
                ON memory_proposals(status, id DESC);

                CREATE TABLE IF NOT EXISTS chat_archives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    turn_count INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE,
                    UNIQUE(chat_id, path)
                );

                CREATE INDEX IF NOT EXISTS idx_chat_archives_chat
                ON chat_archives(chat_id, id DESC);

                CREATE TABLE IF NOT EXISTS response_corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    user_text TEXT NOT NULL,
                    original_response TEXT NOT NULL,
                    corrected_response TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_response_corrections_chat
                ON response_corrections(chat_id, status, id DESC);

                CREATE TABLE IF NOT EXISTS chat_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    chat_id INTEGER NOT NULL,
                    turn_index INTEGER,
                    filename TEXT NOT NULL,
                    stored_path TEXT NOT NULL UNIQUE,
                    mime_type TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    extracted_text TEXT NOT NULL DEFAULT '',
                    secrets_redacted INTEGER NOT NULL DEFAULT 0,
                    extraction_status TEXT NOT NULL DEFAULT 'not_checked',
                    validation_status TEXT NOT NULL DEFAULT 'not_checked',
                    processor TEXT NOT NULL DEFAULT '',
                    diagnostics_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_chat_attachments_chat_turn
                ON chat_attachments(chat_id, turn_index, status, id);

                CREATE TABLE IF NOT EXISTS alexandria_libraries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    slug TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    domain TEXT NOT NULL DEFAULT 'general',
                    language TEXT NOT NULL DEFAULT 'auto',
                    version TEXT NOT NULL DEFAULT '1',
                    license_id TEXT NOT NULL DEFAULT 'unverified',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_alexandria_libraries_status
                ON alexandria_libraries(status, enabled DESC, updated_at DESC);

                CREATE TABLE IF NOT EXISTS alexandria_packages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    package_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    tier TEXT NOT NULL DEFAULT 'optional',
                    domain TEXT NOT NULL,
                    language TEXT NOT NULL,
                    license_id TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    package_root TEXT NOT NULL,
                    library_id INTEGER NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    actor TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    installed_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(library_id) REFERENCES alexandria_libraries(id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_alexandria_packages_tier_name
                ON alexandria_packages(tier, name COLLATE NOCASE);

                CREATE TABLE IF NOT EXISTS alexandria_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    library_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    stored_path TEXT NOT NULL UNIQUE,
                    original_path TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    processor TEXT NOT NULL DEFAULT '',
                    validation_status TEXT NOT NULL DEFAULT 'not_checked',
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'active',
                    imported_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(library_id) REFERENCES alexandria_libraries(id)
                        ON DELETE CASCADE,
                    UNIQUE(library_id, sha256)
                );

                CREATE INDEX IF NOT EXISTS idx_alexandria_sources_library
                ON alexandria_sources(library_id, status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS alexandria_units (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    library_id INTEGER NOT NULL,
                    source_id INTEGER NOT NULL,
                    unit_index INTEGER NOT NULL,
                    heading TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    review_status TEXT NOT NULL DEFAULT 'unreviewed',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(library_id) REFERENCES alexandria_libraries(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(source_id) REFERENCES alexandria_sources(id)
                        ON DELETE CASCADE,
                    UNIQUE(source_id, unit_index)
                );

                CREATE INDEX IF NOT EXISTS idx_alexandria_units_library
                ON alexandria_units(library_id, review_status, status, id);

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT,
                    outcome TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_audit_created
                ON audit_events(created_at DESC);
                """
            )
            chat_columns = {
                str(row["name"]) for row in connection.execute("PRAGMA table_info(chats)")
            }
            if "pinned" not in chat_columns:
                connection.execute("ALTER TABLE chats ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chats_status_pinned_updated
                ON chats(status, pinned DESC, updated_at DESC)
                """
            )
            php_profile_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(php_project_profiles)")
            }
            php_profile_defaults = {
                "composer_enabled": "INTEGER NOT NULL DEFAULT 1",
                "syntax_scan_enabled": "INTEGER NOT NULL DEFAULT 1",
                "phpstan_enabled": "INTEGER NOT NULL DEFAULT 1",
                "phpunit_enabled": "INTEGER NOT NULL DEFAULT 1",
                "fail_fast": "INTEGER NOT NULL DEFAULT 0",
                "require_tools": "INTEGER NOT NULL DEFAULT 0",
                "max_php_files": "INTEGER NOT NULL DEFAULT 2000",
                "exclude_paths_json": "TEXT NOT NULL DEFAULT '[]'",
            }
            for name, definition in php_profile_defaults.items():
                if name not in php_profile_columns:
                    connection.execute(
                        f"ALTER TABLE php_project_profiles ADD COLUMN {name} {definition}"
                    )

            web_profile_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(web_project_profiles)")
            }
            web_profile_defaults = {
                "eslint_enabled": "INTEGER NOT NULL DEFAULT 1",
                "stylelint_enabled": "INTEGER NOT NULL DEFAULT 1",
                "framework_checks_enabled": "INTEGER NOT NULL DEFAULT 1",
                "framework_preset": "TEXT NOT NULL DEFAULT 'auto'",
                "eslint_config": "TEXT NOT NULL DEFAULT ''",
                "stylelint_config": "TEXT NOT NULL DEFAULT ''",
            }
            for name, definition in web_profile_defaults.items():
                if name not in web_profile_columns:
                    connection.execute(
                        f"ALTER TABLE web_project_profiles ADD COLUMN {name} {definition}"
                    )

            attachment_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(chat_attachments)")
            }
            attachment_defaults = {
                "extraction_status": "TEXT NOT NULL DEFAULT 'not_checked'",
                "validation_status": "TEXT NOT NULL DEFAULT 'not_checked'",
                "processor": "TEXT NOT NULL DEFAULT ''",
                "diagnostics_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for name, definition in attachment_defaults.items():
                if name not in attachment_columns:
                    connection.execute(
                        f"ALTER TABLE chat_attachments ADD COLUMN {name} {definition}"
                    )

            ethics_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(assistant_ethics_reviews)")
            }
            ethics_defaults = {
                "confidence": "REAL NOT NULL DEFAULT 1.0",
                "review_stage": "TEXT NOT NULL DEFAULT 'deterministic'",
                "tutor_used": "INTEGER NOT NULL DEFAULT 0",
                "tutor_engine": "TEXT NOT NULL DEFAULT ''",
                "tutor_label": "TEXT NOT NULL DEFAULT ''",
                "uncertainty_reason": "TEXT NOT NULL DEFAULT ''",
            }
            for name, definition in ethics_defaults.items():
                if name not in ethics_columns:
                    connection.execute(
                        f"ALTER TABLE assistant_ethics_reviews ADD COLUMN {name} {definition}"
                    )

            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS alexandria_structured_packs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    package_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    adapter TEXT NOT NULL,
                    language TEXT NOT NULL,
                    target_language TEXT NOT NULL DEFAULT '',
                    locale TEXT NOT NULL DEFAULT '',
                    dialect TEXT NOT NULL DEFAULT '',
                    license_id TEXT NOT NULL,
                    publisher TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    storage_path TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    review_status TEXT NOT NULL DEFAULT 'unreviewed',
                    reviewed_on TEXT NOT NULL DEFAULT '',
                    reviewer TEXT NOT NULL DEFAULT '',
                    limitations_json TEXT NOT NULL DEFAULT '[]',
                    attribution_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    actor TEXT NOT NULL,
                    installed_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_alexandria_structured_packs_type
                ON alexandria_structured_packs(
                    content_type, enabled DESC, language, locale, package_id
                );

                CREATE TABLE IF NOT EXISTS alexandria_structured_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pack_id INTEGER NOT NULL,
                    relative_path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_format TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    attribution TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL,
                    record_count INTEGER NOT NULL,
                    FOREIGN KEY(pack_id) REFERENCES alexandria_structured_packs(id)
                        ON DELETE CASCADE,
                    UNIQUE(pack_id, relative_path)
                );

                CREATE INDEX IF NOT EXISTS idx_alexandria_structured_sources_pack
                ON alexandria_structured_sources(pack_id, id);

                CREATE TABLE IF NOT EXISTS alexandria_lexical_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pack_id INTEGER NOT NULL,
                    source_id INTEGER NOT NULL,
                    entry_key TEXT NOT NULL,
                    language TEXT NOT NULL,
                    target_language TEXT NOT NULL DEFAULT '',
                    lemma TEXT NOT NULL,
                    part_of_speech TEXT NOT NULL DEFAULT 'unknown',
                    sense_id TEXT NOT NULL DEFAULT '',
                    definition TEXT NOT NULL DEFAULT '',
                    translations_json TEXT NOT NULL DEFAULT '{}',
                    morphology_json TEXT NOT NULL DEFAULT '{}',
                    pronunciation_json TEXT NOT NULL DEFAULT '{}',
                    dialect_json TEXT NOT NULL DEFAULT '{}',
                    examples_json TEXT NOT NULL DEFAULT '[]',
                    source_ref TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(pack_id) REFERENCES alexandria_structured_packs(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(source_id) REFERENCES alexandria_structured_sources(id)
                        ON DELETE CASCADE,
                    UNIQUE(pack_id, entry_key)
                );

                CREATE INDEX IF NOT EXISTS idx_alexandria_lexical_entries_pack
                ON alexandria_lexical_entries(pack_id, language, id);

                CREATE TABLE IF NOT EXISTS alexandria_lexical_forms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id INTEGER NOT NULL,
                    pack_id INTEGER NOT NULL,
                    language TEXT NOT NULL,
                    form TEXT NOT NULL,
                    normalized_form TEXT NOT NULL,
                    form_type TEXT NOT NULL,
                    locale TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(entry_id) REFERENCES alexandria_lexical_entries(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(pack_id) REFERENCES alexandria_structured_packs(id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_alexandria_lexical_forms_lookup
                ON alexandria_lexical_forms(language, normalized_form, pack_id, id);

                CREATE TABLE IF NOT EXISTS alexandria_first_aid_cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pack_id INTEGER NOT NULL,
                    source_id INTEGER NOT NULL,
                    card_key TEXT NOT NULL,
                    language TEXT NOT NULL,
                    locale TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    urgency TEXT NOT NULL DEFAULT 'emergency',
                    steps_json TEXT NOT NULL,
                    avoid_json TEXT NOT NULL DEFAULT '[]',
                    red_flags_json TEXT NOT NULL DEFAULT '[]',
                    source_refs_json TEXT NOT NULL,
                    reviewed_on TEXT NOT NULL,
                    FOREIGN KEY(pack_id) REFERENCES alexandria_structured_packs(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(source_id) REFERENCES alexandria_structured_sources(id)
                        ON DELETE CASCADE,
                    UNIQUE(pack_id, card_key, language, locale)
                );

                CREATE INDEX IF NOT EXISTS idx_alexandria_first_aid_cards_lookup
                ON alexandria_first_aid_cards(pack_id, language, locale, card_key);

                CREATE TABLE IF NOT EXISTS alexandria_first_aid_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    card_id INTEGER NOT NULL,
                    pack_id INTEGER NOT NULL,
                    language TEXT NOT NULL,
                    locale TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    normalized_alias TEXT NOT NULL,
                    FOREIGN KEY(card_id) REFERENCES alexandria_first_aid_cards(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(pack_id) REFERENCES alexandria_structured_packs(id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_alexandria_first_aid_aliases_lookup
                ON alexandria_first_aid_aliases(language, locale, normalized_alias, pack_id);
                """
            )

            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_cold_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    chat_public_id TEXT,
                    kind TEXT NOT NULL,
                    project TEXT,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    importance REAL NOT NULL DEFAULT 0.5,
                    status TEXT NOT NULL DEFAULT 'active',
                    source_created_at TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    last_accessed_at TEXT,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(source_type, source_id)
                );

                CREATE INDEX IF NOT EXISTS idx_memory_cold_index_status_project
                ON memory_cold_index(status, project, id DESC);

                CREATE TABLE IF NOT EXISTS memory_recall_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    query_sha256 TEXT NOT NULL,
                    project TEXT,
                    chat_public_id TEXT,
                    hot_hit INTEGER NOT NULL DEFAULT 0,
                    hot_items INTEGER NOT NULL DEFAULT 0,
                    warm_items INTEGER NOT NULL DEFAULT 0,
                    cold_items INTEGER NOT NULL DEFAULT 0,
                    total_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_recall_events_created
                ON memory_recall_events(created_at DESC);

                CREATE TABLE IF NOT EXISTS memory_consolidation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    min_age_days INTEGER NOT NULL,
                    scanned_items INTEGER NOT NULL,
                    indexed_items INTEGER NOT NULL,
                    deleted_items INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                """
            )

            preference_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(memory_proposals)")
            }
            preference_defaults = {
                "preference_category": "TEXT NOT NULL DEFAULT 'general'",
                "preference_scope": "TEXT NOT NULL DEFAULT 'global'",
                "expires_at": "TEXT",
            }
            for column, declaration in preference_defaults.items():
                if column not in preference_columns:
                    connection.execute(
                        f"ALTER TABLE memory_proposals ADD COLUMN {column} {declaration}"
                    )

            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS reviewed_preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    memory_id INTEGER NOT NULL,
                    source_proposal_id INTEGER NOT NULL,
                    category TEXT NOT NULL DEFAULT 'general',
                    scope TEXT NOT NULL DEFAULT 'global',
                    project TEXT,
                    content TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    status TEXT NOT NULL DEFAULT 'active',
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE,
                    FOREIGN KEY(source_proposal_id) REFERENCES memory_proposals(id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_reviewed_preferences_status_scope
                ON reviewed_preferences(status, scope, project, id DESC);

                CREATE INDEX IF NOT EXISTS idx_reviewed_preferences_expiration
                ON reviewed_preferences(status, expires_at);
                """
            )

            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS assistant_tutor_benchmark_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    suite_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tutor_count INTEGER NOT NULL,
                    case_count INTEGER NOT NULL,
                    actor TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_tutor_benchmark_runs_status
                ON assistant_tutor_benchmark_runs(status, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_tutor_benchmark_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    tutor_id TEXT NOT NULL,
                    engine_name TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    score REAL NOT NULL,
                    passed INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    output_sha256 TEXT NOT NULL,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES assistant_tutor_benchmark_runs(id)
                        ON DELETE CASCADE,
                    UNIQUE(run_id, tutor_id, case_id)
                );

                CREATE INDEX IF NOT EXISTS idx_tutor_benchmark_results_task
                ON assistant_tutor_benchmark_results(tutor_id, task_type, run_id DESC);

                CREATE TABLE IF NOT EXISTS assistant_tutor_selections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    task_type TEXT NOT NULL,
                    tutor_id TEXT NOT NULL,
                    engine_name TEXT NOT NULL,
                    selection_reason TEXT NOT NULL,
                    benchmark_run_id TEXT,
                    benchmark_score REAL,
                    prompt_sha256 TEXT NOT NULL,
                    context_items INTEGER NOT NULL DEFAULT 0,
                    candidate_ids_json TEXT NOT NULL DEFAULT '[]',
                    result_status TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    fallback_used INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tutor_selections_task_created
                ON assistant_tutor_selections(task_type, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_tutor_lesson_proposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    tutor_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    lesson_text TEXT NOT NULL,
                    lesson_sha256 TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT,
                    source_sha256 TEXT NOT NULL,
                    observed_score REAL NOT NULL,
                    review_confidence REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    expires_at TEXT,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    reviewed_by TEXT,
                    lesson_id INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_tutor_lesson_proposals_status
                ON assistant_tutor_lesson_proposals(status, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_tutor_lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    proposal_id INTEGER NOT NULL UNIQUE,
                    tutor_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    lesson_text TEXT NOT NULL,
                    lesson_sha256 TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT,
                    source_sha256 TEXT NOT NULL,
                    observed_score REAL NOT NULL,
                    review_confidence REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    reviewed_by TEXT NOT NULL,
                    FOREIGN KEY(proposal_id)
                        REFERENCES assistant_tutor_lesson_proposals(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_tutor_lessons_active_task
                ON assistant_tutor_lessons(status, tutor_id, task_type, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_tutor_evidence_comparisons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    tutor_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    selection_id TEXT,
                    tutor_output_sha256 TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    comparison_method TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    proposal_id INTEGER,
                    FOREIGN KEY(proposal_id)
                        REFERENCES assistant_tutor_lesson_proposals(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tutor_evidence_comparisons_task
                ON assistant_tutor_evidence_comparisons(tutor_id, task_type, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_tutor_lesson_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    lesson_id INTEGER NOT NULL,
                    tutor_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    auditor_id TEXT,
                    suite_version TEXT NOT NULL,
                    case_ids_json TEXT NOT NULL DEFAULT '[]',
                    knowledge_ids_json TEXT NOT NULL DEFAULT '[]',
                    model_fingerprint TEXT NOT NULL,
                    auditor_fingerprint TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    recommendation TEXT NOT NULL DEFAULT '',
                    baseline_score REAL,
                    candidate_score REAL,
                    score_delta REAL,
                    baseline_latency_ms INTEGER NOT NULL DEFAULT 0,
                    candidate_latency_ms INTEGER NOT NULL DEFAULT 0,
                    auditor_status TEXT NOT NULL DEFAULT 'not_requested',
                    auditor_verdict TEXT NOT NULL DEFAULT '',
                    auditor_confidence REAL,
                    auditor_output_sha256 TEXT NOT NULL DEFAULT '',
                    promoted_knowledge_id INTEGER,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(lesson_id)
                        REFERENCES assistant_tutor_lessons(id) ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_tutor_lesson_evaluations_status
                ON assistant_tutor_lesson_evaluations(status, tutor_id, task_type, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_tutor_lesson_evaluation_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    evaluation_id INTEGER NOT NULL,
                    case_id TEXT NOT NULL,
                    baseline_score REAL NOT NULL,
                    candidate_score REAL NOT NULL,
                    baseline_passed INTEGER NOT NULL DEFAULT 0,
                    candidate_passed INTEGER NOT NULL DEFAULT 0,
                    baseline_latency_ms INTEGER NOT NULL DEFAULT 0,
                    candidate_latency_ms INTEGER NOT NULL DEFAULT 0,
                    baseline_output_sha256 TEXT NOT NULL,
                    candidate_output_sha256 TEXT NOT NULL,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(evaluation_id)
                        REFERENCES assistant_tutor_lesson_evaluations(id) ON DELETE CASCADE,
                    UNIQUE(evaluation_id, case_id)
                );

                CREATE INDEX IF NOT EXISTS idx_tutor_lesson_evaluation_results
                ON assistant_tutor_lesson_evaluation_results(evaluation_id, id);

                CREATE TABLE IF NOT EXISTS assistant_tutor_knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    lineage_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    predecessor_id INTEGER,
                    successor_id INTEGER,
                    origin_tutor_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    source_lesson_id INTEGER NOT NULL,
                    source_evaluation_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    validation_status TEXT NOT NULL DEFAULT 'validated',
                    model_fingerprint TEXT NOT NULL,
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    FOREIGN KEY(predecessor_id)
                        REFERENCES assistant_tutor_knowledge(id) ON DELETE RESTRICT,
                    FOREIGN KEY(successor_id)
                        REFERENCES assistant_tutor_knowledge(id) ON DELETE RESTRICT,
                    FOREIGN KEY(source_lesson_id)
                        REFERENCES assistant_tutor_lessons(id) ON DELETE RESTRICT,
                    FOREIGN KEY(source_evaluation_id)
                        REFERENCES assistant_tutor_lesson_evaluations(id) ON DELETE RESTRICT,
                    UNIQUE(lineage_id, version)
                );

                CREATE INDEX IF NOT EXISTS idx_tutor_knowledge_active_task
                ON assistant_tutor_knowledge(status, task_type, reviewed_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_knowledge_acquisition_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    knowledge_kind TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    question TEXT NOT NULL,
                    locale TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_title TEXT NOT NULL,
                    source_ref TEXT NOT NULL DEFAULT '',
                    source_observed_at TEXT,
                    revalidate_after TEXT,
                    evidence_text TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    source_unit_ids_json TEXT NOT NULL DEFAULT '[]',
                    evidence_sources_json TEXT NOT NULL DEFAULT '[]',
                    domain TEXT NOT NULL DEFAULT '',
                    project TEXT NOT NULL DEFAULT '',
                    tutor_id TEXT NOT NULL,
                    auditor_id TEXT,
                    auditor_ids_json TEXT NOT NULL DEFAULT '[]',
                    model_fingerprint TEXT NOT NULL,
                    auditor_fingerprint TEXT,
                    auditor_fingerprints_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    candidate_json TEXT NOT NULL DEFAULT '{}',
                    candidate_sha256 TEXT NOT NULL DEFAULT '',
                    deterministic_audit_json TEXT NOT NULL DEFAULT '{}',
                    related_knowledge_ids_json TEXT NOT NULL DEFAULT '[]',
                    conflict_status TEXT NOT NULL DEFAULT 'none',
                    auditor_status TEXT NOT NULL DEFAULT 'not_requested',
                    auditor_verdict TEXT NOT NULL DEFAULT '',
                    auditor_confidence REAL,
                    auditor_output_sha256 TEXT NOT NULL DEFAULT '',
                    audit_reviews_json TEXT NOT NULL DEFAULT '[]',
                    promoted_knowledge_id INTEGER,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    error TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_knowledge_acquisition_status
                ON assistant_knowledge_acquisition_plans(status, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_general_knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    lineage_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    predecessor_id INTEGER,
                    successor_id INTEGER,
                    knowledge_kind TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    claims_json TEXT NOT NULL DEFAULT '[]',
                    keywords_json TEXT NOT NULL DEFAULT '[]',
                    limitations_json TEXT NOT NULL DEFAULT '[]',
                    locale TEXT NOT NULL,
                    domain TEXT NOT NULL DEFAULT '',
                    project TEXT NOT NULL DEFAULT '',
                    validation_confidence REAL NOT NULL,
                    source_observed_at TEXT,
                    revalidate_after TEXT,
                    validation_status TEXT NOT NULL DEFAULT 'validated',
                    last_revalidated_at TEXT,
                    source_plan_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    FOREIGN KEY(predecessor_id)
                        REFERENCES assistant_general_knowledge(id) ON DELETE RESTRICT,
                    FOREIGN KEY(successor_id)
                        REFERENCES assistant_general_knowledge(id) ON DELETE RESTRICT,
                    FOREIGN KEY(source_plan_id)
                        REFERENCES assistant_knowledge_acquisition_plans(id)
                        ON DELETE RESTRICT,
                    UNIQUE(lineage_id, version)
                );

                CREATE INDEX IF NOT EXISTS idx_general_knowledge_active
                ON assistant_general_knowledge(
                    status, knowledge_kind, reviewed_at DESC, id DESC
                );

                CREATE TABLE IF NOT EXISTS assistant_knowledge_conflicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    knowledge_a_id INTEGER NOT NULL,
                    knowledge_b_id INTEGER NOT NULL,
                    subject TEXT NOT NULL,
                    conflict_kind TEXT NOT NULL DEFAULT 'potential_conflict',
                    status TEXT NOT NULL DEFAULT 'open',
                    resolution TEXT NOT NULL DEFAULT '',
                    resolution_note TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_by TEXT,
                    resolved_at TEXT,
                    FOREIGN KEY(knowledge_a_id)
                        REFERENCES assistant_general_knowledge(id) ON DELETE RESTRICT,
                    FOREIGN KEY(knowledge_b_id)
                        REFERENCES assistant_general_knowledge(id) ON DELETE RESTRICT,
                    UNIQUE(knowledge_a_id, knowledge_b_id)
                );

                CREATE INDEX IF NOT EXISTS idx_knowledge_conflicts_status
                ON assistant_knowledge_conflicts(status, id DESC);
                """
            )

            plan_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(assistant_knowledge_acquisition_plans)"
                )
            }
            plan_defaults = {
                "source_observed_at": "TEXT",
                "revalidate_after": "TEXT",
                "related_knowledge_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "conflict_status": "TEXT NOT NULL DEFAULT 'none'",
                "evidence_sources_json": "TEXT NOT NULL DEFAULT '[]'",
                "domain": "TEXT NOT NULL DEFAULT ''",
                "project": "TEXT NOT NULL DEFAULT ''",
                "auditor_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "auditor_fingerprints_json": "TEXT NOT NULL DEFAULT '{}'",
                "audit_reviews_json": "TEXT NOT NULL DEFAULT '[]'",
            }
            for column, declaration in plan_defaults.items():
                if column not in plan_columns:
                    connection.execute(
                        "ALTER TABLE assistant_knowledge_acquisition_plans "
                        f"ADD COLUMN {column} {declaration}"
                    )

            general_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(assistant_general_knowledge)")
            }
            general_defaults = {
                "source_observed_at": "TEXT",
                "revalidate_after": "TEXT",
                "validation_status": "TEXT NOT NULL DEFAULT 'validated'",
                "last_revalidated_at": "TEXT",
                "domain": "TEXT NOT NULL DEFAULT ''",
                "project": "TEXT NOT NULL DEFAULT ''",
            }
            for column, declaration in general_defaults.items():
                if column not in general_columns:
                    connection.execute(
                        f"ALTER TABLE assistant_general_knowledge ADD COLUMN {column} {declaration}"
                    )

            selection_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(assistant_tutor_selections)")
            }
            selection_defaults = {
                "calibrated_confidence": "REAL",
                "calibration_observations": "INTEGER NOT NULL DEFAULT 0",
                "calibration_sources_json": "TEXT NOT NULL DEFAULT '{}'",
                "lesson_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "knowledge_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "omitted_knowledge_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            }
            for column, declaration in selection_defaults.items():
                if column not in selection_columns:
                    connection.execute(
                        f"ALTER TABLE assistant_tutor_selections ADD COLUMN {column} {declaration}"
                    )

            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS assistant_executive_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    domain TEXT NOT NULL DEFAULT '',
                    project TEXT NOT NULL DEFAULT '',
                    goal_summary TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    candidate_routes_json TEXT NOT NULL DEFAULT '[]',
                    planned_route TEXT NOT NULL,
                    actual_route TEXT NOT NULL DEFAULT '',
                    confidence_json TEXT NOT NULL DEFAULT '{}',
                    approval_required INTEGER NOT NULL DEFAULT 0,
                    verification_required INTEGER NOT NULL DEFAULT 0,
                    context_ids_json TEXT NOT NULL DEFAULT '[]',
                    omitted_context_ids_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'assessed',
                    result_engine TEXT NOT NULL DEFAULT '',
                    result_ok INTEGER,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_executive_decisions_created
                ON assistant_executive_decisions(created_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    domain TEXT NOT NULL DEFAULT '',
                    project TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    status TEXT NOT NULL DEFAULT 'active',
                    target_date TEXT,
                    next_action TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_assistant_goals_status
                ON assistant_goals(status, priority, updated_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_goal_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    goal_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    dependency_ids_json TEXT NOT NULL DEFAULT '[]',
                    due_date TEXT,
                    completion_evidence_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(goal_id) REFERENCES assistant_goals(id)
                        ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_goal_tasks_goal_status
                ON assistant_goal_tasks(goal_id, status, priority, id);

                CREATE TABLE IF NOT EXISTS assistant_outcome_verifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    decision_id INTEGER,
                    expected_outcome TEXT NOT NULL,
                    observed_outcome TEXT NOT NULL,
                    verification_method TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(decision_id)
                        REFERENCES assistant_executive_decisions(id)
                        ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_outcome_verifications_created
                ON assistant_outcome_verifications(created_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_organizer_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    item_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    person_name TEXT NOT NULL DEFAULT '',
                    domain TEXT NOT NULL DEFAULT '',
                    project TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    status TEXT NOT NULL DEFAULT 'active',
                    anchor_date TEXT NOT NULL,
                    time_of_day TEXT,
                    timezone TEXT NOT NULL DEFAULT 'America/Santiago',
                    recurrence_kind TEXT NOT NULL DEFAULT 'once',
                    recurrence_interval INTEGER NOT NULL DEFAULT 1,
                    weekdays_json TEXT NOT NULL DEFAULT '[]',
                    recurrence_month INTEGER,
                    recurrence_day INTEGER,
                    recurrence_until TEXT,
                    birth_year INTEGER,
                    goal_id INTEGER,
                    task_id INTEGER,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(goal_id) REFERENCES assistant_goals(id)
                        ON DELETE RESTRICT,
                    FOREIGN KEY(task_id) REFERENCES assistant_goal_tasks(id)
                        ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_organizer_items_active
                ON assistant_organizer_items(
                    status, item_type, project, domain, anchor_date, id
                );

                CREATE TABLE IF NOT EXISTS assistant_routine_checkins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    routine_id INTEGER NOT NULL,
                    occurrence_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(routine_id, occurrence_date),
                    FOREIGN KEY(routine_id)
                        REFERENCES assistant_organizer_items(id)
                        ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_routine_checkins_date
                ON assistant_routine_checkins(occurrence_date, routine_id);

                CREATE TABLE IF NOT EXISTS assistant_organizer_reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    item_id INTEGER NOT NULL,
                    minutes_before INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'proposed',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    FOREIGN KEY(item_id)
                        REFERENCES assistant_organizer_items(id)
                        ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_organizer_reminders_status
                ON assistant_organizer_reminders(status, item_id, id);

                CREATE TABLE IF NOT EXISTS assistant_wellbeing_checkins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    checkin_date TEXT NOT NULL,
                    mood INTEGER NOT NULL,
                    energy INTEGER NOT NULL,
                    stress INTEGER NOT NULL,
                    focus INTEGER NOT NULL,
                    sleep_hours REAL,
                    sleep_quality INTEGER,
                    hydration INTEGER,
                    nutrition INTEGER,
                    activity_minutes INTEGER,
                    note TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_wellbeing_checkins_date
                ON assistant_wellbeing_checkins(checkin_date DESC, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_coaching_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    focus TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    start_date TEXT NOT NULL,
                    review_date TEXT,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_coaching_plans_status
                ON assistant_coaching_plans(status, review_date, id);

                CREATE TABLE IF NOT EXISTS assistant_coaching_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    plan_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(plan_id) REFERENCES assistant_coaching_plans(id)
                        ON DELETE RESTRICT,
                    UNIQUE(plan_id, position)
                );

                CREATE INDEX IF NOT EXISTS idx_coaching_actions_plan
                ON assistant_coaching_actions(plan_id, status, position, id);

                CREATE TABLE IF NOT EXISTS assistant_automation_policies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    autonomy_level TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    timezone TEXT NOT NULL DEFAULT 'America/Santiago',
                    window_start TEXT,
                    window_end TEXT,
                    max_runs_per_day INTEGER NOT NULL DEFAULT 1,
                    starts_at TEXT,
                    expires_at TEXT,
                    domain TEXT NOT NULL DEFAULT '',
                    project TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_automation_policies_status
                ON assistant_automation_policies(status, action_type, id);

                CREATE TABLE IF NOT EXISTS assistant_automations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    policy_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    schedule_kind TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    time_of_day TEXT NOT NULL,
                    weekdays_json TEXT NOT NULL DEFAULT '[]',
                    month_day INTEGER,
                    schedule_interval INTEGER NOT NULL DEFAULT 1,
                    until_date TEXT,
                    action_params_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(policy_id)
                        REFERENCES assistant_automation_policies(id)
                        ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_automations_status
                ON assistant_automations(status, start_date, time_of_day, id);

                CREATE TABLE IF NOT EXISTS assistant_automation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    automation_id INTEGER NOT NULL,
                    occurrence_key TEXT NOT NULL,
                    occurrence_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    verification_status TEXT NOT NULL DEFAULT 'pending',
                    approved_by TEXT,
                    approved_at TEXT,
                    FOREIGN KEY(automation_id)
                        REFERENCES assistant_automations(id)
                        ON DELETE RESTRICT,
                    UNIQUE(automation_id, occurrence_key)
                );

                CREATE INDEX IF NOT EXISTS idx_automation_runs_status
                ON assistant_automation_runs(status, occurrence_at, id);

                CREATE TABLE IF NOT EXISTS assistant_local_inbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    run_id INTEGER NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unread',
                    visible_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(run_id)
                        REFERENCES assistant_automation_runs(id)
                        ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_local_inbox_status
                ON assistant_local_inbox(status, visible_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_scheduler_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    pid INTEGER NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    lock_path TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    stopped_at TEXT,
                    scans_count INTEGER NOT NULL DEFAULT 0,
                    runs_created INTEGER NOT NULL DEFAULT 0,
                    notifications_created INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_scheduler_sessions_status
                ON assistant_scheduler_sessions(status, started_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_local_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    inbox_id INTEGER NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    seen_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(inbox_id)
                        REFERENCES assistant_local_inbox(id)
                        ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_local_notifications_status
                ON assistant_local_notifications(status, created_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_intent_examples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    intent TEXT NOT NULL,
                    normalized_phrase TEXT NOT NULL,
                    phrase_sha256 TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    approved_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(intent, phrase_sha256)
                );

                CREATE INDEX IF NOT EXISTS idx_intent_examples_active
                ON assistant_intent_examples(status, intent, id);

                CREATE TABLE IF NOT EXISTS assistant_intent_resolutions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    message_sha256 TEXT NOT NULL,
                    intent TEXT,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    entities_json TEXT NOT NULL DEFAULT '{}',
                    alternatives_json TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL,
                    tutor_used INTEGER NOT NULL DEFAULT 0,
                    clarification TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_intent_resolutions_created
                ON assistant_intent_resolutions(created_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_intent_learning_proposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    normalized_phrase TEXT NOT NULL,
                    phrase_sha256 TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    proposed_by TEXT NOT NULL,
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_intent_learning_status
                ON assistant_intent_learning_proposals(status, created_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS assistant_semantic_fallback_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    message_sha256 TEXT NOT NULL,
                    candidate_intent TEXT,
                    confidence REAL NOT NULL,
                    tutor_used INTEGER NOT NULL DEFAULT 0,
                    outcome TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_semantic_fallback_hash
                ON assistant_semantic_fallback_events(message_sha256, created_at DESC);

                CREATE TABLE IF NOT EXISTS local_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    birth_date TEXT NOT NULL,
                    preferred_name TEXT NOT NULL DEFAULT '',
                    pronouns TEXT NOT NULL DEFAULT '',
                    sex TEXT NOT NULL DEFAULT '',
                    gender_identity TEXT NOT NULL DEFAULT '',
                    sexual_orientation TEXT NOT NULL DEFAULT '',
                    system_user TEXT NOT NULL,
                    developer_mode INTEGER NOT NULL DEFAULT 0,
                    telemetry_enabled INTEGER NOT NULL DEFAULT 0,
                    birthday_greeting_enabled INTEGER NOT NULL DEFAULT 1,
                    timezone TEXT NOT NULL DEFAULT 'UTC',
                    language TEXT NOT NULL DEFAULT 'es-CL',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                DROP INDEX IF EXISTS idx_local_accounts_single_active;

                CREATE TABLE IF NOT EXISTS account_vaults (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL UNIQUE,
                    vault_path TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'active',
                    migrated_from_legacy INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES local_accounts(id)
                );

                CREATE INDEX IF NOT EXISTS idx_account_vaults_status
                ON account_vaults(status, account_id);

                CREATE TABLE IF NOT EXISTS account_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    account_id INTEGER NOT NULL,
                    token_sha256 TEXT NOT NULL UNIQUE,
                    interface TEXT NOT NULL,
                    user_agent_sha256 TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES local_accounts(id)
                );

                CREATE INDEX IF NOT EXISTS idx_account_sessions_active
                ON account_sessions(token_sha256, expires_at) WHERE revoked_at IS NULL;

                CREATE TABLE IF NOT EXISTS account_consents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    account_id INTEGER NOT NULL,
                    consent_type TEXT NOT NULL,
                    granted INTEGER NOT NULL,
                    policy_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES local_accounts(id)
                );

                CREATE INDEX IF NOT EXISTS idx_account_consents_account
                ON account_consents(account_id, consent_type, id DESC);

                CREATE TABLE IF NOT EXISTS account_recovery_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL UNIQUE,
                    local_export_enabled INTEGER NOT NULL DEFAULT 1,
                    remote_backup_enabled INTEGER NOT NULL DEFAULT 0,
                    remote_provider TEXT NOT NULL DEFAULT 'none',
                    two_factor_status TEXT NOT NULL DEFAULT 'available_not_configured',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES local_accounts(id)
                );

                CREATE TABLE IF NOT EXISTS account_exports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    account_id INTEGER NOT NULL,
                    format TEXT NOT NULL,
                    destination_sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES local_accounts(id)
                );

                CREATE TABLE IF NOT EXISTS account_mfa_factors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL UNIQUE,
                    account_id INTEGER NOT NULL,
                    factor_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'disabled',
                    secret_ciphertext TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES local_accounts(id)
                );

                CREATE TABLE IF NOT EXISTS assistant_dialogue_states (
                    chat_id TEXT PRIMARY KEY,
                    state_type TEXT NOT NULL,
                    state_json TEXT NOT NULL DEFAULT '{}',
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

            memory_fts = self._create_memory_fts(connection)
            knowledge_fts = self._create_knowledge_fts(connection)
            chat_fts = self._create_chat_fts(connection)
            episode_fts = self._create_episode_fts(connection)
            alexandria_fts = self._create_alexandria_fts(connection)
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('fts5', ?)",
                ("enabled" if memory_fts else "disabled",),
            )
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('knowledge_fts5', ?)",
                ("enabled" if knowledge_fts else "disabled",),
            )
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('chat_fts5', ?)",
                ("enabled" if chat_fts else "disabled",),
            )
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('episode_fts5', ?)",
                ("enabled" if episode_fts else "disabled",),
            )
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('alexandria_fts5', ?)",
                ("enabled" if alexandria_fts else "disabled",),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) "
                "VALUES('alexandria_index_version', '1')"
            )
            if effective_role == "root":
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS alexandria_language_packs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        public_id TEXT NOT NULL UNIQUE,
                        logical_pack_id TEXT NOT NULL,
                        language TEXT NOT NULL,
                        locale TEXT NOT NULL,
                        version TEXT NOT NULL,
                        pack_schema_version INTEGER NOT NULL CHECK(pack_schema_version > 0),
                        status TEXT NOT NULL CHECK(status IN ('enabled','disabled','invalid')),
                        query_priority INTEGER NOT NULL DEFAULT 100
                            CHECK(query_priority BETWEEN 0 AND 1000),
                        storage_relpath TEXT NOT NULL UNIQUE,
                        manifest_sha256 TEXT NOT NULL,
                        database_sha256 TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL,
                        source_count INTEGER NOT NULL CHECK(source_count >= 1),
                        lexeme_count INTEGER NOT NULL DEFAULT 0 CHECK(lexeme_count >= 0),
                        form_count INTEGER NOT NULL DEFAULT 0 CHECK(form_count >= 0),
                        sense_count INTEGER NOT NULL DEFAULT 0 CHECK(sense_count >= 0),
                        synset_count INTEGER NOT NULL DEFAULT 0 CHECK(synset_count >= 0),
                        builder_version TEXT NOT NULL,
                        verification_status TEXT NOT NULL
                            CHECK(verification_status IN ('pending','verified','failed')),
                        verified_at TEXT,
                        installed_by TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        installed_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(logical_pack_id, version, manifest_sha256)
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_language_packs_one_enabled
                    ON alexandria_language_packs(logical_pack_id) WHERE status = 'enabled';
                    CREATE INDEX IF NOT EXISTS idx_language_packs_query
                    ON alexandria_language_packs(
                        language, locale, status, query_priority DESC, logical_pack_id, version
                    );
                    CREATE TABLE IF NOT EXISTS alexandria_language_pack_sources (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        pack_id INTEGER NOT NULL,
                        source_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        source_version TEXT NOT NULL,
                        source_date TEXT NOT NULL DEFAULT '',
                        source_url TEXT NOT NULL,
                        input_filename TEXT NOT NULL,
                        original_sha256 TEXT NOT NULL,
                        license_id TEXT NOT NULL,
                        license_text_path TEXT NOT NULL,
                        attribution TEXT NOT NULL,
                        transformation_notes TEXT NOT NULL,
                        imported_record_count INTEGER NOT NULL CHECK(imported_record_count >= 0),
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(pack_id) REFERENCES alexandria_language_packs(id)
                            ON DELETE RESTRICT,
                        UNIQUE(pack_id, source_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_language_pack_sources_pack
                    ON alexandria_language_pack_sources(pack_id, source_id);
                    """
                )
            else:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS account_language_overlay_proposals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        public_id TEXT NOT NULL UNIQUE,
                        entry_type TEXT NOT NULL CHECK(entry_type IN
                            ('lexeme','form','sense','relation','informal')),
                        language TEXT NOT NULL,
                        locale TEXT NOT NULL DEFAULT '',
                        normalized_expression TEXT NOT NULL,
                        expression_sha256 TEXT NOT NULL,
                        payload_json TEXT NOT NULL CHECK(length(payload_json) <= 16384),
                        source TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
                        proposed_by TEXT NOT NULL,
                        reviewed_by TEXT,
                        reviewed_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_language_overlay_proposals
                    ON account_language_overlay_proposals(status, created_at DESC, id DESC);
                    CREATE TABLE IF NOT EXISTS account_language_overlays (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        public_id TEXT NOT NULL UNIQUE,
                        proposal_id INTEGER NOT NULL UNIQUE,
                        entry_type TEXT NOT NULL CHECK(entry_type IN
                            ('lexeme','form','sense','relation','informal')),
                        language TEXT NOT NULL,
                        locale TEXT NOT NULL DEFAULT '',
                        normalized_expression TEXT NOT NULL,
                        payload_json TEXT NOT NULL CHECK(length(payload_json) <= 16384),
                        status TEXT NOT NULL CHECK(status IN ('active','forgotten')),
                        actor TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(proposal_id) REFERENCES account_language_overlay_proposals(id)
                            ON DELETE RESTRICT
                    );
                    CREATE INDEX IF NOT EXISTS idx_language_overlays_lookup
                    ON account_language_overlays(
                        language, locale, normalized_expression, status, id
                    );
                    """
                )
            if effective_role == "root":
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS online_gateway_sources (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_id TEXT NOT NULL UNIQUE CHECK(length(source_id) BETWEEN 1 AND 64),
                        descriptor_version INTEGER NOT NULL CHECK(descriptor_version > 0),
                        display_name TEXT NOT NULL CHECK(length(display_name) BETWEEN 1 AND 160),
                        source_kind TEXT NOT NULL CHECK(source_kind = 'official-pinned'),
                        enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
                        descriptor_sha256 TEXT NOT NULL CHECK(length(descriptor_sha256) = 64),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS online_gateway_cache_entries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        artifact_key TEXT NOT NULL UNIQUE CHECK(length(artifact_key) <= 200),
                        source_id TEXT NOT NULL CHECK(length(source_id) <= 64),
                        artifact_name TEXT NOT NULL CHECK(length(artifact_name) <= 200),
                        expected_sha256 TEXT NOT NULL CHECK(length(expected_sha256) = 64),
                        observed_sha256 TEXT CHECK(
                            observed_sha256 IS NULL OR length(observed_sha256) = 64
                        ),
                        expected_size INTEGER NOT NULL CHECK(expected_size >= 0),
                        observed_size INTEGER CHECK(observed_size IS NULL OR observed_size >= 0),
                        cache_state TEXT NOT NULL CHECK(cache_state IN
                            ('absent','partial','verified','quarantined','failed')),
                        storage_relpath TEXT CHECK(
                            storage_relpath IS NULL OR length(storage_relpath) <= 500
                        ),
                        verified_at TEXT,
                        last_accessed_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_gateway_cache_source
                    ON online_gateway_cache_entries(source_id, cache_state, updated_at DESC);
                    CREATE TABLE IF NOT EXISTS online_gateway_download_jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        public_id TEXT NOT NULL UNIQUE CHECK(length(public_id) <= 64),
                        artifact_key TEXT NOT NULL CHECK(length(artifact_key) <= 200),
                        state TEXT NOT NULL CHECK(state IN
                            ('planned','approved','unavailable','partial','verified','failed','cancelled')),
                        bytes_written INTEGER NOT NULL DEFAULT 0 CHECK(bytes_written >= 0),
                        expected_size INTEGER NOT NULL CHECK(expected_size >= 0),
                        etag TEXT CHECK(etag IS NULL OR length(etag) <= 512),
                        last_modified TEXT CHECK(
                            last_modified IS NULL OR length(last_modified) <= 128
                        ),
                        partial_relpath TEXT CHECK(
                            partial_relpath IS NULL OR length(partial_relpath) <= 500
                        ),
                        error_code TEXT CHECK(error_code IS NULL OR length(error_code) <= 80),
                        started_at TEXT,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_gateway_jobs_state
                    ON online_gateway_download_jobs(state, updated_at DESC);
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_gateway_one_active_download
                    ON online_gateway_download_jobs((1))
                    WHERE state IN ('approved');
                    """
                )
            else:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS account_online_preferences (
                        id INTEGER PRIMARY KEY CHECK(id = 1),
                        online_enabled INTEGER NOT NULL DEFAULT 0 CHECK(online_enabled IN (0, 1)),
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS account_gateway_sources (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_id TEXT NOT NULL UNIQUE CHECK(length(source_id) BETWEEN 1 AND 64),
                        descriptor_json TEXT NOT NULL CHECK(length(descriptor_json) <= 65536),
                        descriptor_sha256 TEXT NOT NULL CHECK(length(descriptor_sha256) = 64),
                        enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS account_gateway_operations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        public_id TEXT NOT NULL UNIQUE CHECK(length(public_id) <= 64),
                        operation_kind TEXT NOT NULL CHECK(operation_kind IN
                            ('download','install','enable','history-clear')),
                        source_id TEXT NOT NULL CHECK(length(source_id) <= 64),
                        artifact_key TEXT NOT NULL CHECK(length(artifact_key) <= 200),
                        descriptor_sha256 TEXT NOT NULL CHECK(length(descriptor_sha256) = 64),
                        immutable_plan_json TEXT NOT NULL CHECK(
                            length(immutable_plan_json) <= 65536
                        ),
                        plan_sha256 TEXT NOT NULL CHECK(length(plan_sha256) = 64),
                        root_job_public_id TEXT CHECK(
                            root_job_public_id IS NULL OR length(root_job_public_id) <= 64
                        ),
                        operation_state TEXT NOT NULL CHECK(operation_state IN
                            ('planned','transport_unavailable','completed','failed','cancelled')),
                        install_requested INTEGER NOT NULL DEFAULT 0
                            CHECK(install_requested IN (0, 1)),
                        enable_requested INTEGER NOT NULL DEFAULT 0
                            CHECK(enable_requested IN (0, 1)),
                        error_code TEXT CHECK(error_code IS NULL OR length(error_code) <= 80),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_gateway_operations_recent
                    ON account_gateway_operations(created_at DESC, id DESC);
                    """
                )
            self._migrate_gateway_phase3(connection, effective_role)
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', '50')"
            )
        with suppress(PermissionError):
            self.path.chmod(0o600)

    @staticmethod
    def _migrate_gateway_phase3(
        connection: sqlite3.Connection, effective_role: str
    ) -> None:
        if effective_role != "root":
            return
        cache_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(online_gateway_cache_entries)")
        }
        if "descriptor_sha256" not in cache_columns:
            connection.execute(
                "ALTER TABLE online_gateway_cache_entries ADD COLUMN descriptor_sha256 TEXT"
            )
        sql_row = connection.execute(
            """SELECT sql FROM sqlite_master
            WHERE type='table' AND name='online_gateway_download_jobs'"""
        ).fetchone()
        if sql_row is None or "interrupted" in str(sql_row[0]):
            return
        connection.executescript(
            """
            CREATE TABLE online_gateway_download_jobs_phase3 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT NOT NULL UNIQUE CHECK(length(public_id) <= 64),
                artifact_key TEXT NOT NULL CHECK(length(artifact_key) <= 200),
                state TEXT NOT NULL CHECK(state IN (
                    'planned','approved','connecting','downloading','verifying','interrupted',
                    'resume_rejected','unavailable','partial','verified','quarantined','failed',
                    'cancelled'
                )),
                bytes_written INTEGER NOT NULL DEFAULT 0 CHECK(bytes_written >= 0),
                expected_size INTEGER NOT NULL CHECK(expected_size >= 0),
                etag TEXT CHECK(etag IS NULL OR length(etag) <= 512),
                last_modified TEXT CHECK(last_modified IS NULL OR length(last_modified) <= 128),
                partial_relpath TEXT CHECK(
                    partial_relpath IS NULL OR length(partial_relpath) <= 500
                ),
                error_code TEXT CHECK(error_code IS NULL OR length(error_code) <= 80),
                started_at TEXT,
                cancel_requested_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL
            );
            INSERT INTO online_gateway_download_jobs_phase3(
                id, public_id, artifact_key, state, bytes_written, expected_size, etag,
                last_modified, partial_relpath, error_code, started_at, updated_at
            ) SELECT id, public_id, artifact_key, state, bytes_written, expected_size, etag,
                last_modified, partial_relpath, error_code, started_at, updated_at
            FROM online_gateway_download_jobs;
            DROP TABLE online_gateway_download_jobs;
            ALTER TABLE online_gateway_download_jobs_phase3 RENAME TO online_gateway_download_jobs;
            CREATE INDEX idx_gateway_jobs_state
            ON online_gateway_download_jobs(state, updated_at DESC);
            CREATE UNIQUE INDEX idx_gateway_one_active_download
            ON online_gateway_download_jobs((1))
            WHERE state IN ('approved','connecting','downloading','verifying');
            """
        )

    @staticmethod
    def _create_memory_fts(connection: sqlite3.Connection) -> bool:
        try:
            existing = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_fts'"
            ).fetchone()
            connection.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    content,
                    kind,
                    project,
                    content='memories',
                    content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                );

                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memory_fts(rowid, content, kind, project)
                    VALUES (new.id, new.content, new.kind, COALESCE(new.project, ''));
                END;

                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memory_fts(memory_fts, rowid, content, kind, project)
                    VALUES ('delete', old.id, old.content, old.kind, COALESCE(old.project, ''));
                END;

                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memory_fts(memory_fts, rowid, content, kind, project)
                    VALUES ('delete', old.id, old.content, old.kind, COALESCE(old.project, ''));
                    INSERT INTO memory_fts(rowid, content, kind, project)
                    VALUES (new.id, new.content, new.kind, COALESCE(new.project, ''));
                END;
                """
            )
            if existing is None:
                connection.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
            return True
        except sqlite3.OperationalError:
            return False

    @staticmethod
    def _create_chat_fts(connection: sqlite3.Connection) -> bool:
        try:
            connection.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chat_fts USING fts5(
                    chat_id UNINDEXED,
                    public_id,
                    title,
                    project,
                    summary,
                    tokenize='unicode61 remove_diacritics 2'
                );
                """
            )
            return True
        except sqlite3.OperationalError:
            return False

    @staticmethod
    def _create_episode_fts(connection: sqlite3.Connection) -> bool:
        try:
            connection.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS episode_fts USING fts5(
                    episode_id UNINDEXED,
                    chat_id UNINDEXED,
                    kind,
                    project,
                    content,
                    tokenize='unicode61 remove_diacritics 2'
                );
                """
            )
            return True
        except sqlite3.OperationalError:
            return False

    @staticmethod
    def _create_alexandria_fts(connection: sqlite3.Connection) -> bool:
        try:
            connection.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS alexandria_fts USING fts5(
                    unit_id UNINDEXED,
                    library_id UNINDEXED,
                    library_name,
                    heading,
                    content,
                    tokenize='unicode61 remove_diacritics 2'
                );
                """
            )
            return True
        except sqlite3.OperationalError:
            return False

    @staticmethod
    def _create_knowledge_fts(connection: sqlite3.Connection) -> bool:
        try:
            connection.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(
                    chunk_id UNINDEXED,
                    document_id UNINDEXED,
                    title,
                    project,
                    content,
                    tokenize='unicode61 remove_diacritics 2'
                );
                """
            )
            return True
        except sqlite3.OperationalError:
            return False
