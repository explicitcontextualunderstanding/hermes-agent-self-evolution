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



# === SKILL.md ===

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


# === EXAMPLES.md ===

# EXAMPLES — running-isaac-ros-tests

## Example 0: Test MUST Run in rs_humble Workload Pod

**CRITICAL: ALL tests must exec into the rs_humble workload pod, NOT in Argo pods.**

The Argo ladder creates separate pods with `alpine:latest` - which has NO ROS packages, NO PyTorch,
NO CUDA. Any test running there is a false positive.

```bash
# Get the workload pod
POD_NAME=$(kubectl get pods -n isaac-ros -l app.kubernetes.io/component=workload \
  -o jsonpath='{.items[0].metadata.name}')

# Execute test inside workload pod
kubectl exec -n isaac-ros "$POD_NAME" -c isaac-ros -- bash -lc '
  source /opt/ros/humble/setup.bash
  ros2 pkg list | wc -l
'
```

**Verified VLM Tests (2026-04-08):** Direct exec in workload pod:

```bash
# Test qwen3.5-397b-a17b VLM
kubectl exec -n isaac-ros $POD -c isaac-ros -- python3 -c '
import os, requests
api_key = os.environ.get("NVIDIA_API_KEY", "")
resp = requests.post("https://integrate.api.nvidia.com/v1/chat/completions",
    headers={"Authorization": "Bearer " + api_key},
    json={"model": "qwen/qwen3.5-397b-a17b", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 10})
print(resp.json()["choices"][0]["message"]["content"])
'
```

**Results:**

- API key: `nvapi-UpJhz...` (from `build.nvidia.com` item via OnePasswordItem)
- Model: `qwen/qwen3.5-397b-a17b` ✅ (397B MoE VLM)
- Response: "Hello! How can I help you today?"

| **Verified ALL 18 rungs (2026-04-07):** | Rung                  | Test      | Result |
| --------------------------------------- | --------------------- | --------- | ------ |
| 00                                      | rs_humble + isaac_ros | ✅        |
| 02\*                                    | K3s Infra             | auto-skip |
| 03\*                                    | K3s Smoke             | auto-skip |
| 04                                      | Zot/WASM              | ✅        |
| 05                                      | Bag Pipeline          | ✅        |
| 06                                      | ROS2 Contract         | 462 pkgs  |
| 07                                      | GPU Validation        | Orin      |
| 08                                      | Unit Tests            | ✅        |
| 09                                      | cuVSLAM               | ✅        |
| 10                                      | Integration           | ✅        |
| 11                                      | E2E Tests             | ✅        |
| 12                                      | Parity Tests          | ✅        |
| 13                                      | VLM Sanity            | ✅        |
| 14                                      | VLM + Nav2            | ✅        |
| 15                                      | Memory                | ✅        |
| 16                                      | Semantic Distillation | ✅        |
| 17                                      | Multi-Scenario VLM    | ✅        |
| 18                                      | Golden MCAP           | ✅        |

- Auto-skip when kubectl unavailable

**Test Execution Patterns:**

| Rungs | Where        | Why                                  |
| ----- | ------------ | ------------------------------------ |
| 00    | Workload pod | Verify ROS/PyTorch/CUDA in container |
| 02-03 | Argo pod     | Test K3s infra (needs kubectl)       |
| 04+   | Workload pod | Test ROS packages, VLM, etc.         |

**Auto-skip pattern**: Ladder should detect environment and skip 02-03 when kubectl unavailable:

```bash
if command -v kubectl &>/dev/null; then
  echo "Running K3s infra tests..."
  kubectl get nodes
else
  echo "SKIP: rung 02-03 (kubectl not available in workload pod)"
fi
```

This allows tests to run anywhere: Argo pod (has kubectl), workload pod (no kubectl), or direct
exec - always runs what it can.

## Example 1: Verify Image Has Annotations Before Running Ladder

Always verify the image in Zot is complete before running the ladder — a broken build wastes ladder
execution time.

```bash
# Check if image has annotations (returns null if annotate-image step failed)
oras manifest fetch 192.168.100.1:30500/rs_humble:latest \
  | jq -r '.annotations["org.ros.isaac.cuda.enabled"]'

# Check for critical packages
oras manifest fetch 192.168.100.1:30500/rs_humble:latest \
  | jq -r '.annotations["org.ros.isaac.debs"]' | grep -o 'ros-humble-isaac-ros-visual-slam'

# If annotations are missing, rebuild the image first (don't run ladder on broken image)
```

**Expected**: `true` for cuda.enabled, actual package names for debs. If `null`, the build completed
but annotation step failed — the image is in Zot but tests won't work.

## Example 2: Full Argo Ladder on nano2

```bash
# Set correct kubeconfig for nano2
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Verify k3s is running and node is ready
kubectl get nodes

# Submit ladder
argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2

# Watch progress
argo watch @latest -n argo
```

**Expected**: All 14 rungs pass sequentially. Rungs 07, 09, 13, 14 fail if required capabilities
(GPU, cuVSLAM, VLM, Nav2) are missing — these are mandatory for HITL on Jetson Orin Nano. Results
pushed to `isaac-ros/ladder-results` in Zot.

**Note**: Rung 01 now skips if ARC isn't installed, warns if ARC exists but pods aren't Running. The
ladder continues regardless of ARC status.

## Example 2: Quick Check After Failures

```bash
# Previous run: rungs 6, 9, 12 failed
argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2 -p quick_check=true
```

**Expected**: Only runs rungs 6, 9, 12. Other rungs skipped. Full results still saved.

## Example 3: Debug Failing Rung

```bash
# Check which rung failed
argo get <workflow> -n argo

# Get pod name from argo output
POD=isaac-ros-ladder-xxx-rung-06-ros2-contract-yyy

# Get logs
kubectl logs $POD -n argo -c main | tail -30
```

**Expected**: Logs show which check failed and the error message.

## Example 4: Debug Exit Code 127 (command not found)

```bash
# Symptom: rung fails immediately with exit code 127
kubectl logs <pod> -n argo -c main | head -10
# Output: /bin/sh: curl: not found

# Fix: ensure apk add runs before the command
# In workflow YAML: apk add --no-cache curl jq kubectl >/dev/null 2>&1
```

## Example 5: Debug Rung 06 (ROS2 Contract) - apt vs ros2 pkg list

```bash
# Symptom: "No Isaac ROS packages found in workload pod"
kubectl logs <pod> -n argo -c main

# Diagnose: packages are colcon-built, not apt-installed
kubectl exec -n isaac-ros <workload-pod> -- bash -c "apt list --installed | grep isaac"
# (returns nothing)

kubectl exec -n isaac-ros <workload-pod> -- bash -c "
  source /opt/ros/humble/setup.bash
  source /workspaces/isaac_ros-dev/install/setup.bash
  ros2 pkg list | grep isaac"
# (returns isaac_ros_custom)
```

**Fix**: Change rung to use `ros2 pkg list` instead of `apt list --installed`.

## Example 6: Debug Rung 12 (Parity) - colcon test failures

```bash
# Failure 1: merged layout error
# "The install directory 'install' was created with the layout 'merged'"
# Fix: add --merge-install to colcon test command

# Failure 2: COLCON_CURRENT_PREFIX path mismatch
# "The build time path '/workspace-staging/install' doesn't exist"
# Fix: rebuild image in K3s buildah workflow (paths match at runtime)

# Failure 3: optional packages not in image
# "isaac_ros_visual_slam not found in package list"
# Fix: warn instead of fail for optional packages
```

## Example 7: Debug save-results - POSIX shell error

```bash
# Symptom: exit code 2, "syntax error: unexpected (""
# Cause: declare -A (bash associative array) in /bin/sh
# Fix: replace with case function:
rung_name() {
  case "$1" in
    01) echo "ARC Runner" ;;
    02) echo "K3s Infra" ;;
    # ...
  esac
}
```

## Example 8: Pull Results from Zot

```bash
oras repo ls 192.168.100.1:30500/isaac-ros/ladder-results
oras pull 192.168.100.1:30500/isaac-ros/ladder-results:<workflow> -o /tmp/results/
```

**Expected**: JSON results file with per-rung pass/fail/skipped status.

## Example 9: Check ResourceQuota Before Ladder

```bash
kubectl describe resourcequota -n isaac-ros
kubectl get pods -n isaac-ros
```

**Expected**: If quota is nearly full, scale down the persistent workload pod.

## Example 10: Find Working Image in Zot

When multiple tags exist, find the one with annotations:

```bash
# List all tags and check annotation
oras repo tags 192.168.100.1:30500/rs_humble
for tag in $(oras repo tags 192.168.100.1:30500/rs_humble); do
  result=$(oras manifest fetch 192.168.100.1:30500/rs_humble:$tag 2>/dev/null \
    | jq -r '.annotations["org.ros.isaac.cuda.enabled"]')
  if [ "$result" = "true" ]; then
    echo "Working image: $tag"
    break
  fi
done
```

**Expected**: `latest` usually has annotations. Tags like `nano1_bbe59fe75` may be partial builds
(completed but post-annotation failed).

## Example 11: Kubeconfig for Running Ladder on Different Nodes

The ladder runs on the target node specified by `-p target=`. Use the correct kubeconfig:

```bash
# For nano2 (local k3s)
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl get nodes  # Should show nano2 Ready

# Verify target node matches
argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2
```

**Important**: Running ladder on nano1 vs nano2 requires the kubecontext for that specific node. If
you get "connection refused", k3s isn't running on this machine.

## Example 12: Debug Rungs 13-14 Capability Failures

```bash
# Symptom: rungs 13-14 fail with "VLM pipeline is required for HITL testing"
argo get isaac-ros-ladder-xxx -n argo | grep rung-1[34]
# rung-13-vlm-test             5s    Error
# rung-14-vlm-nav2-test        6s    Error

# 5-6 seconds is a capability fail (detected missing annotation), not a test execution.
# Check the logs to confirm:
kubectl logs <rung-13-pod> -n argo -c main
# Output: "FAIL: vlm-provider=unknown — VLM pipeline is required for HITL testing"

kubectl logs <rung-14-pod> -n argo -c main
# Output: "FAIL: nav2-enabled=unknown — Nav2 is required for VLM+Nav2 HITL integration"
```

**Root cause chain** (4 days to debug, 3 sequential fixes):

1. **Wrong package name**: annotate-image checked for `nim_sender` instead of `isaac_ros_vlm`. Fix:
   update detection pattern.
2. **Source-built detection failed**: `ros2 pkg list` can't find colcon-built packages at build time
   because `colcon build` only runs at container runtime. Fix: fall back to checking
   `package.xml`/`setup.py` in the workspace source tree.
3. **Wrong Dockerfile**: Edited `Dockerfile.realsense` but the buildah workflow uses
   `Dockerfile.realsense.collapsed`. Fix: edit the collapsed variant.
4. **Source not in image**: The Dockerfile only `COPY src/isaac_ros_custom` — `isaac_ros_vlm` was
   never copied into the image. Fix: add `COPY src/isaac_ros_vlm`.

**Verification** after each fix:

```bash
# Check annotations on the pushed image
skopeo inspect --tls-verify=false --raw docker://192.168.100.1:30500/rs_humble:latest \
  | jq '.annotations | with_entries(select(.key | startswith("org.ros.isaac")))'

# After fix, should show:
# "org.ros.isaac.vlm.provider": "isaac_ros_vlm"
# "org.ros.isaac.nav2.enabled": "nav2_bringup"

# Then re-run ladder and verify rungs 13-14 take longer than 10 seconds
# (a capability fail exits in ~5s; a real test runs for minutes)
```

## Example 13: Debug VLM Rung OOM

**Note**: VLM model (Qwen 3.5 400B) runs on `build.nvidia.com`, not locally. OOM comes from MCAP
replay.

```bash
kubectl top nodes
kubectl scale deployment -n arc-systems --all --replicas=0
```

**Expected**: With ARC runners scaled down, the Jetson has more memory for MCAP replay and ROS2
nodes.

## Example 13b: Free Memory for VLM Tests (Rungs 13-14)

**Auto check**: The ladder now automatically checks memory in `pre-vlm-resources`. If <2GB free, it
prints:

```
⚠️  WARNING: Low memory for VLM tests!
  Required: 2048MB free
  Available: ~1500MB free

=== RECOMMENDATIONS ===
To free ~2048MB for VLM tests, run:
  kubectl scale deployment gha-rs-controller --replicas=0 -n arc-systems --context=nano2
  kubectl scale deployment nano2-workload-isaac-ros-workload-nano2 --replicas=0 -n isaac-ros --context=nano2
```

The ladder continues with a warning but VLM tests will fail. Manual free recommended.

## Example 13c: Manual Memory Free Before VLM

Before running VLM rungs 13-14, free up memory by scaling down ARC controller and workload pods:

```bash
# Check current memory (should show ~6GB used out of 7.6GB)
tegrastats | head -1

# Scale down ARC controller (~500MB freed)
kubectl scale deployment gha-rs-controller --replicas=0 -n arc-systems --context=nano2

# Scale down workload pod (~800MB freed)
kubectl scale deployment nano2-workload-isaac-ros-workload-nano2 --replicas=0 -n isaac-ros --context=nano2

# Verify memory freed
tegrastats | head -1

# Run ladder starting at rung 13
argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2 -p start_rung=13

# After VLM testing, restore pods
kubectl scale deployment gha-rs-controller --replicas=1 -n arc-systems --context=nano2
kubectl scale deployment nano2-workload-isaac-ros-workload-nano2 --replicas=1 -n isaac-ros --context=nano2
```

**Expected**: Memory available increases from ~1.5GB to ~2.7GB. VLM tests run without OOM. Exit code
143 (SIGTERM) indicates K8s killed the pod due to memory limit — increase the memory limit in the
workflow template or free more memory before testing.

## Example 14: Debug Rung 01 (ARC Runner Health) - No Controller Pod

```bash
# Symptom: "FAIL: No ARC controller pod found in arc-systems namespace"
argo get isaac-ros-ladder-xxx -n argo | grep rung-01
kubectl logs <rung-01-pod> -n argo -c main | tail -10

# Diagnose: ARC controller not installed or namespace missing
kubectl get ns arc-systems
kubectl get pods -n arc-systems

# Fix: reinstall ARC controller
helm upgrade --install arc-controller \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller \
  -n arc-systems --create-namespace
```

**Expected**: After reinstall, wait 30-60s for the controller pod to reach Running, then resubmit.

## Example 15: Debug Rung 02 (K3s Infra) - CrashLoopBackOff in kube-system

```bash
# Symptom: "FAIL: kube-system pods not Running" or "FAIL: CrashLoopBackOff detected"
kubectl logs <rung-02-pod> -n argo -c main | grep FAIL

# Identify the crashing pod
kubectl get pods -n kube-system | grep -v Running

# Check events and logs
kubectl describe pod <crashing-pod> -n kube-system
kubectl logs <crashing-pod> -n kube-system --previous

# Common cause: resource exhaustion — scale down workload pods
kubectl scale deployment -n isaac-ros --all --replicas=0
kubectl scale deployment -n arc-systems --all --replicas=0
```

**Expected**: After freeing resources, kube-system pods recover within 60s.

## Example 16: Debug Rung 07 (GPU Validation) - nvidia-smi Fails Inside Container

```bash
# Symptom: capability gate passes (cuda-enabled=true) but nvidia-smi fails
kubectl logs <rung-07-pod> -n argo -c main | grep FAIL

# Manually test GPU access in the workload pod
POD=$(kubectl get pods -n isaac-ros -l app.kubernetes.io/component=workload -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n isaac-ros $POD -- nvidia-smi

# If "command not found": nvidia-container-toolkit not in image
# If "NVIDIA-SMI has failed": GPU device not mapped to pod

# Check GPU resource request on the workload pod
kubectl get pod $POD -n isaac-ros -o jsonpath='{.spec.containers[0].resources.limits}'
# Should include nvidia.com/gpu: "1"
```

**Expected**: If GPU is missing from limits, the workload deployment needs a GPU resource request.
If nvidia-smi is missing from the image, the Dockerfile needs nvidia-container-toolkit.

## Example 17: Start Ladder from Specific Rung

Skip early rungs and start testing from a specific rung (e.g., after fixing a failure in rung 5):

```bash
# Start from rung 6 (ROS2 Contract), skip rungs 1-5
argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2 -p start_rung=6

# Start from rung 13 (VLM Test), skip all earlier rungs
argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2 -p start_rung=13
```

**Expected**: Only runs from the specified rung onward. Earlier rungs exit with "Skipping rung N
(start_rung=X)" message.

**Use cases**:

- Quick iteration after fixing a specific rung failure
- Run only VLM-related tests (rungs 13-18) without running infrastructure tests
- Resume from a specific rung after manual intervention

**Combined with quick_check**:

```bash
# Start from rung 6 AND only run failed rungs from previous run
argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2 -p start_rung=6 -p quick_check=true
```

## Example 18: Start Rung Names (Alternative to Numbers)

Use named rungs instead of numbers for clarity:

```bash
# Start from "ros2_contract" (rung 6)
argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2 -p start_rung_name=ros2_contract

# Start from "vlm_test" (rung 13)
argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2 -p start_rung_name=vlm_test
```

**Rung names mapping**:

- `arc_runner` → rung 1
- `k3s_infra` → rung 2
- `k3s_smoke` → rung 3
- `zot_wasm` → rung 4
- `bag_pipeline` → rung 5
- `ros2_contract` → rung 6
- `gpu_validation` → rung 7
- `unit_tests` → rung 8
- `cuvslam_diagnostics` → rung 9
- `integration_tests` → rung 10
- `e2e_tests` → rung 11
- `parity_tests` → rung 12
- `vlm_test` → rung 13
- `vlm_nav2` → rung 14
- `vlm_memory_stress` → rung 15
- `semantic_distillation` → rung 16
- `golden_mcap` → rung 17
- `multi_scenario_vlm` → rung 18

## Known Issues & False Positive Risks

| Rung   | Issue                                            | Impact                              |
| ------ | ------------------------------------------------ | ----------------------------------- |
| 08     | "Ran 0 tests" - package builds but no tests      | Weak coverage, not false positive   |
| 09     | `ros2 component list` empty - requires camera/HW | Passes without verifying cuVSLAM    |
| **13** | **Checks build.nvidia.com web UI, NOT VLM API**  | **FALSE POSITIVE** - wrong endpoint |
| 17-18  | May require sensor HW                            | Likely passes without HW            |

**FIXED Rung 13: Proper VLM API Test**

```bash
# Test actual VLM model endpoint (not web UI!)
curl -sSf https://integrate.api.nvidia.com/v1/models \
  -H "Authorization: Bearer $NVIDIA_API_KEY"

# Returns 260+ available models including:
# - nvidia/llama-3.1-nemotron-70b-instruct
# - meta/llama-3.1-405b-instruct
# - moonshotai/kimi-k2.5
```

Verified: API key available in workload pod, returns full model list.


# === RECOVERY.md ===

# RECOVERY — running-isaac-ros-tests

Verifiable if-then recovery and bypass paths.

## Rung Failures by Exit Code

### Exit 127 (command not found)

**Symptom**: Rung fails immediately with `main: Error (exit code 127)`, or logs show
`/bin/sh: kubectl: not found` followed by a capability-based skip.

**Cause**: `apk add` failed silently (network timeout to Alpine repos). The old pattern
`apk add ... >/dev/null 2>&1` hid the failure. The rung then hit `kubectl: not found` and the
ConfigMap read fell back to `"unknown"`, triggering a skip instead of running the test.

**Inspect**:

```bash
kubectl logs <pod-name> -n argo -c main | grep -E 'kubectl: not found|apk add|failed'
```

**Recovery**: The workflow now uses `_install_pkgs()` with 3 retries and explicit failure. If a rung
still shows this pattern, check Alpine repo connectivity from the Jetson:

```bash
kubectl run -n argo debug --image=alpine:latest --rm -it --restart=Never -- apk add --no-cache curl kubectl
```

**If using `alpine:3.18`**: Switch to `alpine:latest`. The default repos on 3.18 may not have all
packages needed by other rungs.

### Exit 1 (general failure)

**Diagnose**:

```bash
kubectl logs <pod-name> -n argo -c main | tail -30
```

**Common patterns**:

| Log message                            | Cause                                 | Recovery                                   |
| -------------------------------------- | ------------------------------------- | ------------------------------------------ |
| `No Isaac ROS packages found`          | `apt list` instead of `ros2 pkg list` | See Rung 06 below                          |
| `visual_slam package NOT discoverable` | Package not in this image variant     | Rebuild image with cuVSLAM (HITL required) |
| `colcon test: layout 'merged'`         | Missing `--merge-install` flag        | Add `--merge-install` to colcon test       |
| `COLCON_CURRENT_PREFIX`                | Build-time path mismatch              | Warn and continue; rebuild in K3s          |
| `Image not found (HTTP 404)`           | Image missing from Zot                | Run buildah workflow first                 |
| `syntax error: unexpected "("`         | Bashism in `/bin/sh` script           | Replace `declare -A` with `case` function  |
| `connection refused`                   | Plain HTTP to HTTPS port              | Use `https://` with `-sk` flag             |

### Exit 2 (syntax error)

**Cause**: Shell syntax error — usually bashisms in `/bin/sh` (alpine default).

**Fix**: Replace `declare -A`, `[[ ]]`, arrays with POSIX equivalents.

## Per-Rung Recovery

### Rung 03 (K3s Smoke): `realsense-enabled=false/unknown`

**Cause**: The rs_humble image was built without librealsense. This is an L4 smoke test failure —
librealsense should always be present in the rs_humble image regardless of which node runs the
ladder (both nano1 and nano2 support rosbag replay, and librealsense is needed for any camera work).

**Recovery**: Rebuild the image with the buildah workflow and verify the annotation:

```bash
skopeo inspect --tls-verify=false --raw docker://192.168.100.1:30500/rs_humble:latest \
  | jq -r '.config.Annotations // (.annotations // {})' | grep realsense
```

Should show `org.ros.isaac.hardware.camera`. If missing, check the annotate-image step in the
buildah workflow.

### Rung 06 (ROS2 Contract): `No Isaac ROS packages found`

**Cause**: Using `apt list --installed | grep isaac` but packages are colcon-built from source.

**Recovery**:

```bash
kubectl exec -n isaac-ros <pod> -- bash -c "
  source /opt/ros/humble/setup.bash
  source /workspaces/isaac_ros-dev/install/setup.bash
  ros2 pkg list | grep isaac
"
```

### Rung 07 (GPU Validation): nvidia-smi fails inside container

**Symptom**: Rung 07 passes the capability gate (`cuda-enabled=true`) but fails when running
`nvidia-smi` inside the workload pod.

**Cause (Jetson K3s)**: On Jetson Orin Nano running K3s, GPU access requires both:

1. Device nodes (`/dev/nvidia*`) mounted as hostPath volumes
2. CUDA libs from `/usr/local/nvidia` mounted

The CUDA libs are already mounted (see `cuda-libs`, `cuda-libs-tegra`, `cuda-toolkit` in
deployment), but the device nodes are NOT mapped.

**Recovery (Jetson K3s)**:

Add device hostPath volumes to `k3s/base/03-deployment.yaml` under the pod's volumes:

```yaml
volumes:
  - name: nvidia-devices
    hostPath:
      path: /dev
      type: Directory
```

And mount to container:

```yaml
volumeMounts:
  - name: nvidia-devices
    mountPath: /dev
```

Alternatively (if full GPU access needed), edit the pod spec to add `nvidia.com/gpu` resource and
ensure the K3s node has the nvidia-device-plugin running:

```bash
kubectl get node <node> -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'
# Should return "1" not "0"
```

**Standard Recovery**:

```bash
# 1. Check workload pod has GPU resource request
kubectl get pod -n isaac-ros -l app.kubernetes.io/component=workload -o jsonpath='{.items[0].spec.containers[0].resources.limits}' | grep gpu

# 2. Check NVIDIA device plugin is running
kubectl get pods -n kube-system | grep nvidia

# 3. Verify GPU allocatable on the node
kubectl get node nano2 -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'

# 4. Manually test nvidia-smi in the pod
kubectl exec -n isaac-ros <workload-pod> -- nvidia-smi
```

### Rung 07 (GPU Validation): CUDA not available despite nvidia-smi working

**Symptom**: `nvidia-smi` reports GPU correctly but the CUDA availability check (PyTorch or ctypes)
fails.

**Cause**: The image has `nvidia-container-toolkit` but PyTorch was not built with CUDA support, or
`libcudart.so` is not on the library path.

**Recovery**:

```bash
# Check for CUDA libraries in the container
kubectl exec -n isaac-ros <workload-pod> -- find / -name 'libcudart.so*' 2>/dev/null

# Check PyTorch CUDA build
kubectl exec -n isaac-ros <workload-pod> -- python3 -c "import torch; print(torch.cuda.is_available())"
```

**Fix**: Rebuild the image with CUDA-enabled PyTorch or ensure `LD_LIBRARY_PATH` includes the CUDA
lib directory.

### Rung 01 (ARC Runner): No ARC controller pod found

**Symptom**: `FAIL: No ARC controller pod found in arc-systems namespace`.

**Cause**: ARC controller was never installed, was uninstalled, or the Helm release was deleted.

**Recovery**:

```bash
# Check if arc-systems namespace exists
kubectl get ns arc-systems

# Check for ARC controller pods
kubectl get pods -n arc-systems

# Reinstall if missing
helm upgrade --install arc-controller \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller \
  -n arc-systems --create-namespace
```

### Rung 01 (ARC Runner): Runner pod not Running

**Symptom**: `FAIL: Runner pod (nano1-xxx-runner-yyy) is in phase Pending` or `CrashLoopBackOff`.

**Cause**: Runner pod can't schedule (ResourceQuota full) or is crashing (bad registration token).

**Recovery**:

```bash
# Check why runner is not Running
kubectl describe pod <runner-pod> -n arc-systems | tail -20

# If Pending: check ResourceQuota
kubectl describe resourcequota -n arc-systems

# If CrashLoopBackOff: check runner logs
kubectl logs <runner-pod> -n arc-systems --previous
```

### Rung 02 (K3s Infra): kube-system pods not Running

**Symptom**: `FAIL: kube-system pods not Running` listing non-Running pods.

**Cause**: Core K3s components (coredns, traefik, metrics-server) are in CrashLoopBackOff or
Pending. Usually caused by node resource exhaustion or failed K3s upgrade.

**Recovery**:

```bash
# Identify the problematic pods
kubectl get pods -n kube-system

# Check pod events
kubectl describe pod <pod-name> -n kube-system

# If CrashLoopBackOff: check logs
kubectl logs <pod-name> -n kube-system --previous

# If resource pressure: scale down workload
kubectl scale deployment -n isaac-ros --all --replicas=0
kubectl scale deployment -n arc-systems --all --replicas=0
```

### Rung 02 (K3s Infra): Target node not Ready

**Symptom**: `FAIL: Target node (nano2) is not Ready (status: Unknown)`.

**Cause**: Node lost network connectivity or K3s agent crashed.

**Recovery**:

```bash
# Check node status
kubectl describe node nano2 | grep -A5 Conditions

# If node is NotReady, SSH in and restart K3s
systemctl restart k3s
```

### Rung 02 (K3s Infra): No GPU allocatable on target node

**Symptom**: `FAIL: No GPU allocatable found on nano2`.

**Cause**: NVIDIA device plugin not running, or node was provisioned without GPU support.

**Recovery**:

```bash
# Check for NVIDIA device plugin
kubectl get pods -n kube-system | grep nvidia-device-plugin

# Check node capacity
kubectl get node nano2 -o jsonpath='{.status.capacity.nvidia\.com/gpu}'

# If missing: install the device plugin
kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.14.5/deployments/static/nvidia-device-plugin.yml
```

### Rung 09 (cuVSLAM): `visual_slam NOT discoverable`

**Cause**: The image was built without cuVSLAM, or the package failed to compile.

**Recovery**: cuVSLAM is mandatory for HITL on Jetson Orin Nano. Rebuild the image with cuVSLAM
included. Verify the buildah workflow completed successfully and `isaac_ros_visual_slam` compiled.

### Rung 12 (Parity Tests): colcon test failures

**Three known failure modes**:

1. **Missing `--merge-install`**:

   ```
   ERROR:colcon:colcon test: The install directory 'install' was created with the layout 'merged'
   ```

   Fix: Add `--merge-install` to `colcon test` command.

2. **Build-time path mismatch**:

   ```
   The build time path "/workspace-staging/install" doesn't exist.
   ```

   Fix: Rebuild image inside K3s buildah workflow so paths match at runtime. Temporary: detect this
   pattern and warn instead of failing.

3. **Optional packages missing** (`isaac_ros_visual_slam`, `isaac_ros_nvblox`): Fix: Change Test 2
   to warn instead of fail. Real gate is colcon test (Test 6).

### Rung 13 (VLM): capability fail — VLM pipeline missing

**Cause**: VLM provider is "unknown" (annotation missing or detection failed). This is a FAIL, not a
skip — VLM is required for HITL on Jetson Orin Nano.

**Recovery**: Fix the annotation detection in the buildah workflow (see "annotation detection
returns null" below). Verify `isaac_ros_vlm` source is in the Dockerfile and compiled.

### Rung 13 (VLM): annotation detection returns null despite source-existing

**Cause**: The `isaac_ros_vlm` source was never copied into the Docker image, so the `package.xml`
fallback check also fails. Three sub-causes found across 4 days:

1. **Wrong detection package**: Annotated checked for `nim_sender` instead of `isaac_ros_vlm`.
2. **Source-built packages invisible at build time**: `ros2 pkg list` can't find packages that
   haven't been `colcon build`-ed yet. Fix: fall back to `package.xml`/`setup.py` check.
3. **Edited wrong Dockerfile**: The buildah workflow uses `Dockerfile.realsense.collapsed`, not
   `Dockerfile.realsense`. Edits to the non-collapsed variant have no effect.

**Recovery**:

```bash
# 1. Verify which Dockerfile the workflow uses
grep DOCKERFILE k3s/buildah-workflow.yaml
# Should show: docker/Dockerfile.realsense.collapsed

# 2. Check if source is in the collapsed Dockerfile
grep isaac_ros_vlm docker/Dockerfile.realsense.collapsed
# Should show: COPY src/isaac_ros_vlm ...

# 3. Verify annotations on the pushed image after a successful build
skopeo inspect --tls-verify=false --raw docker://192.168.100.1:30500/rs_humble:latest \
  | jq '.annotations | to_entries[] | select(.key | startswith("org.ros.isaac.vlm"))'

# 4. If null, check the annotate-image step logs for the detection attempt
kubectl logs <annotate-pod> -n isaac-ros -c main | grep -i vlm

# 5. If annotation is correct but rung still skips, check the ConfigMap
kubectl get configmap image-capabilities -n argo -o jsonpath='{.data.vlm-provider}'
```

### Rung 14 (VLM Nav2): same as rung 13

Nav2 is required for HITL. If `nav2-enabled=unknown/false`, the rung fails — rebuild the image with
Nav2 packages included.

### save-results: `syntax error: unexpected "("`

**Cause**: `declare -A` associative array in `/bin/sh`.

**Fix**: Replace with POSIX-compatible case function:

```sh
rung_name() {
  case "$1" in
    01) echo "ARC Runner" ;;
    02) echo "K3s Infra" ;;
    # ... etc
  esac
}
```

### manifest-gate: skopeo image pull failure

**Cause**: `quay.io/skopeo/stable:v1.14` can't be pulled on Jetson (network/firewall).

**Fix**: Replace skopeo container with `alpine:latest` + `curl` + `jq`:

```bash
HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" "https://$REGISTRY/v2/$IMAGE/manifests/$TAG")
ANNOTATIONS=$(curl -sk -H "Accept: application/vnd.oci.image.manifest.v1+json" \
  "https://$REGISTRY/v2/$IMAGE/manifests/$TAG" | jq -r '.config.Annotations // (.annotations // {})')
```

### Preflight: HTTP to HTTPS port

**Cause**: `curl http://$REGISTRY/v2/` but Zot external port is HTTPS via nginx.

**Fix**: Change all `http://` to `https://` with `-sk` flags.

## ConfigMap Drift

### image-capabilities key mismatch

If manifest-gate writes `realsense-enabled` but save-results reads `hardware-camera`, the
save-results step shows "unknown" for RealSense.

**Fix**: Ensure ConfigMap key names are consistent between writer and all readers.

### ConfigMap not found (quick check)

```bash
kubectl get configmap ladder-failed-rungs -n argo
kubectl get configmap ladder-rung-status -n argo
```

If missing, the previous run didn't create them — quick check will fall back to full run.

## VLM Test Issues (Rungs 13-14)

For detailed VLM recovery procedures, see `testing-vlm` skill.

### Rung 13 (VLM) OOM

**Note**: The VLM model (Qwen 3.5 400B, 400 Billion parameters) runs remotely on `build.nvidia.com`
via NVIDIA NIM API with token — it does NOT run locally on the 8GB Jetson. OOM on VLM rungs is from
MCAP replay + ROS2 nodes, not the model.

```bash
kubectl top nodes
kubectl scale deployment -n arc-systems --all --replicas=0
```

**Expected**: With ARC runners scaled down, the Jetson has more memory for MCAP replay and ROS2
nodes.

### Rung 14 (VLM Nav2) fails

```bash
kubectl exec -n isaac-ros <workload-pod> -- curl -s http://nim-endpoint:8000/health
```

NIM health check must run inside the workload pod — ARC runner can't reach it.


# === REFERENCE.md ===

# REFERENCE — running-isaac-ros-tests

## Two Independent K3s Clusters

| Cluster | Hostname | Hardware                | Purpose               |
| ------- | -------- | ----------------------- | --------------------- |
| nano1   | nano1    | rosbag-only (no camera) | Primary test runner   |
| nano2   | nano2    | realsense-camera (D435) | Secondary test runner |

Not joined — each runs its own K3s, Argo, and ARC. Both run all 18 rungs. Camera is movable. Both
nodes support ROSBAG replay.

## Test Rung Zero: Verify rs_humble + isaac_ros Image

**CRITICAL: Rung 0 (preflight) MUST verify we're running in the correct container image.**

This is the gate - ALL other tests depend on being in the rs_humble container.

```bash
POD_NAME=$(kubectl get pods -n isaac-ros -l app.kubernetes.io/component=workload \
  -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n isaac-ros "$POD_NAME" -c isaac-ros -- bash -lc '
  set -e

  # Check 1: ROS Humble exists
  test -f /opt/ros/humble/setup.bash || { echo "FAIL: No ROS Humble"; exit 1; }
  source /opt/ros/humble/setup.bash

  # Check 2: ros2 CLI works
  which ros2 || { echo "FAIL: No ros2 CLI"; exit 1; }

  # Check 3: PyTorch + CUDA (requires libnvtoolsext1)
  python3 -c "import torch; assert torch.cuda.is_available(), 'CUDA false'" || { echo "FAIL: CUDA unavailable"; exit 1; }

  # Check 4: isaac_ros packages installed
  ls /workspaces/isaac_ros-dev/install/lib/python*/site-packages/isaac_ros_custom && echo "PASS: rs_humble + isaac_ros verified"
'
```

| Check            | Failure                   | Root Cause                       |
| ---------------- | ------------------------- | -------------------------------- |
| ROS Humble       | Missing `/opt/ros/humble` | Wrong base image                 |
| ros2 CLI         | `not found`               | ROS not sourced or broken build  |
| CUDA             | False                     | Missing `libnvtoolsext1` package |
| isaac_ros_custom | Not found                 | Workspace not built              |

**Historical note**: Before this fix, tests ran in Argo's `alpine:latest` pods - all results were
false positives.

## Argo Orchestration

- **Kind**: WorkflowTemplate (`k3s/argo-ladder-workflow.yaml`)
- **Template name**: `isaac-ros-ladder`
- **Namespace**: `argo`
- **Service Account**: `argo-server`
- **Submit**: `argo submit --from workflowtemplate/isaac-ros-ladder -n argo`

## Test Ladder Rungs (18)

| Rung | Level | Name                   | Purpose                                                                          | Skips when                                                              |
| ---- | ----- | ---------------------- | -------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| 01   | L1    | ARC Runner             | ARC controller pod Running, runner pod Running, GitHub API reachable             | Never                                                                   |
| 02   | L4    | K3s Infra              | kube-system pods Running, target node Ready, GPU allocatable, ResourceQuota      | Never                                                                   |
| 03   | L4    | K3s Smoke              | Pod scheduling, image capability validation                                      | **Fails** if `realsense-enabled=false/unknown` (librealsense missing)   |
| 04   | L4    | Zot/WASM               | Registry, Spegel, WASM                                                           | Never                                                                   |
| 05   | L4    | Bag Pipeline           | Rosbag replay from Zot                                                           | Never                                                                   |
| 06   | L5a   | ROS2 Contract          | Package discovery via `ros2 pkg list`                                            | Never                                                                   |
| 07   | L5b   | GPU Validation         | `nvidia-smi` inside pod, CUDA availability (PyTorch/ctypes), GPU memory reported | **Fails** if `cuda-enabled=false/unknown` (GPU mandatory on Jetson)     |
| 08   | L5c   | Unit Tests             | colcon test                                                                      | `unit-tests-enabled=false`                                              |
| 09   | L5d   | cuVSLAM Diagnostics    | VSLAM health                                                                     | **Fails** if `visual_slam` not discoverable (HITL required)             |
| 10   | L5e   | Integration Tests      | Multi-node tests                                                                 | Never                                                                   |
| 11   | L5f   | E2E Tests              | Full pipeline                                                                    | Never                                                                   |
| 12   | L5g   | Parity Tests           | colcon test + package validation                                                 | Never                                                                   |
| 13   | L5h   | VLM Test               | Vision-language model validation                                                 | **Fails** if `vlm-provider=none/null/unknown` (VLM required for HITL)   |
| 14   | L5i   | VLM Nav2 Test          | VLM + Nav2 integration                                                           | **Fails** if `nav2-enabled=false/none/unknown` (Nav2 required for HITL) |
| 15   | L5j   | VLM Memory Stress      | Three-tier memory behavior under pressure                                        | **Fails** if `vlm-provider=none/null/unknown` (VLM required for HITL)   |
| 16   | L5k   | Semantic Distillation  | Real semantic distiller with live ROS2 topics                                    | **Fails** if `vlm-provider=none/null/unknown` (VLM required for HITL)   |
| 17   | L5l   | Golden MCAP Validation | Validates against Isaac Sim ground truth                                         | **Fails** if `vlm-provider=none/null/unknown` (VLM required for HITL)   |
| 18   | L5m   | Multi-Scenario VLM     | Runs all 4 scenarios (office, warehouse, hallway, kitchen)                       | **Fails** if `vlm-provider=none/null/unknown` (VLM required for HITL)   |

### VLM Architecture

Rungs 13-18 use VLM models hosted on **NVIDIA NIM API** (`https://integrate.api.nvidia.com/v1/`).
Access requires **NVIDIA_API_KEY** environment variable.

Test the API:

```bash
curl -sf https://integrate.api.nvidia.com/v1/models \
  -H "Authorization: Bearer $NVIDIA_API_KEY"
```

**Working Large Models (verified with current API key):** | Model | Parameters |
|-------|-----------| | `meta/llama-3.1-405b-instruct` | 405B ✅ | | `qwen/qwen3.5-397b-a17b` | 397B
✅ | | `nvidia/llama-3.1-nemotron-70b-instruct` | ❌ (not available) |

The VLM model does **NOT** run locally on the 8GB Jetson - the Jetson sends frames via NIM API;
memory pressure comes from MCAP replay and ROS2 nodes.

### DAG Helper Tasks

| Task                 | Purpose                                             |
| -------------------- | --------------------------------------------------- |
| `preflight-check`    | Validates 8 prerequisites before all rungs          |
| `fetch-failed-rungs` | Loads failed rungs from Zot (quick_check mode only) |
| `health-check`       | Memory, stale pods, buildah cache, pending pods     |
| `manifest-gate`      | OCI manifest validation via curl+jq (not skopeo)    |
| `save-results`       | Persists results to Zot + ConfigMap summary         |
| `sentinel-diagnose`  | Conditional diagnosis after rung-12                 |

## Container Image Conventions

| Convention        | Value                           | Why                                          |
| ----------------- | ------------------------------- | -------------------------------------------- |
| Base image        | `alpine:latest`                 | All rungs use same image for consistency     |
| Tools installed   | `curl kubectl jq` via `apk add` | Must install before first use                |
| Shell             | `/bin/sh` (POSIX)               | NOT bash — no `declare -A`, `[[ ]]`          |
| Zot access        | `https://` with `-sk` flag      | External port is HTTPS via nginx             |
| Package discovery | `ros2 pkg list`                 | Packages are colcon-built, not apt-installed |
| colcon test       | `--merge-install` flag required | Install dir uses merged layout               |

## ConfigMaps

| ConfigMap             | Writer             | Readers                                    | Purpose                        |
| --------------------- | ------------------ | ------------------------------------------ | ------------------------------ |
| `image-capabilities`  | manifest-gate      | rungs 03, 07, 08, 09, 13, 14, save-results | CUDA/VLM/Nav2/realsense flags  |
| `ladder-rung-status`  | Each rung          | save-results, sentinel                     | Per-rung pass/fail/skipped     |
| `ladder-health-check` | health-check       | (informational)                            | Memory, pending pods, cache    |
| `ladder-failed-rungs` | fetch-failed-rungs | Each rung (quick_check)                    | List of rung numbers to re-run |
| `ladder-summary`      | save-results       | (informational)                            | Markdown summary for display   |

## image-capabilities Keys

| Key                  | Source annotation               | Used by               |
| -------------------- | ------------------------------- | --------------------- |
| `cuda-enabled`       | `org.ros.isaac.cuda.enabled`    | rung 07               |
| `realsense-enabled`  | `org.ros.isaac.hardware.camera` | rung 03, save-results |
| `unit-tests-enabled` | `org.ros.isaac.tests.unit`      | rung 08               |
| `vlm-provider`       | `org.ros.isaac.vlm.provider`    | rungs 13-18           |
| `nav2-enabled`       | `org.ros.isaac.nav2.enabled`    | rungs 14-18           |
| `packages`           | `org.ros.isaac.packages.ament`  | save-results          |

## Zot Artifacts

| Repository                      | Use                                |
| ------------------------------- | ---------------------------------- |
| `isaac-ros/ladder-results`      | Full test results per workflow run |
| `isaac-ros/ladder-failed-rungs` | Failed rung list for quick_check   |

## Zot Access Patterns

| Context               | Protocol         | Example                                          |
| --------------------- | ---------------- | ------------------------------------------------ |
| External (Mac, pod)   | HTTPS            | `curl -sk https://192.168.100.1:30500/v2/`       |
| Internal (containerd) | HTTP             | `registries.yaml` mirrors                        |
| oras                  | HTTPS (CA trust) | `oras pull 192.168.100.1:30500/...`              |
| skopeo                | HTTPS + flag     | `skopeo inspect --tls-verify=false docker://...` |

## Key Inputs

| Input               | Default               | Purpose                          |
| ------------------- | --------------------- | -------------------------------- |
| `target`            | `nano2`               | Target node (nano1 or nano2)     |
| `skip_health_check` | `false`               | Skip health check gate           |
| `registry`          | `192.168.100.1:30500` | Zot registry URL                 |
| `quick_check`       | `false`               | Run only previously failed rungs |

## Workflow Naming Convention

| Layer | Pattern                                 | Examples                              |
| ----- | --------------------------------------- | ------------------------------------- |
| L1    | `1-foundation-*.yml`                    | `1-foundation-arc-runner-test.yml`    |
| L4    | `4-layer-*.yml`, `4-*-k3s-*.yml`        | `4-layer-k3s-infra.yml`               |
| L5    | `5-build-test-*-k3s.yml`, `5-vlm-*.yml` | `5-build-test-gpu-validation-k3s.yml` |
| PTC   | `ptc-ladder.yaml`                       | Full ladder via GitHub Actions        |

## ARC (Actions Runner Controller)

| Aspect           | Detail                 |
| ---------------- | ---------------------- |
| Runner process   | Ephemeral K8s pod      |
| Namespace        | `arc-systems`          |
| Pod name pattern | `nano1-xxx-runner-yyy` |


# === TACTICAL.md ===

# TACTICAL — running-isaac-ros-tests

Strategic know-how distilled from past incidents on nano1/nano2. Non-prescriptive; use judgment.

## CRITICAL: Test Execution Must Be in Workload Pod

**All ladder tests MUST execute inside the rs_humble workload pod**, not in separate Argo pods.

### The Problem (Historical False Positives)

- **Historical context**: Tests originally ran in GitHub Actions workflows via `docker exec` into
  the rs_humble container
- **Transition loss**: When converted to Argo Workflows, each rung created a separate pod with
  `alpine:latest`
- **False results**: Alpine has NO ROS packages, NO PyTorch, NO CUDA - any "passing" test was a
  false positive
- **Evidence**: `import torch` in Argo pod fails with `libnvToolsExt.so.1: not found` - but test
  still showed "passed"

### The Fix: kubectl exec into Workload Pod

```bash
# Get the workload pod
POD_NAME=$(kubectl get pods -n isaac-ros -l app.kubernetes.io/component=workload \
  -o jsonpath='{.items[0].metadata.name}')

# Execute test inside workload pod
kubectl exec -n isaac-ros "$POD_NAME" -c isaac-ros -- bash -lc '
  source /opt/ros/humble/setup.bash
  ros2 pkg list | wc -l
'
```

### Memory Constraints

| State        | Memory Used | Notes                          |
| ------------ | ----------- | ------------------------------ |
| Idle system  | 3.6GB       | k3s, containerd, base services |
| Workload pod | 2GB         | rs_humble with ROS/VLM         |
| Available    | 1.8GB       | Tight margin for tests         |
| Swap         | Full        | 34GB used                      |

Tests CAN run in the workload pod. Just need to exec in, run test, exit - don't leave processes
running.

### VLM API Test (Rungs 13-18)

```bash
kubectl exec -n isaac-ros "$POD_NAME" -c isaac-ros -- bash -lc '
  # Test actual VLM model API (not web UI!)
  curl -sf https://integrate.api.nvidia.com/v1/models \
    -H "Authorization: Bearer $NVIDIA_API_KEY" | jq ".data | length"
'
```

Returns 260+ available models (Llama, Gemma, Kimi, Nemotron, etc.)

## Buildah + Zot Image Discovery

- **The `latest` tag in Zot may not be the most complete image**. Run
  `oras manifest fetch 192.168.100.1:30500/rs_humble:latest | jq -r '.annotations["org.ros.isaac.cuda.enabled"]'`
  to verify annotations exist. A value of `null` or missing key means the build succeeded but the
  annotation step failed — the post-build steps (`annotate-image`, `attach-metadata`) didn't run.
- **`nano1_bbe59fe75` has 137 layers but no annotations**. This is a complete build that failed in
  post-processing. Use `rs_humble:latest` instead, which has full OCI annotations.
- **Verify critical packages before running ladder**:
  ```bash
  oras manifest fetch 192.168.100.1:30500/rs_humble:latest \
    | jq -r '.annotations["org.ros.isaac.debs"]' | grep -o 'ros-humble-isaac-ros-visual-slam'
  ```
  If packages are missing, the build didn't include them — don't expect tests to pass.

## Running the Ladder

- **Use correct KUBECONFIG for target node**. The ladder runs on the node specified by `-p target=`.
  On nano2, use `--context=nano2` flag or set context to match the target node:
  ```bash
  KUBECONFIG=/home/amazon1148/workspace/isaac_ros_custom/kubeconfig-merged argo ... --context=nano2
  ```
  The merged config has both nano1 and nano2 contexts — use `--context=nano2` when on nano2.
- **Run validate-context.sh before submitting**. The script at `k3s/validate-context.sh` checks
  hostname vs context mismatch:
  ```bash
  ./k3s/validate-context.sh
  ```
  Blocks submission if you're trying to run on nano1 but you're on nano2 (or vice versa).
- **K3s must be running on the target node**. `kubectl get nodes` should show the target as Ready.
  If it fails with connection refused, k3s isn't running on this machine.
- **Rung 01 skips if ARC namespace doesn't exist**. If `arc-systems` namespace doesn't exist, the
  rung skips with "ARC not installed". If namespace exists but pods aren't Running, it warns but
  passes. This makes the ladder resilient to nodes without ARC.

## Argo Workflows

- **Use `--from workflowtemplate/isaac-ros-ladder` not raw file submit**. The template is applied
  via `kubectl apply` first, then instantiated with `argo submit --from`.
- **All rung containers use `alpine:latest`**. Don't switch to `alpine:3.18` or specific versions —
  other rungs depend on `apk add curl kubectl jq` which works from the default repos on
  `alpine:latest`.
- **alpine has no curl/jq/kubectl pre-installed**. Every rung that needs these tools must
  `apk add --no-cache curl kubectl jq` BEFORE using them. The install must come before the first
  command that needs the tool, not after.
- **`apk add` can fail transiently on Jetson** (network timeout to Alpine repos). The old pattern
  `apk add ... >/dev/null 2>&1` silently swallowed failures, causing rungs to fall through to
  `kubectl not found` → ConfigMap read returns `"unknown"` → capability-based skip. The fix is a
  retry wrapper:
  `_install_pkgs() { for _i in 1 2 3; do apk add --no-cache "$@" 2>&1 && return 0; sleep 2; done; echo "ERROR: Failed to install: $*"; exit 1; }`.
  All rungs now use this.
- **A rung completing in under 10 seconds is a skip, not a test**. If `apk add` fails silently, the
  rung still exits 0 (skip) instead of failing loudly. Check logs for `kubectl: not found` to
  distinguish between a genuine capability skip and an infra failure.
- **`/bin/sh` is POSIX, not bash**. Never use `declare -A` (associative arrays), `[[ ]]`, or other
  bashisms. Use `case` functions for lookups instead.
- **Quick check is only as good as the failed-rungs artifact**. If Zot was unreachable during the
  previous run, failed rungs may not have been saved — quick check falls back to full run.
- **Preflight Zot check can fail transiently**. The preflight uses `oras repo tags` from an alpine
  pod to verify Zot reachability. A freshly scheduled pod can hit a transient network issue. If this
  happens, just resubmit — it's not a curl flag or cert issue.
- **ARC controller freshly installed may not appear in preflight**. If you just ran
  `helm upgrade --install arc-controller` seconds before submitting the ladder, the controller pod
  may still be ContainerCreating when preflight checks it. The preflight reports "no ARC runner
  controller found" as a warning, not a failure — but it's still worth waiting 30-60s after install
  before submitting.

## Source-Built vs Apt-Installed Packages

- **Isaac ROS packages are colcon-built, NOT apt-installed**. `apt list --installed | grep isaac`
  will find nothing. Always use `ros2 pkg list` for package discovery validation.
- **The correct package discovery pattern is**:
  ```bash
  source /opt/ros/humble/setup.bash
  source /workspaces/isaac_ros-dev/install/setup.bash
  ros2 pkg list | grep isaac_ros
  ```
- **Not all image variants include visual_slam or nvblox**. Capability checks for these packages
  should be handled by rung 09 (cuVSLAM). On Jetson Orin Nano (HITL), cuVSLAM, VLM, and Nav2 are
  mandatory capabilities — missing capabilities should FAIL the rung, not skip.
- **Capability ConfigMap values default to "unknown"** when manifest annotations are missing. On
  HITL rungs (07, 09, 13, 14), `"unknown"` triggers a FAIL — the image was built without a required
  capability. Only rung 08 (unit tests) skips gracefully.

## colcon Test in K3s Pods

- **`colcon test` requires `--merge-install` flag** when the install directory was built with merged
  layout. Without it:
  `ERROR:colcon:colcon test: The install directory 'install' was created with the layout 'merged'. Please remove the install directory...`
- **Build-time vs runtime path mismatch is a real parity issue**. When images are built outside K3s
  (on host with Docker), the workspace path at build time (`/workspace-staging/install`) differs
  from the K3s pod path (`/workspaces/isaac_ros-dev/install`). This causes `COLCON_CURRENT_PREFIX`
  errors during colcon test. Detect this pattern and warn instead of failing — the fix is to rebuild
  inside the K3s buildah workflow.
- **`COLCON_TRACE` unbound variable crashes scripts with `set -u`**. Always set `COLCON_TRACE=''`
  before sourcing ROS setup.

## Container Images

- **External images (quay.io, docker.io) may not be pullable on Jetson**. The
  `quay.io/skopeo/stable` image failed with exit 127 (image pull timeout). Prefer `alpine:latest` +
  `curl`/`jq` for registry operations instead of container-specific tools like skopeo.
- **Zot requires HTTPS externally, HTTP internally**. The nginx sidecar on nano1 presents a TLS cert
  signed by the `jetson-local` CA (`/home/amazon1148/registry-https/ca.crt`). This CA is in the host
  trust store but NOT in K8s pods. All Argo workflow templates that use `oras` must mount a
  `zot-ca-certs` ConfigMap and install the cert before calling oras:
  ```sh
  apk add --no-cache ca-certificates >/dev/null 2>&1
  cp /zot-ca/jetson-local-ca.crt /usr/local/share/ca-certificates/jetson-local-ca.crt
  update-ca-certificates >/dev/null 2>&1
  ```
  The `zot-ca-certs` ConfigMap lives in the `argo` namespace and is created from the host CA cert.
- **BusyBox `cp` requires full target filename**. `cp cert.crt /usr/local/share/ca-certificates/`
  fails with "Is a directory" — busybox `cp` doesn't support directory targets without `-T`. Always
  specify the full destination path including filename.
- **Alpine base image lacks `/usr/local/share/ca-certificates/`**. The directory doesn't exist until
  `apk add ca-certificates` is run. Don't assume it's there.
- **Use `oras`, not `curl -sk`, for Zot operations in workflow pods**. `curl -sk` was replaced by
  `oras` across all templates. oras has proper TLS support but still needs the CA cert in the pod's
  trust store — it doesn't magically skip verification like `-sk` does.

## Capability-Based Rung Skipping

- **The `image-capabilities` ConfigMap drives fail/skip decisions for rungs 07, 08, 09, 13, 14**.
  manifest-gate populates it from OCI annotations. On HITL rungs (07, 09, 13, 14), missing
  capabilities ("unknown") cause a FAIL — these are mandatory on Jetson Orin Nano. Only rung 08
  (unit tests) skips gracefully when `unit-tests-enabled=false`.
- **ConfigMap key names must be consistent** between writer (manifest-gate) and readers
  (save-results, rungs 07/08). A rename like `hardware-camera` → `realsense-enabled` must be updated
  everywhere.
- **Validate annotations end-to-end before shipping gating**. Capability-based skip was added Mar 14
  but the VLM annotation detection checked for `nim_sender` (nonexistent) instead of `isaac_ros_vlm`
  (the actual package). The Nav2 detection missed `isaac_ros_nvblox` (provided by the apt deb).
  Neither was caught because no ladder run verified that rungs 13-14 actually executed — they all
  silently passed via skip in 5-6 seconds. **A rung passing in under 10 seconds is a skip, not a
  test.**
- **Source-built packages aren't visible to `ros2 pkg list` at build time**. The annotate-image step
  runs `buildah run` inside the freshly built image, but colcon build only happens at container
  runtime. Detection must fall back to checking for `package.xml` or `setup.py` in the workspace
  source tree.
- **The buildah workflow uses `Dockerfile.realsense.collapsed`, NOT `Dockerfile.realsense`**.
  Editing the wrong Dockerfile produces no effect — the collapsed variant is a separate
  hand-optimized file. Always verify changes land in the file the workflow actually references.
- **Annotation values of `null` (the literal string) match the skip condition**.
  `if [ "$VLM_PROVIDER" = "null" ]` is true — the string "null" is a valid skip trigger. This is
  correct behavior for "annotation detection ran but found nothing", but means any detection bug
  silently disables the rung.

## K3s Workflows

- **Login shell (`bash -lc`) for ROS commands in K3s pods**. Non-login shells don't source
  `.bashrc`, so `ros2`, `colcon` aren't in PATH.
- **Don't hardcode pod names**. Use label selectors (`app.kubernetes.io/component=workload`).
- **Verify pod is Running before testing**. Check `--field-selector=status.phase=Running` — a
  CrashLoopBackOff pod produces false-positive test passes.

## Infrastructure

- **Zot on port 30500 is HTTPS via nginx proxy**. oras uses the CA trust store (see Container Images
  section). Never use `curl -sk` — always install the `jetson-local` CA cert in the pod.
- **ResourceQuotas cap test pods**. If a rung is Pending, check `kubectl describe resourcequota`.
- **Health-check pending pod check filters CronJob-managed pods**. CronJob pods transiently pass
  through Pending — the jq filter `select(.metadata.ownerReferences[]?.kind != "CronJob")` excludes
  them to avoid false positives. The check also scopes to `-n isaac-ros` (not `-A`) to avoid
  catching Argo controller pods during transient restarts.
- **Debugging preflight false positives: narrow scope incrementally**. The pending pod check went
  through three iterations: (1) all namespaces (`-A`) caught Argo controller pods during restarts,
  (2) scoped to `-n isaac-ros` caught CronJob-managed health monitor pods, (3) added jq
  ownerReferences filter to exclude CronJob pods. Each iteration eliminated one false positive
  class.
- **8GB shared CPU/GPU memory**. Scale down ARC runners and workload pods before memory-intensive
  rungs (Nav2, cuVSLAM, VLM rungs 13-14). Note: VLM rungs use NVIDIA Qwen 3.5 400B (400B parameters)
  on `build.nvidia.com` — accessed via NVIDIA NIM API with token. The model does NOT run locally on
  the Jetson. Memory pressure comes from MCAP replay and ROS2 nodes, not the VLM model.

- **Ladder logs written to `/home/amazon1148/tmp/buildah-logs/`**. The onExit handler copies logs to
  this directory for code-graph indexing. Use this path for the indexer script:
  ```bash
  DATABASE_URL=... python3 .claude/scripts/index_ladder_logs.py --jetson nano2
  ```
- **Directory must be world-writable**. If logs don't appear, fix permissions:
  ```bash
  sudo chmod 777 /home/amazon1148/tmp/buildah-logs
  ```
- **Auto memory check in pre-vlm-resources**. The ladder now checks memory before VLM rungs (13-18).
  If <2GB free, it prints a warning with specific commands to scale ARC and workload pods. The
  ladder continues with a warning (doesn't block), but VLM tests will likely fail with OOM if memory
  isn't freed.

- **Free memory for VLM tests before running rungs 13-14**. On nano2:

  ```bash
  # Scale down ARC to free ~500MB
  kubectl scale deployment gha-rs-controller --replicas=0 -n arc-systems --context=nano2

  # Scale down workload pod to free hermes sidecar memory
  kubectl scale deployment nano2-workload-isaac-ros-workload-nano2 --replicas=0 -n isaac-ros --context=nano2
  ```

  This frees ~1.3GB RAM, allowing VLM tests to run without OOM. Restore after testing:

  ```bash
  kubectl scale deployment gha-rs-controller --replicas=1 -n arc-systems --context=nano2
  kubectl scale deployment nano2-workload-isaac-ros-workload-nano2 --replicas=1 -n isaac-ros --context=nano2
  ```

- **nano1 is rosbag-only (no camera), nano2 has the RealSense D435**. The camera is movable — both
  nodes support ROSBAG replay. Both run all 14 rungs. Hardware differences affect rungs 9-12
  (camera-dependent tests).

## GitHub CLI in Workflow Pods

- **Rungs 13 and 14 need `GH_TOKEN` env var from the ARC app secret**. The `gh` CLI reads `GH_TOKEN`
  (not `GITHUB_TOKEN`). The secret is synced via 1Password Connect into the `argo` namespace as
  `github-arc-app` with key `token` (a `ghp_` PAT). No cross-namespace ref needed — the pod runs in
  `argo`:
  ```yaml
  env:
    - name: GH_TOKEN
      valueFrom:
        secretKeyRef:
          name: github-arc-app
          key: token
  ```
- **`gh` reads `GH_TOKEN`, not `GITHUB_TOKEN`**. Setting `env: GITHUB_TOKEN` will pass silently —
  the pod has the var but `gh auth status` still fails. The failure manifests as "gh not
  authenticated" even though the secret is mounted correctly. The fix is simply using the correct
  env var name: `GH_TOKEN`.
- **`gho_` tokens are runner registration tokens, not PATs**. The vault item `GitHub_ARC_Runner`
  stores a `gho_` token under key `password` — this authenticates ARC runners but NOT `gh` CLI. The
  `ghp_` PAT lives in the `ARC_Runner_Token` vault item under key `token`, synced to K8s as
  `github-arc-app`. Using the wrong token type produces a confusing "not authenticated" error.
- **`--repo` must be the full `owner/repo` slug**. K8s pods have no git repo context, so
  `gh run list` and `gh workflow run` both need
  `--repo explicitcontextualunderstanding/isaac_ros_custom`. Without it, `gh run list` returns empty
  results even when workflows were successfully triggered.
- **`--json databaseId` not `--json id`**. The `gh run list` API uses `databaseId` for run IDs.
  Using `--json id` returns null silently in scripts, causing "Could not get run ID" errors. The
  correct pattern:
  `gh run list --workflow 5-vlm-mcap-test.yml --limit 1 --json databaseId -q '.[0].databaseId' --repo explicitcontextualunderstanding/isaac_ros_custom`.
- **Rung 14 (VLM Nav2) fails in ~11s when Nav2 isn't in the image**. This is a capability-based FAIL
  (`nav2-enabled=unknown`), not a skip — Nav2 is required for HITL. Distinguish by duration: a real
  VLM Nav2 test takes minutes, not seconds.
- **`workflow_dispatch.inputs` indentation must be correct**. GitHub Actions requires input keys to
  be indented as children of `inputs:` (2 more spaces), not as siblings. An input at the same indent
  as `inputs:` causes HTTP 422 "Unexpected inputs provided". `yamllint` passes (valid YAML), but
  `actionlint` catches this. This blocked rung 13 — the workflow was never dispatched despite
  `gh workflow run` appearing to succeed.

## GitHub App Token Generation (Rungs 13-14)

- **Stale PAT tokens break `gh workflow run`**. The `GH_TOKEN` env var referencing a static `ghp_`
  token from a K8s secret becomes stale (tokens expire, get rotated). The fix is to generate fresh
  GitHub App installation tokens at runtime using the App's private key.
- **GitHub App credentials are mounted as a secret volume, not env vars**. The workflow template
  mounts `github-arc-app` secret at `/etc/gh-app/` containing:
  - `github_app_id` — The App's numeric ID
  - `github_app_installation_id` — The installation ID for the org/repo
  - `github_app_private_key` — PEM-encoded RS256 private key
- **The `_gh_auth()` function generates installation tokens on-demand**. It creates a JWT signed
  with the private key, then exchanges it for an installation token via GitHub API. This produces a
  fresh `ghs_` token valid for 1 hour.
- **Add `openssl jq` to package installs for rungs 13-14**. JWT generation requires
  `openssl dgst -sha256 -sign` for RS256 signing and `jq` for parsing the API response.
- **JWT expiry should be short (60s)**. Installation tokens are valid for 1 hour; the JWT just needs
  to be long enough to complete the API call. Short expiry reduces risk if the JWT leaks.
- **VolumeMounts must include both `zot-ca-certs` and `gh-app-credentials`**. Rungs 13-14 need Zot
  CA for registry access and the GitHub App credentials for token generation.
- **Replace `gh auth status` check with `_gh_auth` call**. The old pattern verified a pre-mounted
  token was valid. The new pattern generates a fresh token and exports it as `GH_TOKEN` for `gh` CLI
  to use.

## Concurrency Control

- **Multiple concurrent ladder runs cause pod deletion failures**. Without a mutex, submitting a new
  ladder while one is running causes resource contention — pods get deleted mid-execution, producing
  `pod deleted` errors. The ResourceQuota allows only one workload pod; concurrent tests race to
  claim it.
- **`parallelism: 1` only limits tasks within a workflow, not workflow instances**. To prevent
  multiple ladder submissions, add a mutex at the workflow level:
  ```yaml
  spec:
    synchronization:
      mutex:
        name: isaac-ros-ladder
    parallelism: 1
  ```
- **A pending workflow shows `Waiting for argo/Mutex/isaac-ros-ladder-mutex lock`**. This is
  expected — the mutex serializes workflow executions. Delete pending duplicates if they're not
  needed.
- **Always check for running workflows before submitting a new ladder**. Use
  `argo list -n argo --running` to see if a ladder is already in progress. If so, either wait for
  completion or delete it first.
- **Quick check mode can mask concurrency issues**. Multiple quick_check submissions may all skip
  because the failed-rungs artifact is stale. A full run is needed to verify the mutex works
  correctly.


# === WORKFLOWS.md ===

# WORKFLOWS — running-isaac-ros-tests

## Build Workflow Prerequisites

The `build-image` step in `buildah-workflow.yaml` includes a **sentry sidecar** that POSTs
real-time build errors to the code-graph-mcp service on Mac. This requires:

1. **code-graph-mcp container running** in Apple Container (`apple-honcho-codegraph-mcp`)
2. **socat bridge for port 8788** active on Mac host (managed by LaunchAgent `com.kieranlal.socat-k3s-bridges`)
3. **nano2 scripts accessible** on the build Jetson at `/home/amazon1148/workspace/nano2/scripts/code-graph/`

```bash
# Verify bridge is active
lsof -i :8788 | grep socat

# Verify code-graph-mcp is reachable from Jetson
# (run from a pod or SSH into nano1)
curl -sf --connect-timeout 3 http://192.168.1.118:8788/health 2>/dev/null || echo "BRIDGE DOWN"

# If bridge is down, restart the LaunchAgent
launchctl kickstart -k gui/$(id -u)/com.kieranlal.socat-k3s-bridges
```

**Non-blocking**: If the bridge or code-graph-mcp is down, the build still completes — the
sentry sidecar logs warnings but never crashes the buildah container.

## Running the Test Ladder

### Argo WorkflowTemplate (Recommended)

The ladder uses a WorkflowTemplate (`isaac-ros-ladder`) applied via `kubectl apply`, then
instantiated with `argo submit --from`. Always apply the template first after edits.

```bash
# Step 1: Apply/update the template
kubectl apply -f k3s/argo-ladder-workflow.yaml -n argo

# Step 2: Submit from the template
argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2
argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano1

# Step 3: Watch progress
argo watch @latest -n argo

# Step 4: Check status
argo get <workflow> -n argo

# Step 5: View logs for a failed rung
kubectl logs <pod-name> -n argo -c main | tail -30
```

**Parameters**:

| Parameter           | Default               | Purpose                          |
| ------------------- | --------------------- | -------------------------------- |
| `target`            | `nano2`               | Target node (nano1 or nano2)     |
| `skip_health_check` | `false`               | Skip health check gate           |
| `registry`          | `192.168.100.1:30500` | Zot registry URL                 |
| `quick_check`       | `false`               | Run only previously failed rungs |

### Quick Check Flow

1. First run: `argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2`
2. Rungs 2, 8, 12 fail → saved to Zot
3. Fix the issues
4. Quick check:
   `argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2 -p quick_check=true`
5. Only runs rungs 2, 8, 12

### GitHub Actions (ARC)

```bash
# Run K3s infrastructure
gh workflow run 4-layer-k3s-infra.yml -f runner_label=nano2

# Run full ladder via PTC (supports nano1, nano2, or both)
gh workflow run ptc-ladder.yaml -f target=nano2

# Run individual rung workflow
gh workflow run 5-vlm-sanity-test.yml -f runner_label=nano2
gh workflow run 6-semantic-distillation-test.yml -f runner_label=nano2
gh workflow run 7-golden-mcap-validation.yml -f runner_label=nano2
gh workflow run 8-multi-scenario-vlm.yml -f runner_label=nano2
gh workflow run 5-build-test-gpu-validation-k3s.yml -f runner_label=nano2
```

### Scripted Ladder

```bash
# Auto-detect first failure and run from there
./scripts/ci/run_layerX_and_poll.sh --auto-ladder -t nano2
```

## Debugging a Failed Rung

```bash
# 1. Identify the failed rung
argo get <workflow> -n argo

# 2. Get the pod name from the output (e.g., isaac-ros-ladder-xxx-rung-06-...)
POD_NAME=<pod-name-from-argo-output>

# 3. Get logs
kubectl logs $POD_NAME -n argo -c main | tail -30

# 4. If pod is gone, use --previous
kubectl logs $POD_NAME -n argo -c main --previous
```

## Checking Status

```bash
# Argo workflows
argo list -n argo

# K3s pods
kubectl get pods -n argo
kubectl get pods -n isaac-ros

# ConfigMap state (rung results)
kubectl get configmap ladder-rung-status -n argo -o yaml
kubectl get configmap image-capabilities -n argo -o yaml
```

## Results in Zot

```bash
# List ladder results
oras repo ls 192.168.100.1:30500/isaac-ros/ladder-results

# Pull specific result
oras pull 192.168.100.1:30500/isaac-ros/ladder-results:<workflow> -o /tmp/

# Check failed rungs
oras repo ls 192.168.100.1:30500/isaac-ros/ladder-failed-rungs
```

Note: oras uses the CA cert in its trust store. No `--plain-http` or `--tls-verify=false` flags
needed from either within the K3s cluster or from Mac/external hosts.

