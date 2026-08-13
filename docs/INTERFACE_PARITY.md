# CLI and web interface parity

## Controlled Online Gateway — 0.8.9-alpha Phase 4

CLI exposes official descriptor inspection, manifest/asset planning, verified bundle inspection,
preparation, separately approved installation, install status/cancellation, download cancellation,
partial discard, cache verification and quarantine inspection. The loopback web surface exposes
sanitized status and explicit local control actions. Network execution intentionally remains CLI-only;
web loading and status refreshes are read-only and never acquire permits or start traffic.

CLI alone implements `plan-download` preview, `approve-download` persistence and exact foreground
execution. Web parity is intentionally observational for network work: it may show the same preview and
operation but cannot approve, execute or resume a download. Local installation controls remain shared.

## Controlled Online Gateway — 0.8.9-alpha Phase 3

Read/planning parity remains shared through `OnlineGatewayService`. Blocking execution is deliberately
CLI foreground-only in Phase 3: loopback web execution endpoints remain closed until cancellation and
streaming UX are reviewed. The CLI adds execute, resume, discard, cache verification and quarantine
inspection. This is a documented temporary surface difference, not duplicated policy.

## Controlled Online Gateway — 0.8.9-alpha Phase 2

CLI `elyndra online` and loopback `/api/online/*` use the same `OnlineGatewayService` for status,
account mode, sources, immutable planning, operation history and explicit clearing. JavaScript has no
gateway rules and was unchanged. Both surfaces report transport unavailable and cannot download.

## Controlled Online Gateway — 0.8.9-alpha Phase 1

The planned CLI and web surfaces call one account-scoped gateway application service. The Local/Online
preference is private to the account and never constitutes download approval. Both interfaces present
the same immutable descriptor, download state, verification state, installation state and enablement
state, and consume the same short-lived single-use approval contract.

User mode may select Local/restricted-online and acquire the official pinned Spanish Core artifact;
developer mode is required to manage user-pinned sources or technical quarantine details. HTTP routes
enforce that distinction with 403. JavaScript may render and confirm operations but must not implement
URL policy, SSRF checks, approval consumption, state transitions, hashing or installation logic.

Phase 1 adds no endpoints or network behavior. The planned parity surface is specified in
`docs/ONLINE_GATEWAY.md`.

## Spanish lexical core — 0.8.8-alpha

CLI and web call the same `LanguagePackRegistry`, `SpanishLexicalService` and account overlay
repository. Pack inspection, installation, verification and enable/disable are developer-only; state
changes require explicit confirmation. User mode can ask dictionary questions but cannot see paths,
internal hashes or pack controls. Both surfaces return the same bounded, source-attributed lookup.

Elyndra treats CLI and web as interfaces over one application runtime. A feature
is incomplete until its read and write behavior is reachable through both
surfaces where the operation makes sense.

## Shared routing

Web chat calls `ElyndraApplication.ask`, the same entry point used by CLI `ask`.
Deterministic organizer, knowledge, memory, translation, first-aid, ethics and
wellbeing routes must therefore return the same engine and substantive answer.
Tests cover the application, `ElyndraWebService` and a real loopback HTTP stream.

## Runtime identity

The rendered page, `X-Elyndra-Version` header, bootstrap payload and message
metadata expose the exact version. After applying a patch, the owner must restart
the web process and verify the displayed runtime. A process already in memory
cannot load newly installed Python code without restart.

## Web writes

The Personal workspace uses the same repositories as CLI. Each persistent write
requires a visible confirmation and an `approved=true` request value. Reading an
agenda, opening a workspace or chatting does not authorize a write. Web approval
never grants background execution or notification delivery.

## Release requirement

Any future assistant capability must include parity tests. JavaScript changes
make `node --check src/elyndra/web/static/app.js` mandatory for that release.

## Scheduler and notification parity

The optional scheduler exposes the same durable status, one-shot cycle and notification records to CLI and the Personal web workspace. The CLI may run an attached loop until `Ctrl+C`; the web may run the same loop only inside the current loopback service. Web close and explicit stop use the same clean-shutdown path. Browser notification permission is a presentation choice and does not change SQLite state or automation authority.

## Semantic parity in 0.8.5-alpha

CLI `ask`, web chat and the Personal workspace share the same `SemanticIntentRepository` and `ElyndraApplication.ask` dispatch. HTTP regression tests cover natural wellbeing language, semantic metadata and explicit approval for phrase-learning proposals. A phrase approved in either interface becomes available to both; ordinary conversation cannot activate it.

## Account and developer-mode parity (`0.8.6-alpha`)

Registration, login, logout, profile editing, email/password changes, telemetry preview and encrypted local export use the same `AccountRepository` from CLI and web. Once an account exists, normal CLI commands require a CLI session and web APIs require the HttpOnly local session cookie. User mode hides Control and Alejandría and their HTTP APIs reject access; developer mode exposes them after explicit opt-in. First-run installations remain migratable through the local loopback token until the account is registered, while the web application shell itself stays behind the registration screen.
## Multi-account and stabilized shell parity (`0.8.7-alpha`)

CLI and web resolve accounts through the installation registry and then load the same account-scoped `ElyndraApplication`. No personal repository is shared between accounts. Registration, login, password reset, account switching and profile operations accept the same identity rules.

Web-only presentation rules do not change backend authority: user mode hides Alejandría/Control and their APIs return 403; developer mode exposes them. The brand links home, only history scrolls, search is compact and a new chat is persisted only when content exists. The Local/Online control reports capability but does not enable network access in this release.
# Paridad del núcleo léxico

CLI, web y chat usan `SpanishLexicalService` y `LanguageBundleService`. La web muestra el
estado del núcleo español y permite inspección/instalación local en modo desarrollador.
El mensaje humano agrupado se genera exclusivamente en Python y se comparte sin duplicar
lógica léxica en JavaScript. JSON developer conserva POS, IDs, versiones y procedencia.
