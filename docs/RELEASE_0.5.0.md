# Elyndra 0.5.0-dev

This release introduces the first functional local browser interface while preserving the CLI as a
first-class surface.

## Included

- Standard-library HTTP server bound exclusively to `127.0.0.1`.
- Packaged HTML, CSS and JavaScript with no CDN or remote assets.
- Searchable chat-history sidebar.
- New, open and rename chat flows.
- Full-transcript or summary-only persistence selection for new chats.
- Functional message composer backed by `ElyndraApplication.ask()`.
- Visible processing state and elapsed response duration.
- Responsive layout for desktop and narrow screens.
- Ephemeral write token, local Host validation and strict browser security headers.
- Bounded six-turn in-process context for recently active web conversations.

## Security properties

- No LAN or wildcard bind.
- No CORS allowance.
- No telemetry.
- No remote scripts, styles, fonts or images.
- `Cache-Control: no-store` for local pages and API data.
- `frame-ancestors 'none'`, `X-Frame-Options: DENY` and `nosniff` headers.
- The language engine is released when the web process closes.

## Current limitations

- No memory, document or audit inspector yet.
- No browser approval flow for medium-risk skills.
- No streaming token transport; the UI shows progress until the complete response is ready.
- Summary-only chats display their durable summary after a page reload, not their full prior turns.
- No Markdown renderer; model output is displayed as safe plain text.

## Start

```bash
./scripts/elyndra-dev web
```

Optional:

```bash
./scripts/elyndra-dev web --port 8890 --no-open
```
