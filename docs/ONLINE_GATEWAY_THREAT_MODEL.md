# Controlled Online Gateway threat model

## HTTP diagnostic minimization

Remote bodies, reasons, headers, locations, paths, queries, IPs and TLS details do not cross the
diagnostic boundary. Cleanup requires a regular, single-link, zero-size deterministic local partial.

## Phase 4 mitigations

The activation correction addresses persistent-network misconfiguration and approval-before-preview.
Persistent network enablement remains rejected. Preview is pure and deterministic; approval rebuilds
trusted fields and checks the preview digest; CLI execution validates the stored plan and requires a
command/operation/plan-bound ephemeral capability. Web cannot mint or consume download authority.

Phase 4 adds descriptor substitution, archive confusion and install-authority escalation to the threat
model. Closed official schemas and immutable hashes reject descriptor mutation. Offline validation
rejects traversal, absolute paths, links, special members, duplicate/case/Unicode collisions, unsafe
nested archives and decompression-limit abuse. A consumed download approval cannot install; a fresh
install approval binds the verified parse result and managed destination. No bundle content is code.
Opening the loopback control surface cannot start or resume network work.

## Phase 3 mitigations

Production rejects HTTP, non-443 ports, userinfo, IP literals and any A/AAAA answer that is not
globally routable. The complete answer set is pinned before connection and peer mismatch fails as
rebinding. Certificate/hostname checks and TLS 1.2 minimum are mandatory. Every redirect repeats the
same checks and must remain in the frozen host allowlist.

HTTP headers are bounded while read, transfer encoding and compression are rejected, and bodies may
not exceed exact planned bytes. Filesystem controls reject traversal, symlinks, non-regular files and
hardlinks; successful files are fsynced and atomically renamed. Tests use controlled loopback
dependencies only and never contact the public pack host.

## Phase 2 exposure

Phase 2 has no network attack surface because transport is unavailable. Implemented controls freeze
HTTPS-only credential-free non-`latest` descriptors, exact size/SHA-256, bounded plans,
account/operation/plan approvals, nonserializable single-use permits and query-free allowlisted
audit data. Startup and lookup do not contact a network.

SSRF, rebinding, redirects, TLS, streaming, resume and quarantine remain Phase 3 acceptance work.

## Security objective

Permit one explicitly approved public artifact acquisition without turning Elyndra, its models or its
local automation into general network clients. Downloaded content is hostile until exact verification
and remains untrusted data after verification.

## Assets and trust boundaries

Protected assets include private account data, credentials, loopback services, LAN/cloud metadata,
filesystem integrity, disk capacity, approval authority, audit privacy and verified language packs.
Untrusted inputs include source metadata, DNS, certificates, redirects, headers, bytes, archives,
manifests, license/attribution text and model-visible lexical content.

The main boundaries are account vault to root registry, gateway policy to transport, network to
partial cache, verified cache to local installer, and imported text to model context. Crossing one
boundary never grants authority at the next.

## Threats and required controls

| Threat | Required prevention/detection | Failure outcome |
|---|---|---|
| SSRF IPv4/IPv6 | Reject loopback, private, link-local, multicast, unspecified, reserved, documentation, carrier-grade NAT, IPv4-mapped and non-global addresses for every answer and redirect. Restrict port to 443. | `DNS_PRIVATE_ADDRESS`; no connect. |
| DNS rebinding | Resolve immediately before connection, reject mixed answers, pin one validated address for that connection and do not let the HTTP library resolve again. Re-resolve every retry/redirect. | `DNS_REBINDING`; quarantine partial if bytes exist. |
| Redirect abuse | Disable automatic redirects; validate canonical HTTPS URL, source policy, DNS and limit on every `Location`; reject relative ambiguity, credentials, query/fragment and unpinned final identity. | `REDIRECT_DENIED`. |
| TLS interception/downgrade | TLS 1.2+ with system trust and hostname/SNI validation; no custom CA, ignore-certificate flag, HTTP fallback or certificate pin bypass. | `TLS_INVALID`. |
| Environment proxies | Construct a proxy-free opener/client and clear proxy use explicitly; never honor `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` or `NO_PROXY`. | Startup self-test fails closed. |
| Cookies | No cookie jar and no `Cookie`/`Set-Cookie` persistence or forwarding. | Header ignored; audit contains no value. |
| Authentication leakage | Reject URL userinfo; never accept Authorization, GitHub tokens, SSH, netrc or credential helpers. | `URL_INVALID`. |
| Query leakage | Descriptors reject queries/fragments. Audit stores artifact/source IDs and hostname hash, not raw URLs. | Descriptor rejected before approval. |
| Missing/false length | Require pinned descriptor size and bounded HEAD/GET length. Missing `Content-Length` is rejected for initial downloads; streaming hard-stop catches under/overrun. | `SIZE_REQUIRED` or `SIZE_MISMATCH`. |
| Transparent compression/decompression bomb | Send `Accept-Encoding: identity`; reject encoded HTTP bodies. Archive extraction retains existing member-count, declared/unpacked-size and compression-ratio limits. | `LIMIT_EXCEEDED` or `MANIFEST_REJECTED`. |
| Archive traversal | Existing bundle inspector rejects absolute paths, `..`, special files and containment escapes. Recheck after download. | Quarantine; no install. |
| Symlink/hardlink/device archive entries | Reject all links and non-regular/non-directory entries; do not extract before inspection. | Quarantine. |
| Hash substitution | Descriptor revision is immutable and approval binds its canonical hash. Incremental SHA-256 and exact final hash are mandatory. | `HASH_MISMATCH`; quarantine. |
| Mutable tags/`latest` | Official and user sources must use immutable release IDs and exact asset URLs. `latest`, branch archives and unresolved aliases are rejected. | `DESCRIPTOR_CHANGED`. |
| Partial poisoning | Private exclusive-create `.part`, ownership/mode checks, stored prefix checkpoint and descriptor hash; never append to an unknown file. | `PARTIAL_CORRUPT`. |
| ETag change | Resume requires the same strong ETag, or an approved Last-Modified policy plus exact size; use `If-Range`. Weak/missing validators mean restart, not resume. | `REMOTE_IDENTITY_CHANGED`. |
| Range ignored | Require `206`, exact `Content-Range` start/end/total and identity encoding. A `200` to Range never appends. | `RANGE_UNSUPPORTED`. |
| Disk exhaustion | Preflight free space using pinned and unpacked sizes plus reserve; hard byte limits, ENOSPC handling and cleanup/quarantine. | `DISK_SPACE_LOW`; no install. |
| Concurrent downloads | One root-state advisory/file lock acquired non-blocking; database transition uses compare-and-set. | `GLOBAL_DOWNLOAD_BUSY`. |
| TOCTOU | Safe relative paths, `lstat`, no symlink parents, exclusive file creation, open-descriptor hashing, `fsync`, same-filesystem atomic rename and hash recheck immediately before install. | Quarantine or fail closed. |
| Malicious manifest | Bounded JSON depth/size/strings, exact schema, no executable hooks, path containment, fixed hashes/sizes/licenses and existing bundle compatibility checks. | `MANIFEST_REJECTED`. |
| Cross-account access | Preference, approvals and history live in each vault; every service call compares authenticated account ID. Shared cache contains only public bytes and non-personal metadata. | `APPROVAL_ACCOUNT_MISMATCH`; no disclosure. |
| Approval replay | Random hashed durable grant, exact descriptor/operation/account binding, short expiry and atomic single-use transition. Permit is process-only and one-use. | `APPROVAL_REPLAYED`. |
| Model prompt injection | Models receive neither raw remote manifests/license text nor gateway callables. Imported lexical text is quoted as data under existing bounded retrieval rules. | Content withheld/sanitized; no authority change. |
| Malicious license/attribution | Treat as display-only bounded text, escape for HTML, never concatenate into system/authority prompts, commands or audit fields. | Pack rejected if bounds/encoding fail. |
| Restart during download | Startup changes active states to `interrupted` without opening sockets; `.part` is inert until explicit resume and new approval. | No automatic traffic. |
| Cancellation race | Cancellation flag checked between chunks; close stream, flush checkpoint, release lock once; final rename requires non-cancelled compare-and-set. | `cancelled`; no final artifact. |
| Corrupted cache | Rehash before reuse/install and periodically only on explicit local verification; mismatch quarantines and invalidates reuse. | `CACHE_CORRUPT`. |
| Audit privacy | Gateway allowlist excludes prompts, bodies, query, full headers, cookies, credentials and local sensitive paths. Tests search serialized audit JSON. | Reject audit payload or redact before persistence. |

## HTTP policy details

Only status codes `200` for HEAD/full GET, `206` for a valid resume, and bounded redirects
`301/302/303/307/308` are understood. Other success codes and all authentication/challenge responses
fail. HEAD is advisory, never proof of identity. GET headers must independently match policy. Redirects
cannot change the pinned artifact identity merely because the host is public.

Timeouts are separate and bounded for DNS, connect, TLS/header and idle body reads; a total elapsed
deadline also applies. Header count and bytes are bounded. Connections are not pooled across downloads
or hosts. HTTP/2 support is not required in Phase 1; a later implementation must not accept server push.

## Resume safety

The checkpoint records descriptor hash, completed byte count, SHA-256 checkpoint strategy, strong
validator, expected total and last safe timestamp. Because standard hash objects are not safely
serializable, implementation may rehash the existing partial from disk before resuming; it must not
persist interpreter-internal hash state. Resume revalidates the partial length and prefix locally,
then remote identity and Range semantics. Any ambiguity restarts only after quarantining the old partial
and receiving a new approval.

## Installation boundary

Transport success means only “exact bytes downloaded.” Verification means exact size/hash and a valid
descriptor. Installation still runs the full local bundle inspection: compatibility, bundle hash,
asset hashes, safe archive contents, free space, licenses and pack database verification. Installation
approval cannot be inferred from download approval. Enablement cannot be inferred from either.

## Security test matrix

Use controlled loopback fixtures that simulate DNS and HTTP/TLS behavior without contacting the
Internet. Cover Local mode; online without approval; expired/reused/cross-account approval; HEAD/GET;
HTTP rejection; invalid TLS; IPv4/IPv6 SSRF; rebinding; redirect loops and disallowed hosts; missing,
false and excessive sizes; wrong hash; poisoned partial; changed ETag; ignored/invalid Range;
cancellation; restart; valid/corrupt cache; malicious manifests; incompatible bundles; failed install
and rollback; two concurrent accounts/downloads; audit redaction; and CLI/web parity.

Patch socket constructors in startup, scheduler, ordinary lookup, tutor, auditor, planner and skill
tests so any connection attempt fails the test. Add static import-graph tests proving model and skill
modules do not import the gateway. Exercise the actual Spanish Core only through a loopback fixture in
normal CI. A real public test is manual, opt-in, separately approved, excluded from default pytest and
run only in the final supervised phase.

## Residual risks requiring human review

- Python standard-library HTTP/DNS APIs do not provide a kernel network sandbox; application controls
  reduce risk but cannot contain a compromised interpreter.
- The exact official manifest URL and byte size are absent from the audited metadata and must be pinned
  by a human before implementation.
- Whether to require a strong ETag for all resumable official assets or disable resume when absent.
- Retention periods and shared-cache deletion semantics may need privacy/product confirmation.
- System CA compromise, hostile local DNS and local administrator tampering remain outside the
  application's complete control.
- Archive safety limits must be reconciled with the existing 2.58 GB unpacked Spanish Core before
  setting production defaults.
