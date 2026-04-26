import os
from pathlib import Path

# Paths
HERMES_AGENT_REPO = Path(os.getenv("HERMES_AGENT_REPO", "/Users/kieranlal/workspace/nano2"))
WRAPPERS_DIR = Path(os.path.expanduser("~/.hermes/skills/.wrappers"))
ROTATION_STATE_FILE = WRAPPER_DIR / ".rotation_state.json"
VENV_PYTHON = "/Users/kieranlal/workspace/hermes-agent-self-evolution/.venv/bin/python3"

# Ensure wrapper directory exists
WRAPPERS_DIR.mkdir(parents=True, exist_ok=True)
