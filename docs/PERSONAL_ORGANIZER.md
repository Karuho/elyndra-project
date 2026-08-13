# Personal organizer

Elyndra 0.8.1-alpha adds a local, deterministic personal organizer on top of the
cognitive executive introduced in 0.8.0-alpha.

## Scope

The organizer stores three bounded entity types in SQLite:

- commitments, including one-time or recurring appointments and obligations;
- birthdays, with an optional birth year;
- routines, with explicit per-date check-ins.

All recurrence is calculated on demand. Elyndra does not materialize an
unbounded future calendar and does not load complete histories into working
memory.

## Recurrence

Supported recurrence kinds are:

- `once`;
- `daily`;
- `weekly`, with explicit weekdays;
- `monthly`;
- `yearly`.

Intervals are bounded from 1 to 365. A recurrence can have an optional end date.
Monthly occurrences that target a day missing from a shorter month use that
month's final day. Leap-day yearly occurrences follow the same bounded rule.

## Reminder governance

A reminder is not created as an active notification. The lifecycle is:

1. create an organizer item with explicit approval;
2. create a reminder proposal with explicit approval;
3. approve or reject that proposal in a separate action;
4. show approved reminders in deterministic daily briefs.

Approval does not start a daemon, schedule a system task, contact a service or
send a notification. Background delivery remains disabled.

## Daily brief

A daily brief is generated from only the active items relevant to the requested
local date, domain and project. It contains:

- commitments occurring that day;
- birthdays occurring that day;
- active routine occurrences and their check-in state;
- overdue one-time commitments;
- approved reminders whose local reminder time falls that day.

The conversational fast paths `¿Qué tengo hoy?`, `agenda de mañana` and
`próximos cumpleaños` use this deterministic repository before Ollama.

## Links to goals and tasks

A commitment or routine may be linked to an existing cognitive-executive goal
or task. The link does not complete, reopen or advance the goal. Organizer
changes and goal progress remain separate explicit operations.

## Privacy and resource limits

- All data remains in the local Elyndra SQLite database.
- No network access is introduced.
- No prompt is stored by the organizer.
- Recurrences are expanded only for the requested bounded window.
- Daily briefs return at most bounded local result sets.
- Check-ins do not automatically become health, nutrition or psychological
  conclusions.
