# Elyndra 0.7.21-alpha

## Conversational development-session continuity

Elyndra 0.7.21-alpha lets a local chat recover one focused non-closed development session and use its persisted timeline as bounded conversational context. The focus is created automatically when a chat creates a reviewed change proposal. An explicit phrase such as `estado de la sesión SESSION_ID` can focus an existing owner session in another chat.

Focus is not authorization. It cannot apply a proposal, run a plan, generate a repair, reuse an approval, widen a project root or continue in the background.

## Deterministic next-action guidance

The assistant can answer requests such as `¿Qué sigue?`, `¿Dónde quedamos?` or `Estado de la sesión SESSION_ID` without loading the language model. Elyndra derives the response from persisted events and returns exact supervised commands appropriate to the state:

```text
change proposed → review, apply or reject
change applied → create a validation plan
validation proposed → review or run the exact plan
validation failed or partial → review results or propose a repair
validation passed → review or close the session
```

These commands are suggestions only. The guidance path never executes a skill, writes a file or creates a validation or repair.

## Bounded model context

For ordinary conversational follow-ups, Elyndra may attach a compact session block to the configured local language engine. It contains the session ID, status, project, objective, latest real event summary and up to four available supervised commands. It explicitly states that the block is not authorization and that the model must not claim an action ran unless it appears as a persisted event.

The model still receives no tools, approval tokens, filesystem handle, network access or ability to select additional files.

## Proposal and session visibility

`assistant change-plan` now prints both identifiers immediately:

```text
Propuesta controlada CHANGE_ID
ID de sesión de desarrollo: SESSION_ID
```

The chat approval metadata, `assistant change-show`, recent change listings and the loopback control center expose the same linkage. `assistant session-next SESSION_ID` prints the deterministic guidance directly.

## Persistence and boundaries

The SQLite schema advances to version 29 with `assistant_chat_session_focus`. A chat has at most one focused session. Closing a session removes all focus rows pointing to it.

0.7.21-alpha never executes a suggested action, never reuses an approval, never runs in the background, never installs dependencies, never enables network access and never grants the model direct filesystem or skill access. The registry remains at 100 skills.
