# Structured dictionary, morphology and dialect packs

The bundled `dictionary_core_v1.json` remains a small bootstrap lexicon. Elyndra 0.7.25-alpha adds an explicit structured-pack format so larger dictionaries, translations, morphology resources and dialect libraries can live on disk in Alejandría without being loaded completely into RAM.

## Supported adapters

- `dictionary.monolingual` for definitions, senses, examples and pronunciation;
- `dictionary.bilingual` for sense-aware translations;
- `language.morphology` for lemmas and inflected or conjugated forms;
- `language.dialect` for meanings and forms scoped to a locale or dialect.

Pronunciation data may be included in lexical records. A separate pronunciation adapter is intentionally deferred until its schema and licensing requirements are stable.

## Package layout

A package is a local directory containing:

```text
elyndra-structured-package.json
one-or-more-sources.jsonl
```

The manifest uses `schema_version: 2` and declares a stable package ID, semantic version, adapter, source and target languages, optional locale or dialect, license, publisher, review state, limitations, package attribution and one or more source files. Every source requires its own SHA-256 and attribution text.

Example manifest fragment:

```json
{
  "schema_version": 2,
  "package_id": "language.es-cl.example",
  "name": "Spanish — Chilean dialect example",
  "version": "1.0.0",
  "content_type": "language",
  "adapter": "language.dialect",
  "language": "es",
  "locale": "es-CL",
  "dialect": "es-CL",
  "license_id": "CC-BY-4.0",
  "publisher": "Example publisher",
  "review": {
    "status": "reviewed",
    "reviewed_on": "2026-07-31",
    "reviewer": "Example reviewer"
  },
  "limitations": ["Example coverage only."],
  "attribution": ["Example attribution."],
  "sources": [
    {
      "path": "entries.jsonl",
      "title": "Structured lexical entries",
      "format": "jsonl",
      "sha256": "<64 hexadecimal characters>",
      "source_url": "https://example.invalid/source",
      "attribution": "Required source attribution"
    }
  ]
}
```

Each JSONL line is one bounded UTF-8 object. Language entries may contain `id`, `language`, `lemma`, `pos`, `definition`, `forms`, `translations`, `morphology`, `dialects`, `pronunciation`, `examples` and `source_ref`. The adapter determines the required fields and Elyndra validates records before installation.

## Inspection and installation

```bash
./scripts/elyndra-dev alexandria structured-inspect /ruta/paquete
./scripts/elyndra-dev alexandria structured-install /ruta/paquete --approve
./scripts/elyndra-dev alexandria structured-install /ruta/paquete --replace --approve
./scripts/elyndra-dev alexandria structured-list
./scripts/elyndra-dev alexandria structured-show ID_PAQUETE
./scripts/elyndra-dev alexandria structured-disable ID_PAQUETE --approve
./scripts/elyndra-dev alexandria structured-enable ID_PAQUETE --approve
./scripts/elyndra-dev alexandria structured-remove ID_PAQUETE --approve
```

Inspection verifies the manifest, relative paths, regular files, symlink boundaries, UTF-8 JSONL, record schemas, duplicate IDs, source sizes and every SHA-256 without executing code or using the network. Installation and replacement require explicit owner approval. A different version cannot silently overwrite an installed package.

## Storage and lookup

Installed packages are copied into the private Alejandría data directory under a versioned checksum-derived path. SQLite stores package metadata, source provenance, lexical entries and normalized forms. Lookup remains disk-backed and uses only a bounded cache of recent results; Elyndra never loads a complete external dictionary into hot memory.

The existing `dictionary.lookup` skill and CLI can search enabled packs:

```bash
./scripts/elyndra-dev dictionary lookup pololear \
  --language es \
  --output-language en \
  --dialect es-CL
```

Returned matches expose package ID, review status, license, locale or dialect, examples, morphology, pronunciation, limitations and attribution when present. Definitions remain in their source language; Elyndra does not pretend that an untranslated definition was generated in another language.

## Limits and trust boundary

The initial limits are 512 KB per manifest, 128 MiB total source data, 64 sources, 500,000 records, 5,000,000 indexed lexical forms and 1 MiB per JSONL line. Packages cannot execute code, grant skills, modify permissions or enable network access. Elyndra does not download dictionary packages automatically, merge incompatible licenses, remove attribution or claim complete dialect coverage.

Large future libraries may be built from properly licensed sources such as Wiktionary-derived data or open lexical networks, but extraction, licensing, attribution and package creation happen outside the runtime. Installation remains local, inspected and owner-approved.

## 0.7.26 translation fast paths

The core lexicon, exact local phrasebook, templates and installed structured packs are queried before the model. `translation_core_v1.json` contains only compact reviewed phrases and templates; complete dictionaries, morphology, pronunciation and dialect coverage remain Alejandría package responsibilities. Unknown complex text can use the local model as a fallback, and responses disclose whether a model was used.
