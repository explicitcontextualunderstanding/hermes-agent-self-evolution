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

import json
import logging
import re
import subprocess
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


# ── Scoring functions ──────────────────────────────────────────────────────

def _score_pass(span_attrs: dict) -> float:
    """Pass/fail: 1.0 if final_status == 'completed' else 0.0."""
    status = span_attrs.get("hermes.turn.final_status", "")
    return 1.0 if status == "completed" else 0.0


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


def _compute_scores(span_attrs: dict, duration_ms: float = 0.0) -> dict:
    """Compute all objective scores from span attributes."""
    obj = {
        "pass": _score_pass(span_attrs),
        "efficiency": _score_efficiency(span_attrs, duration_ms),
        "tool_efficiency": _score_tool_efficiency(span_attrs),
        "token_efficiency": _score_token_efficiency(span_attrs),
    }
    obj["composite"] = _score_composite(obj)
    return obj


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
    Returns empty list if query fails.
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

    except Exception as e:
        logger.warning(f"Failed to query OTel spans: {e}")
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


# ── OTelPromptAdapter ──────────────────────────────────────────────────────

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

    def evaluate(
        self,
        batch: list[Any],
        candidate: dict[str, str],
        capture_traces: bool = False,
        cleanup: bool = False,
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

        objective_scores_list: list[dict[str, float]] = []
        scores_list: list[float] = []
        trajectories_list: Optional[list[dict]] = [] if capture_traces else None

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

            # Compute scores from span attributes
            obj_scores = _compute_scores(span_attrs, duration_ms)
            objective_scores_list.append(obj_scores)
            scores_list.append(obj_scores["composite"])

            if trajectories_list is not None:
                trajectories_list.append({
                    "data": data_inst,
                    "full_assistant_response": _strip_hermes_banner(response_text)[:500],
                    "feedback": _make_feedback(obj_scores, span_attrs, duration_ms, session_id),
                })

        # Run cleanup if requested (after evaluation)
        if cleanup:
            self._run_cleanup()

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
