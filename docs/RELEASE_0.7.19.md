# Elyndra 0.7.19-alpha

## Supervised validation and repair cycles

Elyndra 0.7.19-alpha connects the reviewed writing boundary from 0.7.18 with the supervised validation plans from 0.7.17. The connection remains explicit and owner-controlled rather than autonomous.

The workflow is:

```text
applied change proposal
→ frozen validation plan
→ separate single-use approval
→ real skill results
→ optional repair proposal
→ separate review and approval
```

A cycle can begin only from an existing proposal whose status is `applied`. Elyndra creates an allowlisted action plan tied to the same project root, stores that exact plan and executes it only after another explicit approval. The cycle records the action-run identifier and bounded per-step results.

## Repair proposals grounded in real failures

When validation finishes as `failed` or `partial`, the owner may request a repair proposal. Elyndra supplies the local language model with bounded validation evidence and the current content of only the files from the original applied proposal. The model receives no filesystem handle, skills, network, approval token or write capability.

The resulting repair is stored as a normal reviewable change proposal with frozen hashes and a unified diff. It is not applied automatically. A passed validation does not permit a repair proposal through this workflow.

## No autonomous loop

0.7.19-alpha deliberately does not implement:

- automatic repair after validation;
- recursive generate-test-fix loops;
- background execution;
- automatic revalidation after repair;
- package installation or dependency resolution;
- network access;
- arbitrary commands, shell access, commits or pushes;
- expansion beyond the original applied proposal's project and files.

After a repair is applied, the owner may explicitly start a new validation cycle from that repair proposal.

## Persistence and interfaces

The SQLite schema advances to version 27 with `assistant_validation_cycles`. Stored states include `validation_proposed`, `validating`, `validation_passed`, `validation_failed`, `validation_partial`, `repair_proposed`, `repair_applied` and `cancelled`.

CLI commands:

```text
assistant validate-plan CHANGE_ID --request TEXT
assistant validate-run CYCLE_ID --approve
assistant repair-plan CYCLE_ID --instruction TEXT
assistant cycle-show CYCLE_ID
assistant cycles
```

The loopback control center exposes recent cycles through `/api/control/validation-cycles`. The skill registry remains at 100 entries.
