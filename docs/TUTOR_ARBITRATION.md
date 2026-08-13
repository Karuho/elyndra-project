# Supervised local tutor arbitration

Elyndra 0.7.27-alpha can choose among explicitly configured local language models without transferring authority, tools, memory access, filesystem access or approvals to them.

## Authority boundary

The deterministic Elyndra core remains authoritative for:

- constitutional ethics and emergency fast paths;
- permissions and single-use approvals;
- memory, provenance and reviewed preferences;
- Alejandría retrieval and evidence-first answers;
- skill execution and real validation results;
- project and file boundaries.

A tutor receives only the bounded prompt, bounded language context and recent conversation selected by Elyndra. It cannot invoke a skill, approve an action, inspect arbitrary files, install software, use remote endpoints or modify its own configuration.

## Selection policy

Tasks are classified deterministically as one of:

- `general_language`
- `translation`
- `summarization`
- `code_explanation`
- `supervised_planning`
- `code_change`
- `ethical_ambiguity`
- `creative_language`

Elyndra prefers the latest completed local benchmark for the task. If no applicable benchmark exists, it keeps the configured primary model. An explicitly configured external tutor can be selected only when its `tutors.toml` entry uses a local backend, declares a reviewed teacher role and lists the task.

Only one tutor is invoked for a normal response. There is no hidden parallel generation or background comparison. If a selected external tutor fails, Elyndra may fall back to the primary model and records that fallback.

## Local configuration

Optional tutors are declared in:

```text
~/.config/elyndra/tutors.toml
```

Use:

```bash
./scripts/elyndra-dev model tutor-template
```

The command only prints a template. It does not write or modify configuration.

Supported local backends in this release:

- Ollama over HTTP loopback only;
- an explicit local `llama-cli` binary and local GGUF file.

Remote URLs, embedded credentials and unreviewed teacher entries are rejected. A malformed optional file is reported by `model tutor-status` without preventing the deterministic core from starting.

## Benchmarks

The incorporated benchmark suite uses only fixed, non-personal prompts. It measures bounded protocol compliance and latency for translation, summarization, simple code explanation and strict JSON output.

Run it explicitly:

```bash
./scripts/elyndra-dev model tutor-benchmark --approve
```

The benchmark:

- runs sequentially and in the foreground;
- does not expose skills, files, memory or approvals;
- does not download models;
- stores output SHA-256, score, latency and structured metrics;
- does not store raw benchmark prompts or raw generated text.

The score is not a claim of general intelligence, factual correctness or safety certification. It is only a reproducible local signal for the included cases.

## Traceability

Every arbitrated model invocation records:

- task class;
- selected tutor and engine;
- selection reason;
- benchmark run and score when applicable;
- SHA-256 of the prompt, not the prompt text;
- context item count;
- latency and result status;
- whether fallback was used.

The response metadata exposes the same selection provenance with explicit `authority=false`, `tools_allowed=false` and `permissions_transferred=false` markers.
