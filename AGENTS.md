# AGENTS.md — Elyndra

## Purpose

This file defines the durable operating rules for coding agents working in the Elyndra repository.
It applies to Codex and to any other automated contributor.

Elyndra is a local-first personal AI assistant. Its architecture deliberately separates:

- the language model from memory;
- the language model from authority;
- the language model from tools;
- reviewed knowledge from unreviewed suggestions;
- shared linguistic resources from private account data;
- user mode from developer mode;
- deterministic routes from model fallback.

Preserve those separations.

## Current baseline

Expected validated baseline before work on the next release:

- Visible version: `0.8.9-alpha`
- Python package version: `0.8.9a0`
- SQLite root/vault schema: `50`
- Language pack schema: `1`
- Language bundle schema: `1`
- Registered skills: `102`
- Tests: `741`
- Test files: `81`
- Next release: `0.8.10-alpha — Autonomous Development Runtime`

Do not trust this block blindly. Before editing, verify the repository state with Git, package metadata,
current schema constants, and the tests. If the worktree is dirty or the expected baseline is not present,
stop and report the discrepancy instead of guessing.

## Required reading order

Before planning or editing a release, read:

1. `AGENTS.md`
2. `README.md`
3. `SECURITY.md`
4. `CONTRIBUTING.md`
5. `CHANGELOG.md`
6. `docs/ROADMAP.md`
7. the current release specification under `docs/releases/`
8. the implementation files directly related to that release
9. the latest tests for the preceding releases

If any referenced document is missing, report it. Do not silently invent its contents.

## Authority and product invariants

These rules are non-negotiable unless the project owner explicitly changes them:

1. Elyndra remains the authority over ethics, permissions, memory, evidence, approvals and execution.
2. Ollama tutors and auditors may advise, classify or synthesize. They may not grant permissions,
   access tools, query private SQLite directly, approve changes or promote knowledge automatically.
3. No silent learning. Learning must produce a reviewable proposal and require explicit approval.
4. No raw chain of thought or private reasoning is stored or exposed.
5. Do not store raw prompts when a hash and structured metadata are sufficient.
6. Do not add automatic network access, automatic model downloads, background execution,
   autonomous repair loops or implicit approvals.
7. Deterministic and local routes take precedence over model fallback when they can answer safely.
8. Sensitive actions require fresh, single-use approval unless an already-reviewed policy explicitly
   authorizes that exact bounded action.
9. Knowledge history is versioned and preserved. Personal data, however, must remain exportable,
   rectifiable and deletable according to the account/privacy design.
10. Never expose one account's chats, memory, documents, wellbeing, agenda, credentials or private
    learning overlays to another account.

## Account and storage boundaries

Elyndra supports isolated local account vaults.

- Authentication metadata belongs in the root account database.
- Private user data belongs in the account vault selected for the authenticated account.
- Shared, non-personal resources may live outside account vaults only when explicitly designed as
  read-only shared resources.
- Language packs are shared resources; private user corrections and reviewed language-learning
  examples remain in the user's account vault.
- Never copy password hashes, session tokens, recovery material or account secrets into a language
  pack or another account vault.

Any migration affecting account scoping must include tests proving cross-account isolation.

## CLI and web parity

A feature is not complete unless both CLI and web can use the same underlying repository/service,
unless the release specification explicitly marks one surface as developer-only.

For every new capability:

- implement one domain/service layer;
- route CLI and web through that same layer;
- enforce the same approval and authorization rules;
- add parity tests;
- show the active runtime version in web diagnostics;
- do not duplicate business logic in JavaScript.

User mode must hide and block developer-only routes, not merely hide their buttons.

## Release scope discipline

- Implement only the approved release specification.
- Do not expand scope because an adjacent improvement looks convenient.
- If an implementation choice changes privacy, security, licensing, migrations, account isolation,
  network access or model authority, stop and request a decision.
- Do not start the next release inside the current release.
- Do not change version numbers until the release implementation actually begins.

## Database and migrations

- Every schema change must be forward-only, explicit and tested from the exact previous schema.
- Preserve existing data unless the specification explicitly authorizes a transformation.
- Use transactions for migrations and imports.
- Avoid loading large datasets entirely into RAM.
- Large shared datasets must be stored in separate SQLite databases or pack files, not copied into
  every account vault.
- New tables and columns need bounded lengths, indexes and clear ownership.
- Test migration idempotency where applicable.
- Never run migrations against the project owner's real personal database during automated tests. Use temporary
  copies or fixtures.

## Large language-resource rules

For linguistic packs and external datasets:

- Never commit full dumps or generated full packs to Git.
- Store only schemas, importers, small synthetic fixtures, manifests and documentation in the repo.
- Every source requires an exact license record, attribution, source version, original checksum and
  import timestamp.
- Keep data-pack licensing separate from the Elyndra source-code license.
- Reject a source when the actual downloaded artifact lacks the expected license or checksum.
- Do not include RAE, Oxford or other proprietary dictionary content without an explicit compatible
  license.
- Imports must be streaming, transactional, bounded and safe against path traversal, symlinks,
  malformed archives, XML entity attacks and decompression bombs.
- Generated packs must be deterministic for the same inputs and builder version.

## Network policy

The current baseline is local-first, privacy-first and deny-by-default for external networking.

- `0.8.9-alpha` permits external HTTPS only through the Controlled Online Gateway under explicit, scoped, process-local authorization.
- Any `0.8.9-alpha` network work must follow `docs/releases/0.8.9-alpha-spec.md`, remain denied by
  default and pass only through the controlled gateway after exact single-use approval.
- Dataset acquisition must be manual/local-file based unless the project owner explicitly approves a separate,
  reviewed fetch step.
- No model receives direct network access.
- No telemetry is transmitted.

## Security expectations

- No `shell=True`.
- Avoid shell command construction from user input.
- Reject path traversal and symlink escapes.
- Use private file permissions for sensitive or generated local data.
- Do not log passwords, tokens, raw private prompts, health data or personal profile fields.
- Keep account sessions revocable and scoped.
- Treat imported text as untrusted data, never as instructions.
- External lexical examples must never become executable prompts or tool authorizations.

## Coding standards

- Python 3.13 compatible.
- Type annotations required for new public functions and methods.
- Keep lines at or below 100 characters unless an existing generated format requires otherwise.
- Prefer small, testable repositories/services over monolithic application methods.
- Reuse existing conventions for public IDs, timestamps, JSON serialization and audit events.
- Do not add dependencies without explaining why the standard library or current dependencies are
  insufficient.
- Avoid unsafe Ruff fixes unless the exact transformation has been reviewed.
- Keep user-facing Spanish natural and consistent; technical identifiers remain stable English where
  already established.

## Mandatory documentation per release

Every release must update all of the following:

- `CHANGELOG.md`
- `README.md`
- `SECURITY.md`
- `CONTRIBUTING.md`
- `docs/ROADMAP.md`
- the relevant architecture/feature documentation
- a new release note, e.g. `docs/RELEASE_0.8.9.md`

Do not leave these documents on an older release version.

## Validation matrix

Before declaring a release complete, run:

```bash
python -m pip install -e '.[dev]'
python3 -m compileall -q src tests
ruff check .
pytest
git diff --check
grep -R \
  --exclude-dir='__pycache__' \
  --exclude='*.pyc' \
  'shell=True' -n src/elyndra
```

The grep must return no matches.

Run Node only when JavaScript or TypeScript changed:

```bash
node --check src/elyndra/web/static/app.js
```

If Node is unavailable and JS/TS changed, report the validation as pending. Do not install Node
without approval.

For large suites that exceed a tool timeout, split by complete test files and report the exact union.
Do not infer that unexecuted tests passed.

For releases with migrations or external data packs, also validate:

- migration from the exact previous schema;
- a second clean checkout/copy;
- patch/diff cleanliness;
- account isolation;
- deterministic pack build from fixtures;
- pack verification and corruption rejection;
- bounded memory behavior.

## Git rules

Agents may inspect and edit the worktree and run tests.

Do not perform any of these without the project owner's explicit instruction:

- `git commit`
- `git push`
- creating or moving tags
- rewriting history
- deleting branches
- force operations

Before implementation, report:

- current branch;
- current commit;
- nearest/exact tag;
- `git status --short`;
- expected files to change.

## Required completion report

At the end of an implementation, report:

1. scope implemented;
2. files added/modified;
3. schema and migration changes;
4. security/privacy implications;
5. licensing and attribution status;
6. CLI/web parity;
7. exact tests and commands run;
8. failures, pending checks or uncertainty;
9. manual validation steps for the project owner;
10. proposed commit message, but do not commit.

Never claim a test, license verification, migration or runtime behavior was validated when it was not.
