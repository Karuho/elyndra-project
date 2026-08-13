# Elyndra 0.8.3-alpha

## Summary

This release adds policy-bounded local automation with a foreground-only dispatcher and a local result inbox. It is the first controlled step from passive organization toward delegated personal-assistant behavior.

## Security model

Automation has no generic execution authority. Policies bind one incorporated low-risk action and explicit limits. No network, skills, shell, file writes, installations, external notifications or background service are available.

## Interfaces

CLI and the Personal web workspace expose the same policies, schedules, runs and inbox. Every write requires explicit confirmation.

## Storage

SQLite schema 44 adds policies, schedules, occurrence runs and local inbox records. Existing schema-43 data is preserved.
