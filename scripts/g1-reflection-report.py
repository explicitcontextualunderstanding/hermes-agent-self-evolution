#!/usr/bin/env python3
"""G1 Reflection Report — analyzes GEPA canary output from evolve-evidence.jsonl.

Usage:
    python3 scripts/g1-reflection-report.py

Reads the evidence log, extracts G1-canary entries, and produces a structured
reflection report showing: baseline vs evolved scores, delta, enrichment status,
and any failure modes detected.
"""

import json
import sys
from pathlib import Path

EVIDENCE_LOG = Path(__file__).resolve().parent.parent / "compose-pkl" / "docs" / "evolve-evidence.jsonl"
# Fallback: check compose-pkl repo
from evolution.env_config import EVIDENCE_LOG as _EVIDENCE_LOG

FALLBACK = _EVIDENCE_LOG


def load_evidence(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def main():
    path = EVIDENCE_LOG if EVIDENCE_LOG.exists() else FALLBACK
    if not path.exists():
        print(f"✗ Evidence log not found at {path}")
        sys.exit(1)

    entries = load_evidence(path)
    if not entries:
        print("✗ No entries found in evidence log")
        sys.exit(1)

    print("=" * 70)
    print("  G1 REFLECTION REPORT — RewardAwareAdapter Canary")
    print("=" * 70)
    print(f"\nEvidence log: {path}")
    print(f"Total entries: {len(entries)}")

    # Gate entries
    gates = [e for e in entries if e.get("gate")]
    print(f"\n── Integrity Gates ──")
    for g in gates:
        ts = g.get("_ts", "?").replace("T", " ")[:19]
        print(f"  [{ts}] {g['gate']}: {g['result']}")

    # Canary entries
    canaries = [e for e in entries if e.get("phase") == "G1-canary"]
    if canaries:
        print(f"\n── G1 Canary Results ──")
        for c in canaries:
            ts = c.get("_ts", "?").replace("T", " ")[:19]
            delta = c.get("improvement", 0)
            sign = "+" if delta > 0 else ""
            status = "✅ PASS" if delta >= 0 else "❌ FAIL"
            print(f"  [{ts}] Prompt #{c.get('prompt_num', '?')} "
                  f"baseline={c['baseline_score']:.3f} → evolved={c['evolved_score']:.3f} "
                  f"({sign}{delta:.4f}) {status}")

        # Summary
        scores = [c["baseline_score"] for c in canaries]
        deltas = [c["improvement"] for c in canaries]
        print(f"\n── Summary ──")
        print(f"  Prompts evolved: {len(canaries)}")
        print(f"  Avg baseline:    {sum(scores)/len(scores):.3f}")
        print(f"  Avg improvement: {sum(deltas)/len(deltas):.4f}")
        print(f"  Max improvement: {max(deltas):.4f}")
        print(f"  Min improvement: {min(deltas):.4f}")
        print(f"  Non-zero deltas: {sum(1 for d in deltas if d != 0)}/{len(deltas)}")
    else:
        print("\n  No G1-canary entries found.")

    # Tier entries
    tiers = [e for e in entries if e.get("phase", "").startswith("tier")]
    if tiers:
        print(f"\n── Tier Evolution Results ──")
        for t in tiers:
            ts = t.get("_ts", "?").replace("T", " ")[:19]
            print(f"  [{ts}] {t['phase']}: {t.get('doc', '?')} "
                  f"({t.get('prompts_evolved', 0)} prompts, "
                  f"avg delta={t.get('avg_improvement', 0):.4f})")

    # Check for enriched feedback markers
    raw = path.read_text()
    has_simulated = "[SIMULATED]" in raw
    has_classification = "classification" in raw.lower()
    has_insight = "reasoning insight" in raw.lower()
    print(f"\n── Enrichment Status ──")
    print(f"  [SIMULATED] labels:    {'✅ Present' if has_simulated else '❌ Absent'}")
    print(f"  Classification field:  {'✅ Present' if has_classification else '❌ Absent'}")
    print(f"  Reasoning insight:     {'✅ Present' if has_insight else '❌ Absent'}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
