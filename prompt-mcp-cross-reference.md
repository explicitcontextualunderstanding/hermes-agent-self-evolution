# Prompt–MCP Tool Cross-Reference Table

**Classification Legend:**
- **EXISTING** — tool exists in the backend (confirmed from test results)
- **MISSING** — tool does not exist (listed as missing from test results)
- **UNKNOWN** — can't determine which specific MCP tool from prompt text

**Source documents:**
- Doc 1: `hermes-agent-backend-test-prompts.md` (47 prompts, #1–#47)
- Doc 2: `hermes-agent-backend-test-prompts-2.md` (21 prompts, #48–#68)
- Doc 3: `hermes-agent-backend-test-prompts-4.md` (23 prompts, #69–#91)

---

## Document 1: Container Lifecycle + AgentSpec + Checkpoint + Workflows

| # | Prompt Name | Primary Tool(s) Exercised | Category |
|---|-------------|--------------------------|----------|
| 1 | container_create — Basic | `create_container` | EXISTING |
| 2 | container_create — With Volume Mount | `create_container` | EXISTING |
| 3 | container_create — Full Spec | `create_container` | EXISTING |
| 4 | container_start | `start_container` | EXISTING |
| 5 | container_stop | `stop_container` | EXISTING |
| 6 | container_delete | `delete_container` | EXISTING |
| 7 | container_list — All | `list_containers` | EXISTING |
| 8 | container_list — Running Only | `list_containers` | EXISTING |
| 9 | container_inspect | `inspect_container` | EXISTING |
| 10 | container_exec — Simple Command | `exec_in_container` | EXISTING |
| 11 | container_exec — Interactive Shell | `exec_in_container` | EXISTING |
| 12 | container_exec — With Working Directory | `exec_in_container` | EXISTING |
| 13 | pod_lifecycle_handler — Create Pod | `pod_lifecycle_handler` | MISSING |
| 14 | pod_lifecycle_handler — Delete Pod | `pod_lifecycle_handler` | MISSING |
| 15 | pod_lifecycle_handler — Get Pod Status | `pod_lifecycle_handler` | MISSING |
| 16 | agentspec_create — Minimal | `agentspec_create` | MISSING |
| 17 | agentspec_create — With Tools | `agentspec_create` | MISSING |
| 18 | agentspec_create — Full Configuration | `agentspec_create` | MISSING |
| 19 | agentspec_validate — Valid Spec | `agentspec_validate` | MISSING |
| 20 | agentspec_validate — Check Constraints | `agentspec_validate` | MISSING |
| 21 | agentspec_list | `agentspec_list` | MISSING |
| 22 | checkpoint_create — Basic | `checkpoint_create` | MISSING |
| 23 | checkpoint_create — With Metadata | `checkpoint_create` | MISSING |
| 24 | checkpoint_restore | `checkpoint_restore` | MISSING |
| 25 | checkpoint_list — All | `checkpoint_list` | MISSING |
| 26 | checkpoint_list — Filter by Container | `checkpoint_list` | MISSING |
| 27 | checkpoint_analyze — Single Checkpoint | `checkpoint_analyze` | MISSING |
| 28 | checkpoint_analyze — Compare Two Checkpoints | `checkpoint_analyze` | MISSING |
| 29 | Full Container Lifecycle | `create_container` + `start_container` + `inspect_container` + `exec_in_container` + `stop_container` + `delete_container` + `list_containers` | EXISTING |
| 30 | Checkpoint Workflow | `create_container` + `start_container` + `checkpoint_create` + `exec_in_container` + `checkpoint_analyze` + `checkpoint_restore` | MISSING |
| 31 | AgentSpec + Container Integration | `agentspec_create` + `agentspec_validate` + `agentspec_list` | MISSING |
| 32 | Pod + Container Hybrid | `pod_lifecycle_handler` + `create_container` + `list_containers` | MISSING |
| 33 | Error Handling — Invalid Image | `create_container` | EXISTING |
| 34 | Error Handling — Duplicate Name | `create_container` | EXISTING |
| 35 | Error Handling — Exec in Stopped Container | `create_container` + `stop_container` + `exec_in_container` | EXISTING |
| 36 | Error Handling — Restore Nonexistent Checkpoint | `checkpoint_restore` | MISSING |
| 37 | Stress Test — Multiple Containers | `create_container` + `start_container` + `list_containers` + `stop_container` + `delete_container` | EXISTING |
| 38 | Stress Test — Rapid Checkpoint/Restore | `checkpoint_create` + `checkpoint_restore` + `checkpoint_list` | MISSING |
| 39 | Empty Environment Variables | `create_container` + `inspect_container` | EXISTING |
| 40 | Long Command with Special Characters | `create_container` | EXISTING |
| 41 | Container with No Command (Image Default) | `create_container` + `start_container` + `inspect_container` | EXISTING |
| 42 | Checkpoint Without Starting | `checkpoint_create` | MISSING |
| 43 | List Checkpoints for Nonexistent Container | `checkpoint_list` | MISSING |
| 44 | Validate Malformed AgentSpec | `agentspec_create` | MISSING |
| 45 | Inspect Nonexistent Container | `inspect_container` | EXISTING |
| 46 | Clean All Test Resources | `delete_container` + `agentspec_delete` + `pod_lifecycle_handler` + checkpoint deletion (no tool) | MISSING |
| 47 | Verify Cleanup | `list_containers` + `agentspec_list` + `pod_lifecycle_handler` + `checkpoint_list` | MISSING |

**Doc 1 Totals:**
| Category | Count |
|----------|-------|
| EXISTING | 21 |
| MISSING  | 26 |
| UNKNOWN  | 0 |
| **Total** | **47** |

---

## Document 2: Advanced Orchestration (Plan 105–107)

| # | Prompt Name | Primary Tool(s) Exercised | Category |
|---|-------------|--------------------------|----------|
| 48 | RBAC — Enforce FleetAgent Least Privilege | `check_action` | EXISTING |
| 49 | Role Override — Anchor Elevation | `check_action` | EXISTING |
| 50 | Preflight — TCC Access Verification | *(no direct MCP tool — tests internal preflight mechanism)* | UNKNOWN |
| 51 | Schema Evolution — Backward Compatibility | `realize_pod` | EXISTING |
| 52 | Pkl Evaluation — Structural Constraint Violation | `realize_pod` | EXISTING |
| 53 | Creative Pod — Metal Passthrough | `realize_pod` + `inspect_container` + `exec_in_container` | EXISTING |
| 54 | Creative Pod — Shared Memory Asset Routing | `realize_pod` + `exec_in_container` | EXISTING |
| 55 | High-Frequency Signaling (kqueue Stress) | `exec_in_container` | EXISTING |
| 56 | OptimizerAgent — State Machine Recovery | `get_ltl_state` + `analyze_traces` | EXISTING |
| 57 | Recipe Applicability Chain Validation | `realize_pod` + `analyze_traces` | EXISTING |
| 58 | Regression Prevention — Invariant Violation | `check_action` | EXISTING |
| 59 | AgentSpec — Immutability Enforcement | `agentspec_create` | MISSING |
| 60 | Cross-Session Continuity (Harness Checkpoint) | `get_checkpoint` + `reset_checkpoint` | EXISTING |
| 61 | VSOCK Signaling (High Density) | `create_container` | EXISTING |
| 62 | Air Gap Security — Sandbox Escape Attempt | `exec_in_container` | EXISTING |
| 63 | Context Routing — Resource Quota Rejection | `create_container` / `realize_pod` | EXISTING |
| 64 | Capability Discovery — Hardware Missing | `realize_pod` | EXISTING |
| 65 | Trace Ingestion — Bottleneck Identification | `ingest_trace` + `analyze_traces` | EXISTING |
| 66 | Feedback Loop — Automatic Schema Tuning | `ingest_trace` + `analyze_traces` | EXISTING |
| 67 | Sequence Violation — Bypass Preflight | `get_ltl_state` / `check_action` | EXISTING |
| 68 | LTL Liveness — Recovery Timeout | `get_ltl_state` | EXISTING |

**Doc 2 Totals:**
| Category | Count |
|----------|-------|
| EXISTING | 19 |
| MISSING  | 1  |
| UNKNOWN  | 1  |
| **Total** | **21** |

---

## Document 3: Host-Native Secure Lane (Plan 108/121)

| # | Prompt Name | Primary Tool(s) Exercised | Category |
|---|-------------|--------------------------|----------|
| 69 | Slab Lifecycle — Create & List | `create_slab` + `list_slabs` | EXISTING |
| 70 | Slab Cleanup — Delete | `delete_slab` + `list_slabs` | EXISTING |
| 71 | MLX Inference — Deterministic CLIP | `create_slab` + `execute_native_model` | EXISTING |
| 72 | XPC Hardening — Mach Service Verification | `execute_native_model` | EXISTING |
| 73 | Multi-Shot — Persistent Worker Test | `execute_native_model` (5 iterations) | EXISTING |
| 74 | Zero-Path — FD Inheritance Verification | `execute_native_model` (zero_path=true) | EXISTING |
| 75 | Sandbox Escape — Host-Native Boundary | `execute_native_model` | EXISTING |
| 76 | Handshake Timeout | `create_slab` + `execute_native_model` | EXISTING |
| 77 | Slab Size Mismatch | `create_slab` + `execute_native_model` | EXISTING |
| 78 | XPC Authorization — Unsigned Client Rejection | *(no direct MCP tool — tests Mach service auth)* | UNKNOWN |
| 79 | XPC Authorization — Signed Client Acceptance | *(no direct MCP tool — tests Mach service auth)* | UNKNOWN |
| 80 | Worker Crash — Partial Write Corruption | `execute_native_model` | EXISTING |
| 81 | Harness Crash — Orphaned Slab Recovery | *(no direct MCP tool — tests harness restart logic)* | UNKNOWN |
| 82 | Protocol Violation — Invalid State Transition | *(no direct MCP tool — manipulates slab state directly)* | UNKNOWN |
| 83 | Protocol Violation — State Regression | *(no direct MCP tool — manipulates slab state directly)* | UNKNOWN |
| 84 | Fuzzed Input — Random Buffer | `create_slab` + `execute_native_model` | EXISTING |
| 85 | MLX Service Unavailable — Graceful Degradation | `execute_native_model` | EXISTING |
| 86 | Offline Mode — Cached Model Inference | `execute_native_model` | EXISTING |
| 87 | GPU Memory Stability — Sustained Inference | `execute_native_model` (100 iterations) | EXISTING |
| 88 | Model Reload — Memory Return to Baseline | `execute_native_model` (10 iterations) | EXISTING |
| 89 | Sequential MLX — Two Containers | `create_container` + `exec_in_container` | EXISTING |
| 90 | Concurrent MLX — Two Containers Simultaneously | `create_container` + `exec_in_container` | EXISTING |
| 91 | Container Crash During MLX Callback | `create_container` + `exec_in_container` | EXISTING |

**Doc 3 Totals:**
| Category | Count |
|----------|-------|
| EXISTING | 18 |
| MISSING  | 0  |
| UNKNOWN  | 5  |
| **Total** | **23** |

---

## Grand Totals (All 3 Documents)

| Document | EXISTING | MISSING | UNKNOWN | Total |
|----------|----------|---------|---------|-------|
| Doc 1 (Prompts 1–47) | 21 | 26 | 0 | **47** |
| Doc 2 (Prompts 48–68) | 19 | 1 | 1 | **21** |
| Doc 3 (Prompts 69–91) | 18 | 0 | 5 | **23** |
| **Overall** | **58** | **27** | **6** | **91** |

---

## Per-Tool Breakdown (Count of Prompts)

### Existing Tools (exercised by prompts)

| Tool | Prompts |
|------|---------|
| `create_container` | 1–3, 29, 33–35, 37, 39–41, 61, 63, 89–91 |
| `start_container` | 4, 29, 35, 37, 41 |
| `stop_container` | 5, 29, 35, 37 |
| `delete_container` | 6, 29, 37, 46 |
| `list_containers` | 7–8, 29, 47 |
| `inspect_container` | 9, 29, 39, 41, 45, 53 |
| `exec_in_container` | 10–12, 29, 35, 53–55, 62, 89–91 |
| `check_action` | 48–49, 58, 67 |
| `realize_pod` | 51–54, 57, 63–64 |
| `get_ltl_state` | 56, 67–68 |
| `get_checkpoint` | 60 |
| `reset_checkpoint` | 60 |
| `analyze_traces` | 56–57, 65–66 |
| `ingest_trace` | 65–66 |
| `create_slab` | 69, 71, 76–77, 84 |
| `list_slabs` | 69–70 |
| `delete_slab` | 70 |
| `execute_native_model` | 71–77, 80, 84–88 |

### Missing Tools (exercised by prompts)

| Missing Tool | Prompts |
|--------------|---------|
| `pod_lifecycle_handler` | 13–15, 32, 46–47 |
| `agentspec_create` | 16–18, 31, 44, 59 |
| `agentspec_validate` | 19–20, 31 |
| `agentspec_list` | 21, 31, 47 |
| `checkpoint_create` | 22–23, 30, 38, 42 |
| `checkpoint_restore` | 24, 30, 36, 38 |
| `checkpoint_list` | 25–26, 38, 43, 47 |
| `checkpoint_analyze` | 27–28, 30 |

### UNKNOWN Prompts

| # | Prompt | Reason |
|---|--------|--------|
| 50 | Preflight — TCC Access Verification | References internal "preflight check" — no direct MCP tool |
| 78 | XPC Authorization — Unsigned Client Rejection | Tests Mach service auth, not an MCP tool |
| 79 | XPC Authorization — Signed Client Acceptance | Tests Mach service auth, not an MCP tool |
| 81 | Harness Crash — Orphaned Slab Recovery | Tests harness restart / slab recovery logic |
| 82 | Protocol Violation — Invalid State Transition | Requires direct slab state manipulation |
| 83 | Protocol Violation — State Regression | Requires direct slab state manipulation |

---

## Notes

1. **Prompt numbering anomaly in Doc 3:** Prompts 78 and 79 appear before 76 and 77 in the source file, but all 23 prompts are present (69–91 with no gaps).
2. **Multi-step prompts** (29–32, 37–38, 46–47) exercise multiple tools. They are categorized as MISSING if any required tool is missing.
3. **`realize_pod` vs `pod_lifecycle_handler`:** The `realize_pod` tool exists and is used by Plan 105 prompts. The `pod_lifecycle_handler` (a unified create/delete/status pod handler) does NOT exist as a single tool — prompts 13–15 explicitly request it by name.
4. **AgentSpec tools** (`agentspec_create`, `agentspec_validate`, `agentspec_list`) are classified as MISSING per the test results classification, even though tool definitions appear in the current MCP server surface.
5. **Checkpoint tool ambiguity:** The existing `get_checkpoint`/`reset_checkpoint` are Harness-level checkpoint tools. The MISSING `checkpoint_create`/`checkpoint_restore`/etc. are container-level checkpoint tools — a different concept entirely.
