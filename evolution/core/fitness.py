"""Fitness functions for evaluating evolved artifacts.

Uses LLM-as-judge with rubrics to score agent outputs.
Supports length penalties and multi-dimensional scoring.
"""

import hashlib
import dspy
from dataclasses import dataclass, field
from typing import Optional

from evolution.core.config import EvolutionConfig


# ── Sub-sampled judge state (module-level singleton) ──────────────────────────

_SUB_SAMPLE_ENABLED = False
_SUB_SAMPLE_RATE = 0.10  # Fraction of uncertainty-zone calls to judge
_SUB_SAMPLE_UNCERTAINTY_MIN = 0.4
_SUB_SAMPLE_UNCERTAINTY_MAX = 0.7
_JUDGE_CACHE = {}  # key -> FitnessScore


def configure_sub_sampling(
    enabled: bool = True,
    sample_rate: float = 0.10,
    uncertainty_min: float = 0.4,
    uncertainty_max: float = 0.7,
):
    """Configure the sub-sampled judge wrapper for skill_fitness_metric.

    Call once before GEPA.compile() to activate sub-sampling.
    Resets the judge cache on each configure() call.
    """
    global _SUB_SAMPLE_ENABLED, _SUB_SAMPLE_RATE, _SUB_SAMPLE_UNCERTAINTY_MIN, \
           _SUB_SAMPLE_UNCERTAINTY_MAX, _JUDGE_CACHE
    _SUB_SAMPLE_ENABLED = enabled
    _SUB_SAMPLE_RATE = sample_rate
    _SUB_SAMPLE_UNCERTAINTY_MIN = uncertainty_min
    _SUB_SAMPLE_UNCERTAINTY_MAX = uncertainty_max
    _JUDGE_CACHE = {}


def _compute_cache_key(task: str, expected: str, agent_output: str) -> str:
    """Deterministic cache key for a (task, expected, agent_output) triple."""
    raw = f"{task}|||{expected}|||{agent_output}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _should_judge(heuristic: float, task: str, expected: str, agent_output: str) -> bool:
    """Decide whether to call the expensive LLMJudge for this sample.

    Three zones:
      heuristic < 0.4:   clearly bad → skip judge, return heuristic
      heuristic > 0.7:   clearly good → skip judge, return heuristic
      0.4 <= h <= 0.7:  uncertainty zone → sample at _SUB_SAMPLE_RATE

    Within the uncertainty zone, sampling is deterministic based on a
    hash of the task+expected+agent_output, so GEPA sees consistent
    scores across repeated calls for the same (example, prediction) pair.
    """
    if not _SUB_SAMPLE_ENABLED:
        return True  # Full judge mode (no sub-sampling)

    if heuristic < _SUB_SAMPLE_UNCERTAINTY_MIN or heuristic > _SUB_SAMPLE_UNCERTAINTY_MAX:
        return False  # Outside uncertainty zone — heuristic is sufficient

    # Uncertainty zone: deterministic hash-based sampling
    key = _compute_cache_key(task, expected, agent_output)
    # Use first 8 hex chars as a probability [0, 1)
    prob = int(key[:8], 16) / 0xFFFFFFFF
    return prob < _SUB_SAMPLE_RATE


def _interpolate_score(
    heuristic: float, task: str, expected: str, agent_output: str
) -> float:
    """Nearest-neighbor interpolation from cached judge results.

    When this call was NOT selected for judging, estimate its score from
    the nearest judged neighbor(s) in heuristic-score space.

    Current implementation: use the single nearest neighbor by heuristic
    distance. A future enhancement would use embedding-space similarity.
    """
    if not _JUDGE_CACHE:
        return heuristic  # No judged anchors yet — return heuristic raw

    # Find the cached entry with closest heuristic score
    closest_key = None
    closest_dist = float("inf")
    for key, (cached_heuristic, cached_score) in _JUDGE_CACHE.items():
        dist = abs(heuristic - cached_heuristic)
        if dist < closest_dist:
            closest_dist = dist
            closest_key = key

    if closest_key is None or closest_dist > 0.3:
        # No close neighbor — fall back to heuristic
        return heuristic

    # Weighted by inverse distance (not just closest)
    total_weight = 0.0
    weighted_sum = 0.0
    for key, (cached_heuristic, cached_score) in _JUDGE_CACHE.items():
        dist = abs(heuristic - cached_heuristic)
        if dist < 0.3:  # Only consider neighbors within range
            weight = 1.0 / (dist + 0.01)  # +0.01 to avoid div by zero
            total_weight += weight
            weighted_sum += weight * cached_score

    if total_weight > 0:
        return weighted_sum / total_weight
    return heuristic


# ── FitnessScore ───────────────────────────────────────────────────────────────


@dataclass
class FitnessScore:
    """Multi-dimensional fitness score."""
    correctness: float = 0.0
    procedure_following: float = 0.0
    conciseness: float = 0.0
    length_penalty: float = 0.0
    feedback: str = ""

    @property
    def composite(self) -> float:
        raw = (
            0.5 * self.correctness
            + 0.3 * self.procedure_following
            + 0.2 * self.conciseness
        )
        return max(0.0, raw - self.length_penalty)


# ── LLMJudge ───────────────────────────────────────────────────────────────────


class LLMJudge:
    """LLM-as-judge scorer with rubric-based evaluation."""

    class JudgeSignature(dspy.Signature):
        """Evaluate an agent's response against an expected behavior rubric.

        Score the response on three dimensions (0.0 to 1.0 each):
        1. correctness: Did the response correctly address the task?
        2. procedure_following: Did it follow the expected approach/procedure?
        3. conciseness: Was it appropriately concise without omitting important info?

        Also provide specific, actionable feedback on what could be improved.
        """
        task_input: str = dspy.InputField(desc="The task the agent was given")
        expected_behavior: str = dspy.InputField(desc="Rubric describing what a good response looks like")
        agent_output: str = dspy.InputField(desc="The agent's actual response")
        skill_text: str = dspy.InputField(desc="The skill/instructions the agent was following")
        correctness: float = dspy.OutputField(desc="Score 0.0-1.0: Did the response correctly address the task?")
        procedure_following: float = dspy.OutputField(desc="Score 0.0-1.0: Did it follow the expected procedure?")
        conciseness: float = dspy.OutputField(desc="Score 0.0-1.0: Appropriately concise?")
        feedback: str = dspy.OutputField(desc="Specific, actionable feedback on what could be improved")

    def __init__(self, config: EvolutionConfig):
        self.config = config
        self.judge = dspy.ChainOfThought(self.JudgeSignature)

    def score(
        self,
        task_input: str,
        expected_behavior: str,
        agent_output: str,
        skill_text: str,
        artifact_size: Optional[int] = None,
        max_size: Optional[int] = None,
    ) -> FitnessScore:
        lm = dspy.LM(self.config.eval_model)

        with dspy.context(lm=lm):
            result = self.judge(
                task_input=task_input,
                expected_behavior=expected_behavior,
                agent_output=agent_output,
                skill_text=skill_text,
            )

        correctness = _parse_score(result.correctness)
        procedure_following = _parse_score(result.procedure_following)
        conciseness = _parse_score(result.conciseness)

        length_penalty = 0.0
        if artifact_size is not None and max_size is not None:
            ratio = artifact_size / max_size
            if ratio > 0.9:
                length_penalty = min(0.3, (ratio - 0.9) * 3.0)

        return FitnessScore(
            correctness=correctness,
            procedure_following=procedure_following,
            conciseness=conciseness,
            length_penalty=length_penalty,
            feedback=str(result.feedback),
        )


# ── skill_fitness_metric (used by GEPA) ────────────────────────────────────────


def skill_fitness_metric(example, prediction, trace=None, pred_name=None, pred_trace=None) -> float:
    """DSPy-compatible metric function for skill optimization.

    Used by dspy.GEPA(metric=skill_fitness_metric).

    Behavior depends on configuration:
      - If configure_sub_sampling() was called: uses heuristic gating + sub-sampling
      - Default (no config): calls LLMJudge for every example if skill_text is available,
        falls back to keyword overlap heuristic

    The sub-sampling strategy (from research):
      1. Compute heuristic score
      2. If heuristic < 0.4: clearly bad → return heuristic (no judge call)
      3. If heuristic > 0.7: clearly good → return heuristic (no judge call)
      4. If 0.4 <= h <= 0.7: uncertainty zone
         - Sample at _SUB_SAMPLE_RATE (deterministic hash-based)
         - If sampled: call LLMJudge, cache result, return LLM score
         - If not sampled: interpolate from cached nearest neighbors
    """
    agent_output = getattr(prediction, "output", "") or ""
    expected = getattr(example, "expected_behavior", "") or ""
    task = getattr(example, "task_input", "") or ""
    skill_text = getattr(example, "skill_text", "") or ""

    if not agent_output.strip():
        return 0.0

    # ── Step 1: Compute heuristic score ───────────────────────────────────────
    expected_words = set(expected.lower().split())
    output_words = set(agent_output.lower().split())
    heuristic = 0.5
    if expected_words:
        overlap = len(expected_words & output_words) / len(expected_words)
        heuristic = 0.3 + (0.7 * overlap)
    heuristic = min(1.0, max(0.0, heuristic))

    # ── Step 2: If sub-sampling is active, decide judge vs skip ────────────────
    if _SUB_SAMPLE_ENABLED:
        should_judge = _should_judge(heuristic, task, expected, agent_output)
    else:
        should_judge = True  # No sub-sampling — always judge if possible

    if should_judge and skill_text:
        # Check cache first
        cache_key = _compute_cache_key(task, expected, agent_output)
        if cache_key in _JUDGE_CACHE:
            cached_heuristic, cached_score = _JUDGE_CACHE[cache_key]
            return cached_score  # Cache hit — return stored LLM score

        # Cache miss — call LLMJudge
        try:
            from evolution.core.config import EvolutionConfig
            import os
            eval_model = os.environ.get(
                "DSPY_EVAL_MODEL",
                "openai/deepseek-proxy/deepseek-v4-flash",
            )
            class _EvalConfig:
                eval_model = eval_model
            judge = LLMJudge(_EvalConfig())
            score = judge.score(
                task_input=task,
                expected_behavior=expected,
                agent_output=agent_output,
                skill_text=skill_text,
            )
            llm_score = score.composite
            # Cache the result keyed by heuristic for interpolation
            _JUDGE_CACHE[cache_key] = (heuristic, llm_score)
            return llm_score
        except Exception:
            pass  # Fall through to heuristic on failure

    # ── Step 3: Interpolation (if sub-sampling active and this was skipped) ────
    if _SUB_SAMPLE_ENABLED and not should_judge and _JUDGE_CACHE:
        interpolated = _interpolate_score(heuristic, task, expected, agent_output)
        return interpolated

    # ── Step 4: Fallback — return heuristic directly ───────────────────────────
    return heuristic


# ── Parse helper ───────────────────────────────────────────────────────────────


def _parse_score(value) -> float:
    if isinstance(value, (int, float)):
        return min(1.0, max(0.0, float(value)))
    try:
        return min(1.0, max(0.0, float(str(value).strip())))
    except (ValueError, TypeError):
        return 0.5
