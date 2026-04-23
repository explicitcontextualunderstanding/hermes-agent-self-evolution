---
name: hermes-agent-self-evolution
description: Evolve and optimize Hermes Agent skills, tool descriptions, system prompts, and code using DSPy + GEPA (Genetic-Pareto Prompt Evolution). No GPU required — operates via API calls.
version: 1.0.0
author: Nous Research
category: software-development
---

# Hermes Agent Self-Evolution

Evolutionary self-improvement for Hermes Agent using DSPy + GEPA.

## Prerequisites

- Python 3.10+
- uv installed
- Hermes Agent repo available locally (default ~/.hermes/hermes-agent)

## Setup

```bash
cd /Users/kieranlal/workspace/hermes-agent-self-evolution
uv pip install -e ".[dev]"
export HERMES_AGENT_REPO=~/.hermes/hermes-agent
```

## Usage

### Phase 1: Evolve a Skill

```bash
python -m evolution.skills.evolve_skill \
    --skill github-code-review \
    --iterations 10 \
    --eval-source synthetic
```

Or using session history:

```bash
python -m evolution.skills.evolve_skill \
    --skill github-code-review \
    --iterations 10 \
    --eval-source sessiondb
```

### Phase 2: Evolve Tool Descriptions

```bash
python -m evolution.tools.evolve_tool_descriptions \
    --iterations 5 \
    --benchmark-gate tblite-fast
```

### Phase 3: Evolve System Prompt Section

```bash
python -m evolution.prompts.evolve_prompt_section \
    --section MEMORY_GUIDANCE \
    --iterations 5
```

### Phase 4: Evolve Tool Code

```bash
python -m evolution.code.evolve_tool_code \
    --tool file_tools \
    --bug-issue 742 \
    --iterations 10
```

## Architecture

The optimization loop:
1. SELECT TARGET — pick skill/prompt/tool/code
2. BUILD EVALUATION DATASET — synthetic, sessiondb, or golden
3. WRAP AS DSPy MODULE — skill text → dspy.Signature
4. RUN OPTIMIZER — GEPA primary, MIPROv2 fallback
5. EVALUATE & COMPARE — held-out test, statistical significance
6. DEPLOY — git branch + PR, human review required

## Guardrails

- Full test suite must pass (pytest tests/ -q)
- Skills ≤15KB, tool descriptions ≤500 chars
- No mid-conversation caching breaks
- Semantic preservation enforced
- All changes via PR, never direct commit

## Project Structure

```
evolution/
  core/
    dataset_builder.py    # Eval dataset generation
    fitness.py            # LLM-as-judge scoring
    constraints.py        # Validation gates
    benchmark_gate.py     # TBLite/YC-Bench regression checks
    pr_builder.py         # Auto-generated PRs with metrics
  skills/
    evolve_skill.py       # Phase 1 entry point
    skill_module.py       # SKILL.md → DSPy wrapper
  tools/                  # Phase 2
  prompts/                # Phase 3
  code/                   # Phase 4 (Darwinian Evolver)
  monitor/                # Phase 5 (continuous loop)
```

## Key Files

- README.md — Quick start and high-level overview
- PLAN.md — Full architecture, phases, timeline, constraints
- pyproject.toml — Package config (dspy, gepa dependencies)

## Evaluation Data Sources

| Source | Method | Quality |
|--------|--------|---------|
| Synthetic | Strong model generates (task, expected_behavior) pairs | Medium, bootstraps fast |
| SessionDB | Mine real sessions, LLM-as-judge scores | High, improves over time |
| Golden | Hand-curated test cases | Highest, manual effort |
| Auto-eval | Plant bugs/issues, check if fixed | Bonus where applicable |

## Integration with Hermes Infrastructure

| Hermes Component | Role |
|-----------------|------|
| batch_runner.py | Parallel eval harness |
| agent/trajectory.py | Execution traces for GEPA reflection |
| hermes_state.py (SessionDB) | Real usage data mining |
| skills/ directory | Primary optimization targets |
| tools/registry.py | Tool description optimization |
| agent/prompt_builder.py | System prompt sections |
| tests/ | Guardrail — all evolved code must pass |

## Cost

~$2-10 per optimization run depending on iterations and model choice.

## License

MIT — © 2026 Nous Research
