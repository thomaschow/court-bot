#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3.13}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "ERROR: $PYTHON not found. Install via: brew install python@3.13" >&2
  exit 1
fi

if [ ! -d .venv ]; then
  "$PYTHON" -m venv .venv
fi

.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e ".[dev]"
.venv/bin/playwright install chromium

if [ ! -f config/config.yaml ]; then
  cp config/config.example.yaml config/config.yaml
  echo "Created config/config.yaml from example."
fi

echo
echo "Setup complete. Next steps:"
echo "  .venv/bin/keyring set courtbot santa-clara:username"
echo "  .venv/bin/keyring set courtbot santa-clara:password"
echo "  .venv/bin/courtbot login    --facility santa-clara"
echo "  .venv/bin/courtbot discover --facility santa-clara"
