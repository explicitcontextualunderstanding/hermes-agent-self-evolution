---
name: running-isaac-ros-tests
description: |
 Run and monitor isaac_ros_custom CI workflows on Jetson Orin Nanos (nano1/nano2).
 Progressive test ladder: infra -> smoke -> zot -> bag-pipeline -> contract -> gpu ->
 unit -> cuvslm -> integration -> e2e -> parity -> vlm -> vlm-nav2 -> vlm-memory-stress -> semantic-distillation -> golden-mcap -> multi-scenario-vlm. Use when:
 running CI tests, validating builds, debugging test failures, checking workflow
 status, or running the test ladder.
proficiency: 1.00
composition:
 after: [building-rs-humble, managing-k3s-cluster]
 before: [sentinel]
latent_vars:
- contains_gpu: true
- hardware_runner_type: jetson-orin-nano
- k3s_standalone: true
- memory_constraint_8gb: true
- argo_namespace: argo
- gh_actions_memory_overhead: true
---

proficiency: 1.00
composition:
  after: [building-rs-humble, managing-k3s-cluster]
  before: [sentinel]
latent_vars:
  - contains_gpu: true
  - hardware_runner_type: jetson-orin-nano
  - k3s_standalone: true
  - memory_constraint_8gb: true
  - argo_namespace: argo
---

## Overview

Run and monitor an 18-rung progressive test ladder on two standalone K3s clusters (nano1, nano2).
Argo Workflows orchestrates the DAG; results and failed rung lists persist to Zot as OCI artifacts.
Quick check mode re-runs only previously failed rungs.

## Triggers

Use this skill when: running CI tests, validating builds, debugging test failures, checking workflow
status, or running the test ladder.

## Prerequisites

- [ ] `gh` authenticated: `gh auth status`
- [ ] In isaac_ros_custom repo: `pwd`
- [ ] Required tools: `rg`, `jq`, `oras`
- [ ] Jetson runner available (nano1 or nano2)
- [ ] K3s cluster accessible: `kubectl get nodes`
- [ ] Zot reachable: `curl -sk https://192.168.100.1:30500/v2/_catalog`
- [ ] No ladder already running: `argo list -n argo --running`
- [ ] Socat bridges active (for build workflows with sentry sidecar): `lsof -i :8788 | grep socat`

## Quick Start

```bash
# Check for running workflows first
argo list -n argo --running

# Apply template (after edits)
kubectl apply -f k3s/argo-ladder-workflow.yaml -n argo

# Run Argo ladder on nano2
argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2

# Quick check (only previously failed rungs)
argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2 -p quick_check=true

# Watch progress
argo watch @latest -n argo
```

## Test Ladder

18 rungs, sequential, bottom-to-top. Stop on first failure.

**HITL Philosophy**: On Jetson Orin Nano, GPU, cuVSLAM, VLM, and Nav2 are mandatory capabilities.
Rungs 07, 09, 13, 14 **fail** (not skip) when capabilities are missing. Only rung 08 (unit tests)
skips gracefully when `unit-tests-enabled=false`.

| Rung | Level | Name                   | Purpose                                                       | Behavior                     |
| ---- | ----- | ---------------------- | ------------------------------------------------------------- | ---------------------------- |
| 01   | L1    | ARC Runner             | ARC controller/runner pod health, GitHub API reachability     | Always runs                  |
| 02   | L4    | K3s Infra              | kube-system pods, node Ready, GPU allocatable, ResourceQuota  | Always runs                  |
| 03   | L4    | K3s Smoke              | Pod scheduling, image capabilities                            | **Fails** if no librealsense |
| 04   | L4    | Zot/WASM               | Registry, Spegel, WASM                                        | Always runs                  |
| 05   | L4    | Bag Pipeline           | Rosbag replay from Zot                                        | Always runs                  |
| 06   | L5a   | ROS2 Contract          | Package discovery                                             | Always runs                  |
| 07   | L5b   | GPU Validation         | nvidia-smi, CUDA availability, GPU memory inside workload pod | **Fails** if no CUDA         |
| 08   | L5c   | Unit Tests             | colcon test                                                   | Skips if disabled            |
| 09   | L5d   | cuVSLAM Diagnostics    | VSLAM health                                                  | **Fails** if no visual_slam  |
| 10   | L5e   | Integration Tests      | Multi-node tests                                              | Always runs                  |
| 11   | L5f   | E2E Tests              | Full pipeline                                                 | Always runs                  |
| 12   | L5g   | Parity Tests           | Rosbag regression                                             | Always runs                  |
| 13   | L5h   | VLM Test               | Vision-language model validation                              | **Fails** if no VLM          |
| 14   | L5i   | VLM Nav2 Test          | VLM + Nav2 integration                                        | **Fails** if no Nav2         |
| 15   | L5j   | VLM Memory Stress      | Three-tier memory behavior under pressure                     | **Fails** if no VLM          |
| 16   | L5k   | Semantic Distillation  | Real semantic distiller with live ROS2 topics                 | **Fails** if no VLM          |
| 17   | L5l   | Golden MCAP Validation | Validates against Isaac Sim ground truth                      | **Fails** if no VLM          |
| 18   | L5m   | Multi-Scenario VLM     | Runs all 4 scenarios (office, warehouse, hallway, kitchen)    | **Fails** if no VLM          |

## File Index

| File           | Role                                                    |
| -------------- | ------------------------------------------------------- |
| `SKILL.md`     | Entry point (this file)                                 |
| `WORKFLOWS.md` | Command sequences, execution options                    |
| `REFERENCE.md` | Static data: ARC patterns, Zot artifacts, inputs        |
| `RECOVERY.md`  | Verifiable if-then recovery and bypass paths            |
| `EXAMPLES.md`  | Scenarios with verifiable expected outcomes             |
| `TACTICAL.md`  | Non-prescriptive strategic know-how from past incidents |

## Architectural Inconsistency (Hybrid Migration)

The ladder uses **two different execution patterns**:

| Rungs | Pattern                                  | Why?                                           |
| ----- | ---------------------------------------- | ---------------------------------------------- |
| 01-12 | Inline `kubectl exec` into rs_humble pod | Simple ROS2 checks (pkg list, pytest)          |
| 13-18 | Delegate via `gh workflow run`           | Complex VLM tests (MCAP replay, NIM API, Nav2) |

**The Problem:** Rungs 03, 06, 11 have **orphaned GitHub workflows** that exist but are NOT called
by the ladder:

- `4-layer-k3s-smoke.yml` (rung 03) — ladder does inline `kubectl run smoke-test`
## Error Handling Priority

1. L4 failure → Stop, do not proceed to L5
2. Preflight failed → Fix preflight before tests
3. Rung pod Pending → Check ResourceQuota, scale down workload pod
4. Rung pod CrashLoopBackOff → Check logs, verify image in Zot
5. **VLM rung OOM** → See VLM Memory Constraints section below
6. `pod deleted` error → Check for concurrent ladder runs, delete duplicates
7. Workflow `Pending` with mutex message → Another ladder is running, wait or delete
8. YAML indentation errors → Use `argo lint` before commit (block scalar content must be indented)
9. `gh workflow run` HTTP 422 → Check `workflow_dispatch.inputs` indentation (inputs must be
   children of `inputs:`, not siblings)

## Known Pitfalls (from live incidents)

### Jetson kubectl v1.28 Stricter YAML Parser

Mac's kubectl accepts block scalars with lines at column 0. Jetson's kubectl v1.28 rejects them
with `could not find expected ':'`. **Every line** inside a `|` block scalar MUST be indented
beyond the key's indentation level. This has bitten us in:

- `k3s/cluster-health-hook.yaml` — ConfigMap with shell script (lines 89-121 at column 0)
- `k3s/argo-ladder-workflow.yaml` — Rung 6 script (lines 1389-1417 dropped from 12-space to 0)

**Fix pattern**: Find unindented lines inside block scalars, re-indent to match surrounding context.
```bash
# Find column-0 lines inside a block scalar section (example: lines 88-293)
awk 'NR>=88 && NR<=293 && /^[^[:space:]]/' file.yaml
# Re-indent with Python execute_code (batch operation)
```

### Stale ConfigMaps vs Git Source

Deployed ConfigMaps can drift from the source YAML in git. The `ladder-shared-scripts` ConfigMap
had a stale `oras-setup.sh` with typo `orasm_${OS}` (404 on download). The source file
`k3s/workflows/_base/shared-scripts-configmap.yaml` was correct.

**Fix**: After editing shared script source files, always re-apply the ConfigMap:
```bash
ssh nano2-cmd 'kubectl apply -f k3s/workflows/_base/shared-scripts-configmap.yaml -n argo'
```

### Argo CRD Version Skew (nano1 vs nano2)

nano1's Argo CRD (installed 45 days ago) has stricter validation than nano2's:

| Field | nano1 (strict) | nano2 (permissive) |
|-------|----------------|-------------------|
| `ttlSecondsAfterFinished` | Unknown | Accepted |
| `successfulJobsHistoryLimit` | Unknown | Accepted |
| `container.nodeSelector` | Unknown (template-level only) | Accepted |
| Sidecar as standalone template with `args/command` | Unknown | Accepted |

**Correct Argo v4 fields**: Use `ttlStrategy.secondsAfterCompletion` + `podGC: OnPodCompletion`.
Do NOT use deprecated `ttlSecondsAfterFinished` or `failedJobsHistoryLimit` at WorkflowTemplate level.

### `start_at_rung` Does NOT Skip DAG Dependencies

Setting `-p start_at_rung=4` only makes rungs 1-3 exit early (return 0). The DAG still evaluates
each dependency sequentially. Expect rungs 1-3 to show as "Succeeded" with ~0s duration.

### Git `pull.rebase=true` Blocks on Dirty Trees

Both Jetson repos have `pull.rebase=true`. Rebase refuses to run with ANY dirty working tree,
even unstaged changes. Pattern:
```bash
git stash && git pull && git stash pop
```
If stash pop has merge conflicts, resolve manually then `git add` the resolved file.

## Architecture: Triple-Layer Logic Stack

This skill implements the **XSkill** tactical framework:

| Layer         | File                   | Purpose                                        |
| :------------ | :--------------------- | :--------------------------------------------- |
| **Metadata**  | `SKILL.md` (this file) | Agent Router triggers via latent variables     |
| **Reasoning** | `TACTICAL.md`          | Experience-based strategies for tool selection |
| **Execution** | `WORKFLOWS.md`         | Deterministic step-by-step procedures          |

## Validation Checklist

- [x] **Atomic Principle**: Focused on test ladder execution and monitoring
- [x] **Tactical Guidance**: `TACTICAL.md` has strategic know-how (concurrency, quota, hybrid
      pattern)
- [x] **Verifiable Result**: `argo get` shows per-rung pass/fail/skip status
- [x] **Recovery Path**: `RECOVERY.md` has if-then recovery per rung failure
- [x] **TTC Ready**: Ladder runs are sequential DAG; quick_check enables parallel rollout of failed
      rungs

## Related Skills

- `building-rs-humble` -- Build images before testing
- `managing-k3s-cluster` -- Cluster operations, pod debugging
- `testing-vlm` -- VLM pipeline tests (rungs 13-18): sanity, MCAP, Cloudflare Worker, Nav2 tool
  calling
- `sentinel` -- Self-healing diagnostics, stop-loss, metrics
- `managing-github-actions-runners` -- ARC runner lifecycle

---

_Updated March 2026 per AGENT_SKILLS.md standards (XSkill/CARL framework)._
