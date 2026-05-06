#!/opt/homebrew/bin/python3.14
"""kanban-readiness.py — Complexity fingerprint + readiness assessment.

Analyzes a kanban task to produce a multidimensional complexity profile
that feeds into RCF calibration, TOC constraint identification, and the
classification gate. Compares against historical outcomes to sharpen
future forecasts.

Usage:
    # Assess a single task
    python3 kanban-readiness.py t_abc123

    # Assess all tasks in READY state
    python3 kanban-readiness.py --ready

    # Compare against historical calibration database
    python3 kanban-readiness.py --calibrate

    # Output as JSON for programmatic consumption
    python3 kanban-readiness.py t_abc123 --json
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

KANBAN_CLI = "/Users/kieranlal/.hermes/hermes-agent/venv/bin/hermes"
CALIBRATION_DB = Path("/tmp/kanban-readiness-calibration.json")
WIP_LIMIT = 3
REDIR = "\033[91m"  # Red
GREEN = "\033[92m"  # Green
YELLOW = "\033[93m"  # Yellow
CYAN = "\033[96m"    # Cyan
RESET = "\033[0m"


# ── Complexity Fingerprint ──────────────────────────────────────────────

REASONING_KEYWORDS = [
    "design", "architecture", "figure out", "investigate", "cross-file",
    "research", "analyze", "synthesize", "decide", "multi-step",
    "debug", "root cause", "TDD", "test plan", "plan",
]

INFRASTRUCTURE_KEYWORDS = [
    "container", "docker", "vm", "vz", "virtualization", "apple",
    "daemon", "service", "port", "network", "volume", "bridge",
    "k3s", "kubernetes", "pod", "deploy", "certificate", "key",
    "api key", "token", "oauth", "secret", "permission", "entitlement",
]

MECHANICAL_OVERRIDE_PREFIXES = [
    "run", "execute", "dispatch", "archive", "close", "complete",
]


def get_task_json(task_id: str) -> dict | None:
    """Fetch task metadata via kanban CLI."""
    cmd = [KANBAN_CLI, "kanban", "show", task_id, "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
        # The API wraps task data under a 'task' key
        if "task" in data:
            return data["task"]
        return data
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def get_task_runs(task_id: str) -> list[dict]:
    """Fetch run history for a task."""
    cmd = [KANBAN_CLI, "kanban", "runs", task_id, "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return []
        runs = json.loads(r.stdout)
        if isinstance(runs, list):
            return runs
        return []
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


def classify_task(title: str, body: str) -> tuple[str, float, str]:
    """Classify as mechanical or reasoning with confidence."""
    title_lower = title.lower()
    body_lower = body.lower()
    combined = f"{title_lower} {body_lower}"

    # Check mechanical override prefixes
    for prefix in MECHANICAL_OVERRIDE_PREFIXES:
        if title_lower.startswith(prefix):
            return ("mechanical", 0.95, f"keyword_override: {prefix}")

    # Score reasoning keyword matches
    for kw in REASONING_KEYWORDS:
        if kw in combined:
            return ("reasoning", 0.85, f"keyword_match: {kw}")

    return ("mechanical", 0.60, "no_reasoning_keywords")


def extract_complexity_fingerprint(task: dict) -> dict:
    """Extract multidimensional complexity profile from a task."""
    title = task.get("title", "") or ""
    body = task.get("body", "") or ""
    combined = f"{title} {body}".lower()
    assignee = task.get("assignee", "") or ""
    parents = task.get("parents") or []
    skills = task.get("skills") or []
    result_data = task.get("result", "") or ""
    workspace = task.get("workspace_kind", "") or ""

    # 1. Classification
    classification, confidence, method = classify_task(title, body)

    # 2. Dependency depth
    dep_depth = len(parents)

    # 3. Cross-repo count
    repo_paths = set()
    for match in re.finditer(r'/Users/kieranlal/workspace/([^/]+)', body):
        repo_paths.add(match.group(1))
    cross_repo_count = len(repo_paths)

    # 4. Infrastructure touchpoints
    infra_touchpoints = []
    for kw in INFRASTRUCTURE_KEYWORDS:
        if kw in combined:
            infra_touchpoints.append(kw)

    # 5. Body specificity ratio
    # Measure proportion of references to files, classes, functions, paths
    ref_chars = 0
    ref_chars += len(re.findall(r'`[^`]+`', body))  # inline code
    ref_chars += len(re.findall(r'/[\w./_-]+\.\w+', body))  # file paths
    ref_chars += len(re.findall(r'\b[A-Z][a-zA-Z]+\(\)', body))  # function calls
    ref_chars += len(re.findall(r'§[^.\n]+', body))  # plan section refs
    total_chars = len(body)
    specificity_ratio = min(ref_chars / max(total_chars, 1), 1.0)

    # 6. Body length adequacy
    body_lines = len([l for l in body.split("\n") if l.strip()])
    body_length_score = min(body_lines / 8, 1.0)  # 8 lines is full score

    # 7. Flag coverage
    has_assignee = bool(assignee) and assignee not in ("none", "")
    has_max_runtime = bool(result_data) and ("max-runtime" in str(result_data) or "max_runtime" in str(result_data))
    has_parents = len(parents) > 0
    has_skills = len(skills) > 0
    has_workspace = bool(workspace) and workspace != "scratch"  # scratch is default, not explicit
    has_absolute_paths = "~" not in body
    has_scope_directive = bool(re.search(
        r'(scope|repo|directory|workspace):\s*/Users/kieranlal/',
        body, re.IGNORECASE,
    ))

    critical_flags = {
        "has_assignee": has_assignee,
        "has_absolute_paths": has_absolute_paths,
        "has_scope_directive": has_scope_directive,
    }
    weighted_flags = {
        "has_max_runtime": has_max_runtime,
        "has_parents": has_parents,
        "has_skills": has_skills,
        "has_workspace": has_workspace,
    }

    all_critical_pass = all(critical_flags.values())

    # 8. Infrastructure Dependency Tax
    infra_tax_level = "none"
    if len(infra_touchpoints) >= 3:
        infra_tax_level = "high"
    elif len(infra_touchpoints) >= 1:
        infra_tax_level = "medium"

    return {
        "classification": classification,
        "classification_confidence": round(confidence, 2),
        "classification_method": method,
        "dependency_depth": dep_depth,
        "cross_repo_count": cross_repo_count,
        "infrastructure_touchpoints": infra_touchpoints,
        "infrastructure_tax_level": infra_tax_level,
        "specificity_ratio": round(specificity_ratio, 3),
        "body_length_lines": body_lines,
        "body_length_score": round(body_length_score, 2),
        "critical_flags": critical_flags,
        "all_critical_pass": all_critical_pass,
        "weighted_flags": weighted_flags,
        "flag_count": sum(1 for v in {**critical_flags, **weighted_flags}.values() if v),
        "flag_total": len(critical_flags) + len(weighted_flags),
        "assignee": assignee,
        "parents": parents,
        "skills": skills,
    }


def compute_readiness(fingerprint: dict) -> dict:
    """Compute readiness score from complexity fingerprint.

    Critical gates: binary (must pass all).
    Weighted dimensions: continuous score blended into readiness %.
    """
    critical = fingerprint["all_critical_pass"]

    # Weighted dimensions with individual scores
    weighted = {
        "body_specificity": fingerprint["specificity_ratio"],
        "body_adequacy": fingerprint["body_length_score"],
        "max_runtime_alignment": 1.0 if fingerprint["weighted_flags"]["has_max_runtime"] else 0.5,
        "skill_alignment": 1.0 if fingerprint["skills"] else 0.3,
        "dependency_chaining": 1.0 if fingerprint["parents"] else 0.4,
    }

    weights = {
        "body_specificity": 0.30,
        "body_adequacy": 0.15,
        "max_runtime_alignment": 0.20,
        "skill_alignment": 0.15,
        "dependency_chaining": 0.20,
    }

    weighted_score = sum(
        weighted[k] * weights[k] for k in weights
    )

    if not critical:
        readiness = 0.0
        readiness_label = "BLOCKED"
        recommendation = "Fix critical flags before dispatch"
    elif weighted_score >= 0.80:
        readiness = weighted_score
        readiness_label = "READY"
        recommendation = "Dispatch — well-structured task"
    elif weighted_score >= 0.50:
        readiness = weighted_score
        readiness_label = "ADVISORY"
        recommendation = "Dispatch with caution — improve specificity, add max-runtime"
    else:
        readiness = weighted_score
        readiness_label = "WEAK"
        recommendation = "Improve body specificity, add runtime cap, verify scope"

    return {
        "readiness_score": round(readiness, 3),
        "readiness_label": readiness_label,
        "recommendation": recommendation,
        "weighted_dimensions": {k: round(v, 3) for k, v in weighted.items()},
        "critical_gates_pass": critical,
        "critical_gates": fingerprint["critical_flags"],
    }


def compute_rcf_buffer_adjustment(fingerprint: dict) -> dict:
    """Compute RCF buffer adjustment based on complexity fingerprint."""
    base_buffer = 0.0  # mean + 0σ for known-good

    # Classification adjustment
    if fingerprint["classification"] == "reasoning":
        base_buffer += 0.5  # mean + 0.5σ for reasoning tasks

    # Infrastructure tax
    if fingerprint["infrastructure_tax_level"] == "high":
        base_buffer += 0.5
    elif fingerprint["infrastructure_tax_level"] == "medium":
        base_buffer += 0.25

    # Cross-repo penalty
    if fingerprint["cross_repo_count"] >= 2:
        base_buffer += 0.25
    elif fingerprint["cross_repo_count"] >= 1:
        base_buffer += 0.1

    # Dependency depth penalty
    if fingerprint["dependency_depth"] >= 3:
        base_buffer += 0.25
    elif fingerprint["dependency_depth"] >= 1:
        base_buffer += 0.1

    # Body specificity bonus (reduces buffer)
    if fingerprint["specificity_ratio"] >= 0.15:
        base_buffer = max(base_buffer - 0.25, 0.0)

    # Flag completeness bonus
    flag_ratio = fingerprint["flag_count"] / fingerprint["flag_total"]
    if flag_ratio >= 0.8:
        base_buffer = max(base_buffer - 0.25, 0.0)

    # Map to RCF scenario name
    if base_buffer >= 0.75:
        scenario = "PESSIMISTIC"
    elif base_buffer >= 0.25:
        scenario = "BASE"
    else:
        scenario = "OPTIMISTIC"

    return {
        "buffer_adjustment": round(base_buffer, 2),
        "rcf_scenario": scenario,
        "buffer_contributors": {
            "classification": f"+{0.5 if fingerprint['classification'] == 'reasoning' else 0}",
            "infrastructure_tax": f"+{0.5 if fingerprint['infrastructure_tax_level'] == 'high' else 0.25 if fingerprint['infrastructure_tax_level'] == 'medium' else 0}",
            "cross_repo": f"+{0.25 if fingerprint['cross_repo_count'] >= 2 else 0.1 if fingerprint['cross_repo_count'] >= 1 else 0}",
            "dependency_depth": f"+{0.25 if fingerprint['dependency_depth'] >= 3 else 0.1 if fingerprint['dependency_depth'] >= 1 else 0}",
            "specificity_bonus": f"-0.25 (applied)" if fingerprint['specificity_ratio'] >= 0.15 else "none",
            "flag_bonus": f"-0.25 (applied)" if flag_ratio >= 0.8 else "none",
        },
    }


def load_calibration_db() -> dict:
    """Load historical calibration data."""
    if CALIBRATION_DB.exists():
        try:
            return json.loads(CALIBRATION_DB.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"tasks": [], "reference_classes": {}, "pre_mortem_misses": {}}


def save_calibration_db(data: dict):
    """Persist calibration data."""
    CALIBRATION_DB.write_text(json.dumps(data, indent=2))


def record_outcome(task_id: str, fingerprint: dict, runs: list[dict]) -> dict:
    """Record task outcome and update calibration database."""
    db = load_calibration_db()

    # Determine outcome
    outcomes = [r.get("outcome", "") for r in runs]
    completion_time = None
    if runs:
        first_ts = runs[0].get("started", "")
        last_ts = runs[-1].get("completed", "")
        if first_ts and last_ts:
            try:
                t1 = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
                t2 = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                completion_time = (t2 - t1).total_seconds() / 3600
            except (ValueError, TypeError):
                pass

    # Check for escalation hits
    failure_count = 0
    escalation_count = 0
    for r in runs:
        outcome = r.get("outcome", "")
        if outcome in ("reclaimed", "timed_out", "crashed"):
            failure_count += 1
        if outcome == "blocked":
            escalation_count += 1

    is_escalated = "blocked" in outcomes or failure_count >= 3

    # Build reference class key from fingerprint
    ref_class = (
        f"{fingerprint['classification']}"
        f"|dpth={fingerprint['dependency_depth']}"
        f"|infra={fingerprint['infrastructure_tax_level']}"
        f"|repos={fingerprint['cross_repo_count']}"
    )

    record = {
        "task_id": task_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fingerprint": fingerprint,
        "outcomes": outcomes,
        "failure_count": failure_count,
        "escalation_count": escalation_count,
        "is_escalated": is_escalated,
        "completion_time_hours": completion_time,
        "reference_class": ref_class,
    }

    db["tasks"].append(record)

    # Update reference class statistics
    if ref_class not in db["reference_classes"]:
        db["reference_classes"][ref_class] = {
            "count": 0,
            "escalation_count": 0,
            "total_completion_hours": 0,
            "completion_count": 0,
        }
    rc = db["reference_classes"][ref_class]
    rc["count"] += 1
    if is_escalated:
        rc["escalation_count"] += 1
    if completion_time:
        rc["completion_count"] += 1
        rc["total_completion_hours"] += completion_time

    # Adjust pre-mortem misses
    if is_escalated and fingerprint["infrastructure_touchpoints"]:
        for tp in fingerprint["infrastructure_touchpoints"]:
            key = f"infra:{tp}"
            if key not in db["pre_mortem_misses"]:
                db["pre_mortem_misses"][key] = 0
            db["pre_mortem_misses"][key] += 1

    save_calibration_db(db)
    return record


def assess_task(task_id: str, should_record: bool = False) -> dict:
    """Full assessment pipeline: fingerprint → readiness → RCF buffer."""
    task = get_task_json(task_id)
    if not task:
        return {"error": f"Task {task_id} not found"}

    fingerprint = extract_complexity_fingerprint(task)
    readiness = compute_readiness(fingerprint)
    rcf = compute_rcf_buffer_adjustment(fingerprint)

    result = {
        "task_id": task_id,
        "title": task.get("title", ""),
        "status": task.get("status", ""),
        "fingerprint": fingerprint,
        "readiness": readiness,
        "rcf": rcf,
    }

    if should_record:
        runs = get_task_runs(task_id)
        outcome = record_outcome(task_id, fingerprint, runs)
        result["outcome"] = outcome

    return result


def format_output(result: dict) -> str:
    """Colorized terminal output."""
    lines = []
    lines.append(f"\n{CYAN}{'='*60}{RESET}")
    lines.append(f"  Task: {result.get('title', '?')[:70]}")
    lines.append(f"  ID:   {result['task_id']}  Status: {result.get('status', '?')}")
    lines.append(f"{CYAN}{'='*60}{RESET}")

    fp = result["fingerprint"]

    # Classification
    cls_color = GREEN if fp["classification"] == "mechanical" else YELLOW
    lines.append(f"\n  {cls_color}Classification: {fp['classification'].upper()}{RESET}")
    lines.append(f"  Confidence: {fp['classification_confidence']}  Method: {fp['classification_method']}")

    # Complexity
    lines.append(f"\n  Complexity Profile:")
    lines.append(f"    Dependency depth:   {fp['dependency_depth']}")
    lines.append(f"    Cross-repo count:   {fp['cross_repo_count']}")
    lines.append(f"    Infrastructure:     {fp['infrastructure_tax_level'].upper()} ({', '.join(fp['infrastructure_touchpoints'][:3])})")
    lines.append(f"    Body specificity:   {fp['specificity_ratio']:.1%} ({fp['body_length_lines']} lines)")
    lines.append(f"    Flags set:          {fp['flag_count']}/{fp['flag_total']}")

    # Critical gates
    critical = result["readiness"]
    gate_color = GREEN if critical["critical_gates_pass"] else REDIR
    lines.append(f"\n  {gate_color}Critical Gates: {'PASS' if critical['critical_gates_pass'] else 'FAIL'}{RESET}")
    for gate_name, gate_val in critical["critical_gates"].items():
        g = GREEN if gate_val else REDIR
        lines.append(f"    {g}{'✅' if gate_val else '❌'} {gate_name}: {gate_val}{RESET}")

    # Readiness score
    rdy = result["readiness"]
    score_color = {
        "BLOCKED": REDIR, "WEAK": YELLOW, "ADVISORY": YELLOW, "READY": GREEN,
    }.get(rdy["readiness_label"], RESET)
    lines.append(f"\n  {score_color}Readiness: {rdy['readiness_label']} ({rdy['readiness_score']:.0%}){RESET}")
    lines.append(f"  {bcolors.YELLOW if rdy['readiness_label'] == 'ADVISORY' else ''}Recommendation: {rdy['recommendation']}{RESET}")

    # RCF
    rcf = result["rcf"]
    rcf_color = {"OPTIMISTIC": GREEN, "BASE": YELLOW, "PESSIMISTIC": REDIR}.get(rcf["rcf_scenario"], RESET)
    lines.append(f"\n  {rcf_color}RCF Scenario: {rcf['rcf_scenario']} (mean + {rcf['buffer_adjustment']}σ){RESET}")
    for k, v in rcf["buffer_contributors"].items():
        lines.append(f"    {k}: {v}")

    # Outcome
    if "outcome" in result:
        o = result["outcome"]
        o_color = REDIR if o.get("is_escalated") else GREEN
        lines.append(f"\n  {o_color}Historical Outcome: {'ESCALATED' if o.get('is_escalated') else 'OK'}{RESET}")
        lines.append(f"    Failures: {o.get('failure_count')}  Escalations: {o.get('escalation_count')}")
        if o.get("completion_time_hours"):
            lines.append(f"    Completion: {o['completion_time_hours']:.2f}h")
        lines.append(f"    Reference class: {o.get('reference_class', '?')}")

    lines.append(f"\n{CYAN}{'='*60}{RESET}")
    return "\n".join(lines)


def calibrate() -> dict:
    """Analyze calibration database and produce summary statistics."""
    db = load_calibration_db()
    if not db["tasks"]:
        return {"status": "no_data", "message": "No calibration data yet"}

    classes = db["reference_classes"]
    pre_mortem = db["pre_mortem_misses"]

    # Find highest-escalation reference classes
    sorted_classes = sorted(
        classes.items(),
        key=lambda x: x[1]["escalation_count"] / max(x[1]["count"], 1),
        reverse=True,
    )

    # Compute mean completion time per class
    class_stats = {}
    for ref_class, stats in sorted_classes:
        esc_rate = stats["escalation_count"] / max(stats["count"], 1)
        mean_hours = (
            stats["total_completion_hours"] / max(stats["completion_count"], 1)
            if stats["completion_count"] > 0
            else None
        )
        class_stats[ref_class] = {
            "count": stats["count"],
            "escalation_rate": round(esc_rate, 2),
            "mean_completion_hours": round(mean_hours, 2) if mean_hours else None,
            "recommended_buffer": (
                "mean + 0.5σ" if esc_rate >= 0.3
                else "mean + 0.25σ" if esc_rate >= 0.1
                else "mean + 0σ"
            ),
        }

    # Find most-common pre-mortem misses
    sorted_misses = sorted(
        pre_mortem.items(),
        key=lambda x: x[1],
        reverse=True,
    )

    result = {
        "status": "calibrated",
        "total_tasks": len(db["tasks"]),
        "reference_class_count": len(classes),
        "reference_classes": class_stats,
        "top_pre_mortem_misses": sorted_misses[:10],
        "recommended_buffer_adjustments": {
            "high_escalation_class": "mean + 0.5σ",
            "infrastructure_heavy": "add infrastructure probe mandatory",
            "cross_repo_2plus": "mean + 0.25σ",
            "reasoning_class_under_specified": "mean + 0.5σ",
        },
    }

    return result


def list_ready_tasks() -> list[str]:
    """List all tasks in READY state."""
    cmd = [KANBAN_CLI, "kanban", "list", "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return []
        tasks = json.loads(r.stdout)
        ready = []
        for t in tasks:
            status = t.get("status", "")
            if status in ("ready", "running"):
                ready.append(t.get("id", ""))
        return [t for t in ready if t]
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


def main():
    parser = argparse.ArgumentParser(
        description="Kanban task readiness assessment with complexity fingerprint"
    )
    parser.add_argument("task_id", nargs="?", help="Task ID to assess")
    parser.add_argument("--ready", action="store_true",
                        help="Assess all ready + running tasks")
    parser.add_argument("--calibrate", action="store_true",
                        help="Show calibration statistics from historical data")
    parser.add_argument("--record", action="store_true",
                        help="Record outcome and update calibration DB")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON")
    args = parser.parse_args()

    if args.calibrate:
        result = calibrate()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"\n{CYAN}Calibration Database{RESET}")
            print(f"  Total tasks tracked: {result.get('total_tasks', 0)}")
            print(f"  Reference classes:   {result.get('reference_class_count', 0)}")
            print(f"\n  Top escalation-rate reference classes:")
            for rc, stats in result.get("reference_classes", {}).items():
                print(f"    {rc}: {stats['escalation_rate']:.0%} escalation rate "
                      f"({stats['count']} tasks, ~{stats['mean_completion_hours']}h mean)")
            print(f"\n  Recommended buffer adjustments:")
            for adj, val in result.get("recommended_buffer_adjustments", {}).items():
                print(f"    {adj}: {val}")
            if result.get("top_pre_mortem_misses"):
                print(f"\n  Top pre-mortem misses (infrastructure):")
                for key, count in result["top_pre_mortem_misses"][:5]:
                    print(f"    {key}: {count}x")
        return

    task_ids = []
    if args.task_id:
        task_ids = [args.task_id]
    elif args.ready:
        task_ids = list_ready_tasks()
    else:
        parser.print_help()
        sys.exit(1)

    if not task_ids:
        print("No tasks found to assess")
        sys.exit(0)

    results = []
    for tid in task_ids:
        result = assess_task(tid, should_record=args.record)
        results.append(result)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(format_output(result))

    # Summary for batch mode
    if len(results) > 1 and not args.json:
        print(f"\n{CYAN}Batch Summary ({len(results)} tasks){RESET}")
        for r in results:
            rdy = r["readiness"]
            rcf = r["rcf"]
            label_color = {
                "BLOCKED": REDIR, "WEAK": YELLOW, "ADVISORY": YELLOW, "READY": GREEN,
            }.get(rdy["readiness_label"], "")
            print(f"  {label_color}{r['task_id']}: {rdy['readiness_label']} "
                  f"({rdy['readiness_score']:.0%}) | RCF: {rcf['rcf_scenario']} "
                  f"({rcf['buffer_adjustment']}σ) | {r.get('title', '?')[:50]}{RESET}")


# ANSI color class for format_output
class bcolors:
    YELLOW = "\033[93m"


if __name__ == "__main__":
    main()
