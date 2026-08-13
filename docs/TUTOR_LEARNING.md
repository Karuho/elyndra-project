# Reviewed tutor lessons and confidence calibration

Elyndra 0.7.28-alpha can preserve compact, owner-reviewed guidance for a local tutor without training the model or transferring authority.

## Review boundary

A lesson starts as a proposal. Creating or editing a proposal does not change tutor selection, model context, memory, preferences or Alejandría. Only an explicit owner approval creates an active lesson.

Allowed provenance categories are:

- `owner_feedback`;
- `reviewed_evidence`;
- `deterministic_evidence`.

Each proposal stores a compact lesson, task, tutor, observed score, review confidence, source SHA-256 and optional bounded source reference. Raw prompts and raw tutor outputs are not stored.

## Bounded use

An active lesson applies only to its exact tutor/task pair. Elyndra injects at most four current lessons as bounded context and explicitly states that they do not grant tools, permissions, memory access or authority.

Lessons can expire or be forgotten. Once inactive, they no longer affect context or calibration. A lesson is guidance, not durable knowledge. When a lesson is evaluated and separately promoted under the 0.7.29 workflow, its resulting durable knowledge is never deleted: a superior validated version supersedes it while preserving the full lineage.

## Confidence calibration

The raw task benchmark remains visible. Elyndra combines it conservatively with approved observations grouped by source type. This produces a task-specific confidence signal, not a claim of universal intelligence, factuality or safety.

An external tutor still requires an applicable completed benchmark before it can displace the primary model. Approved lessons alone never authorize an unbenchmarked tutor.

## Deterministic evidence comparison

Elyndra can record a comparison between a tutor-output SHA-256 and an evidence SHA-256. Exact-hash comparison computes match or mismatch deterministically. Owner review can record match, partial or mismatch. The comparison creates only a pending lesson proposal and never updates the tutor automatically.

## CLI workflow

```bash
./scripts/elyndra-dev model tutor-learning-status

./scripts/elyndra-dev model tutor-lesson-propose \
  --tutor primary \
  --task translation \
  --lesson "Preferir la traducción exacta de la evidencia local revisada." \
  --source deterministic_evidence \
  --source-sha256 SHA256 \
  --observed-score 1.0 \
  --confidence 1.0 \
  --approve

./scripts/elyndra-dev model tutor-lesson-proposals
./scripts/elyndra-dev model tutor-lesson-approve ID --approve
./scripts/elyndra-dev model tutor-lessons
```

Approval applies only to the named proposal. It is not reusable for another proposal, comparison or action.
