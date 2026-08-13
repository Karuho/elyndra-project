# Local first-aid library and reviewed topic packs

Elyndra includes a small deterministic emergency library in `src/elyndra/resources/first_aid_core_v1.json`. Its purpose is to provide useful actions during the first minutes instead of spending model latency on a generic response that only says to seek help.

The bundled cards cover life-threatening external bleeding, an unresponsive person who is not breathing normally, choking in a conscious adult or child, thermal burns and poison or corrosive exposure. The core resource is local, versioned, source-attributed and model-independent.

This is not a complete medical manual. Elyndra must not invent local emergency numbers, diagnoses, medication doses or procedures unsupported by reviewed content.

## Reviewed Alejandría packs

Elyndra 0.7.25-alpha adds the `first_aid.topic` structured adapter. A medical pack uses the same `elyndra-structured-package.json` manifest as language packs but must additionally declare:

- `content_type: first_aid` and `adapter: first_aid.topic`;
- a locale or regional scope;
- review status `reviewed`;
- review date and reviewer;
- license, package attribution and limitations;
- one or more source files with SHA-256 and source-level attribution.

Unreviewed first-aid packs are rejected during inspection. A package is also bounded to 50,000 cards and 500,000 normalized aliases. Each JSONL card may contain an ID, language, locale, title, summary, urgency (`emergency`, `urgent` or `routine`), aliases, immediate steps, actions to avoid, red flags, source references and review date.

## Commands

```bash
./scripts/elyndra-dev first-aid status
./scripts/elyndra-dev first-aid topics
./scripts/elyndra-dev first-aid lookup "sangrado grave" --language es
./scripts/elyndra-dev first-aid lookup "sangrado nasal" --language es --locale es-CL
```

Packages use the structured Alejandría workflow:

```bash
./scripts/elyndra-dev alexandria structured-inspect /ruta/paquete-medico
./scripts/elyndra-dev alexandria structured-install /ruta/paquete-medico --approve
./scripts/elyndra-dev alexandria structured-show ID_PAQUETE
./scripts/elyndra-dev alexandria structured-disable ID_PAQUETE --approve
```

Installed cards remain on disk and are selected by normalized aliases, language and locale. Enabled reviewed packs are checked before the bundled fallback when their locale matches. Rendered guidance exposes the source package, review date, source references, license, limitations and attribution.

## Safety boundary

Structured packs do not call Ollama, execute code or use the network. Installation, replacement, enable, disable and removal are explicit owner actions and audited. Disabling a pack immediately removes it from lookup without deleting the bundled emergency core.

A reviewed package is still informational guidance, not a diagnosis or substitute for emergency professionals or practical training. Contributors must use authoritative sources, preserve exact provenance, define regional assumptions and exclusions, and add regression fixtures for both detection and rendering. Elyndra must never silently download medical guidance or treat an unreviewed package as emergency authority.

## 0.7.26 direct web/CLI routing

The shared application fast path recognizes short emergency phrases such as `me ahogo`, `no puedo respirar` and `me atraganto` before model fallback. A new severe-trouble-breathing card provides immediate general actions and branches explicitly to choking or CPR when those signs are present. Capability questions list local cards without loading Ollama.

The breathing card is based on reviewed public guidance from the American Red Cross respiratory-distress and choking materials and NHS first-aid guidance. It remains a starter card, not a substitute for emergency dispatch or hands-on training.
