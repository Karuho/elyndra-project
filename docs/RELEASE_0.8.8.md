# Elyndra 0.8.8-alpha — Alejandría Spanish Lexical Core

Schema 49 introduces explicit root/vault roles, a shared versioned language-pack registry and isolated
reviewed overlays. Local builders support WordNet-LMF/MCR-shaped XML, Wiktionary JSONL, original
informal Spanish and optional CLDR annotations. Packs are independently licensed, FTS5-backed,
checksum-verified, atomically installed and queried read-only with bounded results.

Pack administration has CLI/web parity and is developer-only. Semantic integration preserves ethics,
deterministic routing, dialogue continuity and existing confidence thresholds before tutor fallback.

Phase 3 verified and built real MCR 3.0 Spanish and CLDR 48.2 packs, and rebuilt the original informal
pack. Exact artifacts and hashes are recorded in `releases/0.8.8-language-sources-lock.json`. The MCR
adapter consumes only `spa:lemma` and `spa:def`; the Spanish module does not contain Princeton synset
relations, and examples are omitted because the source does not identify a specific sense.

The direct extraction of the official dated 20260801 Spanish Wiktionary XML dump remains rejected with
its evidence preserved. Phase 3B uses Kaikki's pinned pre-extracted raw Wiktextract JSONL for the
Spanish Wiktionary edition, streams it from gzip, filters Spanish entries explicitly and builds a
separate disabled-by-default pack. This build-time acquisition adds no HTTP client or network path to
Elyndra runtime. Direct extraction of the dated XML dump with pinned
wiktextract/wikitextprocessor commits failed while expanding `Template:lengua` because the Lua module
`ustring:ustring` was unavailable. Elyndra rejected that partial output; only the separately sourced
Kaikki pack is accepted. Runtime remains offline.

The final Wiktionary pack contains 121,483 lemmas, 3,333,703 stored forms, 178,158 senses,
173,325 definitions, 6,588 examples and 128,589 distinct stored relations. It deterministically
reproduced content SHA-256 `82a4641a8726288c81b2afe712760152af10e31dd3578326cbb35b23693e23d8`
and database SHA-256 `654583e906af9c2755cab6e7e5e12375e22924411e53fe1202b00593663ecdd6`.
The generated pack remains outside Git and is disabled until explicitly enabled.
# Distribución desmontable

El código y los cuatro packs españoles se publican por separado. Elyndra no descarga
datos en runtime; instalación y activación son locales, explícitas y verificadas.

La presentación final agrupa sentidos y atribuciones sin ocultar los campos técnicos del
JSON. Las heurísticas locales se identifican como código determinista sin dataset externo.
