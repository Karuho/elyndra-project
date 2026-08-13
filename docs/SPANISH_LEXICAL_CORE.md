# Spanish lexical core

Elyndra resolves covered Spanish vocabulary from separately installed SQLite packs before asking a
local tutor. Lookup order is: approved account overlay, curated informal entry, exact lemma, exact form,
then bounded prefix/FTS. Synonyms come from membership in the selected synset; antonyms are explicit
sense relations. Ambiguous terms remain grouped by sense or require clarification.

Packs are shared installation resources. Corrections are private account proposals and become active
only after review. Neither path grants tools, permissions or model authority.

The implementation supports local WordNet-LMF, the official OMW Spanish MCR tab format, structured
Wiktionary JSONL, informal JSONL and CLDR annotations. Phase 3 built real, separate MCR and CLDR packs
outside Git and rebuilt the informal pack. Emoji annotations are queryable but remain explicitly
non-diagnostic.

The direct 20260801 XML extraction remains rejected: the pinned extractor failed on Spanish template
Lua. Phase 3B uses the independently locked Kaikki raw Wiktextract JSONL from the Spanish Wiktionary
edition. The adapter reads gzip and JSONL incrementally, requires both `lang_code=es` and
`lang=Español`, keeps local sense indices explicitly local, maps declared inflections to their lemma,
and stores named relation targets against the source sense without inventing a target-sense identity.
Examples with an external `ref` are omitted because their third-party provenance is ambiguous.
Entries marked only as top-level `form-of` are not promoted to lemmas when no explicit target is
present; they resolve only when a base lemma independently declares that surface form. This prevents
`caminando` and `dormí` from appearing as independent lemmas while preserving their traced resolution.
No fallback parser or partial XML-extractor pack is used. See the source lock and release note for
exact commits and the failure signature.
# Experiencia léxica Phase 4

Las formas flexionadas exponen expresión consultada, lema canónico y rasgos; los sentidos
se deduplican por identidad semántica. Emoji combina CLDR con ambigüedad curada, y las
risas/keyboard-smash usan heurísticas locales, acotadas y no diagnósticas.

CLI, web y chat emplean un solo renderer. La salida española traduce POS, agrupa por lema
y POS, muestra una forma flexionada una sola vez y omite equivalencias vacías. Las variantes
ortográficas se conservan en JSON pero no cuentan como un sentido humano adicional.
