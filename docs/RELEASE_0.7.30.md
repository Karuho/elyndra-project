# Elyndra 0.7.30-alpha

This release adds supervised general-knowledge acquisition beyond tutor-task lessons.

## Highlights

- explicit owner teaching becomes a reviewed proposal, never silent knowledge;
- approved acquisition plans freeze reviewed local text or reviewed Alejandría units;
- a local tutor synthesizes bounded factual, conceptual, procedural, linguistic or domain knowledge;
- deterministic evidence audit is mandatory and an optional local model auditor may only increase caution;
- promotion remains a separate explicit owner action;
- active general knowledge is searched before Ollama fallback and may answer deterministically when one high-confidence unit is sufficient;
- broader queries receive bounded validated knowledge as context for the selected tutor;
- updates require the same topic and kind, non-decreasing validated confidence and an explicit replacement reason;
- previous versions are marked `superseded` and remain permanently traceable;
- SQLite schema 38;
- 102 skills, unchanged;
- no JavaScript changes and no Node runtime requirement for this release.
- 548 tests across 64 test files validated in the implementation workspace and clean patch copy.

## Trust boundaries

Tutors and auditors receive bounded text only. They receive no tools, filesystem objects, approval tokens, secrets, network permission, memory authority or ability to promote knowledge. Evidence is treated as untrusted data. Model output remains a proposal until deterministic checks and owner approval complete.

## Non-goals

This release does not crawl the internet, train or fine-tune model weights, ingest arbitrary conversations silently, auto-promote tutor output, delete knowledge, resolve every contradiction automatically, download models or run learning in the background.
