import os
from pathlib import Path

from evolution.env_config import HERMES_AGENT_REPO as _HERMES_AGENT_REPO

# Paths
HERMES_AGENT_REPO = _HERMES_AGENT_REPO
WRAPPERS_DIR = Path(os.path.expanduser("~/.hermes/skills/.wrappers"))
ROTATION_STATE_FILE = WRAPPERS_DIR / ".rotation_state.json"
VENV_PYTHON = str(Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python3")

# Ensure wrapper directory exists
WRAPPERS_DIR.mkdir(parents=True, exist_ok=True)
