# Architecture

Elyndra keeps deterministic authority outside language models.

```text
owner input
    ↓
deterministic router
    ├── explicit local skill
    ├── direct memory + knowledge search
    └── optional language fallback
            ↓
      retrieved local context
            ↓
      replaceable text-only adapter
            ↓
      generated answer, no tools
```

## Trust boundaries

- `IdentityGuard` verifies the configured Linux owner.
- `PolicyEngine` requires approval for medium-risk actions and blocks high-risk actions.
- `allowed_roots` restrict file access.
- Skills expose narrow capabilities instead of a general shell.
- SQLite stores memory, document provenance, chunks and audit events outside Git.
- Language engines receive only the owner's prompt and selected text context.
- A model never receives `SkillContext`, secret stores, subprocess handles or unrestricted paths.

## Language-engine boundary

The optional language configuration lives outside the repository:

```text
~/.config/elyndra/language.toml
```

Version 0.3 initially supports `llama-cli` as a one-shot subprocess. Each generated request starts a
process, loads the selected GGUF, returns one answer and exits. This favors zero idle RAM over maximum
throughput. The adapter probes `llama-cli --help` and only sends optional flags supported by the local
runtime.

Profiles set conservative ceilings:

```text
eco     3 threads, 2K context, 160 output tokens
normal  4 threads, 4K context, 256 output tokens
work    6 threads, 8K context, 512 output tokens
```

The deterministic router always runs first. Commands such as project inspection, status checks and
memory operations do not load the model.

## Knowledge flow

```text
authorized text file
    ↓
path and extension validation
    ↓
size and binary checks
    ↓
SHA-256 + UTF-8 normalization
    ↓
local text chunks
    ↓
SQLite + optional FTS5
    ↓
selected context with document and fragment provenance
    ↓
optional language adapter
```

Importing knowledge is a medium-risk action because a user may accidentally ingest confidential
content. Search and read operations are read-only low-risk actions.

## Version 0.3 constraints

- No embeddings.
- No model downloads performed by Elyndra.
- No network client.
- No general shell.
- No automatic directory ingestion.
- No autonomous code modification.
- No model-driven tool execution.
- One-shot inference reloads the model for every generated response.


## Connectivity boundary

Language engines are adapters behind `LanguageEngine`. `ollama-local` accepts only an HTTP loopback
endpoint and rejects redirects. The current release has no remote engine. Future online providers
will use a separate explicit configuration and will not inherit permission from local engines.

## Model provenance

`language.toml` records the configured model license identifier, its runtime/teacher role and whether
teacher or redistribution use has been reviewed. These fields are conservative metadata, not legal
proof.

## Multilingual interaction boundary

Language selection is independent from the selected model. Elyndra first applies a small,
dependency-free detector for major writing systems and common Latin-language function words. In
`auto` mode, the detected language becomes the requested response language. In `fixed` mode, the
owner-selected language wins regardless of the input language.

The selection is stored in the private `language.toml` file:

```toml
[interaction]
mode = "auto"
preferred_language = "es"
```

Explicit switch commands are handled before model inference. This allows a message such as
`切换到西班牙语` to change Elyndra to Spanish without trusting a language model to modify
configuration. The model only receives an instruction describing the already-authorized response
language.

Text translation uses the same isolated language adapter and does not grant file, tool or secret
access. Speech recognition is intentionally deferred to a separate on-demand adapter with explicit
microphone permission.

## Persona and context pipeline

Every generative request is assembled from independently controlled layers:

```text
canonical persona
+ retrieved memories
+ retrieved document fragments
+ bounded session history
+ current owner request
```

The canonical persona is loaded from private `persona.toml` or conservative defaults. Retrieval uses dependency-free query variants before SQLite lookup. Interactive history exists only in the current process and is never treated as persistent memory.


## Local web boundary

Version 0.5 adds a standard-library HTTP server that binds only to `127.0.0.1`. The browser layer is
an adapter over the same application object used by the CLI:

```text
browser on this computer
        ↓ HTTP loopback only
ElyndraWebService
        ↓
ElyndraApplication
        ├── deterministic router
        ├── policy engine
        ├── chat and memory repositories
        └── optional language engine
```

The server does not implement a parallel memory system. Chat creation, retrieval, generation and
consolidation all use the existing SQLite repositories. A bounded in-process history cache keeps at
most six turns for a limited number of recently active web chats.

Browser writes require a random process-local token embedded only in the locally served page. The
handler validates the `Host` header, emits no CORS permission, disables caching and sends a strict
Content Security Policy. Assets are packaged with Elyndra and no remote script, stylesheet, font or
analytics request is permitted.

The first web release intentionally excludes file uploads, skill execution approval dialogs and
remote binding. Those capabilities require separate explicit review rather than inheriting trust
from the CLI.
