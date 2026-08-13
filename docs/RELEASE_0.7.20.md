# Elyndra 0.7.20-alpha

## Supervised development sessions

Elyndra 0.7.20-alpha adds a persistent session layer over the reviewed-change and validation-repair primitives from 0.7.18 and 0.7.19. Each new explicit change proposal starts one development session. The session records the proposal, approved application, validation plan, real validation result and any repair proposal as an ordered local timeline.

The session is metadata and history, not an agent. It cannot execute skills, apply a proposal, approve a token, generate a repair, install dependencies, use network access or continue in the background.

## Automatic linkage without inherited authority

When an initial change proposal is created, Elyndra creates a session linked to that proposal. Later events are attached to the same session:

```text
change proposed
→ change applied or rejected
→ validation proposed
→ validation passed, failed or partial
→ optional repair proposed
→ repair applied or rejected
```

Every arrow still represents an explicit operation with its own existing validation and approval boundary. A session identifier never substitutes for a proposal identifier, cycle identifier or approval token.

## Interfaces

CLI commands:

```text
assistant session-start EXISTING_CHANGE_ID
assistant sessions
assistant session-show SESSION_ID
assistant session-close SESSION_ID --approve
```

The loopback control center exposes recent sessions through `/api/control/development-sessions`. The detailed session view includes its objective, project, current proposal and cycle, status and ordered events.

## Persistence and boundaries

The SQLite schema advances to version 28 with `assistant_development_sessions` and `assistant_development_session_events`. Session states are `active`, `completed`, `needs_attention` and `closed`.

0.7.20-alpha does not add automatic execution, automatic validation, automatic repair, recursive loops, background tasks, network access, installers, arbitrary commands, commits, pushes or additional filesystem scope. The skill registry remains at 100 entries.
