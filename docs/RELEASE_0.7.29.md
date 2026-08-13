# Elyndra 0.7.29-alpha

This release adds supervised lesson evaluation, advisory local auditors and versioned durable knowledge.

## Highlights

- inert lesson-evaluation plans and single-use foreground execution;
- baseline/candidate comparison using the existing deterministic benchmark evaluators;
- hash-only output persistence with scores, latency and structured metrics;
- optional local auditor role excluded from normal reply arbitration;
- auditor verdicts that can only make recommendations more conservative;
- model fingerprints, frozen active-knowledge IDs and stale-plan exclusion;
- stale-evaluation exclusion from current calibration;
- separate owner-approved promotion into Elyndra-owned task knowledge;
- bounded durable-knowledge context available independently of the origin tutor;
- immutable lineage and superior-version replacement without deleting previous knowledge;
- SQLite schema 37;
- 102 skills, unchanged;
- no JavaScript changes and no Node runtime requirement for this release.

## Non-goals

This release does not train or fine-tune models, generalize arbitrary conversations into knowledge, grant tools or permissions to tutors or auditors, delete durable knowledge, use remote providers, download models, auto-promote results or run evaluations in the background.

The next planned step is a reviewed general-knowledge acquisition layer that can represent factual, procedural and conceptual units beyond tutor-task lessons. Elyndra will retrieve its validated knowledge first, consult local Ollama tutors or auditors when that knowledge is insufficient, and convert only explicitly reviewed and validated results into Elyndra-owned versioned knowledge with preserved provenance.
