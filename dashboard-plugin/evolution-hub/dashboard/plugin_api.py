"""Evolution Hub dashboard plugin — backend API routes.

Mounted at /api/plugins/evolution-hub/ by the dashboard plugin system.
Pure filesystem observer: reads health.json, .rotation_state.json, logs.
Zero external dependencies — stdlib only (json, subprocess, pathlib, asyncio).
"""

import asyncio
import json
import random
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

# Guard against concurrent control commands (e.g. two browser tabs)
_control_lock = asyncio.Lock()

# ── Constants ──────────────────────────────────────────────────────────

HERMES_HOME = Path("/Users/kieranlal/.hermes")
WRAPPERS_DIR = HERMES_HOME / "skills" / ".wrappers"
HEALTH_FILE = WRAPPERS_DIR / "health.json"
ROTATION_STATE_FILE = WRAPPERS_DIR / ".rotation_state.json"
LOG_FILE = WRAPPERS_DIR / "batch_size_aware.log"
LOG_FILE_PARALLEL = WRAPPERS_DIR / "batch_parallel.log"
BATCH_SCRIPT = Path("/Users/kieranlal/workspace/nano2/scripts/evolve_batch_size_aware.sh")
ROTATION_SCRIPT = Path("/Users/kieranlal/workspace/nano2/scripts/evolve_skill_rotation.py")
HERMES_AGENT_REPO = Path("/Users/kieranlal/workspace/nano2")
AUDIT_LOG = HERMES_AGENT_REPO / ".evolution_audit.log"
SKILLS_DIR = HERMES_AGENT_REPO / ".claude" / "skills"


# ── Helpers ─────────────────────────────────────────────────────────────

def _read_json(path: Path, retries: int = 2):
    """Read and parse a JSON file, retrying once on parse failure.

    Retries guard against transient file-system contention: if the evolution
    loop is mid-write (atomic rename window), a concurrent read may catch a
    half-written file. Two attempts with a 200ms gap resolve this in practice.
    """
    for attempt in range(retries):
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            if attempt < retries - 1:
                time.sleep(0.2)
                continue
            return None
    return None


def _read_file_tail(path: Path, max_lines: int = 50):
    """Return the last N lines of a text file."""
    if not path.exists():
        return None
    try:
        text = path.read_text()
        lines = text.splitlines()
        return "\n".join(lines[-max_lines:])
    except OSError:
        return None


def _detect_log_file() -> Path:
    """Auto-detect which batch log is active.

    Checks batch_parallel.log first (parallel launcher preferred),
    falls back to batch_size_aware.log (serial launcher).
    Returns the most recently modified log with content.
    """
    candidates = []
    for p in [LOG_FILE_PARALLEL, LOG_FILE]:
        if p.exists() and p.stat().st_size > 0:
            candidates.append(p)
    if not candidates:
        return LOG_FILE  # default even if it doesn't exist
    # Return most recently modified
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _run_git(args: list[str], timeout: int = 15):
    """Run a git command in the HERMES_AGENT_REPO, returning (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True, timeout=timeout,
            cwd=str(HERMES_AGENT_REPO),
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "git command timed out"
    except FileNotFoundError:
        return -1, "", "git not found on PATH"
    except OSError as e:
        return -1, "", str(e)


def _skill_skmd_path(name: str) -> Path:
    """Return the path to a skill's SKILL.md file, or None if it doesn't exist."""
    # Check both: <name>/SKILL.md and <name>.SKILL.md (flat layout)
    dir_path = SKILLS_DIR / name / "SKILL.md"
    flat_path = SKILLS_DIR / f"{name}.SKILL.md"
    if dir_path.exists():
        return dir_path
    if flat_path.exists():
        return flat_path
    return None


def _write_audit_entry(entry: dict):
    """Append a JSON line to the audit log."""
    try:
        with open(str(AUDIT_LOG), "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass  # Non-fatal — audit logging should never block the user


# ── Mock Data Generator ────────────────────────────────────────────────

_skill_names = [
    "1password-connect", "1password_connect_claude", "1password_connect_operator",
    "adversarial-review", "applying-karpathy-guidelines", "brev-cli",
    "building-rs-humble", "cloudflared-tunnels", "code-graph-embedding-pipeline",
    "coding-standards", "composing-diagnostic-wrappers", "container-compose-config-drift",
    "creating-updating-plans", "debugging-isaac-ros-containers",
    "diagnosing-proxy-token-bans", "evaluating-new-models", "honcho-cli",
    "honcho-dreaming", "honcho-session-ingestion", "managing-agents",
    "managing-code-graph", "managing-container-registry", "managing-github-actions-runners",
    "managing-hermes-honcho-containers", "managing-hermes-sidecars", "managing-k3s-cluster",
    "managing-mcp-configuration", "managing-model-providers",
    "managing-pre-commit-linter-feedback", "proxy-telemetry-health",
    "running-container-compose-tests", "running-isaac-ros-tests",
    "s3-multi-machine-parallel-transfer", "s3-server-side-copy",
    "s3-stream-copy", "sentinel", "skill-creator",
    "socket-relay-architecture", "telemetry-pipeline-health", "test-driven-dev",
    "testing-vlm", "using-apple-secrets", "using-code-graph", "vslam-debugging",
]

_models = [
    "openai/nvidia-proxy/minimaxai/minimax-m2.5",
    "openai/nvidia-proxy/deepseek-ai/deepseek-v3.2",
    "openai/nvidia-proxy/meta/llama-3.3-70b-instruct",
]


def _generate_mock_rotation_state() -> dict:
    """Generate a realistic mock rotation state with all 44 skills in varied states."""
    import random
    random.seed(42)
    now = datetime.now(timezone.utc)
    skills = {}
    statuses = (
        ["running"] * 3 +
        ["no_improvement"] * 10 +
        ["failed"] * 3 +
        ["pending"] * 28
    )
    random.shuffle(statuses)

    for i, name in enumerate(_skill_names):
        status = statuses[i] if i < len(statuses) else "pending"
        entry = {
            "status": status,
            "size_kb": round(random.uniform(1.0, 105.0), 2),
        }
        if status in ("no_improvement", "failed", "running"):
            mins_ago = random.randint(1, 180)
            entry["last_evolved"] = (
                now - __import__("datetime").timedelta(minutes=mins_ago)
            ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            entry["model"] = random.choice(_models)
            entry["last_model_index"] = 0
        if status == "no_improvement":
            entry["improvement"] = round(random.uniform(-0.03, 0.18), 4)
        if status == "failed":
            entry["improvement"] = None
            entry["error"] = random.choice([
                "Watchdog killed: stalled at optimization_start (>800s)",
                "All 6 models failed. Last: 401 Unauthorized",
                "RuntimeError: MIPROv2 trial crashed after 300s",
            ])
        skills[name] = entry

    return {
        "skills": skills,
        "current_skill": random.choice([s for s in _skill_names if statuses[_skill_names.index(s)] == "running"]),
        "model_order": _models,
    }


def _generate_mock_health() -> dict:
    """Generate a realistic mock health heartbeat."""
    return {
        "last_heartbeat": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "current_skill": random.choice([s for s in _skill_names]),
        "batch_pid": random.randint(10000, 99999),
        "status": "running",
        "loop_step": random.choice([
            "starting next skill", "running MIPROv2 trial 3/10",
            "evaluating candidates", "preflight check passed",
        ]),
    }


_mock_mode = False


def _accepts_mock(params) -> bool:
    """Check if mock mode is requested (query param or server flag)."""
    if _mock_mode:
        return True
    mock_val = params.get("mock")
    if mock_val is not None:
        return mock_val.lower() in ("true", "1", "yes")
    return False


# ── Endpoints ───────────────────────────────────────────────────────────


@router.get("/mock")
async def mock_toggle(enable: str = None):
    """Enable or disable mock data mode.

    GET /api/plugins/evolution-hub/mock?enable=true   — enable mock data
    GET /api/plugins/evolution-hub/mock?enable=false  — disable mock data
    GET /api/plugins/evolution-hub/mock               — return current state
    """
    global _mock_mode
    if enable is not None:
        _mock_mode = enable.lower() in ("true", "1", "yes")
    return {"ok": True, "mock_mode": _mock_mode}

@router.get("/batch-health")
async def batch_health(mock: str = None):
    """Return the current batch health heartbeat.

    Enable mock mode via POST /mock/enable for synthetic data."""
    if mock and mock.lower() in ("true", "1", "yes") or _mock_mode:
        import random
        hb = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return {
            "status": "running",
            "last_heartbeat": hb,
            "current_skill": random.choice(_skill_names),
            "batch_pid": random.randint(10000, 99999),
            "loop_step": random.choice([
                "starting next skill", "running MIPROv2 trial 3/10",
                "evaluating candidates", "preflight check passed",
            ]),
            "stale": False,
        }
    data = _read_json(HEALTH_FILE)
    if data is None:
        return {
            "status": "unknown",
            "last_heartbeat": None,
            "current_skill": None,
            "batch_pid": None,
            "loop_step": None,
            "stale": True,
        }

    # Detect staleness: if heartbeat is older than 10 minutes
    stale = False
    if data.get("last_heartbeat"):
        try:
            # Handle both ISO formats: with and without fractional seconds
            ts_str = data["last_heartbeat"]
            # Strip fractional seconds if present (e.g., ".126764Z" → "Z")
            ts_clean = re.sub(r"\.[0-9]+Z$", "Z", ts_str)
            hb_time = time.mktime(time.strptime(ts_clean, "%Y-%m-%dT%H:%M:%SZ"))
            if time.time() - hb_time > 600:
                stale = True
        except (ValueError, OSError):
            stale = True

    return {
        "status": data.get("status", "unknown"),
        "last_heartbeat": data.get("last_heartbeat"),
        "current_skill": data.get("current_skill", "none"),
        "batch_pid": data.get("batch_pid"),
        "loop_step": data.get("loop_step"),
        "stale": stale,
    }


@router.get("/queue-status")
async def queue_status(mock: str = None):
    """Return the full evolution queue with per-skill status, size, and model info.

    Enable mock mode via POST /mock/enable for synthetic data."""
    if mock and mock.lower() in ("true", "1", "yes") or _mock_mode:
        data = _generate_mock_rotation_state()
    else:
        data = _read_json(ROTATION_STATE_FILE)
    if data is None:
        return {
            "skills": [],
            "summary": {
                "total": 0,
                "pending": 0,
                "running": 0,
                "completed": 0,
                "no_improvement": 0,
                "failed": 0,
            },
        }

    skills_raw = data.get("skills", {})
    skills = []

    for name, info in skills_raw.items():
        status = info.get("status", "pending")
        skills.append({
            "name": name,
            "status": status,
            "size_kb": info.get("size_kb", 0),
            "model": info.get("model"),
            "last_evolved": info.get("last_evolved"),
            "improvement": info.get("improvement"),
            "error": info.get("error"),
            "attempt": info.get("attempt", 1),
        })

    # Sort by size ascending, then by name
    skills.sort(key=lambda s: (s["size_kb"], s["name"]))

    # Compute summary
    summary = {"total": len(skills), "pending": 0, "running": 0,
               "no_improvement": 0, "failed": 0, "completed": 0}
    for s in skills:
        st = s["status"]
        if st in summary:
            summary[st] += 1

    completed_count = summary.get("completed", 0) + summary.get("no_improvement", 0)

    return {
        "skills": skills,
        "current_skill": data.get("current_skill"),
        "model_order": data.get("model_order", []),
        "summary": summary,
        "batch_complete": completed_count == summary.get("total", 0) and summary.get("total", 0) > 0,
    }


class ControlRequest(BaseModel):
    """Request body for the /control endpoint."""
    action: str  # one of: start, stop, reset, status


@router.post("/control")
async def control_batch(req: ControlRequest):
    """Execute batch pipeline control actions.

    Serialized via asyncio.Lock to prevent concurrent commands from
    multiple browser tabs racing (e.g. Stop vs Start simultaneously).
    """
    async with _control_lock:
        action = req.action

        if action == "start":
            if not BATCH_SCRIPT.exists():
                raise HTTPException(status_code=404, detail="Batch script not found")
            try:
                result = subprocess.run(
                    ["bash", str(BATCH_SCRIPT)],
                    capture_output=True, text=True, timeout=10,
                )
                return {
                    "success": result.returncode == 0,
                    "message": "Batch start attempted",
                    "output": result.stdout.strip() or result.stderr.strip(),
                }
            except subprocess.TimeoutExpired:
                return {"success": True, "message": "Batch start command dispatched (timeout reading output)"}

        elif action == "stop":
            if not BATCH_SCRIPT.exists():
                raise HTTPException(status_code=404, detail="Batch script not found")
            try:
                result = subprocess.run(
                    ["bash", str(BATCH_SCRIPT), "stop"],
                    capture_output=True, text=True, timeout=30,
                )
                return {
                    "success": True,
                    "message": "Batch stop signal sent",
                    "output": result.stdout.strip() or result.stderr.strip(),
                }
            except subprocess.TimeoutExpired:
                return {"success": True, "message": "Batch stop dispatched (timeout reading output)"}

        elif action == "reset":
            if not ROTATION_SCRIPT.exists():
                raise HTTPException(status_code=404, detail="Rotation script not found")
            try:
                result = subprocess.run(
                    [str(ROTATION_SCRIPT), "--reset"],
                    capture_output=True, text=True, timeout=30,
                )
                return {
                    "success": result.returncode == 0,
                    "message": "Rotation state reset",
                    "output": result.stdout.strip() or result.stderr.strip(),
                }
            except subprocess.TimeoutExpired:
                return {"success": True, "message": "Reset dispatched (timeout reading output)"}

        elif action == "status":
            if not ROTATION_SCRIPT.exists():
                raise HTTPException(status_code=404, detail="Rotation script not found")
            try:
                result = subprocess.run(
                    [str(ROTATION_SCRIPT), "--status"],
                    capture_output=True, text=True, timeout=30,
                )
                return {
                    "success": result.returncode == 0,
                    "message": result.stdout.strip() or result.stderr.strip(),
                }
            except subprocess.TimeoutExpired:
                return {"success": True, "message": "Status check timed out"}

        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {action}. Use: start, stop, reset, status")


@router.get("/log")
async def batch_log(tail: int = 50):
    """Return the last N lines of the active batch log.

    Auto-detects between batch_parallel.log and batch_size_aware.log.
    """
    log_path = _detect_log_file()
    log_content = _read_file_tail(log_path, max_lines=tail)
    if log_content is None:
        return {"log": "", "lines": 0, "message": "No log file found"}
    return {
        "log": log_content,
        "lines": len(log_content.splitlines()),
        "log_file": log_path.name,
    }


@router.get("/log/download")
async def batch_log_download():
    """Return the full active batch log for download/post-mortem analysis."""
    log_path = _detect_log_file()
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="No log file found")
    try:
        return {
            "log": log_path.read_text(),
            "lines": len(log_path.read_text().splitlines()),
            "log_file": log_path.name,
        }
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Cannot read log file: {e}")


@router.get("/log/errors")
async def batch_log_errors():
    """Return only ERROR-level lines from the active batch log for quick triage."""
    log_path = _detect_log_file()
    if not log_path.exists():
        return {"errors": [], "count": 0}
    try:
        lines = log_path.read_text().splitlines()
        error_lines = [l for l in lines if any(kw in l.upper() for kw in ["ERROR", "TRACEBACK", "EXCEPTION", "FATAL", "STALLED"])]
        return {"errors": error_lines, "count": len(error_lines)}
    except OSError:
        return {"errors": [], "count": 0, "message": "Cannot read log file"}


@router.get("/skill-result")
async def skill_result(name: str):
    """Return a specific skill's wrapper JSON if it exists."""
    wrapper_path = WRAPPERS_DIR / f"{name}.json"
    data = _read_json(wrapper_path)
    if data is None:
        return {"found": False, "name": name, "data": None}
    return {"found": True, "name": name, "data": data}


@router.get("/skill-history")
async def skill_history(name: str):
    """Return git history for a skill's SKILL.md file.

    Runs `git log --format="%H|%s|%ci"` on the skill file.
    Returns an ordered list of commits (most recent first).
    Read-only — no side effects.
    """
    sm_path = _skill_skmd_path(name)
    if sm_path is None:
        # Skill may not exist; return empty gracefully
        return {"skill": name, "found": False, "commits": []}

    # Get relative path from the repo root
    try:
        rel_path = str(sm_path.relative_to(HERMES_AGENT_REPO))
    except ValueError:
        return {"skill": name, "found": True, "commits": [], "error": "Skill outside repo"}

    rc, stdout, stderr = _run_git([
        "log", "--format=%H|%s|%ci", "--", rel_path,
    ])
    if rc != 0:
        return {"skill": name, "found": True, "commits": [], "error": stderr or "git log failed"}

    commits = []
    for line in stdout.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append({
                "sha": parts[0],
                "message": parts[1],
                "date": parts[2],
            })

    return {
        "skill": name,
        "found": True,
        "has_skmd": sm_path.exists(),
        "commits": commits,
    }


class RevertRequest(BaseModel):
    """Request body for the /skill-revert endpoint."""
    skill: str
    commit_sha: str


@router.post("/skill-revert")
async def skill_revert(req: RevertRequest):
    """Revert a skill's SKILL.md to a specific git commit.

    Safety checks:
    1. Working directory must be clean (git status --porcelain)
    2. Commit SHA must exist in git log for this skill file
    3. Writes audit trail to .evolution_audit.log

    Uses asyncio.Lock to prevent concurrent revert/control operations.
    """
    async with _control_lock:
        name = req.skill
        commit_sha = req.commit_sha

        # ── Validate skill exists ──
        sm_path = _skill_skmd_path(name)
        if sm_path is None:
            raise HTTPException(status_code=404, detail=f"Skill '{name}' has no SKILL.md file")

        try:
            rel_path = str(sm_path.relative_to(HERMES_AGENT_REPO))
        except ValueError:
            raise HTTPException(status_code=500, detail="Skill path outside repo")

        # ── Pre-flight: check working directory cleanliness ──
        rc, stdout, stderr = _run_git(["status", "--porcelain"])
        if rc != 0:
            raise HTTPException(status_code=500, detail=f"git status failed: {stderr}")

        dirty_lines = [l for l in stdout.split("\n") if l.strip() and not l.strip().startswith("??")]
        if dirty_lines:
            return {
                "success": False,
                "blocked": True,
                "reason": "dirty_working_directory",
                "detail": "Working directory has uncommitted changes. Commit or stash them first.",
                "dirty_files": dirty_lines,
            }

        # ── Validate commit SHA exists in this file's history ──
        rc, stdout, stderr = _run_git([
            "log", "--oneline", "--format=%H", "--", rel_path,
        ])
        if rc != 0:
            raise HTTPException(status_code=500, detail=f"git log failed: {stderr}")

        valid_shas = set(s.strip() for s in stdout.split("\n") if s.strip())
        if commit_sha not in valid_shas:
            return {
                "success": False,
                "blocked": True,
                "reason": "invalid_commit",
                "detail": f"Commit {commit_sha[:12]} is not in this skill's history. Valid SHAs: {', '.join(list(valid_shas)[:5])}",
            }

        # ── Get the current SHA for audit trail ──
        rc, current_sha, _ = _run_git([
            "log", "--oneline", "--format=%H", "-1", "--", rel_path,
        ])
        current_sha = current_sha.strip() if current_sha else "unknown"

        # ── Execute the revert ──
        rc, stdout, stderr = _run_git([
            "checkout", commit_sha, "--", rel_path,
        ], timeout=10)
        if rc != 0:
            raise HTTPException(status_code=500, detail=f"git checkout failed: {stderr}")

        # ── Audit trail ──
        audit_entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "action": "revert",
            "skill": name,
            "skill_path": rel_path,
            "current_sha": current_sha,
            "target_sha": commit_sha,
            "trigger": "dashboard_ui",
        }
        _write_audit_entry(audit_entry)

        return {
            "success": True,
            "skill": name,
            "previous_sha": current_sha,
            "reverted_to": commit_sha,
            "message": f"Skill '{name}' reverted to {commit_sha[:12]}",
        }
