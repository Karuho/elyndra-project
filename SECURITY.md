# Security Policy

## Sanitized HTTP rejection diagnostics

Rejected statuses retain no body, reason, headers, full URL/query, IP, TLS material, cookies,
credentials or ETag. Cleanup uses only deterministic local paths and rejects links or non-regular
files; it never retries or opens another network capability.

## Controlled Online Gateway Phase 4

Official pack acquisition is anonymous, HTTPS-only and pinned to the repository-shipped descriptor.
Descriptor, manifest, every asset and the offline parse result are hash-bound across separate download
and install approvals. Archives reject traversal, links, special files, duplicate/case/Unicode
collisions, nested dangerous archives and bounded-size/ratio violations. Installation uses private
staging and the existing rollback-capable registry; it never executes or auto-enables content.
Models, tutors, planners and skills receive no network or installation authority. Web status is
read-only by default and loading or refreshing it causes no outbound traffic.

Persistent configuration cannot enable networking. A read-only preview creates no operation or
approval. Only the CLI execution command can receive a process-local capability bound to the exact
persisted operation and plan; execution consumes a fresh single-use approval. The web API has no
download execution or approval action.

## Controlled Online Gateway Phase 3

Only `online_gateway.transport` and `online_gateway.resolver` may use sockets. Production accepts
HTTPS on port 443, rejects literal/non-global addresses, pins all A/AAAA answers, checks the connected
peer, requires normal certificate and hostname verification with TLS 1.2+, and repeats validation on
each allowlisted redirect. No proxy, cookie, authentication, client certificate or insecure mode is
available.

Responses and files are bounded before persistence. Downloads stream to private, no-follow partials,
then fsync, hash and atomically promote or quarantine. Resume requires a rehashed partial and exact
strong ETag/range response. Startup marks incomplete jobs interrupted without network or automatic
resume. Phase 3 tests use explicit loopback-only dependencies; no public artifact was contacted.

## Controlled Online Gateway Phase 2

Schema 50 separates shared public metadata from private per-account preferences, user-pinned
descriptors and plans. Global and account switches default to false. Online mode is inert; an
immutable plan also requires a fresh single-use approval bound to account, operation and plan hash.

`NetworkPermit` is process-only, rejects serialization and disappears after restart. Models, tutors,
planners and skills receive neither permits nor transport. Phase 2's transport is an unavailable
stub. Audit excludes prompts, bodies, cookies, tokens, query strings and absolute paths. No telemetry
or automatic update check occurs.

## Alpha warning

Elyndra 0.8 is an experimental local personal-assistant platform. Do not run it as root and do not
expose its web interface, model endpoints or private data directories to a network.

## Supported versions

Only the current development branch receives security fixes during alpha development. Owners should apply
updates sequentially and run the documented validation suite before committing them.

## Reporting a vulnerability

Do not open a public issue containing secrets, exploit details or personal data.

When GitHub Private Vulnerability Reporting is enabled for this repository, use the repository's Security tab to report the issue privately. Otherwise, use a public issue only to request a private contact channel and do not include vulnerability details, secrets or personal data in that issue.

## Design rules

### Controlled Online Gateway — 0.8.9-alpha Phase 1

Phase 1 defines architecture only; this repository still performs no remote artifact download at
runtime. The future gateway is denied by default and limited to user-initiated, separately approved
public HTTPS HEAD/GET for immutable descriptors containing an exact URL, size and SHA-256.

Every DNS result and redirect must be revalidated against SSRF and rebinding policy. Environment
proxies, cookies, authentication, credentials, query-bearing URLs, transparent decompression,
automatic redirects, startup traffic, silent retries and automatic resume are prohibited. Streaming
uses one global download lock, a private `.part`, bounded bytes, incremental hashing and atomic rename.

Download approval does not approve installation or enablement. The existing local
`LanguageBundleService` retains inspection, compatibility, archive and rollback authority and receives
no transport dependency. Models, Ollama, tutors, auditors, planners, schedulers and skills receive no
gateway object, permit or network callback. See `docs/ONLINE_GATEWAY_THREAT_MODEL.md`.

### Spanish language-pack boundary — 0.8.8-alpha

Language packs are untrusted local data, never executable extensions. Builders reject symlinks,
oversized records, invalid UTF-8, XML DTD/entities, missing notices and checksum mismatches. Builds are
streaming, transactional, non-resumable and finalized by atomic rename. Installed SQLite is opened
with `mode=ro`, `immutable=1` and `query_only=ON`; corrupt, disabled or unverified packs are excluded.

Shared packs live outside account vaults and contain no personal records. Account overlays use a
strict 16 KiB schema, require proposal review and cannot grant permissions or write shared data.
Imported definitions and examples are never inserted into authority or system instructions. Runtime
download, RAE/Oxford content and automatic tutor promotion remain blocked.

- No telemetry and no mandatory cloud service in the core.
- Loopback-only web and model endpoints by default.
- No general-purpose shell, terminal or arbitrary-command skill.
- Subprocesses use validated argument lists with `shell=False`.
- Explicit approval for medium-risk actions and persistent permission changes.
- File and project paths are normalized before authorization.
- One-time project access expires after the requested execution.
- Trusted projects and profiles are separate: a profile never grants access.
- Tool output, runtime and file counts are bounded.
- Local project tools are preferred; tools are never downloaded or installed automatically.
- npm scripts, `npx`, Composer scripts, Python package builds and unrestricted plugin execution are not used by default.
- Python compilation validates source syntax without importing modules or writing bytecode.
- Ruff, mypy and Pytest use fixed argument sets; Elyndra never invokes pip, tox, nox or build backends automatically.
- Java compilation uses `javac -proc:none` and temporary output directories.
- Maven and Gradle projects skip raw `javac` by default because their build system owns the dependency classpath.
- Maven and Gradle use fixed offline invocations; project wrappers such as `mvnw` and `gradlew` are never executed.
- Kotlin/JVM inspection reads Maven and Gradle descriptors without evaluating Kotlin DSL scripts.
- Direct Kotlin compilation uses fixed `kotlinc` arguments and temporary output; `.kts` scripts are not passed to the direct compiler stage.
- Maven and Gradle Kotlin projects skip raw `kotlinc` by default because the managed build owns classpaths and compiler plugins.
- Kotlin project wrappers are detected but never executed; managed builds use fixed offline invocations and require approval.
- .NET inspection parses project, solution, XML and JSON metadata without executing MSBuild.
- `dotnet format` uses verify-only mode and `--no-restore`; it never rewrites source files automatically.
- .NET build and tests require SDK 8 or newer so all artifacts can be redirected to temporary external directories.
- .NET executable stages use fixed arguments, `--no-restore`, disabled build servers and defensive blocked proxy settings.
- Elyndra never runs `dotnet restore`, `dotnet tool restore`, `dotnet run`, `dotnet publish`, workload installation or arbitrary MSBuild targets automatically.
- C and C++ syntax checks use fixed GCC/Clang arguments without linking or execution.
- CMake builds use temporary directories, bounded processes and disconnected FetchContent settings; Make and Meson are not executed automatically.
- CTest executes project binaries only after explicit approval, and cppcheck uses fixed analysis arguments.
- Ruby syntax checks use `ruby -c`; Bundler is limited to `bundle check` and never installs or updates gems.
- RuboCop runs without autocorrection, while RSpec and Minitest execute only after explicit approval.
- Ruby binstubs are resolved only for an allowlisted tool name; Rake and arbitrary project scripts are not executed.
- Go module inspection is deterministic; `gofmt` runs only in diff mode and never writes files.
- Go vet, build and tests use `GOPROXY=off`, `GOSUMDB=off`, `GOTOOLCHAIN=local`, readonly modules and temporary caches.
- Elyndra never runs `go get`, `go install`, `go generate` or `go mod tidy` automatically.
- Rust manifest inspection parses TOML without executing Cargo, rustc, build scripts or procedural macros.
- Cargo format checks use `cargo fmt --all -- --check` and never rewrite source files.
- Cargo check, Clippy and tests use fixed `--offline` and `--locked` arguments with a temporary target directory.
- Cargo stages may execute `build.rs`, procedural macros or tests and therefore require explicit approval.
- Elyndra never runs `cargo install`, `cargo update`, `cargo fix` or arbitrary Cargo commands automatically.
- Swift inspection reads `Package.swift` as bounded UTF-8 text and never evaluates the manifest during inspection.
- Direct Swift syntax checks use `swiftc -parse`; formatting uses `swift-format lint --strict` and never rewrites files.
- SwiftPM build and tests disable automatic resolution, use fixed arguments and temporary scratch/cache directories outside the project.
- SwiftPM can evaluate manifests and execute plugins or tests, so executable stages require approval and are not presented as a complete sandbox.
- Elyndra never runs dependency update/resolve commands, Xcode builds, Swift scripts or automatic Swift toolchain installation.
- Dart/Flutter inspection parses `pubspec.yaml` and `analysis_options.yaml` as bounded YAML data without executing Pub or project code.
- Dart formatting uses verify-only output and never rewrites files; analysis and tests use fixed arguments and no automatic `pub get`.
- Elyndra never runs `dart pub get`, `flutter pub get`, Pub upgrades, `dart run`, Flutter builds, code generators or automatic SDK installation.
- Dart/Flutter analyze and tests may load dependencies or execute project code; proxy restrictions are defensive and not a complete network sandbox.
- SQL files are parsed statically; Elyndra does not execute DDL, DML or migrations from the SQL toolchain.
- Mutating SQL outside recognized migrations and destructive migrations are rejected by default; sensitive constructs such as ATTACH, extension loading and writable schema changes remain blocked.
- SQLite schema inspection uses URI `mode=ro`, `PRAGMA query_only` and an authorizer that denies write, DDL, attachment and transaction actions.
- SQLite schema inspection reads catalog and PRAGMA metadata only; it does not count or return user rows. Query planning accepts one SELECT/CTE and executes only `EXPLAIN QUERY PLAN`.
- SQL profiles may change static validation policy but never grant project access or permission to execute database statements.
- Assistant action plans are limited to four allowlisted inspection or validation steps; model output never grants a skill, path or parameter.
- The approval token is bound to the exact request and frozen plan, expires, is single-use and cannot be reused after cancellation or execution.
- CLI execution requires a previously stored preview ID; the stored plan is validated again and can transition from planned to running only once.
- Supervised orchestration remains sequential and foreground-only; it cannot write project files, install dependencies, access the network or invoke arbitrary commands.
- Only bounded, sanitized results from actually executed skills may be returned to the optional language model for explanation.
- Optional Alexandria packages are checksum-verified, path-contained and installed without executing
  code or granting permissions.
- Personal data, local databases, model files, tokens and generated private packages remain outside
  Git.
- Audit details redact common secret fields and do not store complete source-file contents.


Elyndra 0.7.21-alpha adds a per-chat focus record and bounded conversational context for development sessions. Session focus is not authorization: it cannot approve a proposal, run a validation, apply a repair, widen a project root or persist one-time access. Suggested actions are inert text and structured metadata until the owner invokes the existing reviewed command and approval boundary.

The session context supplied to the optional local model contains identifiers, project root, objective, the latest persisted event summary and at most four supervised commands. It does not contain tools, approval tokens, secret material, full arbitrary files or authority to execute. Closing a session clears its focused-chat mappings.

## Controlled toolchains

PHPStan, PHPUnit, ESLint, Stylelint, mypy, Pytest, kotlinc, Maven, Gradle, dotnet format, MSBuild, .NET tests, CMake, CTest, Bundler, RuboCop, RSpec, Minitest, Go vet, Go tests, Cargo, Clippy, Rust tests, SwiftPM, Swift plugins, Swift tests, Dart/Flutter analyzers and tests, and project configuration files may load code or
plugins from an authorized project. They therefore require explicit approval even when they are used
only for verification. Pytest executes project tests, and mypy may load configured plugins. Owners
should inspect unfamiliar repositories before trusting them persistently.

## Controlled file changes


Elyndra 0.7.20-alpha adds development-session metadata that links existing reviewed proposals, validation cycles and repairs into a local timeline. A session is not an authorization scope and cannot execute, approve, write, install, access the network or continue work in the background. Every linked change and validation retains its own exact approval and one-use boundary.

Elyndra 0.7.19-alpha allows project-file writes only through stored, reviewable change proposals. The local model receives bounded text for explicit files and returns data; it does not receive a filesystem handle, skill executor or approval capability. Elyndra independently validates paths, file types, hashes and the complete diff before a single-use approval can apply the proposal.

The writing boundary rejects symbolic links, traversal, dependency and VCS internals, common secret files, private-key material, deletion, renaming and directory creation. Current hashes are checked again immediately before replacement. Writes use same-directory temporary files and rollback on multi-file failure. This is controlled local writing, not a sandbox and not autonomous software maintenance.

## Supervised validation and repair cycles

A validation cycle may start only from an applied change proposal. Elyndra freezes an allowlisted plan tied to the same project, requires a new single-use approval before executing it and stores bounded results from the real skills. A failed or partial validation may support a new repair proposal for the original explicit files, but the model receives only bounded result data and has no tools or approval capability.

Validation never triggers repair automatically. Repair generation, repair application and any later revalidation are separate foreground actions requiring new owner decisions. Cycles cannot recurse, run in the background, install dependencies, access the network, commit code or widen the original project boundary.

## Constitutional ethics boundary — 0.7.22-alpha

Elyndra 0.7.22-alpha evaluates a deterministic constitutional policy before routing a natural-language request to plans, change proposals, repair proposals or the configured language model. The core cannot be disabled by configuration, imported knowledge, owner prompts or a model response. It blocks explicit facilitation of malicious intrusion, credential theft, privacy abuse, fraud, physical harm, self-harm, environmental harm and sabotage, then returns neutral defensive alternatives without automatic reporting.

The owner remains the local administrator, but owner approval does not override harm boundaries for third parties. Elyndra may preserve data integrity and refuse sabotage, but it must not resist an authorized shutdown, correction, deletion or replacement. `[ethics].proactive_advice` controls optional professional recommendations only; it never disables the core.

Ethics review persistence stores a SHA-256 digest of the request and structured decision metadata, not the raw prompt. The model receives a bounded constitutional context but no ability to edit that policy, grant permissions, access the filesystem or execute a suggested action.

## Ethics review v2 and tutor boundary — 0.7.23-alpha

Elyndra classifies explicit self-harm or crisis, violence or homicide, child sexual abuse material, malicious cyber activity, privacy abuse, fraud, sabotage, environmental harm and ambiguous concealment before any router, supervised action or language-model fallback. Unknown wording is no longer automatically labeled safe. Explicit deterministic blocks do not call the model.

Only an ambiguous request may receive a bounded secondary review from the configured local language engine. The tutor receives no tools, approvals, filesystem capability or policy authority. It may raise the risk level but cannot lower a deterministic restriction. If the tutor is unavailable, malformed or uncertain, Elyndra fails closed and returns neutral safe alternatives. Ethics history stores hashes and structured review metadata rather than raw prompts.

The local dictionary is static package data. Lookup uses no network, model, dynamic code execution or silent download. The shipped data is a starter lexicon and must not be represented as complete linguistic coverage.

## Ethics v3, emergency guidance and tiered memory — 0.7.24-alpha

High-risk classification must not depend solely on one exact phrase. Regression tests must cover normalized repeated letters, contextual child-safety and violence signals, coded terms, ordinary benign contexts and tutor failure. Medical emergencies use local reviewed cards before model fallback. First-aid data must remain source-attributed, versioned, bounded and explicit about its incomplete scope.

Tiered memory must keep hot RAM bounded, preserve source provenance during cold consolidation, avoid raw-query telemetry and never convert an unreviewed preference into trusted durable memory. Dictionary packs require explicit local installation, license metadata and source hashes.

## Structured knowledge-pack boundary — 0.7.25-alpha

Elyndra structured packs are local data, not executable extensions. Inspection reads a bounded JSON manifest and JSONL sources, verifies regular files, rejects symlinks and path escapes, checks every declared SHA-256, validates record schemas and preserves source-level attribution. It performs no network request and executes no package code.

Installation, replacement, enable, disable and removal require explicit owner actions. A package cannot grant skills, permissions, project access, network access or model authority. Installed data remains in a private versioned Alejandría directory and SQLite indexes; lookup caches are bounded and never load a complete external dictionary into RAM.

Medical packages require reviewed status, reviewer, review date and locale. Unreviewed first-aid data is rejected. Review metadata is provenance, not a medical guarantee: guidance must retain source references, limitations and the emergency-professional boundary.

## Preference learning and translation

Preference observations are proposals, not authorization. They require explicit owner approval before becoming durable semantic memory, and can expire or be forgotten. Translation fast paths are read-only and local; model fallback receives text but no skills, approvals, filesystem access or policy authority. Emergency first-aid routing bypasses model latency when a reviewed local card matches.

## Local tutor arbitration boundary — 0.7.27-alpha

Tutor arbitration does not expand Elyndra's authority boundary. The deterministic core owns ethics, memory, evidence, permissions, approvals and skill execution. Tutors receive no tools, filesystem handles, secrets, approval capability or self-modification path. Ollama endpoints must remain HTTP loopback; llama-cli tutors require explicit local executable and GGUF paths. Remote endpoints and unreviewed teacher roles are rejected.

Normal generation invokes one selected tutor, not hidden parallel models. Benchmarks are explicit foreground actions and use only incorporated prompts. Selection history stores a SHA-256 of the prompt, task class, candidates, selected tutor, latency and fallback status; it does not store the raw prompt. Benchmark results store output hashes and metrics, not raw generated text. A selected tutor failure may fall back to the primary model, and that fallback is recorded.
## Reviewed tutor-learning boundary — 0.7.28-alpha

Tutor lessons are reviewable local records, not model training, policy updates or new permissions. A proposal cannot affect context, selection, memory, preferences or Alejandría until the owner approves that exact proposal. Approval is not reusable. Active lessons apply only to the named tutor/task pair, inject at most four bounded context items and can expire or be forgotten.

Provenance is limited to owner feedback, reviewed evidence or deterministic evidence. Persistence stores compact lesson text, source hashes, bounded references, scores and review metadata; deterministic comparisons store tutor-output and evidence SHA-256 values rather than raw prompts or generated output. Confidence calibration remains task-specific, preserves the raw benchmark score and must not be described as universal intelligence, factuality or safety certification.

Lessons cannot authorize an unbenchmarked external tutor, weaken constitutional ethics, grant tools, access files, modify models, promote memory or preferences silently, download data, use remote endpoints or continue in the background. Deterministic evidence comparisons create pending proposals only.


## Supervised tutor evolution and durable knowledge — 0.7.29-alpha

Lesson evaluation is foreground-only, exact-plan and single-use. Creating a plan invokes no model. Running it compares the same built-in case against a baseline context and a candidate context, then stores scores, latency, structured evaluator metrics and SHA-256 hashes. Raw prompts and model outputs are not persisted.

A configured auditor is local and advisory. It receives only the bounded evaluation material during the foreground run, has no skills, filesystem, memory, approval tokens or policy authority, and is excluded from normal tutor selection. Its verdict cannot promote knowledge or weaken a deterministic regression; it may only make the recommendation more conservative.

Durable knowledge requires a completed conservative evaluation and a separate explicit owner approval. It is stored with lineage, version, source lesson, source evaluation, model fingerprint and provenance. There is no knowledge-delete operation. Replacement requires a functionally superior validated version; the previous version remains stored as `superseded` and linked to its successor. Imported or learned knowledge never becomes permission, policy authority or model weight.

No evaluation, audit, promotion, replacement, benchmark or revalidation runs in the background. No model is trained, fine-tuned, downloaded or automatically updated.

## Reviewed general knowledge acquisition — 0.7.30-alpha

General-knowledge plans freeze bounded evidence before any model call. Evidence is treated as untrusted data and cannot grant permissions. Tutors and auditors receive no filesystem handles, skill registry, approval tokens, secrets, network access or durable-write authority. Planning must execute zero model calls; running consumes one pending plan once and stays in the foreground.

Deterministic evidence audit is mandatory. A configured auditor is advisory and may only make promotion more conservative. Promotion is a distinct owner-approved action. There is no delete operation for durable general knowledge. A replacement must preserve kind and topic, use different content, retain or improve validated confidence and record an explicit reason while the prior version remains `superseded`.


## Knowledge governance — 0.7.31-alpha

- Qualitative model confidence is accepted only from a bounded multilingual map and is converted conservatively; arbitrary labels fail closed.
- Failed acquisition approvals are never reused. A retry creates a new plan with fresh model fingerprints.
- Exact duplicate knowledge is rejected. Potential conflicts are preserved as auditable records and require explicit resolution.
- Revalidation never deletes knowledge. Due units remain historical but are excluded from direct answers and normal tutor context.
- Conflict resolution, supersession and revalidation do not grant tools, permissions, network access or policy authority to tutors or auditors.

## Multisource knowledge and cross-auditor review — 0.7.32-alpha

Approved kind, subject and locale are frozen before any model call. A tutor cannot erase or replace those fields; mismatches are retained in proposal provenance. Local evidence packages are bounded to eight sources, 24,000 combined characters and 256 KiB for the JSON container. Every source keeps an independent SHA-256 and attribution.

Multiple auditors run sequentially and only after explicit configuration. Aggregate review is conservative and cannot turn a stricter result into a more permissive one. Auditors receive no tools, filesystem handles, secrets, approval tokens, network authority or promotion capability.

Project-scoped knowledge is withheld from global retrieval. Domain and project labels affect ranking and conflict scope only; they never authorize project access or execution.

## Cognitive executive — 0.8.0-alpha

The cognitive executive stores request hashes and structured routing metadata,
not raw prompts or private chain of thought. It cannot bypass constitutional
ethics, authorization policy, single-use approvals or knowledge review. Goal and
task records do not authorize execution. Outcome verification is explicit and
cannot silently mark an action successful.

## Personal organizer — 0.8.1-alpha

Organizer data is private local data. Commitments, birthdays, routines,
check-ins and reminder proposals remain in SQLite and are never sent to a
network service by the organizer.

A reminder approval does not create a cron entry, systemd timer, desktop
notification, email, message or background worker. It only marks the reminder
as eligible to appear in an on-demand daily brief. Any later notification
transport must be a separate policy-controlled capability.

Recurrence windows, text fields, evidence notes and result counts are bounded.
An organizer item linked to a goal or task cannot automatically complete or
advance that goal. Health, nutrition and wellbeing routines are organizational
records, not diagnoses or treatment instructions.

## Web/CLI parity and wellbeing — 0.8.2-alpha

- The web chat and CLI must call the same `ElyndraApplication.ask` runtime. No
  web-only model fallback may bypass deterministic organizer, knowledge, ethics,
  memory, translation, first-aid or wellbeing routes.
- The web page, `X-Elyndra-Version` response header and message metadata expose
  the active runtime version. After an upgrade, owners must restart the web
  process and verify the displayed version before relying on new behavior.
- Personal web writes require an explicit confirmation and an `approved=true`
  request field. Viewing a page or sending a chat message grants no write approval.
- Wellbeing check-ins and coaching plans are sensitive local data. They remain in
  SQLite, are returned only in bounded windows and are never sent to a remote
  service by the core.
- Wellbeing summaries are deterministic observations, not diagnoses, treatments,
  prescriptions or crisis interventions. Ethics and emergency routes remain ahead
  of wellbeing summaries and language-model fallback.
- Coaching plans do not execute actions, contact people, schedule notifications,
  alter medication, change nutrition automatically or progress in the background.
- JavaScript changes require syntax validation and real HTTP parity tests in
  addition to repository/service tests.
## Policy-bounded automation — 0.8.3-alpha

Automation policies are explicit local records, not general delegation. Each policy binds exactly one incorporated low-risk action, one autonomy level, a timezone, optional time window, maximum daily runs and optional lifetime. Policies and schedules require owner confirmation. `forbidden` policies cannot be activated, and action parameters reject common secret fields.

The dispatcher is foreground-only in this release. It does not install a daemon, cron entry, systemd unit, webhook or long-running thread. Occurrences are bounded to a seven-day catch-up window, materialized idempotently and limited per policy/day. `execute_with_approval` creates a pending run and executes nothing until a separate confirmation. `execute_under_policy` may prepare only local organizer, wellbeing, coaching, goal or routine summaries already named by the policy.

Automation cannot invoke skills, shell commands, files, project tools, model downloads, network requests, external notifications or permission changes. Results are stored in a bounded local SQLite inbox; they are not pushed to the operating system. Pausing or revoking a policy prevents future operational runs without deleting historical runs or results. CLI and web call the same repository and both reject writes without explicit confirmation.

## Optional local scheduler and notifications — 0.8.4-alpha

The scheduler is opt-in and process-bound. CLI mode stays attached to the terminal; web mode runs only inside the current loopback web process. Elyndra does not install cron jobs, systemd units, login autostart entries, detached daemons or external notification services.

A private `0600` lock file under the Elyndra state directory is held with an exclusive non-blocking operating-system lock. A second CLI or web scheduler fails closed instead of racing the existing process. Every scheduler session stores its PID, mode, interval, heartbeat, scan counts, result counts and terminal status in SQLite. Ctrl+C, web shutdown and explicit web stop request a clean stop and release the lock.

The scheduler can only call the existing policy-bounded automation dispatcher. It cannot add policies, widen an action scope, invoke skills, execute shell commands, read or write project files, install software, use the network or bypass per-run approval. Local notifications are copies of already prepared inbox results, remain bounded in SQLite and expose only `pending`, `seen` or `dismissed` states. Browser notifications require browser permission and work only while the local web interface is open. No notification is sent to a third party.

## Semantic understanding and tutor-assisted intent resolution — 0.8.5-alpha

Semantic resolution runs after constitutional ethics and before generic language-model fallback. A local deterministic resolver may read only the bounded ontology and reviewed phrase examples. When local confidence is insufficient, a selected local tutor receives the user text, allowed intent names and local candidate scores, but no SQLite rows, memories, tools, filesystem access, permissions, approvals or action authority. The tutor must return strict JSON and cannot answer the request directly.

Resolution records store a SHA-256 of the normalized message, canonical intent, bounded entities, confidence, source and outcome. They do not store the raw message. Explicit learning proposals may preserve a normalized phrase so the owner can review it. Repeated tutor resolutions may only create a pending proposal; they never activate an example, execute an action or modify knowledge automatically. Ethics, crisis and first-aid routes remain authoritative and precede semantic dispatch.

## Local accounts and identity boundaries (`0.8.6-alpha` / `0.8.7-alpha`)

- Passwords are never stored or logged; Argon2id hashes with per-password salts are used.
- Username and email are unique within an installation, and either identifier may be used for login.
- Session tokens are random, only their hashes are persisted, and browser cookies are HttpOnly and SameSite=Strict.
- Each account receives an isolated SQLite vault plus account-specific data, state and cache directories. Personal repositories are opened only after resolving the authenticated account.
- The installation registry stores credentials, consent/session metadata and vault mapping; it must not store account chat or memory content.
- Legacy personal data is cloned once into the first account vault. Additional accounts start empty and cannot see the legacy or another account's data.
- A new web login revokes the previous web session for this local runtime to prevent accidental cross-account tabs; CLI sessions remain explicit and scoped.
- Developer mode is authorization, not decoration. User-mode requests to Control and Alejandría endpoints return 403 even if the route is entered manually.
- Registration remains available for additional isolated accounts. Removing the old single-account index without vault isolation is prohibited.
- Local password reset remains restricted to the same operating-system user and revokes prior sessions. Remote recovery and 2FA activation remain unavailable.
- Online mode is not implemented in this release. The Local/Online UI must not imply network access or silently enable it.
# Bundles lingüísticos locales

Los bundles son entrada no confiable: se rechazan enlaces, traversal, tipos especiales,
límites excedidos, hashes incorrectos e incompatibilidad. La extracción ocurre en un
directorio temporal privado y nunca concede autoridad ni acceso de red.
