# G0 Gate Scoring Results — Plan 122
## 3-Dimension Rubric Test Across 10 Diverse Prompts (compose-pkl docs)

### Scoring Rubric Dimensions
1. **CLARITY** (0.0–1.0): Does the prompt specify a single, unambiguous action? (exact tool name, exact parameters expected)
2. **RESILIENCE** (0.0–1.0): Does the prompt handle failure gracefully? (timeout handling, precondition checks, expected errors)
3. **SELF-CONTAINMENT** (0.0–1.0): Can this prompt run independently of other prompts? (no references to containers/resources created by other prompts)

---

### Prompt #1 — create_container (hangs)
**Source:** `compose-pkl/docs/hermes-agent-backend-test-prompts.md` prompt #1
**Prompt text:** "Create a new container named 'test-nginx' using the nginx:latest image. Expose port 8080 on the host to port 80 in the container. Set the environment variable FOO=bar."
| Dimension | Score | Rationale |
|-----------|-------|-----------|
| CLARITY | 0.6 | Specifies image, name, port mapping, env var — but port syntax ("8080 on the host to port 80") is ambiguous (no protocol). Missing memory/CPU spec. |
| RESILIENCE | 0.3 | No timeout handling, no precondition checks, no error recovery. Test result shows it **hangs indefinitely** (initfs pull from localhost). |
| SELF-CONTAINMENT | 0.9 | Creates one container, no dependency on other prompts. |

### Prompt #7 — list_containers (passes)
**Source:** `compose-pkl/docs/hermes-agent-backend-test-prompts.md` prompt #7
**Prompt text:** "List all containers, including stopped ones. Show me their names, IDs, images, and status."
| Dimension | Score | Rationale |
|-----------|-------|-----------|
| CLARITY | 0.9 | Single clear action: list all containers with specific fields requested. Unambiguous tool mapping to `list_containers`. |
| RESILIENCE | 0.9 | Passes cleanly — returns empty list `{"containers":[]}` when none exist. Graceful no-data handling. |
| SELF-CONTAINMENT | 1.0 | No dependencies whatsoever. Works on any system state (empty or populated). |

### Prompt #16 — agentspec_create (ghost tool)
**Source:** `compose-pkl/docs/hermes-agent-backend-test-prompts.md` prompt #16
**Prompt text:** "Create an AgentSpec named 'minimal-agent' with: - Description: A minimal test agent - Model: gpt-4o-mini - System prompt: 'You are a helpful assistant.'"
| Dimension | Score | Rationale |
|-----------|-------|-----------|
| CLARITY | 0.7 | Specifies name, description, model, system prompt clearly. Parameters match `agentspec_create` schema. |
| RESILIENCE | 0.2 | Tool `agentspec_create` does not exist in backend. Prompt assumes existence with no fallback or error handling. Returns NOT_IMPL error. |
| SELF-CONTAINMENT | 0.9 | Creates one resource, no external dependencies. |

### Prompt #33 — invalid image (error handling)
**Source:** `compose-pkl/docs/hermes-agent-backend-test-prompts.md` prompt #33
**Prompt text:** "Try to create a container 'bad-image' using image 'this-image-does-not-exist:9999'. Capture and report the error."
| Dimension | Score | Rationale |
|-----------|-------|-----------|
| CLARITY | 0.7 | Clear intent (invalid image), specifies "capture and report error." Action is well-defined. |
| RESILIENCE | 0.4 | Explicitly asks for error capture/report — good intent — but test result shows it **hangs** because image validation happens after the initfs pull. The error recovery path doesn't trigger. |
| SELF-CONTAINMENT | 0.8 | Self-contained resource creation, no inter-prompt dependencies. |

### Prompt #48 — check_action (harness)
**Source:** `compose-pkl/docs/hermes-agent-backend-test-prompts-2.md` prompt #48
**Prompt text:** "Attempt to create a container with 'role: FleetAgent' but override the volume mount to request 'ReadWrite' access. Verify that the Pkl-evaluator rejects the configuration before the VM boot process starts."
| Dimension | Score | Rationale |
|-----------|-------|-----------|
| CLARITY | 0.8 | Specifies exact scenario (FleetAgent role, ReadWrite override, Pkl rejection gate). Multi-condition but unambiguous. |
| RESILIENCE | 0.7 | Explicitly expects and validates a failure (Pkl rejection). Built-in verification step. Could be improved by specifying what to do if Pkl accepts. |
| SELF-CONTAINMENT | 0.7 | References RBAC roles (FleetAgent) which is a system concept, but no direct dependency on other prompts. |

### Prompt #58 — analyze_traces (harness)
**Source:** `compose-pkl/docs/hermes-agent-backend-test-prompts-2.md` prompt #58
**Prompt text:** "Attempt to update a running service's 'signaling' policy from 'KQueue' to 'None' while the pod is Active. Verify that the Harness rejects the change as a violation of the 'Live-Constraint' invariant defined in the Pkl schema."
| Dimension | Score | Rationale |
|-----------|-------|-----------|
| CLARITY | 0.6 | Complex scenario: updating running service's signaling policy. Requires understanding of pod states (Active), signaling modes (KQueue/None), and Live-Constraint invariants. Multi-concept. |
| RESILIENCE | 0.8 | Strong verification step. Expects harness to reject — tests the enforcement path. |
| SELF-CONTAINMENT | 0.5 | Requires a running pod in Active state with KQueue signaling configured. Implies prior setup/state. |

### Prompt #69 — create_slab (host-native)
**Source:** `compose-pkl/docs/hermes-agent-backend-test-prompts-4.md` prompt #69
**Prompt text:** "Create a new SharedMemory slab named 'vision-frame-0' with a size of 2MB. List all active slabs to verify it was created correctly."
| Dimension | Score | Rationale |
|-----------|-------|-----------|
| CLARITY | 0.9 | Crystal clear: create slab with name + size, then list to verify. Two straightforward steps. |
| RESILIENCE | 0.7 | Includes verification step (list after create). No error handling for duplicate names or capacity limits. |
| SELF-CONTAINMENT | 0.8 | Self-contained, creates one resource and immediately verifies. |

### Prompt #71 — execute_native_model (MLX)
**Source:** `compose-pkl/docs/hermes-agent-backend-test-prompts-4.md` prompt #71
**Prompt text:** "1. Create a slab named 'clip-input' (1MB). 2. Execute the 'clip-vit' model through the Host-Native lane using 'clip-input'. 3. Verify that the handshake protocol (Idle -> Ready -> Processing -> Done) completes."
| Dimension | Score | Rationale |
|-----------|-------|-----------|
| CLARITY | 0.5 | Three-step procedure with protocol state machine awareness. "Execute the model through the Host-Native lane" is jargon-heavy. Handshake protocol states are implementation details. |
| RESILIENCE | 0.6 | Has verification step (handshake check). No error handling for MLX unavailability, model not found, or slab creation failure. |
| SELF-CONTAINMENT | 0.4 | Creates its own slab in step 1, but requires MLXWorkerService infrastructure, `clip-vit` model weights, and XPC bridge. Heavy infrastructure dependency. |

### Prompt #78 — XPC hardening (infrastructure)
**Source:** `compose-pkl/docs/hermes-agent-backend-test-prompts-4.md` prompt #78
**Prompt text:** "Connect to the 'MLXWorkerService' Mach Service from an unsigned, unentitled process. Verify that the service rejects the connection with a code signing requirement failure."
| Dimension | Score | Rationale |
|-----------|-------|-----------|
| CLARITY | 0.8 | Very specific: connect from unsigned/unentitled process, verify specific rejection reason. Single clear action with expected outcome. |
| RESILIENCE | 0.8 | Expects a specific failure (rejection by code signing), explicit verification step. Strong negative test pattern. |
| SELF-CONTAINMENT | 0.5 | Requires MLXWorkerService to exist and be code-signed. Infrastructure prerequisite (macOS, XPC service). |

### Prompt #91 — concurrency (multi-container)
**Source:** `compose-pkl/docs/hermes-agent-backend-test-prompts-4.md` prompt #91
**Prompt text:** "Create a container, request a CLIP embedding, then kill the container before it reads the result. Verify that the slab is cleaned up and the next inference request starts from a fresh Idle state."
| Dimension | Score | Rationale |
|-----------|-------|-----------|
| CLARITY | 0.4 | Four-step sequence with conditional logic (kill before read). Ambiguous: which container? which slab? "Next inference request" from where? Multi-step with temporal dependency. |
| RESILIENCE | 0.7 | Tests crash recovery path explicitly. Good verification steps (slab cleanup, fresh Idle state). |
| SELF-CONTAINMENT | 0.3 | Requires running containers, MLX infrastructure, slab management, and CLIP model. Heavy infrastructure chain. |

---

## Summary Statistics

### Composite Scores (average of 3 dimensions)

| Prompt | CLARITY | RESILIENCE | SELF-CONTAINMENT | Composite |
|--------|---------|------------|------------------|-----------|
| #1 (create_container) | 0.6 | 0.3 | 0.9 | **0.60** |
| #7 (list_containers) | 0.9 | 0.9 | 1.0 | **0.93** |
| #16 (agentspec_create) | 0.7 | 0.2 | 0.9 | **0.60** |
| #33 (invalid image) | 0.7 | 0.4 | 0.8 | **0.63** |
| #48 (check_action) | 0.8 | 0.7 | 0.7 | **0.73** |
| #58 (analyze_traces) | 0.6 | 0.8 | 0.5 | **0.63** |
| #69 (create_slab) | 0.9 | 0.7 | 0.8 | **0.80** |
| #71 (execute_native_model) | 0.5 | 0.6 | 0.4 | **0.50** |
| #78 (XPC hardening) | 0.8 | 0.8 | 0.5 | **0.70** |
| #91 (concurrency) | 0.4 | 0.7 | 0.3 | **0.47** |

### Per-Dimension Range

| Dimension | Min | Max | Range | > 0.2? |
|-----------|-----|-----|-------|--------|
| CLARITY | 0.4 | 0.9 | **0.5** | ✅ YES |
| RESILIENCE | 0.2 | 0.9 | **0.7** | ✅ YES |
| SELF-CONTAINMENT | 0.3 | 1.0 | **0.7** | ✅ YES |

### Overall Composite Range

| Metric | Value |
|--------|-------|
| **Min composite score** | 0.47 (Prompt #91 — concurrency) |
| **Max composite score** | 0.93 (Prompt #7 — list_containers) |
| **Range** | **0.46** |
| **G0 gate criterion** | Range > 0.2 |

---

## G0 Gate Verdict

### ✅ PASS — Range 0.46 > 0.2

The 3-dimension rubric produces a **score range of 0.46** across 10 diverse prompts, which **exceeds the 0.2 G0 gate criterion**. The rubric demonstrates discriminative power:

- **High-scoring prompts** (#7 list_containers at 0.93, #69 create_slab at 0.80) are simple, single-action, with verification steps and no dependencies.
- **Low-scoring prompts** (#91 concurrency at 0.47, #71 execute_native_model at 0.50) are complex multi-step procedures with heavy infrastructure dependencies.

### Key Observations
1. **SELF-CONTAINMENT drives the widest spread** (0.3–1.0, range 0.7) — simple list/inspect ops score high, complex multi-step workflows score low.
2. **RESILIENCE also shows strong spread** (0.2–0.9, range 0.7) — prompts with explicit verification/error handling score high; those assuming tools exist score low.
3. **CLARITY has moderate spread** (0.4–0.9, range 0.5) — simple single-action prompts are clearer than multi-step procedural prompts.
4. **The rubric does not need redesign** — it successfully distinguishes between well-structured and poorly-structured prompts across all three dimensions.
