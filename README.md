# Elyndra

## Controlled Online Gateway — sanitized HTTP failures

Future final HTTP rejections expose only status/class, normalized final host, bounded redirect
count/hosts and `resumable=false`. Bodies, headers, URLs/queries, IPs, TLS details, cookies, tokens
and ETags are never recorded. Empty initial partials without strong ETags are removed; schema is 50.

**Elyndra** is a source-available, local-first framework for building a private personal agent.
Its memory, permissions, tools and identity belong to the person running it.

> Status: `0.8.10-alpha` — local assistant with a deny-by-default controlled HTTPS gateway validated
> only against deterministic loopback dependencies in Phases 3 and 4.

## Controlled Online Gateway — Phase 4

Phase 4 completes the supervised official-pack acquisition path. Elyndra accepts only its closed,
pinned official descriptor, downloads the manifest and assets through the Phase 3 transport, parses
the bundle offline, and requires a new exact approval for local installation. Downloading never
installs or enables a pack. There is no generic browser, arbitrary URL, authentication, scheduler,
autoupdate, telemetry, or bundle execution. Loading the loopback web UI starts no network activity;
foreground download execution remains CLI-only.

The post-Phase-4 activation fix keeps persistent networking invalid and adds an offline deterministic
plan preview. `online approve-download` persists only the exact reviewed plan; a later explicitly
approved CLI execution consumes a process-local capability bound to that operation. Web, chat, models,
planners, tutors and skills cannot obtain it.

## Controlled Online Gateway — Phase 3

Phase 3 adds a foreground-only hardened HTTPS transport, pinned DNS resolution, redirect checks,
bounded HTTP parsing, streaming verification, safe partial/cache/quarantine storage, strong-ETag
resume, cancellation and a global lock. Constructing Elyndra, selecting Online or listing a source
never opens a socket. Execution still needs global enablement, account Online mode, an immutable plan
and a fresh single-use approval.

Validation uses controlled loopback test dependencies only. The public Spanish Core release has not
been contacted; its pack repository is currently not anonymously accessible, so real acquisition
remains blocked. There is no remote installation, scheduler, autoupdate or telemetry.

## Controlled Online Gateway — Phase 2 planning foundation

Elyndra now models restricted online consent, immutable pinned-source plans, single-use approvals,
state and privacy-safe auditing. Both the installation gateway switch and account Online mode
default to off. Enabling Online never starts traffic: Phase 2 has no network transport and every
approved download plan ends as `gateway_transport_unavailable`.

```bash
./scripts/elyndra-dev online status
./scripts/elyndra-dev online mode-set online --approve
./scripts/elyndra-dev online sources
./scripts/elyndra-dev online plan-download elyndra-official-language-packs --approve
```

The official language-bundle source is an immutable local descriptor. It is planned only; there is
no download, installation, auto-update or telemetry. See `docs/ONLINE_GATEWAY.md`.


## Local accounts, isolated identity and dialogue

Elyndra supports multiple local accounts per installation. Registration requires a unique username, unique email, password confirmation and an adult birth date. Passwords are stored only as Argon2id hashes; the birth date stays local and age is calculated when needed. A separate preferred name answers “¿Cómo quieres que te llame?”. Optional pronouns, sex, gender identity and sexual orientation remain absent when blank and are not added to model context.

Each account receives an isolated SQLite vault and private data/state directories. Chats, memories, documents, goals, organizer items, wellbeing records, learned preferences, knowledge and automation state never share a database between accounts. The installation registry stores only account/session metadata and the path of each vault. Legacy personal data is copied only to the first account created after migration.

After registration, CLI and web require a local session. User mode hides Alejandría, Control, schema/runtime diagnostics and development tools at both the interface and HTTP authorization layers; developer mode exposes them after an explicit warning and can be changed from Profile. The current profile is the only conversational identity: Elyndra must address the person directly and never name the developer as a third party.

Clarification state is stored briefly and per chat, allowing a follow-up such as `mis objetivos xfa` to answer a preceding `¿bienestar, objetivos o coaching?`. Capability questions are answered from actual features instead of generic model advice.

```bash
./scripts/elyndra-dev account register --username usuario --email persona@example.test \
  --birth-date 1990-01-01 --approve
./scripts/elyndra-dev account login usuario
./scripts/elyndra-dev account profile
./scripts/elyndra-dev account security
```

Encrypted local export is available in Profile and CLI. Remote backup, network delivery of telemetry and active 2FA are intentionally not implemented in this release. See `docs/IDENTITY_AND_DIALOGUE.md`.

## Semantic understanding

Elyndra resolves natural variants into a bounded canonical intent before generic model fallback. Local rules and reviewed examples handle common personal requests; uncertain personal language may be classified by a local tutor that receives no tools, memory, SQLite access, permissions or authority. The tutor returns strict JSON and never answers the request itself. Elyndra then reads the relevant local repository and produces a grounded response. Ambiguous requests produce one concrete clarification. Repeated tutor use can create a pending learning proposal, but only explicit owner review activates a new phrase. See `docs/SEMANTIC_UNDERSTANDING.md`.

```bash
./scripts/elyndra-dev assistant understand "como esta mi animo hoy"
./scripts/elyndra-dev assistant intent-status
./scripts/elyndra-dev assistant intent-learning-proposals --status all
```

## Cognitive executive

Elyndra now wraps normal assistant requests in a deterministic executive layer. It records intent, route, risk, multidimensional confidence, applied context IDs and the observed outcome without storing the raw prompt or private chain of thought. Relevant knowledge is budgeted and weak lexical overlap is excluded before a model receives context. Explicit goals, dependent tasks and outcome verifications are durable records, but they never execute or progress automatically. See `docs/COGNITIVE_EXECUTIVE.md`.

## Personal organizer

Elyndra stores commitments, birthdays and recurring routines locally in SQLite. Recurrences are calculated only for the requested date window, so the organizer does not materialize an unbounded calendar or load full histories into RAM. Routine check-ins are explicit, organizer items may link to cognitive-executive goals and tasks, and daily briefs can answer common agenda questions without Ollama.

Reminder creation is deliberately split into proposal and review. An approved reminder is eligible to appear in an on-demand brief, but it does not create a daemon, cron entry, desktop notification, message or background task.

```bash
./scripts/elyndra-dev assistant organizer-status
./scripts/elyndra-dev assistant daily-brief
./scripts/elyndra-dev assistant commitment-create --title "Cita" --date 2026-08-10 --approve
./scripts/elyndra-dev assistant routine-create --title "Caminar" --start-date 2026-08-03 --recurrence daily --approve
./scripts/elyndra-dev ask "¿Qué tengo hoy?"
```

See `docs/PERSONAL_ORGANIZER.md`.


## Web and CLI parity

CLI and web are equal interfaces over the same `ElyndraApplication` runtime. A
deterministic route available in CLI must be reachable through web chat with the
same repository, memory, knowledge, organizer and safety checks. The web page,
HTTP headers and streamed response metadata expose the exact runtime version so
a stale server process can be identified immediately after an upgrade.

The Personal workspace provides local agenda and wellbeing views plus explicitly
confirmed forms for commitments, routines, birthdays and check-ins. Web writes
use the same repositories as CLI and never imply background execution.

```bash
./scripts/elyndra-dev web
# Open the loopback URL and confirm Elyndra 0.8.8-alpha before testing.
```

See `docs/INTERFACE_PARITY.md`.

## Personal coaching and wellbeing

Elyndra can store bounded owner check-ins for mood, energy, stress, focus, sleep,
hydration, nutrition and activity. It produces deterministic summaries and
reviewed coaching plans with explicit actions. These records support reflection
and organization; they do not diagnose, prescribe treatment or replace medical,
mental-health or nutrition professionals. No plan advances and no action executes
automatically.

```bash
./scripts/elyndra-dev assistant wellbeing-checkin --date 2026-08-02 \
  --mood 3 --energy 3 --stress 2 --focus 4 --approve
./scripts/elyndra-dev assistant wellbeing-summary --days 7
./scripts/elyndra-dev ask "¿Cómo he estado esta semana?"
```

See `docs/PERSONAL_COACHING.md`.

## Reviewed general knowledge

Elyndra can now turn explicit owner teaching, reviewed local text or reviewed Alejandría units into a bounded knowledge proposal. Planning invokes no model. A separately approved foreground run may ask a local tutor to synthesize the evidence and an optional auditor to challenge it. Promotion is another explicit action. Active knowledge is checked before Ollama fallback, while previous versions remain permanently traceable as `superseded`. Model confidence accepts controlled numeric, percentage and qualitative formats without trusting arbitrary labels. Failed plans can be retried only as new approvals, potential conflicts require explicit review, and knowledge due for revalidation remains preserved but is withheld from direct operational answers. See `docs/GENERAL_KNOWLEDGE.md`.

Development sessions provide one persistent timeline for an initial reviewed proposal, its approved application, validation plans, real results and any later repair proposal. The chat can now recover the focused non-closed session, attach a bounded session summary to local-model replies and show deterministic next actions. This continuity is context only: it never grants permissions, executes a suggestion or broadens the files and project already approved.

## Principles

1. Local first.
2. Offline by default.
3. No telemetry.
4. Personal data never belongs in the Git repository.
5. Models are optional and replaceable.
6. High-impact actions require explicit approval.
7. Every action is auditable.
8. The owner can inspect and delete memories and imported documents.

## Policy-bounded automation

Elyndra can schedule a small allowlist of local personal-assistant actions under explicit owner-reviewed policies. Policies bind one action, one autonomy level, a timezone, optional time window, daily limit and optional expiry. Schedules are calculated on demand and materialized only when the owner runs the foreground dispatcher from CLI or web.

```bash
./scripts/elyndra-dev assistant automation-status
./scripts/elyndra-dev assistant automation-policies --status all
./scripts/elyndra-dev assistant automations --status all
./scripts/elyndra-dev assistant automation-scan --approve
./scripts/elyndra-dev assistant automation-inbox --status unread
```

`execute_under_policy` is standing authorization only for the incorporated low-risk local actions. It never grants network access, skills, shell, file writes or authority outside the named action. `execute_with_approval` creates a pending run that must be approved separately. The Personal web workspace exposes the same policies, schedules, runs and local inbox through the same repositories. See `docs/POLICY_BOUNDED_AUTOMATION.md`.

## Optional local scheduler

Elyndra can keep the approved automation dispatcher active only when the owner explicitly starts a local scheduler. The CLI scheduler stays in the foreground until `Ctrl+C`; the web scheduler lives only inside the current loopback web process and stops when that process closes. An exclusive lock under the private state directory prevents two schedulers from running at once.

```bash
./scripts/elyndra-dev assistant scheduler-status
./scripts/elyndra-dev assistant scheduler-cycle --approve
./scripts/elyndra-dev assistant scheduler-run --interval-seconds 60 --approve
./scripts/elyndra-dev assistant local-notifications --status pending
```

Scheduler sessions, heartbeats and notifications are persisted in SQLite. Notifications can be printed by the foreground CLI or displayed by the browser while the Personal page is open and permission is granted. Elyndra does not install systemd units, cron entries or detached daemons, and does not deliver through network services. See `docs/LOCAL_SCHEDULER.md`.

## Constitutional ethics

Elyndra evaluates an immutable local ethics policy before routing, supervised planning, reviewed changes and language-model fallback. It protects human safety, privacy, professional integrity, systems and the environment. Prompts, profiles, imported knowledge, models and owner approvals cannot disable the no-harm core.

Refusals are neutral and private: Elyndra does not shame or automatically report the user. Ethics review v3 handles explicit emergencies, self-harm, homicide, child endangerment or exploitation, sexual violence, child sexual abuse material, malicious cyber activity, fraud, privacy abuse, coded high-risk language and sabotage before model fallback. Ambiguous requests may receive a bounded secondary review from the local tutor; that review can increase caution but never weaken a deterministic block.

`[ethics].proactive_advice` controls only optional professional suggestions. `[ethics].tutor_review` controls secondary local review of ambiguous requests. Ollama and future models remain language tutors without policy authority.

```bash
./scripts/elyndra-dev ethics status
./scripts/elyndra-dev ethics principles
./scripts/elyndra-dev ethics review "request to review"
./scripts/elyndra-dev ethics history
```

## Supervised local tutor arbitration

Elyndra can classify language tasks and select among explicitly configured local tutors. The deterministic core remains authoritative for ethics, evidence, memory, permissions, approvals and skills. Models receive no tools or authority. Selection prefers reproducible local benchmark results and otherwise keeps the primary configured model. Benchmarks run only after explicit approval, sequentially and in the foreground; they store scores, latency and hashes rather than raw prompts or generated text.

```bash
./scripts/elyndra-dev model tutor-status
./scripts/elyndra-dev model tutor-template
./scripts/elyndra-dev model tutor-recommend translation
./scripts/elyndra-dev model tutor-benchmark --approve
./scripts/elyndra-dev model tutor-selections
```

Optional tutors are configured in `~/.config/elyndra/tutors.toml` and must use loopback Ollama or an explicit local llama-cli/GGUF pair. No model is downloaded automatically. See `docs/TUTOR_ARBITRATION.md`.

### Owner-reviewed tutor lessons

Elyndra can retain compact guidance for one exact tutor/task pair, but every lesson starts as a proposal and has no effect until the owner approves it explicitly. Active lessons are bounded, source-attributed and separate from general memory, preferences and Alejandría. They can expire or be forgotten. Confidence calibration keeps the raw benchmark score visible and combines it conservatively with approved observations by task and provenance; it is not a general-intelligence or safety score.

```bash
./scripts/elyndra-dev model tutor-learning-status
./scripts/elyndra-dev model tutor-lesson-proposals
./scripts/elyndra-dev model tutor-lessons
./scripts/elyndra-dev model tutor-evidence-comparisons
```

No lesson trains or rewrites a model, authorizes an unbenchmarked external tutor, grants tools or runs in the background. See `docs/TUTOR_LEARNING.md`.

### Supervised evolution and durable knowledge

Elyndra can evaluate an approved lesson against the same incorporated case with and without that lesson. The plan is inert until a separate approval runs it once in the foreground. An optional local auditor may review the transient baseline and candidate outputs, but its verdict is advisory and can only make the deterministic recommendation more conservative.

A successful evaluation can be promoted explicitly into Elyndra's durable task knowledge. Promotion is never automatic. Knowledge is versioned, source-attributed and available independently of the tutor that helped produce it. Updating knowledge creates a superior linked version and marks the older version `superseded`; the previous content and provenance are not deleted.

```bash
./scripts/elyndra-dev model tutor-lesson-evaluation-plan ID_LECCION --approve
./scripts/elyndra-dev model tutor-lesson-evaluation-run ID_EVALUACION --approve
./scripts/elyndra-dev model tutor-knowledge-promote ID_EVALUACION --title "Título" --approve
./scripts/elyndra-dev model tutor-knowledge
./scripts/elyndra-dev model tutor-calibration-show --tutor primary --task translation
```

Auditors are configured locally with `role = "auditor"` and `auditor_allowed = true`. They are never candidates for normal user replies. See `docs/TUTOR_EVOLUTION.md`.

## Local translation before model fallback

Elyndra now recognizes common translation requests directly in CLI and web chat. Known dictionary entries, exact phrases, templates and installed Alejandría language packs are resolved locally before the language model is considered. Chinese and Japanese templates can include stored romanization. The local layer is intentionally honest: it is not yet a complete grammar or universal translator, and unknown complex text may fall back to the configured local model.

```bash
./scripts/elyndra-dev translate "perro" --to en
./scripts/elyndra-dev ask "como puedo decir hola me llamo Carlos en chino"
```

## Local emergency first aid

Elyndra ships six source-attributed offline first-aid cards for severe bleeding, severe trouble breathing, an unresponsive person who is not breathing normally, adult or child choking, thermal burns, and poison or corrosive exposure. Emergency detection bypasses Ollama latency and gives immediate actions while emergency professionals are contacted. Reviewed locale-specific `first_aid.topic` packs can now be inspected and installed explicitly through Alejandría; unreviewed medical packs are rejected. The bundled library remains a starter, not a complete medical manual or substitute for practical training.

```bash
./scripts/elyndra-dev first-aid status
./scripts/elyndra-dev first-aid lookup "sangrado grave" --language es
```

## Tiered durable memory

Hot memory is a bounded in-process cache, warm memory searches recent SQLite episodes and cold memory searches approved memories plus a provenance-preserving index of older episodes. Elyndra does not load the entire database into RAM or silently promote detected preferences.

```bash
./scripts/elyndra-dev memory tiers
./scripts/elyndra-dev memory tier-recall "consulta" --project proyecto
```

## Offline multilingual starter lexicon

Elyndra 0.8.8 adds separately licensed Spanish lexical packs under the shared Alejandría data
directory. Packs are built from manually supplied local files, installed disabled, verified before
enablement and queried through read-only SQLite. WordNet-LMF/MCR, structured Wiktionary JSONL,
project-authored informal JSONL and optional CLDR annotations have separate adapters. No real dataset
is bundled or approved by the source repository.

Private corrections are proposals inside the active account vault. They have no effect before review,
never modify a shared pack and never cross account boundaries.

```bash
./scripts/elyndra-dev alexandria language-pack-inspect /ruta/al/pack --approve
./scripts/elyndra-dev alexandria language-pack-install /ruta/al/pack --approve
./scripts/elyndra-dev alexandria language-pack-list
./scripts/elyndra-dev dictionary lookup frío --language es
```

Elyndra ships a small deterministic lexicon for Spanish, English, Japanese, Chinese, Italian, French, Portuguese and German. It is loaded locally from versioned package data, includes license and SHA-256 metadata and does not call Ollama or the network. Elyndra can additionally inspect and install disk-backed monolingual, bilingual, morphology and dialect packages with source-level provenance. The bundled lexicon remains a bootstrap and is not presented as a complete dictionary, grammar system or full translation engine.

```bash
./scripts/elyndra-dev dictionary status
./scripts/elyndra-dev dictionary languages
./scripts/elyndra-dev dictionary lookup agua --language es --output-language en
./scripts/elyndra-dev alexandria structured-inspect /ruta/paquete
./scripts/elyndra-dev alexandria structured-install /ruta/paquete --approve
./scripts/elyndra-dev alexandria structured-show ID_PAQUETE
```

## What works

- Local owner identity tied to the operating-system user.
- SQLite memory with full-text search when FTS5 is available.
- Project registry, inspection and source-code text search.
- Safe line-range reading of authorized text files.
- Local knowledge import for text, Markdown and source code.
- SHA-256 document provenance, chunking, reimport detection and searchable citations.
- Combined searches over personal memory and imported knowledge.
- Interactive `elyndra chat` session.
- Audit trail, immutable ethics review v3, emergency fast paths and explicit risk policy.
- 102 deterministic local skills, including disk-backed structured dictionary/dialect and reviewed first-aid lookup plus controlled PHP, frontend, Python, Java/JVM, Kotlin/JVM, C#/.NET, C/C++, Ruby, Go, Rust, Swift, Dart/Flutter and SQL/SQLite toolchains.
- Supervised assistant plans that bind an exact request to at most four allowlisted inspection or validation steps, require explicit approval, execute once and preserve an auditable result history.
- Reviewable change proposals for one to three explicitly selected UTF-8 project files, with frozen hashes, unified diffs, single-use approval and same-directory atomic replacement per file.
- Supervised validation-and-repair cycles that link an applied change to a frozen validation plan, store real results and permit a new repair proposal only after a failed or partial validation.
- Conversational development-session continuity that remembers the focused session per chat, displays proposal and session IDs together and suggests exact next commands without executing them.
- Discovery of existing `llama-cli`, `llama-server`, Ollama processes and GGUF files.
- Optional `llama-cli` adapter with `eco`, `normal` and `work` profiles.
- Durable chat containers, structured summaries, episodic memory and reviewed semantic memory.
- Explicitly approved Alejandría structured-pack installation with per-source SHA-256, license, review and attribution metadata.
- A loopback-only web interface with chat history, search, local conversation and a control center.

Elyndra does not download models, enable remote network access or expose the interface beyond `127.0.0.1`.

## Requirements

- Linux.
- Python 3.11 or newer.
- SQLite supplied by Python.
- Optional: a compatible `llama-cli` binary and an instruction-tuned GGUF model.
- Optional validation tools: PHP, Composer, PHPStan, PHPUnit, Node.js, TypeScript, ESLint, Stylelint, Ruff, mypy, Pytest, Java, javac, Kotlin, kotlinc, Maven, Gradle, .NET SDK 8+, GCC, Clang, CMake, CTest, cppcheck, Ruby, Bundler, RuboCop, RSpec, Go, gofmt, Rust, Cargo, rustfmt, Clippy, Swift, swiftc, swift-format, Dart, Flutter and VS Code. SQL and SQLite validation use Python’s built-in SQLite support.

## Controlled development toolchains

Elyndra separates knowledge from execution:

```text
Alexandria explains.
Skills inspect, execute and verify.
Importing knowledge never grants execution permission.
```

PHP verification supports controlled project inspection, `php -l`, Composer validation, PHPStan,
PHPUnit and comparable verification history. Frontend verification supports internal HTML and CSS
checks, `node --check`, `tsc --noEmit`, ESLint, Stylelint and deterministic Angular/Vite metadata
inspection. Python verification supports metadata-only inspection, deterministic `pyproject.toml`
validation, syntax compilation without imports or bytecode writes, Ruff, mypy and Pytest. Java/JVM
verification supports metadata-only Maven/Gradle inspection, descriptor validation, `javac -proc:none`,
offline Maven/Gradle builds and approved tests without executing project wrappers. Managed Java
projects skip raw `javac` by default because Maven or Gradle owns the dependency classpath. Kotlin/JVM
verification supports metadata-only Maven/Gradle inspection, controlled `kotlinc`, offline managed builds
and approved tests. Managed Kotlin projects skip raw `kotlinc` by default because Maven or Gradle owns
the dependency classpath and compiler plugins. C#/.NET verification supports deterministic solution and
MSBuild descriptor inspection, `dotnet format --verify-no-changes`, build and tests with `--no-restore`,
defensive proxy environment restrictions and .NET 8+ artifact output directed to temporary folders outside the project.
C/C++ verification supports metadata-only CMake inspection,
GCC/Clang syntax checks, cppcheck, temporary
CMake builds and CTest without executing Make or Meson automatically. Ruby verification supports
metadata-only Gemfile and gemspec inspection, `bundle check`, `ruby -c`, RuboCop without autocorrection
and approved RSpec or Minitest execution. Go verification supports deterministic module checks,
`gofmt -d`, offline `go vet`, builds and approved tests with temporary caches. Rust verification
supports deterministic Cargo manifest inspection, `cargo fmt --check`, offline and locked `cargo check`,
Clippy without fixes and approved tests with a temporary target directory. Swift verification supports
metadata-only SwiftPM and Xcode inspection, `swiftc -parse`, verify-only `swift-format` linting and
approved SwiftPM builds/tests with automatic dependency resolution disabled and temporary scratch paths.
Dart/Flutter verification supports metadata-only `pubspec.yaml` and `analysis_options.yaml` inspection,
verify-only `dart format`, controlled `dart analyze` or `flutter analyze --no-pub`, and approved Dart or
Flutter tests without automatic package resolution. SQL/SQLite verification supports static query and migration review, read-only schema metadata inspection and SELECT-only `EXPLAIN QUERY PLAN`; it never applies SQL or reads user rows during schema inspection. Project-local tools such as `vendor/bin/phpstan`, `node_modules/.bin/eslint` and `.venv/bin/ruff` are preferred. Elyndra never
runs arbitrary shell commands, npm scripts, `npx`, package builds or automatic installers.

Useful commands:

```bash
./scripts/elyndra-dev php help
./scripts/elyndra-dev webdev help
./scripts/elyndra-dev pythondev help
./scripts/elyndra-dev javadev help
./scripts/elyndra-dev kotlindev help
./scripts/elyndra-dev dotnetdev help
./scripts/elyndra-dev nativedev help
./scripts/elyndra-dev rubydev help
./scripts/elyndra-dev godev help
./scripts/elyndra-dev rustdev help
./scripts/elyndra-dev swiftdev help
./scripts/elyndra-dev dartdev help
./scripts/elyndra-dev sqldev help
./scripts/elyndra-dev skill list
./scripts/elyndra-dev skill plan php.verify_project --params '{"path":"/ruta/proyecto"}'
```

Project access can come from configured roots, explicitly trusted projects or one-time approval.
Profiles store safe defaults but never grant authorization by themselves.

## Supervised assistant orchestration

Elyndra can now connect a conversational request with controlled project inspection and validation:

```text
request → bounded plan → exact approval → sequential skills → real results → optional local-model explanation
```

The planner accepts an explicit authorized path and proposes no more than four allowlisted steps. The
model may suggest a strict JSON plan, but Elyndra validates every skill name, parameter and path before
showing approval. The approved plan is frozen, single-use and auditable; cancellation executes nothing.
Results are bounded and sanitized before an optional local language model explains them. File writing is a
separate reviewed flow: the owner selects one to three exact files, the model proposes complete replacement
text without tools, Elyndra freezes hashes and a unified diff, and a second approval applies that proposal once.
After a change is applied, the owner may create a separate validation cycle. Failed or partial results may be supplied as bounded evidence for a new repair proposal, but validation, repair generation and repair application each remain separate owner-approved steps. Deletion, renaming, directory creation, installers, network access, arbitrary commands and background or recursive loops remain blocked.

```bash
./scripts/elyndra-dev assistant status
./scripts/elyndra-dev assistant help
./scripts/elyndra-dev assistant plan \
  'Revisa el proyecto Python /ruta/proyecto y explícame los problemas'
./scripts/elyndra-dev assistant run ID_DE_VISTA_PREVIA --approve
./scripts/elyndra-dev assistant history
./scripts/elyndra-dev assistant change-plan /ruta/proyecto \
  --file src/app.py --instruction 'Corrige el error'
./scripts/elyndra-dev assistant change-show ID
./scripts/elyndra-dev assistant change-apply ID --approve
./scripts/elyndra-dev assistant changes
./scripts/elyndra-dev assistant validate-plan ID_CAMBIO \
  --request 'Ejecuta Ruff y Pytest en /ruta/proyecto'
./scripts/elyndra-dev assistant validate-run ID_CICLO --approve
./scripts/elyndra-dev assistant repair-plan ID_CICLO \
  --instruction 'Corrige solo los fallos observados'
./scripts/elyndra-dev assistant cycles
```

## Alexandria knowledge packages

Alexandria supports small, optional, manifest-based knowledge packages. A domestic installation can
keep only basic knowledge while the owner installs programming, cooking, gardening or other domains
separately. Packages are local folders with SHA-256 checksums; installation does not use the network,
execute code, install dependencies or mark sources as reviewed.

```bash
./scripts/elyndra-dev alexandria package-inspect /ruta/paquete
./scripts/elyndra-dev alexandria package-install /ruta/paquete --approve
./scripts/elyndra-dev alexandria package-create /ruta/destino \
  --package-id programming.web.html \
  --name "HTML — Fundamentos" \
  --version 1.0.0 \
  --domain programming/web/html \
  --license-id CC-BY-4.0 \
  --source /ruta/libro.md \
  --approve
./scripts/elyndra-dev alexandria package-export programming.web.html /ruta/exportado --approve

# Paquete opcional incluido en el repositorio
./scripts/elyndra-dev alexandria package-inspect knowledge-packs/python-modern-basic
./scripts/elyndra-dev alexandria package-inspect knowledge-packs/java-modern-basic
./scripts/elyndra-dev alexandria package-inspect knowledge-packs/kotlin-modern-basic
./scripts/elyndra-dev alexandria package-inspect knowledge-packs/dotnet-modern-basic
./scripts/elyndra-dev alexandria package-inspect knowledge-packs/c-cpp-modern-basic
./scripts/elyndra-dev alexandria package-inspect knowledge-packs/ruby-modern-basic
./scripts/elyndra-dev alexandria package-inspect knowledge-packs/go-modern-basic
./scripts/elyndra-dev alexandria package-inspect knowledge-packs/rust-modern-basic
./scripts/elyndra-dev alexandria package-inspect knowledge-packs/swift-modern-basic
./scripts/elyndra-dev alexandria package-inspect knowledge-packs/dart-flutter-modern-basic
./scripts/elyndra-dev alexandria package-inspect knowledge-packs/sql-databases-modern-basic
```

The local control center is available at `http://127.0.0.1:8765/control`.

## Existing installations

After applying an update:

```bash
cd ~/Proyectos/elyndra
python -m pip install -e '.[dev]'
./scripts/elyndra-dev doctor
ruff check .
pytest
```

Database migrations are automatic and non-destructive. Memories, projects, knowledge and audit
records remain under the same XDG directories.

## New installation

```bash
cd ~/Proyectos/elyndra
./scripts/elyndra-dev init --owner TU_NOMBRE --system-user "$(whoami)"
./scripts/bootstrap.sh
source .venv/bin/activate
elyndra doctor
```

The Python runtime itself has no third-party dependencies.

## Discover an existing local model installation

```bash
./scripts/elyndra-dev model discover
```

The command searches conservative local roots for:

- `llama-cli`
- `llama-server`
- Ollama
- active Llama, Ollama or Nomad-related processes
- existing `.gguf` files

It does not download, execute or enable a model.

Restrict discovery to a known location when desired:

```bash
./scripts/elyndra-dev model discover \
  --root "$HOME/Proyectos" \
  --root /opt
```

## Configure the first language engine

Use the paths reported by discovery:

```bash
./scripts/elyndra-dev model configure \
  --binary /ruta/a/llama-cli \
  --model /ruta/al/modelo.gguf \
  --profile eco \
  --approve
```

Elyndra writes a separate private file:

```text
~/.config/elyndra/language.toml
```

Check it:

```bash
./scripts/elyndra-dev model status
./scripts/elyndra-dev doctor
```

Run a controlled test:

```bash
./scripts/elyndra-dev model test "Preséntate en una frase."
```

Disable generation without deleting the model:

```bash
./scripts/elyndra-dev model disable --approve
```

### Resource profiles

```text
eco     3 threads, 2K context, 160 output tokens
normal  4 threads, 4K context, 256 output tokens
work    6 threads, 8K context, 512 output tokens
```

Models larger than 2 GiB are rejected by `model configure` unless the owner explicitly supplies
`--allow-large-model`. Start with `eco`.

## How language fallback works

```text
owner request
    ↓
deterministic router
    ├── known command → execute explicit skill without loading a model
    ├── direct local question → return memory and knowledge directly
    └── open-ended question
            ↓
      retrieve relevant local context
            ↓
      run llama-cli once
            ↓
      return answer and terminate the model process
```

The model receives text only. It cannot invoke skills, inspect arbitrary files, read secrets or run
commands.

## Local web interface

Start the private local interface:

```bash
./scripts/elyndra-dev web
```

Elyndra opens `http://127.0.0.1:8765/` and provides:

- searchable local chat history;
- full or summary-only persistence for new chats;
- a functional conversation view backed by the same `ElyndraApplication` as the CLI;
- visible processing state and response duration;
- no CDN, remote fonts, telemetry or LAN binding.

Choose another local port or avoid opening the browser automatically:

```bash
./scripts/elyndra-dev web --port 8890 --no-open
```

The server uses an ephemeral write token, validates the local `Host` header, sends a strict Content
Security Policy and releases the language engine when the process closes.

## Interactive session

```bash
./scripts/elyndra-dev chat
```

Inside the session:

```text
/status
/model
Inspecciona el proyecto elyndra
Busca KnowledgeRepository en el proyecto elyndra
¿Qué sabes sobre equipos modestos?
Explícame con tus palabras qué objetivo tiene Elyndra.
```

## Project awareness

Register the Elyndra repository itself:

```bash
./scripts/elyndra-dev project add \
  elyndra \
  "$HOME/Proyectos/elyndra" \
  --approve
```

Inspect and search it:

```bash
./scripts/elyndra-dev project inspect elyndra
./scripts/elyndra-dev project search elyndra KnowledgeRepository
```

Read selected lines:

```bash
./scripts/elyndra-dev file read \
  "$HOME/Proyectos/elyndra/src/elyndra/application.py" \
  --start-line 1 \
  --end-line 120
```

## Local knowledge

Import and search a document:

```bash
./scripts/elyndra-dev knowledge import \
  "$HOME/Proyectos/elyndra/README.md" \
  --project elyndra \
  --approve

./scripts/elyndra-dev knowledge search "privacidad local"
```

Documents are copied as text fragments into the private SQLite database. Their original path,
SHA-256 hash, size and project association remain available for provenance.

## Runtime data

Personal data remains outside the repository:

```text
~/.config/elyndra/config.toml
~/.config/elyndra/language.toml
~/.local/share/elyndra/elyndra.db
~/.local/state/elyndra/
~/.cache/elyndra/
```

You can isolate a development instance with:

```bash
export ELYNDRA_HOME="$PWD/.elyndra"
./scripts/elyndra-dev init --owner TU_NOMBRE --system-user "$(whoami)"
```

`.elyndra/` and `models/` are ignored by Git.

## Security boundary

Elyndra does **not** provide a general shell skill. Commands are executed only through explicit,
reviewable skills using argument arrays (`shell=False`). File and knowledge operations are restricted
to configured allowed roots. PHPStan, PHPUnit, mypy plugins and Pytest can load or execute project
code and therefore always require owner approval. Python syntax compilation does not import modules or
write bytecode. Process output and runtime are bounded; network access is requested off in the child
environment but is not a kernel-level network sandbox.

The language model is disabled by default and receives no skill context. Do not grant this pre-alpha
project `sudo`, root access, SSH keys, browser profiles or production credentials.

PHP and web tool runs default to a 120-second timeout and 12,000 output characters. Python tool
runs default to 180 seconds and 12,000 output characters. Existing configurations can override these
under `[skills.php]`, `[skills.web]` and `[skills.python]`.

## Repository map

```text
src/elyndra/
├── application.py       dependency wiring, retrieval and fallback
├── audit.py             local action log
├── cli.py               commands and interactive chat
├── config.py            primary TOML configuration
├── db.py                SQLite schema and migrations
├── dictionary.py        offline starter lexicon and exact lookup
├── ethics.py            immutable primary review and tutor arbitration
├── identity.py          local owner check
├── knowledge/           document import, chunks and search
├── memory/              memories and projects
├── models/              discovery, profiles and private model config
├── policy/              risk and approval decisions
├── resources/           versioned packaged local lexical data
├── router.py            deterministic Spanish router
├── tutors.py            local tutor registry, benchmarks and arbitration
├── tutor_learning.py    reviewed lessons and conservative calibration
├── engines/             replaceable language adapters
├── web/                 loopback HTTP server and packaged local UI
└── skills/              explicit local capabilities
```

## License

Elyndra's public license is the PolyForm Noncommercial License 1.0.0
(`PolyForm-Noncommercial-1.0.0`). It permits use, study, modification and distribution for the
purposes allowed by that license. Commercial exploitation is not granted automatically; separate
commercial licenses or written permissions may be available from the copyright holder.

The canonical terms are in [LICENSE](LICENSE), the required notices are in [NOTICE.md](NOTICE.md), and
the separate-permission model is summarized in
[COMMERCIAL-LICENSING.md](COMMERCIAL-LICENSING.md). Model weights, imported documents, datasets and
other third-party material retain their own licenses and notices.
# elyndra-project


## Ollama local adapter (0.3.1)

Inspect an already-running loopback API:

```bash
./scripts/elyndra-dev model ollama-list
## Development version naming

Active development releases use the human-facing form `0.x.y-alpha` and Git tags such as `v0.x.y-alpha`. Python packaging tools normalize that prerelease label to the PEP 440 equivalent `0.x.ya0`; Elyndra itself continues to display the readable `-alpha` form.

### Development-session history

New reviewed change proposals start a session automatically and the CLI reports both the change-proposal ID and the development-session ID. An older stored proposal can be adopted without executing anything through `assistant session-start CHANGE_ID`; use `assistant sessions`, `assistant session-show SESSION_ID` and `assistant session-next SESSION_ID` to inspect the timeline and deterministic next actions. The chat can keep one focused non-closed session per conversation so questions such as “¿qué sigue?” recover context without executing anything. Closing a session requires explicit confirmation and does not execute any skill.


## Constitución ética local

Elyndra aplica un filtro constitucional antes del router, los planes, las propuestas de cambios y el motor lingüístico. El núcleo protege seguridad humana, privacidad, integridad profesional, sistemas y ambiente. No puede desactivarse ni ser reemplazado por prompts, perfiles, conocimiento, modelos o instrucciones del propietario.

Las negativas son neutrales: Elyndra no reprocha ni denuncia automáticamente. Cuando una solicitud facilitaría daño, fraude, robo de credenciales, intrusión no autorizada, vigilancia abusiva, sabotaje o daño ambiental, ofrece alternativas defensivas, legales o preventivas.

La opción `[ethics].proactive_advice` solo controla sugerencias opcionales y `[ethics].tutor_review` la revisión local secundaria de casos ambiguos. Ninguna desactiva el núcleo de no-daño. Ollama y otros modelos siguen siendo tutores generativos sin autoridad sobre permisos o políticas.

Comandos:

```bash
./scripts/elyndra-dev ethics status
./scripts/elyndra-dev ethics principles
./scripts/elyndra-dev ethics review "solicitud a revisar"
./scripts/elyndra-dev ethics history
```
# Paquetes lingüísticos de 0.8.8-alpha

Elyndra funciona sin datasets externos. El núcleo léxico español completo se instala
como bundle local separado, sin descarga automática; consulta
`docs/LANGUAGE_PACK_DISTRIBUTION.md`.

Las consultas humanas agrupan hasta cinco sentidos por lema y categoría gramatical,
traducen las etiquetas al español y consolidan sus fuentes al final.
