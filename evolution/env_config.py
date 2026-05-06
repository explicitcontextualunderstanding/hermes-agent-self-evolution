"""env_config.py — Environment-aware path resolution for hermes-agent-self-evolution.

Loads paths from .env file (gitignored) with sensible defaults derived from
the repo's location. This prevents hardcoding PII (/Users/kieranlal) in
source code.

The real home is derived from the repo root (which lives at the real path
even when running in the Hermes sandbox where Path.home() is wrong).

Usage:
    from evolution.env_config import COMPOSE_PKL, HERMES_HOME, EVIDENCE_LOG, ...

Dotenv file (repo root .env) — all paths are relative to repo root or use $HOME:
    COMPOSE_PKL_DIR=~/workspace/compose-pkl
    HERMES_AGENT_REPO=~/workspace/nano2
    HERMES_HOME=~/.hermes
    EVIDENCE_LOG=~/workspace/compose-pkl/docs/evolve-evidence.jsonl
    COST_TRACKER=~/.hermes/cost-tracker.jsonl
"""
import os
from pathlib import Path

# ── Repo root (this file lives at evolution/env_config.py → up 2 levels) ──
_REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Derive real home from repo location ─────────────────────────────────
# The repo lives at ~/workspace/hermes-agent-self-evolution.
# This gives the real home even when the Hermes sandbox hijacks Path.home().
# _REPO_ROOT = /Users/kieranlal/workspace/hermes-agent-self-evolution
# → _REAL_HOME = /Users/kieranlal
_REAL_HOME = _REPO_ROOT.parent.parent

# ── Load .env from repo root ──────────────────────────────────────────
_env_path = _REPO_ROOT / ".env"
if _env_path.exists():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            # Expand ~ to REAL home directory (not sandbox)
            if val.startswith("~"):
                val = str(_REAL_HOME / val[1:].lstrip("/"))
            os.environ.setdefault(key, val)


# ── Resolve each path ─────────────────────────────────────────────────

def _resolve(env_key: str, default: str) -> Path:
    raw = os.environ.get(env_key, default)
    # Expand ~ if present
    if raw.startswith("~"):
        raw = str(_REAL_HOME / raw[1:].lstrip("/"))
    return Path(raw)


# ── Public path constants (replace hardcoded /Users/kieranlal/...) ─────

COMPOSE_PKL = _resolve(
    "COMPOSE_PKL_DIR",
    str(_REAL_HOME / "workspace" / "compose-pkl"),
)

HERMES_AGENT_REPO = _resolve(
    "HERMES_AGENT_REPO",
    str(_REAL_HOME / "workspace" / "nano2"),
)

HERMES_HOME = _resolve(
    "HERMES_HOME",
    str(_REAL_HOME / ".hermes"),
)

EVIDENCE_LOG = _resolve(
    "EVIDENCE_LOG",
    str(COMPOSE_PKL / "docs" / "evolve-evidence.jsonl"),
)

COST_TRACKER = _resolve(
    "COST_TRACKER",
    str(HERMES_HOME / "cost-tracker.jsonl"),
)

PROMPT_SCRIPTS_DIR = _resolve(
    "PROMPT_SCRIPTS_DIR",
    str(COMPOSE_PKL / "scripts"),
)

PROMPT_DOCS_DIR = _resolve(
    "PROMPT_DOCS_DIR",
    str(COMPOSE_PKL / "docs"),
)
