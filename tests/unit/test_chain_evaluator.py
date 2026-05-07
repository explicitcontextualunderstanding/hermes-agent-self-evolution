"""Tests for chain_evaluator.py — Plan 130 Phase 4.

Covers: chain definition, credit assignment (P0.3), and
multi-prompt evaluation orchestration.
"""

import sys
from pathlib import Path

# Allow running from workspace or installed location
_ws = Path(__file__).parent.parent.parent / "evolution" / "prompts"
if str(_ws) not in sys.path:
    sys.path.insert(0, str(_ws))
_ws2 = Path(__file__).parent.parent.parent
if str(_ws2) not in sys.path:
    sys.path.insert(0, str(_ws2))

from evolution.prompts.chain_evaluator import (
    Chain,
    ChainStep,
    StepOutcome,
    ChainResult,
    create_chain,
    credit_assignment,
    evaluate_chain,
    simulate_outcomes,
)
# Chain Definition Tests
# ═══════════════════════════════════════════════════════════════════════


class TestChainDefinition:
    """Verify the Create chain is correctly defined."""

    def test_create_chain_has_three_steps(self):
        chain = create_chain()
        assert len(chain.steps) == 3

    def test_create_chain_steps_in_order(self):
        chain = create_chain()
        assert chain.steps[0].role == "create"
        assert chain.steps[1].role == "verify"
        assert chain.steps[2].role == "delete"

    def test_create_chain_prompt_ids(self):
        chain = create_chain()
        assert chain.steps[0].prompt_id == 1
        assert chain.steps[1].prompt_id == 7
        assert chain.steps[2].prompt_id == 6

    def test_create_chain_tool_hints(self):
        chain = create_chain()
        assert chain.steps[0].tool_hint == "create_container"
        assert chain.steps[1].tool_hint == "list_containers"
        assert chain.steps[2].tool_hint == "delete_container"

    def test_create_chain_name(self):
        chain = create_chain()
        assert chain.name == "create"

    def test_prompt_texts_loaded(self):
        chain = create_chain()
        for step in chain.steps:
            assert len(step.prompt_text) > 50, (
                f"Prompt #{step.prompt_id} text too short ({len(step.prompt_text)} chars)"
            )


# ═══════════════════════════════════════════════════════════════════════
# Credit Assignment Tests (P0.3)
# ═══════════════════════════════════════════════════════════════════════


class TestCreditAssignment:
    """P0.3: Cascade blame ground-truth test."""

    def test_all_success_no_blame(self):
        chain = create_chain()
        outcomes = simulate_outcomes(chain, ["success", "success", "success"])
        result = evaluate_chain(chain, outcomes)
        assert all(v == 0.0 for v in result.blame.values())
        assert result.composite == 1.0

    def test_first_step_fails_direct_blame(self):
        """No preceding steps → 100% blame on failing step."""
        chain = create_chain()
        outcomes = simulate_outcomes(chain, ["failed", "skipped", "skipped"])
        result = evaluate_chain(chain, outcomes)
        assert result.blame[0] == 1.0
        assert result.blame[1] == 0.0
        assert result.blame[2] == 0.0
        assert result.composite == 0.0

    def test_second_step_fails_direct_blame(self):
        """Step 1 fails, step 0 clean → 100% blame on step 1."""
        chain = create_chain()
        outcomes = simulate_outcomes(chain, ["success", "failed", "skipped"])
        result = evaluate_chain(chain, outcomes)
        assert result.blame[0] == 0.0
        assert result.blame[1] == 1.0
        assert result.blame[2] == 0.0
        assert result.composite == 1 / 3

    def test_second_step_fails_with_predecessor_warning(self):
        """Step 1 fails after step 0 had warnings → 60/40 split."""
        chain = create_chain()
        outcomes = simulate_outcomes(chain, ["warning", "failed", "skipped"])
        result = evaluate_chain(chain, outcomes)
        assert abs(result.blame[0] - 0.6) < 0.001
        assert abs(result.blame[1] - 0.4) < 0.001
        assert result.blame[2] == 0.0
        assert abs(result.composite - 1 / 3) < 0.001

    def test_third_step_fails_after_one_warning(self):
        """Step 2 fails, step 1 had warning → 60/40, step 0 clean."""
        chain = create_chain()
        outcomes = simulate_outcomes(chain, ["success", "warning", "failed"])
        result = evaluate_chain(chain, outcomes)
        assert result.blame[0] == 0.0
        assert abs(result.blame[1] - 0.6) < 0.001
        assert abs(result.blame[2] - 0.4) < 0.001

    def test_multi_step_cascade(self):
        """F5 adversarial fix: 2 preceding warnings → proportional blame."""
        chain = create_chain()
        outcomes = simulate_outcomes(chain, ["warning", "warning", "failed"])
        result = evaluate_chain(chain, outcomes)
        # Trigger step gets 40%
        assert abs(result.blame[2] - 0.4) < 0.001
        # Both preceding steps get share of remaining 60%
        assert result.blame[0] > 0.0
        assert result.blame[1] > 0.0
        # Blame sums to 1.0
        total = sum(result.blame.values())
        assert abs(total - 1.0) < 0.001

    def test_blame_invariant_sum_to_one(self):
        """Sum of blame should always be 0.0 or 1.0."""
        chain = create_chain()
        scenarios = [
            ["success", "success", "success"],
            ["failed", "skipped", "skipped"],
            ["success", "failed", "skipped"],
            ["warning", "failed", "skipped"],
            ["success", "warning", "failed"],
            ["warning", "warning", "failed"],
        ]
        for statuses in scenarios:
            outcomes = simulate_outcomes(chain, statuses)
            result = evaluate_chain(chain, outcomes)
            total = sum(result.blame.values())
            assert total in (0.0, 1.0), f"Blame sum={total} for {statuses}"

    def test_all_warnings_no_failure(self):
        """Warnings without failure → no blame."""
        chain = create_chain()
        outcomes = simulate_outcomes(chain, ["warning", "warning", "warning"])
        result = evaluate_chain(chain, outcomes)
        assert all(v == 0.0 for v in result.blame.values())
        assert result.composite == 1.0


# ═══════════════════════════════════════════════════════════════════════
# Edge Case Tests
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases for chain evaluation."""

    def test_show_accepts_chain_with_any_prompt_ids(self):
        """Chain should work with arbitrary prompt IDs."""
        custom_chain = Chain(
            name="test",
            steps=[
                ChainStep(
                    prompt_id=99,
                    role="create",
                    prompt_text="Create a test container",
                    tool_hint="create_container",
                ),
                ChainStep(
                    prompt_id=100,
                    role="verify",
                    prompt_text="List containers",
                    tool_hint="list_containers",
                ),
            ],
        )
        outcomes = simulate_outcomes(custom_chain, ["success", "success"])
        result = evaluate_chain(custom_chain, outcomes)
        assert result.composite == 1.0
        assert result.failure_step is None

    def test_empty_chain(self):
        """Empty chain should not error (defensive)."""
        empty_chain = Chain(name="empty", steps=[])
        result = evaluate_chain(empty_chain, [])
        assert result.composite == 1.0
        assert result.failure_step is None
        assert sum(result.blame.values()) == 0.0

    def test_simulate_outcomes_wrong_length(self):
        """simulate_outcomes should reject mismatched statuses."""
        chain = create_chain()
        try:
            simulate_outcomes(chain, ["success"])  # Only 1 status for 3 steps
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

    def test_single_step_chain(self):
        """Single-step chain with failure."""
        single = Chain(
            name="single",
            steps=[
                ChainStep(
                    prompt_id=42,
                    role="create",
                    prompt_text="Do one thing",
                    tool_hint="some_tool",
                ),
            ],
        )
        outcomes = simulate_outcomes(single, ["failed"])
        result = evaluate_chain(single, outcomes)
        assert result.blame[0] == 1.0
        assert result.composite == 0.0

    def test_single_step_chain_success(self):
        """Single-step chain with success."""
        single = Chain(
            name="single",
            steps=[
                ChainStep(
                    prompt_id=42,
                    role="create",
                    prompt_text="Do one thing",
                    tool_hint="some_tool",
                ),
            ],
        )
        outcomes = simulate_outcomes(single, ["success"])
        result = evaluate_chain(single, outcomes)
        assert result.composite == 1.0


# ═══════════════════════════════════════════════════════════════════════
# ChainResult Structure Tests
# ═══════════════════════════════════════════════════════════════════════


class TestChainResult:
    """Verify ChainResult structure is usable by GEPA downstream."""

    def test_result_has_step_outcomes(self):
        chain = create_chain()
        outcomes = simulate_outcomes(chain, ["success", "success", "success"])
        result = evaluate_chain(chain, outcomes)
        assert len(result.step_outcomes) == 3

    def test_result_serializable(self):
        """Blame dict should be JSON-serializable for GEPA."""
        import json

        chain = create_chain()
        outcomes = simulate_outcomes(chain, ["warning", "warning", "failed"])
        result = evaluate_chain(chain, outcomes)
        # blame dict should serialize cleanly
        dumped = json.dumps(
            {
                "composite": result.composite,
                "blame": {str(k): v for k, v in result.blame.items()},
            }
        )
        assert dumped is not None

    def test_failure_propagation_stops_chain(self):
        """Steps after a failure should be skipped."""
        chain = create_chain()
        outcomes = simulate_outcomes(chain, ["success", "failed", "skipped"])
        assert outcomes[2].status == "skipped"
        assert "predecessor failed" in outcomes[2].warnings[0]
