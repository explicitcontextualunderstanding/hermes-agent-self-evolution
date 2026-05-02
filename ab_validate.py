#!/usr/bin/env python3
"""
Phase 2: A/B validate OTel vs heuristic evaluator on 12-15 prompts.
Compares OTel composite scores against heuristic rubric scores.

Usage:
    python3 ab_validate.py                    # Run full A/B validation
    python3 ab_validate.py --test-only         # Only run the discriminability test
    python3 ab_validate.py --dry-run           # Show prompts without running
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Optional, Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s", stream=sys.stdout, force=True)
logger = logging.getLogger(__name__)

# ── Paths ───────────────────────────────────────────────────────────────────
WORKSPACE = Path("/Users/kieranlal/workspace/hermes-agent-self-evolution")
COMPOSE_PKL = Path("/Users/kieranlal/workspace/compose-pkl")
PROMPTS_DIR = COMPOSE_PKL / "docs"
HERMES_BIN = "/Users/kieranlal/.hermes/hermes-agent/venv/bin/hermes"
PROFILE = "coding"
OUTPUT_FILE = WORKSPACE / "ab-otel-validation-results.jsonl"
TEST_FILE = WORKSPACE / "ab_otel_test.py"

# Database config
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5432,
    "user": "postgres",
    "database": "harness_evolution",
}

# Add compose-pkl .venv to path for pg8000
COMPOSE_PKL_VENV = COMPOSE_PKL / ".venv" / "lib"
if COMPOSE_PKL_VENV.exists():
    for p in COMPOSE_PKL_VENV.iterdir():
        site_pkg = p / "site-packages"
        if site_pkg.exists():
            sys.path.insert(0, str(site_pkg))
            break

# ── Target prompts ─────────────────────────────────────────────────────────
TARGET_PROMPTS = [
    {"num": 1,  "name": "create_container",   "category": "hanging",   "expected": "PASS"},
    {"num": 3,  "name": "stop_container",     "category": "hanging",   "expected": "TBD"},
    {"num": 5,  "name": "delete_container",   "category": "dependent", "expected": "TBD"},
    {"num": 6,  "name": "delete_again",       "category": "hanging",   "expected": "PASS"},
    {"num": 10, "name": "start_container",    "category": "dependent", "expected": "TBD"},
    {"num": 16, "name": "agentspec_create",   "category": "ghost",     "expected": "TOOL_NOT_FOUND"},
    {"num": 33, "name": "error_handling",     "category": "working",   "expected": "PASS"},
    {"num": 45, "name": "inspect_ghost",      "category": "ghost",     "expected": "PASS"},
    {"num": 48, "name": "compose_up",         "category": "ghost",     "expected": "TBD"},
    {"num": 51, "name": "realize_pod",        "category": "dependent", "expected": "PASS"},
    {"num": 64, "name": "capability",         "category": "dependent", "expected": "TBD"},
    {"num": 69, "name": "mlx_inference",      "category": "dependent", "expected": "TBD"},
    {"num": 75, "name": "native_slab",        "category": "ghost",     "expected": "TBD"},
]


# ── Prompt extraction ──────────────────────────────────────────────────────

def extract_prompts_doc1(path: Path) -> dict[int, str]:
    """Extract prompts from doc 1 (prompts 1-47).
    
    Headings have format `### N.[not-space]...` but can be embedded anywhere 
    in the content (not necessarily at line start). Also handles doc 4 style 
    with code blocks for prompts >= 69.
    """
    content = path.read_text()
    prompts = {}
    
    # Find all prompt headings anywhere in content (not just line-start)
    # Matches: ### 5.container_stop, ### 1.# title, ### 33.# title, ### 45.## title
    # But NOT: ### 1. Container Creation (sub-heading, has space after dot)
    all_headings = []
    for m in re.finditer(r'### (\d+)\.([^ \n].*)', content):
        num = int(m.group(1))
        all_headings.append((m.start(), m.end(), num))
    
    # Sort by position
    all_headings.sort(key=lambda x: x[0])
    
    for i, (hstart, hend, num) in enumerate(all_headings):
        # Get text from end of heading to start of next heading (or end of file)
        if i + 1 < len(all_headings):
            next_start = all_headings[i + 1][0]
        else:
            next_start = len(content)
        text = content[hend:next_start].strip()
        prompts[num] = text
    
    return prompts


def extract_prompts_doc2(path: Path) -> dict[int, str]:
    """Extract prompts from docs 2&4 where content is inside ``` code blocks."""
    content = path.read_text()
    prompts = {}
    for m in re.finditer(r'^### (\d+)\.\s+(.+)$', content, re.MULTILINE):
        num = int(m.group(1))
        # Find the next ``` block after this heading
        rest = content[m.end():]
        code_m = re.search(r'```\n(.*?)```', rest, re.DOTALL)
        if code_m:
            text = code_m.group(1).strip()
            if text:
                prompts[num] = text
    return prompts


def load_all_prompts() -> dict[int, str]:
    """Load all target prompts, merging from appropriate docs."""
    p1 = PROMPTS_DIR / "hermes-agent-backend-test-prompts.md"
    p2 = PROMPTS_DIR / "hermes-agent-backend-test-prompts-2.md"
    p4 = PROMPTS_DIR / "hermes-agent-backend-test-prompts-4.md"

    # Doc 1: prompts 1-47 (narrative format)
    doc1_prompts = extract_prompts_doc1(p1)

    # Doc 2: prompts 48-68 (code block format)
    doc2_prompts = extract_prompts_doc2(p2)

    # Doc 4: prompts 69-91 (code block format)
    doc4_prompts = extract_prompts_doc2(p4)

    result = {}
    result.update(doc1_prompts)
    result.update(doc2_prompts)
    result.update(doc4_prompts)
    return result


# ── Heuristic scoring ──────────────────────────────────────────────────────

# Import from inventory
sys.path.insert(0, str(WORKSPACE))
try:
    from evolution.prompts.inventory import evaluate_prompt, PROMPT_TOOLS
except ImportError as e:
    logger.warning(f"Could not import from inventory: {e}")
    # Fallback stub
    def evaluate_prompt(text, tools=None):
        return {"dimensions": {}, "composite": 0.5}
    PROMPT_TOOLS = {}


def get_heuristic_score(prompt_num: int, prompt_text: str) -> float:
    """Compute heuristic rubric score."""
    tools = PROMPT_TOOLS.get(prompt_num, [])
    result = evaluate_prompt(prompt_text, tools)
    return result["composite"]


# ── Hermes runner ──────────────────────────────────────────────────────────

def _extract_session_id(output: str) -> Optional[str]:
    """Extract session ID from hermes output."""
    m = re.search(r"hermes\s+--resume\s+(\S+)", output)
    return m.group(1) if m else None


def run_hermes(prompt: str, max_turns: int = 10, timeout: int = 120) -> dict:
    """Run a prompt through Hermes Agent chat -q."""
    start = time.time()
    try:
        cmd = [
            HERMES_BIN, "-p", PROFILE, "chat",
            "-q", prompt,
            "--max-turns", str(max_turns),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        elapsed = (time.time() - start) * 1000
        combined = result.stdout + result.stderr
        session_id = _extract_session_id(combined)
        return {
            "response": result.stdout,
            "duration_ms": round(elapsed, 1),
            "session_id": session_id,
            "error": None if result.returncode == 0 else (result.stderr.strip() or "non_zero_exit"),
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "response": "", "duration_ms": timeout * 1000,
            "session_id": None, "error": "TIMEOUT", "returncode": -1,
        }
    except FileNotFoundError:
        return {
            "response": "", "duration_ms": 0,
            "session_id": None, "error": f"Hermes not found at {HERMES_BIN}", "returncode": -2,
        }
    except Exception as e:
        return {
            "response": "", "duration_ms": (time.time() - start) * 1000,
            "session_id": None, "error": str(e), "returncode": -3,
        }


# ── OTel score computation ─────────────────────────────────────────────────

def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def compute_otel_scores(span_attrs: dict, duration_ms: float = 0.0) -> dict:
    """Compute OTel scores from span attributes.
    
    Dimensions:
        - pass: 1.0 if final_status == 'completed' else 0.0 (50%)
        - efficiency: clip(1.0 - duration_ms/30000, 0, 1) (20%)
        - tool_efficiency: 1.0 / max(api_call_count, 1) (20%)
        - token_efficiency: clip(1.0 - total_tokens/100000, 0, 1) (10%)
        - composite: weighted sum
    """
    # Pass/fail
    status = span_attrs.get("hermes.turn.final_status", "")
    otel_pass = 1.0 if status == "completed" else 0.0

    # Duration efficiency
    if duration_ms <= 0:
        duration_ms = float(span_attrs.get("llm.response.duration_ms", 0))
    efficiency = _clip(1.0 - duration_ms / 30000.0, 0.0, 1.0)

    # Tool efficiency
    api_calls = int(span_attrs.get("hermes.turn.api_call_count", 1) or 1)
    tool_efficiency = 1.0 / max(api_calls, 1)

    # Token efficiency
    total_tokens = int(span_attrs.get("llm.token_count.total", 0) or 0)
    token_efficiency = _clip(1.0 - total_tokens / 100000.0, 0.0, 1.0)

    # Composite: pass*0.5 + efficiency*0.2 + tool_efficiency*0.2 + token_efficiency*0.1
    composite = (otel_pass * 0.5 + efficiency * 0.2 + tool_efficiency * 0.2 + token_efficiency * 0.1)

    return {
        "pass": round(otel_pass, 4),
        "efficiency": round(efficiency, 4),
        "tool_efficiency": round(tool_efficiency, 4),
        "token_efficiency": round(token_efficiency, 4),
        "composite": round(composite, 4),
    }


def _get_span_attributes(span: dict) -> dict:
    """Get span attributes, parsing from JSON if needed."""
    attrs = span.get("attributes", {}) or {}
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except (json.JSONDecodeError, TypeError):
            attrs = {}
    return attrs


def query_otel_spans(session_id: str) -> list[dict]:
    """Query otel_spans table for spans matching session_id."""
    try:
        import pg8000
        conn = pg8000.connect(
            host=DB_CONFIG["host"],
            port=DB_CONFIG["port"],
            user=DB_CONFIG["user"],
            database=DB_CONFIG["database"],
        )
        cur = conn.cursor()
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
        col_names = [
            "span_id", "trace_id", "parent_span_id", "name", "kind",
            "start_time", "end_time", "duration_ms", "status_code",
            "status_message", "attributes", "events", "links",
            "resource_attributes", "scope_name", "scope_version",
            "service_name", "ingested_at",
        ]
        results = []
        for row in rows:
            d = dict(zip(col_names, row))
            d["attributes"] = _get_span_attributes(d)
            results.append(d)
        cur.close()
        conn.close()
        return results
    except Exception as e:
        logger.warning(f"OTel query failed: {e}")
        return []


# ── Main evaluation ───────────────────────────────────────────────────────

def evaluate_prompt_otel(prompt_text: str) -> dict:
    """Run prompt through hermes, query OTel, compute scores."""
    hermes_result = run_hermes(prompt_text, max_turns=10, timeout=120)
    
    duration_ms = hermes_result.get("duration_ms", 0)
    session_id = hermes_result.get("session_id")
    error = hermes_result.get("error")
    returncode = hermes_result.get("returncode", 0)
    
    # If no session_id, we can still compute basic OTel scores from hermes result
    if not session_id or error:
        otel_scores = {
            "pass": 0.0,
            "efficiency": _clip(1.0 - duration_ms / 30000.0, 0.0, 1.0),
            "tool_efficiency": 0.0,
            "token_efficiency": 0.0,
            "composite": 0.0,
        }
        status = "error" if error else "no_session"
        return {
            "status": status,
            "otel_scores": otel_scores,
            "duration_ms": duration_ms,
            "session_id": session_id or "",
            "error": error or "",
            "api_calls": 0,
            "response_snippet": hermes_result.get("response", "")[:200],
        }
    
    # Query OTel spans
    spans = query_otel_spans(session_id)
    
    if not spans:
        # No spans: use hermes result only
        otel_scores = compute_otel_scores({}, duration_ms)
        return {
            "status": "no_spans",
            "otel_scores": otel_scores,
            "duration_ms": duration_ms,
            "session_id": session_id,
            "error": "",
            "api_calls": 0,
            "response_snippet": hermes_result.get("response", "")[:200],
        }
    
    # Use 'agent' span or first span as primary
    agent_spans = [s for s in spans if s.get("name") == "agent"]
    primary = agent_spans[0] if agent_spans else spans[0]
    attrs = primary.get("attributes", {}) or {}
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except (json.JSONDecodeError, TypeError):
            attrs = {}
    
    span_duration = primary.get("duration_ms", 0) or 0
    if span_duration > 0:
        duration_ms = span_duration
    
    api_calls = int(attrs.get("hermes.turn.api_call_count", 0) or 0)
    
    otel_scores = compute_otel_scores(attrs, duration_ms)
    
    return {
        "status": "completed",
        "otel_scores": otel_scores,
        "duration_ms": duration_ms,
        "session_id": session_id,
        "error": "",
        "api_calls": api_calls,
        "num_spans": len(spans),
        "response_snippet": hermes_result.get("response", "")[:200],
    }


def run_validation() -> list[dict]:
    """Run A/B validation across all target prompts."""
    prompt_map = load_all_prompts()
    results = []
    
    for target in TARGET_PROMPTS:
        num = target["num"]
        text = prompt_map.get(num)
        
        if not text:
            logger.warning(f"Prompt #{num} ({target['name']}): text not found, skipping")
            continue
        
        logger.info(f"=== Evaluating Prompt #{num}: {target['name']} ({target['category']}) ===")
        
        # Heuristic score
        heuristic_score = get_heuristic_score(num, text)
        logger.info(f"  Heuristic score: {heuristic_score}")
        
        # OTel evaluation via hermes
        try:
            otel_result = evaluate_prompt_otel(text)
            logger.info(f"  OTel status: {otel_result['status']}")
            logger.info(f"  OTel composite: {otel_result['otel_scores']['composite']}")
            logger.info(f"  OTel pass: {otel_result['otel_scores']['pass']}")
            logger.info(f"  Duration: {otel_result['duration_ms']}ms")
            logger.info(f"  API calls: {otel_result.get('api_calls', 0)}")
            logger.info(f"  Session: {otel_result.get('session_id', 'N/A')}")
        except Exception as e:
            logger.error(f"  OTel evaluation failed: {e}")
            traceback.print_exc()
            otel_result = {
                "status": "error",
                "otel_scores": {"pass": 0.0, "efficiency": 0.0, "tool_efficiency": 0.0, "token_efficiency": 0.0, "composite": 0.0},
                "duration_ms": 0, "session_id": "", "error": str(e), "api_calls": 0,
            }
        
        # Build result entry
        entry = {
            "prompt_num": num,
            "name": target["name"],
            "category": target["category"],
            "expected": target["expected"],
            "heuristic_score": heuristic_score,
            "otel_pass": otel_result["otel_scores"]["pass"],
            "otel_efficiency": otel_result["otel_scores"]["efficiency"],
            "otel_tool_efficiency": otel_result["otel_scores"]["tool_efficiency"],
            "otel_token_efficiency": otel_result["otel_scores"]["token_efficiency"],
            "otel_composite": otel_result["otel_scores"]["composite"],
            "duration_ms": otel_result.get("duration_ms", 0),
            "api_calls": otel_result.get("api_calls", 0),
            "status": otel_result.get("status", "unknown"),
            "session_id": otel_result.get("session_id", ""),
            "num_spans": otel_result.get("num_spans", 0),
        }
        results.append(entry)
        logger.info(f"  → Entry saved: heuristic={heuristic_score}, otel={entry['otel_composite']}")
        logger.info("")
    
    return results


def save_results(results: list[dict]):
    """Save results as JSONL."""
    with open(OUTPUT_FILE, "w") as f:
        for entry in results:
            f.write(json.dumps(entry) + "\n")
    logger.info(f"Saved {len(results)} results to {OUTPUT_FILE}")


# ── Test: OTel discriminates better ──────────────────────────────────────

TEST_CODE = '''"""
TDD Test: OTel scores must have wider range than heuristic scores.
Run with: python3 -m pytest ab_otel_test.py -v
"""
import json
from pathlib import Path

RESULTS_FILE = Path(__file__).parent / "ab-otel-validation-results.jsonl"


def load_results() -> list[dict]:
    results = []
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    results.append(json.loads(line))
    return results


def test_otel_has_wider_range_than_heuristic():
    """OTel scores should show wider range than heuristic scores, proving better discrimination."""
    results = load_results()
    assert len(results) >= 10, f"Need at least 10 results, got {len(results)}"
    
    heuristic_scores = [r["heuristic_score"] for r in results]
    otel_scores = [r["otel_composite"] for r in results]
    
    heuristic_range = max(heuristic_scores) - min(heuristic_scores)
    otel_range = max(otel_scores) - min(otel_scores)
    
    print(f"\\n  Heuristic scores: min={min(heuristic_scores):.3f}, max={max(heuristic_scores):.3f}, range={heuristic_range:.3f}")
    print(f"  OTel scores:      min={min(otel_scores):.3f}, max={max(otel_scores):.3f}, range={otel_range:.3f}")
    print(f"  Range ratio: OTel/Heuristic = {otel_range/max(heuristic_range, 0.001):.2f}x")
    
    assert otel_range > heuristic_range, (
        f"OTel range ({otel_range:.3f}) should be wider than heuristic range ({heuristic_range:.3f})"
    )


def test_all_results_have_required_fields():
    """Verify all result entries have the required fields."""
    results = load_results()
    assert len(results) > 0, "No results loaded"
    required = ["prompt_num", "category", "heuristic_score", "otel_composite", "duration_ms", "status"]
    for r in results:
        for field in required:
            assert field in r, f"Missing field '{field}' in result for prompt #{r.get('prompt_num', '?')}"


def test_at_least_12_results():
    """Should have at least 12 results (as specified in the task)."""
    results = load_results()
    assert len(results) >= 12, f"Expected at least 12 results, got {len(results)}"


if __name__ == "__main__":
    results = load_results()
    if not results:
        print("FAIL: No results found. Run `python3 ab_validate.py` first.")
        sys.exit(1)
    print(f"Loaded {len(results)} results from {RESULTS_FILE}")
    
    heuristic_scores = [r["heuristic_score"] for r in results]
    otel_scores = [r["otel_composite"] for r in results]
    heuristic_range = max(heuristic_scores) - min(heuristic_scores)
    otel_range = max(otel_scores) - min(otel_scores)
    
    print(f"\\n=== A/B Validation Results ===")
    print(f"  Heuristic scores: min={min(heuristic_scores):.3f}, max={max(heuristic_scores):.3f}, range={heuristic_range:.3f}")
    print(f"  OTel scores:      min={min(otel_scores):.3f}, max={max(otel_scores):.3f}, range={otel_range:.3f}")
    print(f"  Range ratio: OTel/Heuristic = {otel_range/max(heuristic_range, 0.001):.2f}x")
    
    if otel_range > heuristic_range:
        print(f"\\n  ✅ TEST PASSED: OTel range ({otel_range:.3f}) > heuristic range ({heuristic_range:.3f})")
        print(f"  → OTel discriminates better between passing and failing prompts")
    else:
        print(f"\\n  ❌ TEST FAILED: OTel range ({otel_range:.3f}) <= heuristic range ({heuristic_range:.3f})")
        print(f"  → Heuristic has comparable or better discrimination")
    
    print(f"\\nPer-prompt breakdown:")
    print(f"{'#':>4} {'Name':20s} {'Category':12s} {'Heuristic':>10s} {'OTel':>10s} {'Status':12s}")
    print("-" * 70)
    for r in results:
        print(f"{r['prompt_num']:>4} {r['name']:20s} {r['category']:12s} {r['heuristic_score']:>10.3f} {r['otel_composite']:>10.3f} {r['status']:12s}")
'''


def write_test():
    with open(TEST_FILE, "w") as f:
        f.write(TEST_CODE)
    logger.info(f"Wrote test file: {TEST_FILE}")


def run_test():
    """Run the discriminability test."""
    results = []
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    results.append(json.loads(line))
    
    if not results:
        logger.error("No results found. Run validation first.")
        return False
    
    heuristic_scores = [r["heuristic_score"] for r in results]
    otel_scores = [r["otel_composite"] for r in results]
    heuristic_range = max(heuristic_scores) - min(heuristic_scores)
    otel_range = max(otel_scores) - min(otel_scores)
    
    logger.info(f"=== Discriminability Test ===")
    logger.info(f"  Heuristic range: {heuristic_range:.4f}")
    logger.info(f"  OTel range:      {otel_range:.4f}")
    logger.info(f"  Range ratio:     {otel_range/max(heuristic_range, 0.001):.2f}x")
    
    if otel_range > heuristic_range:
        logger.info(f"  ✅ PASS: OTel ({otel_range:.4f}) > heuristic ({heuristic_range:.4f})")
        return True
    else:
        logger.info(f"  ❌ FAIL: OTel ({otel_range:.4f}) <= heuristic ({heuristic_range:.4f})")
        return False


# ── Dry run ────────────────────────────────────────────────────────────────

def dry_run():
    """Show what prompts will be evaluated without running them."""
    prompt_map = load_all_prompts()
    print("=== Dry Run: Prompts to evaluate ===\n")
    for target in TARGET_PROMPTS:
        num = target["num"]
        text = prompt_map.get(num)
        heuristic_score = get_heuristic_score(num, text or "")
        print(f"Prompt #{num:>3}: {target['name']:20s} [{target['category']:12s}] "
              f"expected={target['expected']:15s} heuristic={heuristic_score:.3f} "
              f"text_len={len(text or 'MISSING')}")
    print(f"\nTotal: {len(TARGET_PROMPTS)} prompts")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    import argparse
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="A/B Validate OTel vs Heuristic")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--test-only", action="store_true", help="Run test only")
    args = parser.parse_args()
    
    if args.dry_run:
        dry_run()
        return
    
    if args.test_only:
        passed = run_test()
        print(f"\n{'PASS' if passed else 'FAIL'}: OTel broader range than heuristic")
        return
    
    # Write test first (TDD)
    write_test()
    
    # Run validation
    logger.info("Starting A/B validation of OTel vs heuristic evaluator...")
    logger.info(f"Target: {len(TARGET_PROMPTS)} prompts\n")
    
    results = run_validation()
    
    if not results:
        logger.error("No results collected!")
        return
    
    save_results(results)
    
    # Run test
    passed = run_test()
    
    # Print summary
    logger.info("\n=== Summary ===")
    logger.info(f"Total prompts evaluated: {len(results)}")
    heuristic_scores = [r["heuristic_score"] for r in results]
    otel_scores = [r["otel_composite"] for r in results]
    logger.info(f"Heuristic: range={max(heuristic_scores)-min(heuristic_scores):.4f}, "
                f"mean={sum(heuristic_scores)/len(heuristic_scores):.4f}")
    logger.info(f"OTel:      range={max(otel_scores)-min(otel_scores):.4f}, "
                f"mean={sum(otel_scores)/len(otel_scores):.4f}")
    logger.info(f"Results saved to: {OUTPUT_FILE}")
    logger.info(f"Test file: {TEST_FILE}")
    
    if passed:
        logger.info("✅ OTel scores discriminate better than heuristic scores!")
    else:
        logger.info("⚠️  OTel range does not exceed heuristic range (yet)")
    
    print("\nDone.")


if __name__ == "__main__":
    main()
