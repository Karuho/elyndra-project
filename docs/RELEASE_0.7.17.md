# Elyndra 0.7.17-alpha

## Supervised assistant orchestration

Elyndra 0.7.17-alpha connects conversational requests with the controlled skills already present in the local core. A request with an explicit project path can produce a bounded action plan, require approval for that exact plan, execute the approved steps sequentially and explain the real results through the optional local language engine.

The model remains replaceable and receives no direct tools. It may propose strict JSON, but Elyndra validates the plan before approval and execution.

## Execution flow

```text
request
→ deterministic route or strict model proposal
→ Elyndra validation
→ frozen approval summary
→ one-time approval
→ sequential skill execution
→ persistent audit and action-run history
→ optional explanation from bounded real results
```

A plan contains at most four steps. Every step must name a registered allowlisted skill and provide only its accepted parameters. A model proposal cannot replace the path present in the original request, introduce a new skill or add unrestricted arguments.

## Approval and audit

The loopback web interface stores a deep copy of the proposed plan inside the existing approval grant. The token is bound to the exact chat, request text, attachments and plan. It expires, can be cancelled and is single-use. Approval executes the frozen plan without asking the model to plan again.

Elyndra records proposal, validation, start, synthesis and completion events. The CLI stores a planned preview first and executes that exact preview ID only once. Plans and executed runs are stored in `assistant_action_runs` and are visible through:

```bash
./scripts/elyndra-dev assistant status
./scripts/elyndra-dev assistant plan 'Revisa el proyecto Python /ruta y explícame los problemas'
./scripts/elyndra-dev assistant run ID_DE_VISTA_PREVIA --approve
./scripts/elyndra-dev assistant history
./scripts/elyndra-dev assistant report RUN_ID
```

The loopback-only control center exposes recent runs through `/api/control/action-runs`.

## Model boundary

Deterministic routing is preferred. When a deterministic route cannot create a complete plan and a local language engine is available, the model may return a JSON proposal only. It never receives a skill executor or authorization capability.

After execution, Elyndra may provide the model with a bounded, sanitized block explicitly marked as an authorized plan with real results. The model may explain those results but must not claim any unlisted action.

Without a language model, plans and skills still execute and Elyndra returns a deterministic summary.

## Explicit exclusions

This release does not provide:

- autonomous or recursive planning;
- more than four steps per plan;
- project-file writing or patch application;
- dependency or tool installation;
- network access;
- arbitrary commands or a general shell;
- background execution;
- migration execution or SQL writes;
- authorization through profiles or Alexandria knowledge.

The initial allowlist is limited to existing project inspection, static analysis, compilation/build verification and approved test skills. Existing direct CLI and deterministic routes remain compatible.

## Migration

The SQLite application schema advances to version 25 with the additive `assistant_action_runs` table and indexes for plan and chat history. Existing chats, memories, knowledge, profiles, verification history and audit records remain intact.

The release retains 100 registered skills. Orchestration is an application capability, not a new unrestricted skill.
