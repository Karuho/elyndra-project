# Canonical persona and contextual retrieval

Elyndra 0.3.3 separates three kinds of context:

1. **Canonical persona**: stable identity, mission, principles and boundaries.
2. **Retrieved local context**: owner memories and imported document excerpts selected for the current query.
3. **Ephemeral session history**: the last six interactions inside the current `elyndra chat` process.

## Canonical persona

The active persona is loaded from:

```text
~/.config/elyndra/persona.toml
```

Existing installations continue to work without this file. Elyndra uses conservative built-in defaults until the owner materializes an editable configuration:

```bash
elyndra persona init
elyndra persona status
```

The canonical persona is included in every language-model request. It is not a secret and it does not grant tools or filesystem access. It prevents the replaceable model from inventing that Elyndra is a company, fictional character or unrelated service.

## Retrieval variants

Before invoking a language model, Elyndra produces conservative lookup variants without external dependencies. Common question words are removed and meaningful terms are searched independently. This improves recall for natural questions while preserving SQLite FTS5 and its existing provenance records.

Retrieved items remain limited and deduplicated:

- up to five active memories;
- up to five document fragments;
- no automatic whole-document injection.

## Ephemeral conversation history

`elyndra chat` keeps at most six recent owner/assistant turns in process memory. The history:

- is not written to SQLite;
- is not reused by later CLI invocations;
- is cleared when the process exits;
- can be removed manually with `/clear`;
- is truncated before reaching the model.

This is deliberately different from long-term memory. Persistent learning must remain explicit and reviewable through Elyndra's memory system.
