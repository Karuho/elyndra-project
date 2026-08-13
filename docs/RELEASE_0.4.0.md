# Elyndra 0.4.0 development release

This release starts durable personal memory without keeping complete conversation history in RAM or
sending it to the language model on every request.

## Implemented

- Named and isolated chat containers with stable `chat_...` identifiers.
- Compact rolling session summaries stored in the private SQLite database.
- Summary-only persistence as the default mode.
- Optional full turn retention in SQLite through `--transcript full` or `/transcript full`.
- Chat list, show, search, reopen, rename and logical deletion commands.
- Retrieval of a persisted summary only when the current question is related to it.
- Selection of relevant active turns instead of sending every recent topic to the model.
- No personal-memory or document lookup for unrelated casual conversation.
- More natural conversational instructions, including humor and mixed-language adaptation.
- Deterministic truthful response for programming capability.
- Specific local guardrails for suspicious body-disposal wording and protected lyric continuation.
- SQLite schema version 3 with chat tables and optional FTS5 chat-summary search.

## Storage behavior

`summary` mode stores a bounded digest of recent turns and does not retain the complete turn text.
`full` mode stores complete turns in SQLite so a chat can restore a small recent working window.
Neither mode loads all historical chats into RAM. The model receives a persisted summary only when
lexical relevance or an explicit follow-up requires it.

## Not implemented yet

- Semantic model-generated consolidation of a session summary.
- Episodic extraction for decisions, errors and outcomes.
- Compressed or encrypted cold transcript files.
- Memory review, correction and promotion workflows.
- Export/import for chat and personal memory containers.

Those remain the next steps of the 0.4 line.
