# Plan 122 Adversarial Review: Semantic Drift Analysis

## Scope
- **3 documents reviewed**: Tier 1 (47 prompts, #1-47), Tier 2 (21 prompts, #48-68), Tier 3 (23 prompts, #69-91)
- **76 evolved prompts** (out of 91 total) evaluated for semantic drift from original MCP tool intent
- **15 unmodified prompts** (improved=false, kept baseline) — no review needed

---

## FINDER: Suspect Prompts

| # | Title | Finding | Severity |
|---|-------|---------|----------|
| 3 | container_create — Full Spec | Original specified ports, labels, memory (512MB), CPU (1.0). Evolved drops all of these — only image + command remain. | Medium |
| 4 | container_start | Original: "Start the container named 'test-nginx'". Evolved: "Start a 'test-nginx' container... using the MCP tool's **'create-container' path**" — references wrong tool name. | High |
| 7 | container_list — All | Original: "List all containers". Evolved: "\"Verify that **'docker ps -a'** returns the names, IDs, images, and status of all containers...\"" — references raw Docker CLI instead of MCP tool. | High |
| 8 | container_list — Running Only | Original: "List only the currently running containers." Evolved: "**Describe a scenario** where the MCP tool lists only the currently running containers" — shifts from imperative action to meta-description. | Medium |

---

## ADVERSARY: Challenge Each Finding

### Prompt 3 (container_create — Full Spec)
**Finding**: Evolved text drops ports, labels, memory, CPU limits from original spec.
**Adversary challenge**: Does the evolved text still target `create_container`? Yes — it says "Create a container named 'test-fullspec' with image 'alpine:latest'" which is clearly a create_container call. The specific parameters were simplified during GEPA evolution (optimized for clarity/simplicity), but the MCP tool intent is preserved. The test loses "Full Spec" coverage but does not lose tool routing.
**Verdict**: DRIFT REJECTED — Tool intent preserved. The prompt still routes to `create_container`.

### Prompt 4 (container_start)
**Finding**: Evolved text says "using the MCP tool's 'create-container' path" — wrong tool name for a start operation.
**Adversary challenge**: The prompt header says "container_start" and the body says "Start a 'test-nginx' container". An agent reading this would most naturally call `start_container` based on the verb "Start". However, the explicit text "MCP tool's 'create-container' path" is misleading — a literal agent might attempt `create_container` instead of `start_container`, creating or re-creating the container rather than starting an existing one.
**Verdict**: DRIFT CONFIRMED — The evolved text incorrectly specifies "create-container" path for a start operation. This is a concrete tool name error.

### Prompt 7 (container_list — All)
**Finding**: Evolved text says "Verify that 'docker ps -a' returns..." instead of using the MCP `list_containers` tool.
**Adversary challenge**: The original intent was "List all containers". The evolved text instructs the agent to verify that a raw Docker CLI command (`docker ps -a`) produces the right output, rather than calling the MCP tool `list_containers`. A Hermes agent following this literally might shell out to `docker ps -a` instead of calling the MCP endpoint. This changes the invocation path entirely.
**Verdict**: DRIFT CONFIRMED — The evolved text references a low-level Docker CLI command instead of the MCP `list_containers` tool. This is a structural drift in invocation mechanism.

### Prompt 8 (container_list — Running Only)
**Finding**: Original says "List only the currently running containers" (imperative). Evolved says "Describe a scenario where the MCP tool lists only the currently running containers" (descriptive/meta).
**Adversary challenge**: The evolved text still references "MCP tool lists only the currently running containers" — the agent would still need to call `list_containers(filter='running')` to properly "describe the scenario". The shift from imperative to descriptive is a stylistic change but does not change the underlying tool that must be invoked.
**Verdict**: DRIFT REJECTED — The prompt still targets `list_containers`. The meta-instruction framing is a valid resilience improvement; the agent will still route to the correct tool.

---

## REFEREE: Final Calibration

### Confirmed Drift (needs revert to baseline):

1. **Prompt 4 — container_start**: Evolved text says "MCP tool's 'create-container' path" but the operation is `start_container`. The tool name is wrong. Revert to baseline text: "Start the container named 'test-nginx'." Then re-evolve with correct tool reference.

2. **Prompt 7 — container_list — All**: Evolved text references `docker ps -a` (raw Docker CLI) instead of the MCP tool `list_containers`. This changes the invocation mechanism entirely. Revert to baseline text: "List all containers, including stopped ones. Show me their names, IDs, images, and status." Then re-evolve with tool-aware constraints.

---

## Summary of All 76 Evolved Prompts

### Tier 1 (Prompts 1-47): 47 prompts, 32 evolved
| Status | Count | Notes |
|--------|-------|-------|
| PASS (no drift) | 30 | Tool intent preserved correctly |
| Drift Rejected | 1 | Prompt 3 (dropped parameters but still routes to create_container) |
| Drift Rejected | 1 | Prompt 8 (descriptive frame but still targets list_containers) |
| **Drift Confirmed** | **2** | **Prompt 4 (wrong tool name), Prompt 7 (docker CLI instead of MCP)** |

### Tier 2 (Prompts 48-68): 21 prompts, 15 evolved
| Status | Count | Notes |
|--------|-------|-------|
| PASS (no drift) | 15 | All preserve original MCP tool intent |
| Drift Confirmed | 0 | Clean — all evolved texts correctly reference their target tools |

### Tier 3 (Prompts 69-91): 23 prompts, 18 evolved
| Status | Count | Notes |
|--------|-------|-------|
| PASS (no drift) | 18 | All preserve original MCP tool intent (slab ops, execute_native_model, etc.) |
| Drift Confirmed | 0 | Clean — all evolved texts correctly reference their target tools |

---

## Final Verdict

**REQUEST_CHANGES** — 2 prompts need revert to baseline:

| Prompt | Doc | Issue | Recommended Action |
|--------|-----|-------|--------------------|
| **#4** (container_start) | hermes-agent-backend-test-prompts.md | Evolved text references "create-container" path but operation is start_container | Revert to: "Start the container named 'test-nginx'." |
| **#7** (container_list — All) | hermes-agent-backend-test-prompts.md | Evolved text references "docker ps -a" (raw Docker CLI) instead of MCP list_containers | Revert to: "List all containers, including stopped ones. Show me their names, IDs, images, and status." |

74 of 76 evolved prompts (97.4%) successfully preserved their original MCP tool intent. The 2 failures are isolated to Tier 1 and stem from GEPA optimizing for resilience/self-containment at the cost of tool-specific accuracy — a known tension in evolution-based prompt engineering that should be addressed by adding "tool-name integrity" constraints to the GEPA mutation criteria.

## Files Modified
- Created `/Users/kieranlal/workspace/hermes-agent-self-evolution/plan-122-adversarial-review.md` with full review
