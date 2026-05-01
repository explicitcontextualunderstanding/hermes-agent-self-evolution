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
    P1, P2, P4,
    PROMPT_TOOLS,
    BASELINE_STATUS,
)

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


# ── Config ─────────────────────────────────────────────────────────────────
COMPOSE_PKL = Path("/Users/kieranlal/workspace/compose-pkl")
EVIDENCE_LOG = COMPOSE_PKL / "docs" / "evolve-evidence.jsonl"

# Size-aware iteration budgets (DIFF.md §8.4)
TIER_BUDGETS = {
    1: {"doc": P1, "prompts": (1, 47), "iterations": 5, "label": "Tier 1: Container Lifecycle"},
    2: {"doc": P2, "prompts": (48, 68), "iterations": 3, "label": "Tier 2: Advanced Orchestration"},
    3: {"doc": P4, "prompts": (69, 91), "iterations": 3, "label": "Tier 3: Host-Native Lane"},
}


def evaluate_prompt_wrapper(prompt_text: str) -> float:
    """Wraps the rubric evaluator. Returns composite score in [0,1]."""
    # Infer tools from the prompt number by looking for tool-like words in text
    for num, tools in PROMPT_TOOLS.items():
        if any(t.replace("_", " ") in prompt_text.lower() for t in tools):
            result = evaluate_prompt(prompt_text, tools)
            return result["composite"]
    result = evaluate_prompt(prompt_text, [])
    return result["composite"]


def optimize_prompt_text(prompt_text: str, tools: list[str], max_calls: int = 10) -> tuple[str, float, float]:
    """Optimize a single prompt using GEPA with a heuristic adapter.

    Uses a custom HeuristicPromptAdapter that scores prompts via the rubric
    directly (no LLM calls for evaluation). GEPA's reflection_lm proposes
    new prompt variants through the kilo-proxy.
    """
    seed = {"prompt": prompt_text}
    # GEPA needs at least 2 training examples for pareto tracking
    dataset = [
        {"input": "eval", "answer": "pass", "additional_context": {}},
        {"input": "eval", "answer": "pass", "additional_context": {}},
    ]

    # Custom adapter: evaluates prompts heuristically (no LLM call)
    adapter = HeuristicPromptAdapter(
        evaluator_fn=lambda text: max(0.0, min(1.0, evaluate_prompt_wrapper(text))),
    )

    # Proxy model for reflection_lm (proposes prompt variants)
    model_name = "openai/nvidia-proxy/deepseek-ai/deepseek-v4-flash"

    # Custom reflection prompt: tells the LLM this is a TEST DESCRIPTION,
    # not an LLM system instruction (prevents Docker-command generation)
    REFLECTION_PROMPT = """I am optimizing a test scenario description for MCP (Model Context Protocol) tool testing.

Current test description:
```
<curr_instructions>
```

The following are rubric evaluations of the current description, including dimensional scores and feedback on what should be improved:
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

Provide the new description within ``` blocks."""

    try:
        result = gepa.optimize(
            seed_candidate=seed,
            trainset=dataset,
            adapter=adapter,
            reflection_lm=model_name,
            reflection_prompt_template=REFLECTION_PROMPT,
            max_metric_calls=max_calls,
            display_progress_bar=True,
        )
        evolved_text = result.best_candidate.get("prompt", prompt_text)
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

    print(f"\n{'='*60}")
    print(f"Canary: evolving prompt #{prompt_num} ({prompt['title']})")
    print(f"{'='*60}")
    print(f"  Tools: {', '.join(tools)}")
    
    original_score = evaluate_prompt_wrapper(original_text)
    print(f"  Baseline score: {original_score:.3f}")
    print(f"  Original length: {len(original_text)} chars")

    evolved_text, _, evolved_score = optimize_prompt_text(original_text, tools, max_calls=10)

    print(f"  Evolved score: {evolved_score:.3f} (delta: {evolved_score - original_score:+.3f})")
    print(f"  Evolved length: {len(evolved_text)} chars")

    evidence = {
        "phase": "G1-canary",
        "prompt_num": prompt_num,
        "baseline_score": original_score,
        "evolved_score": evolved_score,
        "improvement": round(evolved_score - original_score, 4),
        "original_length": len(original_text),
        "evolved_length": len(evolved_text),
    }
    log_evidence(evidence)
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
                # This prompt is in our tier — evolve it
                prompt_text = sections[i + 1] if i + 1 < len(sections) else ""
                tools = PROMPT_TOOLS.get(prompt_num, [])
                orig_score = evaluate_prompt_wrapper(prompt_text)

                evolved_text, _, evolved_score = optimize_prompt_text(
                    prompt_text, tools, max_calls=cfg["iterations"] * 2
                )
                delta = evolved_score - orig_score
                total_improvement += delta
                prompts_evolved += 1

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


def dry_run() -> dict:
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

    evidence = {"gate": "G1-dry-run", "result": "PASS", "inventory_size": len(inventory)}
    log_evidence(evidence)
    print(f"  ✓ Dry-run PASS — ready for G1 canary")
    return evidence


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Evolve compose-pkl MCP test prompts using GEPA")
    parser.add_argument("--dry-run", action="store_true", help="Validate setup without optimizing")
    parser.add_argument("--single-prompt", type=int, default=None, help="Evolve a single prompt (G1 canary)")
    parser.add_argument("--tier", type=str, default=None, choices=["1", "2", "3", "all"], help="Tier to evolve")
    parser.add_argument("--evidence-file", type=str, default=str(EVIDENCE_LOG), help="Path for evidence log")

    args = parser.parse_args()

    inventory = build_inventory()

    if args.dry_run:
        result = dry_run()
        sys.exit(0 if result["result"] == "PASS" else 1)

    if args.single_prompt:
        result = evolve_single_prompt(args.single_prompt, inventory)
        g1_pass = result["improvement"] >= 0
        print(f"\nG1 gate: {'PASS' if g1_pass else 'FAIL'} (improvement={'+' if g1_pass else ''}{result['improvement']:.4f})")
        sys.exit(0 if g1_pass else 1)

    if args.tier:
        tiers = [1, 2, 3] if args.tier == "all" else [int(args.tier)]
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
