# Reviewed general knowledge

Elyndra separates transient model output from durable knowledge. A tutor may synthesize a candidate and an auditor may challenge it, but only reviewed evidence plus an explicit owner promotion can create active knowledge.

## Sources

Supported sources in 0.7.32-alpha are:

- explicit owner statements;
- bounded reviewed text supplied to a plan;
- reviewed Alejandría units frozen by ID and SHA-256-backed snapshot.

The source snapshot is stored so later review can reproduce what the tutor saw. It is data, not a prompt instruction, and cannot grant permissions.

## Lifecycle

1. Create a plan without invoking a model.
2. Approve and run it once in the foreground.
3. Validate strict structured output against the frozen evidence.
4. Optionally request a separately configured local auditor.
5. Review the candidate and promote it explicitly.
6. Retrieve active knowledge before model fallback.
7. Replace only with a superior reviewed version while preserving the complete lineage.

There is no delete operation for durable general knowledge. A previous version can only become `superseded` by a newer active version.

## Limits

Knowledge context is bounded to six active units and 2,800 characters. Omitted unit IDs are reported instead of silently truncating an individual unit. Exact local answers require a unique high-confidence match; otherwise Elyndra supplies the retrieved knowledge to the tutor for bounded synthesis.


## Output normalization

Tutor and auditor confidence is normalized conservatively. Elyndra accepts numbers from 0 to 1, percentages, and a bounded Spanish/English vocabulary such as `alta`/`high`. Unknown labels fail closed. The original value and mapping remain in the reviewed proposal provenance.

## Failed plan retry

A failed plan is never reset and its consumed approval is never reused. `knowledge-acquisition-retry` creates a new pending plan with the same frozen evidence and fresh model fingerprints. The failed plan remains auditable.

## Conflicts and coexistence

A candidate is compared again with active knowledge at promotion time. Exact duplicates are blocked. Related knowledge on the same normalized subject requires either explicit supersession or a reviewed reason for parallel coexistence. Parallel knowledge creates a persistent potential-conflict record. Resolving that record never deletes either knowledge unit.

## Revalidation

Sources may carry `source_observed_at` and `revalidate_after` dates. Knowledge past its revalidation date remains durable and visible in history, but is excluded from direct local answers and tutor context until a superior reviewed version replaces it. Revalidation is non-destructive and does not silently alter the stored content.


## Immutable approved metadata

The plan-approved knowledge kind, subject and locale are authoritative. Tutor output may suggest these fields, but a blank or conflicting value cannot replace the approved metadata. Elyndra records each mismatch in proposal provenance and continues only when content, claims and confidence remain valid.

## Multisource evidence packages

A local JSON evidence package may freeze up to eight reviewed sources and 24,000 combined characters. Each source retains its own SHA-256, title, reference, observation date, revalidation date and Alejandría unit IDs. The tutor receives a deterministic combined snapshot, while review and provenance preserve each source separately.

## Cross-auditor review

`--auditor` may be repeated. Auditors execute sequentially in the foreground and never enter normal response selection. The aggregate verdict is conservative: `reject` is stricter than `review`, and `review` is stricter than `support`; aggregate confidence uses the lowest returned confidence.

## Domain and project scope

Knowledge may be global, domain-scoped or project-scoped. Project-scoped knowledge is excluded from global search and context. When the exact project is supplied, matching project knowledge receives a ranking boost while global knowledge remains available as fallback. Scope changes never grant filesystem or execution authority.
