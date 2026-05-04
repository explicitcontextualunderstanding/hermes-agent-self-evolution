#!/usr/bin/env python3
"""
OTelPromptAdapter — GEPA adapter that evaluates prompts by running them
through Hermes Agent, then querying OTel spans from PostgreSQL for
performance metrics.

Phase 1 of Plan 123: OTel-driven evaluation for GEPA prompt optimization.

Usage:
    from evolution.prompts.otel_adapter import OTelPromptAdapter

    adapter = OTelPromptAdapter()
    result = adapter.evaluate(batch, candidate)
    objective_scores, scores, trajectories = result
"""

import functools
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Default configuration ──────────────────────────────────────────────────
DEFAULT_HERMES_BIN = "/Users/kieranlal/.hermes/hermes-agent/venv/bin/hermes"
DEFAULT_PROFILE = "coding"
DEFAULT_DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5432,
    "user": "postgres",
    "database": "harness_evolution",
}
DEFAULT_DIMENSIONS = [
    "pass",
    "efficiency",
    "tool_efficiency",
    "token_efficiency",
    "composite",
]

# Scoring weights for composite
W_PASS = 0.5
W_EFFICIENCY = 0.2
W_TOOL_EFFICIENCY = 0.2
W_TOKEN_EFFICIENCY = 0.1

# Scoring thresholds
MAX_DURATION_MS = 30000.0
MAX_TOKENS = 100000

# Session ID regex: looks for "hermes --resume <session_id>" in output
SESSION_ID_RE = re.compile(r"hermes\s+--resume\s+(\S+)")

# Cleanup prompt sent to Hermes after each evaluation
CLEANUP_PROMPT = (
    "Clean up all test resources I created: "
    "Delete all containers with names starting with 'test-', 'stress-', "
    "'lifecycle-', 'checkpoint-', 'rapid-', 'duplicate-', 'stopped-', "
    "'bad-', 'empty-', 'special-', 'default-', 'not-started', "
    "'standalone-', or 'hybrid-'. "
    "Delete all checkpoints. "
    "Delete all AgentSpecs with names starting with 'minimal-', "
    "'tool-', 'full-', or 'container-'. "
    "Delete all pods with names starting with 'test-' or 'hybrid-'."
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clip value to [lo, hi] range."""
    return max(lo, min(hi, value))


def _normalize(value: float) -> float:
    """Normalize a score to [0, 1]. Assumes input is already roughly in range."""
    return _clip(value, 0.0, 1.0)


# ── Prompt Validator Integration ────────────────────────────────────────────
# Path to compose-pkl's prompt_validator.py for cross-repo validation.
# Override via VALIDATOR_PATH env var.
DEFAULT_VALIDATOR_PATH = os.environ.get(
    "VALIDATOR_PATH",
    "/Users/kieranlal/workspace/compose-pkl/scripts/prompt_validator.py",
)


def _run_prompt_validator(prompt_text: str) -> dict:
    """Run prompt_validator.py and return validation result.

    Returns dict with score, tool_names_found, errors, summary.
    Returns empty validation (score=1.0) if validator not found.
    """
    validator = Path(DEFAULT_VALIDATOR_PATH)
    if not validator.exists():
        return {"score": 1.0, "tool_names_found": [], "errors": [],
                "summary": "Validator not available", "valid_parameters": True}

    try:
        r = subprocess.run(
            [sys.executable, str(validator), "--json", prompt_text],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return json.loads(r.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        pass
    return {"score": 1.0, "tool_names_found": [], "errors": [],
            "summary": "Validator error", "valid_parameters": True}


# ── Named-Resource Parameterization ─────────────────────────────────────────
# Prompts create resources with names like "test-dev", "web-nginx". When
# evaluating multiple candidates in the same session, these names collide.
# This function appends a run-specific suffix to prevent state contamination.
#
# Regex: find resource names after "named", "name", "called", or starting
# with a known test prefix (test-, stress-, vision-, mlx-, batch-, spec-,
# compose-, cp-, web-, nginx).
_RESOURCE_NAME_RE = re.compile(
    r'(?:named\s+|name\s+|called\s+)'        # "create container named X"
    r'["\']([a-zA-Z0-9][-a-zA-Z0-9_.]+)["\']'  # the quoted name (at least 2 chars)
    r'|["\']((?:test|stress|vision|mlx|batch|spec|compose|cp|web|nginx|agent|full'
    r'|minimal|tool|standalone|hybrid|lifecycle|rapid|duplicate|stopped|bad|empty'
    r'|special|default|not|ghost|fleet)[-a-zA-Z0-9_]{2,})["\']'  # at least 2 more chars
)

# Specific one-off names from the 121 prompts not caught by the regex above
_ONE_OFF_RESOURCE_NAMES = {
    "vision-frame-0", "clip-input", "exp-001", "hostcapabilityerror",
    "live-constraint", "metalparavirtualized", "immutable-assistant",
    "initimeout", "container-operator", "fleetagent", "fleet-agent",
    "ipaddress", "startedat", "finishedat", "conflict",
}


def _parameterize_names(prompt_text: str, suffix: str) -> str:
    """Replace resource names in prompt text with suffixed versions.

    Each resource name gets _{suffix} appended. For example, with suffix
    'a1b2': "Delete container named 'test-dev'" → "Delete container
    named 'test-dev_a1b2'".

    The suffix is typically a short random string or prompt number, e.g.,
    'p03' for prompt #3 in the current evolution run.
    """
    if not suffix:
        return prompt_text

    result = prompt_text

    # Replace regex-caught names
    def _replace_match(m):
        groups = m.groups()
        name = groups[0] or groups[1]
        # Skip image references (containing ':') and names that are too short
        if name and name not in _ONE_OFF_RESOURCE_NAMES and ':' not in name:
            return m.group(0).replace(name, f"{name}_{suffix}")
        return m.group(0)

    result = _RESOURCE_NAME_RE.sub(_replace_match, result)

    # Replace one-off names (less common, explicit list)
    for name in _ONE_OFF_RESOURCE_NAMES:
        if name in result:
            result = result.replace(name, f"{name}_{suffix}")

    return result


# ── Scoring functions ──────────────────────────────────────────────────────

def _score_pass(span_attrs: dict) -> float:
    """Graded pass/fail (P1.4): 1.0 clean, 0.5 partial/tool errors, 0.0 no execution.

    Uses OTel span attributes for execution quality:
    - 1.0: final_status == 'completed' and tool calls were made
    - 0.5: Tool calls were made but agent hit budget (incomplete)
    - 0.0: No tool calls or session didn't start (hung/crash)

    CRITICAL: The hermes-otel plugin sets final_status to "incomplete"
    when the agent hits its iteration budget — this is a NORMAL termination
    for complex prompts, not a failure. The primary signal is whether the
    agent made at least one tool call (api_call_count > 0).
    """
    api_calls = int(span_attrs.get("hermes.turn.api_call_count", 0) or 0)
    status = span_attrs.get("hermes.turn.final_status", "")
    session_completed = span_attrs.get("hermes.session.completed", False)

    # Primary signal: agent made tool calls and completed
    if status == "completed" and api_calls > 0:
        return 1.0

    # Partial success: agent made tool calls but didn't formally complete
    # (iteration budget reached, complex multi-step prompt)
    if api_calls > 0:
        return 0.5

    # Session completed flag (alternative completion signal)
    if session_completed:
        return 0.5

    # No tool calls and no completion = genuine failure
    return 0.0


def _get_db_config() -> dict:
    """Return the default OTel database configuration."""
    import copy
    return copy.deepcopy(DEFAULT_DB_CONFIG)


def _query_infrastructure_spans(session_start: str | None = None,
                                  window_minutes: int = 3) -> list[dict]:
    """Query compose-pkl infrastructure spans near a session time window.

    Two-tier correlation:
      Tier 1 (exact): If a pod.realize span exists in the time window,
        all spans sharing its trace_id are retrieved. This gives exact
        attribution instead of statistical proximity.
      Tier 2 (time-window fallback): Same as Phase 1 — returns
        container.create, .start, .stop, .delete spans whose start_time
        falls within ±window_minutes of the session timestamp.

    Also returns lifecycle metadata: pod trace_ids, harness ingestion
    sentinel presence, and lifecycle completeness flags.

    Args:
        session_start: ISO timestamp string. If None, returns recent spans.
        window_minutes: Time window in minutes on each side.

    Returns:
        list of dicts with infrastructure span data plus sentinel flags.
    """
    import pg8000
    db = _get_db_config()
    try:
        conn = pg8000.connect(
            host=db["host"], port=db["port"],
            user=db["user"], database=db["database"],
        )
        cur = conn.cursor()

        time_filter = ""
        params: list = []
        if session_start:
            time_filter = (
                "AND start_time BETWEEN %s::timestamptz - interval '%s minutes' "
                "AND %s::timestamptz + interval '%s minutes'"
            )
            params = [session_start, window_minutes, session_start, window_minutes]

        # ── Step 1: Find pod.realize spans and extract trace_ids + span_ids ─
        pod_query = f"""
            SELECT trace_id, span_id, attributes::text
            FROM otel_spans
            WHERE service_name = 'compose-pkl'
              AND name = 'pod.realize'
              {time_filter}
            ORDER BY start_time DESC
            LIMIT 10
        """
        cur.execute(pod_query, params)
        pod_rows = cur.fetchall()
        pod_trace_ids: list[str] = []
        pod_span_ids: list[str] = []
        pod_metadata: list[dict] = []
        for row in pod_rows:
            tid = row[0]
            sid = row[1]
            if tid:
                pod_trace_ids.append(str(tid))
            if sid:
                pod_span_ids.append(str(sid))
            attrs = {}
            if row[2]:
                try:
                    attrs = json.loads(row[2]) if isinstance(row[2], str) else row[2]
                except (json.JSONDecodeError, TypeError):
                    pass
            pod_metadata.append({"trace_id": tid, "span_id": sid, **attrs})

        # ── Step 2: Exact correlation (Tier 1) via trace_id OR parent_span_id
        #            (LDP trace_id match + ContextBus parent_span_id lineage)
        exact_spans: list[tuple] = []
        id_filters: list[str] = []
        id_params: list[str] = []
        if pod_trace_ids:
            placeholders = ",".join("%s" for _ in pod_trace_ids)
            id_filters.append(f"trace_id IN ({placeholders})")
            id_params.extend(pod_trace_ids)
        if pod_span_ids:
            placeholders = ",".join("%s" for _ in pod_span_ids)
            id_filters.append(f"parent_span_id IN ({placeholders})")
            id_params.extend(pod_span_ids)
        if id_filters:
            combined_filter = " OR ".join(id_filters)
            trace_query = f"""
                SELECT name, span_id, trace_id, parent_span_id, start_time, end_time,
                       duration_ms, status_code, attributes::text
                FROM otel_spans
                WHERE service_name = 'compose-pkl'
                  AND ({combined_filter})
                ORDER BY start_time ASC
            """
            cur.execute(trace_query, id_params)
            exact_spans = cur.fetchall()

        # ── Step 3: Time-window fallback (Tier 2) ────────────────────────
        if session_start:
            cur.execute(
                f"""
                SELECT name, span_id, trace_id, parent_span_id, start_time, end_time,
                       duration_ms, status_code, attributes::text
                FROM otel_spans
                WHERE service_name = 'compose-pkl'
                  AND name IN ('container.create', 'container.start',
                               'container.stop', 'container.delete')
                  {time_filter}
                ORDER BY start_time ASC
                """,
                params,
            )
        else:
            cur.execute(
                """
                SELECT name, span_id, trace_id, parent_span_id, start_time, end_time,
                       duration_ms, status_code, attributes::text
                FROM otel_spans
                WHERE service_name = 'compose-pkl'
                  AND name IN ('container.create', 'container.start',
                               'container.stop', 'container.delete')
                ORDER BY start_time DESC
                LIMIT 50
                """
            )

        time_window_spans = cur.fetchall()

        # ── Step 4: Deduplicate (Tier 1 spans take priority) ─────────────
        exact_keys = set()
        combined: list[tuple] = list(exact_spans)
        for row in exact_spans:
            exact_keys.add((row[1], row[2]))  # (span_id, trace_id)
        for row in time_window_spans:
            if (row[1], row[2]) not in exact_keys:
                combined.append(row)

        # ── Step 5: Check for harness.trace.ingest sentinel ──────────────
        sentinel_query = f"""
            SELECT COUNT(*) as cnt
            FROM otel_spans
            WHERE service_name = 'compose-pkl'
              AND name = 'harness.trace.ingest'
              {time_filter}
        """
        cur.execute(sentinel_query, params)
        sentinel_present = cur.fetchone()[0] > 0

        # ── Step 6: Parse results from combined (deduplicated) spans ─────
        results = []
        col_names = [
            "name", "span_id", "trace_id", "parent_span_id", "start_time", "end_time",
            "duration_ms", "status_code", "attributes",
        ]
        for row in combined:
            d = dict(zip(col_names, row))
            # Parse attributes if JSON string
            if isinstance(d.get("attributes"), str):
                try:
                    d["attributes"] = json.loads(d["attributes"])
                except (json.JSONDecodeError, TypeError):
                    d["attributes"] = {}
            results.append(d)

        cur.close()
        conn.close()
        return {
            "spans": results,
            "sentinel_present": sentinel_present,
            "pod_trace_ids": pod_trace_ids,
            "pod_metadata": pod_metadata,
        }
    except Exception as e:
        logger.warning(f"Failed to query infrastructure spans: {e}")
        return {"spans": [], "sentinel_present": False, "pod_trace_ids": [], "pod_metadata": []}


def _score_infrastructure(infra_spans: list[dict]) -> dict:
    """Score infrastructure execution from compose-pkl spans.

    Returns dict with:
      - containers_created: count of container.create spans
      - containers_started: count of container.start spans
      - containers_stopped: count of container.stop spans
      - containers_deleted: count of container.delete spans
      - creation_success: True if at least one container.create completed
      - boot_success: True if at least one container.start completed
      - cleanup_success: True if at least one container.stop or .delete completed
      - lifecycle_pass: lifecycle completeness score (0.0-1.0)
          Full cycle (create → start → stop/delete): 1.0
          Partial (create → start, no cleanup): 0.5
          Orphaned (create only, no start): 0.0
      - infra_pass: combined infrastructure pass score (0.0-1.0)
      - avg_create_ms: average container creation duration
      - avg_boot_ms: average container boot duration
    """
    creates = [s for s in infra_spans if s["name"] == "container.create"]
    starts = [s for s in infra_spans if s["name"] == "container.start"]
    stops = [s for s in infra_spans if s["name"] == "container.stop"]
    deletes = [s for s in infra_spans if s["name"] == "container.delete"]

    # A successful create has status_code=0 (OK) or is non-null
    creates_ok = sum(1 for s in creates if s.get("status_code") in (0, "0", "OK", None))
    starts_ok = sum(1 for s in starts if s.get("status_code") in (0, "0", "OK", None))

    avg_create_ms = 0.0
    if creates:
        durations = [s.get("duration_ms", 0) or 0 for s in creates]
        avg_create_ms = sum(durations) / len(durations)

    avg_boot_ms = 0.0
    if starts:
        durations = [s.get("duration_ms", 0) or 0 for s in starts]
        avg_boot_ms = sum(durations) / len(durations)

    # Infrastructure pass score: did containers actually get created?
    infra_pass = 0.0
    if creates_ok > 0:
        infra_pass += 0.6
    if starts_ok > 0:
        infra_pass += 0.4

    # Lifecycle pass: does the prompt clean up after itself?
    # Full cycle = create → start → (stop OR delete)
    cleanup_success = (len(stops) + len(deletes)) > 0
    if creates_ok > 0 and starts_ok > 0 and cleanup_success:
        lifecycle_pass = 1.0
    elif creates_ok > 0 and starts_ok > 0:
        lifecycle_pass = 0.5
    elif creates_ok > 0:
        lifecycle_pass = 0.0
    else:
        lifecycle_pass = 0.0

    return {
        "containers_created": len(creates),
        "containers_started": len(starts),
        "containers_stopped": len(stops),
        "containers_deleted": len(deletes),
        "creation_success": creates_ok > 0,
        "boot_success": starts_ok > 0,
        "cleanup_success": cleanup_success,
        "lifecycle_pass": round(lifecycle_pass, 4),
        "infra_pass": round(infra_pass, 4),
        "avg_create_ms": round(avg_create_ms, 1),
        "avg_boot_ms": round(avg_boot_ms, 1),
    }


def _compute_scores(span_attrs: dict, duration_ms: float = 0.0,
                     infra_spans: list[dict] | None = None) -> dict:
    """Compute all objective scores from span attributes and infrastructure spans.

    Args:
        span_attrs: OTel span attributes from hermes-agent.
        duration_ms: Agent span duration.
        infra_spans: Optional compose-pkl infrastructure spans for
                     end-to-end execution verification.

    Returns:
        dict with pass, efficiency, tool_efficiency, token_efficiency, composite.
    """
    agent_pass = _score_pass(span_attrs)

    # Infrastructure pass: if infra spans available, blend with agent pass
    if infra_spans:
        infra = _score_infrastructure(infra_spans)
        infra_pass = infra["infra_pass"]
        lifecycle_pass = infra.get("lifecycle_pass", 0.0)

        # Lifecycle penalty: prompts creating orphan containers get docked
        # If there are containers but none were stopped/deleted, apply
        # a 0.8x multiplier to the infra contribution.
        has_containers = infra["containers_created"] > 0
        lifecycle_mult = (0.5 + (lifecycle_pass * 0.5)) if has_containers else 1.0

        # Blended pass: infrastructure can boost partial successes but
        # cannot override total agent failure (0 calls = 0 pass)
        if agent_pass == 0.0 and infra_pass > 0:
            # Agent didn't execute but infra was active — partial credit
            pass_score = min(0.5, infra_pass * 0.6 * lifecycle_mult)
        else:
            pass_score = max(agent_pass, infra_pass * 0.8 * lifecycle_mult)
    else:
        pass_score = agent_pass

    obj = {
        "pass": pass_score,
        "efficiency": _score_efficiency(span_attrs, duration_ms),
        "tool_efficiency": _score_tool_efficiency(span_attrs),
        "token_efficiency": _score_token_efficiency(span_attrs),
    }
    if infra_spans:
        obj["infrastructure"] = _score_infrastructure(infra_spans)

    obj["composite"] = _score_composite(obj)
    return obj
def _score_efficiency(span_attrs: dict, duration_ms: float = 0.0) -> float:
    """Duration efficiency: clip(1.0 - duration_ms/30000, 0, 1)."""
    # Prefer duration_ms from span, fall back to attributes
    if duration_ms <= 0:
        duration_ms = float(span_attrs.get("llm.response.duration_ms", 0))
    return _clip(1.0 - duration_ms / MAX_DURATION_MS, 0.0, 1.0)


def _score_tool_efficiency(span_attrs: dict) -> float:
    """Tool-call efficiency: 1.0 / max(api_call_count, 1)."""
    api_calls = int(span_attrs.get("hermes.turn.api_call_count", 1) or 1)
    return 1.0 / max(api_calls, 1)


def _score_token_efficiency(span_attrs: dict) -> float:
    """Token efficiency: clip(1.0 - total_tokens/100000, 0, 1)."""
    total_tokens = int(span_attrs.get("llm.token_count.total", 0) or 0)
    return _clip(1.0 - total_tokens / MAX_TOKENS, 0.0, 1.0)


def _score_composite(obj_scores: dict) -> float:
    """Composite score as weighted sum of individual dimensions."""
    return (
        W_PASS * obj_scores["pass"]
        + W_EFFICIENCY * obj_scores["efficiency"]
        + W_TOOL_EFFICIENCY * obj_scores["tool_efficiency"]
        + W_TOKEN_EFFICIENCY * obj_scores["token_efficiency"]
    )


# ── Session ID extraction ──────────────────────────────────────────────────

def _extract_session_id(output: str) -> Optional[str]:
    """Extract session ID from hermes output.

    The output contains a line like::
        hermes --resume 20260430_072206_b798c8
    """
    m = SESSION_ID_RE.search(output)
    return m.group(1) if m else None


# ── OTel DB query ──────────────────────────────────────────────────────────

def _query_otel_spans(session_id: str, db_config: dict) -> list[dict]:
    """Query otel_spans table for spans matching session_id.

    Returns list of dicts with span attributes and metadata.
    Returns empty list if no spans found (valid state — new session).
    Raises RuntimeError if the database is unreachable or table missing
    (infrastructure failure — should halt the pipeline).
    """
    try:
        import pg8000

        conn = pg8000.connect(
            host=db_config["host"],
            port=db_config["port"],
            user=db_config["user"],
            database=db_config["database"],
        )
        cur = conn.cursor()

        # Query spans matching session_id in attributes JSONB
        cur.execute(
            """
            SELECT span_id, trace_id, parent_span_id, name, kind,
                   start_time, end_time, duration_ms, status_code,
                   status_message, attributes, events, links,
                   resource_attributes, scope_name, scope_version,
                   service_name, ingested_at
            FROM otel_spans
            WHERE attributes->>'session_id' = %s
               OR attributes->>'hermes.session_id' = %s
            ORDER BY start_time ASC
            """,
            (session_id, session_id),
        )

        rows = cur.fetchall()
        results = []
        col_names = [
            "span_id", "trace_id", "parent_span_id", "name", "kind",
            "start_time", "end_time", "duration_ms", "status_code",
            "status_message", "attributes", "events", "links",
            "resource_attributes", "scope_name", "scope_version",
            "service_name", "ingested_at",
        ]
        for row in rows:
            d = dict(zip(col_names, row))
            # Parse attributes if it's a string (pg8000 returns JSONB as string)
            if isinstance(d.get("attributes"), str):
                d["attributes"] = json.loads(d["attributes"])
            results.append(d)

        cur.close()
        conn.close()
        return results

    except ImportError:
        # pg8000 not installed — soft fallback, log warning
        logger.warning("Failed to query OTel spans: No module named 'pg8000'")
        return []
    except Exception as e:
        err_str = str(e)
        # Database connection errors are infrastructure failures — halt the pipeline
        db_dead_keywords = [
            "connection refused", "could not connect", "timeout",
            "does not exist", "could not translate", "no route to host",
            "ssl connection", "authentication failed",
        ]
        if any(kw in err_str.lower() for kw in db_dead_keywords):
            raise RuntimeError(
                f"OTel database unreachable: {err_str[:200]}. "
                f"Run `skill honcho-db-otel-infrastructure` to restore."
            ) from e
        # Other errors (query syntax, etc.) — soft fallback
        logger.warning(f"Failed to query OTel spans: {err_str[:200]}")
        return []


def _get_agent_spans(spans: list[dict]) -> list[dict]:
    """Filter spans to only 'agent' level spans (root execution spans)."""
    return [s for s in spans if s.get("name") == "agent"]


# ── Hermes invocation ──────────────────────────────────────────────────────

def _run_hermes(
    prompt: str,
    hermes_bin: str,
    profile: str,
    timeout: int = 120,
    session_id: Optional[str] = None,
    max_turns: int = 10,
) -> dict:
    """Run a prompt through Hermes Agent chat -q.

    Returns dict with keys: response, duration_ms, session_id, error, returncode
    """
    start = time.time()
    try:
        if session_id:
            cmd = [
                hermes_bin, "-p", profile, "chat",
                "--resume", session_id, "-q", prompt,
                "--max-turns", str(max_turns),
            ]
        else:
            cmd = [
                hermes_bin, "-p", profile, "chat",
                "-q", prompt,
                "--max-turns", str(max_turns),
            ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        elapsed = (time.time() - start) * 1000

        combined = result.stdout + result.stderr
        sid = _extract_session_id(combined)

        return {
            "response": result.stdout,
            "duration_ms": round(elapsed, 1),
            "session_id": sid,
            "error": None if result.returncode == 0 else (result.stderr.strip() or "non_zero_exit"),
            "returncode": result.returncode,
        }

    except subprocess.TimeoutExpired:
        return {
            "response": "",
            "duration_ms": timeout * 1000,
            "session_id": None,
            "error": f"TIMEOUT after {timeout}s",
            "returncode": -1,
        }
    except FileNotFoundError:
        return {
            "response": "",
            "duration_ms": 0,
            "session_id": None,
            "error": f"Hermes CLI not found at '{hermes_bin}'",
            "returncode": -2,
        }
    except Exception as e:
        return {
            "response": "",
            "duration_ms": (time.time() - start) * 1000,
            "session_id": None,
            "error": str(e),
            "returncode": -3,
        }


# ── Feedback helpers ───────────────────────────────────────────────────────

def _strip_hermes_banner(output: str) -> str:
    """Strip Hermes Agent banner/logo, keeping only the actual response text.

    The hermes chat -q output format:
      ╭─ Hermes Agent ───╮  (lines 0-46: banner, logo, tools, skills)
      ╰───────────────────╯
      (empty)
      Query: <prompt>
      Initializing agent...
      ────────────────────────
      (empty)
        ┊ ⚡ tool_call  N.Ns   (tool call notifications)
       ─  ⚕ Hermes  ──────────  (start of response)
                                (response text)
       ────────────────────────  (end of response)
      Resume this session with:...
      Session:  ...
      Duration: ...
      [hermes-otel] ...

    Strategy: Find the 'Query:' line, then extract text between the
    '⚕ Hermes' header line and the end-of-response separator line.
    """
    lines = output.split('\n')

    # Find the Query: line
    query_idx = -1
    for i, line in enumerate(lines):
        if line.strip().startswith('Query:'):
            query_idx = i
            break

    if query_idx < 0:
        # Fallback: find bottom box border and take text after it
        for i, line in enumerate(lines):
            if '╰' in line and '╯' in line:
                leftovers = '\n'.join(lines[i+1:]).strip()
                if leftovers:
                    return leftovers[:500]
        return output[:500]

    # Find the Hermes response header (⚕ Hermes)
    response_start = -1
    for i in range(query_idx, len(lines)):
        if '╕ Hermes' in lines[i] or '⚕ Hermes' in lines[i]:
            response_start = i
            break

    # Alternatively, find the text after the separator line
    # Look for the separator line that follows "Initializing agent..."
    separator_idx = -1
    for i in range(query_idx, len(lines)):
        s = lines[i].strip()
        if s and all(c in s for c in '─') and 'Hermes' not in s and '╭' not in s and '╰' not in s:
            separator_idx = i
            break

    if response_start < 0 and separator_idx < 0:
        return output[output.find('Query:'):][:500]

    # Collect content lines between response header and end-of-response separator
    start_idx = max(response_start, separator_idx)
    content = []
    in_response = False
    for i in range(start_idx + 1, len(lines)):
        s = lines[i].strip()
        # Skip tool call notifications
        if s.startswith('┊ ⚡') or s.startswith('┊'):
            continue
        # End-of-response separator (all dashes or box-drawing line)
        if s and len(s.strip('─ ')) == 0 and len(s) > 3:
            break
        # Session footer markers
        if s.startswith(('Resume this session', 'Session:', 'Duration:', '[hermes-otel]', 'Messages:')):
            break
        # Empty line before/after response
        if not s:
            continue
        content.append(s)

    result = '\n'.join(content).strip()
    return result if result else output[output.find('Query:'):][:500]


def _make_feedback(obj_scores: dict, span_attrs: dict, duration_ms: float, session_id: str = None) -> str:
    """Build actionable feedback text for the reflection_lm."""
    status = span_attrs.get('hermes.turn.final_status', 'unknown')
    api_calls = span_attrs.get('hermes.turn.api_call_count', 0)
    err = span_attrs.get('hermes.turn.tool_outcomes', '')
    parts = [
        f"Score: {obj_scores['composite']:.3f}.",
        f"Status: {status}.",
        f"API calls: {api_calls}.",
        f"Duration: {duration_ms:.0f}ms.",
    ]
    if err and err != 'completed':
        parts.append(f"Outcomes: {err}.")
    s = obj_scores
    if s['pass'] < 0.5:
        parts.append("PROBLEM: Prompt did not complete — tool may have hung or returned error. Add timeout handling or expected-error documentation.")
    elif s['efficiency'] < 0.5:
        parts.append("PROBLEM: Prompt took too long. Improve specificity to reduce LLM decision time. Add precondition checks.")
    elif s['tool_efficiency'] < 0.5:
        parts.append(f"PROBLEM: Too many tool calls ({api_calls}). Make prompt more directive to reduce retry loops. Add --max-turns guidance.")
    else:
        parts.append("OK: Completed efficiently. Minor refinements possible for edge cases.")
    if session_id:
        parts.append(f"Session: {session_id}.")
    return ' '.join(parts)


# ── Hermes LanguageModel factory (P1.6) ─────────────────────────────────────

def make_hermes_lm(
    hermes_bin: str = DEFAULT_HERMES_BIN,
    profile: str = DEFAULT_PROFILE,
    max_turns: int = 1,
    timeout: int = 60,
    model: str | None = None,
) -> callable:
    """Create a GEPA-compatible LanguageModel callable that uses hermes CLI.

    GEPA's `make_litellm_lm()` can't route to kilo-proxy (proxy config is
    embedded in the hermes binary). This factory produces a LanguageModel
    protocol-compatible callable wrapping `hermes chat -q`.

    Usage:
        adapter = OTelPromptAdapter(...)
        hermes_lm = make_hermes_lm()
        result = gepa.optimize(
            adapter=adapter,
            reflection_lm=hermes_lm,
            max_metric_calls=10,  # MINIMUM 8 for OTel adapter (2 init + 2 valset + 2 reflection + buffer)
            ...
        )

    IMPORTANT: The OTel adapter requires a higher max_metric_calls budget than
    the heuristic adapter because each evaluate() call takes ~15s (hermes CLI
    invocation + OTel DB query). With trainset size 2 and valset size 2:
    -  4 calls consumed by initial eval + valset
    -  2 calls for reflection + re-evolution
    -  2+ calls buffer for GEPA internal accounting
    Use max_metric_calls >= 10 for reliability. Below 8, GEPA exhausts budget
    before calling reflection_lm, causing 0% mutation rate.
    """

    def _hermes_lm(prompt: str | list[dict]) -> str:
        """Call hermes CLI and return stripped response text."""
        if isinstance(prompt, list):
            prompt_text = json.dumps(prompt)
        else:
            prompt_text = prompt

        cmd = [hermes_bin, "-p", profile, "chat", "-q", prompt_text,
               "--max-turns", str(max_turns)]
        if model:
            cmd.extend(["--model", model])
        # Pass through TINKER_API_KEY for K2.6 Tinker model routing
        env = os.environ.copy()
        if "TINKER_API_KEY" not in env:
            tinker_key = os.environ.get("TINKER_API_KEY")
            if tinker_key:
                env["TINKER_API_KEY"] = tinker_key

        # ── Cost & Performance Tracking ─────────────────────────────────
        _start = time.time()
        _prompt_chars = len(prompt_text)
        _model_id = model or "default"
        _cost_entry = {}
        _success = False
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
            _elapsed = time.time() - _start
            _response_chars = len(r.stdout) + len(r.stderr)
            _success = True
        except subprocess.TimeoutExpired:
            _elapsed = time.time() - _start
            _response_chars = 0
            raise
        finally:
            # Always log cost metrics, even on timeout
            _prompt_tokens = max(1, _prompt_chars // 4)
            _response_tokens = max(1, _response_chars // 4)
            _cost_entry = {
                "metric": "reflection_lm_cost",
                "model": _model_id,
                "latency_s": round(_elapsed, 1),
                "success": _success,
                "prompt_chars": _prompt_chars,
                "response_chars": _response_chars,
                "est_prompt_tokens": _prompt_tokens,
                "est_response_tokens": _response_tokens,
                "est_total_tokens": _prompt_tokens + _response_tokens,
                "_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            if _success:
                if "deepseek" in _model_id.lower() or _model_id == "default":
                    # Default model is deepseek-v4-flash via kilo-proxy
                    _cost_entry["est_cost_usd"] = round(
                        _prompt_tokens / 1_000_000 * 0.14 + _response_tokens / 1_000_000 * 0.42, 6
                    )
                elif "kimi" in _model_id.lower() or "k2" in _model_id.lower():
                    _cost_entry["est_cost_usd"] = round(
                        _prompt_tokens / 1_000_000 * 0.74 + _response_tokens / 1_000_000 * 1.48, 6
                    )
                else:
                    _cost_entry["est_cost_usd"] = 0.0
            _cost_log = Path("/Users/kieranlal/.hermes/cost-tracker.jsonl")
            _cost_log.parent.mkdir(parents=True, exist_ok=True)
            with open(_cost_log, "a") as _f:
                _f.write(json.dumps(_cost_entry) + "\n")

        # Strip hermes wrapper from response
        raw = r.stdout + r.stderr
        lines = raw.split('\n')
        content = []
        in_resp = False
        for line in lines:
            s = line.strip()
            if '─' in s and len(s.strip('─ ')) >= 3 and '╭' not in s and '╰' not in s:
                if not in_resp:
                    in_resp = True
                    continue
                else:
                    break
            if in_resp and s and not s.startswith(('┊', '⚡', 'Resume', 'Session:',
                                                     'Duration:', '[hermes-otel]', 'Messages:')):
                content.append(s)

        return '\n'.join(content).strip()

    return _hermes_lm


# ── OTelPromptAdapter ──────────────────────────────────────────────────────

# LRU cache for repeated evaluations of identical prompt text (P1.3)
_evaluation_cache: dict[str, dict] = {}
_EVAL_CACHE_MAXSIZE = 128


class OTelPromptAdapter:
    """GEPA adapter that evaluates prompts by running them through Hermes Agent
    and querying OTel spans from PostgreSQL for performance metrics.

    Scoring dimensions:
        - pass: 1.0 if status=completed else 0.0 (50% weight in composite)
        - efficiency: clip(1.0 - duration_ms/30000, 0, 1) (20%)
        - tool_efficiency: 1.0 / max(api_call_count, 1) (20%)
        - token_efficiency: clip(1.0 - total_tokens/100000, 0, 1) (10%)
        - composite: weighted sum of above

    GEPA checks hasattr(self.adapter, 'propose_new_texts') —
    setting to None lets it fall through to the default proposer.
    """

    propose_new_texts = None

    def __init__(
        self,
        hermes_bin: Optional[str] = None,
        profile: Optional[str] = None,
        db_config: Optional[dict] = None,
        dimension_names: Optional[list[str]] = None,
        hermes_timeout: int = 120,
        max_turns: int = 10,
        cleanup_prompt: Optional[str] = None,
    ):
        self.hermes_bin = hermes_bin or DEFAULT_HERMES_BIN
        self.profile = profile or DEFAULT_PROFILE
        self.db_config = db_config or dict(DEFAULT_DB_CONFIG)
        self.dimension_names = dimension_names or list(DEFAULT_DIMENSIONS)
        self.hermes_timeout = hermes_timeout
        self.max_turns = max_turns
        self.cleanup_prompt = cleanup_prompt or CLEANUP_PROMPT

    def check_telemetry_pipeline(self) -> dict:
        """Pre-flight check: verify OTel database and otel_spans table exist.

        Must be called BEFORE any evaluate() call. Returns dict with status
        and details. Raise on critical failure.

        Returns:
            dict with keys:
                - healthy (bool): True if pipeline is fully operational
                - db_reachable (bool): PostgreSQL connection succeeded
                - table_exists (bool): otel_spans table exists
                - span_count (int): current number of spans in table
                - error (str): error message if any check failed
        """
        result = {
            "healthy": False,
            "db_reachable": False,
            "table_exists": False,
            "span_count": 0,
            "error": "",
        }
        try:
            import pg8000
            conn = pg8000.connect(
                host=self.db_config["host"],
                port=self.db_config["port"],
                user=self.db_config["user"],
                database=self.db_config["database"],
            )
            result["db_reachable"] = True
            cur = conn.cursor()

            # Check otel_spans table
            cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = 'otel_spans')"
            )
            result["table_exists"] = cur.fetchone()[0]

            if result["table_exists"]:
                cur.execute("SELECT COUNT(*) FROM otel_spans")
                result["span_count"] = cur.fetchone()[0]

            cur.close()
            conn.close()

            result["healthy"] = result["db_reachable"] and result["table_exists"]
            if not result["healthy"]:
                if not result["table_exists"]:
                    result["error"] = (
                        "otel_spans table does not exist in database "
                        f"'{self.db_config['database']}'. Run the schema migration."
                    )
                elif not result["db_reachable"]:
                    result["error"] = (
                        f"Cannot reach PostgreSQL at "
                        f"{self.db_config['host']}:{self.db_config['port']}"
                    )
        except Exception as e:
            result["error"] = str(e)

        return result

    def evaluate(
        self,
        batch: list[Any],
        candidate: dict[str, str],
        capture_traces: bool = False,
        cleanup: bool = False,
        run_suffix: str = "",
    ) -> tuple:
        """Evaluate candidate prompts on the batch using OTel metrics.

        For each batch item:
        1. Run the candidate prompt through Hermes Agent chat -q
        2. Extract session ID from hermes output
        3. Query OTel spans from PostgreSQL by session ID
        4. Compute multi-dimensional scores from OTel data

        Returns (objective_scores, scores, trajectories) matching GEPA's
        expected format.

        Parameters
        ----------
        batch: list of data instances (each is a dict with 'input' and 'answer')
        candidate: dict mapping component name -> prompt text
        capture_traces: when True, populate trajectories for reflection
        cleanup: when True, send cleanup prompt after evaluation

        Returns
        -------
        tuple: (objective_scores, scores, trajectories)
            - objective_scores: list[dict[str, float]] — per-example dimension scores
            - scores: list[float] — per-example composite scores
            - trajectories: list[dict] or None — per-example traces
        """
        prompt_text = next(iter(candidate.values()))

        # Parameterize resource names with run-specific suffix to prevent
        # state contamination between evaluations in the same session.
        prompt_text = _parameterize_names(prompt_text, run_suffix)

        # P1.3: Cache check — return cached result if same prompt text evaluated
        # within the same adapter lifecycle.
        # The cache stores per-item scores, so it works regardless of batch size.
        # GEPA evaluates with different batch sizes (1 for subsample, 3+ for valset)
        # and the cache adapts by repeating the cached per-item score N times.
        _cache_key = hash(prompt_text)
        if not capture_traces and _cache_key in _evaluation_cache:
            cached = _evaluation_cache[_cache_key]
            n = len(batch)
            from gepa.core.adapter import EvaluationBatch
            return EvaluationBatch(
                outputs=[cached["scores"]] * n,
                scores=[cached["scores"]] * n,
                trajectories=None,
                objective_scores=([cached["objective_scores"]] * n
                                  if cached.get("objective_scores") else None),
            )

        objective_scores_list: list[dict[str, float]] = []
        scores_list: list[float] = []
        trajectories_list: Optional[list[dict]] = [] if capture_traces else None

        # P2.1: Run prompt validator — fast-fail on hallucinated tools
        validation = _run_prompt_validator(prompt_text)
        has_hallucinated_tools = any("UNKNOWN_TOOL" in e for e in validation.get("errors", []))
        if has_hallucinated_tools:
            for _ in batch:
                obj = {"composite": 0.0, "pass": 0.0, "efficiency": 0.0,
                       "tool_efficiency": 0.0, "token_efficiency": 0.0}
                objective_scores_list.append(obj)
                scores_list.append(0.0)
            if trajectories_list is not None:
                for data_inst in batch:
                    trajectories_list.append({
                        "data": data_inst,
                        "full_assistant_response": "",
                        "feedback": f"VALIDATION FAILED: {validation['summary']}",
                    })
            # Cache zero result and return immediately
            from gepa.core.adapter import EvaluationBatch
            _evaluation_cache[_cache_key] = {"objective_scores": obj, "scores": 0.0}
            return EvaluationBatch(
                outputs=scores_list, scores=scores_list,
                trajectories=trajectories_list or None,
                objective_scores=objective_scores_list,
            )

        for i, data_inst in enumerate(batch):
            # Run the prompt through Hermes
            hermes_result = _run_hermes(
                prompt_text,
                hermes_bin=self.hermes_bin,
                profile=self.profile,
                timeout=self.hermes_timeout,
                max_turns=self.max_turns,
            )

            response_text = hermes_result.get("response", "")
            duration_ms = hermes_result.get("duration_ms", 0)
            session_id = hermes_result.get("session_id")
            error = hermes_result.get("error")

            # If something went wrong, return zero scores for this example
            if error or not session_id:
                obj_scores = {
                    "pass": 0.0,
                    "efficiency": 0.0,
                    "tool_efficiency": 0.0,
                    "token_efficiency": 0.0,
                    "composite": 0.0,
                }
                objective_scores_list.append(obj_scores)
                scores_list.append(0.0)

                if trajectories_list is not None:
                    trajectories_list.append({
                        "data": data_inst,
                        "full_assistant_response": _strip_hermes_banner(response_text)[:500],
                        "feedback": _make_feedback(obj_scores, {}, duration_ms, session_id or "?"),
                    })
                continue

            # Query OTel spans for this session
            spans = _query_otel_spans(session_id, self.db_config)

            if not spans:
                # No spans found — use minimal info from hermes result
                obj_scores = _compute_scores({}, duration_ms)
                objective_scores_list.append(obj_scores)
                scores_list.append(obj_scores["composite"])

                if trajectories_list is not None:
                    trajectories_list.append({
                        "data": data_inst,
                        "full_assistant_response": _strip_hermes_banner(response_text)[:500],
                        "feedback": _make_feedback(obj_scores, {}, duration_ms, session_id),
                    })
                continue

            # Use the 'agent' span for aggregated metrics, or first span as fallback
            agent_spans = _get_agent_spans(spans)
            primary_span = agent_spans[0] if agent_spans else spans[0]
            span_attrs = primary_span.get("attributes", {}) or {}

            # Use span duration if available
            span_duration = primary_span.get("duration_ms", 0) or 0
            if span_duration > 0:
                duration_ms = span_duration

            # ── Tier 3: Query compose-pkl infrastructure spans ──────────────
            # Use the span's start_time as session reference for time-window
            # correlation with pod.realize and container.* spans.
            infra_spans = None
            sentinel_ok = True
            session_start = primary_span.get("start_time")
            if session_start:
                try:
                    infra_result = _query_infrastructure_spans(
                        str(session_start), window_minutes=3
                    )
                    infra_spans = infra_result.get("spans", [])
                    sentinel_ok = infra_result.get("sentinel_present", False)
                except Exception:
                    logger.warning("Infrastructure span query failed", exc_info=True)
                    infra_spans = None

            # If sentinel is missing within the time window, data quality is poor.
            # Apply a confidence discount rather than rejecting entirely.
            confidence_discount = 0.85 if not sentinel_ok else 1.0

            # Compute scores from span attributes and infrastructure evidence
            obj_scores = _compute_scores(span_attrs, duration_ms, infra_spans)
            # Apply sentinel confidence discount to pass score
            if confidence_discount < 1.0 and "pass" in obj_scores:
                obj_scores["pass"] = round(obj_scores["pass"] * confidence_discount, 4)
                obj_scores["composite"] = _score_composite(obj_scores)
            objective_scores_list.append(obj_scores)
            scores_list.append(obj_scores["composite"])

            if trajectories_list is not None:
                fb = _make_feedback(obj_scores, span_attrs, duration_ms, session_id)
                if validation.get("tool_names_found") and validation.get("score", 1.0) < 1.0:
                    fb += f" TOOL_ERRORS: {validation['summary']}"
                trajectories_list.append({
                    "data": data_inst,
                    "full_assistant_response": _strip_hermes_banner(response_text)[:500],
                    "feedback": fb,
                })

        # Run cleanup if requested (after evaluation)
        if cleanup:
            self._run_cleanup()

        # P1.3: Cache the result for repeat evaluations of the same prompt.
        # Store only the first item's score (batch-size independent) so the
        # cache works when GEPA evaluates with different batch sizes.
        if not capture_traces:
            first_score = scores_list[0] if scores_list else 0.0
            first_obj = objective_scores_list[0] if objective_scores_list else {}
            _evaluation_cache[_cache_key] = {
                "objective_scores": first_obj,
                "scores": first_score,
            }
            # Evict oldest if over maxsize
            if len(_evaluation_cache) > _EVAL_CACHE_MAXSIZE:
                oldest = next(iter(_evaluation_cache))
                del _evaluation_cache[oldest]

        # GEPA 0.1.1 expects EvaluationBatch object (not tuple)
        from gepa.core.adapter import EvaluationBatch
        return EvaluationBatch(
            outputs=scores_list,
            scores=scores_list,
            trajectories=trajectories_list if trajectories_list else None,
            objective_scores=objective_scores_list if objective_scores_list else None,
        )

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: Any,  # EvaluationBatch from gepa.core.adapter
        components_to_update: list[str],
    ) -> dict:
        """Build concise feedback for the reflection_lm to propose improvements.

        Uses trajectories from evaluate() to provide per-example feedback.
        Compatible with GEPA's GEPAAdapter protocol.

        Parameters
        ----------
        candidate: dict mapping component name -> prompt text
        eval_batch: EvaluationBatch object from evaluate()
        components_to_update: list of component names to update

        Returns
        -------
        dict: component_name -> list of feedback records
        """
        prompt_text = next(iter(candidate.values()))
        comp = components_to_update[0]

        # Handle both EvaluationBatch and legacy tuple format
        if hasattr(eval_batch, 'trajectories'):
            trajectories = eval_batch.trajectories
            objective_scores = eval_batch.objective_scores or []
        else:
            objective_scores, _, trajectories = eval_batch
        items = []

        if trajectories:
            for traj in trajectories:
                items.append({
                    "Inputs": traj.get("data", {}).get("input", ""),
                    "Generated Outputs": traj.get("full_assistant_response", prompt_text[:200]),
                    "Feedback": traj.get("feedback", "No feedback available."),
                })
        else:
            # Fallback: build from scores
            for i, score in enumerate(scores):
                obj = objective_scores[i] if i < len(objective_scores) else {}
                items.append({
                    "Inputs": f"Prompt #{i}",
                    "Generated Outputs": prompt_text[:200],
                    "Feedback": (
                        f"Score: {score:.3f}. "
                        f"Pass: {obj.get('pass', 0):.1f}, "
                        f"Efficiency: {obj.get('efficiency', 0):.3f}, "
                        f"Tool: {obj.get('tool_efficiency', 0):.3f}, "
                        f"Tokens: {obj.get('token_efficiency', 0):.3f}. "
                        "Target: >0.7 on all dimensions."
                    ),
                })

        return {comp: items}

    def _run_cleanup(self) -> dict:
        """Send cleanup prompt to Hermes after evaluation."""
        return _run_hermes(
            self.cleanup_prompt,
            hermes_bin=self.hermes_bin,
            profile=self.profile,
            timeout=self.hermes_timeout,
            max_turns=self.max_turns,
        )


# ── CLI entry point ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OTelPromptAdapter CLI")
    parser.add_argument("--prompt", type=str, help="Prompt text to evaluate")
    parser.add_argument("--dry-run", action="store_true", help="Validate config without running")
    args = parser.parse_args()

    if args.dry_run:
        print(f"OTelPromptAdapter configured:")
        print(f"  hermes_bin: {DEFAULT_HERMES_BIN}")
        print(f"  profile: {DEFAULT_PROFILE}")
        print(f"  db_config: {DEFAULT_DB_CONFIG}")
        print("  dimensions: pass, efficiency, tool_efficiency, token_efficiency, composite")
        print("Dry-run: PASS")
        sys.exit(0)

    if args.prompt:
        adapter = OTelPromptAdapter()
        batch = [{"input": "eval", "answer": "pass"}]
        candidate = {"prompt": args.prompt}
        obj_scores, scores, _ = adapter.evaluate(batch, candidate)
        print(f"Scores: {scores}")
        print(f"Objective: {obj_scores}")
