# Elyndra 0.4.1 development release

This corrective release improves conversational play and persistent-session recall without adding
a larger language-model context.

## Implemented

- Ruff line-length correction in the guardrail module.
- Deterministic session recap for prompts such as `en que quedamos?`, using the chat summary stored
  in SQLite without loading the language model.
- Freestyle karaoke response that actively continues with an original verse instead of returning a
  sterile refusal.
- Stronger conversational guidance for games, mixed language, nuanced opinions and safe dark humor.
- Explicit instruction not to invent biographical facts or drag unrelated entities from older turns.

## Design boundary

Elyndra should support playful written karaoke, original songs, jokes, board-game assistance and
companionship. When a prompt points to an existing song, the default game mode creates a new line or
verse rather than reproducing a long existing lyric verbatim. Public-domain, owner-written and fully
original material can later use dedicated modes with explicit provenance.

## Next memory work

- Structured session summaries with topics, decisions and pending actions.
- Episodic records linked to chats and projects.
- Reviewable promotion of stable facts into semantic memory.
- Optional compressed cold transcripts.
- Local HTML inspector for chats, summaries, memories and audit events.
