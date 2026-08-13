# Isolated multi-account web shell

Elyndra 0.8.7-alpha supports several local identities without sharing personal data.

## Registry and vaults

The installation database authenticates accounts and maps each account public ID to a private SQLite vault. Chats, memory, documents, goals, organizer entries, wellbeing, learned preferences and automation state live in that vault. Account-specific data, state and cache directories use the same public ID.

The first account created after upgrading a legacy installation receives a SQLite backup of the legacy personal database. Every later account begins from a fresh migrated schema.

## Sessions

`/login` accepts username or email. `/register` remains available for another isolated account. Browser sessions use HttpOnly, SameSite=Strict cookies and are persistent until logout or expiry. Selecting another account revokes the previous web session before loading the new vault.

## Interface rules

Only conversation history scrolls. Navigation and the account menu remain available. User mode cannot see or call developer-only surfaces. `Nuevo chat` is a transient draft until content is sent. Local mode is available; Online is visibly reserved for a later controlled gateway.
