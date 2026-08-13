# Elyndra 0.8.1-alpha

## Personal organizer

This release introduces the first personal-assistant domain built on the
cognitive executive.

- visible version `0.8.1-alpha`;
- wheel version `0.8.1a0`;
- SQLite schema 42;
- skills remain 102;
- commitments, birthdays and routines are stored locally;
- daily, weekly, monthly and yearly recurrence is calculated on demand;
- routine check-ins are explicit and unique per occurrence date;
- reminder proposals require a separate review;
- approved reminders only surface in deterministic briefs;
- no notifications, background jobs or automatic completion are enabled;
- goals and tasks may be linked without automatic goal progress;
- daily briefs and upcoming birthdays can answer without Ollama;
- Node is not required because no JavaScript or TypeScript changed.

The release preserves the local-first memory architecture. Calendar-like data
stays durable in SQLite while only the requested date window enters the active
response context.
