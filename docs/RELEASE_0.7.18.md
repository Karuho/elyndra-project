# Elyndra 0.7.18-alpha

## Controlled change proposals and reviewable patches

Elyndra 0.7.18-alpha adds an owner-reviewed writing boundary on top of the supervised orchestration introduced in 0.7.17. The local language model may propose complete replacement text for one to three explicitly selected project files, but it never receives filesystem tools and it never writes directly.

Elyndra validates the exact requested paths, freezes the original and proposed SHA-256 values, generates a bounded unified diff, stores the proposal in SQLite and requires a second explicit approval before applying it once.

## Change flow

```text
explicit project and files
→ bounded local-model JSON proposal
→ deterministic path and content validation
→ frozen hashes and unified diff
→ owner review
→ single-use approval
→ staged per-file replacement with best-effort rollback
→ persistent status and audit history
```

The CLI exposes:

```bash
./scripts/elyndra-dev assistant change-plan /ruta/proyecto \
  --file src/app.py \
  --instruction 'Corrige el error'

./scripts/elyndra-dev assistant change-show ID
./scripts/elyndra-dev assistant change-apply ID --approve
./scripts/elyndra-dev assistant change-reject ID --approve
./scripts/elyndra-dev assistant changes
```

The loopback chat can also generate a proposal when the owner asks to modify explicit absolute file paths. The approval token is bound to the exact chat request and stored proposal ID. Cancelling writes nothing. Applying the same proposal twice is rejected.

## Filesystem boundaries

This initial writing release is intentionally narrow:

- one to three exact UTF-8 text files;
- existing authorized project roots only;
- complete file replacements, represented as a unified diff;
- existing files may be updated;
- new files may be created only when their parent directory already exists;
- current file hashes must still match the frozen proposal;
- writes are staged in the destination directory and replaced atomically;
- a multi-file failure triggers best-effort rollback;
- existing permission bits are preserved.

Elyndra rejects path traversal, absolute relative-file arguments, symbolic links, protected repository and dependency folders, private-key formats, common credential files and unsupported binary file types.

## Explicit exclusions

0.7.18-alpha does not provide:

- autonomous file selection;
- deletion or renaming;
- directory creation;
- arbitrary patch input;
- direct model access to files or write APIs;
- dependency installation;
- network access;
- shell commands;
- automatic commit or push;
- automatic validation after applying a proposal;
- background or recursive editing loops.

The owner can run the existing supervised validation plans after applying a proposal. A later release may connect approved changes with an explicit validation-and-repair loop, but 0.7.18 does not continue autonomously.

## Persistence and control center

The SQLite schema advances to version 26 with `assistant_change_proposals`. Each record stores the frozen proposal, diff, status and bounded result metadata. States include `proposed`, `applying`, `applied`, `rejected`, `stale` and `failed`.

The loopback control center exposes recent proposals through:

```text
/api/control/change-proposals
```

The existing `assistant_action_runs` history and 100-skill registry remain unchanged.
