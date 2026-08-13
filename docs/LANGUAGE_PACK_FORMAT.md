# Elyndra language-pack format

A pack directory contains `elyndra-language-pack.json`, `lexicon.sqlite`, `ATTRIBUTION.md` and
`LICENSES/`. Manifest schema 1 records logical pack ID, language/locale, version, builder version,
physical `database_sha256`, canonical `content_sha256`, counts, limitations and per-source provenance.

SQLite contains lexemes, forms, synsets, senses, membership, definitions, examples, relations, usage
labels, informal entries, emoji annotations and source records. Exact indexes precede FTS5. Installed
databases are immutable/read-only; several versions may be preserved, but only one version per logical
pack can be enabled.

Builds stream records inside one transaction, are not resumed after failure and use a private
same-filesystem temporary directory followed by atomic rename. `SOURCE_DATE_EPOCH` or `build_epoch`
controls timestamps. Reproducibility is defined by `content_sha256`, not SQLite byte identity across
SQLite versions.
# Bundle de distribución

El schema de pack sigue siendo 1. La distribución de varios packs usa un manifiesto de
bundle schema 1 descrito en `LANGUAGE_PACK_DISTRIBUTION.md`; las bases nunca se fusionan.
El manifest conserva una sola aparición estable de cada atribución y las prioridades
`400/300/250/200` para Informal/Wikcionario/MCR/CLDR.
