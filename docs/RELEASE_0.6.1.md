# Elyndra 0.6.1-dev

## Scope

This release improves Alexandria retrieval quality before adding more subsystems.

## Included

- Compound-question detection and ordered task instructions.
- Dynamic generation budgets: 160, 288, 384 or 512 tokens depending on complexity.
- Domain-aware Alexandria retrieval.
- Reviewed-source priority with explicit unreviewed fallback.
- Strict Alexandria mode for prompts such as “Según Alejandría”.
- Deterministic visible source references `[A1]`, `[A2]`, and so on.
- Technical-review instructions separating confirmed findings, possible risks and pending verification.
- Retrieval relevance metadata and regression tests based on the PHP evaluation set.

## Safety and privacy

- No remote requests are introduced.
- Libraries remain on local disk.
- Only bounded excerpts are sent to the configured local language engine.
- Strict mode refuses to silently replace missing library support with general model knowledge.

## Known limits

- Source citations identify local books and units, not external web URLs.
- Response quality still depends on the configured local model.
- A 3B model may remain slower or less precise than larger optional models.
