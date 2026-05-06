#!/opt/homebrew/bin/python3.14
"""plan-to-kanban.py — Convert a plan.md into Kanban tasks.

Reads a plan markdown file, parses YAML frontmatter + phase sections,
and creates Hermes Kanban tasks with proper dependency chains.

Usage:
    python3 plan-to-kanban.py path/to/plan.md [--dry-run] [--assignee coding]

Dependencies: pyyaml (pip3 install pyyaml)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

KANBAN_CLI = "/Users/kieranlal/.hermes/hermes-agent/venv/bin/hermes"
DEFAULT_ASSIGNEE = "coding"
DEFAULT_WORKSPACE = "scratch"
DEFAULT_PROBE_MAX_RUNTIME = "30m"
DEFAULT_IMPL_MAX_RUNTIME = "2h"
DEFAULT_CANARY_MAX_RUNTIME = "4h"


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from plan markdown."""
    fm = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                import yaml
                fm = yaml.safe_load(parts[1]) or {}
            except ImportError:
                # Fallback: minimal key-value parsing
                for line in parts[1].strip().split("\n"):
                    m = re.match(r'(\w[\w-]*):\s*(.+)', line)
                    if m:
                        fm[m.group(1)] = m.group(2).strip()
            body = parts[2]
    return fm, body


def extract_phases(body: str) -> list[dict]:
    """Extract phases and their tasks from markdown body.
    
    A phase is a ## heading containing 'Phase'. Tasks are - [ ] or - items.
    """
    phases = []
    current_phase = None
    
    for line in body.split("\n"):
        # Detect phase heading (## or ###)
        m = re.match(r'^#{2,3}\s+(.+?)(?:\s*\{#.*\})?$', line)
        if m:
            title = m.group(1).strip()
            if "phase" in title.lower():
                if current_phase:
                    phases.append(current_phase)
                current_phase = {
                    "title": title,
                    "num": len(phases) + 1,
                    "tasks": [],
                }
            continue
        
        # Detect task within phase (- [ ] or - item)
        if current_phase and line.strip().startswith("-"):
            task_text = line.strip()
            if task_text.startswith("- [ ]"):
                task_text = task_text[5:].strip()
            elif task_text.startswith("-"):
                task_text = task_text[1:].strip()
            if task_text:
                current_phase["tasks"].append(task_text)
    
    if current_phase:
        phases.append(current_phase)
    
    return phases


def classify_phase(phase_title: str) -> str:
    """Classify a phase by its title to determine runtime class and flags."""
    title_lower = phase_title.lower()
    if "probe" in title_lower or "pre-flight" in title_lower or "phase 0" in title_lower or "research" in title_lower:
        return "probe"
    if "canary" in title_lower or "e2e" in title_lower or "validate" in title_lower or "verify" in title_lower:
        return "canary"
    if "implement" in title_lower or "integration" in title_lower or "build" in title_lower or "code" in title_lower:
        return "implementation"
    return "implementation"  # default


def run_kanban_create(title: str, body: str, assignee: str = None,
                      parent_ids: list = None, priority: int = None,
                      max_runtime: str = None, workspace: str = None,
                      skill: str = None, triage: bool = False,
                      dry_run: bool = False) -> str | None:
    """Run `hermes kanban create` and return task ID."""
    cmd = [KANBAN_CLI, "kanban", "create", title, "--body", body]
    
    if assignee:
        cmd.extend(["--assignee", assignee])
    if priority:
        cmd.extend(["--priority", str(priority)])
    if max_runtime:
        cmd.extend(["--max-runtime", max_runtime])
    if workspace:
        cmd.extend(["--workspace", workspace])
    if skill:
        cmd.extend(["--skill", skill])
    if triage:
        cmd.append("--triage")
    if parent_ids:
        for pid in parent_ids:
            cmd.extend(["--parent", pid])
    
    if dry_run:
        print(f"  [DRY-RUN] {' '.join(cmd[:20])}...")
        return None
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"  ❌ Failed: {result.stderr.strip()[:200]}", file=sys.stderr)
            return None
        # Extract task ID from output
        m = re.search(r't_([a-f0-9]+)', result.stdout)
        if m:
            task_id = f"t_{m.group(1)}"
            print(f"  ✅ Created {task_id}: {title[:60]}")
            return task_id
        print(f"  ⚠ Created but no ID in output: {result.stdout[:100]}")
        return None
    except subprocess.TimeoutExpired:
        print(f"  ❌ Timeout creating task: {title[:50]}", file=sys.stderr)
        return None


def convert_plan(plan_path: str, assignee: str = DEFAULT_ASSIGNEE,
                 dry_run: bool = False) -> dict:
    """Main conversion function. Returns summary dict."""
    content = Path(plan_path).read_text()
    frontmatter, body = parse_frontmatter(content)
    phases = extract_phases(body)
    
    plan_id = frontmatter.get("plan", Path(plan_path).stem)
    plan_id_short = plan_id.split("-")[0] if plan_id else "plan"
    
    print(f"\n{'='*60}")
    print(f"  Plan: {frontmatter.get('title', plan_id)}")
    print(f"  File: {plan_path}")
    print(f"  Phases: {len(phases)}")
    print(f"  Total tasks: {sum(len(p['tasks']) for p in phases)}")
    print(f"  Assignee: {assignee}")
    print(f"{'='*60}\n")
    
    created_ids = {}
    phase_ids = {}  # last task ID per phase for chain linking
    
    # Phase 0: probes (no parents, run first)
    for phase in phases:
        if classify_phase(phase["title"]) != "probe":
            continue
        if not phase["tasks"]:
            continue
        print(f"\n── Phase {phase['num']}: {phase['title']} ──")
        last_id = None
        for i, task_text in enumerate(phase["tasks"]):
            parents = [last_id] if last_id else None
            task_id = run_kanban_create(
                title=f"P{phase['num']}.{i}: {task_text[:70]}",
                body=f"Plan file: {plan_path}\n§{phase['title']}\n\n{task_text}",
                assignee=assignee,
                parent_ids=parents,
                priority=1,
                max_runtime=DEFAULT_PROBE_MAX_RUNTIME,
                workspace=DEFAULT_WORKSPACE,
                skill="systematic-debugging",
                triage=False,
                dry_run=dry_run,
            )
            if task_id:
                created_ids[task_id] = task_text
                last_id = task_id
        if last_id:
            phase_ids[phase["num"]] = last_id
    
    # Phase 1+: implementation (depend on last Phase 0 or previous phase)
    impl_priority = 2
    for phase in phases:
        if classify_phase(phase["title"]) in ("probe", "canary"):
            continue
        if not phase["tasks"]:
            continue
        print(f"\n── {phase['title']} ──")
        last_id = None
        for i, task_text in enumerate(phase["tasks"]):
            parents = []
            if last_id:
                parents.append(last_id)
            # Depend on last probe task if this is first impl task
            if i == 0 and phase_ids:
                probe_ids = [pid for pnum, pid in phase_ids.items() 
                            if pnum < phase["num"]]
                if probe_ids:
                    parents.extend(probe_ids)
            
            task_id = run_kanban_create(
                title=f"P{phase['num']}.{i}: {task_text[:70]}",
                body=f"Plan file: {plan_path}\n§{phase['title']}\n\n{task_text}",
                assignee=assignee,
                parent_ids=parents or None,
                priority=impl_priority,
                max_runtime=DEFAULT_IMPL_MAX_RUNTIME,
                workspace="worktree",
                skill=None,
                triage=False,
                dry_run=dry_run,
            )
            if task_id:
                created_ids[task_id] = task_text
                last_id = task_id
        if last_id:
            phase_ids[phase["num"]] = last_id
        impl_priority += 1
    
    # Canary phase (fan-in: depends on all last task IDs from every phase)
    for phase in phases:
        if classify_phase(phase["title"]) != "canary":
            continue
        if not phase["tasks"]:
            print(f"\n  ⚠ Canary phase has no tasks — skipping")
            continue
        print(f"\n── {phase['title']} (fan-in) ──")
        all_parents = list(phase_ids.values())
        for i, task_text in enumerate(phase["tasks"]):
            task_id = run_kanban_create(
                title=f"P{phase['num']}.{i}: {task_text[:70]}",
                body=f"Plan file: {plan_path}\n§{phase['title']}\n\n{task_text}\n\nDepends on: {', '.join(all_parents[:5])}",
                assignee=assignee,
                parent_ids=all_parents or None,
                priority=5,
                max_runtime=DEFAULT_CANARY_MAX_RUNTIME,
                workspace="worktree",
                skill=None,
                triage=False,
                dry_run=dry_run,
            )
            if task_id:
                created_ids[task_id] = task_text
    
    return {
        "plan_id": plan_id,
        "phases": len(phases),
        "tasks_created": len(created_ids),
        "task_ids": list(created_ids.keys()),
    }


def main():
    parser = argparse.ArgumentParser(description="Convert plan.md into Kanban tasks")
    parser.add_argument("plan_path", help="Path to plan.md file")
    parser.add_argument("--assignee", default=DEFAULT_ASSIGNEE, 
                        help=f"Default assignee (default: {DEFAULT_ASSIGNEE})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print commands without executing")
    args = parser.parse_args()
    
    if not Path(args.plan_path).exists():
        print(f"❌ Plan file not found: {args.plan_path}", file=sys.stderr)
        sys.exit(1)
    
    # Verify kanban CLI is available
    if not args.dry_run:
        which = subprocess.run(["which", KANBAN_CLI], capture_output=True, text=True)
        if which.returncode != 0 and not Path(KANBAN_CLI).exists():
            print(f"❌ Kanban CLI not found at {KANBAN_CLI}", file=sys.stderr)
            print("   Set KANBAN_CLI or install hermes", file=sys.stderr)
            sys.exit(1)
    
    print(f"📋 Converting {args.plan_path} to Kanban tasks...")
    print(f"   Kanban CLI: {KANBAN_CLI}")
    print(f"   Mode: {'DRY-RUN' if args.dry_run else 'LIVE'}")
    
    result = convert_plan(
        plan_path=args.plan_path,
        assignee=args.assignee,
        dry_run=args.dry_run,
    )
    
    print(f"\n{'='*60}")
    print(f"  Summary:")
    print(f"    Plan:    {result['plan_id']}")
    print(f"    Phases:  {result['phases']}")
    print(f"    Tasks:   {result['tasks_created']}")
    if result['task_ids'] and not args.dry_run:
        print(f"    IDs:     {', '.join(result['task_ids'][:5])}" + 
              (f" +{len(result['task_ids'])-5} more" if len(result['task_ids']) > 5 else ""))
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
