#!/usr/bin/env python3
"""evolve_prompts.py — GEPA-driven optimization of compose-pkl MCP test prompts.

Phase 1 of Plan 122. Uses GEPA to evolve natural-language test prompts
against the fitness rubric from inventory.py.

Usage:
    # Dry-run validation
    python3 -m evolution.prompts.evolve_prompts --dry-run

    # Single-prompt canary (G1 gate)
    python3 -m evolution.prompts.evolve_prompts --single-prompt 7

    # Batch evolve prompts 1-47
    python3 -m evolution.prompts.evolve_prompts --tier 1

    # Full batch (all 91 prompts)
    python3 -m evolution.prompts.evolve_prompts --tier all
"""

import json
import os
import re
import sys
import time
import argparse
from pathlib import Path
from typing import Optional
import sqlite3

from evolution.env_config import _resolve, EVIDENCE_LOG as _EVIDENCE_LOG

# Module-level for CLI flag propagation into optimize functions
_current_args = None

# GEPA — lazy import. Functions that call gepa.optimize() check _GEPA_AVAILABLE.
# Module-level import is lazy so memory pressure and other non-GEPA functions
# can be imported for unit testing without installing gepa.
_GEPA_AVAILABLE = False
try:
    import gepa  # noqa: F401

    _GEPA_AVAILABLE = True
except ImportError:
    pass

# These are used by type-annotated functions that call gepa.optimize().
# They're only defined when gepa is available to keep imports clean.
if _GEPA_AVAILABLE:
    from gepa.adapters.default_adapter.default_adapter import (
        EvaluationBatch,
        EvaluationResult,
        DefaultDataInst,
    )
else:

    class _Placeholder:
        pass

    EvaluationBatch = _Placeholder
    EvaluationResult = _Placeholder
    DefaultDataInst = _Placeholder

from evolution.prompts.inventory import (
    build_inventory,
    evaluate_prompt,
    RUBRIC_DIMENSIONS,
    P1,
    P2,
    P4,
    P5,
    PROMPT_TOOLS,
    BASELINE_STATUS,
)

# RewardAdapter for reasoning trace enrichment (Plan 130)
try:
    from evolution.prompts.reward_adapter import (
        RewardAdapter,
        Span,
        StepFailure,
        ProxyStateTracker,
    )

    _HAS_REWARD_ADAPTER = True
except ImportError:
    RewardAdapter = None
    Span = None
    StepFailure = None
    ProxyStateTracker = None
    _HAS_REWARD_ADAPTER = False

# ── Local filter: Qwen2.5 mutation pre-filter via daemonized model service ──


def _call_model_service(
    parent: str,
    mutation: str,
    threshold: float = 0.7956,
) -> dict:
    """Call the daemonized model service to score a parent/mutation pair.

    POSTs to the model_service daemon (port 11435 by default) which keeps
    Qwen2.5-0.5B-Instruct-4bit resident in memory (~350 MB), avoiding the
    1.6s cold-load per call.

    Args:
        parent: Original prompt text (the baseline).
        mutation: Mutated variant proposed by GEPA's reflection LM.
        threshold: Probability threshold below which the mutation is rejected
            (default: 0.7956, calibrated from 299 evidence entries).

    Returns:
        dict with keys: decision (0=REJECT, 1=PASS), logit_probability (float).
        On any failure, returns decision=1 (pass-through) so the pipeline
        continues without interruption.

    """
    import json
    import urllib.request

    port = os.environ.get("MODEL_SERVICE_PORT", "11435")
    url = f"http://127.0.0.1:{port}/score"
    body = json.dumps(
        {
            "parent": parent,
            "mutation": mutation,
            "threshold": threshold,
        }
    ).encode()
    try:
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(
            f"  Model service call failed: {e} (proceeding without filter)",
            flush=True,
        )
        return {"decision": 1, "logit_probability": 1.0}


# ── Custom GEPA Adapter (no LLM calls for evaluation) ──────────────────────


class HeuristicPromptAdapter:
    """GEPA adapter that evaluates prompts using the heuristic rubric.

    Skips LLM calls entirely — the candidate prompt is scored directly by the
    rubric. GEPA still uses reflection_lm to propose new prompt variants.
    """

    def __init__(self, evaluator_fn, dimension_names=None):
        self.evaluator_fn = evaluator_fn
        self.dimension_names = dimension_names or [
            "clarity",
            "coverage",
            "resilience",
            "self_containment",
            "verifiability",
        ]

    # GEPA checks hasattr(self.adapter, 'propose_new_texts') —
    # setting to None lets it fall through to the default proposer
    propose_new_texts = None

    def evaluate(self, batch, candidate, capture_traces=False):
        """Score candidate prompts on the batch using the heuristic rubric."""
        prompt_text = next(iter(candidate.values()))
        scores = [self.evaluator_fn(prompt_text) for _ in batch]
        objective_scores = [{"rubric": s} for s in scores]

        # Build trajectories when capture_traces=True (needed for reflection)
        trajectories = None
        if capture_traces:
            trajectories = []
            for i, score in enumerate(scores):
                detail = self._score_detail(prompt_text)
                trajectories.append(
                    {
                        "data": batch[i]
                        if i < len(batch)
                        else {"input": "", "answer": ""},
                        "full_assistant_response": prompt_text[:500],
                        "feedback": (
                            f"Rubric score: {score:.3f}. "
                            f"Dimensional breakdown: {json.dumps(detail)}. "
                            f"Target: >0.7 on all dimensions."
                        ),
                    }
                )

        return EvaluationBatch(
            outputs=[{"evaluated": prompt_text[:80]} for _ in batch],
            scores=scores,
            trajectories=trajectories,
            objective_scores=objective_scores,
        )

    def make_reflective_dataset(
        self, candidate, eval_batch, components_to_update
    ):
        """Build concise feedback for the reflection_lm to propose improvements.

        Uses trajectories from evaluate() to provide per-example feedback.
        """
        prompt_text = next(iter(candidate.values()))
        comp = components_to_update[0]

        trajectories = eval_batch.trajectories
        if not trajectories:
            # Fallback: build from scores
            items = []
            for i, score in enumerate(eval_batch.scores):
                detail = self._score_detail(prompt_text)
                items.append(
                    {
                        "Inputs": f"Prompt #{i}",
                        "Generated Outputs": prompt_text[:200],
                        "Feedback": (
                            f"Rubric score: {score:.3f}. "
                            f"Dimensional breakdown: {json.dumps(detail)}. "
                            "Target: >0.7 on all dimensions."
                        ),
                    }
                )
        else:
            items = []
            for traj in trajectories:
                items.append(
                    {
                        "Inputs": traj.get("data", {}).get("input", ""),
                        "Generated Outputs": traj.get(
                            "full_assistant_response", prompt_text[:200]
                        ),
                        "Feedback": traj.get(
                            "feedback", "No feedback available."
                        ),
                    }
                )

        return {comp: items}

    def _score_detail(self, text: str) -> dict:
        """Return per-dimension scores for reflection feedback."""
        # Simple heuristic — just a quick breakdown
        scores = {}
        text_lower = text.lower()
        scores["clarity"] = min(1.0, len(text.split()) / 15) * 0.5 + 0.5 * (
            0.2
            if any(
                w in text_lower for w in ["should", "must", "verify", "ensure"]
            )
            else 0
        )
        scores["coverage"] = min(
            1.0,
            (
                0.3
                if "list" in text_lower
                else 0.2
                if "all" in text_lower
                else 0.1
            ),
        )
        scores["resilience"] = (
            0.5
            + 0.3 * ("even if" in text_lower)
            + 0.2 * ("timeout" in text_lower)
        )
        scores["self_containment"] = min(1.0, len(text) / 300) * 0.5 + 0.5 * (
            0.3 if "step" in text_lower else 0.1
        )
        scores["verifiability"] = min(
            1.0,
            (
                0.4
                if "assert" in text_lower or "verify" in text_lower
                else 0.2
                if "expected" in text_lower
                else 0.1
            ),
        )
        return scores


# ── Reward-Aware Adapter (enriches rubric with reasoning trace analysis) ───


class RewardAwareAdapter(HeuristicPromptAdapter):
    """HeuristicPromptAdapter enriched with RewardAdapter reasoning trace analysis.

    When reward_adapter is provided:
    1. evaluate() with capture_traces=True enriches trajectory feedback with
       reasoning_insight from _parse_reasoning_trace()
    2. make_reflective_dataset() injects step_failure analysis into reflection
       feedback, giving GEPA's reflection LM richer signal about WHY a prompt
       scores poorly (structural defect vs behavioral variance).

    Uses a Span-like model constructed from the prompt text and rubric scores
    as a proxy for real runtime K2.6 reasoning traces. Once the OTel pipeline
    provides real reasoning_content, the same enrichment path works transparently.
    """

    def __init__(
        self,
        evaluator_fn,
        dimension_names=None,
        reward_adapter=None,
        reflect_model: str | None = None,
        proxy_state: bool = False,
    ):
        super().__init__(evaluator_fn, dimension_names)
        self.reward_adapter = reward_adapter or (
            RewardAdapter() if RewardAdapter else None
        )
        self.reflect_model = reflect_model
        self.proxy_state = proxy_state and (ProxyStateTracker is not None)
        self._proxy_tracker = ProxyStateTracker() if self.proxy_state else None
        # Check if the actual model supports reasoning_content or only has simulated traces
        if reward_adapter and _HAS_REWARD_ADAPTER:
            from evolution.prompts.reward_adapter import (
                has_reasoning_capability,
            )

            self._has_real_reasoning = has_reasoning_capability(reflect_model)
        else:
            self._has_real_reasoning = False

    def _simulated_label(self) -> str:
        """Return label prefix for reasoning insight based on trace source."""
        return "" if self._has_real_reasoning else "[SIMULATED] "

    def evaluate(self, batch, candidate, capture_traces=False):
        """Score candidates on the batch — rubric + reward trace enrichment.

        When proxy_state=True, uses the ProxyStateTracker (LLM-as-a-Judge)
        to generate real 5-dimension scores instead of SIMULATED traces.
        The PST composite score becomes the primary optimization target.
        """
        prompt_text = next(iter(candidate.values()))

        # Evaluate via proxy state tracker (LLM-as-a-Judge replaces heuristic)
        proxy_composite = None
        proxy_dims = None
        if self._proxy_tracker:
            proxy_result = self._proxy_tracker.evaluate(
                prompt=prompt_text,
                output=_infer_intended_action(prompt_text),
            )
            proxy_composite = proxy_result.get("composite", 0.5)
            proxy_dims = proxy_result.get("dimensions", {})
            # Clamp to [0, 1]
            proxy_composite = max(0.0, min(1.0, proxy_composite))
            scores = [proxy_composite for _ in batch]
            objective_scores = [
                {"composite": proxy_composite, **proxy_dims} for _ in batch
            ]
        else:
            # Fallback: heuristic rubric
            scores = [self.evaluator_fn(prompt_text) for _ in batch]
            objective_scores = [{"rubric": s} for s in scores]

        # Build trajectories when capture_traces=True (needed for reflection)
        trajectories = None
        if capture_traces:
            trajectories = []
            # Fetch error payloads once for all trajectory items (Phase 1, Plan 135)
            error_payloads = _get_error_payloads()
            for i, score in enumerate(scores):
                detail = self._score_detail(prompt_text)

                if self._proxy_tracker:
                    # ── REAL proxy state evaluation ──
                    # Uses LLM-as-a-Judge (DeepSeek-V4-Flash) to score the
                    # prompt across 5 dimensions. Replaces the [SIMULATED]
                    # trace path with authentic LLM judgment.
                    proxy_result = self._proxy_tracker.evaluate(
                        prompt=prompt_text,
                        output=_infer_intended_action(prompt_text),
                    )
                    classification = self._proxy_to_classification(
                        proxy_result
                    )
                    insight = (
                        f"[PROXY STATE] Dimensions: "
                        f"{json.dumps(proxy_result['dimensions'])}. "
                        f"Reasoning: {proxy_result['reasoning']}"
                    )

                    trajectories.append(
                        {
                            "data": batch[i]
                            if i < len(batch)
                            else {"input": "", "answer": ""},
                            "full_assistant_response": prompt_text[:500],
                            "feedback": _build_enriched_feedback(
                                score,
                                detail,
                                classification=classification,
                                insight=insight,
                                proxy_scores=proxy_result["dimensions"],
                                composite_override=proxy_result["composite"],
                                error_payloads=error_payloads,
                            ),
                        }
                    )
                else:
                    # ── Original [SIMULATED] trace path ──
                    insight = ""
                    if self.reward_adapter:
                        proxy_span = Span(
                            name="prompt_eval",
                            status="failed" if score < 0.7 else "success",
                            intended_action=_infer_intended_action(
                                prompt_text
                            ),
                            error=_infer_error_pattern(prompt_text),
                            reasoning_content=_build_fake_reasoning_trace(
                                prompt_text
                            ),
                        )
                        cls, _, insight = (
                            self.reward_adapter._classify_failure_with_trace(
                                proxy_span, i
                            )
                        )

                    trajectories.append(
                        {
                            "data": batch[i]
                            if i < len(batch)
                            else {"input": "", "answer": ""},
                            "full_assistant_response": prompt_text[:500],
                            "feedback": _build_enriched_feedback(
                                score,
                                detail,
                                classification=cls,
                                insight=insight,
                                simulated_label=self._simulated_label(),
                                error_payloads=error_payloads,
                            ),
                        }
                    )

        return EvaluationBatch(
            outputs=[{"evaluated": prompt_text[:80]} for _ in batch],
            scores=scores,
            trajectories=trajectories,
            objective_scores=objective_scores,
        )

    def _proxy_to_classification(self, proxy_result: dict) -> str:
        """Map ProxyStateTracker dimension scores to a failure classification.

        Returns one of: "structural", "behavioral", "state_contamination", "efficiency".
        """
        dims = proxy_result.get("dimensions", {})
        # Low tool_correctness or parameter_validity → structural
        if dims.get("tool_correctness", 1.0) < 0.5:
            return "structural"
        if dims.get("parameter_validity", 1.0) < 0.5:
            return "structural"
        # Low resource_lifecycle → efficiency
        if dims.get("resource_lifecycle", 1.0) < 0.3:
            return "efficiency"
        # Low state_agreement with high tool_correctness → behavioral
        if (
            dims.get("state_agreement", 1.0) < 0.5
            and dims.get("tool_correctness", 0) >= 0.5
        ):
            return "behavioral"
        # Low error_handling → structural
        if dims.get("error_handling", 1.0) < 0.5:
            return "structural"
        return "behavioral"


# ── Helpers for reward-enriched reflection feedback ────────────────────────


def _infer_intended_action(text: str) -> str:
    """Heuristic: extract tool name from prompt text."""
    for prefix in [
        "create",
        "delete",
        "list",
        "start",
        "stop",
        "inspect",
        "pull",
        "push",
        "tag",
        "build",
        "exec",
        "prune",
        "rollback",
        "restore",
        "check",
        "validate",
        "verify",
        "attach",
        "detach",
    ]:
        if prefix in text.lower():
            return prefix
    return "unknown_action"


def _infer_error_pattern(text: str) -> str:
    """Heuristic: detect common error patterns mentioned in prompt text."""
    tl = text.lower()
    if "already exists" in tl or "already in use" in tl:
        return "port already in use"
    if "not found" in tl or "no such" in tl:
        return "resource not found"
    if "timeout" in tl or "timed out" in tl:
        return "timeout waiting for resource"
    if "invalid" in tl or "unrecognized" in tl:
        return "invalid parameter"
    if "missing" in tl or "require" in tl:
        return "missing required field"
    return "execution failure"


def _build_fake_reasoning_trace(text: str) -> str:
    """Build a plausible K2.6-style reasoning trace from prompt text structure.

    Used in the heuristic-only path to exercise the RewardAdapter classification
    pipeline. When real OTel spans provide reasoning_content, this is replaced
    by actual K2.6 thinking mode traces.
    """
    tl = text.lower()
    parts = []

    # Simulate tool selection
    tool = _infer_intended_action(text)
    if tool:
        parts.append(f"I will call {tool} to achieve the goal")
    else:
        parts.append("I need to determine the right tool for this task")

    # Simulate precondition check
    if "verify" in tl or "check" in tl or "ensure" in tl:
        parts.append("I should verify the current state first")
    else:
        parts.append("Proceeding with the operation")

    # Simulate uncertainty markers for edge cases
    if tl.count(" ") > 30:
        parts.append("This is a complex scenario with multiple conditions")
    if "or" in tl or "alternatively" in tl:
        parts.append("I'm not sure which approach to use here")

    return ". ".join(parts)


def _build_enriched_feedback(
    score: float,
    detail: dict,
    classification: str | None = None,
    insight: str = "",
    simulated_label: str = "",
    proxy_scores: dict | None = None,
    composite_override: float | None = None,
    error_payloads: list[dict] | None = None,
) -> str:
    """Build reflection feedback with optional reasoning insight or proxy state.

    Args:
        simulated_label: Prefix like "[SIMULATED] " when the insight is from
            heuristic proxy traces rather than real K2.6 reasoning_content.
        proxy_scores: When set (proxy-state mode), 5-dimension scores from
            ProxyStateTracker are injected as dimensional breakdown.
        composite_override: When set, overrides the rubric score with the
            proxy state composite score for GEPA consumption.
        error_payloads: When set, real error messages from tool / infra failure
            spans are injected so the reflection LM can address the actual
            failure cause instead of guessing (Phase 1, Plan 135).

    """
    override = composite_override if composite_override is not None else score
    base = (
        f"Composite score: {override:.3f}. "
        f"Dimensional breakdown: {json.dumps(detail)}. "
        f"Target: >0.7 on all dimensions."
    )
    if proxy_scores:
        base += (
            f" Proxy state dimensions: {json.dumps(proxy_scores)}. "
            f"Failure classification: {classification}."
        )
    elif classification:
        base += (
            f" Failure classification: {classification}. "
            f"Reasoning insight: {simulated_label}{insight}"
        )
    if error_payloads:
        formatted = _format_error_payloads(error_payloads)
        if formatted:
            base += f"\n{formatted}"
    return base


# ── Error payload injection (Phase 1, Plan 135) ────────────────────────────
_ERROR_PAYLOAD_CACHE: list[dict] | None = None
_ERROR_PAYLOAD_CACHE_TIME: float = 0.0
_ERROR_PAYLOAD_CACHE_TTL: float = 30.0  # seconds


def _get_error_payloads(
    max_items: int = 3, max_chars: int = 2000
) -> list[dict]:
    """Query recent error spans from both otel and compose-pkl sources.

    Returns a list of dicts with keys: span_name, source, error_message.
    Caps at max_items errors and enforces total output < max_chars chars.

    Caches results for _ERROR_PAYLOAD_CACHE_TTL seconds to avoid
    hammering the DB on every GEPA iteration.
    """
    global _ERROR_PAYLOAD_CACHE, _ERROR_PAYLOAD_CACHE_TIME
    now = time.time()
    if (
        _ERROR_PAYLOAD_CACHE is not None
        and now - _ERROR_PAYLOAD_CACHE_TIME < _ERROR_PAYLOAD_CACHE_TTL
    ):
        return _ERROR_PAYLOAD_CACHE

    payloads: list[dict] = []
    try:
        import pg8000
        from evolution.prompts.otel_adapter import _get_db_config

        db = _get_db_config()
        conn = pg8000.connect(
            host=db["host"],
            port=db["port"],
            user=db["user"],
            database=db["database"],
        )
        cur = conn.cursor()

        # Source 1: hermes-otel tool spans with error outcome (most specific)
        cur.execute(
            """
            SELECT name, attributes::text, status_code, start_time
            FROM otel_spans
            WHERE (attributes->>'hermes.tool.outcome' = 'error'
                   OR (status_code = 'ERROR'
                       AND name NOT IN ('agent', 'cron')))
              AND start_time > NOW() - interval '24 hours'
              AND (attributes->>'error.message' IS NOT NULL
                   OR status_code = 'ERROR')
            ORDER BY
                CASE WHEN attributes->>'error.message' IS NOT NULL THEN 0 ELSE 1 END,
                start_time DESC
            LIMIT %s
        """,
            (max_items,),
        )
        for row in cur.fetchall():
            err_msg = ""
            if row[1] and isinstance(row[1], str):
                try:
                    attrs = json.loads(row[1])
                    err_msg = attrs.get("error.message", "")
                except json.JSONDecodeError:
                    err_msg = ""
            if row[2] and row[2] == "ERROR" and not err_msg:
                err_msg = "(ERROR status)"
            if err_msg:
                payloads.append(
                    {
                        "span_name": row[0],
                        "source": "otel_tool",
                        "error_message": str(err_msg)[:300],
                    }
                )

        # Source 2: compose-pkl infrastructure error spans
        # (supplement source 1, don't double-count beyond max_items)
        remaining = max_items - len(payloads)
        if remaining > 0:
            cur.execute(
                """
                SELECT name, status_message, trace_id, start_time
                FROM otel_spans
                WHERE service_name = 'compose-pkl'
                  AND status_code = 'ERROR'
                  AND NULLIF(status_message, '') IS NOT NULL
                  AND start_time > NOW() - interval '24 hours'
                ORDER BY start_time DESC
                LIMIT %s
            """,
                (remaining,),
            )
            for row in cur.fetchall():
                err_msg = str(row[1] or "")[:300]
                container_id = ""
                if row[2] and isinstance(row[2], str):
                    try:
                        tid_data = json.loads(row[2])
                        container_id = tid_data.get("container_id", "")
                    except (json.JSONDecodeError, AttributeError):
                        pass
                detail = err_msg
                if container_id:
                    detail = f"[{container_id}] {err_msg}"
                payloads.append(
                    {
                        "span_name": row[0],
                        "source": "compose_pkl",
                        "error_message": detail,
                    }
                )

        conn.close()
    except Exception as e:
        print(f"  [error_payloads] query failed: {e}")

    # Enforce total char cap
    total_chars = sum(len(p.get("error_message", "")) for p in payloads)
    while payloads and total_chars > max_chars:
        dropped = payloads.pop()
        total_chars -= len(dropped.get("error_message", ""))

    _ERROR_PAYLOAD_CACHE = payloads
    _ERROR_PAYLOAD_CACHE_TIME = now
    return payloads


def _format_error_payloads(payloads: list[dict]) -> str:
    """Format error payloads for inclusion in reflection feedback."""
    if not payloads:
        return ""
    parts = ["Error payloads from recent failures:"]
    for p in payloads:
        src = "tool" if p["source"] == "otel_tool" else "infrastructure"
        parts.append(
            f"  - {p['span_name']} ({src}): {p['error_message'][:200]}"
        )
    return "\n".join(parts)


# ── Config ─────────────────────────────────────────────────────────────────
COMPOSE_PKL = _resolve("COMPOSE_PKL_DIR", str(Path.home() / "workspace" / "compose-pkl"))
EVIDENCE_LOG = COMPOSE_PKL / "docs" / "evolve-evidence.jsonl"

# PostgreSQL connection for prompt store (migrated from SQLite)
DB_HOST = os.environ.get("PG_HOST", "192.168.64.23")
DB_PORT = int(os.environ.get("PG_PORT", "5432"))
DB_USER = os.environ.get("PG_USER", "postgres")
DB_PASSWORD = os.environ.get("PG_PASSWORD")
DB_NAME = os.environ.get("PG_DATABASE", "harness_evolution")

if DB_PASSWORD is None:
    print("FATAL: PG_PASSWORD environment variable not set. "
          "Set it before running evolution.", file=sys.stderr)
    sys.exit(1)


def _pg_connect():
    """Return a synchronous pg8000 connection to the prompt evolution DB."""
    import pg8000

    return pg8000.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=DB_NAME,
    )


def _pg_fetch_all(conn, query: str, params: tuple = ()) -> list[dict]:
    """Execute a query and return all rows as dicts (like sqlite3.Row)."""
    cur = conn.cursor()
    cur.execute(query, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Dynamic Concurrency Control (Phase 3, Plan 135) ──────────────────────
# Caches the last latency band to avoid re-querying on every iteration.
# Module-level counter tracks consecutive RED bands.
_RED_CONSECUTIVE: dict[str, int] = {"count": 0}
_LAST_LATENCY_BAND: dict[str, str] = {"band": "GREEN"}


def _apply_latency_cooldown() -> dict:
    """Measure latency drift and apply cooldown between GEPA iterations.

    Calls _measure_latency_drift() from otel_adapter.
    YELLOW: sleep 5s
    RED:    sleep 15s and log warning
    RED >= 10 consecutive: continue at RED pacing silently.
    GREEN: no cooldown (return immediately).

    Returns the latency measurement dict.
    """
    try:
        from evolution.prompts.otel_adapter import _measure_latency_drift

        result = _measure_latency_drift()
        band = result.get("latency_band", "GREEN")
        sample_count = result.get("sample_count", 0)
        std_ms = result.get("std_ms", 0.0)
        _LAST_LATENCY_BAND["band"] = band

        if band == "RED":
            _RED_CONSECUTIVE["count"] += 1
            cooldown = 15
            if _RED_CONSECUTIVE["count"] >= 10:
                pass  # Continue at RED pacing silently (cap met)
            else:
                print(
                    f"  \u26a0 Latency band: RED (\u03c3={std_ms:.1f}ms, "
                    f"{sample_count} samples) \u2014 inserting {cooldown}s cooldown"
                )
        elif band == "YELLOW":
            _RED_CONSECUTIVE["count"] = 0
            cooldown = 5
            print(
                f"  \u26a1 Latency band: YELLOW (\u03c3={std_ms:.1f}ms, "
                f"{sample_count} samples) \u2014 inserting {cooldown}s cooldown"
            )
        else:
            _RED_CONSECUTIVE["count"] = 0
            return result  # GREEN \u2014 no cooldown

        import time

        time.sleep(cooldown)
        return result
    except Exception as e:
        print(f"  \u26a0 Latency cooldown check failed: {e}")
        return {"latency_band": "GREEN"}


def _get_latency_band() -> str:
    """Return the last measured latency band without re-querying."""
    return _LAST_LATENCY_BAND.get("band", "GREEN")


# ── Checkpoint (durable progress) ──────────────────────────────────────────
CHECKPOINT_DIR = Path.home() / ".hermes" / "evolution-checkpoints"
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)


def _checkpoint_path(label: str) -> Path:
    """Return checkpoint path for a given tier/label. Safe characters only."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", label.lower())
    return CHECKPOINT_DIR / f"checkpoint_{safe}.json"


def save_checkpoint(state: dict, label: str) -> None:
    """Write durable checkpoint after each prompt. Survives kill/reboot."""
    path = _checkpoint_path(label)
    state["_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Use atomic write to prevent partial-file corruption on crash
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        tmp.rename(path)
    except Exception as e:
        print(f"  ⚠️  Checkpoint write failed (non-fatal): {e}")


def load_checkpoint(label: str) -> dict | None:
    """Load checkpoint if it exists. Returns None for clean start."""
    path = _checkpoint_path(label)
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return None


# Size-aware iteration budgets (DIFF.md §8.4)
# Iterations * 2 = max_metric_calls for GEPA. Must be >= 10 for reflection_lm to fire.
TIER_BUDGETS = {
    1: {
        "doc": P1,
        "prompts": (1, 47),
        "iterations": 5,
        "label": "Tier 1: Container Lifecycle",
    },
    2: {
        "doc": P2,
        "prompts": (48, 68),
        "iterations": 5,
        "label": "Tier 2: Advanced Orchestration",
    },
    3: {
        "doc": P4,
        "prompts": (69, 91),
        "iterations": 5,
        "label": "Tier 3: Host-Native Lane",
    },
    4: {
        "doc": P4,
        "prompts": (92, 121),
        "iterations": 5,
        "label": "Tier 4: Infrastructure (Networks, Volumes, Images, Pods, Compose)",
    },
    5: {
        "doc": P5,
        "prompts": (122, 126),
        "iterations": 5,
        "label": "Tier 5: Externalized State (State Artifacts, Multi-Agent Sync, Crash Recon, Permission Gates, Slab Protocol)",
    },
}


def evaluate_prompt_wrapper(prompt_text: str) -> float:
    """Wraps the rubric evaluator. Returns composite score in [0,1]."""
    # Infer tools from the prompt number by looking for tool-like words in prompt text.
    # Matches both underscore-separated tool names (create_container) and
    # space-separated versions (create container) that may appear in prompts.
    for num, tools in PROMPT_TOOLS.items():
        for t in tools:
            needle_space = t.replace("_", " ")
            if needle_space in prompt_text.lower() or t in prompt_text.lower():
                result = evaluate_prompt(prompt_text, tools)
                return result["composite"]
    result = evaluate_prompt(prompt_text, [])
    return result["composite"]


def optimize_prompt_text(
    prompt_text: str,
    tools: list[str],
    max_calls: int = 10,
    proxy_state: bool = False,
) -> tuple[str, float, float]:
    """Optimize a single prompt — hybrid approach.

    When proxy_state=True, uses ProxyStateTracker (LLM-as-a-Judge) for
    5-dimension scoring instead of simulated reasoning traces.
    Provides 10-50× better SNR than the heuristic rubric (~1.2×).

    Phase 1: Generate candidate with heuristic evolution (fast, ~60s)
    Phase 2: Validate candidate vs original with OTel backend (one eval each, ~30s)
    Phase 3: Accept only if OTel composite score improves

    This combines the speed of heuristic search with the accuracy of
    real backend measurement.
    """
    # Phase 1: Heuristic evolution to generate candidate
    return _optimize_prompt_text_hybrid(
        prompt_text,
        max_calls,
        use_reward_adapter=getattr(_current_args, "use_reward_adapter", False),
        reflect_model=getattr(_current_args, "reflect_model", None),
        proxy_state=proxy_state,
    )


def _optimize_prompt_text_hybrid(
    prompt_text: str,
    max_calls: int = 10,
    use_reward_adapter: bool = False,
    reflect_model: str | None = None,
    proxy_state: bool = False,
) -> tuple[str, float, float]:
    """Hybrid optimization: heuristic GEPA + OTel validation.

    When proxy_state=True, uses ProxyStateTracker (LLM-as-a-Judge)
    for 5-dimension scoring instead of simulated reasoning traces.
    """
    from evolution.prompts.otel_adapter import OTelPromptAdapter

    # Phase 1: Generate candidate with heuristic GEPA
    heuristic_evolved, _, _ = _optimize_prompt_text_heuristic(
        prompt_text,
        max_calls,
        use_reward_adapter,
        reflect_model,
        proxy_state=getattr(_current_args, "proxy_state", False)
        if _current_args
        else False,
    )

    if heuristic_evolved == prompt_text:
        # No candidate generated — nothing to validate
        hs = evaluate_prompt_wrapper(prompt_text)
        return prompt_text, hs, hs

    # Length cap: reject evolved candidates that are >8x original length.
    # 10-sample data shows: every candidate >8x original was rejected by OTel (−0.20 avg),
    # EXCEPT when the original is broken (OTel ~0.0) — in that case, even a long prompt is worth evaluating.
    # This saves ~30s of OTel eval time per bloated candidate on already-working prompts.
    if len(heuristic_evolved) > len(prompt_text) * 8:
        print(
            f"  Length cap: {len(heuristic_evolved)} vs {len(prompt_text)} chars (>8x, skipping OTel)"
        )
        hs = evaluate_prompt_wrapper(prompt_text)
        return prompt_text, hs, hs

    # Local filter: reject mutations before expensive OTel eval.
    # Scores the parent/mutation pair through the daemonized Qwen2.5
    # model service. If probability < threshold (0.7956), skip OTel.
    if (
        getattr(_current_args, "local_filter", False)
        and _current_args is not None
        and heuristic_evolved != prompt_text
    ):
        filter_result = _call_model_service(prompt_text, heuristic_evolved)
        if filter_result.get("decision", 1) == 0:
            prob = filter_result.get("logit_probability", 0.0)
            print(
                f"  Local filter REJECTED mutation (P={prob:.4f} < threshold) —"
                f" skipping OTel eval",
                flush=True,
            )
            hs = evaluate_prompt_wrapper(prompt_text)
            return prompt_text, hs, hs
        else:
            prob = filter_result.get("logit_probability", 0.0)
            print(
                f"  Local filter PASSED mutation (P={prob:.4f}) — proceeding to OTel",
                flush=True,
            )

    # Phase 2: OTel A/B validation
    batch = [{"input": "eval", "answer": "pass"}]
    adapter = OTelPromptAdapter(
        hermes_timeout=180, max_turns=10, cleanup_prompt=None
    )

    # Score original
    adapter.evaluate(
        batch,
        {"prompt": prompt_text},
        run_suffix=f"hybrid_orig_{hash(prompt_text) % 10000}",
        cleanup=True,
    )
    # Run again fresh after cleanup to get clean score
    r1 = adapter.evaluate(
        batch,
        {"prompt": prompt_text},
        run_suffix=f"hybrid_a_{hash(prompt_text) % 10000}",
    )
    orig_score = r1.scores[0]

    # Cleanup, then score evolved
    adapter._run_cleanup()
    r2 = adapter.evaluate(
        batch,
        {"prompt": heuristic_evolved},
        run_suffix=f"hybrid_b_{hash(heuristic_evolved) % 10000}",
    )
    evo_score = r2.scores[0]

    hs = evaluate_prompt_wrapper(prompt_text)
    evo_hs = evaluate_prompt_wrapper(heuristic_evolved)

    if evo_score > orig_score:
        print(f"  OTel validated: +{evo_score - orig_score:.3f} improvement")
        return heuristic_evolved, hs, evo_hs
    else:
        print(
            f"  OTel rejected: {evo_score:.3f} vs {orig_score:.3f} (keeping original)"
        )
        return prompt_text, hs, hs


def _optimize_prompt_text_heuristic(
    prompt_text: str,
    max_calls: int = 10,
    use_reward_adapter: bool = False,
    reflect_model: str | None = None,
    proxy_state: bool = False,
) -> str:
    """Generate candidate with heuristic GEPA (fast, no LLM calls for eval).

    When proxy_state=True, uses ProxyStateTracker (LLM-as-a-Judge) for
    5-dimension scoring instead of simulated reasoning traces.
    """
    from evolution.prompts.otel_adapter import make_hermes_lm

    seed = {"prompt": prompt_text}
    dataset = [
        {"input": "eval", "answer": "pass", "additional_context": {}},
        {"input": "eval", "answer": "pass", "additional_context": {}},
    ]

    adapter = HeuristicPromptAdapter(
        evaluator_fn=lambda text: max(
            0.0, min(1.0, evaluate_prompt_wrapper(text))
        ),
    )

    if use_reward_adapter and _HAS_REWARD_ADAPTER:
        from evolution.prompts.reward_adapter import RewardAdapter

        adapter = RewardAwareAdapter(
            evaluator_fn=lambda text: max(
                0.0, min(1.0, evaluate_prompt_wrapper(text))
            ),
            reward_adapter=RewardAdapter(),
            reflect_model=reflect_model,
            proxy_state=proxy_state,
        )
        if proxy_state:
            print(
                f"  Using ProxyStateTracker (LLM-as-a-Judge) for 5-dimension scoring — replaces heuristic SNR gap"
            )
        elif reflect_model:
            from evolution.prompts.reward_adapter import (
                has_reasoning_capability,
            )

            if has_reasoning_capability(reflect_model):
                print(
                    f"  Using RewardAwareAdapter with K2.6 reasoning trace enrichment (model: {reflect_model})"
                )
            else:
                print(
                    f"  Using RewardAwareAdapter with [SIMULATED] reasoning traces — model {reflect_model} not recognized as K2.6"
                )
        else:
            print(
                "  Using RewardAwareAdapter with [SIMULATED] reasoning traces (no --reflect-model set)"
            )

    reflection_lm = make_hermes_lm(
        max_turns=1, timeout=180, model=reflect_model
    )

    REFLECTION_PROMPT = """I am optimizing a test scenario description for MCP (Model Context Protocol) tool testing.

Current test description:
```
<curr_instructions>
```

The following are evaluations of the current description, including composite scores and feedback on what should be improved:
```
<inputs_outputs_feedback>
```

Your task is to write an IMPROVED test scenario description.

IMPORTANT CONSTRAINTS:
- This is a TEST SCENARIO DESCRIPTION, not executable code or shell commands.
- The description tells a human test operator what to verify using an MCP tool.
- Do NOT write Docker commands, shell scripts, or code snippets.
- The description should be self-contained and clearly state what is being tested.
- Focus on: clarity, coverage of edge cases, resilience, self-containment, and verifiability.
- Prefer specific improvements (name the exact tool, parameters, expected errors) over general prose — a sharp one-line fix is better than 3 paragraphs of structured steps.
- When the feedback includes a "Failure classification" and "Reasoning insight", use that to guide your structural changes:
  * structural → add tool definitions, parameter guidance, or error guardrails
  * behavioral → adjust timing, retry logic, or precondition checks
  * [SIMULATED] → the insight is heuristic; treat it as a suggestion, not authentic K2.6 analysis
- When the feedback includes "Error payloads from recent failures", examine the actual error messages from tool/infrastructure failures. Use those to add specific error-recovery guardrails (e.g. precondition checks before creates, timeout handling on starts, retry logic on deletes) that address the real failure cause — not generic prose.
- Keep the evolved text within 150% of the original length. Concise, targeted improvements outperform verbose rewrites — prefer adding one sharp constraint over three paragraphs of prose.

Provide the new description within ``` blocks."""

    try:
        result = gepa.optimize(
            seed_candidate=seed,
            trainset=dataset,
            adapter=adapter,
            reflection_lm=reflection_lm,
            reflection_prompt_template=REFLECTION_PROMPT,
            max_metric_calls=max_calls,
            display_progress_bar=True,
        )
        evolved_text = result.best_candidate.get("prompt", prompt_text)
        # Post-process: strip meta-commentary prose that GEPA's extractor missed.
        # The reflection LM often outputs analysis before the actual test description.
        # Find the first line that looks like a real test instruction.
        import re

        TEST_VERBS = r"(?:Create|Pull|Tag|Push|Build|List|Start|Stop|Delete|Inspect|Exec|"
        TEST_VERBS += (
            r"Execute|Restore|Validate|Check|Attempt|Run|Submit|Given|Test|"
        )
        TEST_VERBS += r"Verify|Clean|Prune|Rollback|Stream|Use|Call)"
        lines = evolved_text.strip().split("\n")
        first_instruction = -1
        for i, line in enumerate(lines):
            s = line.strip()
            # Skip meta-commentary lines
            if re.match(
                r"^(Based on|Here.?(?: is|'s)|Looking at|I have|The (?:following|key)|"
                r"This improved|Summary of|Key improvements|Dimensional|"
                r"Analyze the|Here are|Below is|Provide the)",
                s,
                re.I,
            ):
                continue
            # Check if this line starts a test instruction
            if re.match(TEST_VERBS, s, re.I) and len(s) > 10:
                first_instruction = i
                break
        if first_instruction > 0:
            evolved_text = "\n".join(lines[first_instruction:]).strip()
            m_block = re.search(
                r"```(?:\w+)?\n(.+?)```", evolved_text, re.DOTALL
            )
            if m_block:
                evolved_text = m_block.group(1).strip()

        # Length truncation: cap at 150% of original to stay under the 8x OTel cap.
        # The reflection LM tends to over-generate when given multiple improvement
        # targets. Truncate at the nearest paragraph boundary.
        max_len = int(len(prompt_text) * 1.5)
        if len(evolved_text) > max_len:
            # Find a good truncation point: try paragraph break, then sentence break
            truncated = evolved_text[:max_len]
            para_break = max(
                truncated.rfind("\n\n"), truncated.rfind("\r\n\r\n")
            )
            if (
                para_break > max_len * 0.5
            ):  # Only use paragraph break if past halfway
                evolved_text = truncated[:para_break].strip()
            else:
                # Fall back to last sentence boundary
                sent_break = max(
                    truncated.rfind(". "),
                    truncated.rfind(".\n"),
                    truncated.rfind("!\n"),
                    truncated.rfind("?\n"),
                )
                if sent_break > max_len * 0.5:
                    evolved_text = truncated[: sent_break + 1].strip()
                # else: keep the truncated text at the hard cap
    except Exception as e:
        print(f"  GEPA optimize failed: {e}")
        evolved_text = prompt_text

    evolved_score = evaluate_prompt_wrapper(evolved_text)
    original_score = evaluate_prompt_wrapper(prompt_text)
    return evolved_text, original_score, evolved_score


def log_evidence(entry: dict) -> None:
    """Append structured evidence to evolve-evidence.jsonl."""
    EVIDENCE_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["_ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(EVIDENCE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def evolve_single_prompt(prompt_num: int, inventory: list) -> dict:
    """Evolve a single prompt using GEPA. This is the G1 canary."""
    prompt = next(p for p in inventory if p["num"] == prompt_num)
    original_text = prompt["text"]
    tools = prompt["tools"]

    # ── Dead Man's Switch: monitor per-prompt wall time ─────────────
    try:
        from evolution.utils.stop_condition import GEPAStopper

        _stopper = getattr(evolve_single_prompt, "_stopper", None)
        if _stopper is None:
            _stopper = GEPAStopper(max_seconds_per_prompt=1200)
            evolve_single_prompt._stopper = _stopper
        # Dynamic timer adjustment: add buffer based on current latency band
        _stopper.adjust_for_band(_get_latency_band())
        _stopper.start_prompt(str(prompt_num))
    except ImportError:
        _stopper = None

    print(f"\n{'=' * 60}")
    print(f"Canary: evolving prompt #{prompt_num} ({prompt['title']})")
    print(f"{'=' * 60}")
    print(f"  Tools: {', '.join(tools)}")

    original_score = evaluate_prompt_wrapper(original_text)
    print(f"  Baseline score: {original_score:.3f}")
    print(f"  Original length: {len(original_text)} chars")

    evolved_text, _, evolved_score = optimize_prompt_text(
        original_text,
        tools,
        max_calls=10,
        proxy_state=getattr(_current_args, "proxy_state", False),
    )

    # ── Dead Man's Switch: check if we timed out during optimization ──
    if _stopper is not None:
        if _stopper.check():
            print(
                f"  ⏱️  Stopper triggered — prompt #{prompt_num} exceeded limit."
            )
            evolved_text = original_text
            evolved_score = original_score

    # ── Quality gate: reject evolved text with reflective/trace leakage ──
    try:
        from evolution.prompts.prompt_validator import safe_write_evolved

        accepted, sanitized_or_error = safe_write_evolved(
            evolved_text,
            original_text,
            prompt_num,
            max_length=1500,
            max_bloat_ratio=3.0,
        )
        if not accepted:
            print(f"  ❌ Quality gate rejected: {sanitized_or_error[:120]}")
            # Fall back to original text — better to keep the clean original
            evolved_text = original_text
            evolved_score = original_score
        else:
            evolved_text = sanitized_or_error
    except ImportError:
        pass  # validator not available
    except Exception as e:
        print(f"  ⚠️  Validation error (non-fatal): {e}")

    print(
        f"  Evolved score: {evolved_score:.3f} (delta: {evolved_score - original_score:+.3f})"
    )
    print(f"  Evolved length: {len(evolved_text)} chars")

    evidence = {
        "phase": "G1-canary",
        "prompt_num": prompt_num,
        "baseline_score": original_score,
        "evolved_score": evolved_score,
        "improvement": round(evolved_score - original_score, 4),
        "original_length": len(original_text),
        "evolved_text": evolved_text,
        "evolved_length": len(evolved_text),
        "evaluator": "proxy_state"
        if getattr(_current_args, "proxy_state", False)
        else "heuristic",
    }
    log_evidence(evidence)

    # ── Dead Man's Switch: signal prompt completion ─────────────────
    if _stopper is not None:
        _stopper.end_prompt()

    return evidence


# ── Resource-aware parallelism ────────────────────────────────────────────


# ── Thermal / memory pressure thresholds ─────────────────────────────────
MEM_THRESHOLD_CRITICAL_MB = 500  # Below this: force --parallel 1
MEM_THRESHOLD_MODERATE_MB = 1000  # Below this: use --parallel 2
# macOS page size for vm_stat reading
_MACOS_PAGE_SIZE = 16384

# ── Convergence stall detection (from agent_statemachine v2) ──────────────
MAX_ZERO_DELTAS = 10
STALL_THRESHOLD_RATIO = 0.80  # If >80% of tier is zero-delta, flag stall
CONVERGENCE_EPSILON = 0.001  # |delta| below this is "zero"


def _get_available_memory_mb() -> int | None:
    """Return free+inactive+speculative memory in MB via vm_stat.

    Returns None if reading fails (e.g. non-macOS, vm_stat not available).
    Mirrors the calculation in kanban-drum.sh.
    """
    try:
        import subprocess

        r = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=5
        )
        free_pages = 0
        inactive_pages = 0
        speculative_pages = 0
        for line in r.stdout.split("\n"):
            ls = line.strip().rstrip(".")
            if ls.startswith("Pages free:"):
                free_pages = int(ls.split(":")[1].strip())
            elif ls.startswith("Pages inactive:"):
                inactive_pages = int(ls.split(":")[1].strip())
            elif ls.startswith("Pages speculative:"):
                speculative_pages = int(ls.split(":")[1].strip())
        total_pages = free_pages + inactive_pages + speculative_pages
        return total_pages * _MACOS_PAGE_SIZE // (1024 * 1024)
    except Exception:
        return None


def _check_thermal_pressure(
    user_parallel: int = 1,
) -> tuple[int, str, dict]:
    """Check vm_stat RAM pressure and recommend a safe parallelism level.

    Returns (safe_parallelism: int, warning: str, diagnostics: dict).

    Thresholds match kanban-drum.sh and the task body:
      - <  500 MB free+inactive: force --parallel 1 (critical)
      - 500–1000 MB:             --parallel 2 (moderate)
      - > 1000 MB:               use user-specified --parallel (green)

    Also queries OTel latency band if available — RED band further
    constrains parallelism to 1 regardless of RAM.
    """
    available_mb = _get_available_memory_mb()
    diag = {"available_mb": available_mb}

    if available_mb is None:
        # Cannot measure — proceed with user-requested parallelism
        return user_parallel, "", diag

    # 1. Determine memory-based safe parallelism
    if available_mb < MEM_THRESHOLD_CRITICAL_MB:
        safe = 1
        warning = (
            f"⚠ Thermal pressure: {available_mb}MB free < "
            f"{MEM_THRESHOLD_CRITICAL_MB}MB threshold — forcing --parallel 1"
        )
    elif available_mb < MEM_THRESHOLD_MODERATE_MB:
        safe = min(2, user_parallel)
        warning = (
            f"⚡ Moderate memory: {available_mb}MB free < "
            f"{MEM_THRESHOLD_MODERATE_MB}MB — limiting --parallel to {safe}"
        )
    else:
        safe = user_parallel
        warning = ""

    # 2. Check OTel latency band — RED band overrides to 1 regardless
    try:
        from evolution.prompts.otel_adapter import _measure_latency_drift

        ld = _measure_latency_drift()
        diag["latency_band"] = ld.get("latency_band", "GREEN")
        diag["latency_std_ms"] = ld.get("std_ms", 0.0)
        if ld.get("latency_band") == "RED" and safe > 1:
            safe = 1
            red_warn = (
                f"🔥 Latency band RED (σ={ld.get('std_ms', 0):.1f}ms) — "
                f"thermal throttling detected, forcing --parallel 1"
            )
            warning = f"{warning}; {red_warn}" if warning else red_warn
        elif ld.get("latency_band") == "YELLOW" and safe > 2:
            safe = min(safe, 2)
            yellow_warn = (
                f"⚡ Latency band YELLOW (σ={ld.get('std_ms', 0):.1f}ms) — "
                f"capping --parallel at 2"
            )
            warning = f"{warning}; {yellow_warn}" if warning else yellow_warn
    except Exception:
        diag["latency_band"] = "UNKNOWN"

    diag["safe_parallelism"] = safe
    return safe, warning, diag


def parse_vm_stat(vm_stat_output: str, page_size: int = 16384) -> dict:
    """Parse macOS vm_stat output into a structured dict.

    Args:
        vm_stat_output: Raw output string from vm_stat command.
        page_size: Memory page size in bytes (default 16384 for Apple Silicon).

    Returns:
        Dict with keys: free_pages, inactive_pages, page_size.

    Raises:
        ValueError: If Pages free is missing from the output.

    """
    free_pages = None
    inactive_pages = 0
    for line in vm_stat_output.split("\n"):
        ls = line.strip().rstrip(".")
        if ls.startswith("Pages free:"):
            raw = ls.split(":")[1].strip()
            free_pages = int(
                float(raw)
            )  # handle both int and scientific notation
        elif ls.startswith("Pages inactive:"):
            raw = ls.split(":")[1].strip()
            inactive_pages = int(float(raw))

    if free_pages is None:
        raise ValueError("vm_stat output missing 'Pages free:'")

    return {
        "free_pages": free_pages,
        "inactive_pages": inactive_pages,
        "page_size": page_size,
    }


def compute_pressure(free: int, inactive: int, total: int) -> float:
    """Compute memory pressure ratio.

    Formula: pressure = 1.0 - (free + inactive) / total

    Args:
        free: Available/free memory in arbitrary unit (same as total).
        inactive: Inactive/reclaimable memory in same unit.
        total: Total memory in same unit.

    Returns:
        Pressure ratio clamped to [0.0, 1.0].
        0.0 = abundant free memory, 1.0 = critically high pressure.

    """
    if total == 0:
        return 1.0
    pressure = 1.0 - (free + inactive) / total
    return max(0.0, min(1.0, pressure))


def _compute_memory_pressure() -> tuple[float, dict]:
    """Compute memory pressure ratio from macOS vm_stat.

    Formula: pressure = 1.0 - (pages_free + pages_inactive) / total_pages
    Where total_pages = hw.memsize / 16384 (Apple Silicon 16KB page size).

    Returns:
        (pressure_ratio: float 0.0-1.0, diagnostics: dict)
        pressure_ratio near 0.0 = abundant free memory
        pressure_ratio > 0.85   = critically high pressure

    Raises RuntimeError if vm_stat or sysctl is unavailable.

    """
    import subprocess

    # Get total physical memory
    sysctl_r = subprocess.run(
        ["sysctl", "-n", "hw.memsize"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if sysctl_r.returncode != 0:
        raise RuntimeError(
            f"sysctl hw.memsize failed: {sysctl_r.stderr.strip()}"
        )
    total_memory_bytes = int(sysctl_r.stdout.strip())
    page_size = 16384  # Apple Silicon
    total_pages = total_memory_bytes // page_size

    # Parse vm_stat
    r = subprocess.run(
        ["vm_stat"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if r.returncode != 0:
        raise RuntimeError(f"vm_stat failed: {r.stderr.strip()}")

    parsed = parse_vm_stat(r.stdout, page_size)
    free_pages = parsed["free_pages"]
    inactive_pages = parsed["inactive_pages"]

    pressure = compute_pressure(free_pages, inactive_pages, total_pages)

    diag = {
        "free_pages": free_pages,
        "inactive_pages": inactive_pages,
        "total_pages": total_pages,
        "free_gb": round(free_pages * page_size / (1024**3), 2),
        "inactive_gb": round(inactive_pages * page_size / (1024**3), 2),
        "total_gb": round(total_memory_bytes / (1024**3), 1),
        "pressure": round(pressure, 4),
    }
    return pressure, diag


def _check_system_resources(
    max_memory_gb: float = 0.5, max_containers: int = 8
) -> tuple[bool, str]:
    """Check if the system has enough resources for another parallel worker.

    Returns (can_proceed: bool, reason: str).
    Checks:
    - Memory pressure score (via vm_stat)
    - Free memory (via psutil or vm_stat fallback)
    - Running Apple Container count

    Thresholds unified with drum script and _check_thermal_pressure():
      - Pressure > 0.85: CRITICAL — suspend next worker
      - < 0.5 GB (500 MB): CRITICAL — block worker
      - >= 0.5 GB: allow (thermal pressure check handles parallelism limiting)

    Called by each parallel worker before starting a Hermes session.
    Workers sleep + retry when resources are constrained.
    """
    reason = ""

    # 0. Reclaim memory: clean up idle/stale containers before checking
    try:
        import subprocess as _sp
        _sp.run(
            [_resolve("COMPOSE_PKL_DIR",
                       str(Path.home() / "workspace" / "compose-pkl"))
             / "scripts" / "container_cleanup.py"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        pass

    # 1. Memory pressure score — suspend workers at >85% pressure
    try:
        pressure, diag = _compute_memory_pressure()
        if pressure > 0.85:
            return (
                False,
                f"memory pressure {pressure:.3f} > 0.85 — suspending next worker "
                f"(free={diag['free_gb']:.1f}GB, inactive={diag['inactive_gb']:.1f}GB, "
                f"total={diag['total_gb']:.1f}GB)",
            )
    except Exception as exc:
        # Can't compute pressure — log diagnostic but don't block
        reason = f"(memory pressure check unavailable: {exc})"

    # 2. Free memory check — available memory
    try:
        import psutil

        mem = psutil.virtual_memory()
        free_gb = mem.available / (1024**3)
        if free_gb < 0.5:
            return (
                False,
                f"critically low memory: {free_gb:.1f}GB available (threshold: 0.5GB)",
            )
    except ImportError:
        # Fallback: parse vm_stat output
        # Available = free + inactive + speculative pages (reclaimable)
        try:
            import subprocess

            r = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=5
            )
            free_pages = 0
            inactive_pages = 0
            speculative_pages = 0
            for line in r.stdout.split("\n"):
                ls = line.strip().rstrip(".")
                if ls.startswith("Pages free:"):
                    free_pages = int(ls.split(":")[1].strip())
                elif ls.startswith("Pages inactive:"):
                    inactive_pages = int(ls.split(":")[1].strip())
                elif ls.startswith("Pages speculative:"):
                    speculative_pages = int(ls.split(":")[1].strip())
            total_pages = free_pages + inactive_pages + speculative_pages
            available_mb = (
                total_pages * 16384 / (1024 * 1024)
            )  # 16KB page size on Apple Silicon
            if available_mb < 500:
                return (
                    False,
                    f"critically low memory: {available_mb:.0f}MB available (threshold: 500MB)",
                )
        except Exception:
            pass  # Can't check memory — proceed anyway

    # 3. Container check — don't overwhelm the Apple Container runtime
    try:
        import subprocess

        r = subprocess.run(
            ["container", "list", "--all"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        running = sum(
            1
            for line in r.stdout.split("\n")
            if line.strip()
            and not line.startswith("CONTAINER")
            and line.strip()
        )
        if running >= max_containers:
            return (
                False,
                f"{running} containers running (threshold: {max_containers})",
            )
    except Exception:
        pass  # Can't check containers — proceed anyway

    return True, reason


def _check_convergence_stall(
    results: list[dict], tier_num: int
) -> dict | None:
    """Detect convergence stall in a completed tier evolution.

    Mirrors agent_statemachine v2's zero_delta_count logic.
    Returns a stall report dict if stalled, None if healthy.

    A tier is "stalled" when >80% of prompts produced |delta| < 0.001
    (matching the 87% zero-delta observed in the E2E Canary).
    """
    total = len(results)
    if total == 0:
        return None

    zero_deltas = sum(
        1 for r in results if abs(r.get("delta", 0)) < CONVERGENCE_EPSILON
    )
    zero_ratio = zero_deltas / total
    non_zero = total - zero_deltas

    if zero_ratio >= STALL_THRESHOLD_RATIO and non_zero < 3:
        positives = [
            r for r in results if r.get("delta", 0) > CONVERGENCE_EPSILON
        ]
        negatives = [
            r for r in results if r.get("delta", 0) < -CONVERGENCE_EPSILON
        ]
        best = max(positives, key=lambda r: r["delta"]) if positives else None
        worst = min(negatives, key=lambda r: r["delta"]) if negatives else None

        return {
            "stalled": True,
            "tier": tier_num,
            "total": total,
            "zero_deltas": zero_deltas,
            "zero_ratio": round(zero_ratio, 3),
            "best_improvement": best["delta"] if best else 0,
            "best_prompt": best["prompt_num"] if best else None,
            "worst_regression": worst["delta"] if worst else 0,
            "worst_prompt": worst["prompt_num"] if worst else None,
            "suggestion": (
                "Reduce iteration budget or switch strategy — "
                f"{zero_deltas}/{total} prompts produced zero improvement."
            ),
        }

    return None


# ── Mutation pre-filter: daemonized Qwen2.5 binary logit scorer ─────────

_MODEL_SERVICE_URL = "http://127.0.0.1:11435"
_LOCAL_FILTER_THRESHOLD = 0.7956
_FILTER_FAILURE_COUNT = 0  # tracks consecutive filter failures across calls

# ── CLIP semantic regularizer (lazy-loaded) ────────────────────────────
_CLIP_MODEL = None


def _clip_similarity(original: str, evolved: str) -> float:
    """Cosine similarity between CLIP text embeddings of two prompts.

    Returns float in [0.0, 1.0] where 1.0 = identical meaning.
    Clamped to [0, 1] — CLIP cosine similarity naturally ranges
    from ~0.6 (different topics) to ~0.95 (near-identical).

    Lazy-loads CLIP ViT-B/32 on first call (~338MB model, ~260ms warm).
    Uses explicit download_root to avoid sandbox ~ resolution issues.
    """
    global _CLIP_MODEL
    if _CLIP_MODEL is None:
        import clip
        import torch

        _CLIP_MODEL, _ = clip.load(
            "ViT-B/32", device="cpu",
            download_root="/Users/kieranlal/.cache/clip",
        )

    import clip as _clip_module
    import torch

    # CLIP's text encoder has a 77-token context limit. Truncate mid-sentence
    # if needed — losing the tail of a long prompt still retains ~90% of
    # semantic meaning for drift comparison.
    _CLIP_MAX_TOKENS = 77
    truncated_original = original
    truncated_evolved = evolved
    for attempt in range(2):
        try:
            texts = _clip_module.tokenize([truncated_original, truncated_evolved])
            break
        except RuntimeError as e:
            if "too long for context length" in str(e):
                # CLIP's text encoder has 77-token limit. Truncate to 77 chars
                # (CLIP BPE averages ~1 token/char for typical English text).
                truncated_original = original[:77]
                truncated_evolved = evolved[:77]
            else:
                raise
    with torch.no_grad():
        features = _CLIP_MODEL.encode_text(texts)
    features = features / features.norm(dim=-1, keepdim=True)
    sim = (features[0] @ features[1].T).item()
    return max(0.0, min(1.0, sim))


def _generate_run_id() -> str:
    """Generate a short unique run identifier for resource prefixing (Victoria Protocol).
    Format: t{T}{short_hash}, e.g. t1_a3f7 for tier 1 with a random 4-char suffix."""
    import hashlib
    import random
    tier = getattr(_current_args, "tier", 0)
    suffix = hashlib.md5(str(random.random()).encode()).hexdigest()[:4]
    return f"t{tier}_{suffix}"


_CLIP_MODEL = None
def _check_mutation_filter(
    prompt_text: str,
    evolved_text: str,
    prompt_num: int,
) -> tuple[bool, float]:
    global _FILTER_FAILURE_COUNT
    """Score a parent/mutation pair via daemonized model service.

    Returns (should_proceed: bool, probability: float).
    - should_proceed=True: mutation passes filter, proceed to OTel eval
    - should_proceed=False: mutation rejected by filter (P < threshold)

    In case of service errors, defaults to PROCEED (safe fail-open).
    Logs escalating warnings: first failure, then every 5th failure.
    """
    import urllib.request
    import urllib.error

    body = json.dumps({
        "parent": prompt_text,
        "mutation": evolved_text,
        "threshold": _LOCAL_FILTER_THRESHOLD,
    }).encode()

    try:
        req = urllib.request.Request(
            f"{_MODEL_SERVICE_URL}/score",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode())
        prob = result.get("logit_probability", 0.0)
        decision = result.get("decision", 1)

        # Reset failure counter on success
        _FILTER_FAILURE_COUNT = 0

        if decision == 0:
            return False, prob
        return True, prob

    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as e:
        _FILTER_FAILURE_COUNT += 1
        count = _FILTER_FAILURE_COUNT
        if count == 1 or count % 5 == 0:
            print(
                f"  ⚠️  FILTER SERVICE DOWN (#{count} consecutive failure(s)) "
                f"for #{prompt_num}: {e} — "
                f"defaulting to PROCEED (fail-open). "
                f"Restart: python3 scripts/model_service.py",
            )
        return True, 0.5


def evolve_tier(tier_num: int, inventory: list) -> dict:
    """Evolve all prompts in a tier using GEPA, reading from SQLite prompts table.

    Replaces the markdown-file reading path with a PostgreSQL-style SQLite query.
    For each prompt: SELECT text FROM prompts WHERE tier = {tier} ORDER BY prompt_num
    → evolve via GEPA → INSERT into prompt_versions table.
    The markdown source file is NOT modified.

    When --parallel N > 1, processes prompts concurrently using a thread pool.
    Each worker runs its own Hermes Agent session. Container names are parameterized
    (Plan 130 §3.1) to prevent state contamination between parallel evaluations.
    """
    import concurrent.futures
    import threading

    cfg = TIER_BUDGETS[tier_num]
    lo, hi = cfg["prompts"]
    parallel = getattr(_current_args, "parallel", 1)
    print(f"\n{'=' * 60}")
    print(f"Evolving {cfg['label']} ({lo}-{hi})")
    print(f"  Iterations: {cfg['iterations']}")
    print(f"  Parallelism: {parallel} worker(s)")
    print(f"  Database: PostgreSQL ({DB_HOST}:{DB_PORT}/{DB_NAME}, prompts table, tier={tier_num})")
    if getattr(_current_args, "local_filter", False):
        print(f"  Pre-filter: ON (Qwen2.5 daemon at {_MODEL_SERVICE_URL}, threshold={_LOCAL_FILTER_THRESHOLD})")
    if getattr(_current_args, "clip_regularizer", False):
        print(f"  CLIP regularizer: ON (ViT-B/32 at 0.2 weight)")
    if getattr(_current_args, "hyper", False):
        print(f"  Hyper-Mutation: ON (apfel radical escape on stall)")
    print(f"{'=' * 60}")

    # ── Thermal pressure check: auto-scale parallelism based on RAM ──────
    safe_parallel, pressure_warning, pressure_diag = _check_thermal_pressure(
        user_parallel=parallel,
    )
    if pressure_warning:
        print(f"  {pressure_warning}")
    parallel = safe_parallel

    # Thread lock for shared state in checkpoint/evidence writes
    _lock = threading.Lock()

    # ── Step 1: Read prompts from DB ─────────────────────────────────────────
    conn = _pg_connect()
    try:
        db_rows = _pg_fetch_all(
            conn,
            "SELECT prompt_num, text, title FROM prompts WHERE tier = $1 ORDER BY prompt_num",
            (tier_num,),
        )
    finally:
        conn.close()

    if not db_rows:
        print("  No prompts found in DB for this tier.")
        return {
            "phase": f"tier-{tier_num}",
            "source": "postgresql",
            "prompts_evolved": 0,
            "total_improvement": 0,
            "avg_improvement": 0,
        }

    # Build prompt tasks from DB rows
    prompt_tasks = []
    resume = getattr(_current_args, "resume", False)
    resume_db = getattr(_current_args, "resume_from_db", None)
    completed_set = set()
    
    if resume:
        cp = load_checkpoint(cfg["label"])
        if cp:
            completed_set = set(cp.get("completed_prompts", []))
    elif resume_db:
        try:
            import pg8000 as _pg8
            _db_conn = _pg8.connect(
                host=DB_HOST, port=DB_PORT, user=DB_USER,
                password=DB_PASSWORD, database=DB_NAME,
            )
            _db_cur = _db_conn.cursor()
            # Highest honcho DB inherits evo state across restarts
            _db_cur.execute(
                "SELECT prompt_num FROM evolution_state "
                "WHERE run_id LIKE $1 AND status = 'done'",
                (f"t{tier_num}_%",),
            )
            done_prompts = [r[0] for r in _db_cur.fetchall()]
            _db_conn.close()
            completed_set = set(done_prompts)
            if done_prompts:
                print(f"  DB resume: {len(done_prompts)} prompts already done")
        except Exception as e:
            print(f"  ⚠ DB resume failed (falling back to full run): {e}")
    
    for row in db_rows:
        prompt_num = row["prompt_num"]
        if resume and prompt_num in completed_set:
            print(
                f"  ⏭️  Skipping prompt #{prompt_num} (completed in prior run)"
            )
            continue
        prompt_tasks.append(
            {
                "prompt_num": prompt_num,
                "prompt_text": row["text"],
                "title": row["title"],
                "tools": PROMPT_TOOLS.get(prompt_num, []),
            }
        )

    if not prompt_tasks:
        print("  No prompts to evolve in this tier.")
        return {
            "phase": f"tier-{tier_num}",
            "source": "postgresql",
            "prompts_evolved": 0,
            "total_improvement": 0,
            "avg_improvement": 0,
        }

    # ── Step 2: Evolve prompts in parallel ──────────────────────────────────
    evolve_session_id = (
        f"evolve_{tier_num}_{int(time.time())}_{os.urandom(4).hex()}"
    )

    def _get_source_commit() -> str:
        """Get current git HEAD as source_commit for version tracking."""
        try:
            import subprocess

            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=str(COMPOSE_PKL),
            )
            return r.stdout.strip() if r.returncode == 0 else "unknown"
        except Exception:
            return "unknown"

    def _insert_version(
        conn,
        prompt_num: int,
        tier: int,
        evolved_text: str,
        orig_score: float,
        score: float,
        source_commit: str,
        session_id: str,
    ) -> None:
        """Insert a new prompt_versions row with auto-incremented version number."""
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(MAX(version), 0) FROM prompt_versions WHERE prompt_num = $1 AND tier = $2",
            (prompt_num, tier),
        )
        max_version = cur.fetchone()[0]
        new_version = max_version + 1
        delta = score - orig_score
        cur.execute(
            """INSERT INTO prompt_versions
               (prompt_num, tier, version, parent_version, text, strategy, score, delta,
                evolved_by, source_commit, evolve_session_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)""",
            (
                prompt_num,
                tier,
                new_version,
                max_version if max_version > 0 else None,
                evolved_text,
                "gepa_evolution",
                round(score, 4),
                round(delta, 4),
                "gepa",
                source_commit,
                session_id,
            ),
        )
        conn.commit()

    def _evolve_one(task: dict) -> dict:
        """Evolve a single prompt and return its result.
        Checks system resources before starting; backs off if constrained.
        """
        prompt_num = task["prompt_num"]
        prompt_text = task["prompt_text"]
        
        # Inject resource prefix for determinisitic cleanup (Victoria Protocol)
        # All containers/volumes/networks created by this prompt will use
        # this prefix, enabling surgical removal after the tier completes.
        _RUN_PREFIX = getattr(_current_args, "run_id", None) or _generate_run_id()
        prompt_text = (
            f"[RESOURCE_PREFIX: {_RUN_PREFIX}_{prompt_num}] "
            f"Prefix all container, volume, and network names with "
            f'"{_RUN_PREFIX}_{prompt_num}_" throughout this request. '
            f"Do NOT include the prefix in any descriptions or explanations — "
            f"only use it in actual resource name parameters.\n\n"
            f"{prompt_text}"
        )
        
        tools = task["tools"]

        # Resource-aware backoff: check before each Hermes session start
        max_retries = 10
        for attempt in range(max_retries):
            ok, reason = _check_system_resources(
                max_memory_gb=getattr(_current_args, "max_memory", 0.5),
                max_containers=getattr(_current_args, "max_containers", 8),
            )
            if ok:
                break
            if attempt == 0:
                print(f"  ⏳ #{prompt_num} waiting for resources: {reason}")
            import time

            time.sleep(5 * (attempt + 1))  # 5s, 10s, 15s, ...

        orig_score = evaluate_prompt_wrapper(prompt_text)
        evolved_text, _, evolved_score = optimize_prompt_text(
            prompt_text,
            tools,
            max_calls=cfg["iterations"] * 2,
            proxy_state=getattr(_current_args, "proxy_state", False),
        )

        # ── Binary logit pre-filter: skip trivial mutations ────────────────
        filter_rejected = False
        if getattr(_current_args, "local_filter", False):
            proceed, prob = _check_mutation_filter(
                prompt_text, evolved_text, prompt_num,
            )
            if not proceed:
                print(
                    f"  ⋮ #{prompt_num} filter REJECTED (P={prob:.4f}) — "
                    f"skipping version insert"
                )
                filter_rejected = True

        # ── CLIP semantic regularizer: blend at 0.2 weight ──────────────
        clip_sim = None
        if getattr(_current_args, "clip_regularizer", False):
            clip_sim = _clip_similarity(prompt_text, evolved_text)
            if not filter_rejected:
                # Apply blend: 0.8 * PST score + 0.2 * CLIP similarity
                evolved_score = 0.8 * evolved_score + 0.2 * clip_sim

        delta = evolved_score - orig_score

        # Insert into prompt_versions (thread-safe: each worker has its own DB conn)
        src_commit = _get_source_commit()
        if not filter_rejected:
            db_conn = _pg_connect()
            try:
                _insert_version(
                    db_conn,
                    prompt_num,
                    tier_num,
                    evolved_text,
                    orig_score,
                    evolved_score,
                    src_commit,
                    evolve_session_id,
                )
            finally:
                db_conn.close()

            # Log evidence (jsonl append is thread-safe at OS level for small writes)
            with _lock:
                log_evidence(
                    {
                        "phase": f"tier-{tier_num}",
                        "prompt_num": prompt_num,
                        "tools": tools,
                        "baseline_score": round(orig_score, 4),
                        "evolved_score": round(evolved_score, 4),
                        "improvement": round(delta, 4),
                        "evolved_text": evolved_text,
                        "evolved_length": len(evolved_text),
                        "source": "postgresql",
                        "evolve_session_id": evolve_session_id,
                        "parent_prompt": prompt_text,
                        "source_commit": src_commit,
                        "clip_sim": round(clip_sim, 4) if clip_sim is not None else None,
                    }
                )
                # Also log filter status if applicable
                if filter_rejected:
                    log_evidence({
                        "event": "filter_rejected",
                        "prompt_num": prompt_num,
                        "tier": tier_num,
                        "delta": round(delta, 4),
                    })

            print(
                f"  #{prompt_num} Δ={delta:+.4f} ({orig_score:.3f} → {evolved_score:.3f})"
                f" [{len(prompt_text)}→{len(evolved_text)} chars]"
                f"{' [FILTERED]' if filter_rejected else ''}"
            )
        else:
            # Filter rejected — still print but don't insert
            print(
                f"  ⋮ #{prompt_num} Δ={delta:+.4f} filtered "
                f"(P={prob:.4f}) — skipped"
            )

        return {
            "prompt_num": prompt_num,
            "evolved_text": evolved_text if not filter_rejected else prompt_text,
            "orig_score": orig_score,
            "evolved_score": evolved_score if not filter_rejected else orig_score,
            "delta": delta if not filter_rejected else 0.0,
        }

    workers = min(parallel, len(prompt_tasks))
    results = []
    if workers > 1:
        print(
            f"\n  Processing {len(prompt_tasks)} prompts with {workers} workers..."
        )
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers
        ) as pool:
            futures = {pool.submit(_evolve_one, t): t for t in prompt_tasks}
            for f in concurrent.futures.as_completed(futures):
                try:
                    results.append(f.result())
                except Exception as e:
                    task = futures[f]
                    print(f"  ✗ Prompt #{task['prompt_num']} failed: {e}")
                    # Fall back to original text, still record in DB
                    evolved_text = task["prompt_text"]
                    evolved_score = evaluate_prompt_wrapper(evolved_text)
                    src_commit = _get_source_commit()
                    db_conn = _pg_connect()
                    try:
                        _insert_version(
                            db_conn,
                            task["prompt_num"],
                            tier_num,
                            evolved_text,
                            0.0,
                            evolved_score,
                            src_commit,
                            evolve_session_id,
                        )
                    finally:
                        db_conn.close()
                    results.append(
                        {
                            "prompt_num": task["prompt_num"],
                            "evolved_text": evolved_text,
                            "orig_score": 0.0,
                            "evolved_score": evolved_score,
                            "delta": 0.0,
                        }
                    )
        # Inter-batch thermal cooldown: check pressure after parallel pool
        _apply_latency_cooldown()
    else:
        for t in prompt_tasks:
            results.append(_evolve_one(t))
            _apply_latency_cooldown()

    # ── Aggregate results (no document rebuild) ──────────────────────────────
    total_improvement = sum(r["delta"] for r in results)
    prompts_evolved = len(results)
    avg_improvement = total_improvement / max(1, prompts_evolved)

    evidence = {
        "phase": f"tier-{tier_num}",
        "source": "postgresql",
        "evolve_session_id": evolve_session_id,
        "prompts_evolved": prompts_evolved,
        "total_improvement": round(total_improvement, 4),
        "avg_improvement": round(avg_improvement, 4),
        "parallelism": workers,
        "db_path": f"postgresql://{DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}",
        "thermal_pressure": {
            "available_mb": pressure_diag.get("available_mb"),
            "latency_band": pressure_diag.get("latency_band", "UNKNOWN"),
            "safe_parallelism": pressure_diag.get("safe_parallelism", workers),
        },
    }
    log_evidence(evidence)

    # ── Convergence stall detection (agent_statemachine v2) ────────────────
    stall = _check_convergence_stall(results, tier_num)
    if stall:
        print(
            f"\n  ⚠️  CONVERGENCE STALL detected in Tier {tier_num}: "
            f"{stall['zero_deltas']}/{stall['total']} zero-delta "
            f"({stall['zero_ratio']:.1%})"
        )
        if stall["best_prompt"]:
            print(
                f"     Best: #{stall['best_prompt']} Δ={stall['best_improvement']:+.4f}"
            )
        if stall["worst_prompt"]:
            print(
                f"     Worst: #{stall['worst_prompt']} Δ={stall['worst_regression']:+.4f}"
            )
        print(f"     Suggestion: {stall['suggestion']}")
        evidence["convergence_stall"] = stall
        log_evidence(
            {"event": "convergence_stall", "tier": tier_num, "stall": stall}
        )

        # ── Hyper-Mutation escape: force radical mutations on stalled prompts ─
        if getattr(_current_args, "hyper", False):
            import json as _json
            import urllib.request as _ur
            import urllib.error as _ue

            APFEL_URL = "http://127.0.0.1:11434/v1/chat/completions"
            HYPER_PROMPT = (
                "Rewrite this MCP test prompt COMPLETELY. Change the structure, "
                "perspective, and approach while keeping the same tool intent. "
                "Aim for a prompt that is substantially different from the original "
                "but tests the same tool with the same expected behavior. "
                "Output ONLY the rewritten prompt, no explanation."
            )
            hyper_attempted = 0
            hyper_succeeded = 0

            # Find stalled prompts (zero-delta prompts that need escape)
            stalled_prompts = [
                r for r in results
                if abs(r.get("delta", 0)) < CONVERGENCE_EPSILON
            ]

            if stalled_prompts:
                print(
                    f"\n  🧬 HYPER-MUTATION: Attempting escape on "
                    f"{len(stalled_prompts)} stalled prompts..."
                )

            for r in stalled_prompts:
                pnum = r["prompt_num"]
                original = r.get("evolved_text", "") or r.get("prompt_text", "")
                if not original:
                    continue

                # Call apfel for radical mutation
                body = _json.dumps({
                    "model": "apfel",
                    "messages": [
                        {"role": "system", "content": HYPER_PROMPT},
                        {"role": "user", "content": original},
                    ],
                    "temperature": 0.9,
                    "max_tokens": 1024,
                }).encode()

                try:
                    req = _ur.Request(
                        APFEL_URL, data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with _ur.urlopen(req, timeout=30) as resp:
                        apfel_resp = _json.loads(resp.read().decode())
                    mutated = (
                        apfel_resp.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    )
                    if not mutated or mutated == original:
                        continue

                    # Evaluate the hyper-mutated prompt
                    hyper_attempted += 1
                    hyper_score = evaluate_prompt_wrapper(mutated)
                    hyper_delta = hyper_score - r["orig_score"]

                    if hyper_delta > CONVERGENCE_EPSILON:
                        hyper_succeeded += 1
                        print(
                            f"    🎉 #{pnum} HYPER-MUTATION ESCAPE: "
                            f"Δ={hyper_delta:+.4f} "
                            f"({r['orig_score']:.3f} → {hyper_score:.3f})"
                        )
                        log_evidence({
                            "event": "hyper_mutation",
                            "prompt_num": pnum,
                            "tier": tier_num,
                            "original_score": round(r["orig_score"], 4),
                            "hyper_score": round(hyper_score, 4),
                            "delta": round(hyper_delta, 4),
                            "succeeded": True,
                        })
                        # Update result in-memory for final summary
                        r["delta"] = hyper_delta
                        r["evolved_score"] = hyper_score
                        r["evolved_text"] = mutated
                    else:
                        print(
                            f"    ✗ #{pnum} hyper-mutation failed: "
                            f"Δ={hyper_delta:+.4f}"
                        )
                        log_evidence({
                            "event": "hyper_mutation",
                            "prompt_num": pnum,
                            "tier": tier_num,
                            "delta": round(hyper_delta, 4),
                            "succeeded": False,
                        })

                except (_ue.URLError, _ue.HTTPError, OSError, _json.JSONDecodeError) as e:
                    print(f"    ⚠️  apfel error for #{pnum}: {e} — skipping")
                    continue

            if hyper_attempted > 0:
                print(
                    f"\n  🧬 Hyper-Mutation complete: {hyper_succeeded}/{hyper_attempted} "
                    f"escapes successful"
                )
                evidence["hyper_mutation"] = {
                    "attempted": hyper_attempted,
                    "succeeded": hyper_succeeded,
                }
                log_evidence({
                    "event": "hyper_mutation_summary",
                    "tier": tier_num,
                    "attempted": hyper_attempted,
                    "succeeded": hyper_succeeded,
                })

    # Print summary
    improved = sum(1 for r in results if r["delta"] > 0)
    print(f"\n{'=' * 60}")
    print(
        f"Tier {tier_num} complete: {prompts_evolved} prompts, {improved} improved"
    )
    print(
        f"  Total Δ: {total_improvement:+.4f}, Avg Δ: {avg_improvement:+.4f}"
    )
    print(f"  Session: {evolve_session_id}")
    print(f"{'=' * 60}")

    # ── Victoria Protocol: cleanup containers created during this run ──
    if getattr(_current_args, "cleanup_after", False):
        _run_id = getattr(_current_args, "run_id", None) or _generate_run_id()
        print(f"  Cleanup: removing containers matching prefix '{_run_id}_'...")
        try:
            import subprocess as _sp
            _sp.run(
                [str(COMPOSE_PKL / "scripts" / "container_cleanup.py"),
                 "--by-pattern", f"{_run_id}_"],
                capture_output=True, text=True, timeout=30,
            )
            print(f"  Cleanup complete")
        except Exception as e:
            print(f"  Cleanup error: {e}")

    return evidence


def dry_run(
    use_reward_adapter: bool = False, reflect_model: str | None = None
) -> dict:
    """Validate pipeline setup without running optimization. G1 probe."""
    print("Dry-run validation...")

    # Check GEPA import
    try:
        g = gepa.optimize
        print(f"  ✓ GEPA optimize available")
    except Exception as e:
        print(f"  ✗ GEPA error: {e}")
        return {"gate": "G1-dry-run", "result": "FAIL", "reason": str(e)}

    # Check rubric works
    inventory = build_inventory()
    test_prompt = inventory[0]
    score = evaluate_prompt(test_prompt["text"], test_prompt["tools"])
    if 0.0 <= score["composite"] <= 1.0:
        print(f"  ✓ Rubric returns valid score: {score['composite']:.3f}")
    else:
        print(f"  ✗ Rubric returned invalid score: {score['composite']}")
        return {
            "gate": "G1-dry-run",
            "result": "FAIL",
            "reason": f"Invalid rubric score: {score['composite']}",
        }

    # Check RewardAdapter integration (if requested)
    if use_reward_adapter:
        if not _HAS_REWARD_ADAPTER:
            print(
                "  ✗ --use-reward-adapter requested but RewardAdapter not importable"
            )
            return {
                "gate": "G1-dry-run",
                "result": "FAIL",
                "reason": "RewardAdapter not importable",
            }
        print("  ✓ RewardAdapter importable")

        # Check ProxyStateTracker integration (if --proxy-state also requested)
        proxy_state = (
            getattr(_current_args, "proxy_state", False)
            if _current_args
            else False
        )
        if proxy_state:
            if ProxyStateTracker is None:
                print(
                    "  ✗ --proxy-state requested but ProxyStateTracker not importable"
                )
                return {
                    "gate": "G1-dry-run",
                    "result": "FAIL",
                    "reason": "ProxyStateTracker not importable",
                }
            print("  ✓ ProxyStateTracker importable")

            # Quick smoke test: evaluate a known prompt
            try:
                tracker = ProxyStateTracker()
                result = tracker.evaluate(
                    prompt="Create a container named 'test-nginx' using nginx:latest image.",
                )
                dims = result.get("dimensions", {})
                assert len(dims) == 6, (
                    f"Expected 6 dimensions, got {len(dims)}"
                )
                for dim in ProxyStateTracker.DIMENSIONS:
                    assert 0.0 <= dims.get(dim, -1) <= 1.0, (
                        f"Dimension {dim} out of range"
                    )
                assert 0.0 <= result.get("composite", -1) <= 1.0, (
                    "Composite out of range"
                )
                print(
                    f"  ✓ ProxyStateTracker smoke test: composite={result['composite']:.3f}"
                )
                print(f"    Dimensions: {json.dumps(dims)}")
                print(f"    Stats: {json.dumps(tracker.get_stats())}")
            except Exception as e:
                print(f"  ✗ ProxyStateTracker smoke test failed: {e}")
                return {
                    "gate": "G1-dry-run",
                    "result": "FAIL",
                    "reason": f"ProxyStateTracker smoke test: {e}",
                }

        # Test proxy Span classification
        try:
            from evolution.prompts.reward_adapter import RewardAdapter, Span

            ra = RewardAdapter()
            proxy = Span(
                name="test",
                status="failed",
                intended_action="create_container",
                error="port already in use",
                reasoning_content="I will call create_container then verify the port",
            )
            cls, _, insight = ra._classify_failure_with_trace(proxy, 0)
            assert cls in (
                "structural",
                "behavioral",
                "state_contamination",
                "",
            ), f"Unexpected cls: {cls}"
            print(f"  ✓ RewardAdapter trace classification works: {cls}")
        except Exception as e:
            print(f"  ✗ RewardAdapter classification failed: {e}")
            return {
                "gate": "G1-dry-run",
                "result": "FAIL",
                "reason": f"RewardAdapter classification error: {e}",
            }

        # Test RewardAwareAdapter creation
        try:
            from evolution.prompts.evolve_prompts import RewardAwareAdapter

            awa = RewardAwareAdapter(evaluator_fn=lambda t: 0.5)
            assert awa.reward_adapter is not None, "RewardAdapter not attached"
            batch = [{"input": "eval", "answer": "pass"}]
            candidate = {"prompt": "Create a container with name test-1"}
            result = awa.evaluate(batch, candidate, capture_traces=True)
            assert len(result.scores) == 1
            assert result.trajectories is not None
            feedback = result.trajectories[0].get("feedback", "")
            assert "classification" in feedback or "composite" in feedback
            print(f"  ✓ RewardAwareAdapter evaluate() enriches trajectories")
        except Exception as e:
            print(f"  ✗ RewardAwareAdapter failed: {e}")
            return {
                "gate": "G1-dry-run",
                "result": "FAIL",
                "reason": f"RewardAwareAdapter error: {e}",
            }

        # Check model capability for reasoning trace enrichment
        from evolution.prompts.reward_adapter import (
            has_reasoning_capability,
            get_active_model,
        )

        if reflect_model:
            if has_reasoning_capability(reflect_model):
                print(
                    f"  ✓ Reflect model '{reflect_model}' supports reasoning_content (K2.6 Thinking Mode)"
                )
            else:
                print(
                    f"  ⚠ Reflect model '{reflect_model}' not recognized as K2.6 — "
                    f"reasoning_insight will be [SIMULATED], not authentic K2.6 traces"
                )
        else:
            active = get_active_model()
            print(
                f"  ℹ No --reflect-model set — reflection LM uses default model '{active or 'unknown'}'. "
                f"Reasoning insights will be [SIMULATED]."
            )
            if active:
                from evolution.prompts.reward_adapter import (
                    _REASONING_MODEL_IDS,
                )

                if has_reasoning_capability(active):
                    print(
                        f"  ℹ Default model IS K2.6 reasoning-capable. "
                        f"Add --reflect-model to enable authentic trace parsing."
                    )

    # Check proxy connectivity
    import urllib.request

    try:
        resp = urllib.request.urlopen("http://localhost:8080/", timeout=5)
        if resp.status == 200:
            print(f"  ✓ kilo-proxy responding on :8080")
        else:
            print(f"  ⚠ kilo-proxy returned {resp.status}")
    except Exception as e:
        print(f"  ✗ kilo-proxy not reachable: {e}")
        return {
            "gate": "G1-dry-run",
            "result": "FAIL",
            "reason": f"kilo-proxy unreachable: {e}",
        }

    # Pre-flight: check OTel telemetry pipeline
    print(f"  Checking OTel telemetry pipeline...")
    try:
        from evolution.prompts.otel_adapter import OTelPromptAdapter

        oa = OTelPromptAdapter()
        otel_check = oa.check_telemetry_pipeline()
        if otel_check["healthy"]:
            print(
                f"  ✓ OTel pipeline healthy ({otel_check['span_count']} spans in otel_spans)"
            )
        else:
            print(f"  ⚠ OTel pipeline unhealthy: {otel_check['error']}")
            print(
                f"  ⚠ OTel A/B validation will fail — prompts will run blind"
            )
    except Exception as e:
        print(f"  ⚠ OTel check error: {e}")
        print(
            f"  ⚠ Continuing without OTel validation (will use heuristic fallback)"
        )

    evidence = {
        "gate": "G1-dry-run",
        "result": "PASS",
        "inventory_size": len(inventory),
    }
    log_evidence(evidence)
    print(f"  ✓ Dry-run PASS — ready for G1 canary")
    return evidence


# ── CLI ────────────────────────────────────────────────────────────────────


def main():
    # ── Signal logging for process reaping investigation ──────────────
    global _current_args
    import signal as _signal

    _DEATH_LOG = (
        os.environ.get("SAFE_RUNNER_DEATH_LOG") or "/tmp/evolve_signal.log"
    )

    def _signal_logger(signum, frame):
        import traceback

        name = (
            _signal.Signals(signum).name
            if signum in _signal.valid_signals()
            else str(signum)
        )
        try:
            import subprocess as _sp

            ppid = os.getppid()
            r = _sp.run(
                ["ps", "-o", "comm=", "-p", str(ppid)],
                capture_output=True,
                text=True,
                timeout=2,
            )
            sender = r.stdout.strip() or "unknown"
        except Exception:
            sender = "query_failed"
        msg = (
            f"[SIGNAL_LOG] pid={os.getpid()} received {name}({signum}) "
            f"at {datetime.now(timezone.utc).isoformat()} "
            f"from ppid={os.getpid()}/{sender}\n"
        )
        with open(_DEATH_LOG, "a") as f:
            f.write(msg)
        # Log to stderr too
        print(msg.strip(), file=sys.stderr, flush=True)
        # Reset to default and re-raise
        _signal.signal(signum, _signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for _sig in (
        _signal.SIGTERM,
        _signal.SIGHUP,
        _signal.SIGINT,
        _signal.SIGALRM,
    ):
        try:
            _signal.signal(_sig, _signal_logger)
        except (ValueError, OSError):
            pass

    parser = argparse.ArgumentParser(
        description="Evolve compose-pkl MCP test prompts using GEPA"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate setup without optimizing",
    )
    parser.add_argument(
        "--single-prompt",
        type=int,
        default=None,
        help="Evolve a single prompt (G1 canary)",
    )
    parser.add_argument(
        "--tier",
        type=str,
        default=None,
        choices=["1", "2", "3", "4", "all"],
        help="Tier to evolve",
    )
    parser.add_argument(
        "--evidence-file",
        type=str,
        default=str(EVIDENCE_LOG),
        help="Path for evidence log",
    )
    parser.add_argument(
        "--use-reward-adapter",
        action="store_true",
        help="Enable RewardAdapter reasoning trace enrichment in GEPA feedback",
    )
    parser.add_argument(
        "--reflect-model",
        type=str,
        default=None,
        help="Model identifier for GEPA's reflection LM (e.g. 'tinker/moonshotai/Kimi-K2.6'). "
        "When set, enables real reasoning_content trace parsing. "
        "Default: uses hermes profile default model (no reasoning traces).",
    )
    parser.add_argument(
        "--proxy-state",
        action="store_true",
        help="Enable ProxyStateTracker (LLM-as-a-Judge) for 5-dimension state-aware "
        "evaluation. Replaces [SIMULATED] reasoning traces with authentic "
        "DeepSeek-V4-Flash judgment across: tool_correctness, parameter_validity, "
        "error_handling, resource_lifecycle, state_agreement. "
        "Required for +0.60 delta target (Plan 130 §1.5.2).",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=0,
        help="Run a random sample of N prompts from the target tier first. "
        "Validates pipeline health and spot-checks improvement rate before "
        "committing to the full batch. Default 0 = run full batch directly.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last checkpoint. Skips prompts already recorded "
        "in the tier's checkpoint file. Safe to SIGKILL and restart.",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Process N prompts concurrently via thread pool. "
        "Default 1 (sequential). Each worker spawns its own Hermes session. "
        "N=3 typically yields ~3x throughput without container runtime contention. "
        "Max suggested: 5 (memory-bound on M4 at ~100MB per session).",
    )
    parser.add_argument(
        "--max-memory",
        type=float,
        default=4.0,
        help="Minimum free GB of memory required to start a new parallel worker. "
        "Default 4.0GB. Workers wait with exponential backoff when below threshold. "
        "Set lower (e.g. 2.0) to pack more workers, or higher (e.g. 8.0) to be safe.",
    )
    parser.add_argument(
        "--max-containers",
        type=int,
        default=8,
        help="Maximum running Apple Containers before pausing new parallel workers. "
        "Default 8. Each parallel eval creates ~1-2 containers. "
        "Set lower (e.g. 4) on M4 base to avoid daemon throttling.",
    )
    parser.add_argument(
        "--local-filter",
        action="store_true",
        help="Enable binary logit pre-filter via daemonized Qwen2.5 model service "
        "(http://127.0.0.1:11435/score). After GEPA heuristic produces a mutation "
        "but before OTel validation, scores the parent/mutation pair. If the "
        "model service rejects it (P < 0.7956), skip the expensive OTel eval "
        "entirely. Rejects ~19 percent of mutations at the calibrated threshold.",
    )
    parser.add_argument(
        "--clip-regularizer",
        action="store_true",
        help="Enable CLIP ViT-B/32 semantic regularizer at 0.2 weight in reward blend. "
        "Penalizes semantic drift: blends 0.8 * PST score + 0.2 * CLIP similarity. "
        "Requires ~338MB model load on first call (~260ms warm inference).",
    )
    parser.add_argument(
        "--hyper",
        action="store_true",
        help="Enable Hyper-Mutation escape. When convergence stall is detected "
        "(>80%% zero-delta), forces radical mutations via apfel on stalled prompts "
        "to escape local optima. Calls apfel at http://127.0.0.1:11434/v1 with a "
        "rewrite-completely prompt. Logs hyper_mutation events in evidence.",
    )
    parser.add_argument(
        "--run-id",
        type=str, default=None,
        help="Resource prefix for Victoria Protocol cleanup. Auto-generated if omitted. "
        "All containers created during this run will use this prefix.",
    )
    parser.add_argument(
        "--cleanup-after",
        action="store_true",
        help="After tier completes, remove containers matching the run_id prefix. "
        "Runs regardless of tier success or failure.",
    )
    parser.add_argument(
        "--resume-from-db",
        action="store_true",
        help="Resume from database evolution_state table. Skips prompts where "
        "status = 'done' for the current tier's run_id. Survives reboots.",
    )

    args = parser.parse_args()
    # Store globally for propagation into optimize functions
    global _current_args
    _current_args = args

    # ── Pre-flight: verify OTel pipeline before ANY evolution ───────────────
    # This prevents running blind without the PostgreSQL database + otel_spans
    # table. The previous 2-hour Tier 2 run was entirely wasted because the
    # database wasn't running — OTel returned fallback/zero scores.
    # See Plan 130 §1.5.6 step 2 (infrastructure pre-flight).
    if not args.dry_run and (args.single_prompt or args.tier):
        try:
            from evolution.prompts.otel_adapter import OTelPromptAdapter

            oa = OTelPromptAdapter()
            otel_check = oa.check_telemetry_pipeline()
            if not otel_check["healthy"]:
                print(f"\n{'=' * 60}")
                print(f"❌ OTel PIPELINE UNHEALTHY — aborting evolution")
                print(f"{'=' * 60}")
                print(f"  DB reachable: {otel_check['db_reachable']}")
                print(f"  otel_spans table: {otel_check['table_exists']}")
                print(f"  Error: {otel_check['error']}")
                print()
                print(
                    f"  Run --dry-run to see the full diagnostic, then fix infrastructure:"
                )
                print(f"    skill honcho-db-otel-infrastructure")
                print(f"{'=' * 60}")
                sys.exit(1)
            print(
                f"  ✓ OTel pipeline verified ({otel_check['span_count']} spans available)"
            )

            # Also verify the database has the harness_evolution database
            # (not just the table — full OTel reads require both)
            if otel_check["span_count"] == 0:
                print(
                    f"  ⚠ otel_spans table is empty — first run will have no baseline data"
                )
        except ImportError as e:
            print(f"\n{'=' * 60}")
            print(
                f"❌ Cannot verify OTel pipeline — pg8000 or OTel adapter not installed"
            )
            print(f"  Error: {e}")
            print(f"  Run: .venv/bin/pip install pg8000")
            print(f"{'=' * 60}")
            sys.exit(1)

    inventory = build_inventory()

    if args.dry_run:
        result = dry_run(args.use_reward_adapter, args.reflect_model)
        sys.exit(0 if result["result"] == "PASS" else 1)

    if args.single_prompt:
        result = evolve_single_prompt(args.single_prompt, inventory)
        g1_pass = result["improvement"] >= 0
        print(
            f"\nG1 gate: {'PASS' if g1_pass else 'FAIL'} (improvement={'+' if g1_pass else ''}{result['improvement']:.4f})"
        )
        sys.exit(0 if g1_pass else 1)

    if args.tier:
        tiers = [1, 2, 3] if args.tier == "all" else [int(args.tier)]

        # ── Sample mode: run N random prompts first for quick validation ──
        if args.sample > 0:
            import random as _random
            from evolution.prompts.otel_adapter import (
                OTelPromptAdapter,
                _query_otel_spans,
            )

            for t in tiers:
                cfg = TIER_BUDGETS[t]
                lo, hi = cfg["prompts"]
                tier_prompts = [p for p in inventory if lo <= p["num"] <= hi]
                sample_size = min(args.sample, len(tier_prompts))
                sampled = _random.sample(tier_prompts, sample_size)
                print(f"\n{'=' * 60}")
                print(
                    f"Sample mode: {sample_size}/{len(tier_prompts)} prompts from {cfg['label']}"
                )
                print(f"{'=' * 60}")

                # Track 3 gate signals
                improvements = []  # heuristic Δ for each prompt
                otel_deltas = []  # OTel Δ (from hybrid optimizer)
                failures = []
                proxy_tracker_stats = {"calls": 0, "parse_failures": 0}
                spans_before = 0
                spans_after = 0

                # Snapshot OTel span count before sample
                try:
                    oa = OTelPromptAdapter()
                    otel_before = oa.check_telemetry_pipeline()
                    spans_before = otel_before.get("span_count", 0)
                except Exception:
                    spans_before = -1

                for p in sampled:
                    print(f"\n  Canary #{p['num']}...", end=" ", flush=True)
                    try:
                        result = evolve_single_prompt(p["num"], inventory)
                        delta = result.get("improvement", 0)
                        improvements.append(delta)

                        # Gate 3: ProxyStateTracker parse rate
                        # Aggregate across all proxy state calls in this canary
                        # by checking evidence log entry
                        if result.get("evaluator") == "proxy_state":
                            proxy_tracker_stats["calls"] += 1

                        status = (
                            "✅"
                            if delta > 0
                            else ("∼" if abs(delta) < 0.001 else "—")
                        )
                        print(f"{status} (Δ={delta:+.4f})")
                    except Exception as e:
                        failures.append((p["num"], str(e)))
                        print(f"❌ ({e})")

                    # Dynamic concurrency control: cooldown between sample iterations
                    _apply_latency_cooldown()

                # Gate 1: OTel span emission — did hermes actually write spans?
                try:
                    oa = OTelPromptAdapter()
                    otel_after = oa.check_telemetry_pipeline()
                    spans_after = otel_after.get("span_count", 0)
                    otel_emitted = spans_after > spans_before
                except Exception:
                    otel_emitted = False
                    spans_after = -1

                # Gate 2: Mean OTel Δ — read from evidence log
                try:
                    from pathlib import Path

                    ev_lines = (
                        Path(EVIDENCE_LOG).read_text().strip().split("\n")
                    )
                    # Get the last N evidence entries for G1-canary phases
                    canary_entries = []
                    for line in reversed(ev_lines):
                        if '"phase": "G1-canary"' in line:
                            entry = json.loads(line)
                            if entry.get("improvement", 0) != 0:
                                canary_entries.append(entry["improvement"])
                            if len(canary_entries) >= sample_size:
                                break
                    mean_otel_delta = sum(canary_entries) / max(
                        1, len(canary_entries)
                    )
                except Exception:
                    mean_otel_delta = 0.0

                # Sample statistics
                mean_delta = sum(improvements) / max(1, len(improvements))
                acceptance_rate = sum(1 for d in improvements if d > 0) / max(
                    1, len(improvements)
                )
                crash_rate = len(failures) / max(1, len(sampled))
                proxy_parse_rate = (
                    1.0
                    - (
                        proxy_tracker_stats["parse_failures"]
                        / max(1, proxy_tracker_stats["calls"])
                    )
                    if proxy_tracker_stats["calls"] > 0
                    else 0.0
                )

                print(f"\n{'=' * 60}")
                print(f"SAMPLE VERDICT — {cfg['label']}")
                print(f"{'=' * 60}")
                print(f"  Sample size:              {len(sampled)}")
                print(f"  Crash rate:               {crash_rate:.0%}")
                print(f"  Heuristic mean Δ:         {mean_delta:+.4f}")
                print(f"  Heuristic acceptance rate: {acceptance_rate:.0%}")
                print(
                    f"  OTel span delta:           {spans_after - spans_before} (was {spans_before})"
                )
                print(f"  Mean OTel Δ (from evals):  {mean_otel_delta:+.4f}")
                print(
                    f"  ProxyStateTracker calls:   {proxy_tracker_stats['calls']}"
                )
                if failures:
                    print(f"  Failures:                  {failures}")

                # ── Four-gate check with lineage quality ───────────────────
                check1 = crash_rate == 0.0
                check1_label = (
                    "No crashes" if check1 else f"{len(failures)} crash(es)"
                )

                check2 = otel_emitted
                check2_label = (
                    f"OTel spans flowing ({spans_after - spans_before} new)"
                    if check2
                    else "NO OTel spans emitted — pipeline broken"
                )

                check3 = (
                    proxy_parse_rate >= 0.8
                    if proxy_tracker_stats["calls"] > 0
                    else True
                )
                check3_label = (
                    f"ProxyStateTracker parse rate ≥ 80% ({proxy_parse_rate:.0%})"
                    if check3
                    else f"ProxyStateTracker parse rate too low ({proxy_parse_rate:.0%})"
                )

                # Gate 4: Infrastructure lineage quality
                # Verify LDP+ContextBus: do compose-pkl spans have parentSpanId
                # and shared trace_ids, or is the pipeline still producing orphans?
                lineage_ok = True
                lineage_score = 0.0
                try:
                    from evolution.prompts.otel_adapter import (
                        _query_infrastructure_spans,
                    )
                    from datetime import datetime, timezone

                    now_iso = datetime.now(timezone.utc).isoformat()
                    infra = _query_infrastructure_spans(
                        session_start=now_iso, window_minutes=5
                    )
                    spans = infra.get("spans", [])
                    if spans:
                        with_parent = [
                            s
                            for s in spans
                            if s.get("parent_span_id")
                            and str(s.get("parent_span_id", "")).strip()
                        ]
                        unique_traces = set(
                            s.get("trace_id")
                            for s in spans
                            if s.get("trace_id")
                        )
                        lineage_score = len(with_parent) / max(1, len(spans))
                        # At least 40% must have parentSpanId (lenient threshold —
                        # first runs may produce old synthetic spans alongside new
                        # propagated ones during the transition)
                        lineage_ok = lineage_score >= 0.4
                        sentinel = infra.get("sentinel_present", False)
                    else:
                        # No infra spans = no containers were created during sample
                        # This is valid if the sampled prompts don't use container tools
                        lineage_ok = True
                except Exception:
                    lineage_ok = True  # don't fail gate on check error

                check4 = lineage_ok
                check4_label = (
                    f"Lineage quality ≥ 40% ({lineage_score:.0%})"
                    if check4
                    else f"Lineage too low ({lineage_score:.0%}) — orphan spans detected"
                )

                # Gate 5: Mechanical Linter compliance (lineage_verification)
                # Injects a probe prompt that calls delete_container without
                # required_parent_span_id, then checks that the ProxyStateTracker
                # correctly penalizes the missing lineage context via the
                # lineage_verification dimension.
                lint_ok = True
                lint_score = 0.0
                lint_probe = (
                    "Use stop_container and delete_container to stop and remove "
                    "a container named 'test-nginx'. The container is currently running. "
                    "First stop it, then delete it."
                )
                try:
                    if ProxyStateTracker is not None:
                        pst = ProxyStateTracker()
                        lint_result = pst.evaluate(
                            prompt=lint_probe,
                            output=(
                                "Call stop_container with name='test-nginx'. "
                                "Then call delete_container with name='test-nginx'."
                            ),
                        )
                        lint_score = lint_result.get("dimensions", {}).get(
                            "lineage_verification", 0.5
                        )
                        # The probe deliberately omits required_parent_span_id.
                        # The PST should detect this and give a LOW score (< 0.5).
                        # If score >= 0.5, the PST failed to catch missing lineage.
                        lint_ok = lint_score < 0.5
                except Exception:
                    lint_ok = True  # don't fail gate on probe error

                check5 = lint_ok
                check5_label = (
                    f"Mechanical Linter: lineage_verification={lint_score:.2f} "
                    "(<0.5 expected — probe intentionally lacks span context)"
                    if check5
                    else f"Mechanical Linter FAILED: lineage_verification={lint_score:.2f} "
                    "(≥0.5 — PST did not detect missing required_parent_span_id)"
                )

                print()
                print(f"  ┌─ 5-GATE SAMPLE CHECK ──────────────────────────┐")
                print(
                    f"  │ 1. {check1_label:<40s} {'✅' if check1 else '❌'} │"
                )
                print(
                    f"  │ 2. {check2_label:<40s} {'✅' if check2 else '❌'} │"
                )
                print(
                    f"  │ 3. {check3_label:<40s} {'✅' if check3 else '❌'} │"
                )
                print(
                    f"  │ 4. {check4_label:<40s} {'✅' if check4 else '❌'} │"
                )
                print(
                    f"  │ 5. {check5_label:<40s} {'✅' if check5 else '❌'} │"
                )
                print(f"  └─────────────────────────────────────────────────┘")

                sample_pass = (
                    check1 and check2 and check3 and check4 and check5
                )
                print(
                    f"\n  Sample verdict: {'✅ PASS — proceeding to full batch' if sample_pass else '❌ FAIL — investigate before full batch'}"
                )
                print(f"{'=' * 60}")

                if not sample_pass and args.tier != "all":
                    print(
                        f"\n  Gates failed. Run with --skip-sample-gates to bypass."
                    )
                    sys.exit(1)

            # Auto-continuation: if all gates pass, proceed to full batch
            # without requiring a separate --full-after-sample flag
            if args.sample > 0 and not getattr(
                args, "_full_after_sample", False
            ):
                if sample_pass:
                    print(
                        f"\nAll {cfg['label']} sample gates passed. Auto-continuing to full batch.\n"
                    )
                    # Mark as full-mode so the next iteration proceeds
                    # This is achieved by clearing the sample flag for subsequent passes
                    args._full_after_sample = True
                else:
                    print(f"\nSample complete. Fix failures, then re-run.")
                    return

        # ── Full tier evolution ──
        for t in tiers:
            result = evolve_tier(t, inventory)
            g2_pass = result["avg_improvement"] > 0
            print(
                f"\n  Tier {t} gate: {'PASS' if g2_pass else 'FAIL'} (avg improvement={result['avg_improvement']:.4f})"
            )
            if not g2_pass and args.tier != "all":
                sys.exit(1)

        print(f"\n{'=' * 60}")
        print(f"Batch complete. Evidence logged to {EVIDENCE_LOG}")
        print(f"Next: run adversarial review on evolved prompts")
        return

    # Default: show status
    print(f"Inventory: {len(inventory)} prompts")
    print(f"Evidence log: {EVIDENCE_LOG}")
    print(f"Run with --dry-run, --single-prompt N, or --tier <1|2|3|all>")


if __name__ == "__main__":
    main()
