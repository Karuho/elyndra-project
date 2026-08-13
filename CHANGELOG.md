# Changelog

## 0.8.9-alpha — HTTP observability correction

- Future rejected downloads preserve only final HTTP status/class, normalized final host and bounded
  redirect hosts in audit JSON. Historical statuses cannot be reconstructed or backfilled.
- A final pre-body rejection is terminal and non-resumable; its verified empty partial is removed
  when no strong ETag exists. Schema remains 50.

## 0.8.9-alpha — Controlled Online Gateway, Phase 4

- Adds a read-only deterministic download-plan preview followed by separate plan approval and exact
  CLI execution with an ephemeral operation-bound network capability.
- Keeps persistent `network_allowed=true` invalid and removes download approval from the web write API.
- Corrects the release-note manifest SHA-256 transcription after the first public attempt stopped
  safely before network; the real public test remains pending.

- Adds a strict official acquisition descriptor and hash-bound offline manifest validation.
- Reuses the Phase 3 downloader for separately approved manifest and asset downloads.
- Hardens bundle archives against traversal, links, special files, ambiguous names, nested archives,
  decompression limits and malformed magic bytes.
- Adds private staging and separately approved, rollback-capable local installation without enabling
  packs automatically.
- Adds CLI and loopback-web observation/control while keeping download execution CLI-only.
- Uses no real Internet in tests; the public GitHub check remains separately gated.

## 0.8.9-alpha — Controlled Online Gateway, Phase 3

- Adds the standard-library-only HTTPS transport, pinned resolver, redirect and bounded-header
  validation, streaming downloads, safe storage, verification, cache, quarantine and strong-ETag
  resume under a global foreground lock.
- Adds explicit execute/resume/discard/cache/quarantine CLI operations with separate approvals and
  no scheduler, automatic retry, installation, telemetry or update checks.
- Validates Phase 3 only with injected loopback test dependencies; no public release was contacted.
- Preserves the Phase 2 schema/model/policy foundation described below.

- Advances root and account-vault SQLite schema to 50 with role-separated gateway metadata,
  account preferences, pinned sources and immutable operation plans.
- Adds deny-by-default policy limits, a pinned official Spanish Core descriptor, single-use
  in-memory permits backed by the existing approval store and privacy-filtered existing audit log.
- Adds CLI and loopback API parity for status, modes, sources, operations and approved planning.
- Keeps transport unavailable: no downloads, telemetry, update checks, background traffic or
  model/tool network authority are introduced.

## 0.8.8-alpha — Alejandría Spanish Lexical Core

- Adds schema 49 with fail-closed root/vault roles, a version-preserving shared language-pack
  registry and isolated reviewed account overlays.
- Adds deterministic streaming builders for local WordNet-LMF, Wiktionary JSONL, original informal
  Spanish and optional CLDR annotation sources.
- Adds separately licensed SQLite packs with FTS5, sense-aware relations, atomic installation,
  checksum verification and read-only bounded queries.
- Adds CLI/web language-pack administration and dictionary integration; real dataset work remains a
  separately supervised Phase 3 activity.
- Phase 3 verifies and builds separate real MCR 3.0 Spanish and CLDR 48.2 packs outside Git, adds the
  official OMW tab adapter and records exact source locks, checksums, licenses and resource metrics.
- Rejects the failed local extraction of the 20260801 XML dump while preserving its evidence, then
  imports the separately pinned Kaikki raw Wiktextract JSONL for the Spanish Wiktionary edition by
  gzip streaming, strict Spanish-language filtering and sense-scoped relations. Runtime stays offline.
- Keeps declared Spanish inflections attached to base lemmas, carries actual pack attribution through
  CLI and web dictionary results, and reproduces the final real-pack content/database hashes.

## 0.8.7-alpha

- Replaces the accumulated 0.8.6 web fixes with a complete, versioned web-shell stabilization release.
- Adds multiple local accounts with isolated SQLite vaults for chats, memory, personal data, goals, organizer, wellbeing, knowledge and automation state.
- Keeps the registry, Argon2id credentials and session hashes in the installation database while every account receives a separate private data directory.
- Preserves legacy personal data by cloning it only into the first registered account; later accounts start with clean vaults.
- Makes `/login` and `/register` first-class routes, supports username-or-email login, account switching and persistent HttpOnly sessions.
- Enforces developer mode in the interface and HTTP layer: Alejandría, Control, runtime diagnostics and technical forms are hidden or rejected for normal users.
- Stabilizes the sidebar so navigation and the account footer remain fixed while only conversation history scrolls.
- Replaces raw engine/offline labels with a clear Local/Online control; Online remains unavailable until the controlled gateway release.
- Makes the Elyndra brand a home link, removes the placeholder avatar, compacts search and removes the unused new-chat transcript selector.
- Makes new chats lazy: repeated clicks do not create empty database rows; a chat is persisted only after the first message or attachment.
- Advances SQLite schema to 48 and keeps CLI/web parity, 102 skills, no network access and no silent data sharing.

## 0.8.6-alpha
- Se añadió recuperación local de contraseña desde el mismo usuario del sistema y la pantalla de login ya no ofrece registrar una segunda cuenta.

- Adds one local account per installation with username/email login, mandatory adult birth-date validation, Argon2id password hashes and revocable local sessions.
- Fixes web authentication navigation with independent `/login` and `/register` routes, protected-page redirects and persistent sessions until explicit logout or expiry.
- Fixes the authenticated web shell: the conversation history scrolls independently, the account/profile/logout menu remains visible at the bottom of the sidebar, and the top chat action menu opens correctly for an active chat.
- Separates the current user profile from developer identity and keeps undefined sensitive profile fields out of model context.
- Adds user/developer web modes, profile/security/privacy settings, telemetry opt-in preview, 2FA-ready schema and encrypted local exports for CLI and web.
- Adds bounded dialogue clarification state and capability-aware help so terse follow-ups use the preceding question and Elyndra explains real Personal/CLI operations without naming a developer as a third party.
- Keeps remote backup, telemetry delivery, online access and 2FA activation disabled while preparing explicit future integration points.
- Advances SQLite schema to 47 while retaining 102 skills and CLI/web parity.

## 0.8.5-alpha

- Adds canonical semantic intents, local similarity/rule resolution, bounded temporal and metric extraction, and concrete clarification instead of generic model fallback.
- Adds tutor-assisted intent resolution with strict JSON, no tools, no SQLite, no permissions and no authority; the tutor proposes language interpretation only.
- Adds reviewed language-learning proposals and recurring-fallback detection without silent activation or automatic action.
- Grounds natural wellbeing, organizer, routine, coaching, goal, automation, notification, scheduler and knowledge variants in existing local data across CLI and web.
- Adds visible semantic diagnostics and full CLI/web parity for proposal and review workflows.
- Advances SQLite schema to 46 while retaining 102 skills and bounded memory use.

## 0.8.4-alpha

- Adds an optional local scheduler that may run in the foreground CLI or inside the active loopback web runtime.
- Adds exclusive inter-process locking, durable scheduler sessions, heartbeats and clean shutdown on Ctrl+C or web-server close.
- Adds bounded local notifications backed by SQLite plus optional browser notifications while the Personal web workspace is open.
- Keeps scheduler actions constrained by existing automation policies; no network, skills, shell, file writes, service installation or authority expansion is introduced.
- Adds CLI/web parity for scheduler status, one-shot cycles, start/stop controls and notification review.
- Advances SQLite schema to 45 while retaining 102 skills.

## 0.8.3-alpha

- Adds policy-bounded local automations with explicit autonomy levels, schedules, time windows and daily execution limits.
- Adds idempotent foreground dispatch, per-run approval where required and a local result inbox without external notifications.
- Restricts automation actions to bounded organizer, wellbeing, coaching and goal summaries; network, skills, shell and file writes remain unavailable.
- Adds full CLI/web parity for policies, automations, due scans, pending-run approval and inbox review.
- Advances SQLite schema to 44 while retaining 102 skills and zero background processes.

## 0.8.2-alpha

- Adds a local personal-coaching and wellbeing layer with bounded daily check-ins, deterministic summaries and explicit owner-reviewed coaching plans.
- Adds a first-class Personal web workspace for commitments, routines, birthdays and wellbeing check-ins with explicit confirmation and no background authority.
- Enforces CLI/web parity through shared `ElyndraApplication.ask` routing, real HTTP streaming regression tests and runtime-version metadata on pages and chat responses.
- Makes stale web runtimes visible and adds deterministic local wellbeing queries before Ollama fallback.
- Advances SQLite schema to 43 while retaining 102 skills, local-only storage and no diagnosis, treatment authority or automatic intervention.

## 0.8.1-alpha

- Added a local personal organizer for commitments, birthdays, recurring routines, routine check-ins and reviewed reminder proposals.
- Added deterministic daily briefs and upcoming occurrence queries that do not require Ollama.
- Kept recurrence expansion bounded and on demand, with no cron, daemon, notification delivery or automatic goal progress.
- Advanced SQLite schema to 42 while retaining 102 skills.

## 0.8.0-alpha

- Added a cognitive executive that records intent, risk, route, multidimensional confidence and result metadata without raw prompts or private reasoning.
- Added budgeted context assembly that excludes weakly related global knowledge before model fallback.
- Added persistent goals, dependent tasks and explicit outcome verification without automatic execution or progress.
- Advanced SQLite schema to 41 while retaining 102 skills.

## 0.7.32-alpha

- Added immutable approved knowledge metadata, bounded multisource evidence packages, conservative cross-auditor review and domain/project scoping.
- Added explicit CLI state guidance for pending, reviewed, failed and promoted knowledge plans.
- Advanced SQLite schema to 40 while retaining 102 skills.

## 0.7.31-alpha

- Added conservative numeric, percentage and Spanish/English qualitative confidence normalization.
- Added single-use retries for failed knowledge plans, non-destructive conflict review and scheduled revalidation.
- Advanced SQLite schema to 39 while retaining 102 skills.

## 0.7.30-alpha

- Added supervised general knowledge acquisition from explicit owner teaching, reviewed local text and reviewed Alejandría evidence.
- Added separate plan, foreground synthesis/audit and promotion stages plus local answer-before-model behavior.
- Preserved immutable version lineage and advanced SQLite schema to 38.

## 0.7.29-alpha

- Added supervised tutor-lesson evaluation, advisory auditors and durable task knowledge with immutable supersession lineage.
- Added conservative calibration from benchmarks, reviewed lessons and evaluation evidence.
- Advanced SQLite schema to 37 while retaining 102 skills.

## 0.7.28-alpha

- Added proposal-first reviewed tutor lessons with exact tutor/task scoping, expiration, rejection and forgetting.
- Added provenance-aware confidence calibration without model training, silent learning or authority transfer.
- Advanced SQLite schema to 36 while retaining 102 skills.

## 0.7.27-alpha

- Added supervised local tutor arbitration, fixed local benchmarks and task-specific selection with safe fallback to the primary model.
- Kept tutors isolated from tools, filesystem access, approvals, secrets and network authority.
- Advanced SQLite schema to 35 while retaining 102 skills.

## 0.7.26-alpha

- Added deterministic local translation fast paths, an expanded first-aid library and reviewed preference learning.
- Added explicit preference proposal, approval, rejection, expiration and forgetting without silent promotion.
- Advanced SQLite schema to 34 while retaining 102 skills.

## 0.7.25-alpha

- Added structured disk-backed language, dialect and reviewed first-aid packages for Alejandría with per-source hashes and attribution.
- Added bounded local installation and lookup without code execution, network access or automatic downloads.
- Advanced SQLite schema to 33 while retaining 102 skills.

## 0.7.24-alpha

- Added tiered hot, warm and cold memory with bounded RAM use and durable provenance-preserving retrieval.
- Expanded deterministic first-aid and ethics handling while keeping emergency routes ahead of model fallback.
- Advanced SQLite schema to 32 while retaining 102 skills.

## 0.7.23-alpha

- Strengthens constitutional review with explicit self-harm/crisis, homicide, child sexual abuse material and ambiguous-concealment categories instead of treating unknown requests as safe.
- Adds a bounded secondary local-tutor review for ambiguous cases; the model may increase caution but can never weaken a deterministic block or authorize an action.
- Adds category-specific neutral alternatives, fail-closed handling when the tutor is unavailable and structured confidence/tutor metadata without storing raw ethics prompts.
- Adds an offline multilingual starter lexicon for Spanish, English, Japanese, Chinese, Italian, French, Portuguese and German with version, license and SHA-256 metadata.
- Adds deterministic dictionary CLI, assistant fast paths, loopback API access and the `dictionary.lookup` skill without model or network use.
- Advances SQLite schema to 31 and the registry to 101 skills while preserving supervised execution, immutable ethics and zero background autonomy.

## 0.7.22-alpha

- Adds an immutable local professional ethics constitution evaluated before routing, planning, change proposals and language-model fallback.
- Adds neutral safe redirection for explicit malicious cyber activity, privacy abuse, fraud, physical harm, self-harm, environmental harm and system sabotage.
- Adds advisory autonomy for optional safer, more maintainable or more efficient recommendations without autonomous execution.
- Adds `assistant_ethics_reviews` with prompt hashes rather than raw prompts and advances SQLite schema to 30.
- Adds `ethics status`, `ethics principles`, `ethics review` and `ethics history` plus loopback control-center visibility.
- Fixes reviewed change proposals when a local model returns the exact authorized file as an absolute path; paths outside the project remain rejected.
- Keeps 100 skills, no network attacks, no automatic reporting, no ethics override, no background work and no autonomous actions.

## 0.7.21-alpha

- Added conversational continuity for supervised development sessions, including one focused non-closed session per local chat.
- Added deterministic session guidance with exact next commands for reviewing, applying, validating, repairing or closing a session; guidance never executes an action.
- Added bounded development-session context for local-model replies without exposing tools, approvals, filesystem access or complete validation payloads.
- Fixed change-proposal output so CLI and chat surfaces expose both the proposal ID and the development-session ID immediately.
- Added next-action metadata to the loopback control center and a read-only `assistant session-next` command.
- Advanced the SQLite schema to version 29 with `assistant_chat_session_focus` while retaining 100 registered skills.

## 0.7.20-alpha

- Added persistent supervised development sessions that group an initial reviewed change, its application, validation cycles, repair proposals and outcomes in one ordered timeline.
- Added automatic session linkage for new change proposals and later validation or repair events without changing the approval boundary of any operation.
- Added CLI commands, loopback control-center history and a read-only API for listing, inspecting and explicitly closing sessions.
- Kept sessions as metadata only: they do not execute skills, apply changes, revalidate, repair, install, use network access or run in the background.
- Advanced the SQLite schema to version 28 while retaining 100 registered skills.

## 0.7.19-alpha

- Added supervised validation cycles linked to an already applied change proposal and a frozen allowlisted action plan.
- Added separate single-use approval for validation, persistent real results and explicit passed, failed or partial cycle states.
- Added repair proposals grounded in bounded failed-validation evidence while keeping the model isolated from tools, approvals and direct filesystem writes.
- Added CLI, chat approval and loopback control-center visibility for validation and repair cycles.
- Kept automatic repair, recursive loops, background execution, commits, installers, network access and unapproved writes disabled.
- Advanced the SQLite schema to version 27 while retaining 100 registered skills.

## 0.7.18-alpha

- Added controlled local-model change proposals for one to three exact UTF-8 project files.
- Added frozen SHA-256 snapshots, bounded unified diffs and single-use owner approval before writing.
- Added same-directory atomic replacement per file, stale-proposal detection, best-effort rollback and permission preservation.
- Added CLI and loopback control-center history for proposed, applied, rejected, stale and failed changes.
- Kept deletion, renaming, directory creation, secret files, symlinks, network, installers and arbitrary commands blocked.
- Advanced the SQLite schema to version 26 while retaining 100 registered skills.

## 0.7.17-alpha

- Added supervised assistant action plans that connect explicit conversational requests to at most four allowlisted inspection or validation skills.
- Added strict plan validation, frozen single-use approval, sequential fail-fast execution, bounded result context and optional local-model synthesis grounded only in real skill results.
- Added persistent action-run history, audit events, CLI commands, loopback control-center visibility and a dedicated read-only API endpoint.
- Preserved direct deterministic skill routing while keeping file writes, arbitrary commands, installations, network access and background autonomy disabled.
- Advanced the SQLite schema to version 25 while retaining 100 registered skills and all existing toolchains.

## 0.7.16-alpha

- Added a controlled SQL and SQLite toolchain for deterministic inspection, static query validation, migration review, read-only schema metadata and safe query-plan analysis.
- Added default rejection of DDL/DML outside migrations, destructive migration checks and detection of sensitive SQL constructs without applying statements.
- Added SQLite `mode=ro`, `query_only` and authorizer protections; schema inspection does not read user rows and query planning only accepts one read-only SELECT/CTE.
- Added SQL project profiles, comparable verification history, deterministic routing, CLI commands, control-center visibility and API endpoints.
- Added the optional Alexandria package for SQL, databases, migrations, SQLite, indexes, transactions, backups and trust boundaries.
- Advanced the SQLite schema to version 24, registered 100 skills and updated README, SECURITY, CONTRIBUTING and release documentation.

## 0.7.15-alpha

- Added a controlled Dart and Flutter project toolchain for deterministic project inspection, descriptor validation, verify-only formatting, static analysis and approved tests.
- Added fixed `dart format`, `dart analyze`, `flutter analyze --no-pub`, `dart test` and `flutter test --no-pub` argument sets without automatic Pub resolution, builds or code generation.
- Added Dart/Flutter project profiles, comparable verification history, deterministic routing, CLI commands, local control-center visibility and API endpoints.
- Added the optional Alexandria package for Dart, Flutter, Pub descriptors, analysis, testing and execution boundaries.
- Advanced the SQLite schema to version 23, registered 94 skills and updated README, SECURITY, CONTRIBUTING and release documentation.

## 0.7.14-alpha

- Added a controlled Swift project toolchain for deterministic project and SwiftPM manifest inspection, direct syntax checks, verify-only formatting, builds and approved tests.
- Added fixed SwiftPM arguments, disabled automatic dependency resolution, temporary scratch/cache directories and explicit warnings that manifests, plugins and tests may execute project code.
- Added Swift project profiles, comparable verification history, deterministic routing, CLI commands, local control-center visibility and API endpoints.
- Added the optional Alexandria package for Swift, SwiftPM, Xcode metadata, formatting, builds, tests and execution boundaries.
- Advanced the SQLite schema to version 22, registered 87 skills and updated README, SECURITY, CONTRIBUTING and release documentation.

## 0.7.13-alpha

- Added a controlled C#/.NET project toolchain for deterministic project and solution inspection, MSBuild descriptor validation, formatting checks, builds and approved tests.
- Forced `--no-restore` for executable .NET stages, applied defensive proxy environment restrictions and directed .NET 8+ build artifacts to temporary directories outside the project.
- Added .NET project profiles, comparable verification history, deterministic routing, CLI commands and control-center visibility.
- Added the optional Alexandria package for C#, .NET, MSBuild, NuGet, formatting, builds, tests and execution boundaries.
- Advanced the SQLite schema to version 21, registered 80 skills and updated README, SECURITY, CONTRIBUTING and release documentation.

## 0.7.12-alpha

- Changed the user-facing development version convention from `.dev0`/`-dev` to the clearer `-alpha` label; Python package metadata may normalize it to the PEP 440 form `0.7.12a0`.
- Added a controlled Kotlin/JVM project toolchain for deterministic inspection, descriptor validation, direct `kotlinc`, offline Maven/Gradle builds and approved tests.
- Added Kotlin project profiles, comparable verification history, deterministic routing and control-center visibility.
- Added the optional Alexandria package for Kotlin/JVM, Gradle Kotlin DSL, Ktor, Compose, Android and execution boundaries.
- Kept project wrappers disabled, direct compiler output temporary and managed-project classpaths authoritative.
- Advanced the SQLite schema to version 20 and updated README, SECURITY, CONTRIBUTING and release documentation.

## 0.7.11-dev

- Added a controlled Rust project toolchain for deterministic Cargo manifest inspection, rustfmt checks, cargo check, Clippy and approved tests.
- Added offline and locked Cargo execution with temporary target directories and no automatic toolchain, component or crate installation.
- Added Rust project profiles, comparable verification history, deterministic routing and control-center visibility.
- Added the optional Alexandria package for Cargo, workspaces, formatting, Clippy, tests, build scripts and execution boundaries.
- Advanced the SQLite schema to version 19 and updated README, SECURITY, CONTRIBUTING and release documentation.

## 0.7.10-dev

- Added a controlled Go project toolchain for deterministic module inspection, `gofmt -d`, `go vet`, builds and approved tests.
- Added offline Go execution with temporary caches, readonly module settings and local toolchain enforcement.
- Added Go project profiles, comparable verification history, deterministic routing and control-center visibility.
- Added the optional Alexandria package for Go modules, formatting, vet, builds, tests and execution boundaries.
- Advanced the SQLite schema to version 18 and updated README, SECURITY, CONTRIBUTING and release documentation.

## 0.7.9-dev

- Added a controlled Ruby project toolchain for deterministic inspection, descriptor checks, `bundle check`, `ruby -c`, RuboCop and approved tests.
- Added Ruby project profiles, comparable verification history, control-center APIs and deterministic routing for incomplete Ruby requests.
- Added project-local Ruby binstub detection while refusing `bundle install`, `bundle update`, Rake tasks and arbitrary commands.
- Added the optional Alexandria package for Ruby, Bundler, RuboCop, RSpec, Minitest and common framework boundaries.
- Advanced the SQLite schema to version 17 and updated README, SECURITY, CONTRIBUTING and release documentation.

## 0.7.8-dev

- Added a controlled C and C++ project toolchain for inspection, descriptor checks, direct compiler syntax validation, cppcheck, CMake builds and CTest.
- Added C/C++ project profiles, comparable verification history, control-center APIs and an optional Alexandria knowledge package.
- Added temporary CMake build directories, fixed compiler arguments, bounded output and explicit approval without executing Make or Meson automatically.
- Fixed Java verification for Maven and Gradle projects by skipping raw `javac` by default when the build system owns the dependency classpath.
- Clarified that explicit `javac` remains available for standalone projects or deliberate low-level checks.
- Updated README, SECURITY, CONTRIBUTING and release documentation for native projects and the Java classpath correction.

## 0.7.7-dev

- Added a controlled Java/JVM project toolchain for deterministic inspection, descriptor validation, `javac`, Maven and Gradle verification.
- Added Java project profiles, comparable verification history and control-center APIs without granting execution permissions.
- Added offline Maven and Gradle execution with fixed arguments while explicitly refusing project wrappers such as `mvnw` and `gradlew`.
- Added `javac -proc:none` compilation into temporary output directories to avoid running annotation processors or modifying projects.
- Added the repository-shipped optional Alexandria package for modern Java fundamentals.
- Updated README, SECURITY, CONTRIBUTING and release documentation for the Java/JVM toolchain.

## 0.7.6-dev

- Added a controlled Python project toolchain for deterministic inspection, `pyproject.toml` validation, syntax compilation, Ruff, mypy and Pytest.
- Added Python project profiles, comparable verification history and control-center visibility without granting execution permissions.
- Added project-local virtual-environment tool resolution and bounded, approval-based execution without shell or automatic installation.
- Added the first repository-shipped optional Alexandria package for modern Python fundamentals.
- Updated README, SECURITY, CONTRIBUTING and release documentation for the Python toolchain and package workflow.

## 0.7.5-dev

- Added controlled ESLint and Stylelint skills with project-local binaries preferred over global tools.
- Added deterministic Angular, Vite, workspace, lockfile and frontend configuration checks.
- Extended web project profiles with framework presets, linter stages and validated config paths.
- Added local creation and export of optional Alexandria packages from CLI and the web control center.
- Backfilled release documentation and updated README, SECURITY and CONTRIBUTING guidance.

## 0.7.4-dev

- Added controlled HTML, CSS, JavaScript and TypeScript project verification.
- Added deterministic routing for incomplete PHP and web verification requests.
- Added reusable web verification history and project profiles.
- Added manifest-based optional Alexandria packages with checksum and path validation.

## 0.7.3-dev

- Added complete PHP project inspection, syntax scanning and deterministic verification pipelines.
- Added comparable verification history, reports and per-stage results.
- Added fail-fast, required-tool and bounded project-scan settings.

## 0.7.2-dev

- Added the local web control center for trusted projects, profiles, verification history and audit.
- Added persistent PHP and web project profiles without granting execution permissions.
- Added local visibility for optional Alexandria packages.

## 0.7.1-dev

- Centralized skill authorization decisions and one-time project approvals.
- Added trusted-project management, skill planning and inspectable audit records.
- Added single-use web approvals to prevent duplicate execution.

## 0.7.0-dev

- Added controlled PHP syntax, Composer, PHPStan and PHPUnit skills.
- Added explicit approval, normalized paths, bounded output, timeouts and execution audit.
- Added single-file authorization for readable PHP files outside persistent roots.
- Kept Composer, PHPStan and PHPUnit restricted to persistent or one-time project authorization.

## 0.6.3-dev

- Added evidence-first Alexandria answers that can bypass the language model when local evidence is sufficient.
- Added section-based Markdown indexing and automatic local reindexing.
- Added extractive synthesis with citations and zero model-generation time for strict queries.

## 0.6.2-dev

- Added Alexandria query planning v2, domain specialization and controlled local streaming.
- Added deterministic blocking of unsupported review claims and smaller evidence contexts.
- Added phase timing and bounded continuation behavior.

## 0.6.1-dev

- Added multiple-question detection, dynamic output budgets and strict Alexandria mode.
- Added reviewed-source priority, visible citations and separated findings, risks and checks.

## 0.6.0-dev

- Added Alexandria libraries, sources, units and FTS5 search.
- Added local provenance, licensing, language, version and review metadata.
- Added library editing, enable/disable controls and permanent deletion.

## 0.5.4-dev

- Added deterministic extraction and validation for common document and source formats.
- Added document diagnostics and controlled reprocessing.

## 0.5.3-dev

- Added the local memory, episode, proposal, correction and audit inspector.
- Added owner-controlled editing and deletion workflows.

## 0.5.2-dev

- Added secure local attachments, bounded extraction and web interface refinements.
- Added attachment management and validation status.

## 0.5.1-dev

- Added durable web chat management, pins, archive, restore and deletion.
- Added persistent chat search and transcript controls.

## 0.5.0-dev

- Added the loopback-only local web chat interface.
- Added persistent chat containers backed by the same application core as the CLI.

## 0.4.2-dev

- Added structured summaries, episodes, semantic proposals and corrections in SQLite.
- Added owner approval before semantic memory promotion.

## 0.4.1-dev

- Improved conversational continuity and persistent chat recall.
- Added bounded context selection across durable conversations.

## 0.4.0-dev

- Added persistent chat containers and contextual memory.
- Added optional compressed transcripts and local chat lifecycle management.

## 0.3.5-dev

- Added canonical deterministic responses and completed the 0.3 persona and language line.
- Improved consistency between owner identity, persona and generated answers.

## 0.3.4-dev

- Added tolerant exit aliases and typo hints for interactive chat.
- Added a visible processing indicator and per-response elapsed time.
- Kept Ollama warm only during an active chat and explicitly released it on exit.
- Reduced retrieved memories, documents, history size and total context budget.
- Added configurable gender identity, pronouns, personality, tone, formality and verbosity.
- Added `persona setup`, `/identity` and `/personality`.
- Added memory architecture and project status checklists.
- Added five tests; the suite now contains 49 tests.

## 0.3.3-dev

- Added a canonical, owner-editable persona configuration in private `persona.toml`.
- Added `persona status` and `persona init` commands.
- Injected the canonical Elyndra identity into every language-model request.
- Added dependency-free retrieval query variants and deduplication across memory and documents.
- Added bounded, process-only conversation history to interactive chat.
- Added `/persona` and `/clear` interactive commands.
- Kept session history out of SQLite and limited it to six truncated turns.
- Added five tests; the suite now contains 44 tests.

## 0.3.2-dev

- Added dependency-free language detection for major writing systems and common Latin languages.
- Added persistent `auto` and fixed response-language modes in private `language.toml`.
- Added `language status`, `language set` and `language detect` commands.
- Added multilingual natural-language switching, including commands written in Chinese.
- Added local model-assisted translation with `elyndra translate --to`.
- Passed the selected response language to all language-engine adapters.
- Added language metadata to generated JSON results and audit records.
- Documented speech recognition as a later isolated adapter rather than part of the text model.
- Added five language tests; the suite now contains 39 tests.

## 0.3.1-dev

- Added a loopback-only Ollama adapter using the local `/api/chat` endpoint.
- Added `model ollama-list` and `model configure-ollama`.
- Enforced `keep_alive = 0` so conversational models unload after each response.
- Added explicit local-only endpoint validation and blocked redirects.
- Added model provenance fields for license, runtime/teacher role and redistribution review.
- Added generation metrics to audit records and JSON results.
- Preserved `llama-cli` as an interchangeable backend.
- Added project principles for future offline and explicit online modes.
- Added five Ollama adapter tests; the suite now contains 34 tests.

## 0.3.0-dev

- Added local runtime and GGUF discovery with `elyndra model discover`.
- Added a private, separate `~/.config/elyndra/language.toml` file.
- Added `model configure`, `model status`, `model test` and `model disable` commands.
- Added conservative `eco`, `normal` and `work` inference profiles.
- Added the first `llama-cli` language adapter using one process per generated response.
- Added runtime feature probing so optional flags are only sent when supported.
- Added local memory and knowledge retrieval before language generation.
- Added support for the Spanish query form `¿Qué sabes sobre ...?`.
- Kept deterministic skills ahead of model fallback and models isolated from tools.

## 0.2.1-dev

- Fixed all Ruff findings reported after the 0.2 development bootstrap.
- Added forced document reindexing with `knowledge import --force`.
- Improved knowledge excerpts so results are centered around matched terms.
- Aligned overlapping chunks to word boundaries to avoid fragments beginning mid-word.
- Updated the high-risk policy message to the current 0.2 release line.

## 0.2.0-dev

- Added an interactive local `elyndra chat` session.
- Added project inspection and project text search.
- Added safe line-range file reading.
- Added local knowledge import with SHA-256 provenance, chunking and FTS5 search.
- Added combined searches over personal memory and imported knowledge.
- Added SQLite schema migration from version 1 to version 2 without deleting existing data.
- Expanded the deterministic Spanish router and the default skill registry.

## 0.1.0-dev

- Initial local deterministic core.
# 0.8.8-alpha — Phase 4

- Endurece formas flexionadas, deduplicación semántica, POS, emoji y lenguaje informal.
- Añade rutas léxicas deterministas compartidas por chat, CLI y web.
- Añade bundles españoles reproducibles, particionables, verificables y transaccionales.
- Normaliza la presentación final en español por lema/POS y las prioridades del bundle a
  Informal 400, Wikcionario 300, MCR/OMW 250 y CLDR 200.
