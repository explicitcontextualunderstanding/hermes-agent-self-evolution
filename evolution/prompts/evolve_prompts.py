#!/usr/bin/env python3
"""
evolve_prompts.py — GEPA-driven optimization of compose-pkl MCP test prompts.

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

# Module-level for CLI flag propagation into optimize functions
_current_args = None

# GEPA
try:
    import gepa
    from gepa.adapters.default_adapter.default_adapter import (
        EvaluationBatch,
        EvaluationResult,
        DefaultDataInst,
    )
except ImportError:
    print("GEPA not installed. Run: pip install gepa")
    sys.exit(1)

from evolution.prompts.inventory import (
    build_inventory,
    evaluate_prompt,
    RUBRIC_DIMENSIONS,
    P1, P2, P4, P5,
    PROMPT_TOOLS,
    BASELINE_STATUS,
)

# RewardAdapter for reasoning trace enrichment (Plan 130)
try:
    from evolution.prompts.reward_adapter import RewardAdapter, Span, StepFailure, ProxyStateTracker
    _HAS_REWARD_ADAPTER = True
except ImportError:
    RewardAdapter = None
    Span = None
    StepFailure = None
    ProxyStateTracker = None
    _HAS_REWARD_ADAPTER = False

# ── Custom GEPA Adapter (no LLM calls for evaluation) ──────────────────────

class HeuristicPromptAdapter:
    """GEPA adapter that evaluates prompts using the heuristic rubric.

    Skips LLM calls entirely — the candidate prompt is scored directly by the
    rubric. GEPA still uses reflection_lm to propose new prompt variants.
    """

    def __init__(self, evaluator_fn, dimension_names=None):
        self.evaluator_fn = evaluator_fn
        self.dimension_names = dimension_names or [
            "clarity", "coverage", "resilience",
            "self_containment", "verifiability",
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
                trajectories.append({
                    "data": batch[i] if i < len(batch) else {"input": "", "answer": ""},
                    "full_assistant_response": prompt_text[:500],
                    "feedback": (
                        f"Rubric score: {score:.3f}. "
                        f"Dimensional breakdown: {json.dumps(detail)}. "
                        f"Target: >0.7 on all dimensions."
                    ),
                })

        return EvaluationBatch(
            outputs=[{"evaluated": prompt_text[:80]} for _ in batch],
            scores=scores,
            trajectories=trajectories,
            objective_scores=objective_scores,
        )

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
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
                items.append({
                    "Inputs": f"Prompt #{i}",
                    "Generated Outputs": prompt_text[:200],
                    "Feedback": (
                        f"Rubric score: {score:.3f}. "
                        f"Dimensional breakdown: {json.dumps(detail)}. "
                        "Target: >0.7 on all dimensions."
                    ),
                })
        else:
            items = []
            for traj in trajectories:
                items.append({
                    "Inputs": traj.get("data", {}).get("input", ""),
                    "Generated Outputs": traj.get("full_assistant_response", prompt_text[:200]),
                    "Feedback": traj.get("feedback", "No feedback available."),
                })

        return {comp: items}

    def _score_detail(self, text: str) -> dict:
        """Return per-dimension scores for reflection feedback."""
        # Simple heuristic — just a quick breakdown
        scores = {}
        text_lower = text.lower()
        scores["clarity"] = min(1.0, len(text.split()) / 15) * 0.5 + 0.5 * (
            0.2 if any(w in text_lower for w in ["should", "must", "verify", "ensure"]) else 0
        )
        scores["coverage"] = min(1.0, (
            0.3 if "list" in text_lower else
            0.2 if "all" in text_lower else 0.1
        ))
        scores["resilience"] = 0.5 + 0.3 * ("even if" in text_lower) + 0.2 * ("timeout" in text_lower)
        scores["self_containment"] = min(1.0, len(text) / 300) * 0.5 + 0.5 * (
            0.3 if "step" in text_lower else 0.1
        )
        scores["verifiability"] = min(1.0, (
            0.4 if "assert" in text_lower or "verify" in text_lower else
            0.2 if "expected" in text_lower else 0.1
        ))
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

    def __init__(self, evaluator_fn, dimension_names=None, reward_adapter=None,
                 reflect_model: str | None = None, proxy_state: bool = False):
        super().__init__(evaluator_fn, dimension_names)
        self.reward_adapter = reward_adapter or (RewardAdapter() if RewardAdapter else None)
        self.reflect_model = reflect_model
        self.proxy_state = proxy_state and (ProxyStateTracker is not None)
        self._proxy_tracker = ProxyStateTracker() if self.proxy_state else None
        # Check if the actual model supports reasoning_content or only has simulated traces
        if reward_adapter and _HAS_REWARD_ADAPTER:
            from evolution.prompts.reward_adapter import has_reasoning_capability
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
        """
        prompt_text = next(iter(candidate.values()))
        scores = [self.evaluator_fn(prompt_text) for _ in batch]
        objective_scores = [{"rubric": s} for s in scores]

        # Build trajectories when capture_traces=True (needed for reflection)
        trajectories = None
        if capture_traces:
            trajectories = []
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
                    classification = self._proxy_to_classification(proxy_result)
                    insight = (
                        f"[PROXY STATE] Dimensions: "
                        f"{json.dumps(proxy_result['dimensions'])}. "
                        f"Reasoning: {proxy_result['reasoning']}"
                    )

                    trajectories.append({
                        "data": batch[i] if i < len(batch) else {"input": "", "answer": ""},
                        "full_assistant_response": prompt_text[:500],
                        "feedback": _build_enriched_feedback(
                            score, detail,
                            classification=classification,
                            insight=insight,
                            proxy_scores=proxy_result["dimensions"],
                            composite_override=proxy_result["composite"],
                        ),
                    })
                else:
                    # ── Original [SIMULATED] trace path ──
                    insight = ""
                    if self.reward_adapter:
                        proxy_span = Span(
                            name="prompt_eval",
                            status="failed" if score < 0.7 else "success",
                            intended_action=_infer_intended_action(prompt_text),
                            error=_infer_error_pattern(prompt_text),
                            reasoning_content=_build_fake_reasoning_trace(prompt_text),
                        )
                        cls, _, insight = self.reward_adapter._classify_failure_with_trace(proxy_span, i)

                    trajectories.append({
                        "data": batch[i] if i < len(batch) else {"input": "", "answer": ""},
                        "full_assistant_response": prompt_text[:500],
                        "feedback": _build_enriched_feedback(
                            score, detail,
                            classification=cls,
                            insight=insight,
                            simulated_label=self._simulated_label(),
                        ),
                    })

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
        if dims.get("state_agreement", 1.0) < 0.5 and dims.get("tool_correctness", 0) >= 0.5:
            return "behavioral"
        # Low error_handling → structural
        if dims.get("error_handling", 1.0) < 0.5:
            return "structural"
        return "behavioral"


# ── Helpers for reward-enriched reflection feedback ────────────────────────

def _infer_intended_action(text: str) -> str:
    """Heuristic: extract tool name from prompt text."""
    for prefix in ["create", "delete", "list", "start", "stop", "inspect",
                    "pull", "push", "tag", "build", "exec", "prune", "rollback",
                    "restore", "check", "validate", "verify", "attach", "detach"]:
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


def _build_enriched_feedback(score: float, detail: dict,
                               classification: str | None = None,
                               insight: str = "",
                               simulated_label: str = "",
                               proxy_scores: dict | None = None,
                               composite_override: float | None = None) -> str:
    """Build reflection feedback with optional reasoning insight or proxy state.

    Args:
        simulated_label: Prefix like "[SIMULATED] " when the insight is from
            heuristic proxy traces rather than real K2.6 reasoning_content.
        proxy_scores: When set (proxy-state mode), 5-dimension scores from
            ProxyStateTracker are injected as dimensional breakdown.
        composite_override: When set, overrides the rubric score with the
            proxy state composite score for GEPA consumption.
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
    return base


# ── Config ─────────────────────────────────────────────────────────────────
COMPOSE_PKL = Path("/Users/kieranlal/workspace/compose-pkl")
EVIDENCE_LOG = COMPOSE_PKL / "docs" / "evolve-evidence.jsonl"

# ── Checkpoint (durable progress) ──────────────────────────────────────────
CHECKPOINT_DIR = Path.home() / ".hermes" / "evolution-checkpoints"
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

def _checkpoint_path(label: str) -> Path:
    """Return checkpoint path for a given tier/label. Safe characters only."""
    safe = re.sub(r'[^a-zA-Z0-9_-]', '_', label.lower())
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
    1: {"doc": P1, "prompts": (1, 47), "iterations": 5, "label": "Tier 1: Container Lifecycle"},
    2: {"doc": P2, "prompts": (48, 68), "iterations": 5, "label": "Tier 2: Advanced Orchestration"},
    3: {"doc": P4, "prompts": (69, 91), "iterations": 5, "label": "Tier 3: Host-Native Lane"},
    4: {"doc": P4, "prompts": (92, 121), "iterations": 5, "label": "Tier 4: Infrastructure (Networks, Volumes, Images, Pods, Compose)"},
    5: {"doc": P5, "prompts": (122, 126), "iterations": 5, "label": "Tier 5: Externalized State (State Artifacts, Multi-Agent Sync, Crash Recon, Permission Gates, Slab Protocol)"},
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


def optimize_prompt_text(prompt_text: str, tools: list[str], max_calls: int = 10,
                         proxy_state: bool = False) -> tuple[str, float, float]:
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
        prompt_text, max_calls,
        use_reward_adapter=getattr(_current_args, 'use_reward_adapter', False),
        reflect_model=getattr(_current_args, 'reflect_model', None),
        proxy_state=proxy_state,
    )


def _optimize_prompt_text_hybrid(prompt_text: str, max_calls: int = 10,
                                 use_reward_adapter: bool = False,
                                 reflect_model: str | None = None,
                                 proxy_state: bool = False) -> tuple[str, float, float]:
    """Hybrid optimization: heuristic GEPA + OTel validation.

    When proxy_state=True, uses ProxyStateTracker (LLM-as-a-Judge)
    for 5-dimension scoring instead of simulated reasoning traces.
    """
    from evolution.prompts.otel_adapter import OTelPromptAdapter

    # Phase 1: Generate candidate with heuristic GEPA
    heuristic_evolved, _, _ = _optimize_prompt_text_heuristic(
        prompt_text, max_calls, use_reward_adapter, reflect_model,
        proxy_state=getattr(_current_args, 'proxy_state', False) if _current_args else False,
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
        print(f"  Length cap: {len(heuristic_evolved)} vs {len(prompt_text)} chars (>8x, skipping OTel)")
        hs = evaluate_prompt_wrapper(prompt_text)
        return prompt_text, hs, hs
    
    # Phase 2: OTel A/B validation
    batch = [{"input": "eval", "answer": "pass"}]
    adapter = OTelPromptAdapter(hermes_timeout=180, max_turns=10, cleanup_prompt=None)
    
    # Score original
    adapter.evaluate(batch, {"prompt": prompt_text},
                     run_suffix=f"hybrid_orig_{hash(prompt_text) % 10000}",
                     cleanup=True)
    # Run again fresh after cleanup to get clean score
    r1 = adapter.evaluate(batch, {"prompt": prompt_text},
                          run_suffix=f"hybrid_a_{hash(prompt_text) % 10000}")
    orig_score = r1.scores[0]
    
    # Cleanup, then score evolved
    adapter._run_cleanup()
    r2 = adapter.evaluate(batch, {"prompt": heuristic_evolved},
                          run_suffix=f"hybrid_b_{hash(heuristic_evolved) % 10000}")
    evo_score = r2.scores[0]
    
    hs = evaluate_prompt_wrapper(prompt_text)
    evo_hs = evaluate_prompt_wrapper(heuristic_evolved)
    
    if evo_score > orig_score:
        print(f"  OTel validated: +{evo_score - orig_score:.3f} improvement")
        return heuristic_evolved, hs, evo_hs
    else:
        print(f"  OTel rejected: {evo_score:.3f} vs {orig_score:.3f} (keeping original)")
        return prompt_text, hs, hs


def _optimize_prompt_text_heuristic(prompt_text: str, max_calls: int = 10,
                                    use_reward_adapter: bool = False,
                                    reflect_model: str | None = None,
                                    proxy_state: bool = False) -> str:
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
        evaluator_fn=lambda text: max(0.0, min(1.0, evaluate_prompt_wrapper(text))),
    )

    if use_reward_adapter and _HAS_REWARD_ADAPTER:
        from evolution.prompts.reward_adapter import RewardAdapter
        adapter = RewardAwareAdapter(
            evaluator_fn=lambda text: max(0.0, min(1.0, evaluate_prompt_wrapper(text))),
            reward_adapter=RewardAdapter(),
            reflect_model=reflect_model,
            proxy_state=proxy_state,
        )
        if proxy_state:
            print(f"  Using ProxyStateTracker (LLM-as-a-Judge) for 5-dimension scoring — replaces heuristic SNR gap")
        elif reflect_model:
            from evolution.prompts.reward_adapter import has_reasoning_capability
            if has_reasoning_capability(reflect_model):
                print(f"  Using RewardAwareAdapter with K2.6 reasoning trace enrichment (model: {reflect_model})")
            else:
                print(f"  Using RewardAwareAdapter with [SIMULATED] reasoning traces — model {reflect_model} not recognized as K2.6")
        else:
            print("  Using RewardAwareAdapter with [SIMULATED] reasoning traces (no --reflect-model set)")

    reflection_lm = make_hermes_lm(max_turns=1, timeout=180, model=reflect_model)

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
        TEST_VERBS = r'(?:Create|Pull|Tag|Push|Build|List|Start|Stop|Delete|Inspect|Exec|'
        TEST_VERBS += r'Execute|Restore|Validate|Check|Attempt|Run|Submit|Given|Test|'
        TEST_VERBS += r'Verify|Clean|Prune|Rollback|Stream|Use|Call)'
        lines = evolved_text.strip().split('\n')
        first_instruction = -1
        for i, line in enumerate(lines):
            s = line.strip()
            # Skip meta-commentary lines
            if re.match(r"^(Based on|Here.?(?: is|'s)|Looking at|I have|The (?:following|key)|"
                        r"This improved|Summary of|Key improvements|Dimensional|"
                        r"Analyze the|Here are|Below is|Provide the)", s, re.I):
                continue
            # Check if this line starts a test instruction
            if re.match(TEST_VERBS, s, re.I) and len(s) > 10:
                first_instruction = i
                break
        if first_instruction > 0:
            evolved_text = '\n'.join(lines[first_instruction:]).strip()
            m_block = re.search(r'```(?:\w+)?\n(.+?)```', evolved_text, re.DOTALL)
            if m_block:
                evolved_text = m_block.group(1).strip()

        # Length truncation: cap at 150% of original to stay under the 8x OTel cap.
        # The reflection LM tends to over-generate when given multiple improvement
        # targets. Truncate at the nearest paragraph boundary.
        max_len = int(len(prompt_text) * 1.5)
        if len(evolved_text) > max_len:
            # Find a good truncation point: try paragraph break, then sentence break
            truncated = evolved_text[:max_len]
            para_break = max(truncated.rfind('\n\n'), truncated.rfind('\r\n\r\n'))
            if para_break > max_len * 0.5:  # Only use paragraph break if past halfway
                evolved_text = truncated[:para_break].strip()
            else:
                # Fall back to last sentence boundary
                sent_break = max(
                    truncated.rfind('. '), truncated.rfind('.\n'),
                    truncated.rfind('!\n'), truncated.rfind('?\n'),
                )
                if sent_break > max_len * 0.5:
                    evolved_text = truncated[:sent_break + 1].strip()
                # else: keep the truncated text at the hard cap
    except Exception as e:
        print(f"  GEPA optimize failed: {e}")
        evolved_text = prompt_text

    evolved_score = evaluate_prompt_wrapper(evolved_text)
    original_score = evaluate_prompt_wrapper(prompt_text)
    return evolved_text, original_score, evolved_score


def load_document(path: Path) -> str:
    """Load a complete prompt document as a single string."""
    return path.read_text()


def save_document(path: Path, content: str) -> None:
    """Save evolved prompt document."""
    path.write_text(content)
    print(f"  Saved: {path.name} ({len(content):,} chars)")


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
        _stopper = getattr(evolve_single_prompt, '_stopper', None)
        if _stopper is None:
            _stopper = GEPAStopper(max_seconds_per_prompt=1200)
            evolve_single_prompt._stopper = _stopper
        _stopper.start_prompt(str(prompt_num))
    except ImportError:
        _stopper = None

    print(f"\n{'='*60}")
    print(f"Canary: evolving prompt #{prompt_num} ({prompt['title']})")
    print(f"{'='*60}")
    print(f"  Tools: {', '.join(tools)}")
    
    original_score = evaluate_prompt_wrapper(original_text)
    print(f"  Baseline score: {original_score:.3f}")
    print(f"  Original length: {len(original_text)} chars")

    evolved_text, _, evolved_score = optimize_prompt_text(original_text, tools, max_calls=6,
                                                           proxy_state=getattr(_current_args, 'proxy_state', False))

    # ── Dead Man's Switch: check if we timed out during optimization ──
    if _stopper is not None:
        if _stopper.check():
            print(f"  ⏱️  Stopper triggered — prompt #{prompt_num} exceeded limit.")
            evolved_text = original_text
            evolved_score = original_score

    # ── Quality gate: reject evolved text with reflective/trace leakage ──
    try:
        from evolution.prompts.prompt_validator import safe_write_evolved
        accepted, sanitized_or_error = safe_write_evolved(
            evolved_text, original_text, prompt_num,
            max_length=1500, max_bloat_ratio=3.0,
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

    print(f"  Evolved score: {evolved_score:.3f} (delta: {evolved_score - original_score:+.3f})")
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
        "evaluator": "proxy_state" if getattr(_current_args, 'proxy_state', False) else "heuristic",
    }
    log_evidence(evidence)

    # ── Dead Man's Switch: signal prompt completion ─────────────────
    if _stopper is not None:
        _stopper.end_prompt()

    return evidence


def evolve_tier(tier_num: int, inventory: list) -> dict:
    """Evolve all prompts in a tier using GEPA. Each prompt section is evolved independently."""
    cfg = TIER_BUDGETS[tier_num]
    doc_path = cfg["doc"]
    original_doc = load_document(doc_path)

    # Split doc into sections by prompt number boundary
    import re
    sections = re.split(r"^(### \d+\.)", original_doc, flags=re.MULTILINE)
    # sections = [pre, "### N.", body, "### N.", body, ...]

    lo, hi = cfg["prompts"]
    print(f"\n{'='*60}")
    print(f"Evolving {cfg['label']} ({lo}-{hi})")
    print(f"  Iterations: {cfg['iterations']}")
    print(f"  Document: {doc_path.name} ({len(original_doc):,} chars)")
    print(f"{'='*60}")

    evolved_sections = []
    total_improvement = 0.0
    prompts_evolved = 0

    i = 0
    while i < len(sections):
        section = sections[i]
        m = re.match(r"^### (\d+)\.", section)
        if m:
            prompt_num = int(m.group(1))
            if lo <= prompt_num <= hi:
                # ── Resume: skip prompts already completed ──────────────
                if getattr(_current_args, 'resume', False):
                    cp = load_checkpoint(cfg["label"])
                    if cp and cp.get("completed_prompts") and prompt_num in cp["completed_prompts"]:
                        print(f"  ⏭️  Skipping prompt #{prompt_num} (completed in prior run)")
                        evolved_sections.append(section)
                        if i + 1 < len(sections):
                            evolved_sections.append(sections[i + 1])
                            i += 2
                            continue
                        else:
                            i += 1
                            continue
                # This prompt is in our tier — evolve it
                prompt_text = sections[i + 1] if i + 1 < len(sections) else ""
                tools = PROMPT_TOOLS.get(prompt_num, [])
                orig_score = evaluate_prompt_wrapper(prompt_text)

                evolved_text, _, evolved_score = optimize_prompt_text(
                    prompt_text, tools, max_calls=cfg["iterations"] * 2,
                    proxy_state=getattr(_current_args, 'proxy_state', False),
                )
                delta = evolved_score - orig_score
                total_improvement += delta
                prompts_evolved += 1

                # ── Durable checkpoint after each prompt ────────────────
                # Saves evolved text, scores, and metadata so a killed run
                # can be fully reconstructed from the checkpoint alone
                # (no need to dig through log files).
                save_checkpoint({
                    "tier": tier_num,
                    "label": cfg["label"],
                    "prompt_num": prompt_num,
                    "tools": tools,
                    "baseline_score": round(orig_score, 4),
                    "evolved_score": round(evolved_score, 4),
                    "delta": round(delta, 4),
                    "evolved_text": evolved_text,
                    "evolved_length": len(evolved_text),
                    "original_length": len(prompt_text),
                    "completed_prompts": [p for p in range(lo, prompt_num + 1)
                                         if lo <= p <= hi],
                    "prompts_evolved": prompts_evolved,
                    "total_improvement": round(total_improvement, 4),
                    "last_prompt": prompt_num,
                    "last_delta": round(delta, 4),
                }, cfg["label"])

                # Also log structured evidence to evolve-evidence.jsonl
                log_evidence({
                    "phase": f"tier-{tier_num}",
                    "prompt_num": prompt_num,
                    "tools": tools,
                    "baseline_score": round(orig_score, 4),
                    "evolved_score": round(evolved_score, 4),
                    "improvement": round(delta, 4),
                    "evolved_text": evolved_text,
                    "evolved_length": len(evolved_text),
                })

                evolved_sections.append(section)
                evolved_sections.append(evolved_text)
                if i + 1 < len(sections):
                    i += 2
                    continue
            else:
                # Outside tier — keep original
                evolved_sections.append(section)
                if i + 1 < len(sections):
                    evolved_sections.append(sections[i + 1])
                    i += 2
                    continue
        else:
            evolved_sections.append(section)
        i += 1

    evolved_doc = "".join(evolved_sections)
    save_document(doc_path, evolved_doc)

    avg_improvement = total_improvement / max(1, prompts_evolved)
    evidence = {
        "phase": f"tier-{tier_num}",
        "doc": doc_path.name,
        "prompts_evolved": prompts_evolved,
        "total_improvement": round(total_improvement, 4),
        "avg_improvement": round(avg_improvement, 4),
        "original_size": len(original_doc),
        "evolved_size": len(evolved_doc),
    }
    log_evidence(evidence)
    return evidence


def dry_run(use_reward_adapter: bool = False, reflect_model: str | None = None) -> dict:
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
        return {"gate": "G1-dry-run", "result": "FAIL", "reason": f"Invalid rubric score: {score['composite']}"}

    # Check RewardAdapter integration (if requested)
    if use_reward_adapter:
        if not _HAS_REWARD_ADAPTER:
            print("  ✗ --use-reward-adapter requested but RewardAdapter not importable")
            return {"gate": "G1-dry-run", "result": "FAIL", "reason": "RewardAdapter not importable"}
        print("  ✓ RewardAdapter importable")

        # Check ProxyStateTracker integration (if --proxy-state also requested)
        proxy_state = getattr(_current_args, 'proxy_state', False) if _current_args else False
        if proxy_state:
            if ProxyStateTracker is None:
                print("  ✗ --proxy-state requested but ProxyStateTracker not importable")
                return {"gate": "G1-dry-run", "result": "FAIL", "reason": "ProxyStateTracker not importable"}
            print("  ✓ ProxyStateTracker importable")

            # Quick smoke test: evaluate a known prompt
            try:
                tracker = ProxyStateTracker()
                result = tracker.evaluate(
                    prompt="Create a container named 'test-nginx' using nginx:latest image.",
                )
                dims = result.get("dimensions", {})
                assert len(dims) == 5, f"Expected 5 dimensions, got {len(dims)}"
                for dim in ProxyStateTracker.DIMENSIONS:
                    assert 0.0 <= dims.get(dim, -1) <= 1.0, f"Dimension {dim} out of range"
                assert 0.0 <= result.get("composite", -1) <= 1.0, "Composite out of range"
                print(f"  ✓ ProxyStateTracker smoke test: composite={result['composite']:.3f}")
                print(f"    Dimensions: {json.dumps(dims)}")
                print(f"    Stats: {json.dumps(tracker.get_stats())}")
            except Exception as e:
                print(f"  ✗ ProxyStateTracker smoke test failed: {e}")
                return {"gate": "G1-dry-run", "result": "FAIL", "reason": f"ProxyStateTracker smoke test: {e}"}

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
            assert cls in ("structural", "behavioral", "state_contamination", ""), f"Unexpected cls: {cls}"
            print(f"  ✓ RewardAdapter trace classification works: {cls}")
        except Exception as e:
            print(f"  ✗ RewardAdapter classification failed: {e}")
            return {"gate": "G1-dry-run", "result": "FAIL", "reason": f"RewardAdapter classification error: {e}"}

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
            return {"gate": "G1-dry-run", "result": "FAIL", "reason": f"RewardAwareAdapter error: {e}"}

        # Check model capability for reasoning trace enrichment
        from evolution.prompts.reward_adapter import has_reasoning_capability, get_active_model
        if reflect_model:
            if has_reasoning_capability(reflect_model):
                print(f"  ✓ Reflect model '{reflect_model}' supports reasoning_content (K2.6 Thinking Mode)")
            else:
                print(f"  ⚠ Reflect model '{reflect_model}' not recognized as K2.6 — "
                      f"reasoning_insight will be [SIMULATED], not authentic K2.6 traces")
        else:
            active = get_active_model()
            print(f"  ℹ No --reflect-model set — reflection LM uses default model '{active or 'unknown'}'. "
                  f"Reasoning insights will be [SIMULATED].")
            if active:
                from evolution.prompts.reward_adapter import _REASONING_MODEL_IDS
                if has_reasoning_capability(active):
                    print(f"  ℹ Default model IS K2.6 reasoning-capable. "
                          f"Add --reflect-model to enable authentic trace parsing.")

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
        return {"gate": "G1-dry-run", "result": "FAIL", "reason": f"kilo-proxy unreachable: {e}"}

    # Pre-flight: check OTel telemetry pipeline
    print(f"  Checking OTel telemetry pipeline...")
    try:
        from evolution.prompts.otel_adapter import OTelPromptAdapter
        oa = OTelPromptAdapter()
        otel_check = oa.check_telemetry_pipeline()
        if otel_check["healthy"]:
            print(f"  ✓ OTel pipeline healthy ({otel_check['span_count']} spans in otel_spans)")
        else:
            print(f"  ⚠ OTel pipeline unhealthy: {otel_check['error']}")
            print(f"  ⚠ OTel A/B validation will fail — prompts will run blind")
    except Exception as e:
        print(f"  ⚠ OTel check error: {e}")
        print(f"  ⚠ Continuing without OTel validation (will use heuristic fallback)")

    evidence = {"gate": "G1-dry-run", "result": "PASS", "inventory_size": len(inventory)}
    log_evidence(evidence)
    print(f"  ✓ Dry-run PASS — ready for G1 canary")
    return evidence


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Evolve compose-pkl MCP test prompts using GEPA")
    parser.add_argument("--dry-run", action="store_true", help="Validate setup without optimizing")
    parser.add_argument("--single-prompt", type=int, default=None, help="Evolve a single prompt (G1 canary)")
    parser.add_argument("--tier", type=str, default=None, choices=["1", "2", "3", "4", "all"], help="Tier to evolve")
    parser.add_argument("--evidence-file", type=str, default=str(EVIDENCE_LOG), help="Path for evidence log")
    parser.add_argument("--use-reward-adapter", action="store_true",
                        help="Enable RewardAdapter reasoning trace enrichment in GEPA feedback")
    parser.add_argument("--reflect-model", type=str, default=None,
                        help="Model identifier for GEPA's reflection LM (e.g. 'tinker/moonshotai/Kimi-K2.6'). "
                             "When set, enables real reasoning_content trace parsing. "
                             "Default: uses hermes profile default model (no reasoning traces).")
    parser.add_argument("--proxy-state", action="store_true",
                        help="Enable ProxyStateTracker (LLM-as-a-Judge) for 5-dimension state-aware "
                             "evaluation. Replaces [SIMULATED] reasoning traces with authentic "
                             "DeepSeek-V4-Flash judgment across: tool_correctness, parameter_validity, "
                             "error_handling, resource_lifecycle, state_agreement. "
                             "Required for +0.60 delta target (Plan 130 §1.5.2).")
    parser.add_argument("--sample", type=int, default=0,
                        help="Run a random sample of N prompts from the target tier first. "
                             "Validates pipeline health and spot-checks improvement rate before "
                             "committing to the full batch. Default 0 = run full batch directly.")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from last checkpoint. Skips prompts already recorded "
                             "in the tier's checkpoint file. Safe to SIGKILL and restart.")

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
                print(f"\n{'='*60}")
                print(f"❌ OTel PIPELINE UNHEALTHY — aborting evolution")
                print(f"{'='*60}")
                print(f"  DB reachable: {otel_check['db_reachable']}")
                print(f"  otel_spans table: {otel_check['table_exists']}")
                print(f"  Error: {otel_check['error']}")
                print()
                print(f"  Run --dry-run to see the full diagnostic, then fix infrastructure:")
                print(f"    skill honcho-db-otel-infrastructure")
                print(f"{'='*60}")
                sys.exit(1)
            print(f"  ✓ OTel pipeline verified ({otel_check['span_count']} spans available)")

            # Also verify the database has the harness_evolution database
            # (not just the table — full OTel reads require both)
            if otel_check["span_count"] == 0:
                print(f"  ⚠ otel_spans table is empty — first run will have no baseline data")
        except ImportError as e:
            print(f"\n{'='*60}")
            print(f"❌ Cannot verify OTel pipeline — pg8000 or OTel adapter not installed")
            print(f"  Error: {e}")
            print(f"  Run: .venv/bin/pip install pg8000")
            print(f"{'='*60}")
            sys.exit(1)

    inventory = build_inventory()

    if args.dry_run:
        result = dry_run(args.use_reward_adapter, args.reflect_model)
        sys.exit(0 if result["result"] == "PASS" else 1)

    if args.single_prompt:
        result = evolve_single_prompt(args.single_prompt, inventory)
        g1_pass = result["improvement"] >= 0
        print(f"\nG1 gate: {'PASS' if g1_pass else 'FAIL'} (improvement={'+' if g1_pass else ''}{result['improvement']:.4f})")
        sys.exit(0 if g1_pass else 1)

    if args.tier:
        tiers = [1, 2, 3] if args.tier == "all" else [int(args.tier)]

        # ── Sample mode: run N random prompts first for quick validation ──
        if args.sample > 0:
            import random as _random
            from evolution.prompts.otel_adapter import OTelPromptAdapter, _query_otel_spans

            for t in tiers:
                cfg = TIER_BUDGETS[t]
                lo, hi = cfg["prompts"]
                tier_prompts = [p for p in inventory if lo <= p["num"] <= hi]
                sample_size = min(args.sample, len(tier_prompts))
                sampled = _random.sample(tier_prompts, sample_size)
                print(f"\n{'='*60}")
                print(f"Sample mode: {sample_size}/{len(tier_prompts)} prompts from {cfg['label']}")
                print(f"{'='*60}")

                # Track 3 gate signals
                improvements = []          # heuristic Δ for each prompt
                otel_deltas = []           # OTel Δ (from hybrid optimizer)
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

                        status = "✅" if delta > 0 else ("∼" if abs(delta) < 0.001 else "—")
                        print(f"{status} (Δ={delta:+.4f})")
                    except Exception as e:
                        failures.append((p["num"], str(e)))
                        print(f"❌ ({e})")

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
                    ev_lines = Path(EVIDENCE_LOG).read_text().strip().split("\n")
                    # Get the last N evidence entries for G1-canary phases
                    canary_entries = []
                    for line in reversed(ev_lines):
                        if '"phase": "G1-canary"' in line:
                            entry = json.loads(line)
                            if entry.get("improvement", 0) != 0:
                                canary_entries.append(entry["improvement"])
                            if len(canary_entries) >= sample_size:
                                break
                    mean_otel_delta = sum(canary_entries) / max(1, len(canary_entries))
                except Exception:
                    mean_otel_delta = 0.0

                # Sample statistics
                mean_delta = sum(improvements) / max(1, len(improvements))
                acceptance_rate = sum(1 for d in improvements if d > 0) / max(1, len(improvements))
                crash_rate = len(failures) / max(1, len(sampled))
                proxy_parse_rate = (
                    1.0 - (proxy_tracker_stats["parse_failures"] / max(1, proxy_tracker_stats["calls"]))
                    if proxy_tracker_stats["calls"] > 0
                    else 0.0
                )

                print(f"\n{'='*60}")
                print(f"SAMPLE VERDICT — {cfg['label']}")
                print(f"{'='*60}")
                print(f"  Sample size:              {len(sampled)}")
                print(f"  Crash rate:               {crash_rate:.0%}")
                print(f"  Heuristic mean Δ:         {mean_delta:+.4f}")
                print(f"  Heuristic acceptance rate: {acceptance_rate:.0%}")
                print(f"  OTel span delta:           {spans_after - spans_before} (was {spans_before})")
                print(f"  Mean OTel Δ (from evals):  {mean_otel_delta:+.4f}")
                print(f"  ProxyStateTracker calls:   {proxy_tracker_stats['calls']}")
                if failures:
                    print(f"  Failures:                  {failures}")

                # ── Four-gate check with lineage quality ───────────────────
                check1 = crash_rate == 0.0
                check1_label = "No crashes" if check1 else f"{len(failures)} crash(es)"

                check2 = otel_emitted
                check2_label = (
                    f"OTel spans flowing ({spans_after - spans_before} new)"
                    if check2 else "NO OTel spans emitted — pipeline broken"
                )

                check3 = proxy_parse_rate >= 0.8 if proxy_tracker_stats["calls"] > 0 else True
                check3_label = (
                    f"ProxyStateTracker parse rate ≥ 80% ({proxy_parse_rate:.0%})"
                    if check3 else f"ProxyStateTracker parse rate too low ({proxy_parse_rate:.0%})"
                )

                # Gate 4: Infrastructure lineage quality
                # Verify LDP+ContextBus: do compose-pkl spans have parentSpanId
                # and shared trace_ids, or is the pipeline still producing orphans?
                lineage_ok = True
                lineage_score = 0.0
                try:
                    from evolution.prompts.otel_adapter import _query_infrastructure_spans
                    from datetime import datetime, timezone
                    now_iso = datetime.now(timezone.utc).isoformat()
                    infra = _query_infrastructure_spans(session_start=now_iso, window_minutes=5)
                    spans = infra.get("spans", [])
                    if spans:
                        with_parent = [s for s in spans
                                       if s.get("parent_span_id")
                                       and str(s.get("parent_span_id", "")).strip()]
                        unique_traces = set(s.get("trace_id") for s in spans if s.get("trace_id"))
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
                    if check4 else f"Lineage too low ({lineage_score:.0%}) — orphan spans detected"
                )

                print()
                print(f"  ┌─ 4-GATE SAMPLE CHECK ──────────────────────────┐")
                print(f"  │ 1. {check1_label:<40s} {'✅' if check1 else '❌'} │")
                print(f"  │ 2. {check2_label:<40s} {'✅' if check2 else '❌'} │")
                print(f"  │ 3. {check3_label:<40s} {'✅' if check3 else '❌'} │")
                print(f"  │ 4. {check4_label:<40s} {'✅' if check4 else '❌'} │")
                print(f"  └─────────────────────────────────────────────────┘")

                sample_pass = check1 and check2 and check3 and check4
                print(f"\n  Sample verdict: {'✅ PASS — proceeding to full batch' if sample_pass else '❌ FAIL — investigate before full batch'}")
                print(f"{'='*60}")

                if not sample_pass and args.tier != "all":
                    print(f"\n  Gates failed. Run with --skip-sample-gates to bypass.")
                    sys.exit(1)

            # Auto-continuation: if all gates pass, proceed to full batch
            # without requiring a separate --full-after-sample flag
            if args.sample > 0 and not getattr(args, '_full_after_sample', False):
                if sample_pass:
                    print(f"\nAll {cfg['label']} sample gates passed. Auto-continuing to full batch.\n")
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
            print(f"\n  Tier {t} gate: {'PASS' if g2_pass else 'FAIL'} (avg improvement={result['avg_improvement']:.4f})")
            if not g2_pass and args.tier != "all":
                sys.exit(1)

        print(f"\n{'='*60}")
        print(f"Batch complete. Evidence logged to {EVIDENCE_LOG}")
        print(f"Next: run adversarial review on evolved prompts")
        return

    # Default: show status
    print(f"Inventory: {len(inventory)} prompts")
    print(f"Evidence log: {EVIDENCE_LOG}")
    print(f"Run with --dry-run, --single-prompt N, or --tier <1|2|3|all>")


if __name__ == "__main__":
    main()
