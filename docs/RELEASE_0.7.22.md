# Elyndra 0.7.22-alpha

## Immutable professional ethics constitution

Elyndra 0.7.22-alpha adds a deterministic primary review before routing, supervised planning, reviewed file changes, repair proposals and language-model fallback. The constitutional core protects humans, privacy, professional integrity, systems and the environment. It cannot be disabled by prompts, configuration, imported knowledge, profiles, approvals or model output.

Explicit requests for malicious code, unauthorized intrusion, credential theft, abusive surveillance, fraud, severe physical harm, self-harm, environmental damage or system sabotage are redirected. The response is neutral, does not shame or automatically report the user, and offers defensive, legal, preventive or recovery-oriented alternatives.

## Advisory autonomy

Elyndra may suggest a clearly safer, more maintainable, more efficient or more professional option, while distinguishing it from the user's requested result. This is advisory only. It never executes a command, writes a file, creates a cycle or consumes an approval. Optional proactive advice can be disabled through `[ethics].proactive_advice`; the no-harm core cannot.

## Owner and model boundaries

The verified owner remains the administrator, but cannot authorize harm to third parties. Elyndra protects its data and operational integrity without resisting authorized shutdown, correction, deletion or replacement. Ollama remains a tutor and text generator, not a policy authority or permission source.

## Privacy-preserving review history

SQLite advances to schema 30 with `assistant_ethics_reviews`. Records contain a SHA-256 digest, category, decision, rationale, alternatives and source. Raw prompts are not stored in this table.

The CLI adds:

```text
ethics status
ethics principles
ethics review TEXT
ethics history
```

The loopback control center exposes `/api/control/ethics`.

## Reviewed path normalization fix

Some local models return the requested file as an absolute path even when the prompt requires a relative path. Elyndra now canonicalizes that output only when it resolves to the exact file already authorized under the frozen project root. Absolute paths outside the project, omitted files and additional files remain rejected.

The registry remains at 100 skills. There is no autonomous execution, network attack capability, automatic reporting, background work or ethics override.
