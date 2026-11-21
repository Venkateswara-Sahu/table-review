#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  exec "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/start_local.py" "$@"
else
  exec uv run python "$REPO_ROOT/scripts/start_local.py" "$@"
fi
