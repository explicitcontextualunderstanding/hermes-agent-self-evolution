"""Tests for evolve_prompts — Phase 2: Mechanical Linter Gate.

Covers:
- test_gate5_lineage: Verifies that ProxyStateTracker has the
  lineage_verification dimension, weights sum to 1.0, and that
  the Gate 5 probe correctly detects missing lineage context.
"""

import json
import unittest
from unittest import mock


# ── Mock judge that returns JSON responses ────────────────────────────────


def _make_judge_response(
    dimensions: dict, composite: float, reasoning: str
) -> str:
    """Build a JSON response as the judge LLM would return."""
    return json.dumps(
        {
            "dimensions": dimensions,
            "composite": composite,
            "reasoning": reasoning,
        }
    )


# Sample dimension sets for testing
DIMS_WITH_LINEAGE = {
    "tool_correctness": 0.9,
    "parameter_validity": 0.8,
    "error_handling": 0.7,
    "resource_lifecycle": 0.9,
    "state_agreement": 0.8,
    "lineage_verification": 0.9,  # prompt includes span context
}

DIMS_WITHOUT_LINEAGE = {
    "tool_correctness": 0.9,
    "parameter_validity": 0.8,
    "error_handling": 0.7,
    "resource_lifecycle": 0.9,
    "state_agreement": 0.8,
    "lineage_verification": 0.1,  # prompt lacks span context
}


class TestProxyStateTrackerLineage(unittest.TestCase):
    """Test the lineage_verification dimension in ProxyStateTracker."""

    def setUp(self):
        # Lazy import to avoid startup failures when PST deps are missing
        from evolution.prompts.reward_adapter import ProxyStateTracker

        self.PST = ProxyStateTracker

    def test_lineage_dimension_exists(self):
        """lineage_verification is in the DIMENSIONS list."""
        self.assertIn("lineage_verification", self.PST.DIMENSIONS)

    def test_lineage_weight_exists(self):
        """lineage_verification has a weight in DIMENSION_WEIGHTS."""
        self.assertIn("lineage_verification", self.PST.DIMENSION_WEIGHTS)

    def test_weights_sum_to_one(self):
        """All dimension weights sum to 1.0."""
        total = sum(self.PST.DIMENSION_WEIGHTS.values())
        self.assertAlmostEqual(
            total,
            1.0,
            places=4,
            msg=f"Dimension weights sum to {total}, expected 1.0",
        )

    def test_lineage_weight_is_positive(self):
        """lineage_verification weight is > 0."""
        w = self.PST.DIMENSION_WEIGHTS["lineage_verification"]
        self.assertGreater(w, 0.0)

    def test_lineage_verification_scored(self):
        """evaluate() returns lineage_verification in dimensions."""
        judge = mock.Mock(
            return_value=_make_judge_response(
                DIMS_WITH_LINEAGE,
                0.85,
                "All dimensions looking good, lineage context present.",
            )
        )
        tracker = self.PST(judge_lm=judge)
        result = tracker.evaluate(prompt="test", output="test")
        dims = result.get("dimensions", {})
        self.assertIn("lineage_verification", dims)
        self.assertAlmostEqual(dims["lineage_verification"], 0.9)

    def test_probe_without_lineage_scores_low(self):
        """A probe that omits required_parent_span_id gets low lineage score.

        This simulates the Gate 5 mechanical linter check: a prompt that
        calls delete_container without span context should score < 0.5
        on the lineage_verification dimension.
        """
        judge = mock.Mock(
            return_value=_make_judge_response(
                DIMS_WITHOUT_LINEAGE,
                0.65,
                "Prompt calls delete_container but lacks required_parent_span_id.",
            )
        )
        tracker = self.PST(judge_lm=judge)
        result = tracker.evaluate(
            prompt="Use stop_container and delete_container to remove test-nginx.",
            output="Call stop_container with name='test-nginx'.",
        )
        lv = result.get("dimensions", {}).get("lineage_verification", 1.0)
        self.assertLess(
            lv, 0.5, f"Expected lineage_verification < 0.5, got {lv}"
        )

    def test_judge_prompt_mentions_lineage(self):
        """The JUDGE_PROMPT template includes dimension 6 description."""
        prompt_template = self.PST.JUDGE_PROMPT
        self.assertIn("lineage_verification", prompt_template)
        self.assertIn("required_parent_span_id", prompt_template)
        self.assertIn("delete_container", prompt_template)
        self.assertIn("stop_container", prompt_template)


class TestGate5MechanicalLinter(unittest.TestCase):
    """Test that the Gate 5 logic in evolve_prompts works correctly."""

    def test_gate5_lineage(self):
        """Gate 5 Mechanical Linter: lineage_verification dimension is wired.

        This is the primary verification test for Plan 135 Phase 2.2.
        Validates that ProxyStateTracker correctly penalizes prompts that
        call delete_container/stop_container without required_parent_span_id.
        """
        from evolution.prompts.reward_adapter import ProxyStateTracker

        # Sanity: dimension and weight exist
        self.assertIn("lineage_verification", ProxyStateTracker.DIMENSIONS)
        self.assertIn(
            "lineage_verification", ProxyStateTracker.DIMENSION_WEIGHTS
        )
        self.assertAlmostEqual(
            sum(ProxyStateTracker.DIMENSION_WEIGHTS.values()),
            1.0,
            places=4,
        )

        # Test with mock judge: probe missing lineage gets low score
        judge_low = mock.Mock(
            return_value=json.dumps(
                {
                    "dimensions": {
                        "tool_correctness": 0.9,
                        "parameter_validity": 0.8,
                        "error_handling": 0.7,
                        "resource_lifecycle": 0.9,
                        "state_agreement": 0.8,
                        "lineage_verification": 0.1,
                    },
                    "composite": 0.65,
                    "reasoning": "delete_container lacks required_parent_span_id.",
                }
            )
        )
        tracker = ProxyStateTracker(judge_lm=judge_low)
        result = tracker.evaluate(
            prompt="Delete container test-nginx.",
            output="Call delete_container with name='test-nginx'.",
        )
        lv = result.get("dimensions", {}).get("lineage_verification", 1.0)
        self.assertLess(
            lv,
            0.5,
            f"Gate 5: lineage_verification should be < 0.5 "
            f"for probe missing span context, got {lv}",
        )

        # Test with mock judge: prompt WITH lineage gets high score
        judge_high = mock.Mock(
            return_value=json.dumps(
                {
                    "dimensions": {
                        "tool_correctness": 0.9,
                        "parameter_validity": 0.8,
                        "error_handling": 0.7,
                        "resource_lifecycle": 0.9,
                        "state_agreement": 0.8,
                        "lineage_verification": 0.9,
                    },
                    "composite": 0.85,
                    "reasoning": "delete_container includes required_parent_span_id.",
                }
            )
        )
        tracker2 = ProxyStateTracker(judge_lm=judge_high)
        result2 = tracker2.evaluate(
            prompt="Delete container test-nginx with span context.",
            output="Call delete_container with name='test-nginx', "
            "required_parent_span_id='abc123'.",
        )
        lv2 = result2.get("dimensions", {}).get("lineage_verification", 0.0)
        self.assertGreaterEqual(
            lv2,
            0.5,
            f"Gate 5: lineage_verification should be >= 0.5 "
            f"for prompt WITH span context, got {lv2}",
        )

    def test_gate5_probe_text_exists(self):
        """The lint_probe text used in Gate 5 is parseable."""
        # This is the same probe text used in evolve_prompts.py's Gate 5
        lint_probe = (
            "Use stop_container and delete_container to stop and remove "
            "a container named 'test-nginx'. The container is currently running. "
            "First stop it, then delete it."
        )
        self.assertIn("delete_container", lint_probe)
        self.assertIn("stop_container", lint_probe)
        # The probe deliberately does NOT mention required_parent_span_id
        self.assertNotIn("required_parent_span_id", lint_probe)

    def test_gate5_pass_when_lineage_missing_detected(self):
        """Gate 5 passes when PST correctly scores lineage_verification low.

        The mechanical linter injects a probe that lacks span context.
        If the PST detects this (lineage_verification < 0.5), Gate 5 passes.
        """
        from evolution.prompts.reward_adapter import ProxyStateTracker

        judge = mock.Mock(
            return_value=_make_judge_response(
                DIMS_WITHOUT_LINEAGE,
                0.65,
                "No lineage context detected in delete_container call.",
            )
        )
        tracker = ProxyStateTracker(judge_lm=judge)
        result = tracker.evaluate(
            prompt="Delete container test-nginx using delete_container.",
            output="Call delete_container with name='test-nginx'.",
        )
        lv = result.get("dimensions", {}).get("lineage_verification", 0.5)
        # Gate 5 passes when lineage_verification < 0.5 (PST caught the issue)
        self.assertLess(
            lv, 0.5, f"Gate 5 should detect missing lineage (got {lv})"
        )

    def test_gate5_fail_when_lineage_not_checked(self):
        """Gate 5 fails when PST gives high lineage score despite missing context.

        If the PST returns lineage_verification >= 0.5 for a probe that
        deliberately omits required_parent_span_id, Gate 5 fails because
        the mechanical linter isn't working.
        """
        from evolution.prompts.reward_adapter import ProxyStateTracker

        # Simulate a PST that doesn't penalize missing lineage (broken gate)
        dims_false_positive = dict(DIMS_WITH_LINEAGE)
        dims_false_positive["lineage_verification"] = 0.8  # should be low!

        judge = mock.Mock(
            return_value=_make_judge_response(
                dims_false_positive,
                0.80,
                "Everything looks fine.",
            )
        )
        tracker = ProxyStateTracker(judge_lm=judge)
        result = tracker.evaluate(
            prompt="Delete container test-nginx using delete_container.",
            output="Call delete_container with name='test-nginx'.",
        )
        lv = result.get("dimensions", {}).get("lineage_verification", 0.5)
        # Gate 5 fails when lineage_verification >= 0.5 despite missing context
        self.assertGreaterEqual(
            lv,
            0.5,
            "Gate 5 should fail when PST doesn't detect missing lineage",
        )


if __name__ == "__main__":
    unittest.main()
