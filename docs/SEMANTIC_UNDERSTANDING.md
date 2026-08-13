# Semantic understanding

## Lexical contribution in 0.8.8-alpha

Ethics, deterministic routes, chat continuity and approved account examples remain ahead of lexical
expansion. Informal normalization is bounded to 12 input tokens, 24 expanded terms and depth one.
Existing confidence thresholds remain authoritative; an ambiguous lexical match cannot promote a weak
intent and antonyms are never synonyms. The tutor remains a final classifier without pack, vault,
SQLite, tools, permission or write access.

Elyndra 0.8.5-alpha introduces a bounded interpretation layer between constitutional safety and generic model fallback. Its purpose is to let the owner speak naturally without turning every phrase into a hard-coded command or giving a language model direct authority over personal data and actions.

## Resolution order

1. Constitutional ethics, crisis and first-aid checks.
2. Existing exact deterministic routes.
3. Local semantic resolution from canonical intents, concepts, reviewed examples and bounded time/metric entities.
4. Tutor-assisted interpretation when local confidence is insufficient.
5. One concrete clarification when ambiguity remains.
6. Retrieval from the relevant local repository and a grounded deterministic response.
7. Generic language-model fallback only when no supported local intent applies.

## Tutor boundary

The tutor sees the message, the allowed intent names and local candidate scores. It receives no SQLite rows, memories, tools, filesystem, network, permissions, approvals or execution authority. It returns strict JSON containing an intent, confidence, bounded entities, alternatives and an optional clarification. Elyndra validates the JSON and performs all data access itself.

## Reviewed language learning

A phrase can be proposed for a canonical intent by an explicit owner correction or after repeated tutor-assisted resolutions. Every proposal remains pending until reviewed. Approval creates a durable active example; rejection preserves the decision. Ordinary conversation never activates language learning silently.

## Privacy and memory budget

Resolution history stores a SHA-256 of the normalized message and structured metadata, not raw messages. Explicit pending proposals may store a normalized phrase for owner review. Retrieval remains repository-specific and bounded; the semantic layer does not load full histories or databases into RAM.
