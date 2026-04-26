---
schema_version: "1.0"
name: applying-karpathy-guidelines
version: "1.0.0"
proficiency: 0.5
composition:
  upstream: []
  downstream:
    - coding-standards
    - test-driven-dev
latent_vars:
  - task_ambiguity
  - codebase_familiarity
  - change_blast_radius
description: "Behavioral process guardrails derived from Andrej Karpathy's observations on LLM coding pitfalls. Unique focus: assumption management, ambiguity surfacing, and blast-radius discipline. Delegates simplicity/style to coding-standards and test methodology to test-driven-dev. Apply when starting any non-trivial implementation."
---



# === SKILL.md ===

---
schema_version: "1.0"
name: applying-karpathy-guidelines
version: "1.0.0"
proficiency: 0.5
composition:
  upstream: []
  downstream:
    - coding-standards
    - test-driven-dev
latent_vars:
  - task_ambiguity
  - codebase_familiarity
  - change_blast_radius
description: "Behavioral process guardrails derived from Andrej Karpathy's observations on LLM coding pitfalls. Unique focus: assumption management, ambiguity surfacing, and blast-radius discipline. Delegates simplicity/style to coding-standards and test methodology to test-driven-dev. Apply when starting any non-trivial implementation."
---

# Applying Karpathy Guidelines

Behavioral process discipline for non-trivial implementation work. This skill covers what `coding-standards` and `test-driven-dev` do not: **how to think before and during coding**, not what style to use or how to test.

## What This Skill Owns (not delegated)

### Think Before Coding
State assumptions explicitly before implementing. If multiple interpretations exist, present them — don't pick silently. If a simpler approach exists, say so. If something is unclear, stop and name what's confusing.

### Surgical Changes — Blast Radius Discipline
Every changed line must trace directly to the user's request. Don't "improve" adjacent code, comments, or formatting. Don't refactor unrelated things. When YOUR changes create orphans, clean them up — but don't touch pre-existing dead code unless asked.

## What This Skill Delegates

| Concern | Delegate to | Why |
|---------|------------|-----|
| Simplicity, YAGNI, no over-engineering | `coding-standards` | KISS/YAGNI/DRY are style rules, not behavioral process |
| Write tests first, coverage targets | `test-driven-dev` | Test methodology is a separate workflow |
| Verifiable goals via tests | `test-driven-dev` | Goal-Driven Execution is just TDD framing |

Do NOT re-state simplicity rules or test instructions here — invoke the downstream skills.

## Calibration

**Full rigor**: New features, refactors, multi-file changes, unfamiliar codebases. State assumptions, plan goals, edit surgically.

**Streamlined**: Typo fixes, obvious one-liners, renaming. Skip the full cycle.

## Layer Reference

For detailed strategic calibration (when to apply vs. relax each principle), see [TACTICAL.md](TACTICAL.md).


# === TACTICAL.md ===

# Applying Karpathy Guidelines: Tactical Guidance

Non-prescriptive strategic know-how for assumption management and blast-radius discipline.

---

## Strategic Options

### Option 1: Full Rigor (non-trivial work)

**When to use**: New features, refactors, multi-file changes, unfamiliar codebases.

**Strategy**:
1. State assumptions and surface tradeoffs before implementing.
2. Invoke `test-driven-dev` for goal verification methodology.
3. Invoke `coding-standards` for simplicity/style guidance.
4. Edit surgically — every line traces to the request.

**Why**: Wrong assumptions on non-trivial work are far more expensive than asking.

### Option 2: Streamlined (trivial work)

**When to use**: Typo fixes, obvious one-liners, renaming.

**Strategy**: Apply blast-radius discipline directly. Don't ask about things that are unambiguous.

**Why**: Full rigor on trivial tasks wastes context.

---

## Per-Principle Tactical Notes

### Think Before Coding

**Tension**: Asking too much signals lack of confidence; assuming too much risks wasted work.

**Calibration**: If the request has >1 noun and >1 verb with ambiguous scope, state your interpretation before coding. If it's a single specific change with clear scope, proceed.

**Failure mode**: Silently picking one of several valid interpretations, implementing it, then needing a full rewrite.

### Surgical Changes

**Tension**: LLM sees nearby code that "could be improved" and wants to fix it.

**Calibration**: If you notice something worth fixing, mention it in one sentence — don't fix it.

**Exception**: When YOUR changes create orphans (unused imports, dead variables from your edit), clean those up — they're your blast radius.

---

## Composition Routing

| Situation | This skill handles | Delegate to |
|-----------|-------------------|------------|
| "Add input validation" | State what "validation" means (assumptions) | `test-driven-dev` (write the tests) |
| "Fix the formatting" | Blast-radius: touch only what's asked | `coding-standards` (what correct is) |
| "This function is too complex" | Surface tradeoffs before refactoring | `coding-standards` (refactoring patterns) |
| "Refactor the auth module" | Assumptions + blast-radius | `test-driven-dev` (tests before/after) |

