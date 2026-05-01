# DIFF — Fork Changes vs Upstream

**Fork:** `explicitcontextualunderstanding/hermes-agent-self-evolution`  
**Upstream:** `NousResearch/hermes-agent-self-evolution`  
**Base commit:** `2e6fa0f`  
**Ahead by:** 21 commits  
**Net delta:** ~3,533 additions, ~76 deletions (excluding `output/` artifacts)

---

## 1. Performance: O(n³) → O(n) Elimination

### Problem
The GEPA optimization loop had an O(candidates × examples × iterations) pattern where `SkillModule.forward()` called the LLM on *every* evaluation — creating an O(n³) token explosion relative to any one dimension. A typical run: ~1,200 LLM calls × ~15K input tokens = ~18M tokens, exhausting all 12+ provider tokens sequentially.

### Fix — `evolution/skills/skill_module.py`

| Layer | Mechanism | Impact |
|-------|-----------|--------|
| **Short-circuit forward()** (default: ON) | `forward()` returns skill text directly as output — no LLM call during the evaluation inner loop. The metric scores the skill text via heuristic + optional LLMJudge without generating a full response. | Eliminates the n² inner loop entirely — O(n³)→O(n) |
| **Forward result cache** | Module-level `dict` keyed by `(skill_text_hash, task_input)`, max 500 entries. Cleared before each `compile()` and holdout. | Catches repeated evaluations (subsample → full eval reuse) |

Controlled via `--full-lm-eval` / `--short-circuit-eval` CLI flags with green ✓ / yellow ⚠ console indicators.

**Files:**
- `evolution/skills/skill_module.py` — `short_circuit` flag, cache logic, `clear_forward_cache()`
- `evolution/skills/evolve_skill.py` — CLI flag, console indicator

---

## 2. Reliability: Timeouts & Resilience

### Process-level `compile()` timeout — `evolution/skills/evolve_skill.py`
`_compile_with_timeout()` wraps `optimizer.compile()` in a `ThreadPoolExecutor` with a wall-clock timeout (120s per iteration×example×KB, min 600s, cap 7200s). Prevents zombie processes when all httpx timeouts fire sequentially.

### httpx timeout=60 on all `dspy.LM()` calls — `evolution/skills/evolve_skill.py`
Every `dspy.LM(...)` now passes `timeout=60`. Prevents silent retry loops inside DSPy/litellm.

### Socket default timeout — `evolution/skills/evolve_skill.py`
`socket.setdefaulttimeout(45)` + patched `requests.Session.request` with (30, 120) connect/read timeouts. Applied before any DSPy/litellm import so it covers the connection layer.

### ChainOfThought→Predict adapter — `evolution/core/dataset_builder.py`
Replaced `dspy.ChainOfThought` with `dspy.Predict` in `SyntheticDatasetBuilder` — avoids CoT's extra output tokens and latency on generation.

### Robust JSON parsing — `evolution/core/dataset_builder.py`
`_parse_test_cases()` with 5 fallback strategies: direct parse → regex extraction → syntax repair (trailing commas, control chars, single quotes) → individual object extraction → minimal fallback dataset. Prevents evolution from crashing on malformed LLM output.

### `dspy.LM` `base_url` — `evolution/core/fitness.py`
`LLMJudge.score()` now passes `base_url=os.environ.get("OPENAI_BASE_URL", None)` so the eval model respects proxy configuration.

---

## 3. Observability: LmCallTracker

### New file: `evolution/core/lm_tracker.py`
Singleton `LmCallTracker` instruments every LLM call site with structured logging:

| Call Site | File | Type |
|-----------|------|------|
| `skill_module.forward` | `skill_module.py` | n² evaluation inner loop (disabled in short-circuit mode) |
| `llm_judge.score` | `fitness.py` | Judge scoring calls |
| `reflection_lm` | `evolve_skill.py` (GEPA) | Candidate generation |
| `dataset_gen` | `dataset_builder.py` | Synthetic dataset generation |

**Output:** Every LM call emits a one-line stderr record `[LMTRACK] site | model | chars | iter/cand/ex | elapsed`. At run end, a `call_summary` shows totals by site. Also writes structured JSON to `progress.jsonl`.

**Progress binding:** `LM_TRACKER.bind_progress()` attaches a `ProgressLogger` so call events are persisted alongside evolution metrics.

---

## 4. Fitness Engine: Sub-Sampled Judge

### `evolution/core/fitness.py` — Major rewrite

**Heuristic gating:** `skill_fitness_metric()` now computes a fast keyword-overlap heuristic before deciding whether to call the expensive LLMJudge:

| Zone | Heuristic Score | Action |
|------|-----------------|--------|
| Clearly bad | < 0.4 | Return heuristic directly — no LLM call |
| Clearly good | > 0.7 | Return heuristic directly — no LLM call |
| Uncertainty zone | 0.4–0.7 | Sample at `_SUB_SAMPLE_RATE` (default 10%) |

**Deterministic sampling:** Within the uncertainty zone, sampling is hash-based on `(task, expected, agent_output)` — GEPA sees consistent scores across repeated calls for the same example/prediction pair.

**Interpolation:** Skipped judge calls estimate their score via inverse-distance-weighted interpolation from cached nearest neighbors in heuristic-score space.

**Judge cache:** `_JUDGE_CACHE` stores `(heuristic, llm_score)` pairs keyed by content hash. Cache hits avoid redundant LLMJudge calls entirely.

**Configuration:** `configure_sub_sampling(enabled, sample_rate, uncertainty_min, uncertainty_max)` — call once before `GEPA.compile()`.

### `skill_text` attachment — `evolution/core/dataset_builder.py` + `evolution/skills/evolve_skill.py`
`skill_text` is now attached to each `dspy.Example` so the metric function can evaluate tactical adherence (whether the agent's output follows the skill's procedure, not just produces correct content).

---

## 5. Dashboard: Evolution Hub Plugin

### Entirely new — `dashboard-plugin/`

A full Hermes dashboard plugin with three tiers:

**Tier 3 (generative):** p5.js canvas with k-means palette extraction from evolution run data — renders topology of candidate fitness as a dynamic generative header.

**Tier 2 (interactive):** Glowing status dots for each skill with hover detail modal showing wrapper JSON + git history + revert button.

**Tier 1 (layout):** Card notching, ambient glow, Kanban grid view with mock data mode for development.

**Structure:**
- `dashboard-plugin/evolution-hub/dashboard/dist/index.js` — 1,570-line IIFE, no build step
- `dashboard-plugin/evolution-hub/dashboard/dist/style.css` — CSS animations + manifest style field
- `dashboard-plugin/evolution-hub/dashboard/manifest.json` — plugin manifest
- `dashboard-plugin/evolution-hub/dashboard/plugin_api.py` — 637-line Python backend
- `dashboard-plugin/themes/evolutionary-ops.yaml` — theme config

**Key features:**
- Status cards (batch health, skill progress, last heartbeat)
- Batch Complete banner
- Queue table (all skills sorted by size, with status/model/error)
- Log viewer with ERROR filter and full download
- Skill detail panel with wrapper JSON + git history + revert
- Sidebar controls (Start / Stop / Reset with confirmation modals)
- p5.js generative canvas with k-means palette extraction
- Kanban grid view as third tab

---

## 6. Documentation

### `PLAN.md` — New "Risk: Token Multiplication (O(n³) Explosion)" section
Detailed documentation of the O(n³) problem including:
- The fundamental problem with call pattern diagram
- Why it overwhelms every provider (NVIDIA, Google, DeepSeek, Cloudflare, Nous) with token exhaustion analysis
- How to diagnose (LM call site table, log format, expected output)
- Current gaps (socket timeout doesn't reach httpx, no evolution-level timeout, split-brain model routing)
- Mitigation strategies table with impact/effort estimates

### `evolution-pipeline-before-after.html`
Standalone HTML + SVG visualization showing the O(n³) → O(n) transformation. Dark/light mode support. Interactive node hover states. Two-panel layout: "Before" with the pipeline call graph and "After" with the 5 mitigations applied.

---

## 7. Chores

### `.gitignore`
Added `output/` — evolution run artifacts (regenerated each run, shouldn't be tracked).

### `evolution/skills/skill_module.py` — Cleanup
Removed stale docstrings, clarified module-level comments.

|---

## 8. Course Correction: TACTICAL.md, EXAMPLES.md, and the Signal Quality Problem

### The Speed-Vs-Signal Trade-Off

The fork's four core optimizations (short-circuit forward, result cache, sub-sampled judge, process-level timeout) all optimize for **throughput** — getting more skills evaluated faster. Each one, however, reduces the **signal quality** that GEPA's reflection_lm receives:

| Optimization | Speed Gain | Signal Loss |
|-------------|-----------|-------------|
| **Short-circuit forward()** | Eliminates n² inner loop (O(n³)→O(n)) | No agent behavior trace — GEPA's reflection_lm gets raw skill text, not a demonstration of how the skill performs in practice |
| **Forward result cache** | Reuses past eval results | Repeated GEPA candidates get the same score as the first eval — no new information for reflection |
| **Sub-sampled judge** | 90% reduction in LLMJudge calls | Interpolated scores are approximations, not actual LLM evaluations — Pareto selection operates on estimates |
| **Process-level timeout** | Prevents zombie processes | Hard cutoff kills mid-iteration — partial results are discarded, wasted API calls |

The nano-pdf run is a concrete example: 20 trials planned, 0 completed, 0.0% improvement. The pipeline ran end-to-end (dataset gen → constraints → GEPA → evolved output) but the optimization produced nothing actionable because short-circuit mode stripped all signal.

### Why TACTICAL.md and EXAMPLES.md Change the Equation

Many Hermes skills are now multi-file, with separate concern layers:

```
skill/
├── SKILL.md          # Core procedure (directives, steps)
├── TACTICAL.md       # Experience, intuition, edge-case heuristics
└── EXAMPLES.md       # Eval rewards, accepted patterns, anti-patterns
```

**TACTICAL.md** encodes experiential knowledge — the hard-won lessons, subtle edge cases, and expert heuristics that distinguish a good skill from a great one. Under short-circuit mode, TACTICAL.md content is included in the skill text returned by forward(), but **the LLM never demonstrates tactical awareness** — it never shows whether it can apply those heuristics. GEPA's reflection_lm can't diagnose a "missed tactical nuance" because no agent behavior was generated to analyze.

**EXAMPLES.md** defines the reward surface — what constitutes good output, acceptable patterns, and anti-patterns to avoid. Under short-circuit mode, examples can't influence scoring because **the LLM never generates output to compare against the examples**. The heuristic (keyword overlap) ignores examples entirely. The LLMJudge, when called, compares skill text against expected behavior, not actual agent output against the examples.

### Proposed Course Correction

#### 8.1 Selective Full-LM Eval (Hybrid Mode)

For skills with TACTICAL.md or EXAMPLES.md, run a **two-phase** evaluation:

```
Phase 1 — Screening (short-circuit):
  Evaluate ALL candidates with short-circuit heuristic
  Filter to top-K candidates (e.g., K = 3)

Phase 2 — Validation (full-lm-eval):
  Run top-K candidates through the LLM for actual skill execution
  Score against TACTICAL.md heuristics + EXAMPLES.md reward criteria
  GEPA's reflection_lm gets real trace data for the next iteration
```

This preserves the O(n) throughput for screening while ensuring final candidates are validated against actual LLM behavior.

**Implementation sketch:**
```python
# In evolve_skill.py: new flag --hybrid-eval N
# Phase 1: short-circuit for initial selection
SkillModule.short_circuit = True  
optimizer.compile(...)  # fast screening → top K candidates

# Phase 2: full-lm-eval for depth
SkillModule.short_circuit = False  
optimizer.compile(..., seed_candidates=top_K)  
```

#### 8.2 TACTICAL-Aware Metric

Modify `skill_fitness_metric()` to weight TACTICAL.md adherence separately:

| Dimension | Weight (default) | Weight (with TACTICAL) | Source |
|-----------|-----------------|----------------------|--------|
| correctness | 0.5 | 0.3 | RLHF-style task completion |
| procedure_following | 0.3 | 0.4 | TACTICAL.md patterns |
| conciseness | 0.2 | 0.1 | Response efficiency |
| **tactical_adherence** | — | **0.2** | How well the agent applies experience/heuristics from TACTICAL.md |

The fourth dimension gives GEPA's Pareto selection a dedicated signal for tactical quality, preventing "correct but inexperienced" candidates from dominating the frontier.

**Implementation sketch:**
```python
# In fitness.py: new FitnessScore.tactical_adherence dimension
@dataclass  
class FitnessScore:
    correctness: float
    procedure_following: float
    tactical_adherence: float  # NEW — scored against TACTICAL.md content
    conciseness: float
    length_penalty: float
    feedback: str
    
    @property
    def composite(self) -> float:
        raw = (
            0.3 * self.correctness + 
            0.4 * self.procedure_following +  # bumped from 0.3
            0.2 * self.tactical_adherence +    # NEW
            0.1 * self.conciseness             # bumped down from 0.2
        )
        return max(0.0, raw - self.length_penalty)
```

#### 8.3 Example-Guided Evaluation Via EXAMPLES.md

Parse EXAMPLES.md into structured eval pairs — accepted patterns become `positive_examples`, anti-patterns become `negative_examples`:

```python
# In dataset_builder.py: parse EXAMPLES.md into eval data
# EXAMPLES.md format (convention):
#   ## ✅ Good: <pattern-name>
#   [example of correct behavior]
#   ## ❌ Bad: <pattern-name>  
#   [example of incorrect behavior]

class ExampleGuidedDatasetBuilder:
    """Builds eval dataset from EXAMPLES.md structured sections."""
    def parse_examples(self, skill_dir: Path) -> EvalDataset:
        # Extract ## ✅ and ## ❌ sections
        # Map to dspy.Example(task_input, expected_behavior, reward_sign)
        # reward_sign = +1 for good examples, -1 for anti-patterns
```

The reward signal `(+1 / -1)` feeds into both the heuristic (weighted keyword matching against accepted patterns) and the LLMJudge rubric (tell the judge to penalize anti-patterns).

#### 8.4 Size-Aware Iteration Budget

Massive skills (100KB+) need more GEPA iterations to explore their larger surface area, but each iteration costs more because the skill text is larger. Proposed adaptive budget:

| Skill Size | Iterations | Eval Examples | Phase 2 Candidates | Total API Cost |
|-----------|-----------|---------------|-------------------|----------------|
| < 10 KB | 3 | 3 | 1 | ~$0.10 |
| 10-50 KB | 5 | 5 | 2 | ~$0.50 |
| 50-100 KB | 8 | 8 | 3 | ~$1.50 |
| > 100 KB | 12 | 10 | 5 | ~$5.00 |

Already partially implemented via `--eval-examples` flag. Extend with:
- `--iterations auto` — scales based on skill size
- `--hybrid-eval <K>` — number of Phase 2 candidates for full-lm-eval

#### 8.5 MIPROv2 Fallback for Low-Signal Cases

When the short-circuit heuristic produces **0 trials completed** or **0.0% improvement**, automatically fall back to MIPROv2. MIPROv2's Bayesian optimization doesn't need execution traces — it optimizes instructions and few-shot examples by treating the prompt space as a hyperparameter search. This is better suited for skills where the evaluation signal is weak.

```python
# In evolve_skill.py: auto-fallback
if result.trials_completed == 0 or result.improvement < 0.01:
    logger.warning(f"GEPA produced no improvement — falling back to MIPROv2")
    mipro = dspy.MIPROv2(metric=metric, ...)
    result = mipro.compile(student=module, trainset=trainset, ...)
```

#### 8.6 Honcho-Guided Eval Data (Beyond Synthetic)

The current `--eval-source synthetic` generates dataset from the skill text alone. For skills with TACTICAL.md and EXAMPLES.md, the synthetic approach misses the most valuable signal — actual failures from production.

Using the Honcho session store (via `--eval-source sessiondb`), extract real-world failure cases where the agent misapplied the skill. These become high-value negative examples:

```python
# Query Honcho for sessions where the skill was invoked but failed
honcho_reasoning("Find sessions where skill 'composing-diagnostic-wrappers' was invoked and user corrected the agent")
# → Returns real-world traces with task_input, agent_output, user_feedback
# → Feed these as negative eval examples with reward_sign = -1
```

This turns the sub-sampled judge's weakest signal (interpolation on uncertain cases) into its strongest — actual production failures are the ground truth GEPA needs.

### Migration Path

| Priority | Change | Effort | Impact |
|----------|--------|--------|--------|
| P0 | Selective full-lm-eval (Section 8.1) | 1-2 days | Immediate quality gain for large skills |
| P0 | TACTICAL-aware metric (Section 8.2) | 1 day | Enables tactical adherence signal |
| P1 | Example-guided eval (Section 8.3) | 1 day | Structured reward from EXAMPLES.md |
| P1 | Size-aware budgets (Section 8.4) | 0.5 days | Prevents over/under-spending on iterations |
| P2 | MIPROv2 auto-fallback (Section 8.5) | 0.5 days | Graceful degradation on weak signal |
| P2 | Honcho-guided data (Section 8.6) | 2-3 days | Production-grade eval data |

**Immediate action:** Implement P0 items (hybrid mode + tactical dimension) to validate on a large skill (e.g., `composing-diagnostic-wrappers` at 100KB+ with TACTICAL.md and EXAMPLES.md). These two changes alone should produce measurable improvement where current runs show 0%.

---

## Commit Map

```
8a8754d  feat(dashboard): Evolution Hub plugin with p5 topology view
7690556  feat(dashboard): Grid/Kanban view as third tab
5e8cc49  feat(dashboard): mock data mode + Kanban grid view + symlink dev workflow
15f9ee9  Fix GEPA reflection LM, add synthetic dataset resilience, bump version
097b0a5  refactor(dashboard): CSS animations to dist/style.css via manifest style field
6df2879  feat(dashboard): card notching + ambient glow (Tier 1, Generative Cockpit)
e074d62  feat(dashboard): glowing status dots + skill detail modal (Tier 2)
3cb125f  feat(dashboard): p5.js generative canvas with k-means palette extraction (Tier 3)
3214e61  fix(dashboard): p5 canvas rendering in header-banner slot
0690e42  fix(dashboard): remove header-banner slot registration to isolate blank page bug
db15260  feat(dashboard): embed generative canvas directly in Evolution Hub tab (Tier 3 fix)
f835494  fix(step0): attach skill_text to dspy.Example so metric evaluates tactical adherence
246fb36  feat(fitness): sub-sampled judge with heuristic gating, cache, interpolation
51fbb13  fix(evolution): ChainOfThought→Predict, socket timeouts, dspy.LM base_url
f147034  LmCallTracker instrumentation + O(n³) token-multiplication documentation
4fd01a8  fix: reflection tracker char counting + mitigation: --eval-examples flag
efed32a  chore: gitignore output/ artifacts (validation run data)
690f4c8  fix: httpx timeout=60 on all dspy.LM() calls
3be7f77  fix: process-level timeout on optimizer.compile()
15ee3f0  fix: short-circuit forward() + forward result cache
e75937c  fix: disable DSPy parallelizer when short-circuit is active
|736901b  fix(otel): return EvaluationBatch object for GEPA 0.1.1 compatibility
|154d044  feat(otel): add OTelPromptAdapter with TDD (17/17 tests passing)
|a4b3b6c  fix(otel): restore redacted constant values in otel_adapter.py
|719398c  fix(otel): actionable feedback + clean response stripping for reflection LM
```

---

## 8. OTel-Backed Prompt Evolution (Plan 123, Phases 1-2)

### Problem
The heuristic rubric (clarity, resilience, self-containment) used by Plan 122's GEPA evolution had **zero correlation** with real backend outcomes. Prompts that scored +0.12 higher on the rubric produced identical backend behavior. The OTel pipeline existed (from Plan 122) but wasn't plugged into the evolution loop.

### Fix — `evolution/prompts/otel_adapter.py`

| Layer | Mechanism | Impact |
|-------|-----------|--------|
| **OTelPromptAdapter** | Evaluates prompts by running them through `hermes chat -q`, extracting session_id, then querying `otel_spans` in PostgreSQL for real timing data | Replaces heuristic LLM-as-judge with real backend pass/fail + timing |
| **Scoring formula** | `composite = pass(50%) + efficiency(20%) + tool_efficiency(20%) + token_efficiency(10%)` | Multi-dimensional score normalized to [0,1] |
| **EvaluationBatch return** | GEPA 0.1.1 requires `EvaluationBatch` dataclass, not raw tuple (breaking change from earlier GEPA versions) | Compatible with installed GEPA v0.1.1 |
| **Session ID correlation** | Uses `session_id` from `hermes chat -q` output (not trace_id injection) to correlate prompts to OTel traces | Avoids dependency on unimplemented trace_id injection feature |

### Empirical Validation (Phase 2 A/B)

30 live agent traces analyzed:
- **PASS** (27 traces): avg OTel composite **0.828** (range 0.680–0.939)
- **FAIL** (3 traces): avg OTel composite **0.126** (range 0.100–0.179)
- **Discrimination: +0.702** — OTel clearly separates working from broken prompts

### GEPA Integration Findings (Phase 3 Canary)

When running GEPA 0.1.1 with OTel-backed adapter:
- Each evaluation: ~15s (one `hermes chat -q` call + OTel query)
- GEPA's evaluation policy calls `evaluate()` ~3× per iteration (minibatch + validation)
- 5 iterations per prompt: ~4.5 min
- Extrapolated to 91 prompts: ~7h raw wall time

### Debugging the Reflection LM Pipeline

GEPA's `reflection_lm` was silently failing to propose text changes. Systematic debugging found 3 root causes:

| # | Problem | Finding | Fix |
|---|---------|---------|-----|
| 1 | **Pure numeric feedback** | Trajectory feedback was just numbers ("Score: 0.754. Pass: 1.0...") — reflection_lm couldn't act on it | Added `_make_feedback()` that emits qualitative diagnosis: "PROBLEM: Too many tool calls (3). Make prompt more directive to reduce retry loops." |
| 2 | **Banner noise in trajectories** | `full_assistant_response` contained Hermes banner/logo/tool listing — reflection_lm saw no actual response text | Rewrote `_strip_hermes_banner()` to correctly parse the actual output format (find `Query:` line, extract text between the `⚕ Hermes` header and the end-of-response separator, skip tool-call notifications and session footer) |
| 3 | **GEPA uses litellm internally** | `make_litellm_lm()` calls `litellm.completion()` directly, but kilo-proxy config is embedded in hermes binary — litellm can't route the model | Must pass `reflection_lm` as a callable wrapping `hermes chat -q` subprocess, not as a model name string |

**Result:** With the hermes-based reflection_lm callable, GEPA successfully generates improved prompt variants. On "Delete the container named test-container":
- **Original:** Basic delete instruction
- **Reflection LM proposed:** "Delete the container named test-container using a single delete_container call with force=true. Do not list containers first. Do not retry. Report the result directly. Use at most 1 tool call and --max-turns=1."
- GEPA **rejected it** (subsample score 1.31 vs 2.27), but the qualitative improvement is clear — the evolved text would reduce tool calls from 3 to 1.

### Files
- `evolution/prompts/otel_adapter.py` — 543 lines, created 2026-05-01
- `tests/test_otel_adapter.py` — 508 lines, 17/17 tests passing, created 2026-05-01
