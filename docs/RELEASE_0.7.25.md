# Elyndra 0.7.25-alpha

## Structured language, dialect and reviewed first-aid packs

Elyndra 0.7.25-alpha turns the architecture documented in 0.7.24 into an installed, disk-backed Alejandría subsystem. It supports explicitly inspected packages for monolingual dictionaries, bilingual translations, morphology, dialect forms and reviewed first-aid topics.

### New capabilities

- Manifest schema 2 in `elyndra-structured-package.json`.
- JSONL adapters: `dictionary.monolingual`, `dictionary.bilingual`, `language.morphology`, `language.dialect` and `first_aid.topic`.
- Per-source SHA-256, URL and attribution preservation, with every indexed entry or card linked to its source row.
- Review metadata, limitations, locale and dialect metadata.
- Strict path containment, regular-file checks, symlink rejection, duplicate validation and bounded source sizes.
- Explicit inspection before installation and explicit approval for install, replacement, enable, disable and removal.
- Disk-backed SQLite indexes with bounded lookup caches rather than loading whole libraries into RAM.
- Integration with `dictionary.lookup`, `first_aid.lookup`, CLI lookup and loopback web lookup.
- Read-only control endpoint and package/source inspection.

### Medical content boundary

First-aid packages must be reviewed and include reviewer, review date, locale, source references, limitations and attribution. Unreviewed packages cannot become emergency authority. The bundled five-card library remains available as the safe local fallback.

### Non-capabilities

This release does not download packages, resolve licenses, scrape remote sources, execute package code, auto-update medical guidance, infer package trust from imported text or turn linguistic knowledge into execution permissions.

### State

- Version: `0.7.25-alpha`
- Python wheel: `0.7.25a0`
- SQLite schema: `33`
- Skills: `102`
- Automatic package download: disabled
- Network required: no
