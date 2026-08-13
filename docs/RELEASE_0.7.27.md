# Elyndra 0.7.27-alpha

This release adds supervised local tutor arbitration and reproducible foreground benchmarks.

## Highlights

- deterministic task classification for language-model fallback;
- optional local tutors configured in `~/.config/elyndra/tutors.toml`;
- loopback-only Ollama and explicit local llama-cli/GGUF adapters;
- benchmark-based recommendation with primary-model fallback;
- task-bound tutor routing for language fallback, translation, ethical ambiguity, supervised planning and reviewed change proposals;
- prompt/output privacy in benchmark and selection history;
- CLI and loopback control-center visibility;
- SQLite schema 35;
- 102 skills, unchanged.

## Non-goals

This release does not add autonomous model downloads, remote providers, parallel hidden generation, background benchmarks, model-controlled permissions, self-modification, automatic skill execution or automatic acceptance of model output.
