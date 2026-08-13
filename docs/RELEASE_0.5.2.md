# Elyndra 0.5.2-dev

## Web polish

- Pinned chats live in a dedicated section above search.
- A maximum of five chats can be pinned at once.
- Printable chat views open the native browser print/PDF dialog automatically.
- Permanent deletion uses an Elyndra dialog instead of a browser-origin prompt.
- Chat URLs remain stable under `/chat/<chat_id>`.

## Local attachments

- Text, Markdown, source code, configuration files and common images can be attached.
- Files are stored under the private XDG data directory and linked to a chat and turn.
- Each message accepts at most five files, each no larger than 5 MiB.
- Hidden credential files such as `.env` and private-key filenames are rejected.
- Obvious password, token, API-key and private-key content is redacted before text reaches a model.
- Original files remain local and retain SHA-256 provenance.
- Text and source files are added to the prompt under a strict character budget.
- Images are previewed locally. Visual analysis only occurs when the configured Ollama model
  explicitly declares the `vision` capability; Elyndra otherwise reports that vision is unavailable.

## Storage

Attachments are stored under:

```text
~/.local/share/elyndra/attachments/<chat_id>/
```

Metadata is kept in SQLite schema version 6. Permanent chat deletion removes known local
attachments and cold archives before deleting the chat rows.

## Current limitations

- Attachments are associated with conversations, not yet promoted directly into Alexandria.
- PDF and office-document ingestion are not included yet.
- No OCR is performed.
- Image understanding depends on an optional local vision model.
- There is not yet drag-and-drop or a full attachment inspector.
