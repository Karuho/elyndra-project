# Language-source licensing

Elyndra code uses `PolyForm-Noncommercial-1.0.0`. Language data keeps its own license. Every source
requires its actual artifact checksum, version/date, URL, license text, attribution and transformation
notes.

- Spanish WordNet/MCR requires verification of the actual MCR 3.0 artifact and CC BY 3.0 notice.
- Wiktionary-derived packs remain separate and retain CC BY-SA, attribution/history and any applicable
  GFDL obligations of the actual dump.
- CLDR is optional and requires its pinned release plus Unicode license notice.
- The bundled informal file is original; tests use explicitly fictitious synthetic notices.
- RAE, Oxford and other proprietary content remain prohibited without compatible explicit licensing.

Phase 3 source identities, actual artifact hashes, notices, transformations and limitations are locked
in `releases/0.8.8-language-sources-lock.json`. MCR Spanish was verified as CC BY 3.0 and CLDR 48.2 as
Unicode-3.0 from notices contained in the downloaded artifacts. The dated 20260801 Wiktionary XML dump
remains rejected because pinned extraction failed in `Template:lengua`; its hashes and failure evidence
remain in the lock. Phase 3B instead uses Kaikki's separately pinned raw Wiktextract JSONL, derived from
the Spanish Wiktionary 20260703 dump on 2026-08-01. Its chain is Wikimedia contributors → Wiktextract →
Kaikki → Elyndra, under CC BY-SA and GFDL terms with attribution to every transformation participant. A
partial extractor output is not a distributable or installable artifact.
# Separación de distribución

Los bundles conservan por pack IDs de licencia, atribuciones, checksums originales y
textos de licencia. Su licencia de datos permanece separada de la licencia de Elyndra.
