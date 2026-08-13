# Roadmap

## 0.8.9-alpha post-Phase-4 correction

- [x] Persist sanitized final HTTP context in existing audit JSON without a schema change.
- [x] Make pre-body rejection terminal and remove only proven empty non-resumable partials.

## 0.1 — Local deterministic core

- [x] CLI and XDG directories.
- [x] Owner identity check.
- [x] SQLite memory and projects.
- [x] Audit trail.
- [x] Policy engine.
- [x] Five safe skills.
- [x] No-model engine boundary.
- [x] Tests and CI.

## 0.2 — Project awareness and local knowledge

- [x] Interactive local session.
- [x] Project inspection.
- [x] Text search inside registered projects.
- [x] Safe line-range file reading.
- [x] Plain text, Markdown and source-code ingestion.
- [x] Provenance, SHA-256 hashes, chunking and logical deletion.
- [x] FTS5 search with a SQLite fallback.
- [x] Search result citations.
- [x] Combined memory and knowledge lookup.
- [ ] Directory import queue with owner review.
- [ ] PDF text adapter as an optional external capability.

## 0.3 — Optional local language engine

- [x] Discover existing `llama-cli`, `llama-server`, Ollama and GGUF files.
- [x] Separate private `language.toml` configuration.
- [x] One-shot `llama-cli` adapter with zero idle model RAM.
- [x] Resource profiles: eco, normal and work.
- [x] Retrieve memory and knowledge before generation.
- [x] Keep models isolated from tools and secrets.
- [x] Disable reasoning mode by default where supported.
- [x] Loopback-only Ollama adapter with immediate unload.
- [x] Model provenance and teacher-approval metadata.
- [x] Offline language detection and persistent auto/fixed response language.
- [x] Local dictionary/phrase/template translation before model fallback.
- [x] Canonical owner-editable persona included in every model request.
- [x] Concept-oriented retrieval variants for memory and document lookup.
- [x] Bounded process-only conversation history in interactive chat.
- [x] Visible progress, elapsed time and tolerant exit commands.
- [x] Warm Ollama only during an active chat, followed by explicit release.
- [x] Strict prompt/context budget to avoid sending excessive retrieved text.
- [x] Configurable identity, gender, pronouns and presentation style.
- [ ] Benchmark latency, CPU, peak RAM and tokens per second.
- [ ] Optional idle-sleep `llama-server` adapter.
- [x] Structured tool proposals requiring deterministic validation.

## 0.4 — Durable personal memory and learning

- [x] Named, isolated chat containers.
- [x] Summary-only durable session memory by default.
- [x] Optional full transcript retention in SQLite and logical deletion.
- [x] Reopen, list, search, rename and inspect chat containers.
- [x] Relevance-gated persisted-summary retrieval.
- [x] Gzip-compressed cold transcript export with optional SQLite pruning.
- [ ] Optional local encryption and key management for cold transcripts.
- [x] Episodic memory linked to chats, projects, decisions and outcomes.
- [x] Correction records.
- Small owner-trained intent classifier.
- [x] Preference, routine and rule proposals.
- [x] Reviewed preference scope, expiration and durable forgetting.
- Routine detection.
- [x] Owner review, edit, approve, reject and delete commands.
- [x] Loopback preference status and review visibility.
- [ ] Export/import of personal memory.

## 0.5 — Local interface

- [x] Loopback-only web server with no runtime dependencies.
- [x] Functional local chat composer and conversation view.
- [x] Searchable chat-history sidebar.
- [x] Full or summary-only local persistence selector.
- [x] Visible processing state and response duration.
- [x] Ephemeral write token, Host validation and strict browser security headers.
- [x] Memory and knowledge inspector.
- [x] Audit viewer.
- [ ] Skill permission editor.
- [ ] Explicit approval UI for medium-risk actions.

## 0.6 — Connectivity providers

- Offline-only profile.
- Explicit online opt-in profile.
- Provider allowlist and per-provider consent.
- Redaction and secret broker before remote requests.
- Visible cost and data-transfer preview.
- No silent fallback from local to remote.


## 0.6.2 — Retrieval engine v2

- [x] Compound-query task planning.
- [x] Specialized-library priority and source diversity.
- [x] Deterministic missing-input handling.
- [x] Compact context budgets and ten-minute interactive keep-alive.
- [x] Local NDJSON token streaming and optional latency diagnostics.
- [x] Controlled continuation after model token-limit stops.

## 0.7 — Speech and conversation input

- [ ] Local speech-to-text adapter loaded on demand.
- [ ] Microphone permission and visible recording state.
- [ ] Language identification from transcripts and optional audio metadata.
- [ ] Conversation translation with explicit source and target languages.
- [ ] Local text-to-speech voice adapter.
- [ ] No microphone background recording by default.

## Later

- Image engine adapters.
- Home automation plugins.
- Digital avatar / companion layer.
- Signed community skill packages.
- Minimal low-resource interface profile for small personal devices after the desktop core is stable.


## 0.4.1 — Conversational correction

- [x] Fast persistent-session recap without loading the model.
- [x] Freestyle karaoke and original song play.
- [x] Conversational games and mixed-language guidance.
- [x] Safer nuanced humor guidance.
- [x] Avoid unrelated-entity carryover and invented biographies.
- [ ] Structured episodic consolidation.
- [ ] Local HTML memory inspector.

## 0.5.2 — Local attachments and web management

- [x] Dedicated pinned-chat section with a five-chat limit.
- [x] Native browser print/PDF dialog.
- [x] Elyndra-owned permanent-delete confirmation.
- [x] Local text, code and image attachments.
- [x] Secret redaction before model context.
- [x] Optional Ollama vision path when the configured model declares vision.
- [ ] PDF and office-document adapters.
- [ ] Drag-and-drop and attachment inspector.


## 0.5.3 — Memory inspector

- [x] Local overview with durable-memory counts and database size.
- [x] Semantic-memory browser with edit and forget actions.
- [x] Episodic-memory browser linked to source chats.
- [x] Proposal review with edit, approve and reject actions.
- [x] Correction, document, cold-archive and audit views.
- [x] Stable `/memory` route and pending-proposal badge.
- [x] Explicit distinction between indexed content and validated syntax.
- [ ] Web skill-permission editor.
- [ ] Explicit approval UI for medium-risk skills.

## 0.5.4 — Document trust layer

- [x] Drag-and-drop attachments.
- [x] PDF and Office text extraction.
- [x] Deterministic JSON, TOML, XML and YAML validation.
- [x] PHP lint integration when PHP CLI is available.
- [x] Explicit extraction and validation states.
- [x] Attachment inspector and local reprocessing.
- [x] Safe lightweight Markdown rendering.

## 0.6.0 — Alexandria foundations

- [x] Local library registry with domain, language, version and license metadata.
- [x] Deterministic import of text, code, PDF and Office sources.
- [x] Source provenance, SHA-256 hashes and private local copies.
- [x] Small indexed knowledge units instead of loading whole libraries into RAM.
- [x] FTS5 retrieval from enabled libraries under a strict context budget.
- [x] Reviewed versus unreviewed source state controlled by the owner.
- [x] CLI and local web interface for creating, importing and searching libraries.
- [x] Concise deterministic syntax-validation replies.
- [x] Code-shaped owner messages rendered as code blocks in the web chat.
- [ ] Remote source synchronization with explicit online permission.
- [ ] Signed library manifests and community catalog.
- [ ] Canonical-unit editor and conflict resolution.
- [ ] Skill proposals derived from reviewed libraries.

## 0.6.1 — Alexandria retrieval quality

- [x] Multi-question task detection and ordered response contract.
- [x] Dynamic output budget for technical and compound requests.
- [x] Reviewed-source priority before unreviewed fallback.
- [x] Domain-aware PHP library selection.
- [x] Strict “Según Alejandría” mode without silent general-knowledge fallback.
- [x] Visible source references for every retrieved Alexandria unit.
- [x] Separate confirmed findings, possible risks and pending verification.
- [x] Regression tests for PDO, GROUP_CONCAT, transactions, architecture, testing and operations.
- [ ] Answer-quality benchmark against a larger local model.
- [ ] Owner-adjustable retrieval budget and strictness controls.

- [x] Reviewable file-change proposals with frozen diffs and single-use approval.

- [x] Supervised validation-and-repair cycles with separate approvals and no autonomous loops.

## 0.7.20-alpha — supervised development sessions

- Group reviewed changes, approved validation cycles, real results and repair proposals into a single persistent timeline.
- Keep every action separately approved and single-use.
- No autonomous continuation, background execution or permission inheritance.

## 0.7.21-alpha — conversational session continuity

- [x] Keep one focused development session per chat.
- [x] Explain the current state and suggest exact next commands without execution.
- [x] Show both change-proposal and development-session IDs.
- [x] Never convert guidance into approval or background work.

## 0.7.22-alpha — immutable professional ethics

- [x] Apply a deterministic constitutional review before routing, planning, file-change proposals and model fallback.
- [x] Protect people, privacy, authorized systems, professional integrity and the environment.
- [x] Redirect harmful requests neutrally without shaming or automatic reporting.
- [x] Keep owner authority inside non-harm, privacy and third-party-rights boundaries.
- [x] Permit optional proactive advice while keeping the non-harm core non-disableable.
- [x] Treat Ollama as a replaceable tutor and language generator, never a policy authority.
- [x] Normalize an absolute model-returned file path only when it maps exactly to an owner-selected file inside the frozen project root.

## 0.7.23-alpha — ethics review v2 and offline multilingual dictionary foundation

- [x] Explicit deterministic categories for self-harm/crisis, homicide, child sexual abuse material and ambiguous concealment.
- [x] Secondary local-tutor review for ambiguous cases only; it can increase caution but never weaken a deterministic block.
- [x] Fail closed with neutral alternatives when the tutor is unavailable or uncertain.
- [x] Deterministic local lookup for Spanish, English, Japanese, Chinese, Italian, French, Portuguese and German.
- [x] Versioned starter lexicon stored as package data with license and SHA-256 metadata.
- [x] Normalized exact forms, compact glosses and translations without model or network access.
- [x] Explicit distinction between starter lexical evidence and complete dictionaries, grammar or generative translation.
- [ ] Expand the starter lexicon through separately reviewed, licensed and versioned packs.

## 0.7.24-alpha — ethics v3, emergency guidance and tiered memory

- [x] Separate hot conversational context, warm recent episodes and cold durable memory on disk.
- [x] Retrieve older memories by relevance and project context under a strict budget.
- [x] Consolidate inactive context without deleting its provenance or source links.
- [x] Make retrieval latency visible and avoid loading the full memory database into RAM.
- [x] Add explicit logical forgetting for cold-index records without erasing their source rows.
- [x] Expand deterministic ethics with contextual emergency, child-safety, violence, coded-language and non-retaliation cases.
- [x] Add source-attributed offline first-aid starter cards that run without Ollama or network access.

## 0.7.25-alpha — structured language and first-aid knowledge packs

- [x] Add structured on-disk adapters for explicitly installed monolingual, bilingual, morphology and dialect packs.
- [x] Add reviewed first-aid topic packs with source, review date, locale, limitations and regression fixtures.
- [x] Keep large packs in Alejandría on disk and cache only bounded lookup results.
- [x] Require explicit package inspection and owner approval before installation or replacement.
- [x] Preserve license, attribution and SHA-256 provenance for every imported source.
- [x] Reject package-root and source symlinks, path traversal, duplicate records and hash mismatches.
- [x] Keep automatic download, code execution and permission grants disabled.

## 0.7.26-alpha — reviewed preference learning

- [x] Detect stable preferences, routines and personal rules as proposals rather than silent facts.
- [x] Require owner review before durable promotion, with edit, reject, forget and expiry controls.
- [x] Distinguish user preferences from universal facts, project rules and temporary instructions.
- [x] Use approved preferences across chats while minimizing hot-memory use.
- [x] Never infer sensitive identity attributes or turn imported knowledge into permission.

## 0.7.27-alpha — model-teacher arbitration

- [x] Keep Elyndra's deterministic policy, memory, retrieval and skill boundaries authoritative.
- [x] Use local models as replaceable tutors for language, explanations and bounded proposals.
- [x] Classify tutor tasks deterministically and expose selection provenance in every generated reply.
- [x] Prefer evidence-first and local fast paths before any tutor invocation.
- [x] Add reproducible quality/protocol and latency benchmarks without granting tools, permissions or self-modification.
- [x] Store prompt/output hashes and metrics rather than raw benchmark content.
- [x] Keep benchmark execution foreground-only, sequential and explicitly approved.

## 0.7.28-alpha — reviewed tutor lessons and confidence calibration

- [x] Propose compact lessons from tutor outputs only when grounded in reviewed evidence or owner feedback.
- [x] Require owner review before any lesson becomes active; never promote it into memory or preferences silently.
- [x] Calibrate confidence by task and source instead of treating benchmark scores as universal intelligence.
- [x] Compare tutor-supported answers against deterministic evidence using hashes and create only pending proposals.
- [x] Inject at most four active owner-reviewed lessons for the exact tutor/task pair.
- [x] Preserve the benchmark score separately and keep unbenchmarked external tutors ineligible for selection.
- [x] Support edit, reject, expiry and forgetting without model training or self-modification.

## 0.7.29-alpha — supervised tutor evolution and durable knowledge

- [x] Plan lesson evaluations without invoking a model.
- [x] Run exact baseline/candidate evaluations once, sequentially and in the foreground.
- [x] Let explicitly configured local auditors provide advisory review without entering normal response selection.
- [x] Store evaluation hashes, scores, latency and structured metrics rather than raw prompts or outputs.
- [x] Use current-model fingerprints and exclude stale evaluations from active calibration.
- [x] Promote validated lessons into Elyndra-owned durable task knowledge only after a separate owner approval.
- [x] Preserve immutable knowledge lineage: updates create superior versions and retain superseded history.
- [x] Inject bounded durable knowledge independently of the tutor that helped create it.
- [x] Keep skills at 102 and avoid JavaScript changes or a Node requirement.

## 0.7.30-alpha — general reviewed knowledge acquisition

- [x] Generalize durable knowledge beyond tutor-task lessons into reviewed factual, procedural, linguistic and conceptual units.
- [x] Let tutors propose explanations and auditors challenge provenance, support and scope before owner review.
- [x] Retrieve validated knowledge by type, language and lexical relevance before Ollama fallback; domain/project ranking remains planned.
- [x] Invoke Ollama tutors only when deterministic retrieval is insufficient, then convert an explicitly reviewed and validated result into Elyndra-owned knowledge rather than leaving it as transient model output.
- [x] Support superior reviewed revisions with non-decreasing confidence while preserving the complete lineage.
- [ ] Add contradiction sets, confidence decay by evidence age and explicit revalidation without destructive deletion.
- [x] Keep learning local, reviewable and bounded; do not train weights or grant tools, permissions or policy authority.

## 0.7.31-alpha — resilient acquisition and non-destructive knowledge governance

- [x] Normalize numeric, percentage and controlled qualitative confidence in Spanish and English.
- [x] Preserve the original confidence value and its conservative mapping in proposal provenance.
- [x] Retry failed acquisitions only through a new pending plan and a new approval.
- [x] Detect exact duplicates and require explicit review for same-subject parallel knowledge.
- [x] Persist potential conflicts and resolve them without deleting either knowledge version.
- [x] Add source observation and revalidation dates; due knowledge remains durable but is withheld from operational retrieval.
- [x] Preserve 102 skills, local-only execution and immutable knowledge lineage.
- [x] Add source bundles with independent hashes and attribution per source.
- [x] Add domain/project ranking and multi-auditor cross-review.

## Path toward 1.0 beta

- Stabilize ethics, memory consolidation, preference learning and multilingual lexical retrieval.
- Add reproducible installation, migration recovery, encrypted local backups and long-running reliability tests.
- Preserve supervised execution: no autonomous loops, hidden network access, silent installations or unreviewed file writes.

## 0.7.26-alpha — Local response fast paths and reviewed preference learning

- [x] Shared web/CLI translation parser.
- [x] Dictionary, phrasebook, templates and installed packs before model fallback.
- [x] Pronunciation/romanization follow-up for stored translations.
- [x] Direct first-aid emergency phrases and local capability catalog.
- [x] Severe trouble-breathing starter card.
- [x] Reviewable preference proposals, scope, expiration and forgetting.
- [x] Schema 34 and loopback control endpoints.
- [ ] Expand phrasebooks through reviewed Alejandría packages.
- [ ] Add locale-aware emergency-number presentation without network access.

## 0.7.32-alpha — multisource knowledge and cross-auditor review

- [x] Keep approved kind, subject and locale immutable when a tutor omits or changes them.
- [x] Record model metadata mismatches without failing an otherwise valid candidate.
- [x] Accept bounded local evidence packages with up to eight independently hashed sources.
- [x] Preserve per-source attribution, observation dates, revalidation dates and unit IDs.
- [x] Support sequential cross-review by multiple explicitly configured local auditors.
- [x] Aggregate auditor results conservatively; one stricter review cannot be weakened by another.
- [x] Add domain and project scopes with project-specific knowledge excluded from global retrieval.
- [x] Make CLI plan, proposal and knowledge IDs explicit for every next action.
- [x] Preserve foreground-only execution, separate promotion and immutable knowledge lineage.

## 0.8.0-alpha — cognitive executive and unified assistant runtime

- [x] Wrap normal assistant requests in a deterministic executive assessment.
- [x] Record structured intent, risk, routes, confidence and outcome without raw prompts.
- [x] Assemble relevant context under an explicit budget and relevance threshold.
- [x] Introduce persistent goals, dependent tasks and explicit outcome verification.
- [x] Keep execution, goal progress and learning supervised and foreground-only.

## 0.8.1-alpha — personal organizer

- [x] Add local commitments, birthdays and routines backed by SQLite.
- [x] Calculate bounded recurrence rules on demand without expanding history.
- [x] Add explicit routine check-ins and optional goal/task links.
- [x] Separate reminder proposal from review and keep delivery disabled.
- [x] Generate deterministic daily briefs and upcoming views without Ollama.
- [x] Add conversational fast paths for today, tomorrow and upcoming birthdays.
- [x] Preserve foreground-only behavior and automatic goal progress disabled.

## 0.8.2-alpha — personal coaching and wellbeing

- enforce CLI/web parity through the shared application runtime and real HTTP tests;
- expose the exact web runtime version to detect stale processes after upgrades;
- provide a Personal web workspace for organizer and wellbeing data;
- add bounded local check-ins for mood, energy, stress, focus, sleep and habits;
- add reviewed coaching plans and explicit action status without automatic progress;
- preserve professional boundaries, emergency-first routing and zero background intervention;
- update CHANGELOG, README, SECURITY, CONTRIBUTING and focused release docs.

## 0.8.3-alpha — policy-bounded automation

- [x] Add explicit autonomy levels: observe, suggest, prepare, execute-with-approval and execute-under-policy.
- [x] Bind every policy to one low-risk incorporated action, timezone, optional window, daily limit and optional expiry.
- [x] Add bounded once/daily/weekly/monthly schedules calculated on demand.
- [x] Materialize occurrence runs idempotently through a foreground dispatcher only.
- [x] Require separate approval for execute-with-approval runs.
- [x] Add a bounded local result inbox without operating-system notifications.
- [x] Keep network, skills, shell, file writes, installs and permission changes unavailable to automation.
- [x] Expose equivalent CLI and Personal-web operations with explicit confirmation.
- [ ] Add an optional long-running scheduler only after reliability, locking, shutdown and notification policies are reviewed in a later release.

## 0.8.4-alpha — optional local scheduler and notifications

- [x] Add an owner-started scheduler for CLI foreground and the active loopback web runtime.
- [x] Prevent concurrent schedulers with an exclusive private state lock.
- [x] Persist scheduler sessions, heartbeats, scan counts, errors and clean-stop state.
- [x] Reuse only policy-bounded automation actions and approvals; do not broaden authority.
- [x] Add bounded local notifications sourced from the existing local inbox.
- [x] Add optional browser notifications while the web interface is open.
- [x] Stop the web scheduler during service shutdown and release the process lock.
- [x] Keep cron, systemd, detached daemons, network delivery, skills, shell and file writes unavailable.
- [x] Expose equivalent CLI/web controls and update release/security/contribution documentation.

## 0.8.5-alpha — semantic understanding and tutor-assisted intent resolution

- [x] Add a bounded canonical ontology for personal, knowledge, memory and automation intents.
- [x] Resolve natural paraphrases through local semantic similarity, deterministic concepts and temporal/metric extraction.
- [x] Use a local tutor only when local confidence is insufficient; transfer no tools, memory, permissions or authority.
- [x] Ask a concrete clarification when multiple interpretations remain plausible.
- [x] Route resolved language to existing local repositories rather than allowing a generic model disclaimer.
- [x] Store message hashes and structured outcomes instead of raw prompts.
- [x] Add reviewed language-learning proposals with no silent activation.
- [x] Detect repeated tutor fallback and prepare, but never approve, a learning proposal.
- [x] Maintain CLI/web parity and visible semantic diagnostics.
- [x] Pause development after this objective for owner review of the language architecture.

## 0.8.6-alpha — identity and dialogue foundation

- [x] Add local registration/login, Argon2id credentials, adult validation and persistent local sessions.
- [x] Separate username, preferred name, current-user identity and developer mode.
- [x] Keep optional sensitive profile fields absent from model context when undefined.
- [x] Add profile, privacy and security surfaces, encrypted local export and a 2FA-ready schema.
- [x] Preserve clarification state per chat and add capability-aware help.

## 0.8.7-alpha — stabilized web UX and isolated multi-account runtime

- [x] Replace accumulated authentication fixes with dedicated `/login` and `/register` routes.
- [x] Support multiple username/email accounts without sharing personal repositories.
- [x] Allocate an isolated SQLite vault and data/state/cache directories per account.
- [x] Preserve legacy data in the first account only and start later accounts empty.
- [x] Enforce developer mode in navigation and HTTP authorization.
- [x] Keep navigation/account controls fixed while only conversation history scrolls.
- [x] Replace raw offline/model text with an explicit Local/Online capability control.
- [x] Make the brand a home link, compact search and remove unused new-chat mode controls.
- [x] Persist a new chat only after its first message or attachment.

## 0.8.8-alpha — Alejandría Spanish Lexical Core

- [x] Build separately licensed, disk-backed Spanish lexical packages from local inputs.
- [x] Import lemmas, forms, senses, definitions, synonyms, antonyms and semantic relations.
- [x] Add morphology fields, FTS5, internet abbreviations, emoji annotations and conservative laughter.
- [x] Query language data on demand without loading the complete dictionary into RAM.
- [ ] Validate real MCR, Wiktionary and optional CLDR artifacts in supervised Phase 3.

## 0.8.9-alpha — Controlled Online Gateway

- [x] Complete Phase 1 architecture, specification and threat model without implementing runtime.
- [x] Add schema 50, Local/restricted-online preferences and deny-by-default planning.
- [x] Add immutable pinned plans, single-use permits and privacy-safe audit events.
- [x] Expose transport-free CLI and loopback API parity.
- [x] Implement hardened Phase 3 transport, streaming, cache, quarantine, resume and global lock.
- [x] Complete Phase 4 supervised official descriptor, offline bundle validation and separately
  approved local installation, with loopback observation and CLI-only download execution.
- [x] Separate read-only plan preview, exact persisted approval and ephemeral CLI-only activation;
  keep persistent and web network authority disabled.
- [ ] Validate the real pinned release only after anonymous repository access is resolved and approved.
- [ ] Allow only explicitly approved public HTTPS HEAD/GET for pinned artifacts.
- [ ] Keep Ollama, tutors, planners and skills without direct or delegated network access.
- [ ] Separate download, verification, installation and enablement state and approvals.
- [ ] Integrate the pinned Spanish Core bundle first through loopback-tested acquisition.
- [ ] Sanitize all downloaded data and defend against SSRF, rebinding and prompt injection.
- [ ] Keep private proxies, authentication, telemetry, update checks and community marketplace out of
  `0.8.9-alpha`.

## 0.8.10-alpha — Privacy and Telemetry

- [ ] Add versioned consent, payload preview, revocation and non-sensitive opt-in telemetry.
- [ ] Publish concise privacy and service terms without collecting prompts, searches or personal content.

## 0.9.0-alpha — Encrypted Account Recovery

- [ ] Add optional remote encrypted backup, verified email, restoration and portability.
- [ ] Complete recovery-key and 2FA integration without server-side plaintext access.
