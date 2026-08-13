# Cognitive executive

Elyndra 0.8.0-alpha adds a deterministic executive layer around the existing
assistant runtime. It does not replace ethics, memory, Alejandría, tutors,
skills or approval boundaries. It coordinates them.

For every assistant request the executive records only structured metadata:

- SHA-256 of the request, never the raw prompt;
- intent, domain, project and an operational goal summary;
- candidate routes and the planned route;
- risk, approval and verification requirements;
- multidimensional confidence;
- knowledge IDs applied or omitted;
- actual route, engine and outcome.

Private chain of thought is never requested or stored.

## Confidence

Confidence is not a single model claim. The executive keeps separate values for
model, source, retrieval, consistency and freshness confidence. The final
decision confidence is conservative and route-specific. A single reviewed
source does not become epistemic certainty merely because a model returns 1.0.

## Context budget

General knowledge retrieval removes common functional words and requires a
minimum relevance score. Domain and project scope add ranking weight, while
unrelated global knowledge is excluded. Revalidation and open conflicts still
prevent normal context use.

## Goals and tasks

Goals and tasks are explicit durable records. They do not run themselves.
Creation and mutation require an explicit CLI action with `--approve`. Tasks may
have dependencies, and completion requires bounded evidence.

## Verification

Outcome verification stores expected outcome, observed outcome, method, status
and bounded structured evidence. A process exit code alone is not sufficient to
prove that a user goal was achieved.

## Boundaries

The executive cannot grant permissions, bypass ethics, promote knowledge,
execute tools automatically, progress goals in the background or reuse an
approval. It remains local-only and foreground-only.
