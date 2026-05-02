# Adversarial Review: Plan 123 — OTel-Backed Prompt Evolution

**Reviewer:** Hermes Subagent  
**Date:** 2026-05-01  
**Plan:** `/Users/kieranlal/workspace/isaac_ros_custom/.claude/plans/123-otel-backed-prompt-evolution.md`  
**Workspace:** `/Users/kieranlal/workspace/hermes-agent-self-evolution`

---

## Confirmed Issues (Must Fix Before Execution)

### 🔴 C-1: Trace ID Injection Is NOT Implemented (Fatal Gap)

**Finding:** The plan depends on OTel trace correlation to link `hermes chat -q` output to the correct `otel_spans` row. The referenced `otel-trace-injection-plan.md` is a **proposal document** — it describes Candidates C and D for injecting trace_id into MCP tool args, but **neither is implemented**. The `_query_latest_trace()` method in the plan's adapter code relies on querying the "most recent" trace, which is fragile.

**Why it's fatal:** Without trace_id injection, the adapter cannot reliably associate a prompt execution with its OTel trace when:
- Multiple concurrent sessions exist (another TUI session could produce traces between chat -q completion and query)
- Evaluation runs back-to-back (stale traces from previous prompt may be "most recent")
- The OTel bridge has ingestion delay (trace arrives after the query)

**Evidence:**
- `otel-trace-injection-plan.md` ends at line 310 with "Implementation Steps (Candidate C — first iteration)" — it's a plan, not a done change
- No `transform_tool_args` hook exists in `model_tools.py`
- No `_get_current_trace_id()` helper exists in `hermes_otel`
- The plan's own adapter code shows `_query_latest_trace()` with no trace_id parameter

**Diagnostic command (in Phase 0):**
```python
# Check if trace_id injection exists
grep -r "trace_id" ~/.hermes/hermes-agent/venv/lib/python*/site-packages/hermes_cli/model_tools.py
```

**Fix needed:** Either implement trace_id injection (Candidate C or D from the proposal) before Phase 1, or redesign the adapter to use a different correlation mechanism (e.g., session_id → trace_id mapping via `hermes_otel` plugin's internal span context).

---

### 🔴 C-2: GEPA Is NOT Installed — Pre-Flight Phase 0 Will Fail

**Finding:** `python3 -c "import gepa"` fails with `ModuleNotFoundError`. The plan lists `GEPA ≥ v0.0.27 installed` as a prerequisite with `pip install gepa`, but this hasn't been done. Phase 0 probe `python3 -c "import gepa; print(gepa.__version__)"` will fail.

**Impact:** Blocks Phase 1 adapter development (can't test against GEPA API), Phase 3 execution, and breaks the Phase 0 pre-flight check.

**Evidence:**
```
$ python3 -c "import gepa"
ModuleNotFoundError: No module named 'gepa'
```

**Fix:** Run `pip install gepa` in the active venv before starting Phase 0.

---

### 🔴 C-3: OTel Adapter Interface Mismatch with GEPA's Expected Return Type

**Finding:** The plan's `OTelPromptAdapter.evaluate()` returns a multi-dimensional dict score (`{"pass": ..., "efficiency": ..., "composite": ...}`). But GEPA's `DefaultAdapter.evaluate()` interface expects `objective_scores` as a list of dicts keyed by dimension name, and `scores` as a list of **floats** (single scalar per batch item). The existing `HeuristicPromptAdapter` returns:

```python
scores = [self.evaluator_fn(prompt_text) for _ in batch]  # list of floats
objective_scores = [{"rubric": s} for s in scores]         # list of {dim: float}
```

The plan's `_compute_score()` returns a dict `{"pass": ..., "efficiency": ..., "composite": ...}`. It's unclear whether GEPA expects `scores[i]` to be a dict or a float, and whether `objective_scores[i]` must match dimension names defined at init time.

**Impact:** Phase 1 adapter may break silently during Phase 3 GEPA evolution, wasting the 40-minute batch run.

**Fix:** Before Phase 1, test the exact return format GEPA expects:
```python
from gepa.adapters.default_adapter.default_adapter import EvaluationBatch
import inspect
print(inspect.signature(EvaluationBatch.__init__))
```
And build a 3-line stub adapter that tests the return format.

---

### 🔴 C-4: Cost Estimate Is Overly Optimistic — Real Time Likely 2-3x Higher

**Finding:** The plan's "~40 min wall time" estimate has compounding optimistic assumptions:

| Assumption | Reality Check |
|---|---|
| Each evaluation = 10s (chat -q) | A/B validation shows prompt #1 **hangs for 120s** even with --max-turns. Many prompts hit 60s timeout. Average per-prompt time is likely 20-40s, not 10s |
| Session reuse reduces latency by 23% | Session reuse across DIFFERENT prompts only helps if they share prefixes. These are 91 different prompts with different tool intents. Prefix caching benefits are marginal (~5-15%, not 23%) |
| Skip prompts with pass > 0.8 | Step 1 requires running OTel evaluation on ALL 91 prompts first (baseline). This is not included in the 40min estimate |
| 5 iterations = plateau | UNVERIFIED assumption. The plan acknowledges this but doesn't build in an early test |
| No overhead for GEPA reflection_lm | Each iteration also calls GEPA's reflection_lm (kilo-proxy → deepseek), which adds ~3-5s per iteration. This is NOT counted |

**Corrected estimate:** Baseline evaluation of all 91 prompts: ~30-60 min. Evolution of ~60 prompts (those with pass < 0.8) at 5 iterations each with reflection_lm overhead: ~2-3h. Total: **2.5-4h**, not 40min.

**Fix:** 
- Add OTel baseline evaluation as a separate Phase 1.5 step with its own estimate
- Account for reflection_lm call overhead (3-5s per iteration) in addition to chat -q time
- Add timeout-mitigated prompts to the pessimistic scenario
- Run the 3-prompt 15-iteration plateau test (mentioned in assumptions as UNVERIFIED) before Phase 3

---

### 🔴 C-5: No Trace-to-Prompt Correlation — `_query_latest_trace()` Is Unreliable

**Finding:** The plan's `_query_latest_trace()` is defined as:
```python
def _query_latest_trace(self):
    # Query otel_spans for the most recent agent span
    ...
```

Without trace_id injection (C-1), there is NO mechanism to identify WHICH trace corresponds to the prompt just evaluated. The adapter queries "most recent," but:
1. The OTel bridge may have a 1-3s ingestion delay — the trace may not exist yet
2. Other processes (TUI sessions, cron, other evaluations) may insert traces in between
3. The adapter adds "2s sleep" as mitigation, but this is fragile in shared environments

**Evidence from `container-link-query.py`:** The existing container_id JOIN approach uses output attribute parsing to link spans. The plan doesn't adopt this pattern.

**Fix:** Either (a) implement trace_id injection (C-1 fix), or (b) use a session-based approach: extract session_id from `hermes chat -q` output, use it to filter traces. The `hermes_prompt_runner.py` already extracts session IDs — piggyback on that.

---

## Recommended Changes (Should Fix)

### 🟡 R-1: Add Cleanup Between Evaluations (Plan Mentions But Doesn't Implement)

**Finding:** The plan's assumptions table says "Add cleanup prompt between evaluations" for the stale-container risk, but the adapter code in Phase 1 shows no cleanup logic. The `hermes_prompt_runner.py` already has a `cleanup_commands()` method and `--clean-between` flag — reuse this.

**Recommendation:** Add an optional `cleanup_prompt` parameter to `OTelPromptAdapter.__init__()` that runs a cleanup command between evaluations when enabled. Document that this adds ~5-10s per evaluation.

---

### 🟡 R-2: Missing Verification for Phase 3 Regression Against Plan 122 Evolved Texts

**Finding:** The plan states "Not evolving prompts from scratch — the Plan 122 evolved texts are the starting point." But Phase 3 re-runs GEPA evolution on the same 91 prompts using the new OTel evaluator. This may produce evolved texts that are WORSE than Plan 122's outputs on the heuristic rubric, or that regress on working prompts.

**Verification criteria** (line 257): `OTel score(evolved) ≥ OTel score(original) - 0.05` — this is a weak threshold. A prompt could drop 5% and still pass. The plan also doesn't verify that heuristic rubric scores don't drop catastrophically.

**Recommendation:** Add a "regression guard" step after Phase 3: run both heuristic rubric AND OTel evaluation on all evolved prompts. Flag any prompt where heuristic score drops >0.1 OR OTel score drops >0.05.

---

### 🟡 R-3: Phase 4 ReAct Scope Creep — Either Commit or Remove

**Finding:** Phase 4 is labeled "Stretch Goal" but:
1. It appears in the plan's deliverable list (line 194: "Deliverable: A dspy.ReAct-style module...")
2. It has verification criteria in the main table (line 258: "ReAct reduces tool calls...")
3. It has a rollback section (line 289)
4. The OTel pipepline is orthogonal to ReAct — this is a separate initiative

**Recommendation:** 
- Either promote Phase 4 to a committed phase with its own plan (Plan 124) and remove it from this plan entirely
- Or remove all ReAct references from this plan's deliverables, verification criteria, and rollback sections. Keep only as a "future direction" note.

The mixing of prompt evolution (text optimization) with agent architecture (ReAct reasoning patterns) dilutes focus. They test different hypotheses with different success criteria.

---

### 🟡 R-4: Plan 122 Found That Prompts That Hang CANNOT Be Fixed by Text Evolution

**Finding:** The `ab-validation-findings.md` (lines 27-31) explicitly states:

> *"The LLM flow is: read prompt → decide tool → call MCP tool → wait for response → process response. The prompt text only affects step 1. If the MCP tool hangs at step 3 (e.g., initfs pull from localhost), no prompt text can help."*

The conclusion is that infrastructure fixes (pre-pull images, tool-side timeouts) are needed for hanging prompts, not text evolution. The plan doesn't address this — it assumes OTel evaluation will magically fix hanging prompts by giving them lower scores. But the evolution (text mutation) STILL can't fix the hang.

**Recommendation:** Add explicit pre-filter to Phase 3: identify prompts whose failure mode is infrastructure-related (hang, timeout, tool not found) vs. prompt-quality-related (ambiguous instruction, missing context). Only evolve the latter. Document which failure modes are out of scope for text evolution.

---

### 🟡 R-5: A/B Validation Sample Size (6 Prompts) Is Too Small for Meaningful Discrimination

**Finding:** Phase 2 proposes A/B validation on 6 prompts (2 from each category). The pass criterion is "OTel score for a PASS prompt > OTel score for a FAIL prompt." With only 6 samples and binary categories, this is vulnerable to:
- A single outlier invalidating the test
- No statistical significance testing possible (n=3 per category minimum)
- The prompts are cherry-picked from known categories, not representative

**Recommendation:** Expand to at least 12-15 prompts (4-5 per category). Add a Mann-Whitney U test or bootstrapped confidence intervals for discrimination significance.

---

### 🟡 R-6: Phase 0 Missing Critical Pre-Flight Checks

**Finding:** Phase 0 probes check OTel bridge, compose-pkl spans, runner file, max-turns, Hermes binary, GEPA, and DB. Missing:

| Missing Probe | Why Critical |
|---|---|
| `trace_id injection active` | Without this, `_query_latest_trace()` returns wrong data |
| `pg8000 installed` | Adapter code uses `pg8000.native.Connection` |
| `PostgreSQL schema matches` | `otel_spans` table column names/types may differ from expected |
| `Reflection_lm proxy reachable` | GEPA evolution needs kilo-proxy for reflection_lm |
| `Concurrent session detection` | Another TUI session pollutes trace data |

**Recommendation:** Add these probes to Phase 0.

---

### 🟡 R-7: The Plan Has No Rollback for Phase 2 (A/B Validation)

**Finding:** Rollback section covers Phases 1, 3, and 4 but not Phase 2. Phase 2 doesn't modify files (it's an empirical measurement), but it may produce misleading results that cause bad decisions in Phase 3.

**Recommendation:** Add "Phase 2 rollback: revert to heuristic-only evaluation for Phase 3 decisions" — i.e., if A/B validation fails (OTel doesn't discriminate), Phase 3 should not proceed.

---

## Observations (No Change Needed)

### ⚪ O-1: Plan's Architecture Diagram Is Accurate
The "replace evaluator only" approach is sound — the GEPA pipeline, reflection_lm, dataset, and iteration logic are proven. Only the adapter function changes. This is correctly identified as lower risk.

### ⚪ O-2: `hermes_prompt_runner.py` Has `--max-turns` Support
Verified at line 221-223 of the runner: `--max-turns` flag is present and set to 10. This prerequisite is already met.

### ⚪ O-3: OTel Bridge Infrastructure Exists
The `otel-pg-bridge.py` script (307 lines) and `container-link-query.py` (299 lines) are implemented and deployed. The OTel pipeline itself is real — the missing piece is trace_id injection (C-1).

### ⚪ O-4: Session Reuse Already Works in Runner
The runner correctly manages `_SESSION_ID` and `_MAX_PROMPTS_PER_SESSION`. The prefix-caching optimization is real for sequential calls within a session.

### ⚪ O-5: Adapter's Composite Weighting Seems Reasonable
50% pass/fail + 20% duration + 20% tool efficiency + 10% token efficiency. This is a sensible default. The plan correctly identifies that the weighting may need tuning (BASE scenario).

### ⚪ O-6: Pre-Mortem Risk Table Is Thorough
The pre-mortem correctly identifies the key risk (GEPA adapter API compatibility) and the OTel trace visibility race condition. These align with confirmed issues C-1 and C-3.

---

## Summary

| Severity | Count | Key Items |
|----------|-------|-----------|
| 🔴 Confirmed (must fix) | 5 | Trace ID injection missing, GEPA not installed, adapter API mismatch, cost estimate 2-3x low, no trace-to-prompt correlation |
| 🟡 Recommended (should fix) | 7 | Cleanup gap, regression guard, ReAct scope creep, hanging prompts unfixable by text, small A/B sample, missing probes, Phase 2 rollback |
| ⚪ Observation (no change) | 6 | Architecture correct, max-turns exists, OTel bridge exists, session reuse works, weighting reasonable, pre-mortem thorough |

**Bottom line:** The plan's core insight (replace heuristic rubric with real backend signal) is correct, and its architecture (replace only the adapter) is sound. But it has **3 blocking preconditions** that are treated as done but actually aren't: trace_id injection, GEPA installation, and adapter API verification. The cost estimate should be revised to 2.5-4h. Phase 4 (ReAct) should be extracted into its own plan.
