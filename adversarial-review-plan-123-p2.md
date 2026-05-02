# Adversarial Review: Plan 123 v1.5.0 — P2.1 `prompt_validator.py` Spec

**Review Type:** Content/Deliverable — Phase 2 (proposed)
**Review Role:** Finder/Adversary/Referee
**Plan File:** `/Users/kieranlal/workspace/isaac_ros_custom/.claude/plans/123-otel-backed-prompt-evolution.md`
**Spec Location:** Lines ~442-600 (P2.1: `scripts/prompt_validator.py`)
**Reviewer Context:** Codebase inspected at `/Users/kieranlal/workspace/hermes-agent-self-evolution/` and `/Users/kieranlal/workspace/compose-pkl/`

---

## Phase 1 Reality Check (What's Actually Implemented vs Claimed)

Before reviewing P2.1, I verified the Phase 1 claims since P2 is contingent on P1:

| Claim | Status | Evidence |
|-------|--------|----------|
| "17/17 tests passing" | **CONFIRMED** | `unittest` run via `.venv` python3 shows all 17 pass in 0.025s |
| "evaluation cache" (P1.3) | **CONFIRMED** | `_evaluation_cache` dict with LRU eviction in `otel_adapter.py` |
| "graded scoring" (P1.4) | **CONFIRMED** | `_score_pass()` returns 1.0/0.5/0.0 based on tool outcomes |
| "make_hermes_lm()" (P1.6) | **CONFIRMED** | Factory function present, wraps `hermes chat -q` |
| "validated on real backend" | **PARTIALLY** | Unit tests are mocked. The `ab_otel_test.py` exists but no validation results file found (`ab-otel-validation-results.jsonl` missing) |
| GEPA v0.0.27 installed | **CONFIRMED** | In `.venv/lib/python3.14/site-packages` |
| GEPA `EvaluationBatch` importable | **CONFIRMED** | `from gepa.core.adapter import EvaluationBatch` works |

---

## Confirmed Gaps (Priority Order)

### GAP 1 [CRITICAL]: `extract_tool_references()` Is Undefined — Hardest Part Missing

**Finding:** The spec references `extract_tool_references(prompt_text)` extensively (lines 495-497) but provides **zero pseudocode, zero regex patterns, zero logic**. This is where the spec fails the "complete enough to implement" test.

**Why it's critical:** Extracting tool names from natural language prompts is a fundamentally hard NLP problem. The inventory in `inventory.py` already reveals that prompts look like:
- *"List all running containers"* — zero tool name references
- *"Create a container named test-container with 2 CPUs and 1024MB memory based on ubuntu:22.04"* — zero tool name references  
- *"Delete the container I just created"* — zero tool name references
- *"call mcp_hermes_agent_backend_list_containers() with filter='running'"* — explicit tool reference (rare)

In `PROMPT_TOOLS` (inventory.py, lines 85-163), tool names like `"list_containers"` and `"delete_container"` were assigned **manually** by reading prompt texts — not through automated extraction.

**What's missing from the spec:**
- No regex or parsing strategy for `mcp_hermes_agent_backend_*` extraction
- No strategy for shorthand matching (e.g., does "list containers" match `list_containers`?)
- No handling for prompts with zero tool references (the most common case)
- No fuzzy matching for near-matches (e.g., "create a container" → `create_container` and/or `mcp_hermes_agent_backend_create_container`)
- No handling for parameter extraction from natural language (e.g., "container named test" → `id="test"`)

**Without this function, the entire validator returns `score=1.0` for every prompt written in natural language, making it useless for the vast majority of the 91 prompts.**

**Fix:** The spec MUST include at minimum:
1. Regex patterns for matching `mcp_hermes_agent_backend_*` calls
2. A shorthand-to-tool-name mapping (e.g., `"list containers"` → `list_containers`)
3. A "NO_TOOLS_FOUND" scoring case (returns 0.7 or 0.8, not 1.0)
4. Signal that prompts without explicit tool references cannot be fully validated by deterministic means

---

### GAP 2 [HIGH]: Cross-Repo Import Architecture Not Addressed

**Finding:** The plan places `prompt_validator.py` in `compose-pkl/scripts/` but the primary integration point is `otel_adapter.py` in `hermes-agent-self-evolution/evolution/prompts/`. These are **different repositories** with different virtual environments.

**Evidence from codebase:**
- OTel adapter lives at: `~/workspace/hermes-agent-self-evolution/evolution/prompts/otel_adapter.py`
- The `.venv` is at `~/workspace/hermes-agent-self-evolution/.venv/`
- Compose-pkl scripts live at: `~/workspace/compose-pkl/scripts/`
- Compose-pkl uses `uv.lock` for dependency management (not pip)

**The plan says:**
> "In the adapter, the validation result is included..."

But **how** does `otel_adapter.py` import `compose-pkl/scripts/prompt_validator.py`? The options are:
- **PYTHONPATH hack** — fragile, breaks in CI
- **Duplicate the file** — violates DRY
- **Shared package** — not addressed in plan
- **Subprocess call** — kills the "0.1s" latency claim

**Fix:** Specify the import mechanism. The simplest approach: place `prompt_validator.py` inside the self-evolution repo (e.g., `evolution/prompts/prompt_validator.py`), and the regeneration script in compose-pkl. Or specify that the adapter calls the validator via `subprocess.run(["python3", "prompt_validator.py", prompt_text])` with latency accounting.

---

### GAP 3 [HIGH]: The "No Tool Reference" Edge Case Breaks the Validator for Most Prompts

**Finding:** As noted in GAP 1, the validator's scoring logic (lines 524-533) has:
```python
if not errors:
    score = 1.0  # No tools found → score = 1.0
```

For the 91 prompts in the inventory, the majority are written in natural language with no explicit tool name references. For these prompts, `extract_tool_references()` returns an empty list, `errors` is empty, and the score is `1.0` — meaning the validator finds them "perfect." This is **false feedback** for the reflection LM.

**What makes this worse:** The reflection LM will see "TOOL_ERRORS: none" and think the prompt is valid, when in fact the validator simply couldn't find any tools to check.

**Fix:** Add a dedicated scoring case for "no tools found" that returns a middle-range score (e.g., 0.7) with a clear `summary` message: "NO_TOOLS_FOUND: Prompt does not explicitly reference any MCP tool. Consider adding tool names for clarity."

---

### GAP 4 [MEDIUM]: `type_check()` and `summarize_errors()` Are Unspecified

**Finding:** The spec references two helper functions without any implementation detail:
- `type_check(value, expected)` (line 517) — no specification of what types are supported, how dict/list types are validated, or error behavior
- `summarize_errors(errors)` (lines 540, 559) — no format specification, no truncation strategy, no error grouping

**Fix:** Add type-checking rules:
- `"string"` → `isinstance(value, str)`
- `"int"` → `isinstance(value, int)` (or numeric string)
- `"bool"` → `isinstance(value, bool)`
- `"list[str]"` → `isinstance(value, list) and all(isinstance(v, str) for v in value)`
- `"dict"` → `isinstance(value, dict)`
- `"list[dict]"` → `isinstance(value, list) and all(isinstance(v, dict) for v in value)`

Add `summarize_errors()` format: `"N error(s): [UNKNOWN_TOOL: 'mcp_fake_tool' not found, MISSING_PARAM: 'create_container' requires 'id']"`

---

### GAP 5 [MEDIUM]: Scoring Rubric Lacks Justification and Multi-Error Handling

**Finding:** The scoring rubric assigns:
- 1.0: no errors
- 0.7: type mismatch
- 0.5: missing params  
- 0.3: unknown/hallucinated tool
- 0.8: "else" (catch-all)

**Issues:**
1. **No justification** for these specific values. Why not 0.6/0.4/0.2?
2. **Multi-error discounting:** If a prompt has a hallucinated tool AND missing params AND a type mismatch, it's scored at 0.3 (just the hallucinated tool penalty). The other errors don't affect the score.
3. **The "else" clause is suspicious:** Line 532-533: `score = 0.8` — what errors could lead here? No specification.

**Fix:** Use a multiplicative penalty model:
- Start at 1.0
- Deduct 0.3 for unknown/hallucinated tools
- Deduct 0.1 for each missing required param
- Deduct 0.05 for each type mismatch
- Floor at 0.0

This gives: `1.0 - 0.3*(unknown_tools) - 0.1*(missing_params) - 0.05*(type_mismatches)`

---

### GAP 6 [MEDIUM]: Tool Manifest Completeness Not Addressed

**Finding:** The spec shows 3 tools in the manifest but claims "... all 50 MCP tools" (line 479). Looking at the actual available tools from the Hermes MCP server:

From `mcp_hermes_agent_backend_*` tool list (available in this context), there are ~46 tool functions. Each has between 2 and 12 parameters with specific types.

**What the spec is missing:**
- No mention of how the 46+ tool schemas will be acquired (what MCP endpoint? What client library?)
- No mention of parameter constraints beyond types (e.g., `id` has length constraints: "1-64 chars, alphanumeric/hyphen/underscore")
- No handling for nested parameter types (e.g., `sockets: list[dict]` where each dict has specific keys)
- The `update_tool_manifest.py` is said to query "the MCP endpoint for the current tool schema" — but which endpoint? Using what protocol? The `hermes-agent-backend` MCP server exposes tool definitions programmatically via `tools/list`, but this isn't documented.

**Fix:** Add specification for:
1. Which MCP endpoint provides tool schemas
2. How to map from MCP JSON Schema to the Python manifest format
3. Include parameter constraints (min/max length, enum values) in the manifest

---

### GAP 7 [LOW]: Static vs Dynamic Manifest — Recommendation

**Finding:** The plan commits to a static Python dict + regeneration script. A dynamic approach (querying MCP endpoint at runtime) would avoid drift.

**Analysis:**

| Criterion | Static Dict | Dynamic Query | Hybrid (Cached) |
|-----------|-------------|---------------|-----------------|
| Latency | ~0ms | ~100-200ms | ~0ms (warm) |
| Always current | No (needs regen) | Yes | Yes (with TTL) |
| Offline-capable | Yes | No | Yes (with fallback) |
| Complexity | Low | Medium | Medium |
| Regeneration burden | Manual/CI trigger | None | None |

**Recommendation:** Use a **hybrid approach**:
- `prompt_validator.py` tries to query the MCP endpoint for tool schemas at import time
- Caches the result in an in-memory dict (TTL: 5 minutes)
- Falls back to the static manifest if the MCP endpoint is unreachable
- The static manifest serves as the "last known good" fallback
- The regeneration script exists but is only needed for truly offline scenarios

This removes the drift problem while maintaining the "0.1s fast-fail" latency (first call pays the ~200ms query, subsequent calls are instant).

---

## Dismissed Claims

| Plan Claim | Assessment | Reasoning |
|------------|-----------|-----------|
| "The tool manifest regeneration script queries the MCP endpoint for the current tool schema" | **Incomplete** — not implementable as specified | Which MCP endpoint? Protocol? Client? Schema format? The spec is too vague to implement. |
| "The validator runs in <1s" | **Plausible but unverified** | Regex-based parsing is fast, but manifest import and init time aren't accounted. With the hybrid dynamic approach, first-call latency could be ~200ms. |
| "Integration point: Adapter evaluate() — called before hermes execution" | **Missing cross-repo detail** | No mechanism specified for how the self-evolution adapter imports from compose-pkl scripts. This is not false, just unspecified. |
| "File structure: compose-pkl/scripts/ is appropriate" | **Disputed** | The validator's primary consumer is the self-evolution repo's adapter. Placing the code in compose-pkl creates an awkward cross-repo dependency. Better to place the validator logic in the self-evolution repo and the regeneration bootstrap in compose-pkl. |
| "Scoring rubric is reasonable" | **Partially** | The hierarchical ordering (unknown > missing > type) is correct. The specific values (1.0/0.7/0.5/0.3) are arbitrary. The missing multi-error handling and "no tools found" case are genuine gaps. |
| "GEPA adapter API verified" (prerequisite) | **CONFIRMED** | `EvaluationBatch` is importable from `gepa.core.adapter`. The adapter code creates it correctly. |
| "Session-based trace correlation works" | **Unverified — Phase 0 probe result unknown** | The `_extract_session_id()` regex `hermes --resume (\\S+)` looks correct for the described output format, but I cannot verify because `hermes chat -q` output format was not tested in this session. |

---

## Specific Changes Needed Before Phase 2 Implementation

### Must-Fix (Blockers)

1. **Define `extract_tool_references()` in full** — Include regex patterns for `mcp_hermes_agent_backend_*`, a shorthand mapping dict (e.g., `"list containers"` -> `list_containers`), and a "no tools found" sentinel.

2. **Add cross-repo import mechanism** — Chose ONE:
   - Option A: Move `prompt_validator.py` to `~/workspace/hermes-agent-self-evolution/evolution/prompts/`
   - Option B: Specify subprocess call: `subprocess.run(["python3", "../compose-pkl/scripts/prompt_validator.py", prompt_text])`, document the ~0.3s latency
   - Option C: Create a shared package that both repos can import

3. **Add "NO_TOOLS_FOUND" scoring case** — When `tools_found` is empty, return `score=0.7` with `summary="NO_TOOLS_REFERENCED: Prompt does not explicitly name any MCP tool. Add tool names for deterministic validation."`

4. **Define `type_check()` and `summarize_errors()`** — As specified in GAP 4 above. Without these, the validation logic has unreachable code paths.

### Should-Fix (Strongly Recommended)

5. **Switch to hybrid static/dynamic manifest** — Query MCP endpoint at import time, cache for 5min, fall back to static manifest. Remove the manual regeneration burden.

6. **Fix multi-error scoring** — Use multiplicative penalty model instead of first-match-wins:
   ```python
   score = 1.0
   score -= 0.3 * any("UNKNOWN_TOOL" in e for e in errors)
   score -= 0.1 * sum(1 for e in errors if "MISSING_PARAM" in e)
   score -= 0.05 * sum(1 for e in errors if "TYPE_MISMATCH" in e)
   score = max(0.0, score)
   ```

7. **Specify the MCP schema query endpoint** — Document the exact `tools/list` call against the MCP server, including how to map from JSON Schema format to the Python manifest.

8. **Document parameter constraints** — Extend manifest entries to include value constraints (min/max length for string params, valid keys for dict params, protocol format for port params).

### Nice-to-Have

9. **Add parameter value extraction** — For prompts that specify values in natural language (e.g., "2 CPUs" → cpus=2), include a basic extraction pass in `extract_tool_references()`. This dramatically increases validator usefulness.

10. **Add component name to manifest** — Many MCP tools share a namespace prefix (`mcp_hermes_agent_backend_`). Include the short name (`create_container`, `list_containers`) alongside the full name for the shorthand matching.

---

## Summary

| # | Gap | Severity | Impact | Fix Effort |
|---|-----|----------|--------|------------|
| 1 | `extract_tool_references()` undefined | CRITICAL | Validator non-functional for natural-language prompts | ~2h (regex + mapping table) |
| 2 | Cross-repo import not addressed | HIGH | Integration doesn't work as specified | ~0.5h (choose option + document) |
| 3 | No-tool-reference → false 1.0 score | HIGH | Reflection LM gets misleading feedback | ~0.5h (add scoring case) |
| 4 | `type_check()`/`summarize_errors()` undefined | MEDIUM | Unreachable code paths in validation loop | ~0.5h (implement helpers) |
| 5 | Scoring rubric lacks justification + multi-error | MEDIUM | Suboptimal feedback granularity | ~0.5h (revise scoring formula) |
| 6 | Tool manifest completeness unspecified | MEDIUM | ~46 tools need to be acquired from unknown endpoint | ~1h (document MCP query) |
| 7 | Static manifest drift risk | LOW | Regeneration script must be run manually | ~1h (convert to hybrid) |

**Total fix effort:** ~5-7h for all gaps, or ~3.5h for must-fix minimum.

**Bottom line:** The P2.1 spec is **not complete enough to implement** in its current form. Gaps 1-4 must be resolved before a developer can produce a working `prompt_validator.py`. The architecture (static manifest + integration points) is sound in concept but missing critical implementation details, especially around tool name extraction from natural language prompts and cross-repo dependency management.
