"""
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
    
    print(f"\n  Heuristic scores: min={min(heuristic_scores):.3f}, max={max(heuristic_scores):.3f}, range={heuristic_range:.3f}")
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
    
    print(f"\n=== A/B Validation Results ===")
    print(f"  Heuristic scores: min={min(heuristic_scores):.3f}, max={max(heuristic_scores):.3f}, range={heuristic_range:.3f}")
    print(f"  OTel scores:      min={min(otel_scores):.3f}, max={max(otel_scores):.3f}, range={otel_range:.3f}")
    print(f"  Range ratio: OTel/Heuristic = {otel_range/max(heuristic_range, 0.001):.2f}x")
    
    if otel_range > heuristic_range:
        print(f"\n  ✅ TEST PASSED: OTel range ({otel_range:.3f}) > heuristic range ({heuristic_range:.3f})")
        print(f"  → OTel discriminates better between passing and failing prompts")
    else:
        print(f"\n  ❌ TEST FAILED: OTel range ({otel_range:.3f}) <= heuristic range ({heuristic_range:.3f})")
        print(f"  → Heuristic has comparable or better discrimination")
    
    print(f"\nPer-prompt breakdown:")
    print(f"{'#':>4} {'Name':20s} {'Category':12s} {'Heuristic':>10s} {'OTel':>10s} {'Status':12s}")
    print("-" * 70)
    for r in results:
        print(f"{r['prompt_num']:>4} {r['name']:20s} {r['category']:12s} {r['heuristic_score']:>10.3f} {r['otel_composite']:>10.3f} {r['status']:12s}")
