# Elyndra 0.7.28-alpha

This release adds owner-reviewed tutor lessons and task/source confidence calibration.

## Highlights

- compact tutor lesson proposals grounded in owner feedback, reviewed evidence or deterministic evidence;
- explicit edit, approve, reject, expire and forget lifecycle;
- approved lessons scoped to one exact tutor/task pair and bounded to four context items;
- conservative calibration that keeps raw benchmark score separate from reviewed observations;
- source-specific weighting for owner feedback, reviewed evidence and deterministic evidence;
- hash-only deterministic evidence comparisons that create pending proposals rather than changing behavior;
- traceable lesson IDs and calibration provenance in tutor selection history;
- loopback control-center and CLI visibility;
- SQLite schema 36;
- 102 skills, unchanged.

## Non-goals

This release does not train, fine-tune or rewrite models. It does not silently update memory or preferences, authorize unbenchmarked external tutors, execute tools, download models, use remote providers, create autonomous repair loops or run learning in the background.
