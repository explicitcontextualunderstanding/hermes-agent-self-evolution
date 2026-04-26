---
name: sentinel
description: |
  K3s Test Ladder Agent with self-healing diagnostics on Jetson Orin Nano (nano1/nano2).
proficiency_score: 0.85
compositional_triggers:
  after: [managing-k3s-cluster, running-isaac-ros-tests, building-rs-humble]
  before: [debugging-isaac-ros-containers, vslam-debugging]
latent_vars:
  - contains_gpu: true
  - hardware_runner_type: jetson-orin-nano
  - k3s_standalone: true
  - memory_constraint_8gb: true
  - limitrange_min_enforcement: true
  - cross_namespace_monitoring: true
  - pod_accumulation_risk: true
---



# === SKILL.md ===

---
name: sentinel
description: |
  K3s Test Ladder Agent with self-healing diagnostics on Jetson Orin Nano (nano1/nano2).
proficiency_score: 0.85
compositional_triggers:
  after: [managing-k3s-cluster, running-isaac-ros-tests, building-rs-humble]
  before: [debugging-isaac-ros-containers, vslam-debugging]
latent_vars:
  - contains_gpu: true
  - hardware_runner_type: jetson-orin-nano
  - k3s_standalone: true
  - memory_constraint_8gb: true
  - limitrange_min_enforcement: true
  - cross_namespace_monitoring: true
  - pod_accumulation_risk: true
---

## Overview

Sentinel monitors and self-heals two standalone K3s clusters on Jetson Orin Nano (8GB shared
CPU/GPU). It provides CLI tools for Argo workflow diagnostics, Zot artifact management, LLM-powered
failure analysis, and PVC testing. The 14-rung test ladder is orchestrated via Argo Workflows (not
sentinel CLI). Self-healing is handled by CronJob hooks that recover from OOM, disk pressure,
thermal throttling, and zombie processes automatically.

## Triggers

Use sentinel when: diagnosing K3s test failures, analyzing pod crashes, investigating OOM/disk
pressure, checking test ladder status, running auto-diagnostics, or managing ARC runner limits.

## Key Concepts

- **Dual Pod Architecture**: ARC Runner pods (minimal memory, CI tasks) vs Workload pods (SLAM,
  Nav2)
- **Isolated Clusters**: Each node is a standalone K3s cluster with node-specific Kustomize overlays
- **8GB Shared Memory**: ResourceQuotas + LimitRanges + PriorityClasses prevent resource exhaustion
- **Self-Healing Hooks**: CronJobs detect and recover from OOM, zombies, disk, thermal, network
  issues

## Quick Start

```bash
# Argo workflow management
sentinel nano2 --watch-workflow                    # Watch latest workflow in real-time
sentinel nano2 --get-workflow-status               # Get current workflow status
sentinel nano2 --run-argo 'get workflows'          # Run Argo CLI command
sentinel nano2 --resubmit-rung 7                   # Resubmit a specific failed rung
sentinel nano2 --parse-dag k3s/argo-ladder-workflow.yaml  # Parse DAG dependencies

# Zot artifact management
sentinel nano2 --fetch-results                     # Fetch ladder results from Zot
sentinel nano2 --fetch-failed-rungs                # Fetch failed rung list

# Diagnostics
sentinel nano2 --check-partition                   # Partition-aware disk check
sentinel nano2 --diagnose-pvc zot-data --pvc-namespace zot  # PVC writability test
sentinel nano2 --analyze /path/to/state.json      # LLM analysis of failure state

# 1Password recovery
sentinel nano2 --reinstall-onepassword            # Reinstall 1Password Connect
sentinel nano2 --reinstall-onepassword --onepassword-creds ~/op-creds.json

# Test ladder (via Argo, not sentinel CLI)
argo submit k3s/argo-ladder-workflow.yaml -n argo -p target=nano2
argo submit k3s/argo-ladder-workflow.yaml -n argo -p target=nano2 -p quick_check=true
```

## What Sentinel Does

1. **Loads test state** from `k3s/overlays/{nano1,nano2}/state/test-results.json`
2. **Checks stop-loss** — halts on critical errors (OOMKilled, kernel panic, disk pressure)
3. **Diagnoses failures** — uses LLM to analyze and recommend fixes
4. **Self-heals** — CronJob hooks delete stale pods, prune images, shed workloads
5. **Tracks metrics** — four-pillar: tool selection (>=95%), groundedness (>=90%), stop-loss
   (>=98%), constraint enforcement (>=99%)

## File Index

| File           | Role                                                |
| -------------- | --------------------------------------------------- |
| `SKILL.md`     | Entry point (this file)                             |
| `WORKFLOWS.md` | Command sequences, decision trees, CI integration   |
| `REFERENCE.md` | Static schemas: quotas, hooks, thresholds, topology |
| `RECOVERY.md`  | Recovery paths, failure modes, hook debugging       |
| `EXAMPLES.md`  | Scenarios with verifiable expected outcomes         |
| `TACTICAL.md`  | Experience-based strategies from past incidents     |

## Related Skills

- `managing-k3s-cluster` — Cluster operations, pod debugging
- `running-isaac-ros-tests` — CI test ladder execution
- `building-rs-humble` — Buildah image builds via Argo
- `managing-github-actions-runners` — ARC runner lifecycle
- `debugging-isaac-ros-containers` — Container-level ROS diagnostics


# === EXAMPLES.md ===

# Sentinel Examples — Scenarios & Expected Outcomes

## Example 1: OOM Detection and Recovery

```bash
# OOM is detected by the CronJob automatically (every 5 min)
# Manual trigger:
kubectl create job --from=cronjob/oom-monitor oom-test -n isaac-ros
```

**Expected Outcome:**

- oom-monitor CronJob detects OOMKilled event within 30 minutes
- Evicted pods automatically deleted via `--force --grace-period=0`
- Deployment controller recreates pod with current resource limits
- Diagnostic report shows: "OOM detected, pod restarted"

**Verification:**

```bash
kubectl get events -n isaac-ros --field-selector reason=OOMKilled --sort-by='.lastTimestamp'
kubectl get pods -n isaac-ros -l app=isaac-ros-custom
```

---

## Example 2: Disk Pressure Cleanup

```bash
# Disk pressure is detected by CronJobs (75% preemptive, 85% monitor)
# Manual trigger:
kubectl create job --from=cronjob/disk-preemptive-cleanup disk-test -n isaac-ros
```

**Expected Outcome:**

- disk-preemptive-cleanup triggers at 75% usage (before K3s taint)
- If missed: disk-pressure-monitor triggers at 85%
- Evicted pods deleted, containerd images pruned, journal vacuumed
- System partition freed below 70%, pods resume normal scheduling

**Verification:**

```bash
df -h /
kubectl get jobs -n isaac-ros | grep disk
kubectl logs -n isaac-ros job/disk-preemptive-cleanup-<hash>
```

---

## Example 3: ARC Runner Recovery (Stale EphemeralRunnerSet)

```bash
# Symptom: workflows queued, no runners picking up jobs
gh api repos/explicitcontextualunderstanding/isaac_ros_custom/actions/runners | jq '.runners | length'
# Returns: 0

kubectl get EphemeralRunnerSet -n arc-systems
# Shows: failed: 2 (stale set)
```

**Expected Outcome:**

1. Delete stale EphemeralRunnerSet and orphaned listener
2. Restart ARC controller
3. New listener connects to GitHub within 60 seconds
4. GitHub shows runner as "idle" and ready

**Verification:**

```bash
kubectl delete EphemeralRunnerSet <stale-name> -n arc-systems
kubectl delete AutoscalingListener <orphaned> -n arc-systems
kubectl rollout restart deployment arc-controller-gha-rs-controller -n arc-systems
# Wait 60s then:
gh api repos/explicitcontextualunderstanding/isaac_ros_custom/actions/runners | jq '.runners[].status'
```

---

## Example 4: Thermal Throttling Prevention

```bash
kubectl get cronjob thermal-monitor -n isaac-ros -o yaml | grep threshold
```

**Expected Outcome:**

- At 75 deg C: Warning logged (exit 1), no action
- At 85 deg C: Critical (exit 2), tier-3 pods deleted
- Baseline services (1Password, ARC, K3s) survive via `tier-0-critical` PriorityClass
- Workloads auto-restart when temperature drops (thermal-recover.sh)

**Verification:**

```bash
cat /sys/class/thermal/thermal_zone0/temp  # millidegrees
kubectl get pods -n isaac-ros --field-selector=status.phase=Failed
kubectl logs -n isaac-ros job/thermal-monitor-<hash> | tail -20
```

---

## Example 5: ImagePullBackOff on nano2

```bash
# Sentinel LLM diagnosis
sentinel nano2 --analyze /path/to/state.json

# Or directly:
kubectl describe pod <pod-name> -n isaac-ros | grep -A5 Events
```

**Expected Outcome:**

- Sentinel detects ImagePullBackOff in pod status
- LLM diagnosis identifies registry connectivity or missing image
- Fix: verify Zot reachable, check image tag exists, re-pull base image

**Verification:**

```bash
curl -sk https://192.168.100.1:30500/v2/_catalog
curl -sk https://192.168.100.1:30500/v2/<image>/tags/list
```

---

## Example 6: PVC Writability Test

```bash
sentinel nano2 --diagnose-pvc zot-data --pvc-namespace zot
```

**Expected Outcome:**

1. Debug pod created with PVC mounted
2. Test file written with timestamp
3. File read back and verified
4. Debug pod cleaned up
5. Report: PASS or FAIL with details

---

## Example 7: Network Health (Offline Jetson)

```bash
kubectl create job --from=cronjob/network-health-monitor test-network -n isaac-ros
kubectl logs -n isaac-ros job/test-network
```

**Expected Outcome:**

- Exit 0: All reachable (Zot + WAN + DNS)
- Exit 1: Local network issue (Zot unreachable) — investigate ethernet
- Exit 2: WAN offline — acceptable, CI can use cached images

---

## Example 8: Cross-Namespace Monitoring (oom-monitor-1password)

```bash
# oom-monitor-1password runs in the 1password namespace with its own ConfigMap copy
kubectl get cronjob oom-monitor-1password -n 1password -o yaml

# Verify it monitors the correct namespace
kubectl logs -n 1password job/oom-monitor-1password-<hash> | grep "1password"
```

**Expected Outcome:**

- CronJob runs in `1password` namespace with `oom-hook-script` ConfigMap mounted locally
- Script checks for OOMKilled events in `1password` namespace via `NAMESPACE=1password` env
- zot-cleanup still uses the cross-namespace pattern (runs in `isaac-ros` with `NAMESPACE=zot`)

**Verification:**

```bash
# Check the env var is set
kubectl get cronjob oom-monitor-1password -n 1password -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].env}' | jq '.[] | select(.name=="NAMESPACE")'

# Verify ConfigMap exists in both namespaces
kubectl get configmap oom-hook-script -n isaac-ros -o name
kubectl get configmap oom-hook-script -n 1password -o name
```

---

## Example 9: Bulk Pod Cleanup (9K+ Stale Pods)

```bash
# Symptom: kubectl commands hang on zot namespace (9,416 stale ReplicaSet pods)
kubectl get pods -n zot --request-timeout=15s | wc -l

# Trigger bulk cleanup via zot-cleanup CronJob
kubectl create job --from=cronjob/zot-cleanup zot-manual -n isaac-ros

# Monitor progress
kubectl logs -n isaac-ros job/zot-manual -f
```

**Expected Outcome:**

- `cleanup-zot.sh` uses `--field-selector=status.phase=Failed` (server-side filtering)
- Bulk `kubectl delete pods --field-selector=... --force --grace-period=0 --request-timeout=30s`
- Deleted in seconds, not minutes (compared to jsonpath+loop)

**Verification:**

```bash
# Count remaining pods (should be decreasing)
kubectl get pods -n zot --request-timeout=15s --no-headers | wc -l

# Check only healthy pods remain
kubectl get pods -n zot --request-timeout=15s --field-selector=status.phase=Running
```

---

## Example 10: Full Ladder on nano2

```bash
argo submit k3s/argo-ladder-workflow.yaml -n argo -p target=nano2
```

**Expected Outcome:**

- 14 rungs execute sequentially, stop on first failure
- Results pushed to Zot as OCI artifact (`isaac-ros/ladder-results`)
- Failed rungs saved to `isaac-ros/ladder-failed-rungs` for quick_check mode
- Host logs at `/tmp/isaac_ros_logs/k3s-argo-ladder-workflow/`

---

## Example 11: Buildah Workflow Failure (Preflight / Disk)

```bash
# Submit build
argo submit k3s/buildah-workflow.yaml -n isaac-ros \
  -p IMAGE_NAME=rs_humble -p NODE_SELECTOR=nano1

# Workflow stuck at preflight-disk
argo get @latest -n isaac-ros
```

**Expected Outcome:**

- preflight-jetson passes (running on Jetson hardware)
- preflight-disk blocks if system partition > 70%
- check-existing skips build if image+commit already in Zot
- build-image uses overlay storage driver (fuse-overlayfs), caches layers, writes logs to host

**Verification:**

```bash
# Build logs are persisted at build time (not scraped from pod)
ls ~/tmp/buildah-logs/

# If preflight blocked, check disk
df -h /

# If image already exists, check-existing skipped
curl -sk https://192.168.100.1:30500/v2/rs_humble/tags/list | jq
```

**Common Failure: Image tag is `_unknown-*`**

Cause: `DOCKERFILE_COMMIT` was not resolved at submission time.

```bash
# Fix: always resolve the commit hash
argo submit k3s/buildah-workflow.yaml -n isaac-ros \
  -p DOCKERFILE_COMMIT=$(git rev-parse --short HEAD)
```

---

## Example 13: Scale Down ARC Runners for Memory-Intensive Workload

```yaml
# In GitHub Actions workflow:
- name: 'Scale down ARC runner to free memory for VLM'
  uses: ./.github/actions/scale-runner
  with:
    runner: nano1
    replicas: 0
    wait_timeout: 60

# ... run memory-intensive workload ...

- name: 'Scale ARC runner back up'
  uses: ./.github/actions/scale-runner
  with:
    runner: nano1
    replicas: 1
```

**Expected Outcome:**

- ARC runners scaled to 0, freeing ~200Mi memory
- `free -h` confirms memory freed (action verifies this)
- Memory-intensive workload completes without OOM
- Runners scaled back up after workload finishes

**Verification:**

```bash
# Verify runners are gone
kubectl get pods -n arc-systems

# Check memory freed
free -h
```

---

## Example 12: Argo Workflow Pods Accumulating

```bash
# Stalled eventbus prevents TTL cleanup
kubectl get pods -n argo --no-headers | wc -l
# Returns: 80+ completed/failed workflow pods

# Check eventbus health
kubectl get pods -n argo-events
```

**Expected Outcome:**

- Restart eventbus: `kubectl rollout restart deployment eventbus-controller-manager -n argo-events`
- Bulk cleanup completed pods:
  `kubectl delete pods -n argo --field-selector=status.phase=Succeeded --force --grace-period=0`
- Workflow TTL of 3600s should resume cleaning up automatically
- If accumulation recurs, consider adding argo pod cleanup to `disk-preemptive-cleanup` hook

---

## Example 14: Quick Check Mode (Retry Only Failed Rungs)

```bash
# Full ladder failed at rung 7 (GPU Validation)
argo submit k3s/argo-ladder-workflow.yaml -n argo -p target=nano2
# Result: rungs 1-6 passed, rung 7 failed

# Quick check retries only the failed rung
argo submit k3s/argo-ladder-workflow.yaml -n argo -p target=nano2 -p quick_check=true
```

**Expected Outcome:**

- quick_check pulls `isaac-ros/ladder-failed-rungs:nano2` from Zot (plain text list)
- Only rungs 7-12 are submitted (7 is the root cause; 8-12 were transitively blocked)
- Full ladder would take 20+ minutes; quick_check takes ~5 minutes

**Verification:**

```bash
# Check what rungs are in the failed list
kubectl logs -n argo job/<workflow> | grep "rung"

# View persisted results after completion
curl -sk https://192.168.100.1:30500/v2/isaac-ros/ladder-results/tags/list
```

---

## Example 15: L0 Manifest Gate (Annotation Validation Before Pull)

```bash
# Verify image annotations exist BEFORE pulling (KB operation, not GB)
skopeo inspect --tls-verify=false docker://192.168.100.1:30500/rs_humble:latest | jq '.Annotations'

# Check for required annotations
skopeo inspect --tls-verify=false docker://192.168.100.1:30500/rs_humble:latest | \
  jq '.Annotations["org.ros.isaac.build.commit"]'
# Should return: "abc1234" (the commit SHA)
```

**Expected Outcome:**

- 16 OCI annotations present (build commit, date, provenance, AMENT index, debs)
- If annotations are missing, image is rejected — no 3GB+ download attempted
- Annotation format is enum-style: `org.ros.isaac.hardware.camera` not
  `org.ros.isaac.realsense.enabled`


# === RECOVERY.md ===

# Sentinel Troubleshooting

## API Key Issues

### "anthropic SDK not installed"

```bash
pip install anthropic
```

### "Failed to get API key from 1Password"

```bash
# Verify 1Password CLI is installed
op --version

# Check secret exists
op item get ANTHROPIC_API_KEY --vault Personal
```

### Set key manually

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Kubectl Connection Errors

### "context not found"

```bash
# Check available contexts
kubectl config get-contexts

# Verify nano1/nano2 contexts exist
kubectl config use-context nano1
kubectl config use-context nano2
```

### "connection refused"

```bash
# Check K3s is running on remote
ssh nano1 "systemctl status k3s"
ssh nano2 "systemctl status k3s"

# Check kubeconfig points to correct endpoint
kubectl config view
```

## LLM Timeout

### "LLM diagnosis failed: timeout"

- Check network connectivity to Anthropic API
- Use `--skip-llm` for offline hardcoded analysis

### "429 Too Many Requests"

- Anthropic rate limit - wait and retry
- Or set `ANTHROPIC_API_KEY` to use different key

## Guardrail Errors

### "Command blocked by guardrail"

Both nano1 and nano2 have identical hardware (RealSense D435, 8GB shared). Guardrails were removed —
all commands are available on both nodes. If you see a guardrail error, the code may be outdated;
update from the repo.

## Metrics Issues

### "No events recorded"

- Ensure `METRICS_AVAILABLE=True` in sentinel_core.py
- Check metrics imports: `python -c "from metrics import *"`

### Metrics show 0%

- This is normal on first run with no failures
- Metrics track failures, not successes

## Common Failure Scenarios

| Error            | Likely Cause              | Fix                                    |
| ---------------- | ------------------------- | -------------------------------------- |
| ImagePullBackOff | Registry not reachable    | Check network, restart registry        |
| OOMKilled        | Pod memory limit exceeded | Check ResourceQuota, reduce pod limits |
| CrashLoopBackOff | Application failing       | Check logs with `compact_logs`         |

## Resource Issues

### CronJob Rejected by LimitRange `min`

### Init Container Fails ResourceQuota Validation

Symptom: Pod stuck in `Pending` with event:

```
Error: pod exceeds quota, requested: limits.memory=2Gi, used: limits.memory=1.5Gi, limited: limits.memory=2Gi
```

**Root cause**: ResourceQuota counts ALL containers including init containers. If the init container
doesn't specify resources, LimitRange defaults apply — and the sum of all containers can exceed
quota.

```bash
# Check which containers are consuming quota
kubectl describe resourcequota -n isaac-ros

# Check if init containers have resource limits
kubectl get pod <name> -n isaac-ros -o jsonpath='{.spec.initContainers[*].name}'
kubectl get pod <name> -n isaac-ros -o jsonpath='{.spec.initContainers[*].resources}'
```

**Fix**: Either add explicit resources to init containers or adjust LimitRange defaults to leave
room.

### Scale-Runner Action Targets Wrong Namespace

Symptom: `scale-runner` action fails with "deployment not found".

```bash
# scale-runner action checks NAMESPACE env var
# ARC runners are in arc-systems, NOT isaac-ros
kubectl get deployments -n arc-systems
```

### Zot Push Fails with Large Image (413 Request Entity Too Large)

Symptom: `buildah push` or `skopeo copy` fails when pushing images > 1GB.

```bash
# Check nginx client_max_body_size
kubectl exec -n zot <nginx-pod> -- cat /etc/nginx/conf.d/default.conf | grep client_max_body

# Should be 5G for Isaac ROS images (3GB+). Default is 1G.
# Fix: update k3s/zot-tls.yaml and reapply
```

### CronJob Rejected by LimitRange `min`

Symptom: CronJob pods stuck in `Pending` or fail to create with events like:

```
Error: pod exceeds LimitRange min cpu/memory
```

**Root cause**: The target namespace has an enforceable `min` in its LimitRange. Pods that
explicitly specify resource requests below this floor are rejected. This is different from
`defaultRequest` which only sets defaults when pods omit resources entirely.

```bash
# Check the namespace's LimitRange
kubectl get limitrange -n <namespace> -o yaml | grep -A10 'min:'

# Check if the LimitRange has an enforceable min (vs just defaultRequest)
kubectl get limitrange -n <namespace> -o jsonpath='{.items[*].spec.limits[*].min}'
```

**Fix**: Deploy the CronJob in a namespace without an enforceable `min` (e.g., `isaac-ros`) and use
`NAMESPACE=<target>` env var to query the target namespace remotely. This is the cross-namespace
monitoring pattern.

```bash
# Example: oom-monitor-1password runs in 1password namespace
kubectl get cronjob oom-monitor-1password -n 1password -o yaml | grep NAMESPACE
# Shows: NAMESPACE=1password
```

See REFERENCE.md "Cross-Namespace Monitoring" for the full pattern.

### "0/1 nodes are available: didn't have free ports"

Zot or other hostNetwork pod won't schedule - port conflict:

```bash
# Check for conflicting pods
kubectl get pods -o wide | grep -E "hostNetwork|hostPort"

# Delete stuck replicasets
kubectl get replicaset -n <namespace>
kubectl delete replicaset <stuck-rs> -n <namespace>
```

### "failed to create pod: pods ... is forbidden"

ResourceQuota exceeded:

```bash
# Check quota usage
kubectl get resourcequota -n <namespace>
kubectl describe resourcequota -n <namespace>

# Reduce pod count or resource requests
kubectl scale deployment <name> -n <namespace> --replicas=1ImagePullBackOff:
```

### " Back-off pulling image"

Registry issues or image doesn't exist:

```bash
# Check if image exists in Zot
curl https://<registry>/v2/<image>/tags/list

# Check pod events
kubectl describe pod <name> -n <namespace>
```

### Modern ARC Runner Not Starting

```bash
# Check controller logs
kubectl logs -n arc-systems -l app.kubernetes.io/name=gha-runner-scale-set-controller

# Check listener status
kubectl get autoscalingrunnerset -n arc-systems

# Verify CRD exists
kubectl get crd autoscalingrunnersets.actions.github.com
```

### Stale Scale Set ID (Listener can't find GitHub scale set)

If listener logs show:

```
ephemeralrunnersets.actions.github.com "nano2-xxxxx" not found
# OR
No runner scale set found with identifier 8
```

The AutoscalingRunnerSet is pointing to a deleted GitHub scale set:

```bash
# Delete stale runner set
kubectl delete AutoscalingRunnerSet <name> -n arc-systems

# Recreate with fresh GitHub scale set
kubectl apply -k k3s/overlays/arc-nano2-arc
```

### Modern ARC RBAC Errors

If you see errors like:

- "cannot create resource roles in API group rbac.authorization.k8s.io"
- "cannot delete resource rolebindings"

The ClusterRole is missing permissions. Apply the fix:

```bash
# Patch the clusterrole to add create/delete permissions
kubectl patch clusterrole arc-controller-gha-rs-controller --type=json -p='[
  {"op": "replace", "path": "/rules/14/verbs", "value": ["create", "delete", "list", "watch", "patch"]},
  {"op": "replace", "path": "/rules/15/verbs", "value": ["create", "delete", "list", "watch", "patch"]}
]'
```

Then restart the controller:

```bash
kubectl rollout restart deployment arc-controller-gha-rs-controller -n arc-systems
```

### Modern ARC Not Installed (Empty arc-systems)

**Symptom**: `kubectl get pods -n arc-systems` returns nothing, or cluster-health reports
`arc-controller=down`.

**Cause**: ARC was never deployed, or the controller Deployment was deleted without Helm tracking.

**Recovery** — Use Helm OCI charts (not manual manifests):

```bash
# 1. Install controller (handles CRDs + webhook certs automatically)
helm upgrade --install arc-controller \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller \
  -n arc-systems --version 0.13.1

# 2. Verify controller is running
kubectl get deployment arc-controller-gha-rs-controller -n arc-systems

# 3. Install runner scale set (requires github-arc-app secret in arc-systems)
helm upgrade --install nano1-runner \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set \
  -n arc-systems --version 0.13.1 \
  --set githubConfigUrl=https://github.com/<org>/<repo> \
  --set githubConfigSecret=github-arc-app \
  --set controllerManager.serviceAccountName=arc-controller-gha-rs-controller
```

If you get "invalid ownership metadata" on the runner install, add Helm labels to the pre-existing
ServiceAccount (see TACTICAL.md for details).

### Modern ARC Manual Manifest Cleanup

If someone applied the combined manifest (`actions-runner-controller.yaml`) instead of using Helm:

```bash
# The manual approach leaves resources without Helm labels and requires cert-manager.
# Clean up and switch to Helm:
kubectl delete validatingwebhookconfiguration mutating-webhook-configuration --ignore-not-found
kubectl delete deploy controller-manager -n arc-systems --ignore-not-found
kubectl delete service controller-manager-metrics-service webhook-service -n arc-systems --ignore-not-found
# Then install via Helm (see above)
```

## 1Password Connect Offline

When the 1Password Connect pod goes missing or cannot start, many of the sentinel helpers and other
automation will break because they rely on the `op_api_key_wrapper.sh` script to fetch secrets.

## Sentinel CLI Not Found

If you run `sentinel` and see `bash: sentinel: command not found`, it simply means the Python script
isn't on your PATH. You have a few options:

```bash
# run from repo (works anywhere but requires typing the full path)
python3 /home/amazon1148/workspace/isaac_ros_custom/sentinel_core.py \
        --target nano1 --get-workflow-status

# install a lightweight launcher once
sudo ln -s /home/amazon1148/workspace/isaac_ros_custom/sentinel_core.py \
            /usr/local/bin/sentinel
sudo chmod +x /usr/local/bin/sentinel
```

### Optional wrapper (recommended)

For convenience we keep a tiny shell wrapper in `~/.local/bin/sentinel` that isn't tied to the repo
location. It demonstrates several handy features —auto‑detecting the Nano hostname, configuring
`KUBECONFIG`, and working from macOS or either Jetson without needing to think about the current
directory.

```bash
#!/usr/bin/env bash
# ~/.local/bin/sentinel

# choose node based on the hostname
host=$(hostname)
if [[ $host == nano1* ]]; then
target=nano1
elif [[ $host == nano2* ]]; then
target=nano2
else
target=nano1  # default when running from laptop
fi

# merged kubeconfig lives under workspace
export KUBECONFIG=~/workspace/merged-kubeconfig.yaml

exec python3 /home/amazon1148/workspace/isaac_ros_custom/sentinel_core.py \
     --target ${target} "$@"
```

Once the wrapper is in place you can simply type:

```bash
sentinel --version
sentinel status             # auto-detects node
sentinel --watch-workflow   # real-time workflow monitoring
sentinel --check-partition  # partition-aware disk check
```

This pattern is already deployed on **nano2** and the user's Mac; the same file works unchanged from
any environment.

### Symptoms

- `onepassword-connect` pod remains in `ContainerCreating` or enters `CrashLoopBackOff` with events
  complaining about missing secrets.
- `sentinel ... --reinstall-onepassword` reports `FOUND len=0` or the CLI returns an HTTP error when
  probing the local service.
- Attempts to run `op` on the Jetson return `error connecting to server`.

### Recovery Steps

1. Generate or locate the credentials JSON (`op connect server create ...`) on whichever machine you
   use for CLI access and copy it to the Nano.
2. Create/update the Kubernetes secret on the affected node:
   ```bash
   kubectl create secret generic op-credentials -n 1password \
     --from-file=1password-credentials.json=/path/to/creds.json \
     --dry-run=client -o yaml | kubectl apply -f -
   ```
3. (Optional) patch the service to NodePort so that host‑side wrappers can reach it; sentinel will
   perform this automatically when using `--reinstall-onepassword`:
   ```bash
   kubectl patch svc onepassword-connect -n 1password \
     -p '{"spec":{"type":"NodePort"}}'
   ```
4. Wait until the pod status shows `2/2 Running` and verify with a curl health probe:
   `curl -fs http://<node-ip>:<node-port>/v1/health`.
5. Test the wrapper script or re‑run `sentinel nano1 --reinstall-onepassword`. You should see
   `FOUND len=###` where ### is the number of bytes in the token response.

If the service is still unreachable the node may be firewalled; port‑forward as a fallback:

```bash
kubectl -n 1password port-forward svc/onepassword-connect 8080:80
curl -fs localhost:8080/v1/health
```

Once the pod is healthy, subsequent sentinel runs and ARC runners will be able to acquire API keys
normally. This also applies after migrating k3s data or reinstalling the Helm chart.

### Modern ARC Missing kubeconfig ConfigMap

## Buildah Workflow Issues

### Buildah Workflow Stuck in Preflight

```bash
# Check preflight step logs
argo get <workflow> -n isaac-ros
argo logs <workflow> -n isaac-ros --step preflight-jetson

# Common: system disk > 70%
df -h /

# Fix: clean up and resubmit
kubectl create job --from=cronjob/disk-preemptive-cleanup manual -n isaac-ros
```

### Build Step Fails (Storage Driver / Cache Issues)

Buildah on Jetson uses overlay with fuse-overlayfs (`--storage-driver=overlay`, `/dev/fuse`). If
fuse is missing, buildah silently falls back to VFS (slow, copies entire layers).

```bash
# Check buildah storage driver
buildah info 2>/dev/null | grep GraphDriverName
# Expected: "overlay". If "vfs", fuse is missing.

# If cache is too large
buildah system prune -f --keep-storage=2GB

# Build logs are persisted at build time (not pod scraping)
ls ~/tmp/buildah-logs/<workflow>/build-image.log
```

### Push-Image Fails (FUSE Device Missing)

Symptom: `push-image` step fails with "fuse: device not found".

```bash
# Check if FUSE module is loaded
lsmod | grep fuse
sudo modprobe fuse

# The build pod needs privileged: true + /dev/fuse mount
# This is configured in k3s/buildah-workflow.yaml — if it was removed, re-add it
```

### Image Tag Shows `_unknown-<timestamp>`

Cause: `DOCKERFILE_COMMIT=HEAD` was used but git isn't available in the build pod.

```bash
# Fix: resolve commit at submission time
argo submit k3s/buildah-workflow.yaml -n isaac-ros \
  -p DOCKERFILE_COMMIT=$(git rev-parse --short HEAD)
```

## Argo Workflow Issues

### Workflow Stuck in Pending

```bash
# Check eventbus — if it's down, workflows can't schedule
kubectl get pods -n argo-events
kubectl get pods -n argo

# If eventbus is CrashLoopBackOff
kubectl rollout restart deployment eventbus-controller-manager -n argo-events
```

### Completed Workflow Pods Not Being Cleaned Up

Argo sets `ttlSecondsAfterFinished: 3600` but a stalled eventbus prevents cleanup.

```bash
# Check for old workflow pods
kubectl get pods -n argo --field-selector=status.phase=Succeeded --no-headers | wc -l

# Manual bulk cleanup
kubectl delete pods -n argo --field-selector=status.phase=Succeeded \
  --force --grace-period=0 --request-timeout=30s

kubectl delete pods -n argo --field-selector=status.phase=Failed \
  --force --grace-period=0 --request-timeout=30s
```

### Ladder Rung Passes But Workload Pod Is Down

Symptom: `ros2 pkg list | grep isaac_ros_custom` succeeds but workload pod is CrashLoopBackOff.

**Root cause**: The ladder step checks package discovery via `ros2 pkg list` which may work from a
different pod or cached index. The actual workload pod is not verified.

```bash
# Verify the workload pod is actually running
kubectl get pods -n isaac-ros --field-selector=status.phase=Running

# Check workload pod logs
kubectl logs -n isaac-ros <workload-pod> --tail=50
```

### Modern ARC Missing kubeconfig ConfigMap

If runner pods fail with:

- "configmap kubeconfig not found"

Create the ConfigMap:

```bash
kubectl create configmap kubeconfig -n arc-systems \
  --from-literal=k3s.yaml="$(cat /path/to/kubeconfig)"
```

### Swap Death Spiral (8GB Jetson)

When swap usage exceeds 2GB, the system becomes unresponsive:

```bash
# Check swap usage
ssh nano1 "free -h"

# Check tegrastats
ssh nano1 "sudo tegrastats"

# Kill memory-hungry pods
kubectl delete pod -l app=zot -n zot
```

---

## Hook Failures

### oom-monitor Not Detecting OOM

```bash
# Check if CronJob is running
kubectl get cronjob oom-monitor -n isaac-ros

# Check last job status
kubectl get jobs -n isaac-ros -l app=oom-monitor

# Check pod logs
kubectl logs -n isaac-ros job/oom-monitor-xxxxx

# Manual trigger
kubectl create job oom-monitor-manual -n isaac-ros --from=cronjob/oom-monitor
```

### stale-pod-cleanup Not Working

Pod cleanup is now handled by the `zot-cleanup` and `disk-preemptive-cleanup` CronJobs in
`isaac-ros` namespace (not `kube-system`). Use these instead:

```bash
# Trigger zot cleanup (targets zot namespace cross-namespace)
kubectl create job --from=cronjob/zot-cleanup zot-test -n isaac-ros

# Trigger preemptive cleanup (cleans failed pods in isaac-ros)
kubectl create job --from=cronjob/disk-preemptive-cleanup disk-test -n isaac-ros

# Check last run
kubectl logs -n isaac-ros job/zot-test
kubectl logs -n isaac-ros job/disk-test
```

### kubectl Hanging on Large Namespace (9K+ pods)

Symptom: Any `kubectl get pods -n <namespace>` call hangs for 30+ seconds or times out.

**Root cause**: Namespace accumulated thousands of stale ReplicaSet pods (zot hit 9,416). The API
server blocks on large result sets.

**Fix**: All kubectl calls in hook scripts must use `--request-timeout`:

```bash
# Bad (hangs forever)
kubectl get pods -n zot -o jsonpath='{.items[?(@.status.phase=="Failed")].metadata.name}'

# Good (times out, uses field-selector for server-side filtering)
kubectl get pods -n zot --field-selector=status.phase=Failed \
  --no-headers -o custom-columns=':metadata.name' --request-timeout=15s
```

**Bulk deletion** is orders of magnitude faster than one-by-one loops:

```bash
# Bad (loop)
for pod in $(kubectl get pods -n zot ...); do kubectl delete pod "$pod"; done

# Good (single bulk call)
kubectl delete pods -n zot --field-selector=status.phase=Failed \
  --force --grace-period=0 --request-timeout=30s
```

See `disk-pressure-hook.yaml` `cleanup-zot.sh` for the canonical implementation.

### ContainerStatusUnknown Not Being Cleaned

```bash
# Check if jq is available in the cleanup pod
kubectl exec -n kube-system job/stale-pod-cleanup-manual -- which jq

# Test the jq query manually
kubectl get pods -A -o json | jq '.items[] | select(.status.initContainerStatuses[]?.state?.terminated?.reason == "ContainerStatusUnknown") | .metadata.name'
```

### disk-pressure-hook Not Triggering

```bash
# Check node disk usage
kubectl get nodes -o json | jq '.items[] | {name: .metadata.name, allocatable: .status.allocatable."ephemeral-storage"}'

# Check if cleanup script permissions
kubectl auth has can-i delete pods -n kube-system --as=system:serviceaccount:kube-system:default
```

### thermal-hook Not Working

```bash
# Check if tegrastats is available
ssh nano1 "which tegrastats"

# Check temperature directly
ssh nano1 "cat /sys/class/thermal/thermal_zone*/temp"

# Verify cronjob permissions
kubectl get cronjob thermal-monitor -n isaac-ros -o yaml
```

---

## Baseline Protection Issues

### PriorityClass Not Applied

```bash
# Check if PriorityClass exists
kubectl get priorityclass infrastructure-critical

# Apply manually
kubectl apply -f k3s/baseline-priority.yaml

# Check pod priority
kubectl get pods -n 1password -o json | jq '.items[] | {name: .metadata.name, priority: .spec.priority}'
```

### PDB Blocking Draining

```bash
# Check PDB status
kubectl get pdb -A

# Check if disruptions are allowed
kubectl get pdb onepassword-connect-pdb -n 1password -o yaml

# Temporarily disable PDB for maintenance
kubectl delete pdb onepassword-connect-pdb -n 1password
```

### Guaranteed QoS Not Set

```bash
# Check pod QoS class
kubectl get pods -n isaac-ros -o json | jq '.items[] | {name: .metadata.name, qos: .status.qosClass}'

# Verify resources.requests == resources.limits
kubectl get pod <pod-name> -n isaac-ros -o jsonpath='{.spec.containers[0].resources}'
```

### Reinstall 1Password Connect

When the `1password` namespace is missing or the connect pod has gone offline, you can reprovision
it with Sentinel instead of hand‑running helm.

```bash
# basic reinstall (may still fail if credentials secret is missing)
python3 sentinel_core.py --target <nano1|nano2> --reinstall-onepassword

# include credentials file to auto-create the secret
python3 sentinel_core.py --target nano1 \
    --reinstall-onepassword --onepassword-creds ~/op-credentials.json
```

The helper will:

1. create the `1password` namespace if necessary
2. upload `op-credentials` secret when `--onepassword-creds` is given
3. warn if no `op-credentials` secret exists and no credentials file was supplied
4. update the Helm repos and run `helm upgrade --install connect 1password/connect`

Use this during recovery after migrations, cluster restarts or when the pod disappears.

---

## Simulating Failures

### Simulate Disk Pressure

```bash
# Create large file to trigger disk pressure
ssh nano1 "dd if=/dev/zero of=/tmp/test bs=1G count=1"

# Wait for disk-pressure-hook to trigger (10 min)
# Or trigger manually:
kubectl create job disk-pressure-test -n isaac-ros --from=cronjob/disk-pressure-monitor
```

### Simulate Thermal Throttling

```bash
# Check current temperature
ssh nano1 "cat /sys/class/thermal/thermal_zone0/temp"

# Monitor thermal events
ssh nano1 "journalctl -f -u k3s | grep thermal"
```

### Simulate OOM

```bash
# Create a pod that requests more memory than available
kubectl run oom-test --image=busybox --restart=Never --rm -it --limits=memory=100Gi -- sh
```


# === REFERENCE.md ===

# Sentinel Reference — Static Schemas & Latent Variables

## Hardware

| Node  | Hostname | Hardware         | Memory             | Role             |
| ----- | -------- | ---------------- | ------------------ | ---------------- |
| nano1 | nano1    | Jetson Orin Nano | 8GB shared CPU/GPU | Primary runner   |
| nano2 | nano2    | Jetson Orin Nano | 8GB shared CPU/GPU | Secondary runner |

Both run all 14 test ladder rungs. Both have RealSense D435 cameras for hardware-in-the-loop
testing.

## Disk Layout (nano1)

| Partition | Device            | Size  | Mount Point                                  |
| --------- | ----------------- | ----- | -------------------------------------------- |
| System    | `/dev/nvme0n1p1`  | 56GB  | `/` (keep under 70%)                         |
| Data      | `/dev/nvme0n1p16` | 1.8TB | `/mnt/bigdata`, `/home/amazon1148/workspace` |

## Network

| Network  | Subnet        | Use                     |
| -------- | ------------- | ----------------------- |
| Ethernet | 192.168.100.x | Inter-Jetson, Zot HTTPS |
| WiFi     | 192.168.1.x   | Mac access, dev work    |

## ResourceQuotas

| Namespace     | requests.mem | limits.mem | requests.cpu | limits.cpu | pods |
| ------------- | ------------ | ---------- | ------------ | ---------- | ---- |
| `kube-system` | 2Gi          | 4Gi        | 2            | 4          | 20   |
| `default`     | 1Gi          | (none)     | 1            | (none)     | 10   |
| `arc-systems` | 1Gi          | 1Gi        | 2            | 2          | 4    |
| `isaac-ros`   | 6Gi          | 6Gi        | 4            | 4          | 2    |

### ARC Memory Optimization (2026-03-06)

| Resource | Quota | Per-Container Limit             |
| -------- | ----- | ------------------------------- |
| pods     | 4     | (controller+listener+2 runners) |
| memory   | 1Gi   | 256Mi (LimitRange)              |
| CPU      | 2     | 500m (LimitRange)               |

## PriorityClasses (4 tiers)

| Tier | Name                   | Value   | Workloads                          |
| ---- | ---------------------- | ------- | ---------------------------------- |
| 0    | `tier-0-critical`      | 1000000 | API server, etcd, CoreDNS          |
| 1    | `tier-1-orchestration` | 800000  | Argo Workflows, Argo Events        |
| 2    | `tier-2-services`      | 600000  | Zot Registry                       |
| 3    | `tier-3-workloads`     | 100000  | ARC runners, test pods (evictable) |

## LimitRanges

| Namespace     | min cpu | min mem | defaultRequest mem            | defaultRequest cpu | Notes                                                       |
| ------------- | ------- | ------- | ----------------------------- | ------------------ | ----------------------------------------------------------- |
| `arc-systems` | 100m    | (none)  | 128Mi                         | 100m               | No enforceable `min`                                        |
| `isaac-ros`   | (none)  | (none)  | varies by deployed LimitRange | varies             | No enforceable `min`                                        |
| `1password`   | 100m    | 128Mi   | 256Mi                         | 250m               | **Has enforceable `min`** — blocks pods with lower requests |

## CronJob Hooks

| CronJob                 | Schedule          | Threshold    | ConfigMap                   | Purpose                                        |
| ----------------------- | ----------------- | ------------ | --------------------------- | ---------------------------------------------- |
| network-health-monitor  | \*/5 \* \* \* \*  | connectivity | `network-health-script`     | Zot, DNS, WAN (hostNetwork)                    |
| oom-monitor             | \*/5 \* \* \* \*  | OOMKilled    | `oom-hook-script`           | Detect OOM + Evicted pods (isaac-ros)          |
| oom-monitor-1password   | \*/5 \* \* \* \*  | OOMKilled    | `oom-hook-script`           | Detect OOM in 1password (runs in 1password ns) |
| zombie-monitor          | \*/10 \* \* \* \* | zombies      | `zombie-hook-script`        | Kill defunct processes                         |
| disk-preemptive-cleanup | \*/30 \* \* \* \* | 75% disk     | `disk-pressure-hook-script` | Preempt cleanup before K3s taint               |
| disk-pressure-monitor   | \*/30 \* \* \* \* | 85% disk     | `disk-pressure-hook-script` | Cleanup after taint exists                     |
| thermal-monitor         | \*/30 \* \* \* \* | 75/85 deg C  | `thermal-hook-script`       | Warn at 75, shed at 85                         |
| zot-cleanup             | \*/30 \* \* \* \* | stale pods   | `disk-pressure-hook-script` | Clean stale Zot pods                           |
| helm-health-monitor     | \*/30 \* \* \* \* | helm health  | (inline)                    | Monitor Helm releases                          |
| cluster-health-monitor  | \*/5 \* \* \* \*  | cross-ns     | `cluster-health`            | Cross-namespace stability gate                 |

### Cross-Namespace Monitoring

Some hooks monitor namespaces other than `isaac-ros`. The CronJob runs in `isaac-ros` (no
enforceable `min` LimitRange) with `NAMESPACE=<target>` env to query the target namespace. The
`isaac-ros` default SA must have RBAC for the target namespace.

| Hook                  | Runs In   | NAMESPACE Env | Target Namespace |
| --------------------- | --------- | ------------- | ---------------- |
| oom-monitor-1password | 1password | `1password`   | 1password        |
| zot-cleanup           | isaac-ros | `zot`         | zot              |

All use `concurrencyPolicy: Forbid`, `failedJobsHistoryLimit: 1`, `successfulJobsHistoryLimit: 1`.

## Hook Scripts (by ConfigMap)

### oom-hook-script

- `check-oom.sh`: Scans events for OOMKilled, checks for Evicted pods, auto-deletes
- Uses `kubectl delete pod --force --grace-period=0` for evicted pods

### zombie-hook-script

- `check-zombies.sh`: Scans for `<defunct>` processes
- `cleanup-zombies.sh`: Kills parent processes (SIGTERM -> SIGKILL)
- Also kills stuck: vlm_node.py, ros2 topic, ros2daemon, rclpy

### disk-pressure-hook-script

- `preemptive-cleanup.sh` (75%): evicted pods, completed/failed pods, image prune, journal vacuum,
  old logs, temp files
- `check-disk.sh` (85%): Checks root disk usage
- `cleanup-disk.sh` (85%): crictl prune, journal vacuum, large log truncation, temp clear

### thermal-hook-script

- `check-thermal.sh`: Reads thermal_zone0, exit 1 at 75C, exit 2 at 85C
- `thermal-shed.sh`: Deletes tier-3 pods when critical
- `thermal-recover.sh`: Restarts shed deployments after cooldown

### network-health-script

- `check-network.sh`: curl Zot (192.168.100.1:30500), WAN (google/generate_204), DNS, physical link
  via /sys/class/net/\*/operstate

## 1Password Connect

| Node  | WiFi IP      | NodePort | Namespace |
| ----- | ------------ | -------- | --------- |
| nano1 | 192.168.1.86 | 31307    | 1password |
| nano2 | 192.168.1.81 | 31308    | 1password |

## Zot Registry

| Network  | Host          | Port  | Protocol |
| -------- | ------------- | ----- | -------- |
| Ethernet | 192.168.100.1 | 30500 | HTTPS    |
| WiFi     | 192.168.1.86  | 30500 | HTTPS    |

## Test Ladder Rungs

| Rung | Level | Name                | Purpose                          |
| ---- | ----- | ------------------- | -------------------------------- |
| 01   | L1    | ARC Runner          | Runner health, K3s connectivity  |
| 02   | L4    | K3s Infra           | Cluster, GPU, volumes            |
| 03   | L4    | K3s Smoke           | Pod ready, ROS2 pkg discovery    |
| 04   | L4    | Zot/WASM            | Registry, Spegel, Dagger, WASM   |
| 05   | L4    | Bag Pipeline        | Rosbag replay from Zot           |
| 06   | L5a   | ROS2 Contract       | Package discovery                |
| 07   | L5b   | GPU Validation      | GPU resources available          |
| 08   | L5c   | Unit Tests          | colcon test                      |
| 09   | L5d   | cuVSLAM Diagnostics | VSLAM health                     |
| 10   | L5e   | Integration Tests   | Multi-node tests                 |
| 11   | L5f   | E2E Tests           | Full pipeline                    |
| 12   | L5g   | Parity Tests        | Rosbag regression                |
| 13   | L5h   | VLM Test            | Vision-language model validation |
| 14   | L5i   | VLM Nav2 Test       | VLM + Nav2 integration test      |

## Kustomize Overlays

| Target         | Overlay Path                  | Namespace   |
| -------------- | ----------------------------- | ----------- |
| ARC nano1      | `k3s/overlays/arc-nano1-arc`  | arc-systems |
| ARC nano2      | `k3s/overlays/arc-nano2-arc`  | arc-systems |
| Workload nano1 | `k3s/overlays/nano1-workload` | isaac-ros   |
| Workload nano2 | `k3s/overlays/nano2-workload` | isaac-ros   |

## Essential Files

| Resource         | Path                            |
| ---------------- | ------------------------------- |
| Apply script     | `k3s/apply.sh`                  |
| ResourceQuotas   | `k3s/resource-quota-*.yaml`     |
| PriorityClasses  | `k3s/priorityclasses.yaml`      |
| LimitRange       | `k3s/isaac-ros-limitrange.yaml` |
| Hook YAMLs       | `k3s/*-hook.yaml`               |
| Zot manifests    | `k3s/zot/*.yaml`                |
| Buildah workflow | `k3s/buildah-workflow.yaml`     |
| Argo ladder      | `k3s/argo-ladder-workflow.yaml` |


# === TACTICAL.md ===

# Sentinel Tactical Insights — Experience-Based Strategies

distilled from past incidents on nano1/nano2. Non-prescriptive; use judgment.

## Memory

- **8GB is shared CPU/GPU**. ARC listener allocates 500MB but uses ~10MB — audit LimitRange defaults
  before adding workloads. A single VLM pod can OOM the entire node.
- **Actual ARC memory usage is much lower than limits**: controller ~30Mi, listener ~10Mi, runner
  ~50-100Mi — total ~200Mi actual. LimitRange caps at 256Mi per container. The gap between actual
  and limit is safety margin for burst operations.
- **Swap death spiral**: Once swap exceeds 2GB, the system becomes unresponsive. Kill
  memory-intensive pods (Zot, test workloads) immediately rather than waiting for CronJobs.
- **ResourceQuota vs LimitRange**: Quota caps the namespace total; LimitRange caps individual
  containers. Set quota high enough for all pods, limit low enough to prevent any single pod from
  hogging memory.
- **ResourceQuota counts ALL containers, including init containers**. Init containers without
  explicit resource limits can fail quota validation. Either add limits to init containers or set
  LimitRange defaults high enough to cover them.
- **Scale-to-zero for idle runners on edge**. `minRunners: 0` saves ~200Mi when no workflows are
  running. Every MiB matters on 8GB shared memory. Runners spin up on demand.

## Disk

- **System partition (56GB) is the bottleneck**. Data partition (1.8TB) is under `/mnt/bigdata` but
  `/home/amazon1148/workspace` may or may not be on it — always `df -h` before moving files.
- **Preemptive cleanup at 75% is critical**. K3s applies `disk-pressure` taint at 85%, blocking all
  scheduling. The 10% gap is consumed fast by containerd images and journal logs.
- **Journal logs grow fast on Jetson**. `journalctl --vacuum-time=1d` should be the first cleanup
  step, not the last.

## Thermal

- **Jetson Orin Nano throttles at 80 deg C, trips at 85 deg C**. The CronJob warns at 75 to give a
  5-degree buffer. If you see sustained 70+, check for runaway containers before it escalates.
- **Thermal shedding deletes tier-3 pods**. This is intentional — ARC runners and test pods are
  sacrificial. 1Password and K3s survive because they have `tier-0-critical`.

## ARC Runners

- **Modern ARC uses two Helm OCI charts, not manual manifests**. The controller chart
  (`gha-runner-scale-set-controller`) handles CRDs, webhook certs, and RBAC automatically — no
  cert-manager needed. The runner chart (`gha-runner-scale-set`) creates the `AutoscalingRunnerSet`.
  Install via:
  ```bash
  helm upgrade --install arc-controller \
    oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller \
    -n arc-systems --version 0.13.1
  helm upgrade --install nano1-runner \
    oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set \
    -n arc-systems --version 0.13.1 \
    --set githubConfigUrl=https://github.com/<org>/<repo> \
    --set githubConfigSecret=github-arc-app \
    --set controllerManager.serviceAccountName=arc-controller-gha-rs-controller
  ```
  The manual manifest approach (`actions-runner-controller.yaml` from GitHub releases) requires
  cert-manager for webhook TLS and fails with CRD annotation size limits — avoid it.
- **Large CRDs exceed kubectl apply annotation size**. Modern ARC CRDs
  (`autoscalingrunnersets.actions.github.com`, etc.) are too large for the
  `kubectl.kubernetes.io/last-applied-configuration` annotation (256KB limit). Use
  `kubectl apply --server-side --force-conflicts` instead. If you already applied via regular
  `kubectl apply` and got "Too long" errors, the CRDs may be in a partial state — clean up and use
  server-side apply.
- **Helm ownership labels needed for pre-existing resources**. If a ServiceAccount or other resource
  existed before Helm (e.g., from a manual overlay), Helm rejects it with "missing key
  app.kubernetes.io/managed-by". Fix by adding the labels:
  ```bash
  kubectl label sa <name> -n arc-systems app.kubernetes.io/managed-by=Helm
  kubectl annotate sa <name> -n arc-systems meta.helm.sh/release-name=<release> \
    meta.helm.sh/release-namespace=arc-systems
  ```
- **`github-arc-app` secret must be in the same namespace as the runner scale set**. The
  OnePasswordItem creates the secret in its own namespace (`actions-runner-system` by default), but
  Helm expects it in `arc-systems`. Copy it:
  `kubectl get secret ... -o yaml | sed 's/ns1/ns2/' | kubectl apply -f -`.
- **Stale EphemeralRunnerSet is the most common ARC failure**. When the controller restarts, old
  sets don't clean up. The fix is always: delete set + delete listener + restart controller.
- **Never apply both nano1 and nano2 overlays to the same cluster**. Each cluster is standalone;
  cross-cluster runner management causes "already has an active session" errors.
- **Scale set ID mismatch**: If GitHub deletes a scale set but K3s still references it, the listener
  crashes. Fix: delete AutoscalingRunnerSet, reapply overlay, verify new scale set ID.
- **Missing kubeconfig ConfigMap** causes runner pods to fail on startup. Always verify after
  applying a new overlay.
- **GPU access is via device mounts, not resource requests**. K3s on Jetson reports 0 GPU available
  via `nvidia.com/gpu` resource. GPU works through `/dev/nvidia*` device mounts and
  `LD_LIBRARY_PATH` — not through resource requests in pod spec.
- **LD_LIBRARY_PATH is required for GPU workloads in runners**. Add
  `/usr/lib/aarch64-linux-gnu/nvidia:/usr/local/cuda/lib64` to ARC runner env. Without it, GStreamer
  `nvjpegenc` GPU encoding fails silently.
- **Use component labels to distinguish pod types**. `app.kubernetes.io/component=workload` vs
  `app.kubernetes.io/component=runner` prevents label selector confusion — VLM tests selected the
  wrong pod (512Mi vs 1Gi) and OOM'd before labels were added.

## Clusters

- **Both nano1 and nano2 are independent K3s clusters**, not joined. Always `hostname` first to know
  which cluster you're talking to. `kubectl` commands run against the local cluster unless you
  explicitly `--kubeconfig` to the other node.
- **You may already be ON the target node**. If `hostname` returns `nano2`, `kubectl` commands hit
  nano2 directly — no SSH needed. Only SSH when you need to operate on the _other_ node.
- **Zot is only on nano1** (192.168.100.1:30500). nano2 accesses it over ethernet. If nano1 is down,
  nano2 cannot pull images or push bags.

## CronJobs

- **Network health uses `hostNetwork: true`** because the `bitnami/kubectl` image lacks `ping`,
  `nslookup`, and `ip`. Workarounds: curl for connectivity, `/sys/class/net/*/operstate` for link
  status.
- **All hooks use `concurrencyPolicy: Forbid`**. If a hook is still running when the next schedule
  fires, the new run is skipped. Long-running hooks may need manual triggering.
- **Hook job retention is 1**. If the last job failed, the previous success is already gone. Check
  logs before deleting stuck jobs.
- **`kubectl get pods` without `--request-timeout` hangs on large namespaces**. A namespace with 9K+
  stale pods (as zot had) causes the API server to block for 30+ seconds. All kubectl calls in hook
  scripts must include `--request-timeout`.
- **Bulk deletion over one-by-one loops**: Always prefer `kubectl delete pods --field-selector=...`
  over jsonpath+loop. A single bulk call is orders of magnitude faster than iterating.
- **Failed pod cleanup must always run, not only when thresholds are met**. Make it Step 0 before
  any disk/temperature check — otherwise pods accumulate during healthy periods and become
  unmanageable later.
- **SIGTERM before SIGKILL for zombie cleanup**. `pkill -9` directly creates new zombies because the
  parent never gets to reap children. Use `pkill -TERM ... sleep 2 ... pkill -KILL` as a two-step
  escalation.
- **Cleanup memory limits need headroom**. 256Mi OOM'd during disk cleanup; 512Mi is the minimum for
  operations that iterate pods or parse large kubectl output.
- **Cleanup hooks must tolerate disk-pressure taint**. They need to run when disk is full — exactly
  when the taint is active. Add both `NoSchedule` and `NoExecute` tolerations.

### CronJob Authoring Pitfalls

- **`ttlSecondsAfterFinished` goes in `jobTemplate.spec`, not CronJob `spec`**. K3s rejects the
  field at the CronJob level with `strict decoding error: unknown field`. Place it under
  `jobTemplate.spec.ttlSecondsAfterFinished`.
- **`kubectl apply` needs the `patch` verb in RBAC**. Even with `create` and `update` granted,
  `kubectl apply` (strategic merge patch) fails or hangs without `patch`. Always include it for
  ConfigMap/Deployment write access in ClusterRoles.
- **`kubectl delete --force` on CRD resources hangs without a controller**. EphemeralRunnerSets and
  other ARC CRD resources require a controller to process finalizers. If the controller is down,
  `--force --grace-period=0` hangs indefinitely inside pods. Don't attempt — log and delegate to
  `helm-health-monitor`.
- **Pipes inside `bitnami/kubectl` pods can hang**.
  `kubectl create --dry-run=client -o yaml | kubectl apply -f -` hangs even though both commands
  work individually. Write to a temp file first:
  `kubectl create ... --dry-run=client -o yaml > /tmp/cm.yaml && kubectl apply -f /tmp/cm.yaml`.
- **`hostname -s` inside a pod returns the pod name**, not the node. Use
  `kubectl get nodes -o jsonpath='{.items[0].metadata.name}'` for the actual node name.
- **The namespace is `1password` (digit 1), not `onepassword` (letter o)**. Easy typo that causes
  silent false negatives — the kubectl query returns empty and the phase reports "not running"
  because it queried a nonexistent namespace. This bug was found in 17 references across 11 files.
  **Not all `onepassword` strings are wrong**: CRD API group (`onepassword.com/v1`), Helm labels
  (`app.kubernetes.io/name: onepassword-connect`), and service names (`onepassword-connect`) use the
  letter 'o' correctly per upstream naming. Only `-n onepassword` namespace references are bugs.
- **Heredocs inside YAML block scalars break on delimiter indentation**. The heredoc terminator must
  appear at column 0 with no YAML indent, but the block scalar strips leading whitespace. Use
  `printf` for multi-line YAML generation inside scripts.
- **`OnFailure` + non-zero exit = infinite restart loop**. If your hook exits 2 for expected failure
  states (e.g., "infra is down"), the Job restarts the pod forever. Always exit 0 and use the
  ConfigMap to carry granular status. `ttlSecondsAfterFinished` alone doesn't prevent this — the pod
  restarts repeatedly before TTL cleans it up.
- **`kubectl delete` needs `--timeout`**. Without it, the command waits for API server confirmation
  which may never come. Always add `--timeout=10s` on destructive operations in hook scripts.

### Cluster Health Monitor

- **`cluster-health-monitor` is the cross-namespace stability gate** (`*/5 * * * *`, `isaac-ros`).
  It checks node health, critical infra (ARC/Argo/Zot), supporting services (1Password), and
  workload (isaac-ros) in order. Results go to ConfigMap `cluster-health` in `isaac-ros`.
- **Hard failures (node not Ready, ARC/Argo/Zot down) skip remaining phases but still exit 0**. The
  ConfigMap `overall` field carries the granular status (`fail`). Non-zero exits with `OnFailure`
  restartPolicy cause CrashLoopBackOff — the Job restarts the pod forever. Always exit 0 and let the
  ConfigMap distinguish pass/degrade/fail.
- **Quick cluster status check**:
  `kubectl get configmap cluster-health -n isaac-ros -o jsonpath='{.data.overall}'` — returns
  `pass`, `degraded`, or `fail`.
- **ARC controller Deployment can be missing despite Helm saying "deployed"**. On both nano1 and
  nano2, the `arc-controller-gha-rs-controller` Deployment had 0 availableReplicas while
  `helm status` reported `deployed`. `helm-health-monitor` is the remediation path (reinstall the
  release). On nano1, ARC was never deployed at all — the namespace was empty. The
  `cluster-health-monitor` correctly reports `arc-controller=down` in both cases.
- **ARC can be completely absent from a cluster, not just crashed**. On nano1, `arc-systems`
  namespace exists but has zero pods. The test ladder blocks at rung 1 (ARC Runner) because there's
  no runner to execute L4/L5 rungs. Fix: apply the ARC overlay (`./k3s/apply.sh arc-nano1`) before
  running the ladder.
- **Multiple critical controllers can fail simultaneously**. On nano1, both ARC controller and Argo
  controller were down while Zot was up. The hook's Phase 2 reports all failures in `phase2_detail`
  (e.g., `arc-controller=down argo-controller=down zot=up`) before the early exit — useful for
  diagnosing cascading failures vs isolated incidents.
- **Orphaned EphemeralRunnerSets persist without a controller**. Deleting them via
  `kubectl delete --force` hangs. The `cluster-health-monitor` logs them but delegates cleanup to
  `helm-health-monitor` (which reinstalls the Helm release, recreating the controller).
- **Pending pod checks must exclude CronJob-managed pods**. The health check's pending pod query
  catches CronJob pods during transient Pending phase. Use jq to filter:
  `jq -r '.items[] | select(.metadata.ownerReferences[]?.kind != "CronJob") | .metadata.name'` to
  avoid false positives from regularly-scheduled hooks.
- **Argo controller deployment name varies by install method**. Helm chart creates
  `argo-workflows-workflow-controller`; direct kubectl apply creates `workflow-controller`. The
  health monitor tries both names with a fallback.

## LimitRanges

- **`defaultRequest` is not enforceable** — it only sets defaults when pods omit resources. Pods
  that explicitly specify lower values (like `cpu: 10m`) are accepted. Only the `min` field blocks
  pods below its threshold.
- **`1password` namespace has an enforceable `min: cpu 100m, memory 128Mi`**. The
  oom-monitor-1password CronJob runs in the `1password` namespace with its own copy of the
  `oom-hook-script` ConfigMap. The zot-cleanup hook still uses the cross-namespace pattern (runs in
  `isaac-ros` with `NAMESPACE=zot`).
- **`isaac-ros` has no enforceable `min`** — only `defaultRequest`. This is why the hooks can run at
  10m/16Mi in isaac-ros but not in 1password. Don't assume `defaultRequest` applies everywhere.

## Pod Accumulation

- **Zot namespace accumulated 9,400+ stale ReplicaSet pods** over days. The zot-cleanup hook hung
  because `kubectl get pods -n zot` with 9K results takes 30+ seconds. Fix: all scripts now use
  `--request-timeout=15s` and bulk deletion.
- **Argo namespace accumulates completed workflow pods**. The `ttlSecondsAfterFinished` on Argo
  Workflows (3600s) should clean these, but a stalled eventbus can prevent it. Consider adding
  periodic cleanup to the `disk-preemptive-cleanup` hook.

## Buildah

- **Overlay storage driver with fuse-overlayfs on Jetson**. The buildah workflow uses
  `--storage-driver=overlay` with `/dev/fuse` mounted and `modprobe fuse`. Buildah silently falls
  back to VFS if fuse is missing — always verify the driver with
  `buildah info | grep GraphDriverName`. Force `BUILDAH_STORAGE_DRIVER=overlay` as env var AND CLI
  flag.
- **Resolve git commits at submission time, not runtime**. `DOCKERFILE_COMMIT=HEAD` produces
  `_unknown-<timestamp>` tags when git isn't available inside the buildah pod. Always pass
  `-p DOCKERFILE_COMMIT=$(git rev-parse --short HEAD)` when submitting the workflow.
- **Persist logs at build time, don't rely on pod scraping**. Build logs written via `tee` to
  `~/tmp/buildah-logs/<workflow>/build-image.log` on the host. `kubectl logs` post-hoc is unreliable
  — the pod may be gone by the time you check.
- **YAML indentation trap**: Shell script lines can silently land in the DAG task list instead of
  inside `build-image.args`. Verify multi-line scripts stay under the correct YAML key.
- **FUSE device is required for push-image**. The build pod needs `privileged: true`, `/dev/fuse`
  mount, and `modprobe fuse || true` at start.
- **Buildah cache fills the system partition**. Preflight blocks at 70% disk, and the cleanup step
  prunes cache after build. If buildah cache is large, `buildah system prune -f --keep-storage=2GB`
  is the escape hatch.
- **Use K8s `HOSTNAME` env var instead of the `hostname` binary**. The `hostname` command is missing
  in some pod contexts. K8s always injects `HOSTNAME` as an environment variable.
- **Dockerfile multi-stage COPY of 1000+ files is slow on Jetson**. Tarball approach
  (`tar -cf /tmp/usr_local.tar` + `COPY --from`) converts random to sequential I/O, which is much
  faster on disk-constrained Jetson.
- **Prebuilt librealsense debs have `arm64` label but Jetson reports `aarch64`**. The Dockerfile
  detects both and uses `--force-overwrite` — don't use `--force-depends` which leaves broken
  packages.
- **Telemetry runs locally, not via SaaS**. Sentinel migrated from AgentOps to OpenTelemetry for
  full local control. No external API dependency for metrics — avoids another failure point on edge
  devices with intermittent connectivity.

## Argo Workflows

- **Stalled eventbus prevents TTL cleanup**. Argo Workflows sets `ttlSecondsAfterFinished: 3600` but
  if the eventbus controller is down, completed workflow pods accumulate indefinitely. Consider
  adding periodic Argo pod cleanup to the `disk-preemptive-cleanup` hook.
- **Zot CA cert must be mounted in workflow pods that use oras**. The `jetson-local` CA signs Zot's
  TLS cert but K8s pods don't trust it. Use a `zot-ca-certs` ConfigMap in the `argo` namespace,
  mount at `/zot-ca`, and install the cert before any oras call:
  ```sh
  apk add --no-cache ca-certificates >/dev/null 2>&1
  cp /zot-ca/jetson-local-ca.crt /usr/local/share/ca-certificates/jetson-local-ca.crt
  update-ca-certificates >/dev/null 2>&1
  ```
  The ConfigMap is workflow-level `volumes` with per-container `volumeMounts`. BusyBox `cp` requires
  the full target filename (not just directory). Alpine lacks `/usr/local/share/ca-certificates/`
  until `ca-certificates` package is installed.
- **Use `oras`, not `curl -sk`, for Zot operations in workflow pods**. oras has proper TLS support
  but still needs the CA cert in the pod's trust store — it doesn't skip verification like `-sk`
  does.
- **Workflow steps in containers need runtime kubectl install**. Use lightweight images like
  `alpine` with `_install_pkgs()` retry wrapper for `curl kubectl jq`. The old
  `apk add ... >/dev/null 2>&1` pattern silently swallowed failures.
- **Verify pod is Running before testing it**. Ladder rungs that just run `ros2 pkg list | grep`
  without checking pod status pass falsely when the workload pod is CrashLoopBackOff. Always check
  `kubectl get pods --field-selector=status.phase=Running` first.
- **`continueOn` + `depends` in DAGs causes validation errors**. Use proper dependency chains
  instead of mixing continueOn with depends.
- **Buildah uses `/bin/sh`, not bash**. ROS source commands (`source /opt/ros/humble/setup.sh`) must
  be wrapped in `bash -c '...'` inside buildah steps.
- **DAG dependency parsing identifies cascade failures**. When rung 3 fails, rungs 4-14 are blocked
  transitively. Sentinel's `parse_argo_dag_dependencies()` walks the graph to report only the root
  cause, not every blocked rung.

## K3s Workflows

- **Login shell (`bash -lc`) is required for all ROS commands in K3s pods**. Non-login shells don't
  source `.bashrc`, so `ros2`, `colcon`, and other ROS tools aren't in PATH. This was fixed
  independently across 6 rungs before being standardized. Always use `bash -lc` for kubectl exec
  commands that run ROS tools.
- **COLCON_TRACE unbound variable crashes scripts with `set -u`**. When `COLCON_TRACE` is unset and
  the script uses `set -u`, sourcing ROS setup fails. Fix: set `COLCON_TRACE=''` before sourcing.
  This appeared in rungs 7, 8, and 10.
- **kubectl cp is needed to sync code into pods in ARC mode**. ARC runner pods don't have host
  workspace mounts — `hostPath` anchors were migrated to `emptyDir`. Use `kubectl cp` or init
  containers to copy fresh code into the workload pod before running tests.
- **ORAS must be installed to a user-writable location**. CI pods run as non-root; ORAS installed to
  `/usr/local/bin` fails. Install to `~/.local/bin` and add to PATH. This was fixed independently in
  rungs 4, 9, and 12.
- **ARC detection is needed in workflows that run on both Docker and K3s**. Workflows that migrated
  from Docker to K3s need conditional logic (check for `arc-systems` namespace or label) to choose
  between `docker exec` and `kubectl exec`.
- **AMENT_PREFIX_PATH must be explicitly set in non-interactive shells**. After sourcing ROS setup
  in a kubectl exec context, AMENT_PREFIX_PATH may not include the merged install path. Export it
  explicitly after sourcing.
- **CUDA PATH setup is required before any GPU test in K3s pods**. `CUDA_HOME` and adding
  `/usr/local/cuda/bin` to PATH must happen before running cuVSLAM or other GPU tests. Without it,
  CUDA-dependent packages fail silently.

## Per-Rung Insights

- **cuVSLAM diagnostics: use pre-built packages, don't build from source**. Building cuVSLAM in CI
  pods OOMs or exceeds the 60-minute timeout. Use pre-built `.deb` packages from the Docker image
  instead of `colcon build`.
- **Parity tests: use `validation` test group on 8GB nodes**. Running the full parity test group
  OOMs the Jetson. Filter to `validation` group to stay within memory constraints.
- **VLM tests: bag replay must run sequentially before VLM inference**. Running rosbag play and VLM
  tests in parallel causes a memory spike that OOMs the node on 8GB shared memory.
- **VLM tests: use aggressive memory management**. Reduce resolution to 160x120, enable aggressive
  GC (`aggressive_gc` parameter), and scale down ARC runners before VLM tests to free memory.
- **VLM Nav2: NIM API health check must run inside the workload pod**. The ARC runner can't reach
  the NIM endpoint — the check must `kubectl exec` into the workload pod.
- **pip install in CI steps can OOM on 8GB**. Don't `pip install` dependencies at runtime;
  pre-install everything in the Docker image. A single pip install was enough to tip the node into
  OOM.
- **E2E tests: don't hardcode pod names**. Pod names include random suffixes and change on every
  deployment. Always use label selectors (`app.kubernetes.io/component=workload`).

## Sentinel CLI

- **kubectl subprocess calls don't inherit shell PATH**. The `argo` CLI lives in `~/bin/` which
  isn't in subprocess PATH. Must inject `env["PATH"] = f"{argo_path}:{env['PATH']}"` explicitly.
- **Transient kubectl errors should be retried**. Connection refused, I/O timeout, and context
  deadline exceeded get 3 retries with 5s backoff. Non-retryable errors (permission denied) fail
  fast.
- **Stop-loss checks both error strings and exit codes**. Critical error list (`"kernel panic"`,
  `"oomkilled"`) catches text in stderr; exit code 137 catches OOMKilled even without text.
- **Both nano1 and nano2 run all 14 ladder rungs**. Both have identical hardware (RealSense D435,
  8GB shared). No per-node restrictions exist.
- **LLM system prompt must encode hardware constraints explicitly**. Both nodes have RealSense D435
  cameras and can run rosbag play. The prompt now reflects this.
- **LLM calls always have a hardcoded fallback**. When Anthropic API is unavailable (network, rate
  limit, no key), sentinel falls back to pattern-matching analysis from common error types.
- **API key retrieval cascades**: env var `ANTHROPIC_API_KEY` → 1Password wrapper script → None. The
  wrapper abstracts the secret source; fallback is `--skip-llm` mode.
- **Workflow watching only prints status changes**. The poll loop (10s interval) compares current
  rung phases against previous and only logs on change — avoids log spam during long workflows.
- **Host-persisted logs enable remote autonomous diagnosis**. All workflow logs go to
  `/tmp/isaac_ros_logs/k3s-{workflow}/`. An agent can SSH to the Jetson and read logs without
  needing interactive `kubectl logs`.

## Test Ladder

- **Quick_check mode runs only previously failed rungs**. Failed rung list is pushed to Zot as OCI
  artifact (`isaac-ros/ladder-failed-rungs:nano2`). On next run, quick_check pulls the list and
  submits only those rungs — avoids 20+ minute full ladder for known issues.
- **L0 Manifest Gate validates annotations before pulling**. `skopeo inspect` (KB, not GB) checks
  OCI annotations. Broken images are rejected before the 3GB+ download even starts.
- **ORAS push to Zot uses `--allow-any-path`** for flexibility. Explicit media type suffix
  (`:text/plain`, `:application/json`) is required on the artifact reference.
- **GPU validation runs before unit tests** (rung 7 before 8). cuVSLAM needs CUDA, so the GPU must
  be validated before any test that depends on GPU-accelerated packages.
- **Both nanos have identical hardware and run all 14 rungs**. No per-node restrictions.

## Deployment (apply.sh)

- **Delete stale deployments before applying new overlays**. Generic `isaac-ros-custom` deployments
  persist alongside node-specific ones and cause confusion. Clean up first.
- **Use `--server-side` apply when a deployment already exists**. Regular `kubectl apply` can delete
  running pods. Server-side apply preserves them.
- **ARC overlay paths use `-arc` suffix**: `arc-nano1-arc`, `arc-nano2-arc` — not `arc-nano1`.
- **Delete classic runners, don't just scale to 0**. Scaled-to-zero classic deployments can still
  hold GPU resources and prevent ARC pods from scheduling.
- **hostPath anchor mounts don't exist on ARC runners**. The migration from `hostPath` anchors to
  `emptyDir` was necessary because ARC runner pods don't have host workspace mounts. Use init
  containers to copy from the image instead.
- **Node selectors prevent wrong-node scheduling**. Always set `kubernetes.io/hostname: nano1` or
  `nano2` in overlays. Without it, a pod may schedule on the wrong node with wrong paths/config.

## Zot Registry

- **Zot uses Nginx sidecar for TLS, not native TLS**. Zot's native TLS config format is incompatible
  with newer versions. Nginx handles HTTPS on :30500, Zot runs HTTP internally.
- **Large images need bigger nginx `client_max_body_size`**. Set to 5G (default 1G rejects 3GB+
  Isaac ROS images). The setting is in `k3s/zot-tls.yaml`.
- **Zot `gc: false` in ConfigMap**. Don't enable garbage collection — it can delete layers still
  referenced by running pods on edge devices with intermittent connectivity.

## ROS Pipeline

- **QoS overrides in `config/qos_overrides.yaml` are mandatory**. Missing file silently drops frames
  — SLAM gets no data but doesn't error. Camera images use `best_effort`, camera_info uses
  `reliable` + `transient_local` (latched).
- **Argo task outputs require explicit parameter handoff**. An output from one DAG step must be
  declared in `arguments.parameters` and referenced by name in downstream `depends` steps. Argo
  doesn't auto-propagate.

## 1Password Connect

- **The CRD kind is `OnePasswordItem` (capital O, P, I) — this is immutable**. It's defined by the
  1Password Connect Operator CRD (`onepassworditems.onepassword.com`). You cannot change it to
  `1PasswordItem` — only comments and prose should use `1Password` (digit 1). The `kind:` field must
  stay `OnePasswordItem`.
- **The namespace is `1password` (digit 1)**. See CronJob Authoring Pitfalls above for the full
  breakdown.
- **Operator deployed != secrets synced**. The 1Password Connect operator is deployed and running,
  but it has zero `OnePasswordItem` resources configured. The operator watches for CRs and syncs
  secrets — without CRs, no secrets are injected into any namespace. To sync a secret (e.g., GitHub
  PAT for rungs 13-14): create a `OnePasswordItem` manifest pointing to the vault item and target
  namespace, then add `envFrom` to the pod spec.
- **Sentinel depends on 1Password for API keys**. If the connect pod goes down, sentinel falls back
  to `ANTHROPIC_API_KEY` env var or `--skip-llm` mode.
- **The `op_api_key_wrapper.sh` script caches credentials**. If you rotate secrets in 1Password,
  restart the wrapper or wait for cache expiry.
- **Helm state can corrupt while pods still run**. `helm-health-monitor` catches this by checking
  `helm get manifest` across all namespaces, not just pod status.


# === WORKFLOWS.md ===

# Sentinel Workflows — Command Sequences & Decision Trees

## Basic Commands

```bash
# Watch active workflow
sentinel nano2 --watch-workflow

# Get workflow status (no watch)
sentinel nano2 --get-workflow-status

# Run Argo CLI command
sentinel nano2 --run-argo 'get workflows'

# Resubmit a failed rung
sentinel nano2 --resubmit-rung 7

# Parse DAG dependencies
sentinel nano2 --parse-dag k3s/argo-ladder-workflow.yaml

# Diagnose PVC writability
sentinel nano2 --diagnose-pvc zot-data --pvc-namespace zot

# Fetch ladder results from Zot
sentinel nano2 --fetch-results
sentinel nano2 --fetch-failed-rungs

# Partition-aware disk check
sentinel nano2 --check-partition

# LLM analysis of failure state
sentinel nano2 --analyze /path/to/state.json

# Skip LLM (offline/hardcoded analysis)
sentinel nano2 --skip-llm

# 1Password recovery
sentinel nano2 --reinstall-onepassword

# Test ladder (via Argo, not sentinel CLI)
argo submit k3s/argo-ladder-workflow.yaml -n argo -p target=nano2
argo submit k3s/argo-ladder-workflow.yaml -n argo -p target=nano2 -p quick_check=true
```

## Exit Codes

| Code | Meaning                 |
| ---- | ----------------------- |
| 0    | Success / No failures   |
| 1    | Test failures detected  |
| 2    | Critical error (halted) |
| 3    | Configuration error     |

## Decision Tree: Pod Not Starting

```
Pod not starting?
├── Pending?
│   ├── ResourceQuota exceeded? → kubectl describe resourcequota -n <ns>
│   ├── NodeSelector mismatch? → kubectl describe pod, check node labels
│   └── PVC not bound? → sentinel --diagnose-pvc <name>
├── ImagePullBackOff?
│   ├── Zot unreachable? → curl 192.168.100.1:30500/v2/
│   ├── Image tag missing? → curl .../v2/<image>/tags/list
│   └── Cert issue? → check network-health-monitor logs
├── CrashLoopBackOff?
│   ├── OOMKilled? → check events, reduce memory limit
│   ├── App error? → kubectl logs --previous
│   └── Missing config? → kubectl describe pod, check env/secrets
└── ContainerCreating (stuck)?
    └── Check events: kubectl describe pod <name>
```

## Decision Tree: Workflow Queued But Not Running

```
Workflow not starting?
├── No ARC runners? → kubectl get pods -n arc-systems
│   ├── Controller down? → kubectl rollout restart deployment arc-controller-gha-rs-controller -n arc-systems
│   ├── Listener crashloop? → check for stale EphemeralRunnerSet
│   └── No runners registered? → gh api repos/.../actions/runners
├── Stale EphemeralRunnerSet?
│   1. kubectl delete EphemeralRunnerSet <name> -n arc-systems
│   2. kubectl delete AutoscalingListener <name> -n arc-systems
│   3. kubectl rollout restart deployment arc-controller-gha-rs-controller -n arc-systems
├── Scale set ID mismatch?
│   1. kubectl delete AutoscalingRunnerSet <name> -n arc-systems
│   2. kubectl apply -k k3s/overlays/arc-nano<N>-arc
│   3. Verify listener connects: kubectl logs <listener> -n arc-systems --tail=10
└── Orphaned GitHub runners?
    → gh api -X DELETE /repos/.../actions/runners/<id>
```

## Decision Tree: Node Unresponsive

```
Node unresponsive?
├── Disk pressure? → df -h /
│   ├── > 85%? → kubectl create job --from=cronjob/disk-preemptive-cleanup manual -n isaac-ros
│   └── > 70%? → investigate /var/lib, journalctl --vacuum-time=1d
├── OOM / swap death spiral? → free -h
│   ├── Swap > 2GB? → kill memory-hungry pods immediately
│   └── Check OOM events: kubectl get events --field-selector reason=OOMKilled
├── Thermal throttle? → cat /sys/class/thermal/thermal_zone0/temp
│   └── > 75000? → tegrastats, consider shedding workloads
└── K3s stuck? → check for zombie processes, may need sudo pkill -9 k3s
```

## Decision Tree: Buildah Workflow Failing

```
Build workflow failing?
├── Stuck at preflight? → argo logs <wf> -n isaac-ros --step preflight-disk
│   ├── System disk > 70%? → disk cleanup, resubmit
│   └── Not on Jetson? → preflight-jetson blocks non-Jetson hardware
├── Build step fails? → ls ~/tmp/buildah-logs/<workflow>/build-image.log
│   ├── VFS storage error? → buildah needs --storage-driver=vfs on Jetson
│   ├── Cache full? → buildah system prune -f --keep-storage=2GB
│   └── FUSE missing? → modprobe fuse, check privileged:true + /dev/fuse mount
├── Push fails? → check Zot connectivity
│   └── curl -sk https://192.168.100.1:30500/v2/
├── Tag is _unknown-*? → DOCKERFILE_COMMIT not resolved at submission
│   └── Use: -p DOCKERFILE_COMMIT=$(git rev-parse --short HEAD)
└── check-existing skipped build? → image+commit already in Zot
    └── Verify: curl -sk https://192.168.100.1:30500/v2/rs_humble/tags/list
```

## Decision Tree: Argo Ladder Stuck

```
Ladder workflow not progressing?
├── All pods Pending? → eventbus down
│   └── kubectl rollout restart deployment eventbus-controller-manager -n argo-events
├── Completed pods accumulating? → stalled eventbus prevents TTL cleanup
│   └── kubectl delete pods -n argo --field-selector=status.phase=Succeeded --force --grace-period=0
├── Rung passes but workload pod is down?
│   └── Verify pod status: kubectl get pods -n isaac-ros --field-selector=status.phase=Running
└── DAG validation error?
    └── Don't mix continueOn with depends — use dependency chains only
```

## CI Integration

### From GitHub Actions

```yaml
- name: Run Sentinel Diagnostics
  run: |
    python sentinel_core.py --target nano2 --analyze state.json
```

### From CI Script (both nodes)

```bash
#!/bin/bash
for node in nano1 nano2; do
    echo "=== Checking $node ==="
    python3 sentinel_core.py --target $node --get-workflow-status
done
```

## Deploy Hooks

```bash
kubectl apply -f k3s/network-health-hook.yaml
kubectl apply -f k3s/oom-hook.yaml
kubectl apply -f k3s/zombie-hook.yaml
kubectl apply -f k3s/disk-pressure-hook.yaml
kubectl apply -f k3s/thermal-hook.yaml

# Verify
kubectl get cronjobs -n isaac-ros
kubectl get jobs -n isaac-ros --sort-by='.lastTimestamp'
```

## Manual Hook Triggers

```bash
# OOM check
kubectl create job --from=cronjob/oom-monitor oom-test -n isaac-ros

# Disk preemptive cleanup (75% threshold)
kubectl create job --from=cronjob/disk-preemptive-cleanup disk-test -n isaac-ros

# Network health
kubectl create job --from=cronjob/network-health-monitor net-test -n isaac-ros

# Thermal check
kubectl create job --from=cronjob/thermal-monitor thermal-test -n isaac-ros

# Zombie check
kubectl create job --from=cronjob/zombie-monitor zombie-test -n isaac-ros

# Zot cleanup (cross-namespace: runs in isaac-ros, targets zot)
kubectl create job --from=cronjob/zot-cleanup zot-test -n isaac-ros
```

## Self-Healing Flow

```
Failure Detected (CronJob)
    ↓
1. Detect: CronJob identifies failure type
    ↓
2. Diagnose: sentinel --analyze <state.json> or kubectl logs
    ↓
3. Recover: Delete stale pod → Controller recreates → Verify healthy
    ↓
4. Report: Job logs + state persisted
```

## Deploy Full Stack

```bash
# On nano1
./k3s/apply.sh full-nano1

# On nano2
./k3s/apply.sh full-nano2
```

See `REFERENCE.md` for overlay paths, quota values, and hook thresholds.

