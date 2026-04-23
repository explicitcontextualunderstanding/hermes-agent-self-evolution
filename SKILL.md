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

**Path notes:**
- `uv` may be at `/opt/homebrew/bin/uv` if not on PATH
- The venv used by Hermes is at `/Users/kieranlal/workspace/.venv/bin/python3`
- System `python` does not exist — use `python3`

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

### Phase 2-4: Not Yet Implemented

**IMPORTANT:** Only Phase 1 (skill evolution) is implemented. Phases 2-5 are empty directories with `__init__.py` only. The commands below reference modules that do not exist.

```bash
# These modules do not exist yet:
# python -m evolution.tools.evolve_tool_descriptions ...
# python -m evolution.prompts.evolve_prompt_section ...
# python -m evolution.code.evolve_tool_code ...
```

## What It Optimizes (Reality vs. Plan)

**The repo README/PLAN.md describes a 5-phase vision. Only Phase 1 is actually implemented.**

| Phase | Target | Status | Notes |
|-------|--------|--------|-------|
| **Phase 1** | Skill files (SKILL.md) | ✅ Implemented | `evolve_skill.py`, `skill_module.py`, `dataset_builder.py`, `fitness.py`, `constraints.py` |
| **Phase 2** | Tool descriptions | ❌ Empty stub | `evolution/tools/__init__.py` only |
| **Phase 3** | System prompt sections | ❌ Empty stub | `evolution/prompts/__init__.py` only |
| **Phase 4** | Tool implementation code | ❌ Empty stub | `evolution/code/__init__.py` only |
| **Phase 5** | Continuous improvement loop | ❌ Empty stub | `evolution/monitor/__init__.py` only |

**Modules referenced in PLAN.md but NOT built:**
- `evolution/core/benchmark_gate.py` — does not exist
- `evolution/core/pr_builder.py` — does not exist

**Default model values in `evolve_skill.py` are OpenAI models** (`openai/gpt-4.1`, `openai/gpt-4.1-mini`) — these will fail if you only have NVIDIA tokens. Pass `--optimizer-model` and `--eval-model` explicitly (see Option A below).

**Note:** The repo defaults have been patched to `openai/nvidia-proxy/...` models. If you are on an older checkout, you still need to pass models explicitly.

## Architecture

The optimization loop (Phase 1 only):
1. SELECT TARGET — pick a skill
2. BUILD EVALUATION DATASET — synthetic, sessiondb, or golden
3. WRAP AS DSPy MODULE — skill text → dspy.Signature
4. RUN OPTIMIZER — GEPA primary, MIPROv2 fallback
5. EVALUATE & COMPARE — held-out test, statistical significance
6. DEPLOY — git branch + PR, human review required

## Guardrails (Implemented)

- Size limits — `constraints.py` enforces skill ≤15KB
- Skill structure validation — YAML frontmatter required
- Growth limits — prevents runaway expansion

**Guardrails NOT implemented:**
- Full test suite gate (no `benchmark_gate.py`)
- Auto-PR generation (no `pr_builder.py`)
- Tool description size limits (Phase 2 not built)

## Project Structure

```
evolution/
  core/
    config.py             # Auto-discovers hermes-agent repo path
    dataset_builder.py    # Eval dataset generation
    fitness.py            # LLM-as-judge scoring
    constraints.py        # Validation gates (size, structure)
    external_importers.py # Session importers (Claude Code, Copilot, Hermes)
    benchmark_gate.py     # ❌ NOT IMPLEMENTED
    pr_builder.py         # ❌ NOT IMPLEMENTED
  skills/
    evolve_skill.py       # Phase 1 entry point (works)
    skill_module.py       # SKILL.md → DSPy wrapper (works)
  tools/                  # Phase 2 — ❌ empty stub
  prompts/                # Phase 3 — ❌ empty stub
  code/                   # Phase 4 — ❌ empty stub
  monitor/                # Phase 5 — ❌ empty stub
```

## Key Files

| File | Purpose | Status |
|------|---------|--------|
| `evolution/skills/evolve_skill.py` | CLI entry point — full Phase 1 pipeline | ✅ Working |
| `evolution/skills/skill_module.py` | Wraps SKILL.md as DSPy module | ✅ Working |
| `evolution/core/dataset_builder.py` | Synthetic eval dataset generation | ✅ Working |
| `evolution/core/fitness.py` | LLM-as-judge scoring with rubrics | ✅ Working |
| `evolution/core/constraints.py` | Size/structure validators | ✅ Working |
| `evolution/core/config.py` | Auto-discovers hermes-agent repo path | ✅ Working |
| `evolution/core/external_importers.py` | Session importers for Claude Code, Copilot, Hermes | ✅ Working |
| `tests/core/test_constraints.py` | 23 tests for constraints + skill parsing | ✅ Working |
| `tests/skills/test_skill_module.py` | Tests for SKILL.md → DSPy wrapper | ✅ Working |
| `evolution/core/benchmark_gate.py` | TBLite/YC-Bench regression checks | ❌ Not built |
| `evolution/core/pr_builder.py` | Auto-generated PRs with metrics | ❌ Not built |

## Running Phase 1

### Option A: Through kilo-proxy (recommended — token rotation, observability)

The kilo-proxy at `localhost:8080` manages 5 NVIDIA tokens with auto-rotation on 429.

```bash
# 1. Ensure proxy is running
bash ~/workspace/nano2/scripts/restart-kilo-proxy.sh

# 2. Run evolution through the proxy
export OPENAI_BASE_URL="http://localhost:8080/v1"
export OPENAI_API_KEY="dummy"  # proxy ignores this, uses enclave tokens
export HERMES_AGENT_REPO=/Users/kieranlal/.hermes/hermes-agent

cd /Users/kieranlal/workspace/hermes-agent-self-evolution
python3 -m evolution.skills.evolve_skill \
    --skill github-code-review \
    --iterations 10 \
    --optimizer-model "openai/nvidia-proxy/meta/llama-3.1-405b-instruct" \
    --eval-model "openai/nvidia-proxy/moonshotai/kimi-k2.5"
```

**CRITICAL: Model names must use `openai/` prefix.**

litellm requires the provider prefix when using a custom `OPENAI_BASE_URL`. Pass `openai/nvidia-proxy/MODEL` not just `nvidia-proxy/MODEL`. Without this prefix you get:
```
BadRequestError: LLM Provider NOT provided. You passed model=nvidia-proxy/...
```

**Latency by provider:**
- `google-proxy/gemma-4-31b-it` — ~3-5s per call, 5 free AIStudio tokens
- `nvidia-proxy/meta/llama-3.1-405b-instruct` — ~20-45s per call, 5 NIM tokens
- `nvidia-proxy/moonshotai/kimi-k2.5` — ~15-25s per call

For testing and fast iteration, use `google-proxy/gemma-4-31b-it`. For production optimization with stronger reasoning, use `nvidia-proxy/meta/llama-3.1-405b-instruct`.

**Available models via nvidia-proxy:**
- `openai/nvidia-proxy/moonshotai/kimi-k2.5`
- `openai/nvidia-proxy/minimaxai/minimax-m2.5`
- `openai/nvidia-proxy/nvidia/nemotron-3-super-120b-a12b`
- `openai/nvidia-proxy/meta/llama-3.1-405b-instruct`
- `openai/nvidia-proxy/deepseek-ai/deepseek-v3.2`
- `openai/nvidia-proxy/qwen/qwen3.5-397b-a17b`

**Why this works:** `dspy.LM()` routes through litellm, which respects `OPENAI_BASE_URL` and `OPENAI_API_KEY`. The proxy presents an OpenAI-compatible API and transparently rotates NVIDIA tokens from `~/.enclave/nvidia_{1..5}.txt`.

**Verify proxy is running:**
```bash
ps aux | grep "[k]ilo-proxy.py"        # should show a process
python3 -c "from openai import OpenAI; c=OpenAI(base_url='http://localhost:8080/v1',api_key='dummy'); print(c.models.list().data[0].id)"
tail ~/.local/share/kilo/log/token-rotation.log | grep "nvidia-proxy.*Loaded"
```

**If proxy is not running:**
```bash
bash ~/workspace/nano2/scripts/restart-kilo-proxy.sh
```

**Proxy token path pitfall:**
The proxy reads tokens from the *real* `~/.enclave/`, not the sandboxed `$HOME`. If `gemma_*.txt` or `nvidia_*.txt` exist only under `/Users/kieranlal/.hermes/profiles/coding/home/.enclave/`, copy them to `/Users/kieranlal/.enclave/` and restart the proxy. You'll see `No tokens loaded for provider 'google-proxy'` otherwise.

**Quick validation (no LLM calls):**
```bash
python3 -m evolution.skills.evolve_skill --skill github-code-review --dry-run
```

### Option B: Direct NVIDIA API (single token, no rotation)

Same pattern as `llmwiki-compile-nvidia.sh`:

```bash
export OPENAI_BASE_URL="https://integrate.api.nvidia.com/v1"
export OPENAI_API_KEY="$(cat ~/.enclave/nvidia_1.txt | tr -d '\n\r')"
export HERMES_AGENT_REPO=/Users/kieranlal/.hermes/hermes-agent

cd /Users/kieranlal/workspace/hermes-agent-self-evolution
python3 -m evolution.skills.evolve_skill \
    --skill github-code-review \
    --iterations 10 \
    --eval-source synthetic
```

### Option C: OpenAI direct (requires your own key)

```bash
export OPENAI_API_KEY=sk-...
export HERMES_AGENT_REPO=/path/to/hermes-agent

cd /Users/kieranlal/workspace/hermes-agent-self-evolution
python3 -m evolution.skills.evolve_skill \
    --skill github-code-review \
    --iterations 10 \
    --eval-source synthetic
```

**Python path note:** Use `python3` or the venv python at `/Users/kieranlal/workspace/.venv/bin/python3`. System `python` does not exist.

## Troubleshooting

### `BadRequestError: LLM Provider NOT provided`

You passed a model name without the `openai/` prefix. When using `OPENAI_BASE_URL`, litellm needs the provider prefix:
```bash
# WRONG:
--optimizer-model "nvidia-proxy/meta/llama-3.1-405b-instruct"

# CORRECT:
--optimizer-model "openai/nvidia-proxy/meta/llama-3.1-405b-instruct"
```

### Timeouts / hangs during optimization

NVIDIA endpoints are slow (~20-45s per call). MIPROv2 bootstraps fewshot sets + runs trials. With default settings this easily exceeds 10 minutes.

**Solutions:**
- Use `--dry-run` first to validate setup without LLM calls
- Use `google-proxy/gemma-4-31b-it` for 10x faster iteration (~3-5s per call)
- Use a small golden dataset instead of synthetic generation:
  ```bash
  --eval-source golden --dataset-path datasets/skills/github-code-review/
  ```
  Create the dataset manually as `train.jsonl`, `val.jsonl`, `holdout.jsonl` files.
- Reduce iterations: `--iterations 1` (only affects GEPA; MIPROv2 uses `num_trials`)
- Be patient — the process is working, just slow

### `ImportError: MIPROv2 requires optional dependency 'optuna'`

Install it:
```bash
uv pip install optuna -p /Users/kieranlal/workspace/.venv/bin/python3
```

### `GEPA.__init__() got an unexpected keyword argument 'max_steps'`

GEPA in the current DSPy version does NOT accept `max_steps`. The code auto-falls back to MIPROv2. This is expected behavior. The fallback path is the only working optimizer in this environment.

### MIPROv2 parameter pitfalls

If you edit the optimizer code directly, these combinations are required:

```python
# To use num_trials, you MUST set auto=None
optimizer = dspy.MIPROv2(
    metric=skill_fitness_metric,
    auto=None,           # required when passing num_trials
    num_candidates=1,    # required when auto=None
)
optimized_module = optimizer.compile(
    baseline_module,
    trainset=trainset,
    valset=valset,
    num_trials=1,        # now works because auto=None
    minibatch=False,     # required for small valset (< minibatch_size default 35)
)
```

Common errors and fixes:
- `If auto is not None, num_candidates and num_trials cannot be set` → set `auto=None`
- `If auto is None, num_candidates must also be provided` → add `num_candidates=1`
- `Minibatch size cannot exceed the size of the valset` → add `minibatch=False`

### `ValueError: Trainset must have at least 2 examples if no valset specified`

This happens when MIPROv2 fallback is triggered and `valset` is not passed. Fixed in current code, but if you see this on an older checkout, ensure your dataset has at least 2 train examples or pass an explicit valset.

### Evolved skill "fails" constraints with `skill_structure` error

The constraint validator expects YAML frontmatter (`---`, `name:`, `description:`) on all skills. Some Hermes skills (e.g. `github-code-review`) do not use YAML frontmatter. This causes the evolved skill to be saved as `evolved_FAILED.md` even though the pipeline worked correctly. The constraint check is overly strict for skills that use a different format.

### Default models already patched

The repo's default models have been changed from `openai/gpt-4.1` to `openai/nvidia-proxy/...`. If you pull updates and want OpenAI models again, pass them explicitly:
```bash
--optimizer-model "openai/gpt-4.1" --eval-model "openai/gpt-4.1-mini"
```

## What's Missing / TODO

If you want the full 5-phase vision from PLAN.md, these need to be built:
1. `evolution/core/benchmark_gate.py` — run TBLite/YC-Bench on evolved artifacts
2. `evolution/core/pr_builder.py` — auto-generate PRs with metrics
3. `evolution/tools/evolve_tool_descriptions.py` — Phase 2
4. `evolution/prompts/evolve_prompt_section.py` — Phase 3
5. `evolution/code/evolve_tool_code.py` — Phase 4 (Darwinian Evolver integration)
6. `evolution/monitor/continuous_loop.py` — Phase 5

## License

MIT — © 2026 Nous Research
