# Elyndra 0.7.24-alpha

## Ethics review v3, local first-aid cards and hot, warm and cold memory

Elyndra 0.7.24-alpha hardens the constitutional filter using contextual risk signals rather than relying only on a short list of exact phrases. It adds deterministic handling for medical emergencies, child endangerment, grooming or exploitation, sexual violence, violence already in progress or confessed, service disruption, coded high-risk language, extremist praise and non-retaliatory hostility. Repeated letters and common punctuation or accent variations are normalized before classification. Explicit high-risk cases still run before the router and before the language model; ambiguous cases may use the bounded local tutor and fail closed when no safe interpretation is established.

The release adds `first_aid.lookup` and an offline first-aid starter library. Severe bleeding, an unresponsive person who is not breathing normally, adult or child choking, thermal burns and poison or corrosive exposure return immediate steps without waiting for Ollama or network access. These cards support the first minutes while emergency or poison professionals are contacted. They are versioned, source-attributed and deliberately described as a starter library rather than a complete clinical manual.

Memory is divided into hot, warm and cold retrieval. Hot memory is a bounded in-process query cache; warm memory searches recent SQLite episodes; cold memory searches approved durable memories and a provenance-preserving index of older episodes. Consolidation never deletes the source episode and never silently promotes an unreviewed preference into a trusted semantic memory. Retrieval metrics store hashes and latency rather than raw queries.

Elyndra now registers 102 skills and advances SQLite to schema 32. It remains offline by default, does not load the whole memory database into RAM, does not install dictionaries or medical data silently and does not grant the tutor policy or tool authority.

## Dictionary-pack direction

The 20-concept lexicon remains a bootstrap fast path. Complete language and dialect resources should be installed as explicit, licensed, versioned Alejandría packages with manifests, SHA-256 provenance and owner-controlled enable or removal. `docs/DICTIONARY_PACKS.md` defines the intended format and separates monolingual definitions, translations, morphology, pronunciation and dialect data. A structured pack adapter remains a later release rather than being implied by the starter lexicon. The same reviewed-package model will support expanded first-aid topics without loading entire libraries into memory.

## Safety boundaries

- Emergency guidance is deterministic and foreground-only; it does not place calls or infer the user's location.
- The first-aid package is not a substitute for practical training, dispatchers or medical professionals.
- Coded terms are interpreted with context; ordinary cooking language is not automatically treated as abusive content.
- The tutor can increase caution but cannot weaken deterministic child-safety, violence, emergency or cyber blocks.
- Cold consolidation preserves provenance, supports explicit logical forgetting and does not create learned preferences without review.
- No network access, background work, autonomous repair, hidden installation or model-controlled policy is introduced.
