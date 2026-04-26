---
name: hermes-agent-self-evolution
description: Evolve and optimize Hermes Agent skills, tool descriptions, system prompts, and code using DSPy + GEPA (Genetic-Pareto Prompt Evolution). No GPU required — operates via API calls.
version: 1.1.0
author: Nous Research
category: software-development
---

# Hermes Agent Self-Evolution

Evolutionary self-improvement for Hermes Agent using DSPy + GEPA.

## Prerequisites

- Python 3.14 (via uv — NOT system python3 [3.9.6])
- uv installed (`/opt/homebrew/bin/uv`)
- Hermes Agent repo at `~/.hermes/hermes-agent`

## Setup

Use `uv run --no-project --python python3.14` to avoid the broken `pyproject.toml` dependency (`darwinian-evolver` doesn't exist in any registry).

```bash
cd /Users/kieranlal/workspace/hermes-agent-self-evolution
export HERMES_AGENT_REPO=/Users/kieranlal/.hermes/hermes-agent
uv run --no-project --python python3.14 python3 -m evolution.skills.evolve_skill --dry-run
```

**Path notes:**
- `uv` is at `/opt/homebrew/bin/uv` — Hermes' sandboxed PATH may not include it
- `.python-version` files exist in `isaac_ros_custom`, `nano2`, ops wiki — all pin `3.14`
- `uv` already manages Python 3.14.4 — no download needed
- System `/usr/bin/python3` is **3.9.6** — do not use it

## Current Operational State (2026-04-25)

**Status:** Batch evolution running (PID 99454), 1 skill completed (`coding-standards`), 1 skill active (`test-driven-dev`), 42 pending. Rotation order: size-sorted ascending (1.0 KB → 103 KB).

### Confirmed Working Models

| Model | Via | Status | Notes |
|-------|-----|--------|-------|
| `openai/nvidia-proxy/minimaxai/minimax-m2.5` | kilo-proxy | ✅ Primary | Fastest (5-10s/call), reliable JSON Schema support, handles up to 100KB skills |
| `openai/nvidia-proxy/deepseek-ai/deepseek-v3.2` | kilo-proxy | ✅ Tier 1 | Strong reasoning, any size, works through existing NVIDIA tokens |
| `gemini-2.0-flash` | direct Google API | ✅ Tier 1 | Bypasses degraded google-proxy; requires `GOOGLE_API_KEY` |
| `openai/nvidia-proxy/meta/llama-3.1-405b-instruct` | kilo-proxy | ✅ Tier 2 | Slow (20-45s/call) but strongest, any size |
| `openai/nvidia-proxy/moonshotai/kimi-k2.5` | kilo-proxy | ✅ Tier 2 | Medium speed, good eval quality |
| `openai/nvidia-proxy/nvidia/nemotron-3-super-120b-a12b` | kilo-proxy | ⚠️ Tier 2 | Can return 404 if model delisted from NIM catalog |

### Disabled / Unavailable Models

| Model | Reason | Workaround |
|-------|--------|------------|
| `openai/google-proxy/gemma-4-26b-a4b-it` | Google-proxy degraded: 503 on all structured output requests (2026-04-24) | Use direct `gemini-2.0-flash` instead |
| `openai/google-proxy/gemma-4-31b-it` | Google-proxy degraded (same as above) | — |
| `openai/deepseek-proxy/deepseek-v4*` | Token not yet retrieved from 1Password Connect on nano2 | Use `deepseek-v3.2` via nvidia-proxy for now |
| `@cf/*` (Cloudflare) | Does not support JSON Schema structured output required by DSPy | Excluded from rotation |
| `openai/google-proxy/gemini-2.0-flash-lite` | Google-proxy degraded | Use direct `gemini-2.0-flash` |

### Actual Batch Configuration in Use

**Batch orchestrator:** `~/workspace/nano2/scripts/evolve_batch_size_aware.sh`
- Uses venv Python: `/Users/kieranlal/workspace/hermes-agent-self-evolution/.venv/bin/python3`
- Exports `HERMES_AGENT_REPO` explicitly so workers locate skills
- Traps `EXIT`/`INT` → `pkill -f evolve_skill_rotation.py` for clean shutdown
- Sleeps 30s between skills
- Log: `~/.hermes/skills/.wrappers/batch_size_aware.log`

**Rotation script:** `~/workspace/nano2/scripts/evolve_skill_rotation.py` (809 lines)
- Size-sorted rotation (ascending)
- Size-aware timeouts: small (<10KB) → attempt 1200s / stall 400s; medium (10-100KB) → 1800s / 600s; large (>100KB) → 3600s / 1200s
- Phase-aware watchdog grace: `optimization_start` gets 2× base grace to accommodate MIPROv2 bootstrap
- Model rotation: each skill tracks `last_model_index`, cycles through eligible model list
- Cross-provider fallback: tries all NVIDIA models first, then direct models if all fail

**Socket timeouts (hardened, set before DSPy import):**
```python
socket.setdefaulttimeout(45)  # any socket operation
requests timeout: (30, 120)   # (connect, read)
```

**Models in `MODEL_LIST` (working configuration):**
```python
[
    # Tier 1: primary workhorses
    {"id": "openai/nvidia-proxy/minimaxai/minimax-m2.5", "provider": "nvidia", "available": True, "max_size_kb": 100},
    {"id": "openai/nvidia-proxy/deepseek-ai/deepseek-v3.2", "provider": "direct", "available": True, "max_size_kb": float('inf')},
    {"id": "gemini-2.0-flash", "provider": "direct", "available": True, "max_size_kb": float('inf')},
    # Tier 2: capacity (some may be offline)
    {"id": "openai/nvidia-proxy/nvidia/nemotron-3-super-120b-a12b", "provider": "nvidia", "available": True, "max_size_kb": 100},
    {"id": "openai/nvidia-proxy/meta/llama-3.1-405b-instruct", "provider": "nvidia", "available": True, "max_size_kb": float('inf')},
    {"id": "openai/nvidia-proxy/moonshotai/kimi-k2.5", "provider": "nvidia", "available": True, "max_size_kb": 100},
    # Disabled: google-proxy models (503 degraded), deepseek-v4 (token missing), cloudflare (no json_schema)
]
```

### Key Lessons from 2026-04-25 Batch Run

1. **Model ID format is non-negotiable:** All model IDs must use `openai/<provider>/<model>` format. Without the `openai/` prefix, litellm routes to native provider SDKs and completely bypasses kilo-proxy, causing "Unknown provider" or direct-API auth failures. **Verify with DSPy, not just curl.**

2. **Google-proxy is degraded on structured output** (503 BAD_REQUEST for all `response_format` requests). This is a provider-level capability outage, not a token issue. All `google-proxy/*` models are **disabled** in rotation until the proxy recovers. Use direct `gemini-2.0-flash` via Google's OpenAI-compatible endpoint instead.

3. **DeepSeek V3.2 works via nvidia-proxy** — no separate `deepseek-proxy` token needed. This provides large-skill coverage immediately. V4 models remain disabled pending 1Password Connect retrieval on nano2.

4. **Size-aware timeouts prevent false kills:** A flat 90s timeout killed `managing-hermes-honcho-containers` (102KB) at 96s while minimax was actively bootstrapping. The new tiered timeouts (1200/400 for small, 1800/600 for medium, 3600/1200 for large) allow large skills to complete.

5. **Process hygiene matters:** Batch restarts left orphaned workers (PIDs 16429, 14380, 18748). The `pkill -f` pattern in the stop handler and trap on EXIT/INT now ensure clean shutdowns. Always use `bash evolve_batch_size_aware.sh stop` before killing the batch.

6. **Progress file is ground truth:** The rotation state file can show `"running"` while the process is actually dead (zombie). Always cross-check `progress.jsonl` timestamps and `pgrep` before trusting status.

7. **socket.setdefaulttimeout + requests patch required** before DSPy import, or a hung endpoint blocks forever. The rotation script sets these at module top-level.

8. **GEPA now works (fixed 2026-04-25)** — Two bugs were fixed: (a) `max_steps` parameter doesn't exist in DSPy 3.2.0 GEPA — should be `max_full_evals`. (b) `skill_fitness_metric()` in `fitness.py` must accept 5 positional args `(example, prediction, trace, pred_name, pred_trace)` instead of 3. GEPA is now the primary optimizer; verification: logs show "Configuring GEPA..." not "Falling back to MIPROv2".

9. **Separate eval model impractical** — All attempts to use a stronger model for evaluation failed: DeepSeek V4 (11 consecutive failures), Gemini (no json_schema), Hermes 4 (litellm routing strips prefix). Proven approach: use same model for optimizer and eval.

10. **DeepSeek V4 model names** — API returns `deepseek-v4-flash` and `deepseek-v4-pro`, NOT plain `deepseek-v4`. The plain name fails with "model not found". Correct in rotation script.

11. **Model reliability ladder must be seeded** — Without historical seeding, all models start at uniform 0.50 and workers waste rounds exploring. Seed from historical data: minimax (11 attempts, 6 successes → 0.65), nemotron (3 attempts, 2 successes → 0.50).

12. **Epsilon-greedy tuned to 10%** — Starting at 20% meant ~1.6 of 8 workers wasted on random selection. Reduced to 10% after removing unreliable models (Gemini, Hermes 4). With 8 workers, 7.2 exploit proven models.

13. **State corruption requires wrapper-file-aware recovery** — When workers are killed, "running" entries persist in rotation_state.json. Reset logic must check for completed `.json` wrapper files in WRAPPERS_DIR and restore those as `completed`/`no_improvement`, falling back to `pending` for skills without wrappers.

### Quick Commands

```bash
# Check batch health
tail -20 ~/.hermes/skills/.wrappers/batch_size_aware.log
watch -n 30 "jq '.skills | to_entries[] | select(.value.status != \"pending\")' ~/.hermes/skills/.wrappers/.rotation_state.json"

# Force-reset all to pending (if needed)
python3 -c "import json,pathlib; p=pathlib.Path('~/.hermes/skills/.wrappers/.rotation_state.json'); d=json.loads(p.read_text()); [s.update({'status':'pending','error':None}) for s in d['skills'].values()]; p.write_text(json.dumps(d,indent=2))"

# Stop batch cleanly
bash ~/workspace/nano2/scripts/evolve_batch_size_aware.sh stop
```

## Tiered Model Routing (Critical)

Route each skill to the cheapest model that can handle its size. Prevents model thrashing:

**Why Google direct (not proxy):** The kilo-proxy `google-proxy` provider is degraded on structured output. Google's own OpenAI-compatible endpoint works fine — route small skills there directly.

**Easy button — use the wrapper (handles all routing):**

```bash
~/bin/evolve-skill.sh                          # next skill in rotation
~/bin/evolve-skill.sh --skill honcho-cli       # specific skill
~/bin/evolve-skill.sh --status                 # status of all 44 skills
~/bin/evolve-skill.sh --force --skill X        # re-evolve
```

The wrapper measures skill size, sets OPENAI_BASE_URL per tier, passes --model to rotation script. On Google failure, falls back to NVIDIA.

**Token pool:**
- 5 Google AIStudio tokens (`gemma_1.txt`–`gemma_5.txt`) via `google-proxy` — default for fast iteration
- 5 NVIDIA NIM tokens (`nvidia_1.txt`–`nvidia_5.txt`) via `nvidia-proxy` — fallback for stronger reasoning
- 1 Cloudflare Workers AI token via `cloudflare-proxy` — **NOT usable for DSPy MIPROv2** (see below)
- The proxy auto-rotates on 429s; no manual token management needed

**kilo-proxy health endpoint (built-in diagnostics):**
```bash
# Show all providers, tokens, current active token, degradation status
curl -s http://localhost:8080/ | python3 -m json.tool

# List available models per provider
curl -s http://localhost:8080/v1/models

# Active upstream probe (cached 30s)
curl -s http://localhost:8080/health/upstream

# Reload tokens from disk without restart
curl -s -X POST http://localhost:8080/tokens/reload

# Sync from 1Password enclave then reload
curl -s -X POST http://localhost:8080/tokens/refresh
```

**Log files:**
- `~/.local/share/kilo/log/token-rotation.log` (stdout — shows rotation decisions per request)
- `~/.local/share/kilo/log/proxy-launchd.err` (stderr — shows startup errors and panics)

## Usage

### Phase 1: Evolve a Skill

```bash
python -m evolution.skills.evolve_skill \
    --skill github-code-review \
    --iterations 10 \
    --eval-source synthetic \
    --num-trials 5
```

Or using session history:

```bash
python -m evolution.skills.evolve_skill \
    --skill github-code-review \
    --iterations 10 \
    --eval-source sessiondb \
    --num-trials 5
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
| **Phase 1** | Skill files (SKILL.md) | ⚠️ Partial | Pipeline runs but does NOT edit skill text (see below) |
| **Phase 2** | Tool descriptions | ❌ Empty stub | `evolution/tools/__init__.py` only |
| **Phase 3** | System prompt sections | ❌ Empty stub | `evolution/prompts/__init__.py` only |
| **Phase 4** | Tool implementation code | ❌ Empty stub | `evolution/code/__init__.py` only |
| **Phase 5** | Continuous improvement loop | ❌ Empty stub | `evolution/monitor/__init__.py` only |

### ⚠️ CRITICAL: MIPROv2 Does NOT Edit Skill Content

The optimizer improves the **task prompt wrapper** (the instructions that tell the LLM how to read and follow a skill), NOT the skill document itself. The evolved `SKILL.md` output is **byte-for-byte identical** to the baseline.

**What actually gets optimized:**
- The `dspy.Signature` docstring (e.g., "You are a specialized AI technical agent...")
- Few-shot example selection and ordering
- Instruction candidates that prepend to the task

**What does NOT get optimized:**
- Skill markdown body (Overview, Prerequisites, Quick Start, etc.)
- Command sequences or code blocks inside the skill
- Structure, headings, or file index

**Evidence:** A full `diff` of `output/*/baseline_skill.md` vs `output/*/evolved_skill.md` shows zero changes. The only difference is in the DSPy module's internal prompt state, which is ephemeral and not serialized into the skill file.

**Why this happens:** `SkillModule` wraps `dspy.Predict(TaskWithSkill)` where `skill_instructions` is an **InputField**. MIPROv2 optimizes the signature's `__doc__` and bootstrapped demos — it has no mechanism to rewrite an input string. It is a prompt optimizer, not a text editor.

**Implications:**
- The "evolved skill" artifact is misleading — it is the baseline skill with a better prompt wrapper
- True skill content evolution requires a **mutator** (LLM-based text editing) + **fitness function** + **selector** loop
- MIPROv2 is useful for optimizing how the *agent* reads skills, not for improving the skills themselves

### Validation/Holdout Overfitting on Tiny Datasets

With only 3 validation examples (MIPROv2 `light` default), the optimizer overfits. Example from `running-isaac-ros-tests`:

| Set | Baseline | Best Trial | Change |
|-----|----------|------------|--------|
| Val (3 ex) | 49.62 | **51.57** (Instr 2 + FS 0) | +3.9% |
| Holdout (2 ex) | 0.554 | 0.494 | **-6.0%** |

The "improvement" on validation does not generalize. Minimum viable val set: **10+ examples**. Recommended: 25+.

### GEPA Fixed (2026-04-25) — Now Working, Was Non-Functional

Two bugs prevented GEPA from working in DSPy 3.2.0:

1. **Wrong parameter name:** `max_steps` doesn't exist — should be `max_full_evals`. Fixed in `evolution/skills/evolve_skill.py` line 251.

2. **Metric signature too narrow:** `skill_fitness_metric()` accepted only 3 args `(example, prediction, trace)` but DSPy 3.2.0 GEPA calls it with **5 positional args**: `(example, prediction, trace, pred_name, pred_trace)`. Fixed in `evolution/core/fitness.py`.

**Verify GEPA is active:** Check logs for `"Configuring GEPA..."` — if you see `"Falling back to MIPROv2"` after both fixes are applied, the metric signature is still wrong.

**Eval model constraint:** All attempts to use a separate/stronger model for evaluation failed:
- DeepSeek V4: 11 consecutive failures through deepseek-proxy
- Gemini models: no `json_schema` support required by DSPy
- Nous Hermes 4: litellm strips `nousresearch/` prefix, routes to wrong endpoint

**Proven approach:** Use the same model for both optimizer and eval. This was the original working configuration that produced the +0.051 best score improvement.

**Model reliability ladder seeding:** Instead of starting all models at uniform 0.50 reliability (wasting rounds on exploration), seed from historical data:
```python
model_stats = {
    "minimax-m2.5": {"attempts": 11, "successes": 6, "reliability": 0.65},
    "nemotron":    {"attempts": 3,  "successes": 2, "reliability": 0.50},
}
```
Seeded in `rotation_state.json` before batch launch.

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
- Skill structure validation — YAML frontmatter OR plain-markdown accepted
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

### Option A: Through kilo-proxy with Google AIStudio

```bash
export OPENAI_BASE_URL="http://localhost:8080/v1"
export OPENAI_API_KEY="dummy"
export HERMES_AGENT_REPO=/Users/kieranlal/.hermes/hermes-agent

cd /Users/kieranlal/workspace/hermes-agent-self-evolution
python3 -m evolution.skills.evolve_skill \
    --skill github-code-review \
    --iterations 1 \
    --eval-source golden \
    --dataset-path datasets/skills/github-code-review \
    --optimizer-model "gemini-2.0-flash" \
    --eval-model "gemini-2.0-flash"
```

### Option B: Through kilo-proxy with NVIDIA NIM (slower, stronger reasoning)

```bash
export OPENAI_BASE_URL="http://localhost:8080/v1"
export OPENAI_API_KEY="dummy"
export HERMES_AGENT_REPO=/Users/kieranlal/.hermes/hermes-agent

cd /Users/kieranlal/workspace/hermes-agent-self-evolution
python3 -m evolution.skills.evolve_skill \
    --skill github-code-review \
    --iterations 10 \
    --optimizer-model "minimaxai/minimax-m2.5" \
    --eval-model "moonshotai/kimi-k2.5"
```

**⚠️ CORRECTION (2026-04-24): DSPy/litellm REQUIRES the `openai/` prefix — curl tests do NOT.**

This was the most hard-won lesson of the evolution pipeline. There are TWO different behaviors depending on how you call the proxy:

### DSPy/litellm (via OPENAI_BASE_URL): `openai/nvidia-proxy/...` IS CORRECT

When `OPENAI_BASE_URL=http://localhost:8080/v1` is set, DSPy/litellm sees the `openai/` prefix and routes through the custom base URL. **Before sending to the proxy, litellm strips `openai/`.** So the proxy receives a model name it recognizes:

```
DSPy sends:       openai/nvidia-proxy/minimaxai/minimax-m2.5
litellm strips:   openai/
Proxy receives:   nvidia-proxy/minimaxai/minimax-m2.5    ✓ routeable
```

**Verified working model IDs (tested 2026-04-24 through dspy.LM + dspy.Predict):**
```python
import dspy
lm = dspy.LM('openai/nvidia-proxy/minimaxai/minimax-m2.5')
dspy.configure(lm=lm)
class T(dspy.Signature): msg: str = dspy.OutputField()
result = dspy.Predict(T)()
# → SUCCESS
```

### Direct curl (bypassing litellm): NO `openai/` prefix

Curl sends the model AS-IS in the POST body. The proxy does NOT recognize `openai/` prefix:

```bash
# WRONG for curl:
curl ... -d '{"model": "openai/nvidia-proxy/minimaxai/minimax-m2.5"}'
# → "Unknown provider for model 'openai/nvidia-proxy/minimaxai/minimax-m2.5'"

# CORRECT for curl:
curl ... -d '{"model": "nvidia-proxy/minimaxai/minimax-m2.5"}'
# → OK — proxy matches nvidia-proxy prefix
```

### Why this is confusing

The NATURAL debugging path that leads to the wrong conclusion: "model X doesn't work → test it with curl → curl says 'Unknown provider' → strip the prefix → curl works → update the code to use bare names → code breaks."

**Always verify model IDs through DSPy, not just curl.**

### Google models FAIL even with `openai/` prefix

`openai/google-proxy/gemma-4-26b-a4b-it` is NOT a valid DSPy model ID. Even though litellm strips `openai/` and sends `google-proxy/gemma-4-26b-a4b-it` to the proxy, litellm's internal model detection recognizes `gemma-4-26b-a4b-it` as a Google model and tries to route through Vertex AI — which crashes without `google-cloud-aiplatform` installed.

Use only NVIDIA-routed models with `openai/nvidia-proxy/...` prefix for DSPy MIPROv2.

### DeepSeek models (when proxy is wired)

Use the same pattern:
```bash
openai/deepseek-proxy/deepseek-v4
# litellm strips openai/ → proxy sees deepseek-proxy/deepseek-v4 → routes through deepseek-proxy
```

**Latency by provider (bare model names, no `openai/` prefix):**
- `gemma-4-26b-a4b-it` — ~3-5s per call (when google-proxy tokens available)
- `gemma-4-31b-it` — ~3-5s per call, but can go completely offline (HTTP 500 then all tokens timeout)
- `meta/llama-3.1-405b-instruct` — ~20-45s per call, 5 NIM tokens
- `moonshotai/kimi-k2.5` — ~15-25s per call
- `minimaxai/minimax-m2.5` — ~5-10s per call, most reliable NVIDIA model
- `nvidia/nemotron-3-super-120b-a12b` — ~10-15s per call, but can return 404 when model is delisted
- `qwen/qwen3.5-397b-a17b` — ~15-30s per call, massive 397B MoE

For testing and fast iteration, use `google-proxy/gemma-4-26b-a4b-it`. For production optimization with stronger reasoning, use `nvidia-proxy/meta/llama-3.1-405b-instruct`.

**Available models (via proxy, bare names — no `openai/` prefix):**
- `moonshotai/kimi-k2.5`
- `minimaxai/minimax-m2.5`
- `nvidia/nemotron-3-super-120b-a12b`
- `meta/llama-3.1-405b-instruct`
- `deepseek-ai/deepseek-v3.2` (may time out — not reliable for json_schema)
- `qwen/qwen3.5-397b-a17b`
- `gemini-2.0-flash` (when google-proxy available)
- `gemma-4-26b-a4b-it` (<10KB skills only, when google-proxy available)
- `gemma-4-31b-it` (<10KB skills only, when google-proxy available)

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

### Option C: Direct NVIDIA API (single token, no rotation)

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

### Option D: OpenAI direct (requires your own key)

```bash
export OPENAI_API_KEY="sk-..."
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

### Non-Interactive / Background Execution: `requires_permission_to_run=False`

**CRITICAL:** MIPROv2 prompts for confirmation before running, which aborts immediately in non-interactive contexts (cronjobs, background processes, no TTY):

```
To proceed with the execution of this program, please confirm by typing 'y' ...
Compilation aborted by the user.
```

The code now passes `requires_permission_to_run=False` to all `.compile()` calls. If you are on an older checkout or calling MIPROv2 directly, you **must** add this parameter:

```python
optimized_module = optimizer.compile(
    baseline_module,
    trainset=trainset,
    valset=valset,
    requires_permission_to_run=False,  # REQUIRED for cron/background
)
```

Without this, every batch/cron invocation will abort after building the dataset and score zero improvement.

### Provider Outage: Gemma 31b Can Fail Completely

`google-proxy/gemma-4-31b-it` is fast when healthy, but **can go completely offline** — the first request returns HTTP 500, then all 5 tokens timeout after 60s sequentially. This wastes ~5 minutes before kilo-proxy gives up.

**Observed pattern (2026-04-23):**
```
[google-proxy] Response 500 | TTFB: 0.7s | token: gemma_1.txt | model: gemma-4-31b-it
[google-proxy] Timeout on gemma_1.txt — ROTATING
[google-proxy] Timeout on gemma_2.txt — ROTATING
... (all 5 tokens)
[google-proxy] ALL TOKENS FAILED
```

**Meanwhile, `gemma-4-26b-a4b-it` on the SAME tokens works fine.** This is a model-specific outage, not a token/auth issue.

**Also observed:** `nvidia-proxy/nvidia/nemotron-3-super-120b-a12b` can return HTTP 404 when the model is temporarily delisted from the NIM catalog.

### Cloudflare Workers AI: Incompatible with DSPy MIPROv2 (2026-04-24)

**Cloudflare Workers AI does NOT support complex `json_schema` structured output** required by DSPy MIPROv2. It only supports basic `json_object` (unstructured JSON). MIPROv2 uses JSON Schema for bootstrapping few-shot examples and instruction candidates, which causes `BAD_REQUEST` or `503 degraded` errors.

**Observed behavior:**
```
[cloudflare-proxy] Response 503 | TTFB: 0.3s | token: cloudflare.txt | model: @cf/meta/llama-3.1-8b-instruct
[cloudflare-proxy] Degraded: 503 on cloudflare.txt
```

**Verdict:** Do not use Cloudflare for the evolution pipeline. Use **Google (5 tokens)** or **NVIDIA (5 tokens)** only.

**Empirically verified token rotation (2026-04-24):**
- Google: `gemma_4.txt` → `gemma_3.txt` confirmed active after rotation
- NVIDIA: `nvidia_5.txt` → `nvidia_2.txt` confirmed active after rotation
- Cloudflare: `cloudflare.txt` loaded but fails on first MIPROv2 structured output call

### Provider-Specific Hangs During Dataset Generation: Minimax Actually Works (2026-04-24)

**CORRECTION:** The earlier diagnosis that "minimax hangs indefinitely" was wrong. The model was actively making progress but was killed by an **aggressive flat timeout** before it could complete.

**Evidence from `managing-hermes-honcho-containers` (102.8KB):**
```
Model: minimax-m2.5
Phase reached: optimization_start
Progress: Bootstrapped 4 full traces, reached 40% of bootstrapping set 4/6
Result: Killed by watchdog at 96s (>90s flat timeout)
```

Minimax successfully bootstrapped 4 traces for a **102KB skill** in ~80s. The 90s timeout killed it mid-flight. Nemotron-3-super-120b on the SAME skill only reached `skill_loaded` before being killed at 98s — minimax is actually **faster** than nemotron for large context.

**Why this was misdiagnosed:**
- A flat 90s timeout kills ALL models on large skills equally
- Minimax reaches later phases before dying, making it *appear* to hang at `optimization_start`
- Nemotron dies earlier (at `skill_loaded`), so it doesn't trigger the "hangs at later phase" confusion

**Corrected understanding:**
- Minimax **supports** `response_format` (structured JSON Schema) reliably
- Minimax is the **fastest** NVIDIA model at inference (~5-10s per call)
- Minimax does NOT hang on large context; it just needs proportionally more time
- For small skills (<10KB), minimax completes fully within 90s — that's why we already have wrappers from early runs
- For large skills, minimax needs the same size-aware timeout as any other model

**The real fix is size-aware timeouts, not model avoidance.** See "Size-Aware Timeouts" below.

### Watchdog Recovery: Killing Hung Processes with multiprocessing (2026-04-24)

**Problem:** Free-tier models hang forever (0% CPU, no progress) during MIPROv2 optimization. The Python process blocks indefinitely in an I/O wait loop. A single hung model can stall the entire batch evolution pipeline for hours.

**Root cause:** DSPy/MIPROv2 makes blocking HTTP calls to the LLM API. If the model endpoint stops responding (but doesn't close the TCP connection), the request hangs forever. `requests` / `urllib3` has no default timeout for reading response body. The process shows 0% CPU in `S` (sleep) state.

**Solution:** Run each model attempt in an **isolated `multiprocessing.Process`** with:
1. **Hard timeout** — `join(timeout=600)` kills the process after 10 minutes regardless of state
2. **Progress file stall detection** — `progress.jsonl` must advance at least every 5 minutes
3. **Restart loop detection** — if `progress.jsonl` shows >3 `"phase": "start"` entries, the process is stuck in a crash-restart cycle
4. **Graceful fallback** — when killed, return `status="hung"` so the retry loop tries the next model

**Implementation:**

```python
import multiprocessing

MODEL_ATTEMPT_TIMEOUT_SECONDS = 600  # 10 min max per model
PROGRESS_STALL_SECONDS = 300         # 5 min without new progress.jsonl = hung
MAX_RESTART_LOOPS = 3                # >3 "start" entries = kill

def check_progress_health(progress_file: Path) -> tuple[bool, str]:
    """Detect hung evolution by inspecting progress.jsonl.
    Returns (healthy, reason)."""
    if not progress_file.exists():
        return True, "no progress file yet"

    lines = progress_file.read_text().strip().split("\n")
    entries = [json.loads(line) for line in lines if line.strip()]

    # Check 1: Restart loop detection
    start_count = sum(1 for e in entries if e.get("phase") == "start")
    if start_count > MAX_RESTART_LOOPS:
        return False, f"restart loop ({start_count} starts, max {MAX_RESTART_LOOPS})"

    # Check 2: Progress stall detection
    last_entry = entries[-1]
    last_ts = datetime.fromisoformat(last_entry["_ts"].replace("Z", "+00:00"))
    stalled = (datetime.now().astimezone() - last_ts).total_seconds()
    if stalled > PROGRESS_STALL_SECONDS:
        return False, f"stalled {stalled:.0f}s at phase '{last_entry.get('phase')}'"

    return True, f"last phase: {last_entry.get('phase', 'unknown')}"

def _evolve_worker(skill_name, skill_path, model, ..., result_queue):
    """Runs in isolated subprocess. Safely killable."""
    try:
        from evolution.skills.evolve_skill import evolve
        evolve(skill_name=skill_name, skill_path=skill_path,
               optimizer_model=model, eval_model=model, ...)
        # Extract metrics, copy wrapper...
        result_queue.put({"status": "done", ...})
    except Exception as e:
        result_queue.put({"status": "failed", "error": str(e)})

def run_evolution_with_watchdog(skill_name, skill_path, model, ...) -> dict:
    output_dir = HERMES_REPO / "output" / skill_name
    progress_file = output_dir / "progress.jsonl"
    result_queue = multiprocessing.Queue()

    proc = multiprocessing.Process(
        target=_evolve_worker,
        args=(skill_name, str(skill_path), model, ..., result_queue),
    )
    proc.start()

    # Poll with health checks
    while proc.is_alive() and (time.time() - start) < MODEL_ATTEMPT_TIMEOUT_SECONDS:
        healthy, reason = check_progress_health(progress_file)
        if not healthy:
            proc.terminate()
            proc.join(timeout=5)
            if proc.is_alive():
                proc.kill()
            return {"status": "hung", "error": f"Watchdog: {reason}"}
        time.sleep(10)

    # Timeout kill
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=5)
        if proc.is_alive():
            proc.kill()
        return {"status": "hung", "error": f"Timeout after {MODEL_ATTEMPT_TIMEOUT_SECONDS}s"}

    # Collect result
    if not result_queue.empty():
        return result_queue.get_nowait()
    return {"status": "failed", "error": "Subprocess died without result"}
```

**State machine integration:**
```python
# In the fallback loop, "hung" is treated same as "failed"
if result["status"] not in ("failed", "hung"):
    state.update_skill(target, status="done", ...)
    break
else:
    # Try next model
    continue
```

**Why multiprocessing over threading:**
- Python's `threading` cannot kill a blocked thread (no `Thread.terminate()`)
- `multiprocessing.Process` has `.terminate()` and `.kill()` which reliably abort hung I/O
- Each attempt is fully isolated — no state leakage between model retries
- The parent process remains responsive to monitor health

**Why not `signal.alarm()` or `timeout` command:**
- `signal.alarm()` doesn't work on Windows and is unreliable inside DSPy's async loops
- `timeout` command is not installed on macOS by default
- `multiprocessing` is cross-platform and works inside complex Python frameworks

**Stale progress file pitfall:**
If `progress.jsonl` from a previous failed run is not cleared, the restart-loop detector will count old `"phase": "start"` entries and immediately kill the new process. Always unlink the file before starting a new attempt:

```python
output_dir = HERMES_REPO / "output" / skill_name
progress_file = output_dir / "progress.jsonl"
if progress_file.exists():
    progress_file.unlink()  # critical: prevents false restart-loop kills
```

**Tunable parameters:**
| Parameter | Default | When to adjust |
|-----------|---------|----------------|
| `MODEL_ATTEMPT_TIMEOUT_SECONDS` | 600 (10 min) | Increase for slow NVIDIA models (1200s), decrease for fast Google models (300s) |
| `PROGRESS_STALL_SECONDS` | 300 (5 min) | MIPROv2 bootstrapping can take 2-3 min per set — keep >180s |
| `MAX_RESTART_LOOPS` | 3 | Lower to 2 for faster failure detection, raise to 5 for flaky providers |

**Status tracking:** The rotation state records `"hung"` as a distinct status (separate from `"failed"`). Both `"failed"` and `"hung"` trigger the model fallback loop. The `--status` table shows:
```
Summary: 42 pending | 1 done | 0 failed | 0 hung | 44 total
```

### Model Rotation: Cycling Through Multiple Models Per Provider (2026-04-24)

**Problem:** Individual models can hang, return malformed output, or be temporarily delisted. A single-model-per-provider configuration means one bad model blocks the entire pipeline.

**Solution:** The rotation script now maintains a **list of models per provider** and cycles through them using `last_model_index` persisted in the rotation state.

**Implementation in `evolve_skill_rotation.py`:**

```python
# Before: single model per provider
PROVIDER_MODELS: dict[str, str] = {
    "google": "openai/google-proxy/gemma-4-26b-a4b-it",
    "nvidia": "openai/nvidia-proxy/minimaxai/minimax-m2.5",
}

# After: model lists with rotation
PROVIDER_MODELS: dict[str, list[str]] = {
    "google": [
        "openai/google-proxy/gemma-4-26b-a4b-it",
        "openai/google-proxy/gemma-4-31b-it",
        "openai/google-proxy/gemma-3-27b-it",
        "openai/google-proxy/gemma-3-12b-it",
        "openai/google-proxy/gemma-2-2b-it",
    ],
    "nvidia": [
        "openai/nvidia-proxy/minimaxai/minimax-m2.5",
        "openai/nvidia-proxy/nvidia/nemotron-3-super-120b-a12b",
        "openai/nvidia-proxy/meta/llama-3.1-405b-instruct",
    ],
}

def pick_model_rotation(skill_name: str, provider: str, state: RotationState) -> tuple[str, int]:
    """Pick next model in rotation, cycling evenly through all available models."""
    models = PROVIDER_MODELS[provider]
    last_idx = state.skills.get(skill_name, {}).get("last_model_index", -1)
    next_idx = (last_idx + 1) % len(models)
    return models[next_idx], next_idx
```

**Retry logic in `main()`:**
```python
for attempt, model in enumerate(models_to_try, 1):
    try:
        result = evolve(..., optimizer_model=model, eval_model=model, ...)
        if result.get("success"):
            state.update_skill(skill_name, status="done", model=model,
                               last_model_index=model_index, ...)
            break
    except Exception as e:
        if attempt < len(models_to_try):
            continue  # try next model
        raise  # all models exhausted
```

**Benefits:**
- NVIDIA `minimax-m2.5` hangs on `s3-server-side-copy` → falls back to `nemotron-3-super` or `llama-3.1-405b`
- Google `gemma-4-26b` returns `{summary}` malformed JSON → falls back to `gemma-4-31b` or `gemma-3-27b`
- Even load distribution across all tokens/models

**State tracking:** The rotation state records `last_model_index` per skill so the next evolution starts from the following model, not always model 0.

### Google Provider Returns `{summary}` During MIPROv2 Bootstrapping (2026-04-24)

**Observed with:** `managing-mcp-configuration` on `gemma-4-26b-a4b-it`
**Error:**
```
Adapter JSONAdapter failed to parse the LM response.
LM Response: {summary}
Expected to find output fields: [proposed_instruction]
Actual output fields parsed: []
```

**What happens:** During MIPROv2's few-shot bootstrapping (step 1), the model is asked to generate instruction candidates in a structured format. Instead of valid JSON, it returns the literal string `{summary}` — likely a template placeholder that wasn't filled.

**Diagnostic:** The error appears in the state file:
```bash
cat ~/.hermes/skills/.wrappers/.rotation_state.json | jq '.skills["managing-mcp-configuration"].error'
```

**Workaround:**
1. Reduce `--num-threads` to 1 (some models handle structured output better without concurrency)
2. Use `--auto light` (fewer bootstrap sets = fewer opportunities for malformed output)
3. Retry with NVIDIA provider if Google fails on a specific skill
4. For critical skills, use `--eval-source golden` with a hand-written dataset to bypass synthetic generation entirely

### `progress.jsonl` as the Ground Truth for Process Health (2026-04-24)

When running evolutions in background, **do not trust the state file** (`.rotation_state.json`) alone. The state file can show `"running"` while the process is actually a zombie.

**Reliable health check:**
```bash
# 1. Get the last progress timestamp
jq -r '._ts' /Users/kieranlal/workspace/hermes-agent-self-evolution/output/SLUG/progress.jsonl | tail -1

# 2. Compare to current time
# If > 10 minutes old, the process is dead or hung

# 3. Cross-check with actual process
pgrep -f "evolve_skill.*SLUG" || echo "PROCESS DEAD"
```

**The state file lies; the progress file tells the truth.** Always check `progress.jsonl` timestamps before trusting a `"running"` status.

**When this happens:**
- kilo-proxy log shows: `Timeout on gemma_N.txt — ROTATING` for all 5 tokens
- Then: `ALL TOKENS FAILED`
- The evolution script wastes ~5 minutes before failing

**Fix:** Use `gemma-4-26b-a4b-it` as the default — it uses the same tokens but is more reliable:
```bash
--optimizer-model "openai/google-proxy/gemma-4-26b-a4b-it" \
--eval-model "openai/google-proxy/gemma-4-26b-a4b-it"
```

If BOTH Google models are down, failover to NVIDIA:
```bash
--optimizer-model "openai/nvidia-proxy/minimaxai/minimax-m2.5"
```

**Batch rotation script defaults** (as of 2026-04-24):
- `google` provider → `gemma-4-26b-a4b-it` (was `gemma-4-31b-it`)
- `nvidia` provider → `minimaxai/minimax-m2.5` (was `nemotron-3-super-120b-a12b`)
- **Cloudflare is excluded from rotation** — it does not support DSPy MIPROv2 structured output (see "Cloudflare Workers AI" section above)
- Provider assignment: even skill index → Google, odd skill index → NVIDIA (`idx % 2`)

Test provider health before a batch run:
```bash
python3 -c "
import requests
for model in ['google-proxy/gemma-4-26b-a4b-it', 'nvidia-proxy/minimaxai/minimax-m2.5']:
    try:
        r = requests.post('http://localhost:8080/v1/chat/completions',
            json={'model': f'openai/{model}', 'messages': [{'role': 'user', 'content': 'pong'}], 'max_tokens': 5},
            timeout=12)
        print(f'{model}: {\"OK\" if r.status_code == 200 else r.status_code}')
    except Exception as e:
        print(f'{model}: FAIL ({e})')
"
```

**kilo-proxy improvement needed:** The proxy currently has no provider-wide health detection. When the first request gets 5xx and subsequent requests timeout, it should mark the provider degraded and fail over to an alternate provider automatically. This would save 5 minutes per outage.

### Robust JSON Parsing for Synthetic Datasets

When using `--eval-source synthetic`, the LLM generates JSON test cases. Gemma models sometimes produce malformed JSON:
- Trailing commas before `]` or `}`
- Missing delimiters (`Expecting ',' delimiter`)
- Control characters or single-quoted strings

**Before fix:** The pipeline crashes with `json.decoder.JSONDecodeError` and the skill is marked failed.

**After fix:** `dataset_builder.py` uses a 5-strategy fallback parser:
1. Direct `json.loads()`
2. Regex extract `[...]` array
3. Auto-fix trailing commas + control chars + single quotes
4. Extract individual `{...}` objects one by one
5. Minimal default dataset (2 generic examples) with a warning

This ensures the evolution always proceeds even with bad LLM output.

### Sandboxed `$HOME` Pitfall

The evolution code uses `Path.home()` to locate `~/.hermes/hermes-agent`. In sandboxed environments, `Path.home()` resolves to the **sandboxed** home (e.g. `/Users/kieranlal/.hermes/profiles/coding/home/`), not the real user home. This causes `FileNotFoundError` on the hermes-agent repo.

**Always set `HERMES_AGENT_REPO` explicitly:**
```bash
export HERMES_AGENT_REPO=/Users/kieranlal/.hermes/hermes-agent  # absolute path
```

The batch rotation script now sets this automatically:
```python
HERMES_AGENT_REPO = Path(os.getenv("HERMES_AGENT_REPO", "/Users/kieranlal/.hermes/hermes-agent"))
```

### Python API: `evolve()` Function Signature

If calling the evolution pipeline programmatically (e.g. from the rotation script), use the correct signature:

```python
from evolution.skills.evolve_skill import evolve

evolve(
    skill_name="github-code-review",           # required — name of the skill
    skill_path="/path/to/SKILL.md",            # optional — bypass repo search
    optimizer_model="openai/google-proxy/gemma-4-26b-a4b-it",
    eval_model="openai/google-proxy/gemma-4-26b-a4b-it",
    eval_source="synthetic",                    # or "golden", "sessiondb"
    dataset_path="datasets/skills/...",         # required for "golden" source
    iterations=10,
    num_trials=20,
    num_candidates=10,
    auto_mode="light",                          # or "medium", "heavy", None
    progress_file="/path/to/progress.jsonl",    # optional — checkpoints
    run_tests=False,
    dry_run=False,
)
```

**Wrong (will fail):**
```python
evolve(skill="github-code-review", model="openai/...")  # Wrong param names
```

**Correct:**
```python
evolve(skill_name="github-code-review", optimizer_model="openai/...")
```

NVIDIA endpoints are slow (~20-45s per call). MIPROv2 bootstraps fewshot sets + runs trials. With default settings this easily exceeds 10 minutes.

**Solutions:**
- Use `--dry-run` first to validate setup without LLM calls
- Use `google-proxy/gemma-4-26b-a4b-it` for 10x faster iteration (~3-5s per call)
- If gemma-4-26b is also down, failover to `nvidia-proxy/minimaxai/minimax-m2.5` (~5-10s per call)
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

**Update (2026-04-25): This is now FIXED.** The code used `max_steps` but DSPy 3.2.0 GEPA expects `max_full_evals`. See the "GEPA Fixed" section above. If you still see this error, your checkout has stale code — pull from upstream or manually patch `evolution/skills/evolve_skill.py` line 251.

### gemma-4-31b-it outputs stray `` tags with `ChainOfThought`

When using `dspy.ChainOfThought` with gemma-4-31b-it, the model sometimes outputs stray `` tags or truncated reasoning chains. The fix is to use `dspy.Predict` instead of `dspy.ChainOfThought` in `skill_module.py` for the task executor. This avoids parsing failures and still produces valid output because the skill instructions already contain the reasoning procedure.

### Multiprocessing hang with `num_threads > 1`

Passing `num_threads=5` (or any value > 1) to MIPROv2 causes the process to hang indefinitely on macOS due to a multiprocessing semaphore leak. The process must be killed with SIGKILL. **Always run single-threaded:** omit `--num-threads` entirely or pass `num_threads=1`.

### `--skill-path` flag for external skill repos

**For one-off evolution:** Use `--skill-path` to evolve a skill without copying it into the hermes-agent repo:
```bash
python3 -m evolution.skills.evolve_skill \
    --skill running-isaac-ros-tests \
    --skill-path /Users/kieranlal/workspace/isaac_ros_custom/.claude/skills/running-isaac-ros-tests/SKILL.md \
    --eval-source golden \
    --dataset-path datasets/skills/running-isaac-ros-tests \
    --auto light
```

**For batch rotation across many skills:** Use the rotation script instead. It discovers skills from all repos, orders by recency, and rotates through them automatically:
```bash
# Show status of all skills across all repos
python3 ~/workspace/nano2/scripts/evolve_skill_rotation.py --status

# Evolve the next skill in rotation (most recently modified first)
python3 ~/workspace/nano2/scripts/evolve_skill_rotation.py --auto light

# Force re-evolve a specific skill
python3 ~/workspace/nano2/scripts/evolve_skill_rotation.py --skill running-isaac-ros-tests --force

# Preview what would run next without modifying state
python3 ~/workspace/nano2/scripts/evolve_skill_rotation.py --dry-run
```

**How batch rotation works:**
1. Discovers skills from all configured repos (e.g. `nano2/.claude/skills/`, `isaac_ros_custom/.claude/skills/`)
2. Orders them by recency via `git log --format=%ct` on each `SKILL.md`
3. Maintains persistent rotation state in `~/.hermes/skills/.wrappers/.rotation_state.json`
4. Runs one skill per invocation, then copies the evolved wrapper to `~/.hermes/skills/.wrappers/<skill>.json`
5. Hermes loads wrappers via `_load_prompt_wrapper()` in `skill_commands.py` to replace generic activation notes with evolved instructions + few-shot demos

**DSPy → kilo-proxy wiring (CRITICAL):**
The script sets these env vars **before importing dspy**:
```python
os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:8080/v1")
os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("HERMES_AGENT_REPO", "/Users/kieranlal/.hermes/hermes-agent")
```
If dspy is imported first, it caches the real OpenAI endpoint and ignores the proxy. If `HERMES_AGENT_REPO` is not set, `Path.home()` may resolve to the sandboxed home and fail to find the repo.

**Thread safety:**
Default `--num-threads` is reduced to **2** (from 5) to avoid hammering the proxy. The proxy handles 429s with jittered backoff and token cooldown, but lower concurrency is safer.

**Cronjob setup:**
```bash
# Run every 3 hours (8 skills/day)
cronjob create --name skill-evolution-rotation \
  --schedule "0 */3 * * *" \
  --prompt "cd /Users/kieranlal/workspace/hermes-agent-self-evolution && \
    python3 /Users/kieranlal/workspace/nano2/scripts/evolve_skill_rotation.py \
    --auto medium --eval-source synthetic"
```

### Multi-File Skills: Loading the Full Corpus

**Most hard-won skills have 5-6 linked files, but the pipeline originally only read `SKILL.md`.**

For `running-isaac-ros-tests`, the directory contains:

| File | Size | Content Type |
|------|------|-------------|
| `TACTICAL.md` | 20,749 | Negative data — incidents, anti-patterns, pitfalls |
| `EXAMPLES.md` | 18,833 | Positive data — verified scenarios, expected outcomes |
| `RECOVERY.md` | 14,267 | Recovery paths — if-then corrections |
| `REFERENCE.md` | 12,502 | Static reference — ARC patterns, Zot artifacts |
| `SKILL.md` | 11,566 | Entry point — triggers, prerequisites |
| `WORKFLOWS.md` | 4,504 | Command sequences, execution options |

**Total: 82,421 bytes. `SKILL.md` alone: 11,566 bytes (14%).**

**The problem:** `load_skill()` in `evolution/skills/skill_module.py` originally did `skill_path.read_text()` — only `SKILL.md`. The optimizer was blind to 86% of the skill content, including all incident data and verified examples.

**The fix:** Patch `load_skill` to concatenate all `.md` files in the skill directory:

```python
def load_skill(skill_path: Path) -> dict:
    skill_dir = skill_path.parent
    skill_files = sorted(skill_dir.glob("*.md"))
    if skill_path in skill_files:
        skill_files.remove(skill_path)
        skill_files.insert(0, skill_path)

    raw_parts = []
    for f in skill_files:
        raw_parts.append(f"\n\n# === {f.name} ===\n\n")
        raw_parts.append(f.read_text())
    raw = "".join(raw_parts)
    # ... frontmatter parsing from SKILL.md only ...
```

**Result:** The full 82KB corpus is loaded as `skill["body"]` and passed to MIPROv2 as the optimizable parameter.

### Building Golden Datasets from Multi-File Skills

When hand-writing eval datasets for multi-file skills, draw from the right source files:

| Source File | Example Type | What to Extract |
|-------------|-------------|-----------------|
| `EXAMPLES.md` | **Positive** | Verified scenarios with expected outcomes, exact commands, pass/fail criteria |
| `TACTICAL.md` | **Negative / Pitfall** | What went wrong, why it happened, the fix pattern, how to detect it |
| `RECOVERY.md` | **Recovery** | If-then paths — "If X fails, do Y" |
| `WORKFLOWS.md` | **Procedural** | Step-by-step command sequences |

**Positive example from `EXAMPLES.md`:**
```json
{"task_input": "User wants to run the full 18-rung test ladder on nano2.",
 "expected_behavior": "1. Check no ladder running: argo list -n argo --running. 2. Verify Zot annotations: oras manifest fetch ... 3. Submit: argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2 ..."}
```

**Negative/pitfall example from `TACTICAL.md`:**
```json
{"task_input": "User says: 'My ladder run failed at rung 06 with exit code 127.'",
 "expected_behavior": "1. Exit 127 = command not found in alpine pod. 2. Check apk add ran BEFORE the command. 3. Verify retry wrapper: _install_pkgs() { for _i in 1 2 3; do apk add --no-cache \"$@\" ... }. 4. If alpine repo timeout, resubmit."}
```

**Rule of thumb:** Build 15-20 train examples with ~60% positive (EXAMPLES.md) and ~40% negative/pitfall (TACTICAL.md). This gives the optimizer real gradients on both "do this" and "don't do this."

### `--auto light/medium/heavy` presets

MIPROv2 default settings are too slow for gemma-4-31b-it within a 600s foreground timeout. The `--auto` flag adds DSPy presets:
- `light` — 10 trials, 6 fewshot candidates, 3 instruction candidates, `minibatch=False`
- `medium` — 20 trials, 12 fewshot candidates, 6 instruction candidates
- `heavy` — 40 trials, 24 fewshot candidates, 12 instruction candidates

Recommended: start with `--auto light` for iteration, then scale up if the gradient is promising.

### Context Compaction Kills ALL Processes — delegate_task Is NOT Background

**CRITICAL OPERATIONAL REALITY:** When Hermes Agent context is compacted (typically after 200+ turns), ALL processes spawned within those turns are killed silently. This includes:
- Foreground `terminal()` commands
- `delegate_task` subagents (they live inside the same context, not independent OS processes)
- Any Python processes started via `execute_code`

**What does NOT survive compaction:**
- `delegate_task` workers — these are sub-conversations within the same agent context. They die when the parent context is compacted.
- `terminal(background=true)` processes started in a turn that gets compacted — the process IS a real OS process, BUT if the turn that started it is removed, the process may be orphaned or killed depending on the runtime.

**The ONLY reliable survival strategy:**
Start evolution as a **truly detached OS background process** using `terminal(background=true)` from a turn that is immediately saved/acknowledged, then **do not rely on the agent to monitor it** — check disk files instead.

**The Zombie State Problem:**
`evolve_skill_rotation.py` marks skills as `"running"` in `~/.hermes/skills/.wrappers/.rotation_state.json` when evolution starts. If the process dies (compaction, crash, SIGKILL), the state file is **never updated**. The skill remains "running" forever, blocking the rotation.

**Evidence from production:**
```
Skill                    Status       Prov     Last Evolved
s3-server-side-copy      running      nvidia   2026-04-23T20:27:02
honcho-cli               running      google   2026-04-23T20:33:19
```
Both had **zero running processes** (`ps aux | grep evolve` returned nothing). The `progress.jsonl` files showed `optimization_start` as the last phase with no subsequent entries. The state file was stale.

**How to detect zombies:**
```bash
# Check if any evolution processes are actually alive
ps aux | grep -E "evolve_skill_rotation|evolve_skill\.py" | grep -v grep

# Check progress file for stalled optimization (no entries after optimization_start)
tail ~/.hermes/skills/.wrappers/progress.jsonl
# If last entry is "optimization_start" and timestamp is >1h old, it's a zombie

# Check state file for stale "running" entries
cat ~/.hermes/skills/.wrappers/.rotation_state.json | jq '.skills | to_entries[] | select(.value.status=="running")'
```

**How to recover from zombies:**
```bash
# Reset zombie skills back to pending so rotation can continue
python3 << 'PYEOF'
import json
from pathlib import Path

state_file = Path("/Users/kieranlal/.hermes/skills/.wrappers/.rotation_state.json")
state = json.loads(state_file.read_text())

for name, data in state["skills"].items():
    if data.get("status") == "running":
        data["status"] = "pending"
        data["error"] = "Reset: was zombie (process died, state stale)"
        print(f"Reset {name} -> pending")

state_file.write_text(json.dumps(state, indent=2))
PYEOF
```

**One-liner zombie reset (no heredoc needed):**
```bash
python3 -c "import json, pathlib; p=pathlib.Path('/Users/kieranlal/.hermes/skills/.wrappers/.rotation_state.json'); d=json.loads(p.read_text()); [s.update({'status':'pending','error':'Zombie reset'}) for s in d['skills'].values() if s.get('status')=='running']; p.write_text(json.dumps(d,indent=2))"
```

**How to run evolution that survives:**
```bash
# Start as Hermes tracked background process from a SAVED turn,
# then verify via disk files, not process polling from the agent.
terminal(background=true) run:
  cd /Users/kieranlal/workspace/hermes-agent-self-evolution
  HERMES_AGENT_REPO=/Users/kieranlal/.hermes/hermes-agent \
  OPENAI_BASE_URL=http://localhost:8080/v1 \
  OPENAI_API_KEY=dummy \
  python3 /Users/kieranlal/workspace/nano2/scripts/evolve_skill_rotation.py \
    --auto light --eval-source synthetic > evolve.log 2>&1

# IMMEDIATELY after starting, verify the process exists OUTSIDE the agent:
# Run this in a fresh terminal() call (separate turn):
ps aux | grep evolve_skill_rotation | grep -v grep

# Poll for completion by checking files, NOT by holding agent context:
tail /Users/kieranlal/workspace/hermes-agent-self-evolution/evolve.log
ls ~/.hermes/skills/.wrappers/*.json  # wrapper appears when done
```

**Why cronjob is the ONLY safe automation:**
```bash
# Use Hermes cronjob — each run is a fresh session with no context accumulation
# This avoids compaction entirely because each invocation is independent
cronjob create --name skill-evolution-rotation \
  --schedule "0 */3 * * *" \
  --prompt "cd /Users/kieranlal/workspace/hermes-agent-self-evolution && \
    HERMES_AGENT_REPO=/Users/kieranlal/.hermes/hermes-agent \
    OPENAI_BASE_URL=http://localhost:8080/v1 \
    OPENAI_API_KEY=*** \
    python3 /Users/kieranlal/workspace/nano2/scripts/evolve_skill_rotation.py \
    --auto light --eval-source synthetic"
```

### Batch Loop Script: Continuous Evolution Until Complete (2026-04-24)

For evolving many skills (30+), a bash loop that calls the rotation script repeatedly is more efficient than cronjob alone. The loop exits when no pending skills remain.

**Script (`evolve_batch_loop.sh`):**
```bash
#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROTATION_SCRIPT="$SCRIPT_DIR/evolve_skill_rotation.py"
LOG_FILE="/Users/kieranlal/.hermes/skills/.wrappers/batch_loop.log"

export HERMES_AGENT_REPO="/Users/kieranlal/.hermes/hermes-agent"
export OPENAI_BASE_URL="http://localhost:8080/v1"
export OPENAI_API_KEY="***"
export PYTHONPATH="/Users/kieranlal/workspace/hermes-agent-self-evolution:$PYTHONPATH"

cd /Users/kieranlal/workspace/hermes-agent-self-evolution

echo "[batch started at $(date -Iseconds)]" >> "$LOG_FILE"

for i in $(seq 1 40); do
    # Check if any pending skills remain
    PENDING=$(python3 -c "
import json
from pathlib import Path
state = json.loads(Path('/Users/kieranlal/.hermes/skills/.wrappers/.rotation_state.json').read_text())
print(sum(1 for d in state['skills'].values() if d.get('status') == 'pending'))
" 2>/dev/null || echo "0")

    if [ "$PENDING" -eq 0 ]; then
        echo "[no pending skills, exiting]" >> "$LOG_FILE"
        break
    fi

    python3 "$ROTATION_SCRIPT" --auto light --eval-source synthetic \
        >> "$LOG_FILE" 2>&1 || true
    sleep 30
done
```

**Launch:**
```bash
chmod +x evolve_batch_loop.sh
# From Hermes Agent — use background=true so it survives context compaction
terminal(background=true) run: bash /path/to/evolve_batch_loop.sh
```

**Why this works:** Each iteration is a fresh Python process. If one skill hangs, the watchdog kills it and the loop continues. The loop polls the state file to detect completion.

### Backup Cronjob: Run Only If Idle

A cronjob that acts as a backup — only running when no other evolution is active — provides safety without collision:

```bash
# Runs every 30 min, but skips if another evolution process exists
cronjob create --name skill-evolution-backup \
  --schedule "*/30 * * * *" \
  --prompt "cd /Users/kieranlal/workspace/hermes-agent-self-evolution && \
    export HERMES_AGENT_REPO=/Users/kieranlal/.hermes/hermes-agent && \
    export OPENAI_BASE_URL=http://localhost:8080/v1 && \
    export OPENAI_API_KEY=*** && \
    RUNNING=\$(ps aux | grep -E 'evolve_skill_rotation|evolve_batch_loop' | grep -v grep | wc -l | tr -d ' ') && \
    if [ \"\$RUNNING\" -gt 0 ]; then echo '\$(date -Iseconds) Another evolution running, skipping'; exit 0; fi && \
    python3 /Users/kieranlal/workspace/nano2/scripts/evolve_skill_rotation.py \
      --auto light --eval-source synthetic >> /Users/kieranlal/.hermes/skills/.wrappers/cron.log 2>&1"
```

**Rule:** For any evolution expected to take >10 minutes, use **batch loop + idle-aware cronjob** — never `delegate_task`, never long foreground runs, and never trust a "running" state without verifying the OS process exists.

### Golden datasets must exercise skill mechanics

Hand-written eval datasets derived from actual skill content produce meaningful score variance (baseline 49.62 -> best 51.57). Generic synthetic examples produce flat scores (~0.65) because the fitness function (`0.3 + 0.7 * keyword_overlap`) cannot distinguish candidates when no skill-specific commands are invoked.

**Rule:** At least 50% of examples must test the skill's real mechanics:
- For `running-isaac-ros-tests`: `argo`, `k ladder`, `kubectl`, `ros2 launch`
- For `github-code-review`: `git diff`, `gh pr view`, `curl`, structured output sections
- For `building-rs-humble`: `docker buildx`, `buildah`, registry tags

**Minimum viable dataset:** 20 examples (10 train / 5 val / 5 holdout)
**Recommended:** 50+ examples with procedural task inputs

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

### Evolution runs but score never improves (flat 0.65 on every trial)

This is the most common silent failure. The pipeline completes, all constraints pass, but the holdout score is identical to baseline. The optimizer is not learning. Root cause is almost always a mismatch between what the dataset tests and what the skill actually does.

**How to diagnose:**

1. Check dataset size and split:
   ```bash
   wc -l datasets/skills/YOUR_SKILL/*.jsonl
   ```
   If total < 20, the optimizer has no variance to learn from. MIPROv2 needs at least 10+ validation examples to distinguish prompt candidates.

2. Check whether examples exercise the skill's mechanics:
   ```bash
   cat datasets/skills/YOUR_SKILL/val.jsonl | jq -r '.task_input'
   ```
   For a procedural skill like `github-code-review`, the task_input should reference actual skill procedures (`git diff`, `gh pr view`, `curl` API calls, structured output format). If the examples are generic natural language ("Review a PR that adds a function without docstring"), the model never invokes the skill's commands, and the fitness function cannot measure whether the skill instructions helped.

3. Check the fitness function behavior:
   The metric in `evolution/core/fitness.py` line 123-136 does keyword overlap between `expected_behavior` and `agent_output`:
   ```python
   expected_words = set(expected_lower.split())
   output_words = set(output_lower.split())
   overlap = len(expected_words & output_words) / len(expected_words)
   score = 0.3 + (0.7 * overlap)
   ```
   This means every candidate that mentions "docstring" and "missing" scores ~0.65. There is no gradient on whether the agent used `git diff`, followed the structured output format, or invoked the correct API endpoint.

**Real example from github-code-review:**
- Skill: 480 lines of procedural markdown with bash commands, gh CLI, curl API calls, structured review templates
- Dataset: 4 abstract examples ("Review a PR that changes API without updating tests" → "Should flag that tests need updating")
- Result: Every MIPROv2 trial scores 0.65. The optimizer is blind to the skill's actual content.

**Fix:** Generate or hand-write examples that exercise the skill's mechanics:
- For `github-code-review`: task inputs should be actual git diffs or PR numbers, expected behavior should check for specific command usage (`gh pr view`, `git diff`) and structured output sections
- For `building-rs-humble`: task inputs should reference Docker/BuildKit commands, expected behavior should check for correct `buildah` flags or registry tags
- For `managing-k3s-cluster`: task inputs should ask for specific `kubectl` operations

**Quick synthetic generation:**
```bash
python3 -m evolution.skills.evolve_skill \
    --skill github-code-review \
    --eval-source synthetic \
    --iterations 1
```
The synthetic builder reads the full skill text and generates cases. However, it may still produce abstract descriptions. Inspect the output in `datasets/skills/github-code-review/` and manually rewrite the most important examples to be procedural.

**Target numbers:**
- Minimum viable: 20 examples (10 train / 5 val / 5 holdout)
- Recommended: 100 examples (50 train / 25 val / 25 holdout)
- For procedural skills: at least 50% of examples should test specific commands/APIs from the skill

### Default models already patched

The repo's default models have been changed from `openai/gpt-4.1` to `openai/google-proxy/gemma-4-26b-a4b-it` for fast iteration (26b is more reliable than 31b — see Provider Outage section above). If you want NVIDIA models or OpenAI models, pass them explicitly:

```bash
# NVIDIA (slower, stronger reasoning):
--optimizer-model "openai/nvidia-proxy/meta/llama-3.1-405b-instruct" \
--eval-model "openai/nvidia-proxy/moonshotai/kimi-k2.5"

# OpenAI (requires your own key):
--optimizer-model "openai/gpt-4.1" --eval-model "openai/gpt-4.1-mini"
```

### Socket-Level Timeout Hardening Against I/O Hangs (2026-04-24)

**Problem:** Even with multiprocessing watchdogs, a blocked HTTP call can hang the subprocess until the hard `join(timeout)` fires — wasting 10+ minutes. DSPy uses `requests` which has no default read timeout. A model endpoint that accepts the TCP connection but never sends a response body will block forever.

**Solution:** Set aggressive socket and HTTP timeouts **before** importing DSPy:

```python
import socket
import requests

# Global socket timeout — kills connections that never respond
socket.setdefaulttimeout(45)

# Monkey-patch requests.Session to add connect/read timeouts
_original_request = requests.Session.request
def _patched_request(self, method, url, **kwargs):
    if "timeout" not in kwargs:
        kwargs["timeout"] = (30, 120)  # (connect_timeout, read_timeout)
    return _original_request(self, method, url, **kwargs)
requests.Session.request = _patched_request
```

**Why this order matters:**
- `socket.setdefaulttimeout` must be called **before** `urllib3` creates connection pools
- The `requests` patch must be applied **before** `dspy` imports and caches its LM client
- If DSPy is imported first, it creates `OpenAI` client instances that cache their own `httpx` sessions and ignore later patches

**Placement in rotation script:**
```python
# At the very top of evolve_skill_rotation.py, BEFORE any dspy import
import socket, requests
socket.setdefaulttimeout(45)
# ... requests patch ...

# ONLY NOW import dspy and evolution modules
import dspy
from evolution.skills.evolve_skill import evolve
```

**Tuning:**
| Parameter | Default | Behavior |
|-----------|---------|----------|
| `socket.setdefaulttimeout` | 45s | Hard ceiling on any socket operation |
| `requests` connect | 30s | Time to establish TCP + TLS |
| `requests` read | 120s | Time to receive full response body |

If a model typically responds in 5-10s but occasionally hangs, these timeouts convert an indefinite hang into a fast `requests.exceptions.ReadTimeout` that triggers the fallback loop within 2 minutes instead of 10.

---

### Phase-Aware Watchdog Grace Periods (2026-04-24)

**Problem:** A flat 5-minute stall timeout falsely kills healthy runs. MIPROv2's holdout evaluation phase can take 3-5 minutes with no progress log updates because it scores the full validation set sequentially. The default 90s grace killed runs that were making legitimate progress.

**Solution:** Vary the stall grace period by phase:

```python
def check_progress_health(progress_file: Path) -> tuple[bool, str]:
    if not progress_file.exists():
        return True, "no progress file yet"

    entries = [json.loads(line) for line in progress_file.read_text().strip().split("\n") if line.strip()]
    last_entry = entries[-1]
    last_phase = last_entry.get("phase", "unknown")
    last_ts = datetime.fromisoformat(last_entry["_ts"].replace("Z", "+00:00"))
    stalled = (datetime.now().astimezone() - last_ts).total_seconds()

    # Phase-aware grace periods
    if last_phase in ("evolved_constraints", "optimization_complete"):
        grace = 300  # 5 min — holdout evaluation is slow
    elif last_phase == "trial":
        grace = 180  # 3 min — trial scoring takes time
    else:
        grace = 90   # 1.5 min — bootstrapping should be fast

    if stalled > grace:
        return False, f"stalled {stalled:.0f}s at phase '{last_phase}' (grace={grace}s)"
    return True, f"last phase: {last_phase}"
```

**Phase definitions:**
| Phase | Typical Duration | Grace |
|-------|-----------------|-------|
| `start`, `skill_loaded`, `dataset_ready` | 10-60s | 90s |
| `optimization_start` | 30-120s | 90s |
| `trial` | 60-180s per trial | 180s |
| `evolved_constraints`, `optimization_complete` | 180-300s | 300s |

This eliminates false kills while still catching true hangs quickly in early phases.

---

### Size-Aware Timeouts: Flat Timeouts Kill Large Skills (2026-04-24)

**Problem:** A flat 90-second stall timeout falsely kills healthy runs on large skills. Minimax-m2.5 bootstrapped 4 traces for a 102KB skill in ~80s but was killed at 96s by the watchdog. The model was actively working; the timeout was too aggressive for the skill size.

**Root cause:** MIPROv2's synthetic dataset generation and bootstrapping scale linearly with skill text size. A 102KB skill sends the full skill text as context on every example generation call, while a 3KB skill completes in under 30s.

**Skill size distribution (real data, 44 skills):**
| Size Bucket | Count | Timeout Needed | Typical Duration |
|-------------|-------|---------------|------------------|
| <10KB | 21 | 90s | 30-60s |
| 10-50KB | 13 | 180-300s | 60-180s |
| >50KB | 10 | 300-600s | 180-300s |
| 102KB | 1 | 600-900s | 300-600s |

**Solution:** Make stall timeout proportional to skill size:
```python
def get_stall_timeout(skill_path: Path) -> int:
    size_kb = skill_path.stat().st_size / 1024
    if size_kb < 10:   return 90   # 1.5 min
    if size_kb < 50:   return 300  # 5 min
    if size_kb < 100:  return 600  # 10 min
    return 900                      # 15 min for 100KB+ monsters
```

**Additional insight:** Process small skills FIRST. The 21 `<10KB` skills complete in minutes and build wrapper momentum quickly. Save the 102KB monster for last when the pipeline is warm and proven.

**Why Minimax-m2.5 works for larger skills:**
- It **supports** `response_format` (structured JSON Schema) — unlike degraded Google
- It is the **fastest** NVIDIA model at inference (~5-10s per call)
- It does NOT hang on large context; it just needs more time
- Nemotron-3-super-120b is SLOWER than minimax for large skills; deepseek-v3.2 is comparable

**Order of processing:**
1. Sort skills by size ascending
2. Process all `<10KB` skills first (fast wins)
3. Then 10-50KB, then >50KB
4. The 102KB skill goes last with 900s timeout

---

### Size-Gated Model Selection: Different Models for Different Skill Sizes (2026-04-24)

**Problem:** Not all models handle all skill sizes equally. gemma-4-26b reached `evolved_constraints` (80%+ through MIPROv2) on a 21KB skill but was killed by timeout — it works, but is too slow for large skills. minimax-m2.5 bootstraps 4 traces for a 102KB skill in 80s but should be reserved for skills where its speed matters. Using the wrong model wastes tokens and time.

**Solution:** Gate model eligibility by skill size. Each model has `min_size_kb` and `max_size_kb` thresholds:

```python
MODELS = [
    # Tier 0: Premium large-context (placeholder until available)
    {
        "id": "deepseek-ai/deepseek-v4",
        "provider": "direct", "tier": 0,
        "max_size_kb": float("inf"), "min_size_kb": 100,
        "available": False,  # Enable when proxy adds it
        "note": "1M context, premium — for >100KB skills",
    },
    # Tier 1: Fast, proven reliable
    {
        "id": "openai/nvidia-proxy/minimaxai/minimax-m2.5",
        "provider": "nvidia", "tier": 1,
        "max_size_kb": 100, "min_size_kb": 0,
        "available": True,
        "note": "Fastest NVIDIA model, up to 100KB",
    },
    {
        "id": "deepseek-ai/deepseek-v3.2",
        "provider": "direct", "tier": 1,
        "max_size_kb": float("inf"), "min_size_kb": 0,
        "available": True,
        "note": "Excellent reasoning, any size",
    },
    {
        "id": "gemini-2.0-flash",
        "provider": "direct", "tier": 1,
        "max_size_kb": float("inf"), "min_size_kb": 0,
        "available": True,
        "note": "Direct Google API, bypasses degraded proxy",
    },
    # Tier 2: gemma-4 — proven capable but size-restricted
    {
        "id": "gemma-4-26b-a4b-it",
        "provider": "direct", "tier": 2,
        "max_size_kb": 10, "min_size_kb": 0,
        "available": True,
        "note": "Proven to evolved_constraints, ONLY <10KB",
    },
    {
        "id": "gemma-4-31b-it",
        "provider": "direct", "tier": 2,
        "max_size_kb": 10, "min_size_kb": 0,
        "available": True,
        "note": "gemma-4 variant, ONLY <10KB",
    },
    # Tier 2-3: Large-context fallbacks
    {
        "id": "openai/nvidia-proxy/nvidia/nemotron-3-super-120b-a12b",
        "provider": "nvidia", "tier": 2,
        "max_size_kb": 100, "min_size_kb": 0,
        "available": True,
        "note": "Strong but slower, up to 100KB",
    },
    {
        "id": "hermes-4-405b",
        "provider": "direct", "tier": 2,
        "max_size_kb": float("inf"), "min_size_kb": 0,
        "available": True,
        "note": "Massive model, any size",
    },
    {
        "id": "meta/llama-3.1-405b-instruct",
        "provider": "direct", "tier": 2,
        "max_size_kb": float("inf"), "min_size_kb": 0,
        "available": True,
        "note": "Huge context window, any size",
    },
    {
        "id": "openai/nvidia-proxy/moonshotai/kimi-k2.5",
        "provider": "nvidia", "tier": 3,
        "max_size_kb": 100, "min_size_kb": 0,
        "available": True,
        "note": "Good reasoning, up to 100KB",
    },
    {
        "id": "gemini-2.0-flash-lite",
        "provider": "direct", "tier": 3,
        "max_size_kb": float("inf"), "min_size_kb": 0,
        "available": True,
        "note": "Lightweight Gemini, any size",
    },
]

def filter_models_by_size(models: list[dict], size_kb: float) -> list[dict]:
    return [m for m in models
            if m.get("available", True)
            and m.get("min_size_kb", 0) <= size_kb <= m.get("max_size_kb", float("inf"))]
```

**Resulting eligibility by skill size:**
| Skill Size | Eligible Models | Excluded |
|-----------|----------------|----------|
| <10KB | **All 10 models** (including gemma-4) | None |
| 10-100KB | 8 models | gemma-4 (max 10KB) |
| >100KB | 5 models | gemma-4, minimax, nemotron, kimi |

**Why gemma-4 is restricted to <10KB:**
- **Proven capable:** Reached `evolved_constraints` on 21KB skill before timeout
- **Too slow:** ~32s per trace on 18KB skill; would need 600s+ for larger skills
- **Better fit:** Small skills where it completes within 180s; saves faster models for larger work

**Why minimax is restricted to <=100KB:**
- **Proven capable:** Bootstrapped 4 traces for 102KB skill in ~80s
- **Not optimal:** For >100KB skills, large-context models (deepseek, hermes, llama) are more reliable
- **Reserve capacity:** Keep minimax available for the many 10-100KB skills where speed matters

**Why deepseek-v4 is min 100KB:**
- **Placeholder:** Not yet available in proxy
- **When enabled:** Will be primary for >100KB skills (1M context, premium quality)
- **Cost-conscious:** Don't burn premium tokens on small skills that cheap models handle fine

**Dry-run verification example:**
```
Skill: coding-standards (1.0KB)
  Eligible models: 10 of 10 available
  → gemma-4, minimax, deepseek-v3.2, gemini-flash, etc.

Skill: s3-server-side-copy (12.6KB)
  Eligible models: 8 of 10 available
  🚫 Excluded: gemma-4-26b (max 10KB), gemma-4-31b (max 10KB)

Skill: managing-hermes-honcho-containers (103.4KB)
  Eligible models: 5 of 10 available
  🚫 Excluded: minimax (max 100KB), gemma-4 (max 10KB), nemotron (max 100KB), kimi (max 100KB)
  → deepseek-v3.2, gemini-flash, hermes-4-405b, llama-3.1-405b, gemini-flash-lite
```

**Integration with rotation script:**
1. Discover skill sizes at startup
2. Sort pending skills by size ascending (smallest first)
3. For each skill, filter models by `min_size_kb <= size <= max_size_kb`
4. Rotate through eligible models using `last_model_index`
5. On failure, fallback to next eligible model (cross-provider if needed)

---

### Google-Proxy Provider Degradation on Structured Output (2026-04-24)

**Critical finding:** Google-proxy is NOT failing from token exhaustion or rate limits. It is **degraded on structured output** — every request with `response_format` (JSON Schema) returns `503 BAD_REQUEST`.

**Observed from kilo-proxy health endpoint:**
```json
{
  "health": "healthy",
  "degraded": true,
  "degraded_reason": "BAD_REQUEST",
  "consecutive_failures": 3
}
```

**All 8 Google models fail identically:**
```
Both structured output format and JSON mode failed.
Please choose a model that supports `response_format` argument.
Original error: litellm.ServiceUnavailableError: ServiceUnavailableError:
OpenAIException - Error code: 503 - {'error': 'Provider google-proxy is degraded'}
```

**Implication:** Google-proxy currently cannot be used for DSPy MIPROv2 at all, regardless of model choice or token rotation. This is a **provider-level capability outage**, not a token issue.

**Workaround:** Use NVIDIA-proxy exclusively until Google-proxy recovers. The 5 NVIDIA tokens (minimax-m2.5, deepseek-v3.2, nemotron-3-super, llama-3.1-405b, kimi-k2.5) support structured output reliably.

**Verification command:**
```bash
# Test structured output support directly
curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/google-proxy/gemma-4-26b-a4b-it",
    "messages": [{"role": "user", "content": "Return JSON with key foo"}],
    "response_format": {"type": "json_schema", "json_schema": {"name": "test", "schema": {"type": "object", "properties": {"foo": {"type": "string"}}, "required": ["foo"]}}}
  }'
# Returns 503 BAD_REQUEST if google-proxy is degraded
```

**Models that reliably support `response_format` (verified 2026-04-24):**
| Model | Provider | Speed | Notes |
|-------|----------|-------|-------|
| `minimaxai/minimax-m2.5` | nvidia-proxy | Fastest | Best workhorse for batch |
| `deepseek-ai/deepseek-v3.2` | nvidia-proxy | Fast | Excellent reasoning |
| `meta/llama-3.1-405b-instruct` | nvidia-proxy | Slow | Strongest but expensive |
| `moonshotai/kimi-k2.5` | nvidia-proxy | Medium | Good for eval |
| `gemini-2.0-flash` | direct | Fast | Bypasses degraded google-proxy |
| `hermes-4-405b` | direct | Medium | Strong instruction following |

---

### Cross-Provider Fallback: Updated Reality (2026-04-24)

**Problem:** Restricting evolution to a single provider wastes half the token pool and increases blast radius when that provider has an outage.

**Updated reality:** Google-proxy is degraded on structured output. The cross-provider fallback now means NVIDIA-primary with Google-fallback, but Google will likely fail. The true resilience comes from **model rotation within NVIDIA** plus **direct-model fallback** (bypassing the proxy entirely for gemini/hermes).

**Provider assignment strategy (updated):**
```python
PROVIDER_MODELS = {
    "nvidia": [  # Primary: all 5 tokens support structured output
        "openai/nvidia-proxy/minimaxai/minimax-m2.5",
        "openai/nvidia-proxy/deepseek-ai/deepseek-v3.2",
        "openai/nvidia-proxy/nvidia/nemotron-3-super-120b-a12b",
        "openai/nvidia-proxy/meta/llama-3.1-405b-instruct",
        "openai/nvidia-proxy/moonshotai/kimi-k2.5",
        "openai/nvidia-proxy/qwen/qwen3.5-397b-a17b",
    ],
    "direct": [  # Fallback: bypass degraded proxy providers
        "gemini-2.0-flash",
        "hermes-4-405b",
    ],
    # "google": EXCLUDED — degraded on structured output (2026-04-24)
    # "cloudflare": EXCLUDED — never supported json_schema
}
```

**Fallback flow (updated):**
```python
# 1. Try all NVIDIA models (6 models across 5 tokens)
for model in PROVIDER_MODELS["nvidia"]:
    result = run_evolution_with_watchdog(..., model)
    if result["status"] == "done":
        break

# 2. If NVIDIA exhausted, try direct models (bypass proxy)
if result["status"] != "done":
    for model in PROVIDER_MODELS["direct"]:
        result = run_evolution_with_watchdog(..., model)
        if result["status"] == "done":
            break
```

**Key change from earlier:** Google-proxy models are no longer in the rotation. They will all fail on structured output. The fallback is direct models, not Google-proxy.

**Cloudflare is permanently excluded** — it does not support DSPy MIPROv2's `json_schema` structured output.

---

### Stale Progress File Pitfall (2026-04-24)

**Problem:** `progress.jsonl` from a previous failed run contains old `"phase": "start"` entries. The restart-loop detector counts these and immediately kills the new process on its first health check, before it has a chance to make any progress.

**Solution:** Unlink the progress file **before** every new attempt:

```python
output_dir = HERMES_REPO / "output" / skill_name
progress_file = output_dir / "progress.jsonl"
if progress_file.exists():
    progress_file.unlink()  # critical: prevents false restart-loop kills
```

**Also clear** any other stale artifacts that can poison health checks:
- `output_dir / "evolved_skill.md"` (from previous failed run)
- `output_dir / "metrics.json"` (stale scores)

**Why this matters:** A zombie reset (see "Context Compaction Kills ALL Processes" above) leaves `progress.jsonl` behind. Without clearing it, the next evolution attempt on that skill will be killed within 30 seconds by the restart-loop detector.

---

### Full 10-Token Batch Architecture Summary

For running evolution across 40+ skills with maximum resilience:

```
Batch Loop (bash, background=true)
  ├── For each skill:
  │    ├── Clear stale progress.jsonl
  │    ├── Pick primary provider (google/nvidia) by skill index parity
  │    ├── Try all models from primary provider
  │    │    ├── Run in multiprocessing.Process
  │    │    ├── Socket timeout 45s + requests (30,120)
  │    │    ├── Phase-aware watchdog (90s/180s/300s grace)
  │    │    └── If hung → kill, return status="hung", try next model
  │    ├── If primary exhausted → fallback to OTHER provider
  │    └── If all 14 models fail → mark failed, move on
  └── Sleep 15s, repeat until no pending skills remain
```

**Key parameters for 40-skill batch:**
| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `MODEL_ATTEMPT_TIMEOUT_SECONDS` | 600 | 10 min max per model attempt |
| `PROGRESS_STALL_SECONDS_DEFAULT` | 90 | Bootstrapping is fast |
| `PROGRESS_STALL_SECONDS_TRIAL` | 180 | Trial scoring needs time |
| `PROGRESS_STALL_SECONDS_LATE` | 300 | Holdout eval is slow |
| `MAX_RESTART_LOOPS` | 3 | Detect crash-restart cycles |
| `socket.setdefaulttimeout` | 45 | Hard socket ceiling |
| `requests timeout` | (30, 120) | TCP + read timeout |
| Provider assignment | `idx % 2` | Evenly distribute 10 tokens |
| Cross-provider fallback | Yes | Double model surface |
| Progress cleanup | `unlink()` before each attempt | Prevent false kills |

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
