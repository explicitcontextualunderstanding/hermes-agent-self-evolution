#!/bin/bash
# launchctl wrapper for evolve_prompts.py
# Required because launchctl doesn't set PYTHONPATH/cwd
SELF_EVOLVE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SELF_EVOLVE_DIR" || exit 1
export PYTHONPATH="$SELF_EVOLVE_DIR:$PYTHONPATH"
exec "$SELF_EVOLVE_DIR/.venv/bin/python3" -u -m evolution.prompts.evolve_prompts "$@"
