# Elyndra 0.4.2 development release

This release completes the durable-memory MVP required before the first local HTML inspector. The
memory architecture remains disk-first: only the current working window and a small retrieved
context stay in process memory.

## Implemented

- SQLite schema version 4 with structured chat memory, episodic records, reviewable semantic-memory
  proposals, correction records and cold-transcript metadata.
- Deterministic structured summaries with bounded topics, decisions, pending work, outcomes and
  recent context.
- Lazy migration of existing 0.4.0/0.4.1 rolling summaries into the structured representation.
- Episodic extraction for decisions, pending work, problems, outcomes and owner corrections.
- FTS5-backed episodic lookup with a SQLite `LIKE` fallback.
- A strict retrieval budget of at most three semantic memories, two episodic records, two document
  fragments and a bounded current-chat summary.
- Preference, routine and rule proposals that remain pending until the owner approves them.
- Proposal inspection, editing, approval and rejection commands.
- Semantic-memory editing and logical deletion.
- Episodic-memory editing and logical deletion, followed by summary rebuilding.
- `/correct ...` inside a chat to record a better answer without automatically training or changing
  model weights.
- Optional gzip-compressed JSONL cold transcripts for chats that used full retention.
- Optional pruning of full turns from SQLite only after the archive file is written and hashed.
- Chat `/memory` inspection plus CLI commands for episodes, proposals, corrections and archives.

## Storage model

- Working memory: at most six recent turns in RAM for the active process.
- Structured session memory: SQLite on disk.
- Episodic memory: SQLite on disk, linked to a chat and optional project.
- Semantic memory: SQLite on disk, promoted only after owner review or an explicit remember command.
- Cold transcript: `~/.local/share/elyndra/transcripts/YYYY/chat_....jsonl.gz` with mode `0600`.

The compressed cold archive is not encrypted yet. Local encryption, export/import and key-management
hardening remain later work and do not block the read-only HTML inspector.

## Main commands

```text
elyndra memory episodes
elyndra memory proposals
elyndra memory proposal-edit ID "..." --approve
elyndra memory approve ID --approve
elyndra memory reject ID --approve
elyndra memory corrections
elyndra memory edit ID "..." --approve
elyndra memory episode-edit ID "..." --approve
elyndra memory episode-forget ID --approve
elyndra chat archive CHAT_ID --prune --approve
elyndra chat archives
```

Inside a chat:

```text
/memory
/summary
/correct RESPUESTA_CORRECTA
```

## Next milestone

The next release starts a loopback-only, read-only HTML inspector for chats, structured summaries,
episodes, semantic-memory proposals, documents and audit events. It will use the same repositories
and SQLite data rather than introducing a separate memory implementation.
