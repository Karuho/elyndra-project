# Elyndra 0.7.15-alpha

## Controlled Dart and Flutter project toolchain

This release adds deterministic project inspection, bounded YAML descriptor validation, verify-only formatting, static analysis and approved tests for Dart and Flutter projects.

The toolchain never runs automatic Pub resolution, package upgrades, builds, `dart run`, code generators or SDK installers. Flutter analysis and tests use `--no-pub`; Dart formatting uses `--output=none --set-exit-if-changed`.

Dart/Flutter profiles, verification history, deterministic routing, CLI commands, local control-center APIs and an optional Alexandria knowledge package are included.

SQLite schema version: 23. Registered skills: 94.
