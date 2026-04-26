# PLAN 116 — FINDER ADVERSARIAL REVIEW REPORT

**Date**: 2026-04-26  
**Scope**: Migrate kilo-proxy.py → litellm proxy (Phase 1), evolve_skill_rotation.py/evolve_batch_size_aware.sh → Temporal/Prefect (Phase 2), cleanup (Phase 3)  
**Files analyzed**: kilo-proxy.py (1509 lines), evolve_skill_rotation.py (1043 lines), evolve_batch_size_aware.sh (182 lines), kilo-proxy-watchdog.sh, restart-kilo-proxy.sh, test_kilo_proxy_routing.py, config.py, kilo.mac.jsonc  
**Total findings**: 26 (4 Critical, 9 High, 8 Medium, 5 Low)

---

## 🔴 CRITICAL FINDINGS (Blocks execution / Catastrophic risk)

### C-01: Open Proxy — Binds to 0.0.0.0:8080 with Zero Authentication
**File**: kilo-proxy.py, lines 1505-1507  
```
server = ThreadedHTTPServer(("0.0.0.0", PORT), KiloProxy)
```
**Issue**: The proxy binds to ALL network interfaces (`0.0.0.0`) on port 8080 with **no authentication whatsoever**. Any machine on the local network (LAN, WiFi, VPN) can send requests through this proxy and consume LLM API tokens from all 9 providers (NVIDIA, DeepSeek, Anthropic, Google, Cloudflare, Nous, OpenRouter, OpenCode, Kilo). There is no IP allowlist, no API key check, no rate limiting per client.

**Why it matters for the plan**: This is an active security vulnerability that exists NOW and must be fixed BEFORE migration begins. An attacker on the same subnet could drain all token balances. The migration plan doesn't mention fixing this — it assumes the problem is solely about code quality.

**Plan step that fails**: The plan has no security hardening step. Without addressing this, migrating to litellm just moves an open proxy to a new codebase.

### C-02: Duplicate `deepseek-proxy` Entry — Second Definition Silently Overwrites First
**File**: kilo-proxy.py, lines 77-90 AND lines 261-280  
```python
# First definition (line 77-90):
"deepseek-proxy": { "token_env_vars": ["DEEPSEEK_API_KEY"], ... }

# Second definition (line 261-280):
"deepseek-proxy": { "token_env_vars": ["LLM_DEEPSEEK_API_KEY"], ... }
```
**Issue**: The `PROVIDERS` dict has `"deepseek-proxy"` defined **twice**. Python dict semantics mean the second definition silently replaces the first. The **first definition is entirely dead code**. Key difference: first had `token_env_vars: ["DEEPSEEK_API_KEY"]`, second has `["LLM_DEEPSEEK_API_KEY"]`. If any client or deployment relies on `DEEPSEEK_API_KEY` env var, it will silently fail to find tokens.

**Why it matters for the plan**: If you migrate to litellm's YAML config and reference `DEEPSEEK_API_KEY` (the first definition), it won't work because the live code uses `LLM_DEEPSEEK_API_KEY`. This is a latent config drift bug that the migration will inherit or break in new ways.

**Plan step that fails**: Phase 1 YAML config generation — which env var name do you use? The plan doesn't specify, and this ambiguity will cause token loading failures.

### C-03: Race Conditions in All Shared Mutable State — No Thread Locks
**File**: kilo-proxy.py, multiple locations — `_token_cooldowns` (line 369), `_exhausted_models` (line 401), `_slow_models` (line 404), `_provider_failures` (line 455), `_provider_degraded` (line 456), `_provider_degrade_reason` (line 457), `_provider_last_failure` (line 458), `_upstream_probe_cache` (line 561)

**Issue**: All these module-level dictionaries are accessed and mutated from multiple `ThreadingMixIn` handler threads **without any locks** (except `TokenStore._lock` which only protects the token index). The thread-safety fix described in the plan (shared requests.Session) is only HALF the problem. Consider:
- Two concurrent requests exhaust the same model simultaneously — `_exhausted_models[key] = time.monotonic()` races
- `is_provider_degraded()` checks and mutates `_provider_degraded` without synchronization
- `_jittered_backoff` uses `random.random()` which is NOT thread-safe

**Why it matters for the plan**: The plan states "Thread-safety bug: shared requests.Session" as a known issue, but the actual session fix (thread-local sessions via `_thread_local`) works only for sessions. The remaining race conditions affect circuit breaker logic, token cooldowns, and model exhaustion — directly causing:
- Token rotation corruption (two threads rotating the same token simultaneously)
- False circuit breaker trips from concurrent failures that shouldn't cascade
- Lost recovery events

**Plan step that fails**: Phase 1 litellm migration assumes the only thread-safety issue was the shared session. It will replace the code but inherit the architecture problems if the YAML config + litellm doesn't replicate the custom token rotation logic differently.

### C-04: File Descriptor Leak — `open()` Without Context Manager
**File**: kilo-proxy.py, line 680  
```python
val = open(path).read().strip()
```
**Issue**: Every token file read opens a file handle without closing it. For 9 providers with 1-5 token files each (~25 files), every token reload leaks 25 file descriptors. On macOS, default ulimit is 256 file descriptors. With concurrent requests triggering reloads, this leaks rapidly.

**Why it matters for the plan**: The plan doesn't address existing reliability issues. If the proxy crashes from FD exhaustion during migration testing, the migration will be blamed on the new system rather than the pre-existing bug being fixed.

**Plan step that fails**: Phase 1 testing — flaky failures from FD exhaustion will be incorrectly attributed to litellm configuration problems.

---

## 🟠 HIGH FINDINGS (Significant rework needed)

### H-01: Model ID Prefix Incompatibility with litellm — "openai/" Hack Won't Survive
**File**: evolve_skill_rotation.py, lines 84, 116, 126, 136, 464  
```python
"id": "openai/deepseek-proxy/deepseek-v4-flash",
"id": "openai/nvidia-proxy/nvidia/nemotron-3-super-120b-a12b",
```
**File**: kilo-proxy.py, lines 822-827  
```python
if model.startswith("openai/"):
    model = model[len("openai/"):]
```

**Issue**: The `"openai/"` prefix is a DSPy/litellm compatibility shim baked into model IDs at EVERY layer:
1. `evolve_skill_rotation.py` stores model IDs with `"openai/"` prefix in its model list
2. `kilo-proxy.py` strips this prefix and maps the remainder to a provider
3. The `rotation_state.json` persistence records these prefixed model IDs
4. The `MODEL_STATS` reliability ladder uses these prefixed IDs

With litellm proxy, the model naming convention is entirely different. The `"openai/"` prefix means "use OpenAI-compatible format" in DSPy but "strip this routing prefix" in kilo-proxy. Litellm requires models to be defined in config with their upstream provider mapped. Every single model ID in the evolution rotation would need to be renamed, AND all persisted state would become stale/invalid.

**Why it matters for the plan**: Phase 1 says "Replace kilo-proxy.py with litellm proxy (YAML config)". But the model IDs in the evolution pipeline are tightly coupled to kilo-proxy's routing scheme. After migration, `evolve_skill_rotation.py` will send model IDs like `"openai/deepseek-proxy/deepseek-v4-flash"` to litellm, which will treat them as OpenAI model names and fail.

**Plan step that fails**: Phase 1 rollout — every evolution run after migration will submit broken model IDs.

### H-02: Custom Anthropic SSE Patching Will Break Under litellm
**File**: kilo-proxy.py, lines 1027-1054  
```python
# Filter problematic blocks for anthropic-proxy
if provider_name == "anthropic-proxy":
    if obj.get("type") == "content_block_start":
        cb = obj.get("content_block", {})
        # OpenRouter's redacted_thinking — SDK drops text when seen
        if cb.get("type") == "redacted_thinking":
            ...
        # Strip thinking signature
        if cb.get("type") == "thinking" and "signature" in cb:
            del cb["signature"]
```

**Issue**: This is 35+ lines of carefully tuned SSE stream patching that handles:
- Stripping `redacted_thinking` blocks (causes Claude Code to silently drop response)
- Removing `signature` from `thinking` blocks (prevents SDK from converting to redacted_thinking)
- Stripping `thinking` parameter from request body (line 1149)

This is undocumented, untested, and works through trial-and-error. litellm has its own Anthropic API integration that will NOT include these patches. If litellm is used as a passthrough to OpenRouter for Anthropic models, Claude Code will experience silent response drops (text disappears, agent appears to stall).

**Why it matters for the plan**: The plan mentions this as assumption #8 but provides no mitigation. The kilocode agent (Claude Code) depends on this patching. Without it, Anthropic proxy calls via OpenRouter will be broken or produce corrupted responses.

**Plan step that fails**: Phase 1 — Anthropic `/v1/messages` passthrough will be non-functional after litellm swap.

### H-03: Google AI Studio Uses `?key=` Query Auth — litellm May Not Support This
**File**: kilo-proxy.py, lines 1211-1213, 1234-1237  
```python
elif auth_header_name.lower() == "api_key":
    # Google AI Studio uses ?key= query parameter
    pass
...
# Google AI Studio auth: append ?key= query parameter
if auth_header_name.lower() == "api_key":
    sep = '&' if '?' in url else '?'
    url += f"{sep}key={current_token['value']}"
```

**Issue**: Google AI Studio uses `?key=` query parameter authentication, not Bearer tokens or API Key headers. The litellm proxy's standard auth support (Bearer, API key in header) does NOT handle query-param-based auth natively. This requires custom preprocessing or a provider-specific plugin.

**Why it matters for the plan**: The plan assumes litellm can handle all 9 providers' auth methods. Google's `?key=` approach is non-standard. Without confirming litellm supports this (or having a plan to add it), google-proxy will be broken after migration.

**Plan step that fails**: Phase 1 — 5 Google AI Studio tokens (gemma_1.txt through gemma_5.txt) become unusable.

### H-04: Kilocode Client Hardcodes Direct Proxy URL — No Migration Path
**File**: evolve_skill_rotation.py, line 46  
```python
os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:8080/v1")
```
**File**: kilo.mac.jsonc — references LLM_NVIDIA_API_KEY_1..4 directly
**File**: restart-kilo-proxy.sh, kilo-proxy-watchdog.sh — all hardcode port 8080

**Issue**: The entire client ecosystem is wired to `localhost:8080`:
- `evolve_skill_rotation.py` sets OPENAI_BASE_URL to `http://localhost:8080/v1`
- `restart-kilo-proxy.sh` checks `http://localhost:8080`
- `kilo-proxy-watchdog.sh` polls `http://localhost:8080/`
- Kilo agent config references NVIDIA tokens directly, not through the proxy

After migration to litellm, litellm may use a different port (default 4000) or require a different URL scheme. The plan doesn't specify the litellm port, config path, or how clients discover the new endpoint.

**Why it matters for the plan**: Phase 1 has no client migration step. Switching the proxy means ALL consuming code must be updated. Missing even one `localhost:8080` reference will silently fail.

**Plan step that fails**: Phase 1 rollout — evolution pipeline, watchdog, restart scripts, and agent configs all break simultaneously.

### H-05: Dual State File Problem Not Actually Fixed — Plan Description Hides Complexity
**File**: evolve_skill_rotation.py, lines 197-232 (RotationState)
**File**: config.py, line 7: `ROTATION_STATE_FILE = WRAPPERS_DIR / ".rotation_state.json"`
**File**: config.py, line 5: `HERMES_AGENT_REPO = Path(os.getenv("HERMES_AGENT_REPO", "/Users/kieranlal/workspace/nano2"))`

**Issue**: The dual state file problem (sandbox $HOME vs real $HOME) exists because:
1. `WRAPPERS_DIR = ~/.hermes/skills/.wrappers` uses `os.path.expanduser("~")` which respects sandbox $HOME
2. But `HERMES_AGENT_REPO` default hardcodes `/Users/kieranlal/workspace/nano2` — NOT sandbox-aware
3. The batch `evolve_batch_size_aware.sh` hardcodes `/Users/kieranlal/.hermes/skills/.wrappers/`
4. The evolution's `health.json` writer in `run_evolution_with_watchdog` (line 630) writes to `WRAPPERS_DIR/health.json` using sandbox-aware path, but the batch reads from hardcoded path

The plan says "Dual state file problem" is a known issue but offers no resolution in Phase 3 beyond "cleanup." This inconsistency persists across all three phases.

**Why it matters for the plan**: Phase 3 "cleanup" is undefined. Without explicitly resolving the dual-path issue, rotation state will silently diverge between sandbox and real environments, causing the 9-zombie-state problem to recur.

**Plan step that fails**: Phase 3 — the cleanup step doesn't specify how or when to unify state files.

### H-06: No Rollback Plan — Migration Has No Reverse
**Issue**: The plan is entirely forward-looking: Phase 1 → Phase 2 → Phase 3. There is no rollback strategy if litellm or Temporal/Prefect fails in production. The existing scripts work (albeit with bugs), and replacing them with new infrastructure creates a single point of failure with no fallback.

**Why it matters for the plan**: If litellm has a bug that breaks streaming, or Temporal requires a database migration that corrupts workflow state, there is no documented way to revert to kilo-proxy.py while maintaining continuity. This is especially dangerous for 27 pending skills (600-3600s each) — interrupting them mid-migration loses days of computation.

**Plan step that fails**: All phases — no safety net.

### H-07: Temporal Requires Full Infrastructure Stack — Not a Drop-in Replacement
**Assumption**: "Temporal is NOT installed — needs `brew install temporal` or Docker"

**Issue**: `brew install temporal` installs the CLI, not a running server. To actually use Temporal:
- Need a Temporal Server (Docker Compose with 4+ containers: cassandra/PostgreSQL, temporal, temporal-web, temporal-admin-tools)
- Need schema initialization and migration
- Need worker processes that stay alive
- Need a database (PostgreSQL is already running for Honcho, but Temporal needs its own schema)

This is NOT a simple `pip install prefect` or `brew install temporal` scenario. It's a multi-day infrastructure setup with its own operational burden.

**Why it matters for the plan**: Phase 2 is severely underestimated. The plan says Phase 2 replaces the batch orchestrator, but the actual orchestration dependencies (Temporal server, database, workers, SDK) are not accounted for in timeline, cost, or reliability.

**Plan step that fails**: Phase 2 — won't complete without significant infrastructure work.

### H-08: No Test Suite for Proxy (Acknowledged) — Migration Cannot Validate Correctness
**File**: test_kilo_proxy_routing.py (219 lines)

**Issue**: The existing test file only covers `detect_provider()`, `TokenStore`, and model exhaustion helpers. It does NOT test:
- POST request routing and response forwarding
- Token rotation loop behavior
- SSE streaming and chunk normalization
- Error handling (all the 400/401/403/429/502/503 paths)
- Circuit breaker logic
- Concurrent request handling
- Token reload/refresh scenarios

The plan acknowledges "no test suite for proxy — only manual curl tests" (assumption #9). Without a test suite, the migration from kilo-proxy.py to litellm cannot be validated for behavioral parity. You won't know if litellm handles token rotation, SSE patching, auth methods, or error classification correctly until it fails in production.

**Why it matters for the plan**: Phase 1 has no validation step. The migration is a blind swap.

**Plan step that fails**: Phase 1 — no acceptance criteria defined, no automated validation.

### H-09: Token Rotation Customization Cannot Be Expressed in litellm YAML
**File**: kilo-proxy.py, lines 1180-1495 (full token rotation + fallback logic ~315 lines)

**Issue**: kilo-proxy.py's token rotation includes:
- Per-token jittered backoff with ±20% randomization (line 372-377)
- Per-provider cooldown tracking for Google tokens to avoid abuse detection (lines 1332-1339)
- Model exhaustion tracking with 10-minute cooldown (line 402)
- Circuit breaker that degrades providers after 3 consecutive failures (line 459)
- 401-all-automatic-token-refresh via 1Password (lines 1467-1481)
- Response code classification (rate-limit vs auth vs model-unavailable)
- Streaming stall detection with abort (lines 965-987)
- Zero-byte response detection (lines 1439-1452)

litellm proxy's YAML config supports *basic* load balancing and fallback, but NONE of these sophisticated behaviors. The rotation, cooldown, degradation, refresh, and classification logic would need to be re-implemented as a custom litellm router plugin or middleware — which defeats the purpose of "replacing with hardened libraries."

**Why it matters for the plan**: Phase 1 is framed as a simplification but actually requires re-implementing 300+ lines of custom logic. The plan doesn't acknowledge this.

**Plan step that fails**: Phase 1 — litellm YAML config will be grossly insufficient to replicate current behavior.

---

## 🟡 MEDIUM FINDINGS (Moderate effort, should be addressed)

### M-01: No Graceful Shutdown — ThreadingMixIn Threads Abandoned on Ctrl+C
**File**: kilo-proxy.py, lines 1506-1509  
```python
try:
    server.serve_forever()
except KeyboardInterrupt:
    log_event("[Multi-Provider Proxy] Shutting down.")
```

**Issue**: ThreadingMixIn creates daemon threads (line 845: `daemon_threads = True`). When the main thread exits on Ctrl+C, daemon threads are terminated mid-operation — any in-flight requests are aborted with partially-sent responses. Clients see truncated data. The migration plan doesn't address this for litellm (which may handle it better) but the Phase 3 cleanup must ensure old proxy is killed cleanly.

**Plan relevance**: Phase 3 cleanup — if the old proxy is `pkill -9`'d (as done in restart scripts), in-flight requests fail unpredictably.

### M-02: Hardcoded Absolute Paths Everywhere — Not Portable
**Files**: 
- evolve_batch_size_aware.sh: lines 8, 10, 15, 19, 22 — `/Users/kieranlal/...`
- config.py: line 8 — `/Users/kieranlal/workspace/hermes-agent-self-evolution/.venv/bin/python3`
- evolve_skill_rotation.py: lines 57, 63 — hardcoded paths
- restart-kilo-proxy.sh: line 12 — absolute path

**Issue**: Every script hardcodes the user's home path. This breaks:
- Running on a different macOS machine
- Running as a different user
- CI/CD environments
- Jetson devices (different $HOME)

**Plan relevance**: The plan mentions macOS host but doesn't specify whether Temporal/Prefect will also require hardcoded paths. This is a pre-existing issue that the migration should fix but doesn't address.

### M-03: Token File Reading Is Non-Atomic — Partial Read Risk
**File**: kilo-proxy.py, line 680  
```python
val = open(path).read().strip()
```

**Issue**: Token files reside in `~/.enclave/` which is concurrently written by `agent_wrapper.sh --sync` (1Password sync). If a read coincides with a write, the file may be partially written. The value would be silently truncated, causing auth failures that are hard to diagnose.

**Plan relevance**: Migration to litellm should include atomic file reading (`write to tmp, rename` pattern) but doesn't.

### M-04: Preflight Checks Are Shallow — False Confidence
**File**: evolve_skill_rotation.py, lines 790-884

**Issue**: The preflight checks:
- Check proxy reachability to `/v1/models` but NOT actual model availability (an endpoint may report models but 503 on completion)
- Check config.py import but NOT that VENV_PYTHON actually exists (hardcoded path)
- Check WRAPPERS_DIR writability but NOT that all state files are consistent
- Do NOT check disk space (27 pending skills × 600-3600s each = days of compute, logs can grow large)

**Plan relevance**: Phase 2 batch migration should include robust preflight validation but the plan doesn't specify what Temporal/Prefect preflight would look like.

### M-05: Batch Lockfile Vulnerable to Stale PID Collision
**File**: evolve_batch_size_aware.sh, lines 103-116

**Issue**: The lockfile at `/tmp/evolve_batch.lock` uses PID to detect running instances. If:
1. System restarts (PID counter resets)
2. Another process with a different project uses the same PID
3. `/tmp` is cleaned but batch is still running

Lock will be stale or falsely claim exclusivity. This is a classic `/tmp/pid.lock` antipattern.

**Plan relevance**: Phase 2 Temporal/Prefect provides proper job IDs but Phase 3 cleanup doesn't address removing the old lockfile.

### M-06: No Rate Limiting or Abuse Prevention — Single Client Can Starve Others
**File**: kilo-proxy.py — no rate limiting anywhere

**Issue**: A single aggressive client (or runaway agent) can:
- Exhaust all token rotations for a provider
- Trigger circuit breaker for all clients sharing the proxy
- Consume Cloudflare's 10K/day neuron budget in minutes

The circuit breaker is per-provider, not per-client. There's no fairness mechanism.

**Plan relevance**: litellm has basic rate limiting but the plan doesn't specify configuring it. Migration should include per-client quotas.

### M-07: "cloudflare-proxy" Budget Warning Is Advisory Only — No Enforceable Guard
**File**: kilo-proxy.py, lines 231-233  
```python
# ⚠️ BUDGET WARNING: Cloudflare Workers AI free tier = 10,000 neurons/day.
```
**File**: kilo-proxy.py, MODEL_FALLBACK, line 298  
```python
# NOTE: cloudflare-proxy is NOT listed here — it has a 10K neuron/day hard limit
```

**Issue**: The budget warning is a comment. There is no code that tracks daily neuron usage, prevents automatic fallback to Cloudflare, or alerts on budget depletion. An accidental route to Cloudflare (e.g., model prefix typo) silently burns real money.

**Plan relevance**: litellm needs to replicate this cautiously — but the plan doesn't mention budget controls at all.

### M-08: Context Bloat / Stall / Stream Detection Uses Non-Thread-Safe deque
**File**: kilo-proxy.py, lines 596, 616, 636

**Issue**: `_TTFB_WINDOW`, `_STALL_WINDOW`, `_STREAM_WINDOW` are `collections.deque` objects modified from multiple handler threads without locks. These are used for cmux alerting decisions but could produce incorrect medians under concurrent load (false alerts or missed alerts).

**Plan relevance**: If these alerts drive operator decisions, reliability degrades under load. Migration should make these thread-local or locked.

---

## 🔵 LOW FINDINGS (Minor, nice-to-fix)

### L-01: Phase 3 "Cleanup and Retirement" Is Undefined
**Plan text**: "Phase 3: Cleanup and retirement of old scripts"

**Issue**: No criteria for when Phase 3 starts, what constitutes "retirement" (renaming files? archiving? deleting?), or what monitoring validates Phase 1+2 are stable enough to proceed. This is a plan hole.

### L-02: Cloudflare Account ID Hardcoded in Upstream URL
**File**: kilo-proxy.py, line 235  
```python
"upstream": "https://api.cloudflare.com/client/v4/accounts/3e92750111311c9b5247eb627cef7b19/ai/v1"
```
**Issue**: The Cloudflare account ID is hardcoded. If the account changes or the proxy is deployed elsewhere, this breaks silently.

### L-03: No TLS/HTTPS for Proxy-to-Client Communication
**File**: kilo-proxy.py — all communication is plain HTTP

**Issue**: The proxy communicates via plain HTTP to clients. Any process on the same machine (or network, see C-01) can intercept request/response data, including model prompts and generated content.

### L-04: Evolve_skill_rotation.py `_pick_eval_model` Documentation Says "broken" but Still References Dead Models
**File**: evolve_skill_rotation.py, lines 469-471  
```python
# Note: deepseek-v4 and hermes-4 are currently broken (503/NotFound).
```

**Issue**: Documentation comments about broken models are stale. The code references models that are explicitly documented as broken but aren't removed from the pool. This technical debt carries over after migration.

### L-05: Restart Script's Token Count Extraction Uses Fragile `python3 -c` Command
**File**: restart-kilo-proxy.sh, line 18  
```bash
TOKENS=$(curl -sf http://localhost:8080 2>/dev/null | python3 -c "import sys,json; ...")
```

**Issue**: The token count display uses a piping chain that breaks if curl returns non-JSON (e.g., HTML error page). This gives a misleading "FAIL" message when the proxy might actually be healthy but the health endpoint returned a non-standard response.

---

## SUMMARY TABLE

| Severity | Count | Key Themes |
|----------|-------|------------|
| 🔴 Critical | 4 | Open proxy, duplicate config, race conditions, FD leak |
| 🟠 High | 9 | Model ID incompatibility, SSE patching loss, auth incompatibility, no migration path, state file bifurcation, no rollback, Temporal infra cost, no test suite, rotation logic loss |
| 🟡 Medium | 8 | No graceful shutdown, hardcoded paths, non-atomic reads, shallow preflight, PID collision, no rate limiting, advisory-only budget guard, non-thread-safe deques |
| 🔵 Low | 5 | Undefined Phase 3, hardcoded IDs, no TLS, stale doc comments, fragile script |

**Total: 26 findings**

---

## TOP 5 BLOCKERS FOR PLAN EXECUTION

1. **C-01 (Open Proxy)**: Must fix before migrating — security vulnerability active in production
2. **C-02 (Duplicate deepseek-proxy)**: Ambiguity in config means YAML generation is impossible without resolving which env var name is correct
3. **H-01 (Model ID Incompatibility)**: All client tooling sends `openai/deepseek-proxy/...` style IDs that litellm cannot route
4. **H-02 (SSE Patching)**: Anthropic proxy breaks without custom stream modification — undocumented and untested behavior
5. **H-09 (Token Rotation Logic)**: 315 lines of custom rotation/backoff/circuit-breaker/refresh logic that litellm YAML cannot express

**Recommendation**: Do NOT proceed with Phase 1 until these five are addressed in a revised plan.
