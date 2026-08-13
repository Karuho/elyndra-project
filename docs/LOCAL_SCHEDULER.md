# Optional local scheduler

Elyndra 0.8.4-alpha can keep policy-bounded automation active while the owner deliberately runs a local process.

## Runtime modes

- `assistant scheduler-run`: attached to the current terminal until `Ctrl+C`.
- Personal web scheduler: attached to the current loopback web process and stopped when that process closes.
- `assistant scheduler-cycle`: one locked cycle with no long-running process.

The scheduler never installs or edits cron, systemd, login startup or desktop autostart configuration.

## Locking and shutdown

The first scheduler acquires an exclusive non-blocking lock at `state/automation-scheduler.lock`. Metadata contains only PID, actor, mode, interval and start time. A second process fails closed. The lock is private, is released on clean stop and does not grant any new permission.

Scheduler sessions are durable SQLite rows with heartbeat, scan counts, created runs, created notifications, final status and a bounded error field. CLI interruption, explicit web stop and web-service close request orderly shutdown before releasing the lock.

## Local notifications

A completed automation inbox result may create one durable notification. Notification materialization is idempotent because every inbox item can have at most one notification. States are `pending`, `seen` and `dismissed`.

The CLI prints newly created notifications during a scheduler run. The Personal web workspace can show browser notifications only after browser permission is granted and only while the local page is open. No network service, remote push provider or operating-system command is used.

## Authority boundary

The scheduler only calls the existing policy-bounded dispatcher. Policies, action allowlists, time windows, daily limits, expiration and per-run approval remain authoritative. The scheduler cannot use tools, skills, shell, project files, installers, model downloads, external services or permission changes.
