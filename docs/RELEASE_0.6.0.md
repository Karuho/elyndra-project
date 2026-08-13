# Elyndra 0.6.0-dev

## Alexandria foundations

Alejandría is Elyndra's local library layer. It stores sources on disk, divides extracted text into small searchable units and retrieves only the units relevant to a request. Importing a source does not train the language model and does not automatically make the content canonical.

Each library records a name, domain, language, version, license identifier and enabled state. Each source retains its SHA-256 digest, local path, processor, validation state and provenance metadata. Sources begin as unreviewed and can be marked reviewed by the owner.

The web interface is available at `/alexandria`. The CLI uses `elyndra alexandria`.

## Resource model

- Libraries and sources remain on disk.
- FTS5 performs local lookup when available.
- At most two Alexandria units enter a normal language-model context.
- Disabled libraries do not participate in retrieval.
- No network access or automatic model training is introduced.

## Document interaction fixes

Syntax-validation requests now use deterministic attachment metadata or local parsers and return a concise result. Long code-shaped owner messages are rendered as code blocks in the web chat instead of flattened prose.
