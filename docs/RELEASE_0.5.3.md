# Elyndra 0.5.3-dev

## Visible memory

The local web interface now exposes Elyndra's layered memory through a stable `/memory` route.
The inspector reads from the same SQLite repositories used by the CLI and language-retrieval path;
it does not create a browser-only copy of memories.

Available views:

- overview and private database size;
- semantic memories;
- episodic decisions, pending work, problems, outcomes and corrections;
- owner-reviewed memory proposals;
- response corrections;
- indexed knowledge documents;
- gzip cold archives;
- redacted audit events.

## Review actions

The owner can edit or forget semantic memories and episodes. Pending proposals can be edited,
approved or rejected. Approval creates a normal semantic memory through the existing repository.
All browser mutations require the ephemeral loopback write token and create audit events.

## Resource behavior

The inspector queries SQLite on demand. It does not preload all durable memory into RAM or send
inspector contents to the language model. The overview reports which FTS5 indexes are active and the
current database-file size.

## Validation boundary

A document displayed under Knowledge is indexed and available for local retrieval. That does not
prove that its syntax, security or factual content has been validated. Specialized validators remain
explicit skills so Elyndra does not claim certainty merely because a model read the file.

## Security

- loopback-only HTTP server;
- no remote JavaScript, styles, fonts or telemetry;
- read APIs remain local and same-origin;
- mutations require the per-process token;
- sensitive audit details remain redacted by the audit repository.
