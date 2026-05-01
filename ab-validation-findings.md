# Plan 122 — A/B Validation: Heuristic vs Real Backend

**Validated:** 2026-05-01T13:27:04Z

## Method

For 3 prompts, ran both the ORIGINAL (pre-evolution) and EVOLVED text through the real Hermes Agent pipeline (`hermes chat -q` → LLM → MCP tool call → compose-pkl backend).

## Results

| Prompt | Tool | Original | Evolved | Rubric Δ | Real Δ |
|--------|------|----------|---------|----------|--------|
| #1 (create_container) | create_container | ❌ HANG (120s) | ❌ HANG (120s) | +0.54 | **Same** |
| #6 (delete_container) | delete_container | ✅ PASS (14s) | ✅ PASS (14s) | +0.06 | **Same** |
| #45 (inspect_ghost) | inspect_container | ✅ PASS (12s) | ✅ PASS (13s) | +0.38 | **Same** |

## Critical Finding

**The rubric evolution improved prompt text quality but did NOT change any real backend outcomes.**

- Prompts that already passed (simple tools returning errors quickly) continued to pass — the evolution didn't break them.
- Prompts that hung (create_container with initfs pull stall) continued to hang — the evolution's timeout instructions don't help because the LLM can't abort a pending MCP tool call.
- The heuristic rubric scores text quality (clarity, resilience, self-containment) but these don't predict or change backend behavior.

## Why Text Evolution Alone Can't Fix Hangs

The LLM flow is: read prompt → decide tool → call MCP tool → wait for response → process response. The prompt text only affects step 1 (reading). If the MCP tool hangs at step 3 (e.g., initfs pull from localhost), no prompt text can help because:
1. The LLM doesn't know the call will hang until it makes it
2. There's no mechanism to timeout a MCP tool call mid-flight from the prompt
3. The backend has an infrastructure problem (no local initfs/criu images) that no prompt can fix

## What Would Actually Fix This

The plan's stated goals are correct but the mechanism (text evolution) is insufficient. Fixing the hangs requires:
1. **Infrastructure**: Pre-pull initfs/criu images so create_container doesn't stall
2. **Tool-side timeouts**: MCP tool handler should enforce its own timeouts
3. **LLM-side timeouts**: The hermes agent needs a tool-call timeout mechanism

## Updated Success Criteria

The evolution succeeded at improving text quality (clarity, self-containment) but cannot fix the fundamental HANG, NOT_IMPL, and infrastructure issues. Those require backend changes, not text evolution.
