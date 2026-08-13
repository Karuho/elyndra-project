# Elyndra 0.8.5-alpha

This release completes Semantic Understanding and Tutor-Assisted Intent Resolution. Natural personal-language variants can resolve to canonical intents and existing local data without generic Ollama disclaimers. Uncertain supported language can be interpreted by a local tutor under strict JSON and zero-tool boundaries; ambiguity produces a concrete clarification.

SQLite schema 46 adds reviewed intent examples, structured resolution history, language-learning proposals and semantic fallback events. Raw messages are hashed in resolution history. Repeated tutor resolution can prepare a pending proposal but cannot activate it. CLI and web expose the same runtime, diagnostics and review operations. Skills remain at 102.

Development pauses after this objective so the owner can review the language architecture before another release is designed.

Validation covered 609 tests in 72 files, real HTTP parity, JavaScript syntax, a real schema-45 to schema-46 migration and preservation of existing local memory.
