#!/usr/bin/env python3
"""Cost Tracker Report — reads ~/.hermes/cost-tracker.jsonl and produces summary.

Usage:
    python3 scripts/cost-report.py [--last N]
"""

import json
import sys
from pathlib import Path

COST_LOG = Path.home() / ".hermes" / "cost-tracker.jsonl"


def load_entries(path: Path, last_n: int = None) -> list[dict]:
    if not path.exists():
        print(f"✗ Cost tracker not found at {path}")
        sys.exit(1)
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if last_n:
        entries = entries[-last_n:]
    return entries


def main():
    last_n = None
    if len(sys.argv) > 1 and sys.argv[1] == "--last":
        last_n = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    entries = load_entries(COST_LOG, last_n)
    if not entries:
        print("No cost tracker entries found.")
        return

    total_cost = 0
    total_latency = 0
    total_tokens = 0
    successes = 0
    timeouts = 0
    models = {}

    for e in entries:
        model = e.get("model", "?")
        models[model] = models.get(model, 0) + 1
        total_cost += e.get("est_cost_usd", 0)
        total_latency += e.get("latency_s", 0)
        total_tokens += e.get("est_total_tokens", 0)
        if e.get("success", False):
            successes += 1
        else:
            timeouts += 1

    print("=" * 70)
    print("  COST TRACKER REPORT")
    print("=" * 70)
    print(f"\nLog: {COST_LOG}")
    print(f"Entries: {len(entries)} (showing {'all' if not last_n else f'last {last_n}'})")

    print(f"\n── Model Usage ──")
    for model, count in sorted(models.items(), key=lambda x: -x[1]):
        print(f"  {model}: {count} calls")

    print(f"\n── Performance ──")
    print(f"  Successful:  {successes}")
    print(f"  Timeouts:    {timeouts}")
    print(f"  Avg latency: {total_latency / max(1, len(entries)):.1f}s")
    print(f"  Total latency: {total_latency:.0f}s ({total_latency/60:.1f}m)")

    print(f"\n── Cost ──")
    print(f"  Total est. cost: ${total_cost:.6f}")
    print(f"  Avg cost/call:   ${total_cost / max(1, len(entries)):.8f}")
    print(f"  Total est. tokens: {total_tokens:,}")

    if entries:
        latest = entries[-1]
        print(f"\n── Latest Entry ──")
        for k, v in latest.items():
            print(f"  {k}: {v}")

    # Cost per delta (if evidence log available)
    evidence_log = Path.home() / "workspace" / "compose-pkl" / "docs" / "evolve-evidence.jsonl"
    if evidence_log.exists():
        deltas = []
        with open(evidence_log) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        e = json.loads(line)
                        if e.get("phase") == "G1-canary" or "improvement" in e:
                            deltas.append(e.get("improvement", 0))
                    except json.JSONDecodeError:
                        continue
        if deltas:
            print(f"\n── Cost per Delta ──")
            print(f"  Total improvement: {sum(deltas):+.4f}")
            print(f"  Total cost: ${total_cost:.6f}")
            print(f"  Cost per delta point: ${total_cost / max(0.0001, abs(sum(deltas))):.6f}")
            print(f"  Cost per +0.01 improvement: ${total_cost / max(0.0001, abs(sum(deltas))*100):.6f}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
