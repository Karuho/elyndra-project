# Elyndra 0.8.0-alpha

## Cognitive Executive and Unified Assistant Runtime

This release begins the 0.8 series by integrating existing deterministic routes,
reviewed knowledge, tutor arbitration, supervised skills and verification under
a common executive layer.

Release state:

- visible version `0.8.0-alpha`;
- wheel version `0.8.0a0`;
- SQLite schema 41;
- 102 registered skills;
- no JavaScript or TypeScript changes;
- no changes to `CHANGELOG.md`.

The executive stores no raw prompt and no chain of thought. It records structured
intent, route, risk, confidence, context identifiers and outcomes. Goals, tasks
and outcome verifications are durable but never progress automatically.

General knowledge retrieval now filters functional stopwords and applies a
minimum relevance threshold. This prevents weak lexical overlap from injecting
unrelated global knowledge into a model context.
