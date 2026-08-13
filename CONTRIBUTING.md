# Contributing

## Public contribution policy

Issues, bug reports, ideas, discussions, and responsible security reports are welcome.

During Elyndra's initial public alpha, substantial external code contributions require prior coordination with the project maintainer while the project's long-term contribution and rights policy is being finalized. This does not restrict forks or other uses permitted by Elyndra's public license.


## HTTP rejection tests

Use injected transport or loopback only. Prove rejected bodies stay unread, empty non-resumable
partials disappear, useful resumable partials remain, and public DNS/connections stay blocked.

## Controlled Online Gateway contributions

Phase 2 contributions must remain transport-free. Do not add socket or HTTP-client imports under
`src/elyndra/online_gateway`, automatic traffic, retries, downloads, installation or enablement.
Preserve deny-by-default switches, immutable exact plans, the existing approval and audit stores,
account isolation and 102 skills. Gateway tests must fail any socket attempt and need no Internet.

Elyndra is pre-alpha. Contributions must preserve its local-first, privacy-first and owner-controlled
principles.

## Development

```bash
./scripts/bootstrap.sh
source .venv/bin/activate
python -m pip install -e '.[dev]'
python3 -m compileall -q src tests
ruff check .
pytest
git diff --check
```

When JavaScript changes and Node.js is available:

```bash
node --check src/elyndra/web/static/app.js
```

## Rules

- Add tests for every behavior or security-policy change.
- Do not add telemetry, mandatory cloud services or implicit network access.
- Do not create a generic shell, terminal or unrestricted file-access skill.
- New skills must declare a risk level, validate all parameters and document their permissions.
- Use subprocess arguments as lists and keep `shell=False`.
- Do not accept unrestricted flags from user text.
- Do not download or install tools automatically.
- Python validation must not invoke pip, package builds, tox, nox or arbitrary project commands.
- Python syntax checks must compile source without importing modules or writing bytecode.
- Ruff, mypy and Pytest integrations must use validated, fixed argument sets and bounded output.
- Java integrations must not execute project wrappers or accept arbitrary Maven/Gradle tasks.
- `javac` validation must disable annotation processing and write only to temporary directories.
- Maven and Gradle integrations must use offline mode, fixed goals/tasks and bounded output.
- Managed Maven/Gradle verification must not treat raw `javac` without the build classpath as a mandatory default stage.
- Kotlin integrations must not execute project wrappers or evaluate Gradle Kotlin DSL during inspection.
- Direct `kotlinc` checks must use fixed arguments, compile only `.kt` sources and write output to temporary directories.
- Managed Maven/Gradle Kotlin projects must treat their build as authoritative and skip raw `kotlinc` by default.
- .NET integrations must inspect project and solution metadata without evaluating MSBuild during inspection.
- .NET formatting must use verify-only mode and must never apply changes automatically.
- .NET build and tests must use `--no-restore`, fixed arguments, disabled build servers and external temporary artifact paths.
- Do not execute `dotnet restore`, `dotnet tool restore`, `dotnet run`, `dotnet publish`, workload installation or arbitrary MSBuild targets automatically.
- C/C++ integrations must use fixed compiler arguments, temporary build directories and bounded output.
- Do not execute Make, Meson or arbitrary CMake targets automatically.
- CMake and CTest execution requires explicit approval because project configuration and tests can execute code.
- Ruby integrations must not execute `bundle install`, `bundle update`, Rake tasks or arbitrary binstubs.
- `ruby -c`, `bundle check`, RuboCop, RSpec and Minitest must use fixed arguments, approval and bounded output.
- RuboCop integrations must never apply autocorrections automatically.
- Go integrations must keep module resolution offline and readonly, use temporary caches and fixed argument lists.
- Go formatting checks must use `gofmt -d`; never run `gofmt -w`, `go get`, `go install`, `go generate` or `go mod tidy` automatically.
- Rust integrations must parse `Cargo.toml` without execution before invoking Cargo.
- Cargo commands must use offline, locked, fixed argument sets and temporary target directories.
- Rust formatting checks must use check mode; never run `cargo fmt` as a writer or invoke `cargo fix`.
- Do not run `cargo install`, `cargo update` or arbitrary subcommands automatically.
- Document that Cargo check, Clippy and tests may execute build scripts or procedural macros.
- Swift integrations must inspect `Package.swift` as data before invoking SwiftPM and must not execute Xcode projects automatically.
- Swift syntax checks must use `swiftc -parse`; format checks must use lint/check mode and never rewrite source files.
- SwiftPM commands must disable automatic resolution, use fixed arguments and external temporary scratch/cache directories.
- Do not run Swift package update/resolve commands, arbitrary Swift scripts or automatic toolchain installation.
- Document that SwiftPM manifests, plugins and tests may execute project code and therefore require approval.
- Dart/Flutter integrations must parse Pub descriptors as data before invoking tools and must not run automatic dependency resolution.
- Dart formatting checks must use verify-only mode and never rewrite source files.
- Dart and Flutter analysis/tests must use fixed arguments; Flutter commands must use `--no-pub` when available.
- Do not run `dart pub get`, `flutter pub get`, Pub upgrades, `dart run`, Flutter builds, code generators or automatic SDK installation.
- Document that analysis and tests can load dependencies or execute project code and that proxy restrictions are not a complete sandbox.
- SQL integrations must remain static or read-only by default; do not add arbitrary query execution, migration application or automatic database connections.
- SQLite inspection must use `mode=ro`, query-only mode and an authorizer that denies writes, schema changes, attachments and transactions.
- Schema inspection must expose metadata only and must not read, sample or count user rows.
- Query-plan features must accept exactly one SELECT/CTE and execute only `EXPLAIN QUERY PLAN`.
- Destructive or mutating SQL policy must be explicit, bounded, auditable and separate from authorization.
- Assistant planners must produce strict structured plans with no more than four steps and must validate every proposed skill, parameter and path against a fixed allowlist.
- Approval must bind the original request to an immutable single-use plan; cancellation and invalid tokens must execute zero skills.
- CLI execution must consume a previously stored exact preview ID and must reject reuse or plan-ID mismatches.
- Orchestration results sent to a language model must be bounded, sanitized and derived only from completed skill results.
- Do not add autonomous file writes, package installation, network access, recursive planning or background execution to supervised plans.
- Validation cycles must start from an applied proposal, freeze an exact allowlisted plan and require a separate single-use approval before execution.
- Failed validation evidence supplied to a repair model must be bounded, sanitized and derived from real completed skill results.
- Do not create automatic repair loops: every repair proposal, application and revalidation requires a new explicit owner action.
- Policy-bounded automation may use the reviewed optional scheduler architecture only with an exclusive process lock, explicit startup, clean shutdown and no service installation.
- Scheduler changes must test lock contention, idempotent cycles, Ctrl+C/web shutdown, bounded notification materialization and CLI/web parity.
- Automation policies must bind one allowlisted low-risk action, explicit limits and immutable scope; they must never become generic skill, shell, file or network authority.
- Every user-facing capability must have CLI and web parity through shared repositories and application services, with regression tests for both surfaces.
- Keep authorization, profiles and imported knowledge separate.
- Never commit personal data, tokens, keys, model weights, local databases or private package
  contents.

## Release documentation

## Spanish lexical sources and fixtures

Contributions for 0.8.8 may include schemas, importers, original curated records and small synthetic
fixtures only. Do not commit dumps or generated full packs. Every real local input requires an exact
SHA-256, version/date, license notice and attribution. Code and data licensing remain separate;
Wiktionary share-alike obligations must be preserved. Real-source validation belongs to supervised
Phases 3 and 4 and must not use network access from Elyndra. Phase 4 bundle tests must keep every
temporary root below `build/test-runs/v089-phase4/`, use only synthetic artifacts, and prove that
download approval does not imply installation approval.

Gateway activation tests must also prove the three-step CLI boundary: deterministic read-only preview,
exact persisted plan approval, and foreground execution with a fresh process-local capability. Never
enable networking through persistent configuration or a generic web/chat flag.

Language-pack changes require tests for root/vault roles, migration from schema 48, source bounds,
FTS5, deterministic `content_sha256`, atomic cleanup, read-only lookup, sense-aware relations,
user-mode denial and cross-account overlay isolation.

Every alpha development release must update, when applicable:

- `CHANGELOG.md` with the current release and any explicitly requested historical backfill;
- `README.md` when commands, capabilities, requirements or architecture change;
- `SECURITY.md` when permissions, tools, network behavior or trust boundaries change;
- `CONTRIBUTING.md` when validation or contribution rules change;
- a focused release note under `docs/` for every alpha implementation.

Preserve the existing Markdown structure and command syntax. Before producing a patch, confirm that
all new files are included, the documented version matches `pyproject.toml`, and the patch applies to
a clean copy of the previous release.

## Version labels

Use the human-facing form `0.x.y-alpha` in Elyndra, documentation and Git tags. `pyproject.toml` may contain that form; Python packaging tools are expected to normalize it to the PEP 440 representation `0.x.ya0`. Do not revert active releases to `.dev0` unless the project explicitly changes this policy.

## Change-proposal safety tests

Changes to the reviewed writing path must preserve exact requested-file allowlists, SHA-256 stale checks, symbolic-link rejection, secret-path rejection, single-use approval, per-file atomic replacement and best-effort rollback. Tests must prove that proposal generation does not modify files and that cancellation performs zero writes.


For 0.7.20-alpha, development sessions are a presentation and audit layer over existing supervised primitives. Contributions must not let a session reuse approvals, bypass single-use tokens, broaden project roots or files, trigger validation or repair automatically, or schedule background execution.

For 0.7.21-alpha, conversational session continuity must remain deterministic and read-only. A chat may focus an existing owner session, display its persisted state and suggest commands, but it must not interpret a focus record as approval, auto-run a suggestion, mutate project scope or pass tools and approval tokens to the model. Tests must cover explicit session references, per-chat focus, closed-session cleanup and zero execution while requesting next steps.

## Ethics changes in 0.7.22-alpha

Changes to `src/elyndra/ethics.py` must preserve the immutable core: no prompt, profile, imported package, owner setting or model output may disable protection against harm, privacy abuse, malicious cyber activity, fraud, sabotage or deliberate environmental damage. Tests must prove that the filter runs before model calls and supervised plans, that neutral alternatives are returned, that raw prompts are not persisted, and that defensive authorized security work is not blocked merely because it mentions a vulnerability.

Proactive advice may be configurable, but it must remain advisory text only. It cannot apply a change, execute a skill, create a validation cycle or reuse approval. Path normalization from model output may accept an absolute path only when it resolves to the exact already-authorized file under the frozen project root.

## Ethics v2 and dictionary changes in 0.7.23-alpha

Changes to ethics classification must include regression tests for explicit self-harm or crisis, homicide or violence, child sexual abuse material, malicious cyber activity, defensive authorized work and ambiguous concealment. An unrecognized or ambiguous harmful request must not silently become `allow`. Deterministic blocks must run before model calls, and a tutor result must never weaken such a block. Tutor output must remain strict, bounded, local and unable to grant tools, approvals or filesystem access.

Dictionary contributions must preserve offline deterministic lookup, package version, license and SHA-256 metadata. Tests must cover all declared languages and prove that dictionary fast paths do not load a model or use the network. Do not describe the starter lexicon as a complete dictionary or full translation system.

## Ethics v3, first-aid and memory-tier changes in 0.7.24-alpha

Add regression tests using realistic paraphrases rather than only the exact phrase that motivated a rule. Preserve benign contextual exceptions, fail closed when an ambiguous high-risk tutor review is unavailable and prove explicit emergencies do not load the language model. First-aid additions require authoritative provenance, deterministic bilingual text and an explicit statement that the library is not complete.

Memory changes must prove bounded hot storage, durable warm and cold retrieval, provenance-preserving consolidation, latency visibility and owner-controlled forgetting. No contribution may silently install language data, promote inferred preferences or load the complete SQLite database into RAM.

## Structured Alejandría packs — 0.7.25-alpha

Structured language, dialect and first-aid contributions must use `elyndra-structured-package.json` schema 2 and bounded UTF-8 JSONL sources. Every source requires a SHA-256 and attribution. Do not add remote fetches, executable package hooks, archive extraction, silent replacement or automatic installation.

Language adapters must preserve source language, locale or dialect, license, examples and known limitations. They must not claim complete coverage or fabricate translations. Large resources remain disk-backed and may use only bounded lookup caches.

First-aid packages must be reviewed and include reviewer, review date, locale, source references, limitations and regression tests. Unreviewed medical packages must be rejected rather than treated as emergency authority. Package inspection and all state-changing operations remain explicit owner actions.

## Translation, first-aid and preference changes

Add regression tests for both CLI and web application paths. Local translation data must state its scope and license. Medical cards require reviewed sources and must avoid implying complete clinical coverage. Preference learning changes must preserve explicit approval, provenance, expiration and forgetting semantics.

## Tutor arbitration and benchmarks — 0.7.27-alpha

Tutor adapters must be local-only, bounded and replaceable. A new adapter may receive text context but must never receive a skill registry, approval token, filesystem object, secret store or network permission. Optional tutor configuration must fail safely without preventing deterministic startup.

Benchmarks must use fixed non-personal prompts, run only after explicit approval, execute sequentially in the foreground and store hashes plus structured metrics rather than raw prompt or output text. Tests must cover malformed configuration, remote endpoint rejection, benchmark privacy, deterministic selection, external-tutor failure fallback and provenance metadata. A benchmark score must never be represented as general intelligence, safety certification or permission.
## Reviewed tutor lessons and calibration — 0.7.28-alpha

Tutor-learning changes must preserve proposal-first review, exact tutor/task scoping, bounded context injection, explicit approval, expiration and forgetting. Tests must prove that pending, rejected, expired and forgotten lessons have no behavioral or calibration effect; duplicate or failed evidence operations must not leave orphan records.

New evidence sources require an explicit provenance category, a bounded representation and regression coverage showing that raw prompts and tutor outputs are not persisted. Calibration changes must retain the raw benchmark score, group reviewed observations by source and avoid universal-intelligence, factuality or safety claims. An external tutor without an applicable completed benchmark must remain ineligible regardless of approved lessons.

Do not add fine-tuning, weight updates, silent memory or preference promotion, approval reuse, hidden parallel generation, automatic benchmark execution, network access, model downloads or background learning loops.


## Tutor evolution and durable knowledge — 0.7.29-alpha

Contributions must preserve the exact plan/run separation for lesson evaluations. Planning must execute zero model calls. A run must consume one pending plan exactly once, stay in the foreground and persist hashes plus structured metrics rather than raw prompts or outputs. Failures must not leave partial result rows.

Auditor adapters require an explicit `auditor` or `both` role and `auditor_allowed=true`. An auditor must never enter normal response arbitration, grant authority, approve a promotion or override deterministic evidence. Tests must cover invalid roles, advisory-only behavior and model-fingerprint staleness.

Knowledge promotion is a separate owner-approved action. Do not add deletion of durable knowledge. Updates must preserve lineage and the complete previous version, require a validated functional improvement and mark the earlier row `superseded`. Do not silently copy learned knowledge into preferences, permissions, ethics policy, model weights or external services.

## General knowledge acquisition — 0.7.30-alpha

Contributions must preserve plan/run/promotion separation. Planning performs zero model calls; running is foreground-only and single-use; promotion requires a new explicit approval. Tests must cover frozen evidence hashes, malformed model JSON, deterministic-audit rejection, optional auditor downgrade, failure without partial knowledge, local answer-before-model behavior and immutable supersession lineage.

Do not add silent conversation ingestion, automatic promotion, destructive deletion, remote retrieval, weight updates, model downloads or permission transfer. New source types require bounded storage, explicit provenance, review semantics and regression coverage. Node validation is required only when JavaScript/TypeScript or a Node-dependent path changes.


## Knowledge governance changes — 0.7.31-alpha

Changes to confidence parsing, conflict handling or revalidation must include tests for fail-closed unknown values, approval non-reuse, immutable version history and operational exclusion of unresolved or due knowledge. Do not add destructive knowledge deletion. Node validation is required only when JavaScript, TypeScript, package metadata or a Node-dependent path changes.

## Multisource knowledge changes — 0.7.32-alpha

Tests must cover blank or conflicting tutor metadata, per-source hashes, package limits, conservative multi-auditor aggregation and project-scope isolation. Approved kind, subject and locale must remain authoritative. Do not silently infer a scope, accept unbounded evidence files, parallelize model calls or let an auditor promote knowledge.

CLI changes must state whether an identifier is a pending plan, reviewed proposal or durable knowledge ID, and must print the exact valid next command. Node validation remains conditional on JavaScript, TypeScript, package metadata or another Node-dependent path changing.

## Cognitive executive changes — 0.8.0-alpha

Executive changes must preserve deterministic inspectability. New routes require
explicit risk, approval and verification behavior. Do not store raw prompts or
chain of thought in executive tables. Retrieval changes must include negative
tests proving that irrelevant global knowledge is excluded. Goal and task
mutations require explicit approval and may not trigger background work.

## Personal organizer changes — 0.8.1-alpha

Changes to organizer behavior must preserve all of these invariants:

- no background execution or notification delivery;
- no unbounded expansion of recurrence instances;
- separate creation and review for reminder proposals;
- unique routine check-ins per routine and local date;
- explicit timezone and ISO-date validation;
- no automatic goal or task progress;
- project-scoped items do not leak into unrelated project briefs;
- deterministic brief generation must not require a language model;
- organizer writes require explicit CLI approval;
- migration tests must preserve executive goals, tasks and earlier knowledge.

Add tests for leap days, short months, DST-sensitive timezone handling,
recurrence boundaries and reminder offsets whenever those paths change.


## Personal coaching and web parity changes — 0.8.2-alpha

Changes to an assistant capability must preserve interface parity. Tests must
exercise the shared application method, `ElyndraWebService` and a real loopback
HTTP request whenever routing or response metadata changes. A deterministic CLI
fast path must not silently fall back to a model in web chat.

Web releases must expose the exact runtime version in the rendered page, response
headers and streamed message metadata. Tests must detect stale-version mismatches.
All web writes require explicit confirmation and must invoke the same bounded
repository methods used by CLI.

Wellbeing changes must remain descriptive and owner-controlled. Do not add
diagnosis, treatment authority, medication advice, automatic intervention,
background coaching or silent progression. Store only bounded local check-ins and
reviewed plans. Safety and emergency routing must precede wellbeing and model
fallback.

For every release update `CHANGELOG.md`, `README.md`, `SECURITY.md`,
`CONTRIBUTING.md`, the focused release note and any affected architecture guide.
When JavaScript or TypeScript changes, `node --check` is mandatory; otherwise it
may be omitted.

## Semantic-intent changes

Changes to language understanding must add natural paraphrases rather than only canonical command-like phrases. Tests must cover local resolution, tutor-assisted strict JSON fallback, concrete clarification, no-tool/no-authority tutor context, reviewed learning, raw-message hashing and CLI/web parity. New intent examples must not silently activate from ordinary conversation. Every release must continue updating `CHANGELOG.md`, `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, `docs/ROADMAP.md` and a focused release note.

## Account, identity and dialogue changes

Changes affecting login, profiles, sessions, dialogue continuity or personal identity must include:

- CLI and real loopback-HTTP tests over the same repositories;
- validation that passwords never appear in SQLite, logs, JSON responses or patches;
- user-mode tests proving developer-only Control/Alejandría routes are unavailable;
- tests that blank optional identity fields are neither displayed nor sent to a model;
- direct-address tests preventing the developer or current user from being described as a third party;
- migration tests preserving pre-account installations and all prior local data;
- synchronized updates to `CHANGELOG.md`, `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, the roadmap and the release note.
## Web identity and multi-account requirements (`0.8.7-alpha`)

Changes to authentication or the web shell must preserve all of the following:

- `/login` and `/register` are dedicated routes; successful authentication redirects to `/`.
- Login accepts username or email, stores only session-token hashes and persists until logout or expiry.
- Every account uses a separate SQLite vault and account-specific data/state/cache directories. Tests must prove that chats and memories from one account are absent from another.
- Legacy installations migrate personal data only to the first account; subsequent accounts begin empty.
- User mode hides developer navigation and receives HTTP 403 for developer-only APIs.
- Sidebar navigation and the account footer remain fixed; only conversation history scrolls.
- `Nuevo chat` must be lazy and idempotent until the first message or attachment.
- The UI must not expose raw adapter/model identifiers to normal users. Local and future Online modes must be described accurately.
- JavaScript changes require `node --check src/elyndra/web/static/app.js`; frontend and real HTTP tests are mandatory.
- Every release updates CHANGELOG, README, SECURITY, CONTRIBUTING, ROADMAP and a focused release note.
# Datos lingüísticos

No agregues dumps, SQLite generados ni bundles al repositorio. Los cambios de formato o
distribución requieren fixtures pequeños, pruebas reproducibles y verificación de las
licencias del artefacto fuente real.
