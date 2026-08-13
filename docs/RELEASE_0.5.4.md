# Elyndra 0.5.4-dev

## Document trust layer

This release separates four states that were previously easy to confuse:

1. Stored locally.
2. Text extracted.
3. Syntax validated by a deterministic parser or linter.
4. Semantically analyzed by the language engine.

Reading a file never implies that its syntax or factual content is correct.

## Supported document adapters

- Plain text and source code.
- PDF through the optional local `pypdf` adapter.
- DOCX, ODT, PPTX and XLSX through bounded ZIP/XML readers.
- Images remain local and require a vision-capable model for visual analysis.

## Deterministic validation

- JSON: Python `json` parser.
- TOML: Python `tomllib` parser.
- XML: Python `ElementTree` parser.
- YAML: `PyYAML.safe_load_all` when the local dependency is installed.
- PHP: `php -l` when PHP CLI is installed.
- CSV: structural sampling only; reported as partial validation.

## Web experience

- Drag and drop into the chat composer.
- Extraction and validation badges on attachment cards.
- Safe lightweight Markdown rendering for assistant responses.
- Attachment inspector with local reprocessing.

## Resource policy

Processing occurs once when the file is attached or explicitly reprocessed. Extracted text and
validation metadata live on disk in SQLite. The full document is not retained in RAM and only a
bounded excerpt enters a model prompt.
