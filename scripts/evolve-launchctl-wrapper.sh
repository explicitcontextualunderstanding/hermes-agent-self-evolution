#!/bin/bash
# launchctl wrapper for evolve_prompts.py
# Required because launchctl doesn't set PYTHONPATH/cwd
cd /Users/kieranlal/workspace/hermes-agent-self-evolution || exit 1
export PYTHONPATH="/Users/kieranlal/workspace/hermes-agent-self-evolution:$PYTHONPATH"
exec /Users/kieranlal/workspace/hermes-agent-self-evolution/.venv/bin/python3 -u -m evolution.prompts.evolve_prompts "$@"
