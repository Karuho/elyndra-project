# Controlled Online Gateway

## Rejected HTTP responses

Executions after this correction audit only status/class, normalized final host, bounded redirect
hosts and `resumable=false`; audit JSON is the persistent source under schema 50. The body is not
read. A proven empty initial partial without a strong ETag is removed and its job is `failed`.
Earlier records are unchanged: their exact status is unknowable and never inferred or backfilled.

## Phase 4 supervised official-pack pipeline

### Post-Phase-4 CLI activation correction

Normal application composition remains globally offline and `network_allowed=true` remains invalid in
persistent configuration. `online plan-download` is now a deterministic, read-only preview with a
stable digest and no database, approval, DNS, cache or transport effect. `online approve-download`
reconstructs trusted fields, requires that digest and persists the immutable operation without network.
Only `execute-download` or `resume-download` can obtain a process-local capability bound to command,
operation and plan hash; a fresh approval is then consumed by the existing `NetworkPermit` path. The
capability dies with the CLI process and is not reachable from web, chat, models, skills or planners.

Phase 4 composes, without merging authority, the official source registry, closed acquisition
descriptor, Phase 3 download manager, offline bundle validator, private staging area and existing
language-pack registry. The descriptor pins the official URL, exact size, SHA-256, pack ID and
version. The downloaded manifest derives only same-release asset URLs and exact asset constraints.
Each asset still uses the Phase 3 permit, resolver, TLS, lock, cache, quarantine and resume path.

Installation is a separate local operation whose immutable plan binds account, bundle/version,
descriptor hash, manifest hash, parse-result hash, managed destination and `enable=false`. Successful
downloads remain inert cache entries. The parser never executes content. Web GETs only observe state;
foreground network execution remains CLI-only to avoid a worker, scheduler or blocking web request.

## Phase 3 transport boundary

Phase 3 implements foreground HTTPS HEAD/GET, fixed A/AAAA resolution, peer pinning, TLS verification,
allowlisted redirects, bounded headers, identity encoding, exact Content-Length, streaming SHA-256,
private partial/cache/quarantine storage, atomic promotion, strong-ETag resume, cancellation and one
global flock. Transport exists but startup, Online mode and source reads never invoke it.

Tests use an explicit injected loopback resolver/connector. Production cannot enable loopback through
configuration. The real public source remains untested because the pack repository is not currently
anonymously accessible. Remote installation, scheduler execution, autoupdate and telemetry remain
absent.

## Phase 2 implemented boundary

Phase 2 implements schema 50, bounded policy, pinned-source validation, immutable planning,
existing-store single-use approvals, operation state and allowlisted auditing. It does not implement
a download manager or real transport. `GatewayTransport.execute()` always raises
`gateway_transport_unavailable`; no Phase 2 path opens a socket.

Global and account switches default to false. Online mode is inert. Planning shows only a query-free
HTTPS URL, exact size/hash, source and privacy summary. No installation, enablement, telemetry,
automatic update or startup lookup occurs. Shared tables contain public metadata; user descriptors,
preferences and operations remain isolated per vault.

## Status and purpose

This is the Phase 1 architecture for Elyndra `0.8.9-alpha`. No network or runtime behavior is
implemented by this document. The gateway is a narrow acquisition boundary for explicitly pinned,
public artifacts; it is not a browser, remote retrieval system, model tool or update service.

The first integration is the already published Spanish Core bundle. Normal dictionary lookup remains
local and performs no freshness check.

## Existing architecture and reuse

The current runtime already provides useful primitives:

- `ElyndraPaths` separates XDG config, data, state and cache and creates private directories.
- `for_account()` isolates each account's data, state and cache; the root database remains the
  installation registry.
- `ApprovalStore` binds a random, expiring, single-use token to a chat and request fingerprint.
- stored assistant plans demonstrate immutable previews and one-way transitions.
- `AuditRepository` records structured JSON and redacts common secret keys.
- `LocalScheduler` provides a process lock and clean interruption semantics.
- `LanguageBundleService` verifies compatibility, size, archives, hashes and rollback before local
  installation; `LanguagePackRegistry` installs shared verified packs disabled by default.
- CLI and web already meet through one `ElyndraApplication` and developer-mode authorization.

These components must not be duplicated, but they also must not be reused outside their contracts.
The web `ApprovalStore` is in-memory, chat-specific and skill-shaped; the gateway needs durable,
account-scoped approval records plus a non-serializable capability. The scheduler is account-local and
periodic, so it must not run downloads. Its lock-file technique may be reused in a new global download
lock. `AuditRepository` needs gateway-specific field allowlisting because generic key redaction does
not remove query strings or response bodies. `LanguageBundleService` remains local-only.

## Current Local/Online control

`AppConfig` currently reads installation-wide `[privacy].offline` and `network_allowed`; loading fails
if network is enabled. Web bootstrap exposes those values and `app.js` renders Local as active when
offline or network is disallowed. Clicking Online only displays that a future gateway is planned.
Consequently there is no current per-account online preference and no current network authority.

Schema 50 replaces this presentation input with an account-vault preference. Installation config
still provides a hard administrative ceiling: absent or disabled gateway configuration wins over an
account's `restricted_online` preference. The preference is capability intent, not approval.

## Authority graph

```text
authenticated user
  -> account-scoped gateway service -> exact preview -> single-use NetworkPermit
                                      -> DownloadManager -> GatewayTransport
                                                           -> public HTTPS only
  -> separate install approval -> LanguageBundleRemoteService
                                  -> existing local LanguageBundleService

model / tutor / planner / skill -- no reference or call path --> gateway transport
scheduler / startup / lookup    -- no invocation path --------> download manager
```

Dependency injection constructs transport only inside a gateway composition root. The application
may expose high-level gateway methods, but model contexts and `SkillContext` receive only immutable
result data. Protocols passed to models must not include callables. Imports from `online_gateway` are
forbidden in `engines/`, `tutors.py`, `orchestration.py`, `cognitive_executive.py` and `skills/`, and a
static regression test enforces that rule.

## Contracts

### `OnlineGatewayPolicy`

Pure, deterministic policy. It accepts an account preference, source and artifact descriptor, method,
redirect target, DNS answers and configured limits. It returns an allow/deny decision plus stable
reason code. It never opens sockets or mutates state.

It enforces HTTPS, `HEAD`/`GET`, public resources, `official-pinned` or `user-pinned`, exact size/hash,
query-free canonical URL, bounded redirects and bytes, prohibited ports and addresses, and a disabled
default. All IPs returned by resolution must be globally routable; mixed public/private answers fail.

### `NetworkPermit`

An opaque, process-local, non-serializable capability created only after consuming one durable
approval. It binds account ID, download ID, source revision, descriptor SHA-256, method set, expiry and
a random nonce. Copy, pickle, JSON and string representations must not reveal the nonce. It is consumed
atomically before the first connection attempt; redirects remain inside the same exact operation.
Resume consumes a new approval and creates a new permit.

### `TrustedSourceRegistry`

Reads root-owned `official-pinned` and `user-pinned` source revisions. A source revision is immutable;
editing creates a new revision and disables no history silently. Official descriptors ship as reviewed
metadata. User-pinned sources require developer mode and explicit review. No community marketplace,
remote catalog refresh or private GitHub authentication exists in 0.8.9.

### `RemoteArtifactDescriptor`

Immutable value object with source revision ID, artifact ID/version, canonical HTTPS URL, filename,
media type, exact size, SHA-256, optional ETag and Last-Modified, and optional manifest/bundle identity.
It validates bounded ASCII identifiers, a safe filename, a permitted port and no username, password,
fragment or query. The canonical descriptor hash covers every security-relevant field.

### `GatewayTransport`

The only module allowed to use DNS, TLS and HTTP. It accepts a consumed permit and validated
descriptor; exposes bounded `head()` and streaming `get()` results; disables proxy discovery, cookies,
authentication, transparent content decoding and automatic redirects. It resolves and validates every
hop, connects to a selected validated IP, sends the canonical Host/SNI name, verifies the system trust
store and hostname, and rejects TLS downgrade. Redirects are returned to policy for revalidation.

### `DownloadManager`

Owns state transitions, the global lock, `.part` lifecycle, incremental hashing, cancellation,
durable progress checkpoints and atomic finalization. It does not install. It accepts no arbitrary URL:
only a stored descriptor revision. It stops when length, time, redirect or disk limits are exceeded.

### `DownloadRecord`

Root-owned metadata for one shared artifact acquisition. It contains public IDs, descriptor hash,
state, byte counters, validators, safe relative paths, verification outcome, timestamps and stable
errors. It contains no approval token, prompt, response body, credential or raw query.

### `GatewayAudit`

An allowlisting adapter over the account-vault `AuditRepository`. It records actor/account, operation
ID, source/artifact IDs, descriptor hash, method, coarse hostname hash, byte counts, redirect count,
state transition and error code. It rejects unknown fields and never records bodies, prompts, complete
URLs, temporary query strings, DNS search suffixes or headers that may contain secrets.

### `LanguageBundleRemoteService`

Coordinates a verified download with the existing local bundle service. It confirms the final file
still matches the stored size/hash and constructs a local manifest directory view. Installation then
requires its own fresh account-scoped approval and calls `LanguageBundleService.inspect/install`.
Transport and permits are not constructor dependencies of `LanguageBundleService`.

## Configuration and storage

Installation configuration under `~/.config/elyndra/config.toml` gains a bounded `[online_gateway]`
administrative section: `enabled=false` by default, redirect/timeout/size limits and an allowed port
set containing only 443. It contains no proxy, cookie, credential or automatic-update options.

Paths follow the existing XDG roots:

```text
~/.local/share/elyndra/online-gateway/downloads/<sha256>/<safe-filename>
~/.local/state/elyndra/online-gateway/download.lock
~/.cache/elyndra/online-gateway/partials/<download-id>.part
~/.local/share/elyndra/alexandria/language-packs/       # existing installed packs
~/.local/share/elyndra/accounts/<account-id>/elyndra.db # private consent/history
```

Directories are `0700`; files and the global lock are `0600`. Database paths store safe relative paths
only. Partials and final cache must be on suitable filesystems for the specified atomic rename; if not,
copying is refused rather than presented as atomic.

## Source types and official descriptor

Only `official-pinned` and `user-pinned` exist. The initial official source is:

- source ID: `elyndra-official-language-packs`
- repository identity: `Karuho/elyndra-language-packs`
- immutable release: `spanish-core-2026.08.01-r1`
- bundle: `elyndra-es-core` / `2026.08.01-r1`
- compatibility: `>=0.8.8a0,<0.9.0a0`
- manifest SHA-256: `b45b0aecb2c32ff5c94ad04143f90c43ff46698e6dbccbca31645c1c60f009db`
- bundle content SHA-256:
  `7253976778a27aa3eea81934bf4b897bcf0e57806bc31361a5fd8bf13e2d1bde`

The runtime descriptor must additionally pin the public release URL and exact manifest size from the
published artifact. Those values are intentionally not guessed from the local metadata-only repository;
they require human review before Phase 2. Each of the four assets is independently pinned by the
existing bundle manifest, including name, exact size and SHA-256.

## User flows

### Download

1. Authenticated user enables `restricted_online` for that account; no traffic occurs.
2. User selects a pinned artifact. Elyndra builds a local preview without network.
3. User approves the exact descriptor. The vault record is consumed and yields one permit.
4. A foreground download acquires the global lock, performs validated HEAD/GET and streams to `.part`.
5. Elyndra hashes and atomically finalizes the exact bytes, then marks verification separately.
6. The artifact remains uninstalled and disabled.

### Install and enable

1. User reviews the verified local artifact and licenses.
2. A separate installation approval calls the existing local bundle service.
3. Packs install disabled unless enablement is separately and explicitly requested in the install
   confirmation. Enablement remains a distinct state even if one UI dialog presents both decisions.

### Resume

Interrupted state persists, but restart performs no work. User requests resume; Elyndra repeats policy,
DNS and remote identity checks and asks for a fresh approval. Resume requires a strong unchanged
validator, `Range` support and a valid `206 Content-Range` beginning at the exact partial size. If the
server ignores Range or validators changed, the partial is quarantined and a new full download requires
a new approval.

## State machines

Download state:

```text
planned -> awaiting_approval -> approved -> connecting -> downloading
planned|awaiting_approval|approved -> cancelled
connecting|downloading -> interrupted|failed|cancelled|quarantined
interrupted -> awaiting_approval (explicit resume only)
downloading -> downloaded
downloaded -> quarantined (later cache check failure)
```

`approved` is durable approval state; the permit itself is not durable. On process restart, approved,
connecting or downloading records become `interrupted`, never automatically continue.

Verification state is independent:

```text
pending -> verifying -> verified
pending|verifying -> failed|quarantined
verified -> quarantined (subsequent integrity mismatch)
```

Installation state is independent:

```text
not_installed -> awaiting_approval -> installing -> installed
awaiting_approval -> cancelled
installing -> failed -> awaiting_approval (new explicit retry)
installed -> rollback_required -> rolled_back|failed
```

Enablement state is independent: `disabled -> enabling -> enabled`; verification failure forces
`enabled -> disabled` and may quarantine the cached artifact. Download verification never enables a
pack. Installation rollback removes only packs newly created by that attempt, matching 0.8.8 behavior.

## CLI and web contract

Planned CLI commands use one application service:

```text
elyndra online status
elyndra online mode local|restricted-online --approve
elyndra online sources
elyndra online artifact-show SOURCE ARTIFACT
elyndra online download-plan SOURCE ARTIFACT
elyndra online download-run DOWNLOAD_ID --approve
elyndra online download-resume DOWNLOAD_ID --approve
elyndra online download-cancel DOWNLOAD_ID --approve
elyndra online downloads
elyndra online download-show DOWNLOAD_ID
elyndra online install-plan DOWNLOAD_ID
elyndra online install-run INSTALL_ID --approve
```

Planned loopback APIs mirror those operations:

```text
GET  /api/online/status
POST /api/online/mode
GET  /api/online/sources
GET  /api/online/artifacts/{id}
POST /api/online/downloads/plan
POST /api/online/downloads/{id}/run
POST /api/online/downloads/{id}/resume
POST /api/online/downloads/{id}/cancel
GET  /api/online/downloads
GET  /api/online/downloads/{id}
POST /api/online/installations/plan
POST /api/online/installations/{id}/run
```

Mutations require the authenticated session, same-account record ownership, CSRF/write token and
explicit approval. User mode may control Local/Online, download an official pinned artifact and review
its own history without seeing local paths or full technical hashes. Adding/editing `user-pinned`
sources, raw diagnostics and quarantine administration are developer-only and server-authorized with
403, not merely hidden. Both modes see accurate no-traffic status.

## Error codes and recovery

Stable codes include `GATEWAY_DISABLED`, `LOCAL_MODE`, `APPROVAL_REQUIRED`, `APPROVAL_EXPIRED`,
`APPROVAL_REPLAYED`, `APPROVAL_ACCOUNT_MISMATCH`, `DESCRIPTOR_CHANGED`, `SOURCE_DISABLED`,
`URL_INVALID`, `METHOD_DENIED`, `DNS_PRIVATE_ADDRESS`, `DNS_REBINDING`, `REDIRECT_DENIED`,
`TOO_MANY_REDIRECTS`, `TLS_INVALID`, `HTTP_STATUS_DENIED`, `SIZE_REQUIRED`, `SIZE_MISMATCH`,
`LIMIT_EXCEEDED`, `DISK_SPACE_LOW`, `GLOBAL_DOWNLOAD_BUSY`, `REMOTE_IDENTITY_CHANGED`,
`RANGE_UNSUPPORTED`, `PARTIAL_CORRUPT`, `HASH_MISMATCH`, `CANCELLED`, `INTERRUPTED`,
`CACHE_CORRUPT`, `MANIFEST_REJECTED`, `BUNDLE_INCOMPATIBLE`, `INSTALL_FAILED` and
`ROLLBACK_FAILED`.

Failures preserve bounded diagnostics and never auto-retry. Security failures quarantine bytes.
Transient connection failures become interrupted only when a safe partial exists; otherwise failed.
Cancellation closes the response, flushes the checkpoint and leaves a non-resumable partial
quarantined unless identity and hash-prefix metadata are complete.

## Retention and privacy

Shared verified artifacts are content-addressed and may be reused across accounts only after the new
account grants its own installation approval. Partials are retained for at most seven days; failed or
quarantined bytes for at most seven days unless the owner deletes them sooner. Completed shared cache
uses a 90-day last-access policy, but active installed packs prevent eviction. Private approval records
expire after two minutes; consumed/cancelled records and account history are retained 90 days, then
reduced to minimal audit facts. Account deletion removes its preferences and history, not shared bytes
still referenced by other accounts. No URL query, prompt or HTTP body is retained.

## Compatibility and rollback

Schema 50 is a forward-only root and vault migration from exact schema 49. It does not change language
pack schema 1, bundle schema 1 or the 102 skills. Existing 0.8.8 packs, offline lookups, local bundle
inspection/install and user overlays remain unchanged. Downgrading code after schema migration is not
supported; operational rollback disables the gateway and preserves schema 50/history. A failed
migration transaction leaves schema 49 intact.

Likely regressions are accidental movement of shared pack paths into account cache, treating the old
installation flag as account consent, adding transport to `LanguageBundleService`, enabling installed
packs automatically, making the scheduler resume downloads, or changing existing bundle validation.

## Phase plan

1. Phase 1: architecture, specification and threat model only.
2. Phase 2: schema 50, pure models/policy/source registry/approvals and synthetic tests; no real network.
3. Phase 3: hardened transport and download manager tested only against controlled loopback servers.
4. Phase 4: CLI/web parity, Spanish Core remote adapter, install separation and recovery tests.
5. Phase 5: full validation, explicitly authorized real public download test, documentation and human
   release review. The real test is never implied by Phase 1 or normal test execution.

The estimated implementation touches `db.py`, `paths.py`, `config.py`, `application.py`, `cli.py`,
`web/server.py`, `web/static/app.js`, the new gateway package, `language_packs/remote.py`, focused tests
and mandatory release documentation. It must not change tutor, model or skill authority.
