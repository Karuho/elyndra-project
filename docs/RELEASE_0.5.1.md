# Elyndra 0.5.1-dev

This release polishes the first local web interface and adds durable chat management.

## Included

- Graceful handling of browser disconnects without terminal tracebacks.
- Stable per-chat URLs under `/chat/<chat_id>`.
- Explicit `Renombrar chat` action.
- Sidebar context menu with pin, archive, restore, printable export and permanent deletion.
- Pinned chats stored in SQLite and ordered before regular conversations.
- Archived-chat filter in the sidebar.
- Permanent deletion cascades through chat memory and removes known cold transcript files.
- Printable HTML transcript intended for the browser's **Print → Save as PDF** workflow.
- `Nuevo chat` as the default Spanish title, renamed from the first message.
- Removal of the center suggestion cards to keep history in the sidebar.

## Intentionally deferred

- File and image attachments.
- Native PDF generation without the browser print dialog.
- Markdown rendering and code blocks.
- Alexandria knowledge libraries.
- Desktop executable packaging.
- Compact-device distribution.
