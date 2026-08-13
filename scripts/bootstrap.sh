#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
ruff check .
pytest

cat <<'EOF'

Elyndra development environment is ready.

Activate it:
  source .venv/bin/activate

Create local runtime state:
  elyndra init --owner "${ELYNDRA_OWNER:-$(whoami)}" --system-user "$(whoami)"

Then verify:
  elyndra doctor
EOF
