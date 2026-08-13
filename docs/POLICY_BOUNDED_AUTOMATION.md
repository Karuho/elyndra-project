# Policy-bounded automation

Elyndra 0.8.3-alpha introduces supervised local automation without general agent authority.

## Autonomy levels

- `observe`: record a bounded local observation only.
- `suggest`: prepare a suggestion in the local inbox.
- `prepare`: prepare a deterministic local summary in the inbox.
- `execute_with_approval`: create a pending occurrence that requires a separate approval.
- `execute_under_policy`: execute the exact incorporated low-risk action under the standing policy when the foreground dispatcher is run.
- `forbidden`: cannot be activated.

## Incorporated actions

- `daily_brief.prepare`
- `organizer.upcoming.prepare`
- `wellbeing.weekly_summary.prepare`
- `coaching.review.prepare`
- `goal.review.prepare`
- `routine.missed_checkin.suggest`

These actions read only Elyndra's own local SQLite repositories and prepare bounded text/results. They do not invoke Ollama, skills, shell, files, network services or external notifications.

## Dispatch and idempotency

Schedules are stored as compact recurrence rules. `automation-scan --approve` evaluates due occurrences in the foreground, with a seven-day catch-up bound and a maximum of 200 active automations per scan. Each automation/occurrence pair is unique, so repeated scans cannot execute it twice. There is no daemon, cron job, systemd service or background thread in this release.

## Approval boundaries

Creating or changing a policy/automation requires explicit confirmation. `execute_with_approval` creates a pending run and requires `automation-run-approve`. `execute_under_policy` does not require per-run approval, but it remains limited to the exact action, time window, daily limit, scope and expiry already approved.

## Web parity

The Personal web workspace uses the same `AutomationRepository` as CLI. It supports policy and automation creation, foreground scanning, pending-run approval and inbox review. Missing `approved=true` is rejected by the HTTP service.

## Optional scheduler in 0.8.4-alpha

The dispatcher may now be invoked repeatedly by the reviewed local scheduler. This does not alter policy scope: every occurrence still passes the same action allowlist, timezone, window, daily limit, expiration and per-run approval checks. The scheduler is started explicitly, holds an exclusive private process lock and stops with its CLI or web host process. It never installs a system service or gains network, skills, shell or file authority.
