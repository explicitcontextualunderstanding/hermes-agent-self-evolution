#!/usr/bin/env python3
"""
chain_evaluator.py — Plan 130 Phase 4: Mini-Chain evaluation orchestration.

Extends the Plan 130 credit assignment heuristic (adversarial F5 fix) for
multi-prompt chain evaluation. Scope-limited to Create chain only
(create → verify → delete) per plan scope limits.

Integrates with:
  - inventory.py  → prompt text + tool mapping
  - reward_adapter.py → trajectory Span/Span dataclass (shared types)
  - evolve_prompts.py → GEPA pipeline (via score_prompt() compatibility)

Usage:
    python3 -m evolution.prompts.chain_evaluator         # run Create chain
    python3 -m evolution.prompts.chain_evaluator --test  # run unit tests
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from evolution.env_config import PROMPT_DOCS_DIR

# ── Reuse types from reward_adapter where available ─────────────────────
# These mirror reward_adapter.Span for chain trajectory tracking without
# importing the full adapter (which has Apple Container CLI deps).


@dataclass
class ChainStep:
    """A single step in a mini-chain."""

    prompt_id: int
    role: str  # "create" | "verify" | "delete"
    prompt_text: str
    tool_hint: str  # Short description of the MCP tool exercised


@dataclass
class Chain:
    """A named chain of steps that must execute in sequence."""

    name: str
    steps: list[ChainStep]


@dataclass
class StepOutcome:
    """Outcome of executing one step in a chain."""

    step_index: int
    role: str
    status: str  # "success" | "failed" | "warning"
    tool_calls: int = 0
    error: str = ""  # Error message if failed
    warnings: list[str] = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class ChainResult:
    """
    Result of evaluating a full chain.

    Attributes:
        chain_name: Name of the evaluated chain
        step_outcomes: Outcome for each step in chain order
        blame: {step_index: blame_fraction} from credit assignment
        composite: Overall chain score (0.0-1.0)
        failure_step: Index of first failing step, or None
    """

    chain_name: str
    step_outcomes: list[StepOutcome]
    blame: dict[int, float]
    composite: float
    failure_step: Optional[int] = None


# ═══════════════════════════════════════════════════════════════════════
# Chain Definitions
# ═══════════════════════════════════════════════════════════════════════

# Create chain: prompt IDs mapped to roles
#   create  → prompt #1  (container_create — Basic)
#   verify  → prompt #7  (container_list — All)
#   delete  → prompt #6  (container_delete)
#
# These are the single-tool container lifecycle prompts from P1.
# Multi-step prompts (e.g., #29) are NOT included per scope limits.

CREATE_CHAIN_PROMPTS: dict[int, dict[str, Any]] = {
    1: {
        "role": "create",
        "title": "container_create — Basic",
        "tool_hint": "create_container",
        # Text truncated for initialization; full text loaded by _load_prompt_text()
    },
    7: {
        "role": "verify",
        "title": "container_list — All",
        "tool_hint": "list_containers",
    },
    6: {
        "role": "delete",
        "title": "container_delete",
        "tool_hint": "delete_container",
    },
}


def _load_prompt_text(prompt_id: int) -> str:
    """Load prompt text from the test prompts document.

    Uses the same parsing logic as inventory.py to extract a specific
    prompt by number. Falls back to a descriptive placeholder if parsing
    fails (e.g., prompt doc file structure changes).
    """
    import re as _re

    paths = [
        PROMPT_DOCS_DIR / "hermes-agent-backend-test-prompts.md",
        PROMPT_DOCS_DIR / "hermes-agent-backend-test-prompts-2.md",
        PROMPT_DOCS_DIR / "hermes-agent-backend-test-prompts-4.md",
        PROMPT_DOCS_DIR / "hermes-agent-backend-test-prompts-5.md",
    ]

    for path in paths:
        if not path.exists():
            continue
        content = path.read_text()
        # Normalize: ensure headings start on their own line
        content = _re.sub(r"([^\n])(### \d+\.)", r"\1\n\2", content)
        sections = _re.split(r"^### (\d+)\.\s*(.+)$", content, flags=_re.MULTILINE)
        i = 1
        while i < len(sections):
            num = int(sections[i])
            if num == prompt_id:
                # Return the text body. For multi-section prompts (prompt #1
                # has sub-sections also parsed as #1), take the largest segment.
                body = sections[i + 2].strip() if i + 2 < len(sections) else ""
                if len(body) > 100:
                    return body
            i += 3

    # Fallback: descriptive placeholder
    titles = {
        1: "Container Creation",
        6: "Container Deletion",
        7: "Container List All",
    }
    return f"[Prompt #{prompt_id}: {titles.get(prompt_id, 'Unknown')}]"


def create_chain() -> Chain:
    """Build the Create chain from prompt definitions.

    Returns:
        Chain with 3 steps: create → verify → delete
    """
    steps = []
    for pid, info in CREATE_CHAIN_PROMPTS.items():
        text = _load_prompt_text(pid)
        steps.append(
            ChainStep(
                prompt_id=pid,
                role=info["role"],
                prompt_text=text,
                tool_hint=info["tool_hint"],
            )
        )
    return Chain(name="create", steps=steps)


# ═══════════════════════════════════════════════════════════════════════
# Extended Credit Assignment (Adversarial F5 Fix)
# ═══════════════════════════════════════════════════════════════════════


def credit_assignment(
    outcomes: list[StepOutcome], chain_length: int
) -> dict[int, float]:
    """
    Assign failure blame across chain steps using the EXTENDED heuristic.

    Base heuristic (Plan 130 §4.3):
    - If step N fails and all prior succeeded → blame N (100%)
    - If step N fails and step N-1 had warnings → split 60/40

    Extended (adversarial F5 fix): Instead of checking only step N-1 for
    cascade contributions, check ALL preceding steps. Proportional
    distribution:
    - Failing step gets 40-50% (it's the direct trigger)
    - Preceding warning steps share remaining blame, weighted by recency
    (closer to failure = more blame)

    Returns {step_index: blame_fraction} summing to 1.0 or 0.0.

    Args:
        outcomes: Step outcomes from chain execution
        chain_length: Expected number of steps in the chain
    """
    blame: dict[int, float] = {i: 0.0 for i in range(chain_length)}

    # Find the first real failure
    first_fail_idx = None
    for i, out in enumerate(outcomes):
        if out.status == "failed":
            first_fail_idx = i
            break

    if first_fail_idx is None:
        # No failures — return all zeros (do not waste mutation budget)
        return blame

    # Collect warnings from ALL preceding steps (not just N-1)
    preceding_warnings: list[int] = []
    for j in range(first_fail_idx):
        if outcomes[j].warnings or outcomes[j].status == "warning":
            preceding_warnings.append(j)

    if not preceding_warnings:
        # Simple case: no preceding contamination → blame the failing step
        blame[first_fail_idx] = 1.0
        return blame

    # Multi-step cascade: distribute blame proportionally
    # The failing step gets 40-50% (it's the direct trigger)
    # Preceding warning steps share the remainder

    num_preceding = len(preceding_warnings)

    if num_preceding == 1:
        # Two-step cascade: 40% trigger, 60% predecessor
        blame[first_fail_idx] = 0.4
        blame[preceding_warnings[0]] = 0.6
    else:
        # Multi-step cascade (3+ preceding warnings): distribute
        # Weighted by recency — closer to failure = more blame
        blame[first_fail_idx] = 0.4
        remaining = 0.6

        # Linear distance weights: closest to failure gets highest weight
        weights = {}
        total_weight = 0.0
        for j in preceding_warnings:
            dist = first_fail_idx - j  # 1, 2, 3, ...
            w = 1.0 / dist  # Inverse distance: closer = higher weight
            weights[j] = w
            total_weight += w

        for j in preceding_warnings:
            blame[j] = remaining * weights[j] / total_weight

    return blame


# ═══════════════════════════════════════════════════════════════════════
# Chain Evaluation Orchestration
# ═══════════════════════════════════════════════════════════════════════


def evaluate_chain(
    chain: Chain, outcomes: Optional[list[StepOutcome]] = None
) -> ChainResult:
    """
    Evaluate a mini-chain: run credit assignment and compute composite score.

    This is the orchestration entry point. In simulation mode (used for
    testing and P0.3 validation), pass pre-computed outcomes. In production,
    outcomes are collected by actually executing each prompt through the
    agent (see run_chain() below for the full real-execution path).

    Args:
        chain: The chain definition to evaluate
        outcomes: Pre-computed step outcomes (for simulation/testing).
            If None, run the chain against the real environment.

    Returns:
        ChainResult with blame distribution, composite score, and metadata.
    """
    if outcomes is None:
        outcomes = _execute_chain(chain)

    # Compute blame
    blame = credit_assignment(outcomes, len(chain.steps))

    # Find first failure
    failure_step = None
    for i, out in enumerate(outcomes):
        if out.status == "failed":
            failure_step = i
            break

    # Compute composite score:
    # - If no failures: full score (1.0)
    # - If first step fails: 0.0 (entire chain broken)
    # - If later step fails: score = fraction of steps completed before failure
    if failure_step is None:
        composite = 1.0
    elif failure_step == 0:
        composite = 0.0
    else:
        composite = failure_step / len(chain.steps)

    return ChainResult(
        chain_name=chain.name,
        step_outcomes=outcomes,
        blame=blame,
        composite=composite,
        failure_step=failure_step,
    )


def _execute_chain(chain: Chain) -> list[StepOutcome]:
    """
    Execute the chain against the real environment.

    Runs each prompt through the agent and records the outcome.
    This requires the Apple Container CLI to be available.

    NOTE: This is the production execution path. For testing, use
    simulate_outcomes() instead.
    """
    # In practice, this calls the agent harness for each prompt.
    # The current scaffold returns placeholders — real execution
    # is wired through the GEPA pipeline's score_prompt() or the
    # RewardAdapter's evaluate() API.
    outcomes: list[StepOutcome] = []
    for i, step in enumerate(chain.steps):
        print(f"  [{i}] {step.role}: prompt #{step.prompt_id} — {step.tool_hint}")
        outcome = _execute_single_prompt(step, i)
        outcomes.append(outcome)
        if outcome.status == "failed":
            print(f"      FAILED: {outcome.error[:80] if outcome.error else 'unknown'}")
            break  # Stop on first failure
    return outcomes


def _execute_single_prompt(step: ChainStep, index: int) -> StepOutcome:
    """
    Execute a single prompt through the agent harness.

    This is a placeholder for the production path. In real usage, the
    prompt would be sent to the agent via the test harness, and the
    OTel trajectory would be recorded.

    For the stub, we return a success outcome — real integration
    should replace this with the actual evaluation call.
    """
    # TODO: Replace with actual agent execution + OTel span collection
    # Steps:
    # 1. Send prompt to agent via harness_evolve or evolve_prompts
    # 2. Collect OTel spans for the execution
    # 3. Parse into StepOutcome via RewardAdapter.Span conversion
    return StepOutcome(
        step_index=index,
        role=step.role,
        status="success",
        tool_calls=1,
        duration_ms=0.0,
    )


def simulate_outcomes(chain: Chain, statuses: list[str]) -> list[StepOutcome]:
    """
    Generate simulated step outcomes for testing.

    Args:
        chain: Chain definition
        statuses: Status strings per step ("success", "failed", "warning").
            Must match len(chain.steps). Only first failure is used;
            subsequent steps get "skipped".

    Returns:
        List of StepOutcome matching the chain length, with execution
        stopped at the first failure (subsequent steps get "skipped").
    """
    if len(statuses) != len(chain.steps):
        raise ValueError(f"Expected {len(chain.steps)} statuses, got {len(statuses)}")

    outcomes: list[StepOutcome] = []
    encountered_failure = False

    for i, (step, status) in enumerate(zip(chain.steps, statuses)):
        if encountered_failure:
            outcomes.append(
                StepOutcome(
                    step_index=i,
                    role=step.role,
                    status="skipped",
                    warnings=["predecessor failed — not executed"],
                )
            )
            continue

        if status == "failed":
            encountered_failure = True
            outcomes.append(
                StepOutcome(
                    step_index=i,
                    role=step.role,
                    status="failed",
                    error=f"Prompt #{step.prompt_id} execution failed",
                )
            )
        elif status == "warning":
            outcomes.append(
                StepOutcome(
                    step_index=i,
                    role=step.role,
                    status="warning",
                    warnings=["partial success"],
                )
            )
        else:
            outcomes.append(
                StepOutcome(
                    step_index=i,
                    role=step.role,
                    status="success",
                    tool_calls=1,
                )
            )

    return outcomes


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════


def _test_credit_assignment() -> dict:
    """Run credit assignment test scenarios (P0.3 probe)."""
    tests = [
        {
            "name": "all_success",
            "statuses": ["success", "success", "success"],
            "expect_blame": {0: 0.0, 1: 0.0, 2: 0.0},
            "expect_composite": 1.0,
        },
        {
            "name": "first_step_fails_no_warnings",
            "statuses": ["failed", "skipped", "skipped"],
            "expect_blame": {0: 1.0, 1: 0.0, 2: 0.0},
            "expect_composite": 0.0,
        },
        {
            "name": "second_step_fails_no_warnings",
            "statuses": ["success", "failed", "skipped"],
            "expect_blame": {0: 0.0, 1: 1.0, 2: 0.0},
            "expect_composite": 1 / 3,
        },
        {
            "name": "second_step_fails_after_warning",
            "statuses": ["warning", "failed", "skipped"],
            "expect_blame": {0: 0.6, 1: 0.4, 2: 0.0},
            "expect_composite": 1 / 3,
        },
        {
            "name": "third_step_fails_multi_cascade",
            "statuses": ["warning", "warning", "failed"],
            "expect_blame_mode": "multi_cascade",
            "expect_composite": 2 / 3,
        },
        {
            "name": "third_step_fails_one_warning",
            "statuses": ["success", "warning", "failed"],
            "expect_blame": {0: 0.0, 1: 0.6, 2: 0.4},
            "expect_composite": 2 / 3,
        },
    ]

    chain = create_chain()
    results = []
    all_pass = True

    for test in tests:
        outcomes = simulate_outcomes(chain, test["statuses"])
        result = evaluate_chain(chain, outcomes)
        blame = result.blame
        composite = result.composite

        passed = True
        if "expect_blame" in test:
            passed &= blame == test["expect_blame"]
        if "expect_composite" in test:
            passed &= abs(composite - test["expect_composite"]) < 0.001

        # Multi-cascade: verify step 0 gets blame and failing step gets 0.4
        if test.get("expect_blame_mode") == "multi_cascade":
            passed &= abs(blame[2] - 0.4) < 0.001  # failing step gets 0.4
            passed &= blame[0] > 0.0  # step 0 (create) gets some blame
            passed &= blame[1] > 0.0  # step 1 (verify) gets some blame
            # Verify they sum to 1.0
            total = sum(blame.values())
            passed &= abs(total - 1.0) < 0.001

        if not passed:
            all_pass = False

        results.append(
            {
                "name": test["name"],
                "passed": passed,
                "blame": blame,
                "composite": round(composite, 4),
            }
        )

    return {
        "gate": "P0.3",
        "all_pass": all_pass,
        "test_count": len(tests),
        "pass_count": sum(1 for r in results if r["passed"]),
        "tests": results,
    }


def _format_result(result: ChainResult) -> str:
    """Format a ChainResult for human-readable output."""
    lines = [f"Chain: {result.chain_name}"]
    lines.append(f"Composite: {result.composite:.3f}")
    if result.failure_step is not None:
        lines.append(f"First failure: step {result.failure_step}")
    else:
        lines.append("All steps passed")

    lines.append("\nStep outcomes:")
    for i, out in enumerate(result.step_outcomes):
        status_icon = {
            "success": "  OK",
            "failed": " FAIL",
            "warning": " WARN",
            "skipped": " SKIP",
        }.get(out.status, "  ??")
        lines.append(f"  [{i}] {status_icon} {out.role}")
        if out.error:
            lines.append(f"       error: {out.error[:80]}")

    lines.append("\nBlame distribution:")
    for step_idx, fraction in sorted(result.blame.items()):
        if fraction > 0:
            pct = fraction * 100
            lines.append(f"  Step {step_idx}: {pct:.0f}%")

    return "\n".join(lines)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Plan 130 Phase 4: Mini-Chain evaluation"
    )
    parser.add_argument(
        "--test", action="store_true", help="Run P0.3 credit assignment tests"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed output"
    )
    parser.add_argument("--output", type=str, help="Save test results to JSON file")
    args = parser.parse_args()

    if args.test:
        result = _test_credit_assignment()
        print(f"\n{'=' * 60}")
        print(f"P0.3 Cascade Blame Test: {'PASS' if result['all_pass'] else 'FAIL'}")
        print(f"  {result['pass_count']}/{result['test_count']} scenarios passed")
        print(f"{'=' * 60}")
        for test in result["tests"]:
            icon = "PASS" if test["passed"] else "FAIL"
            print(f"  [{icon}] {test['name']}")
            if not test["passed"] or args.verbose:
                print(f"         blame={test['blame']}, composite={test['composite']}")
        print(f"\nVerdict: {'P0.3 passed' if result['all_pass'] else 'P0.3 failed'}")

        if args.output:
            Path(args.output).write_text(json.dumps(result, indent=2))
            print(f"  Results written to {args.output}")

        sys.exit(0 if result["all_pass"] else 1)

    # Default: run the Create chain (simulated if no real execution)
    chain = create_chain()
    print(f"\n{'=' * 60}")
    print(f"Create Chain: {len(chain.steps)} steps")
    print(f"{'=' * 60}")
    for i, step in enumerate(chain.steps):
        preview = step.prompt_text[:60].replace("\n", " ")
        print(f"  [{i}] {step.role:7s} | prompt #{step.prompt_id:2d} | {preview}...")

    # Run simulation with success outcomes by default
    statuses = (
        args.verbose
        and ["success", "success", "success"]
        or ["success", "success", "success"]
    )
    outcomes = simulate_outcomes(chain, statuses)
    result = evaluate_chain(chain, outcomes)

    print(f"\n{_format_result(result)}")
    return result


if __name__ == "__main__":
    main()
