# Elyndra 0.7.6-dev

Elyndra 0.7.6 adds the first complete controlled Python project toolchain and ships the first optional
Alexandria knowledge package maintained inside the public repository.

## Python verification

The Python pipeline supports deterministic project inspection, `pyproject.toml` validation, syntax
compilation, Ruff, mypy and Pytest. Project-local tools under `.venv/bin` or `venv/bin` take priority
over global tools. Elyndra does not invoke pip, build backends, tox, nox or arbitrary project scripts.

Syntax compilation uses Python's built-in compiler without importing project modules or writing
bytecode. Ruff and mypy run with fixed argument sets and bounded output. Pytest requires explicit
approval because project tests execute code.

## Profiles and history

Python project profiles can enable or disable stages, select validated configuration files inside the
authorized project, set limits and require optional tools. Profiles never grant project access.
Verification results use the shared history model and can be inspected or compared from CLI and the
local control center.

## Optional Python knowledge

The repository now includes `knowledge-packs/python-modern-basic`, a checksum-verified optional
Alexandria package covering project structure, configuration, syntax, Ruff, mypy, Pytest and safe
verification workflows. Installation remains local, approved and separate from execution permission.

## Documentation and migration

README, CHANGELOG, SECURITY, CONTRIBUTING and the Alexandria package guide document the Python
toolchain, its trust boundaries and the new shipped knowledge package. SQLite schema version 14 adds
Python project profiles without deleting existing memories, projects, knowledge or audit records.
