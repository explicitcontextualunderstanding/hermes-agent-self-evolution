"""
Prompt Inventory and Fitness Rubric — Phase 0 of Plan 122.

Categorizes all 91 compose-pkl test prompts by tool exercised,
backend dependency, and baseline status. Provides the evaluation
rubric that GEPA's optimize_anything API uses as its fitness signal.

Usage:
    python3 -m evolution.prompts.inventory         # print inventory
    python3 -m evolution.prompts.inventory --rubric  # validate rubric discriminability (G0 gate)
"""

import re
import json
from pathlib import Path
from typing import Optional

# ── Doc paths ──────────────────────────────────────────────────────────────
COMPOSE_PKL = Path("/Users/kieranlal/workspace/compose-pkl")
PROMPTS_DIR = COMPOSE_PKL / "docs"
P1 = PROMPTS_DIR / "hermes-agent-backend-test-prompts.md"
P2 = PROMPTS_DIR / "hermes-agent-backend-test-prompts-2.md"
P4 = PROMPTS_DIR / "hermes-agent-backend-test-prompts-4.md"

# ── Backend tool inventory (from test-results-2026-04-23) ──────────────────
IMPLEMENTED_TOOLS = {
    "list_containers", "create_container", "inspect_container",
    "delete_container", "start_container", "stop_container",
    "exec_in_container", "realize_pod",
    "get_checkpoint", "reset_checkpoint", "get_ltl_state",
    "check_action", "ingest_trace", "analyze_traces", "submit_escalation",
}

MISSING_TOOLS = {
    "agentspec_create", "agentspec_validate", "agentspec_list",
    "checkpoint_create", "checkpoint_restore", "checkpoint_list",
    "checkpoint_analyze", "pod_lifecycle_handler",
}

ALL_TOOLS = IMPLEMENTED_TOOLS | MISSING_TOOLS

# ── Prompt category mapping ───────────────────────────────────────────────
CATEGORY_MAP = {
    (1, 47): "Container Lifecycle & Core MCP",
    (48, 68): "Advanced Orchestration (Plans 105-107)",
    (69, 91): "Host-Native Secure Lane (Plans 108/121)",
}

def get_category(prompt_num: int) -> str:
    for (lo, hi), cat in CATEGORY_MAP.items():
        if lo <= prompt_num <= hi:
            return cat
    return "Unknown"

# ── Baseline status from Apr 23 test run ──────────────────────────────────
# Status categories: PASS, HANG, NOT_IMPL, SKIP, UNKNOWN
# From test-results-2026-04-23-prompts-1-47.md
BASELINE_STATUS = {
    # Container Lifecycle (1-12)
    1: "HANG", 2: "HANG", 3: "HANG", 4: "SKIP", 5: "SKIP",
    6: "PASS", 7: "PASS", 8: "PASS", 9: "PASS", 10: "SKIP",
    11: "SKIP", 12: "SKIP",
    # Pod Lifecycle (13-15)
    13: "HANG", 14: "NOT_IMPL", 15: "NOT_IMPL",
    # AgentSpec (16-21)
    16: "NOT_IMPL", 17: "NOT_IMPL", 18: "NOT_IMPL",
    19: "NOT_IMPL", 20: "NOT_IMPL", 21: "NOT_IMPL",
    # Checkpoint (22-28)
    22: "NOT_IMPL", 23: "NOT_IMPL", 24: "NOT_IMPL",
    25: "NOT_IMPL", 26: "NOT_IMPL", 27: "NOT_IMPL", 28: "NOT_IMPL",
    # Multi-Step (29-32)
    29: "HANG", 30: "HANG", 31: "NOT_IMPL", 32: "HANG",
    # Error Handling (33-36)
    33: "HANG", 34: "HANG", 35: "SKIP", 36: "NOT_IMPL",
    # Stress (37-38)
    37: "HANG", 38: "HANG",
    # Edge Cases (39-45)
    39: "HANG", 40: "SKIP", 41: "HANG", 42: "NOT_IMPL",
    43: "NOT_IMPL", 44: "NOT_IMPL", 45: "PASS",
    # Cleanup (46-47)
    46: "PASS", 47: "PASS",
}

# ── Tool mapping ───────────────────────────────────────────────────────────
PROMPT_TOOLS = {
    # 1-12: Container lifecycle
    1: ["create_container"], 2: ["create_container"], 3: ["create_container"],
    4: ["start_container"], 5: ["stop_container"], 6: ["delete_container"],
    7: ["list_containers"], 8: ["list_containers"], 9: ["inspect_container"],
    10: ["exec_in_container"], 11: ["exec_in_container"], 12: ["exec_in_container"],
    # 13-15: Pod
    13: ["realize_pod"], 14: ["realize_pod"], 15: ["realize_pod"],
    # 16-21: AgentSpec (NOT_IMPL)
    16: ["agentspec_create"], 17: ["agentspec_create"], 18: ["agentspec_create"],
    19: ["agentspec_validate"], 20: ["agentspec_validate"], 21: ["agentspec_list"],
    # 22-28: Checkpoint (NOT_IMPL)
    22: ["checkpoint_create"], 23: ["checkpoint_create"], 24: ["checkpoint_restore"],
    25: ["checkpoint_list"], 26: ["checkpoint_list"], 27: ["checkpoint_analyze"],
    28: ["checkpoint_analyze"],
    # 29-32: Multi-step
    29: ["create_container", "start_container", "inspect_container",
         "exec_in_container", "stop_container", "delete_container", "list_containers"],
    30: ["create_container", "start_container", "checkpoint_create",
         "exec_in_container", "checkpoint_analyze", "checkpoint_restore"],
    31: ["agentspec_create", "agentspec_validate", "agentspec_list",
         "create_container", "start_container", "stop_container",
         "delete_container", "list_containers"],
    32: ["realize_pod", "create_container", "list_containers"],
    # 33-36: Error handling
    33: ["create_container"], 34: ["create_container"], 35: ["exec_in_container"],
    36: ["checkpoint_restore"],
    # 37-38: Stress
    37: ["create_container", "start_container", "stop_container",
         "delete_container", "list_containers"],
    38: ["create_container", "checkpoint_create", "checkpoint_restore"],
    # 39-45: Edge cases
    39: ["create_container"], 40: ["create_container"], 41: ["create_container"],
    42: ["checkpoint_create"], 43: ["checkpoint_list"],
    44: ["agentspec_create", "agentspec_validate"],
    45: ["inspect_container"],
    # 46-47: Cleanup
    46: ["delete_container", "list_containers"],
    47: ["list_containers"],
    # 48-68: Advanced Orchestration (Plans 105-107)
    # RBAC (48-52)
    48: ["check_action"], 49: ["check_action"], 50: ["submit_escalation"],
    51: ["realize_pod"], 52: ["realize_pod"],
    # Creative/Metal (53-55)
    53: ["realize_pod"], 54: ["realize_pod"], 55: ["ingest_trace", "analyze_traces"],
    # Meta-harness (56-58)
    56: ["get_ltl_state", "analyze_traces"], 57: ["analyze_traces"],
    58: ["get_ltl_state", "check_action"],
    # Agent Lifecycle (59-60)
    59: ["agentspec_create"], 60: ["get_checkpoint", "reset_checkpoint"],
    # IPC/Network (61-62)
    61: ["realize_pod"], 62: ["exec_in_container"],
    # Context Routing (63-64)
    63: ["create_container"], 64: ["realize_pod"],
    # Trace Analysis (65-66)
    65: ["ingest_trace", "analyze_traces"], 66: ["analyze_traces", "submit_escalation"],
    # State Transitions (67-68)
    67: ["get_ltl_state", "check_action"], 68: ["get_ltl_state"],
    # 69-91: Host-Native Secure Lane (Plans 108/121)
    # Slab (69-70) — FIXED: was incorrectly using get_checkpoint
    69: ["create_slab", "list_slabs"], 70: ["delete_slab"],
    # MLX (71-73)
    71: ["get_checkpoint"], 72: ["get_checkpoint"], 73: ["get_checkpoint"],
    # Zero-Path/Security (74-75, 78-79)
    74: ["get_checkpoint"], 75: ["exec_in_container"],
    78: ["check_action"], 79: ["check_action"],
    # Error handling (76-77, 80-84)
    76: ["get_checkpoint"], 77: ["get_checkpoint"],
    80: ["get_checkpoint"], 81: ["get_checkpoint", "reset_checkpoint"],
    82: ["get_ltl_state"], 83: ["get_ltl_state"], 84: ["get_checkpoint"],
    # Resilience (85-86)
    85: ["get_checkpoint"], 86: ["get_checkpoint"],
    # Performance (87-88)
    87: ["ingest_trace"], 88: ["ingest_trace"],
    # Concurrency (89-91)
    89: ["create_container", "start_container", "get_checkpoint"],
    90: ["create_container", "start_container", "get_checkpoint"],
    91: ["create_container", "get_checkpoint"],
    # 92-117: Infrastructure Utilities (Networks, Volumes, Images, Pods, Compose)
    # Network Lifecycle (92-95)
    92: ["create_network"], 93: ["list_networks"],
    94: ["delete_network"], 95: ["prune_networks"],
    # Volume Lifecycle (96-99)
    96: ["create_volume"], 97: ["list_volumes"],
    98: ["delete_volume"], 99: ["prune_volumes"],
    # Image Lifecycle (100-103)
    100: ["pull_image"], 101: ["tag_image"],
    102: ["push_image"], 103: ["build_image"],
    # Pod Full Lifecycle (104-108)
    104: ["pod_create", "list_containers"], 105: ["pod_status"],
    106: ["pod_spec"], 107: ["rollback_pod"],
    108: ["pod_delete"],
    # Compose Orchestration (109-110)
    109: ["run_compose_up"], 110: ["run_compose_down"],
    # Maintenance & Cleanup (111-114)
    111: ["stream_container_logs", "exec_in_container"],
    112: ["prune_containers"], 113: ["prune_images"],
    114: ["agentspec_create", "agentspec_delete", "agentspec_list"],
    # Slab Cleanup (115-117) — extends 69-70 coverage
    115: ["create_slab"], 116: ["list_slabs"], 117: ["delete_slab"],
    # Remaining tool coverage (118-120)
    118: ["batch_container_operation", "create_container", "stop_container"],
    119: ["pod_spec"],
    120: ["execute_native_model", "create_slab"],
    121: ["get_pkl_template"],
}


# ── Fitness Rubric ─────────────────────────────────────────────────────────

RUBRIC_DIMENSIONS = {
    "clarity": 0.25,
    "coverage": 0.20,
    "resilience": 0.25,
    "self_containment": 0.15,
    "verifiability": 0.15,
}


def evaluate_clarity(prompt_text: str) -> float:
    """Does the prompt produce a single, unambiguous action?

    +1.0: One clear action verb, no ambiguity
    +0.7: Single action but some ambiguous terms
    +0.4: Multiple possible actions (reader must choose)
    +0.0: Vague or contradictory
    """
    lines = prompt_text.strip().split("\n")
    action_verbs = {"create", "start", "stop", "delete", "list", "inspect",
                    "exec", "execute", "restore", "validate", "check",
                    "attempt", "run", "submit"}
    found = [w for w in action_verbs if any(w in l.lower() for l in lines)]
    if len(found) == 1:
        return 1.0
    elif len(found) == 2:
        return 0.7
    elif len(found) >= 3:
        return 0.4
    return 0.0


def evaluate_coverage(prompt_text: str, tool_names: list[str]) -> float:
    """Does the prompt exercise the intended tool path?

    Checks that the expected tool names appear in the prompt text.
    """
    text_lower = prompt_text.lower()
    found = sum(1 for tool in tool_names if tool.replace("_", " ") in text_lower or tool in text_lower)
    if not tool_names:
        return 0.5
    return min(1.0, found / len(tool_names))


def evaluate_resilience(prompt_text: str) -> float:
    """Does the prompt handle known failure modes gracefully?

    +0.3: Has timeout or error handling language
    +0.3: Has precondition or probe check
    +0.2: Has 'expected' or 'verify' language
    +0.2: Has fallback behavior specified
    """
    text_lower = prompt_text.lower()
    score = 0.0
    if any(w in text_lower for w in ["timeout", "wait", "retry", "max attempt"]):
        score += 0.3
    if any(w in text_lower for w in ["verify", "confirm", "check"]) and any(
        w in text_lower for w in ["expected", "should", "must"]
    ):
        score += 0.3
    if any(w in text_lower for w in ["expected", "if fail", "fallback", "otherwise"]):
        score += 0.2
    if any(w in text_lower for w in ["abort", "report", "capture", "log"]):
        score += 0.2
    return min(1.0, score)


def evaluate_self_containment(prompt_text: str) -> float:
    """Can the prompt run independently of other prompts?

    -0.2 per reference to another prompt number or external state.
    """
    text_lower = prompt_text.lower()
    penalty = 0.0
    # References to other prompts by number
    refs = re.findall(r"prompt\s*#?\d+|step\s+\d+|previous|above", text_lower)
    penalty += 0.2 * len(refs)
    # References to "the container from before" or similar
    if any(w in text_lower for w in ["previous", "from before", "existing", "already"]):
        penalty += 0.3
    return max(0.0, 1.0 - penalty)


def evaluate_verifiability(prompt_text: str) -> float:
    """Does the prompt produce a clear pass/fail signal?

    +0.5: Has explicit pass/fail criteria
    +0.3: Has verification step
    +0.2: Has expected output or behavior
    """
    text_lower = prompt_text.lower()
    score = 0.0
    if any(w in text_lower for w in ["verify that", "confirm that", "should return"]):
        score += 0.5
    elif any(w in text_lower for w in ["check", "verify", "confirm"]):
        score += 0.3
    if any(w in text_lower for w in ["expected", "pass", "fail", "error"]):
        score += 0.2
    return min(1.0, score)


def evaluate_prompt(prompt_text: str, tool_names: Optional[list[str]] = None) -> dict:
    """Score a single prompt on all 5 rubric dimensions. Returns dimension scores + composite."""
    if tool_names is None:
        tool_names = []
    scores = {
        "clarity": evaluate_clarity(prompt_text),
        "coverage": evaluate_coverage(prompt_text, tool_names),
        "resilience": evaluate_resilience(prompt_text),
        "self_containment": evaluate_self_containment(prompt_text),
        "verifiability": evaluate_verifiability(prompt_text),
    }
    composite = sum(scores[d] * RUBRIC_DIMENSIONS[d] for d in RUBRIC_DIMENSIONS)
    return {"dimensions": scores, "composite": round(composite, 4)}


# ── Parse prompts from documents ──────────────────────────────────────────

def parse_prompts(path: Path) -> list[dict]:
    """Parse a prompts doc into individual prompt entries with number and text."""
    content = path.read_text()
    # Split on ### N. heading
    sections = re.split(r"^### (\d+)\.\s+(.+)$", content, flags=re.MULTILINE)
    prompts = []
    # sections format: [..., num, title, body, num, title, body, ...]
    i = 1
    while i < len(sections):
        num = int(sections[i])
        title = sections[i + 1].strip()
        body = sections[i + 2].strip() if i + 2 < len(sections) else ""
        prompts.append({"num": num, "title": title, "text": body})
        i += 3
    return prompts


# ── Inventory ──────────────────────────────────────────────────────────────

def build_inventory() -> list[dict]:
    """Build full prompt inventory across all 3 docs."""
    inventory = []
    for path in [P1, P2, P4]:
        for p in parse_prompts(path):
            n = p["num"]
            tools = PROMPT_TOOLS.get(n, [])
            status = BASELINE_STATUS.get(n, "UNKNOWN")
            has_tool = all(t in IMPLEMENTED_TOOLS for t in tools) if tools else False
            inventory.append({
                "num": n,
                "title": p["title"],
                "category": get_category(n),
                "tools": tools,
                "has_all_tools": has_tool,
                "baseline_status": status,
                "text": p["text"],
            })
    return inventory


# ── G0 Gate: Rubric discriminability check ────────────────────────────────

def g0_gate(inventory: list[dict]) -> dict:
    """Check that the rubric produces a >0.2 score range across all prompts."""
    scores = []
    for prompt in inventory:
        result = evaluate_prompt(prompt["text"], prompt["tools"])
        scores.append(result["composite"])

    score_range = max(scores) - min(scores)
    mean = sum(scores) / len(scores)

    import statistics
    std = statistics.stdev(scores) if len(scores) > 1 else 0.0

    return {
        "gate": "G0",
        "result": "PASS" if score_range > 0.2 else "FAIL",
        "score_range": round(score_range, 4),
        "mean": round(mean, 4),
        "std": round(std, 4),
        "min": round(min(scores), 4),
        "max": round(max(scores), 4),
        "count": len(scores),
        "threshold": 0.2,
        "message": (
            f"Rubric range={score_range:.3f} (threshold >0.2) → {'PASS' if score_range > 0.2 else 'FAIL'}"
            f" | mean={mean:.3f} std={std:.3f}"
        ),
    }


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    inventory = build_inventory()

    if "--rubric" in sys.argv:
        result = g0_gate(inventory)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["result"] == "PASS" else 1)

    # Default: print inventory table
    print(f"{'#':>4} | {'Category':40s} | {'Tools':40s} | {'Status':10s} | {'All Tools?':10s}")
    print("-" * 110)
    passing = 0
    for p in inventory:
        tools_str = ", ".join(p["tools"][:3]) + ("..." if len(p["tools"]) > 3 else "")
        print(f"{p['num']:>4} | {p['category']:40s} | {tools_str:40s} | {p['baseline_status']:10s} | {'✅' if p['has_all_tools'] else '❌':>10s}")
        if p["baseline_status"] == "PASS":
            passing += 1

    print("-" * 110)
    print(f"Total: {len(inventory)} prompts | Passing: {passing} | "
          f"Fail: {sum(1 for p in inventory if p['baseline_status'] == 'FAIL')} | "
          f"Hang: {sum(1 for p in inventory if p['baseline_status'] == 'HANG')} | "
          f"NOT_IMPL: {sum(1 for p in inventory if p['baseline_status'] == 'NOT_IMPL')} | "
          f"SKIP/UNKNOWN: {sum(1 for p in inventory if p['baseline_status'] in ('SKIP', 'UNKNOWN'))}")
