# Supervised tutor evolution and durable knowledge

Elyndra 0.7.29-alpha adds a reviewed path from an active tutor lesson to durable knowledge owned by Elyndra. Tutors and auditors help produce and challenge candidate knowledge, but the deterministic core and the owner retain authority.

## Distinct layers

A **lesson** is compact guidance for one exact tutor/task pair. A **lesson evaluation** measures whether that guidance improves an incorporated deterministic case. **Durable knowledge** is an owner-approved, versioned unit that Elyndra can provide to any selected tutor for that task.

Knowledge is not a model weight, permission, preference or ethics rule. Promotion does not train Ollama or modify a tutor.

## Evaluation lifecycle

1. Create a plan for an active lesson. Planning invokes no model.
2. Review the frozen tutor, task, lesson, model fingerprint, active durable-knowledge IDs, optional auditor and incorporated cases.
3. Run the plan once with explicit approval. It executes sequentially and in the foreground.
4. Compare baseline and candidate outputs with deterministic evaluators.
5. Store scores, latency, evaluator metrics and output SHA-256 values, never raw prompts or outputs.
6. Receive a deterministic recommendation. No knowledge is promoted automatically.

A changed model fingerprint or active durable-knowledge set invalidates the approved plan before execution. Completed evaluations from another fingerprint remain historical but are excluded from current calibration.

## Auditor boundary

An optional local model can be configured with:

```toml
role = "auditor"
teacher_allowed = false
auditor_allowed = true
```

Auditors do not participate in normal responses. During an approved evaluation they may inspect bounded transient baseline and candidate outputs and return strict JSON with `support`, `review` or `reject`. The response is not authority: it cannot approve promotion and can only make the final recommendation more conservative.

## Durable knowledge and versions

Promotion requires a completed evaluation with no deterministic regression, a sufficient candidate score and a separate owner approval. The resulting row stores content, SHA-256, task, origin tutor, source lesson, source evaluation, model fingerprint, provenance, lineage and version.

There is no durable-knowledge deletion operation. To update a unit, promote a functionally superior validated evaluation with `--supersedes`. Elyndra creates the next version, links both rows and marks the older one `superseded`. The original content and provenance remain available for audit and historical comparison.

Only active versions enter bounded task context. At most six units and 2,400 rendered characters are applied; whole units are omitted rather than silently truncated.

## CLI workflow

```bash
./scripts/elyndra-dev model tutor-lesson-evaluation-plan \
  ID_LECCION \
  --auditor local-auditor \
  --approve

./scripts/elyndra-dev model tutor-lesson-evaluation-run \
  ID_EVALUACION \
  --approve

./scripts/elyndra-dev model tutor-lesson-evaluation-show ID_EVALUACION

./scripts/elyndra-dev model tutor-knowledge-promote \
  ID_EVALUACION \
  --title "Conocimiento validado" \
  --approve

./scripts/elyndra-dev model tutor-knowledge
./scripts/elyndra-dev model tutor-knowledge-context --task translation
./scripts/elyndra-dev model tutor-calibration-show \
  --tutor primary \
  --task translation
```

Every state-changing command requires its own explicit approval. Approvals are not reusable, and none of these operations runs in the background.
