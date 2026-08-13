# Local translation fast paths

Elyndra 0.7.26 resolves known translations before loading a language model.

## Sources

1. Core multilingual dictionary entries.
2. Exact phrases and templates in `translation_core_v1.json`.
3. Explicitly installed Alejandría structured language and dialect packs.
4. The configured local language model only when the local layers have no answer.

The local layer does not claim universal coverage or full grammar. It returns provenance in structured results and marks whether a model was used.

## Web and CLI

The same `ElyndraApplication.ask()` fast path serves the web chat and CLI. Requests such as `como se dice perro en ingles`, `como puedo decir hola me llamo Carlos en chino` and pronunciation follow-ups avoid model latency when a local answer exists.

Chinese and Japanese templates can expose romanization. A pronunciation follow-up reads only the bounded recent conversation history; it does not grant tools or permissions.
