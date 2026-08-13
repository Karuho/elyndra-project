# Model policy

Every configured model has a declared role:

- `runtime`: may answer local requests but may not generate official training data.
- `teacher`: may produce candidate training samples after license review.
- `both`: approved for both purposes.

`teacher_allowed` defaults to `false`. It must only be enabled after reviewing the model license and
the intended output use. `redistribution_allowed` is a separate review flag and never causes Elyndra
to copy or publish model files.

Elyndra 0.3.1 supports only `connectivity = "local-only"`. The Ollama endpoint must be HTTP loopback,
may not contain credentials or paths, and redirects are rejected. A future online mode will use a
separate provider configuration and explicit user consent.
