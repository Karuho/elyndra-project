# Elyndra 0.8.9-alpha — Controlled Online Gateway, Phase 4

## Post-Phase-4 HTTP observability correction

Future HTTP rejections are diagnosable through sanitized audit JSON and are not resumable. Empty
initial partials without strong ETags are removed. Schema and all authority boundaries remain intact;
the third public attempt's precise status cannot be recovered.

Phase 4 completes supervised official-pack acquisition while preserving Phase 3's network boundary.
An anonymous pinned descriptor leads to separately approved manifest and asset downloads, offline
validation, private staging, and a new exact approval for local installation. Nothing auto-installs,
auto-enables, executes, updates, schedules or transmits telemetry. Web load is network-inert and public
GitHub validation was not performed.

The first authorized public attempt stopped before network because its authorization transcribed a
different manifest hash. Offline audit confirmed the pinned `b45b0aec...009db` value. A subsequent
attempt exposed two integration gates—persistent networking was intentionally invalid and plan preview
created approval too early—so no public traffic occurred. This correction adds read-only preview,
separate persisted approval and exact ephemeral CLI activation. The real public test remains pending.

Phase 3 completes the foreground acquisition transport boundary: hardened HTTPS, fixed DNS answers,
peer verification, redirects, bounded headers, exact streaming, private atomic storage, hash
verification, cache/quarantine, cancellation, strong-ETag resume and global locking. It was tested
only with controlled loopback dependencies and did not contact the public release.

The repository ships an exact official release URL and expected hashes, but Phase 4 did not contact
it. Anonymous public validation remains blocked pending separate authorization; there is no scheduler,
automatic update or telemetry.

This release advances root and vault schema to 50 and implements controlled-online authority without
transport. Global gateway and account Online mode default false; Online alone causes no traffic.

It adds official/user-pinned descriptors, exact immutable plans, fixed limits, existing-store
single-use approval, process-only nonserializable permits, operation state and allowlisted audit.
The official Spanish Core descriptor pins the approved release URL, 5009-byte manifest and hashes.

CLI and loopback APIs expose status, preference, source, planning and local installation controls.
Download execution stays foreground CLI-only. There is no automatic download, enable, telemetry,
autoupdate or background work, and Ollama, tutors, planners, skills and models receive no network
capability.

## Propuesta de prueba pública controlada

This proposal is not executed. After separate approval, use the existing `elyndra online` descriptor,
plan and foreground download commands to perform one HTTPS GET for the exact pinned manifest at
`https://github.com/Karuho/elyndra-language-packs/releases/download/spanish-core-2026.08.01-r1/elyndra-es-core-2026.08.01-r1.bundle.json`.
The primary host is `github.com`; any redirect host must already be explicitly pinned by the official
descriptor policy, otherwise stop. Maximum: one initial request plus the bounded redirect count,
5,009 bytes, expected SHA-256
`b45b0aecb2c32ff5c94ad04143f90c43ff46698e6dbccbca31645c1c60f009db`. Storage is the managed gateway
cache, without displaying its absolute path. Evidence: sanitized operation/audit state, byte count,
TLS/policy success and final digest. Residual risks are public-host availability, certificate or DNS
change, and a policy-rejected redirect. Exact commands requiring approval are:

```text
elyndra online mode-set online
elyndra online descriptor-show elyndra-official-language-packs
elyndra online plan-download elyndra-official-language-packs
elyndra online execute-download OPERATION_ID
elyndra online cache-verify ARTIFACT_KEY
```

Cleanup discards a partial on failure; a verified cache entry is retained only for the next separately
approved step. Success means exact size/hash and verified cache metadata. Stop on any redirect-policy,
DNS/SSRF, TLS, size, digest, timeout or quarantine event. Asset downloads and installation require
their own later approvals and are outside this proposed manifest-only check.
