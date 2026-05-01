# Heuristic vs Real Backend — Correlation Validation

Validated 2026-05-01 against compose-pkl MCP backend (Apple Container runtime).

## Results

| Prompt | Tool | Heuristic Score | Real Outcome | Gap |
|---|---|---|---|---|
| 7 (list_containers) | `list_containers` | 0.325 | ✅ Returned holdout-test container | Heuristic under-estimates |
| 9 (inspect_container) | `inspect_container` | 0.400 | ✅ Returned full config (status, memory, image, cpus) | Heuristic under-estimates |
| 87 (ingest_trace) | `ingest_trace` | 0.400 | ✅ Trace holdout-validation-001 ingested | Heuristic under-estimates |

## Finding

The heuristic rubric scores **prompt text quality** (clarity, coverage, resilience, self-containment, verifiability). It does NOT predict whether the backend tool will execute successfully — all 3 tested prompts worked despite low heuristic scores (0.325-0.400).

The heuristic is a **lower bound on prompt polish**, not a predictor of execution failure. Low-scoring prompts can execute correctly. Evolution should focus on improving coverage and resilience dimensions (currently 0.0 for all holdout prompts).

## Method

1. Created container `holdout-test` (alpine:latest, 2 CPUs, 256MB) via `create_container` MCP tool
2. Ran `list_containers` — returned 1 container, correct state
3. Ran `inspect_container(holdout-test)` — returned full config
4. Ran `ingest_trace(...)` — returned ingested=true
5. Deleted test container

## Real Hermes Pipeline — Holdout Set Results

All 3 prompts ran through `hermes_prompt_runner.py` (uses `hermes chat -q` → LLM → MCP tool call):
- **Binary**: `/Users/kieranlal/.hermes/hermes-agent/venv/bin/hermes`
- **Backend**: `compose-pkl/.build/debug/hermes-agent-backend` (native host process)
- **LLM**: DeepSeek V4 Flash via kilo-proxy on :8080

| # | Prompt | Heuristic | Real Result | Duration | Gap Analysis |
|---|---|---|---|---|---|
| 6 | delete_container | 0.325 | ✅ "Container test-dev doesn't exist" (correct — never created) | 13.7s | Heuristic under-estimates; prompt executed correctly |
| 7 | list_containers | 0.325 | ✅ "No containers exist" (correct — pool empty) | 14.7s | Heuristic under-estimates; LLM called list_containers correctly |
| 9 | inspect_container | 0.400 | ✅ "Container test-nginx does not exist" (correct) | 17.4s | Heuristic under-estimates; LLM also found test-nginx.pkl spec file |

**Finding**: All 3 prompts executed correctly against the real backend despite low heuristic scores (0.325-0.400). The heuristic is a reliable **lower bound on text quality** but NOT a predictor of backend execution success. Real execution adds ~14-17s per prompt for the LLM inference + tool call round trip.
