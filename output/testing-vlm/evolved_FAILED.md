---
name: testing-vlm
description: |
 Test VLM pipeline on Jetson Orin Nano (rungs 13-18). Use when: running VLM CI tests,
 debugging VLM/Nav2 integration, Cloudflare Worker issues, NITROS JPEG bridge,
 semantic distillation, or memory stress validation.
proficiency: 0.90
composition:
 after: [running-isaac-ros-tests, managing-k3s-cluster, debugging-isaac-ros-containers]
 before: []
latent_vars:
- contains_gpu: true
- hardware_runner_type: jetson-orin-nano
- memory_constraint_8gb: true
- nvidia_api_key_required: true
- cloudflare_worker_dependency: true
- mcap_replay_required: true
- protobuf_enabled: true
- nitros_jpeg_bridge: true
- gh_actions_memory_overhead: true
---



# === SKILL.md ===

---
name: testing-vlm
description: |
 Test VLM pipeline on Jetson Orin Nano (rungs 13-18). Use when: running VLM CI tests,
 debugging VLM/Nav2 integration, Cloudflare Worker issues, NITROS JPEG bridge,
 semantic distillation, or memory stress validation.
proficiency: 0.90
composition:
 after: [running-isaac-ros-tests, managing-k3s-cluster, debugging-isaac-ros-containers]
 before: []
latent_vars:
- contains_gpu: true
- hardware_runner_type: jetson-orin-nano
- memory_constraint_8gb: true
- nvidia_api_key_required: true
- cloudflare_worker_dependency: true
- mcap_replay_required: true
- protobuf_enabled: true
- nitros_jpeg_bridge: true
- gh_actions_memory_overhead: true
---

## Test Ladder Rungs (13-18)

| Rung | Workflow                           | Purpose                                      |
| ---- | ---------------------------------- | -------------------------------------------- |
| 13   | `5-vlm-mcap-test.yml`              | VLM sanity, MCAP replay, Worker, JPEG bridge |
| 14   | `5-vlm-nav2-test.yml`              | VLM + Nav2 tool calling, text parsing        |
| 15   | `5-vlm-memory-stress-test.yml`     | Three-tier memory validation                 |
| 16   | `6-semantic-distillation-test.yml` | Real distiller, live topics                  |
| 17   | `7-golden-mcap-validation.yml`     | Scene Graph F1 >= 0.7                        |
| 18   | `8-multi-scenario-vlm.yml`         | All 4 scenarios                              |

## Quick Start

```bash
# Rungs 13-15: Basic VLM
gh workflow run 5-vlm-mcap-test.yml -f runner_label=nano2
gh workflow run 5-vlm-nav2-test.yml -f runner_label=nano2
gh workflow run 5-vlm-memory-stress-test.yml -f runner_label=nano2

# Rungs 16-18: Advanced (see WORKFLOWS.md)
gh workflow run 6-semantic-distillation-test.yml -f runner_label=nano2

# Via Argo ladder
argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2
```

## Prerequisites (Pre-Flight)

- NVIDIA_API_KEY in pod (1Password Connect)
- Zot reachable: `oras repo ls 192.168.100.1:30500`
- MCAP: `bags/test_mcap:latest` (~4MB)
- CF Worker: `curl https://mcap-nim-shaper.kieran-3e9.workers.dev/health`
- Correct kubectl context: `kubectl config current-context`
## Critical Rules

1. **Scale ARC to 0 before Nav2 tests** -- VLM + Nav2 = ~6GB
2. **Use CF Tunnel in CI** -- `api_endpoint:=https://nvidia-bridge.rossollc.com/v1/chat/completions`
3. **NITROS JPEG is canonical** -- ImageCompressionNode (NVJPEG), not H.264
4. **Protobuf field 13 = wire type 5** -- memory_pressure is float
5. **Apply WorkflowTemplate after edits** --
 `kubectl apply -f k3s/argo-ladder-workflow.yaml -n argo`

## Memory Constraints (CRITICAL)

**Pod baseline**: 4.4GB used, ~2.5GB available
**GH Actions overhead**: 100-200MB per `kubectl exec` call
**VLM node**: ~46MB when running

**Solutions**:
1. Use `test_type=sanity` with single `kubectl exec` pattern
2. Use `5-vlm-sanity-test.yml` for minimal memory tests
3. Pre-installed dependencies in Dockerfile (avoid runtime pip)
4. See `docs/diagnostics/gh-actions-memory-overhead.md` for analysis

## File Index

| File           | Purpose                               |
| -------------- | ------------------------------------- |
| `WORKFLOWS.md` | Step-by-step test procedures          |
| `REFERENCE.md` | API endpoints, topic maps, parameters |
| `RECOVERY.md`  | Failure diagnosis and fixes           |
| `EXAMPLES.md`  | Verifiable walkthroughs               |
| `TACTICAL.md`  | Strategic know-how from incidents     |

## Related Skills

- `running-isaac-ros-tests` -- Ladder execution
- `debugging-isaac-ros-containers` -- Pod debugging
- `managing-k3s-cluster` -- ResourceQuotas, Zot

---

_Updated March 2026 per AGENT_SKILLS.md (XSkill/CARL)._


# === EXAMPLES.md ===

# EXAMPLES.md -- Testing VLM

Walkthroughs with verifiable expected outcomes.

## Example 1: VLM Sanity Check

**Goal**: Verify VLM node starts, receives camera frame, and responds to query.

```bash
POD=$(kubectl get pods -n isaac-ros -l app.kubernetes.io/component=workload \
  --field-selector=status.phase=Running -o name 2>/dev/null | sed 's|pod/||' | head -1)

kubectl exec -n isaac-ros "$POD" -- bash -c '
  source /opt/ros/humble/setup.bash
  source /workspaces/isaac_ros-dev/install/setup.bash 2>/dev/null || true
  cd /workspaces/isaac_ros-dev/src/isaac_ros_custom

  python3 vlm_node.py --ros-args \
    -p max_image_width:=160 -p max_image_height:=120 -p image_quality:=70 \
    -p gc_interval:=10.0 > /tmp/vlm.log 2>&1 &
  VLM_PID=$!
  sleep 5

  # Verify running
  ps -p $VLM_PID > /dev/null && echo "PASS: VLM running" || { echo "FAIL: VLM not running"; exit 1; }

  # Send query
  ros2 topic pub /vlm/query std_msgs/String "data: test" --once &

  # Wait for response
  for i in $(seq 1 10); do
    RESP=$(ros2 topic echo /vlm/response std_msgs/String --spin-time 1 --once 2>/dev/null || true)
    if [ -n "$RESP" ]; then
      echo "PASS: Got VLM response"
      kill $VLM_PID 2>/dev/null
      exit 0
    fi
    sleep 1
  done
  echo "WARN: No response (may need MCAP replay for camera frames)"
  kill $VLM_PID 2>/dev/null
'
```

**Expected**: VLM node starts, responds within 10s if camera frames available.

## Example 2: MCAP Replay + VLM Query

**Goal**: Download MCAP from Zot, replay camera, and get VLM scene description.

### Step 1: Validate Zot Repository

```bash
# Ensure repository exists before pulling
oras repo ls 192.168.100.1:30500 | grep test_mcap

# Expected output: bags/test_mcap
```

### Step 2: Download MCAP

```bash
POD=$(kubectl get pods -n isaac-ros -l app.kubernetes.io/component=workload \
  --field-selector=status.phase=Running -o name 2>/dev/null | sed 's|pod/||' | head -1)

# Download MCAP using ORAS (use test_mcap - it's validated)
kubectl exec -n isaac-ros "$POD" -- bash -c '
  mkdir -p /tmp/mcap
  cd /tmp/mcap

  # Install ORAS if needed
  if ! command -v oras &> /dev/null; then
    curl -Lo oras.tar.gz "https://github.com/oras-project/oras/releases/download/v1.2.2/oras_1.2.2_linux_arm64.tar.gz"
    tar -xzf oras.tar.gz -C /tmp oras
    chmod +x /tmp/oras
    rm oras.tar.gz
  fi
  export PATH=/tmp:$PATH

  # Pull test_mcap from Zot (HTTPS, ~4MB, has camera topics)
  oras pull 192.168.100.1:30500/bags/test_mcap:latest --allow-path-traversal

  # Verify download
  ls -la /tmp/mcap/
  file /tmp/mcap/*.mcap 2>/dev/null && echo "MCAP valid" || echo "MCAP missing"
'
```

**Expected**: MCAP file ~4MB in `/tmp/mcap/`, `file` command shows "Rosbag2 SQLite3 database".

## Example 3: Validate Image Capabilities Before VLM Test

**Goal**: Verify image has VLM and Nav2 capabilities before running tests.

```bash
POD=$(kubectl get pods -n isaac-ros -l app.kubernetes.io/component=workload \
  --field-selector=status.phase=Running -o name 2>/dev/null | sed 's|pod/||' | head -1)

# Validate VLM provider
VLM_PROVIDER=$(kubectl exec -n isaac-ros "$POD" -- bash -c '
  source /opt/ros/humble/setup.bash 2>/dev/null || true
  source /workspaces/isaac_ros-dev/install/setup.bash 2>/dev/null || true
  if ros2 pkg list 2>/dev/null | grep -q "isaac_ros_vlm"; then
    echo "isaac_ros_vlm"
  elif [ -f /workspaces/isaac_ros-dev/src/isaac_ros_custom/vlm_node.py ]; then
    echo "vlm_node"
  else
    echo "null"
  fi
' 2>/dev/null || echo "unknown")

echo "VLM provider: $VLM_PROVIDER"

if [ "$VLM_PROVIDER" = "null" ] || [ "$VLM_PROVIDER" = "unknown" ]; then
  echo "ERROR: Image lacks VLM capability"
  echo "Rebuild with: argo submit k3s/buildah-workflow.yaml -n isaac-ros"
  exit 1
fi

# Validate Nav2 (for Nav2 tests)
NAV2_ENABLED=$(kubectl exec -n isaac-ros "$POD" -- bash -c '
  source /opt/ros/humble/setup.bash 2>/dev/null || true
  if ros2 pkg list 2>/dev/null | grep -qE "nav2_bringup|nvblox_nav2"; then
    echo "true"
  else
    echo "false"
  fi
' 2>/dev/null || echo "unknown")

echo "Nav2 enabled: $NAV2_ENABLED"

# Validate NVIDIA_API_KEY
kubectl exec -n isaac-ros "$POD" -- bash -c '
  if [ -z "${NVIDIA_API_KEY}" ]; then
    echo "ERROR: NVIDIA_API_KEY not set"
    exit 1
  fi
  echo "NVIDIA_API_KEY present (length: ${#NVIDIA_API_KEY})"
'

# Get provenance
COMMIT=$(kubectl exec -n isaac-ros "$POD" -- cat /etc/realsense_versions 2>/dev/null | grep -oP "commit=\K[a-f0-9]+" || echo "unknown")
echo "Image built from commit: $COMMIT"
```

**Expected**: VLM provider non-null, Nav2 true (for Nav2 tests), NVIDIA_API_KEY present.

### Step 3: Replay MCAP + Query VLM

```bash
# Replay + query
kubectl exec -n isaac-ros "$POD" -- bash -c '
source /opt/ros/humble/setup.bash
source /workspaces/isaac_ros-dev/install/setup.bash 2>/dev/null || true
cd /workspaces/isaac_ros-dev/src/isaac_ros_custom

ros2 bag play /tmp/mcap/*.mcap -l --remap \
  "/camera/color/image_raw:=/camera/camera/color/image_raw" &
  sleep 8

  ros2 topic hz /camera/camera/color/image_raw --spin-time 2 || echo "No camera topics"

  python3 vlm_node.py --ros-args \
    -p max_image_width:=320 -p max_image_height:=240 -p image_quality:=70 \
    -p api_endpoint:=https://nvidia-bridge.rossollc.com/v1/chat/completions \
    -p enable_streaming:=False > /tmp/vlm.log 2>&1 &
  sleep 5

  ros2 topic pub /vlm/query std_msgs/String "{data: \"Describe what you see\"}" --once

  for i in {1..60}; do
    RESP=$(timeout 1 ros2 topic echo /vlm/response std_msgs/String --once 2>/dev/null || echo "")
    if [ -n "$RESP" ]; then
      echo "PASS: $RESP"
      kill %1 2>/dev/null; kill %2 2>/dev/null
      exit 0
    fi
    sleep 1
  done
  echo "FAIL: No response in 60s"
  cat /tmp/vlm.log | tail -20
'
```

**Expected**: VLM describes scene content from MCAP replay within 60s.

## Example 4: Download and Analyze Failed Run Logs

**Goal**: Get local copy of GitHub Actions logs for offline analysis.

```bash
# Download full run logs
gh run view <run-id> --log > /tmp/vlm_full_run.log

# Download failed steps only
gh run view <run-id> --log-failed > /tmp/vlm_failed.log

# Search for specific errors
grep -E "exit code 137|OOM|Killed|Low memory|NOT FOUND" /tmp/vlm_failed.log

# Extract memory warning
grep "Runner available memory" /tmp/vlm_failed.log
grep "Low memory" /tmp/vlm_failed.log

# View in browser
open "https://github.com/explicitcontextualunderstanding/isaac_ros_custom/actions/runs/<run-id>"
```

**Expected**: Log files downloaded, searchable locally. Exit code 137 indicates OOM kill.

## Example 5: Nav2 Tool Calling

**Goal**: VLM parses navigation command and sends goal to Nav2.

```bash
POD=$(kubectl get pods -n isaac-ros -l app.kubernetes.io/component=workload \
  --field-selector=status.phase=Running -o name 2>/dev/null | sed 's|pod/||' | head -1)

# Ensure Nav2 is running
kubectl exec -n isaac-ros "$POD" -- bash -c '
source /opt/ros/humble/setup.bash
ros2 action list 2>/dev/null | grep navigate_to_pose && echo "PASS: Nav2 ready" || echo "FAIL: Nav2 not running"
'

# Test tool calling
kubectl exec -n isaac-ros "$POD" -- bash -c '
source /opt/ros/humble/setup.bash
cd /workspaces/isaac_ros-dev/src/isaac_ros_custom

ros2 bag play /tmp/mcap/*.mcap -l --remap \
  "/camera/color/image_raw:=/camera/camera/color/image_raw" &
  sleep 10

  python3 vlm_node.py --ros-args \
    -p enable_tools:=True -p enable_nav2:=True -p enable_streaming:=False \
    -p api_endpoint:=https://nvidia-bridge.rossollc.com/v1/chat/completions \
    > /tmp/vlm_nav2.log 2>&1 &
  sleep 5

  ros2 topic pub /vlm/query std_msgs/String "{data: \"Navigate to x=1.0 y=0.5\"}" --once

  # Check if Nav2 received goal
  for i in {1..60}; do
    if ros2 topic echo /navigate_to_pose/goal --once --timeout 1 2>/dev/null | grep -q "pose:"; then
      echo "PASS: Nav2 received navigation goal"
      kill %1 2>/dev/null; kill %2 2>/dev/null
      exit 0
    fi
    sleep 2
  done
  echo "WARN: No Nav2 goal (tool calling may have returned text instead)"
  cat /tmp/vlm_nav2.log | tail -10
'
```

**Expected**: Nav2 receives goal with `x=1.0, y=0.5` on `/navigate_to_pose/goal`.

## Example 6: Text Parsing Fallback

**Goal**: VLM returns text with coordinates, parser extracts them for Nav2.

```bash
# Test parser standalone (no VLM node needed)
kubectl exec -n isaac-ros "$POD" -- python3 -c "
from isaac_ros_custom.vlm_nav2_parser import parse_navigation_command

tests = [
    ('Navigate to x=1.0 y=2.0', {'x': 1.0, 'y': 2.0}),
    ('Go to x: 3.5, y: -1.2', {'x': 3.5, 'y': -1.2}),
    ('Target y=0.5 x=2.0', {'x': 2.0, 'y': 0.5}),
    ('No coordinates here', None),
]

all_pass = True
for text, expected in tests:
    result = parse_navigation_command(text)
    status = 'PASS' if result == expected else 'FAIL'
    if status == 'FAIL': all_pass = False
    print(f'{status}: parse(\"{text}\") = {result} (expected {expected})')

print(f'\nOverall: {\"ALL PASSED\" if all_pass else \"SOME FAILED\"}')
"
```

**Expected**: All 4 test cases pass (3 matches, 1 None).

## Example 7: Cloudflare Worker Health

**Goal**: Verify Worker, Tunnel, and NIM round-trip.

```bash
# Worker health
HEALTH=$(curl -s https://mcap-nim-shaper.kieran-3e9.workers.dev/health)
echo "Version: $(echo $HEALTH | jq -r '.version')"
echo "Status: $(echo $HEALTH | jq -r '.status')"

# Tunnel DNS
echo "Tunnel IP: $(dig nvidia-bridge.rossollc.com +short)"

# NIM round-trip through tunnel
RESP=$(curl -s -w "\n%{http_code}" \
  -H "Authorization: Bearer $NVIDIA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"nvidia/llama-3.1-nemotron-nano-8b-v1","messages":[{"role":"user","content":"hi"}],"max_tokens":5}' \
  https://nvidia-bridge.rossollc.com/v1/chat/completions)
HTTP_CODE=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | sed '$d')

echo "HTTP: $HTTP_CODE"
echo "Body: $(echo $BODY | jq -r '.choices[0].message.content // "empty")' 2>/dev/null)
```

**Expected**: Version non-empty, status "ok", HTTP 200, body contains model response.

## Example 8: NITROS JPEG Bridge + NimSender

**Goal**: Verify hardware-accelerated JPEG encoding and Protobuf transmission to Worker.

```bash
POD=$(kubectl get pods -n isaac-ros -l app.kubernetes.io/component=workload \
  --field-selector=status.phase=Running -o name 2>/dev/null | sed 's|pod/||' | head -1)

kubectl exec -n isaac-ros "$POD" -- bash -c '
  source /opt/ros/humble/setup.bash
  source /workspaces/isaac_ros-dev/install/setup.bash 2>/dev/null || true
  cd /workspaces/isaac_ros-dev/src/isaac_ros_custom

  # 1. Start MCAP replay (camera input)
ros2 bag play /tmp/mcap/*.mcap -l --remap \
  "/camera/color/image_raw:=/camera/camera/color/image_raw" &
  sleep 5

  # 2. Launch NITROS JPEG bridge
  ros2 launch isaac_ros_custom nitros_bridge.launch.py > /tmp/nitros.log 2>&1 &
  sleep 5

  # 3. Verify JPEG stream
  if ros2 topic hz /vlm/jpeg_stream --spin-time 3 2>/dev/null | grep -q "average rate"; then
    echo "PASS: /vlm/jpeg_stream publishing"
  else
    echo "FAIL: No JPEG stream - check nitros.log"
    cat /tmp/nitros.log | tail -20
    exit 1
  fi

  # 4. Start NimSender (Protobuf mode)
  ros2 run isaac_ros_custom nim_sender_node --ros-args \
    -p use_protobuf:=true \
    -p input_topic:=/vlm/jpeg_stream \
    -p worker_url:=https://mcap-nim-shaper.kieran-3e9.workers.dev > /tmp/nim.log 2>&1 &
  sleep 5

  # 5. Check NimSender status
  for i in $(seq 1 10); do
    STATUS=$(timeout 1 ros2 topic echo /vlm/status std_msgs/String --once 2>/dev/null || echo "")
    if [ -n "$STATUS" ]; then
      echo "PASS: NimSender status: $STATUS"
      break
    fi
    sleep 1
  done
'
```

**Expected**: JPEG stream publishes at camera framerate, NimSender reports status to Worker.

## Example 9: Semantic Distillation with VLM

**Goal**: Publish mock scene state, verify VLM uses causal graph context.

```bash
POD=$(kubectl get pods -n isaac-ros -l app.kubernetes.io/component=workload \
  --field-selector=status.phase=Running -o name 2>/dev/null | sed 's|pod/||' | head -1)

kubectl exec -n isaac-ros "$POD" -- bash -c '
  source /opt/ros/humble/setup.bash
  source /workspaces/isaac_ros-dev/install/setup.bash 2>/dev/null || true
  cd /workspaces/isaac_ros-dev/src/isaac_ros_custom

  # 1. Run unit tests first
  cd /workspaces/isaac_ros-dev
  python3 -m pytest tests/unit/test_semantic_distiller.py -v --tb=short 2>&1 | tail -25

  # 2. Publish mock scene state
  ros2 topic pub /vlm/scene_state std_msgs/String \
    "{data: \"{\\\"nodes\\\":[{\\\"id\\\":\\\"table_1\\\",\\\"label\\\":\\\"table\\\",\\\"centroid\\\":[1.5,1.2,0.0],\\\"parent_id\\\":\\\"floor\\\"},{\\\"id\\\":\\\"cup_1\\\",\\\"label\\\":\\\"cup\\\",\\\"centroid\\\":[1.5,1.2,0.8],\\\"parent_id\\\":\\\"table_1\\\"}],\\\"edges\\\":[{\\\"source\\\":\\\"table_1\\\",\\\"target\\\":\\\"cup_1\\\",\\\"relation\\\":\\\"supports\\\"}]}\"}" \
    --once &
  sleep 2

  # 3. Start VLM and query scene understanding
  cd /workspaces/isaac_ros-dev/src/isaac_ros_custom
  python3 vlm_node.py --ros-args \
    -p max_image_width:=160 -p max_image_height:=120 \
    -p enable_streaming:=False \
    -p api_endpoint:=https://nvidia-bridge.rossollc.com/v1/chat/completions > /tmp/vlm_sem.log 2>&1 &
  sleep 5

  ros2 topic pub /vlm/query std_msgs/String \
    "{data: \"Is there a cup on the table? Answer yes or no.\"}" --once

  for i in $(seq 1 30); do
    RESP=$(timeout 1 ros2 topic echo /vlm/response std_msgs/String --once 2>/dev/null || echo "")
    if [ -n "$RESP" ]; then
      echo "PASS: Got response: $RESP"
      echo "$RESP" | grep -qiE "cup|table|support" && \
        echo "PASS: VLM referenced scene objects" || \
        echo "WARN: VLM may not have used scene context"
      break
    fi
    sleep 1
  done
'
```

**Expected**: 19 unit tests pass, VLM responds and references scene objects (cup, table, supports).

## Example 10: YAML Block Scalar Indentation Diagnosis

**Goal**: Detect and fix YAML block scalar breaks in GitHub Actions workflows.

```bash
# Lint both VLM workflow files
yamllint .github/workflows/5-vlm-mcap-test.yml
yamllint .github/workflows/5-vlm-nav2-test.yml
actionlint .github/workflows/5-vlm-mcap-test.yml
actionlint .github/workflows/5-vlm-nav2-test.yml
```

**Typical error output**:

```
5-vlm-mcap-test.yml:316:1    warning  wrong indentation: expected 10 but found 0 (comment)
```

**Fix**: Indent the offending comment/code to match the block scalar base indent:

```yaml
# BEFORE (BROKEN): Comment at column 0 inside run: | block
- name: 'Test VLM Query'
  run: |
    kubectl exec ...
    # Install isaac_ros_custom - FAIL on error
    kubectl exec ...

# AFTER (FIXED): Comment indented to block scalar base
- name: 'Test VLM Query'
  run: |
    kubectl exec ...
      # Install isaac_ros_custom - FAIL on error
      kubectl exec ...
```

**Expected**: `yamllint` returns no errors. `actionlint` may still warn about non-critical issues.

## Example 11: JWT Null Byte Truncation Diagnosis

**Goal**: Verify JWT signature integrity in `_gh_auth()`.

```bash
# Check signature size directly (should be 256 for 2048-bit key)
POD=$(kubectl get pods -n argo -l workflows.argoproj.io/workflow=$(argo get @latest -n argo -o name | sed 's|workflow/||') -o name | grep "rung-vlm-test" | sed 's|pod/||')

# Method 1: Check raw signature byte count
kubectl exec -n argo "$POD" -- sh -c '
  printf "test" > /tmp/test_sign.txt
  openssl dgst -sha256 -sign /etc/gh-app/github_app_private_key -binary /tmp/test_sign.txt 2>/dev/null | wc -c
'
# Expected: 256
# If 253: null byte truncation — fix by piping directly to base64

# Method 2: Verify the canonical _gh_auth code in shared-scripts ConfigMap
kubectl get configmap ladder-shared-scripts -n argo -o yaml | grep -A2 "openssl.*base64"
# Should show pipe: openssl ... | base64 -w0 | tr ...
# Should NOT show intermediate variable: SIG=$(printf ... $RAW_SIG ...)
```

**Expected**: Signature is 256 bytes, ConfigMap shows pipe-based code.


# === RECOVERY.md ===

# RECOVERY.md -- Testing VLM

Verifiable if-then recovery paths for VLM test failures.

## GitHub App Authentication Failure (JWT Generation)

**Symptoms**: Test ladder fails at rung 13/14 with "Failed to generate JWT" error, may include
"Algorithm 'RS256' could not be found" or "No such file or directory" for private key.

### Check 1: gh-app-credentials Volume Mount

```bash
# Check if the volume mount exists on the rung-vlm-test template
kubectl get wftmpl isaac-ros-ladder -n argo -o yaml | grep -A 20 "rung-vlm-test"
```

**If missing**: The `gh-app-credentials` volume mount is required for GitHub App authentication.
Verify the template has:

```yaml
volumeMounts:
  - name: gh-app-credentials
    mountPath: /etc/gh-app
    readOnly: true
volumes:
  - name: gh-app-credentials
    secret:
      secretName: gh-app-credentials
      defaultMode: 0400
```

### Check 2: Cryptography Module Installation

```bash
# Test if cryptography module is available in the alpine container
argo logs @latest -c main isaac-ros-ladder-*-rung-vlm-test-* -n argo 2>&1 | grep -i "cryptography"
```

**If not installed**: The `py3-cryptography` module is required for RS256 algorithm support. Verify
the installation command in `_gh_auth()` function:

```bash
apk add --no-cache python3 py3-jwt py3-cryptography
```

### Check 3: gh-app-credentials Secret Exists

```bash
kubectl get secret gh-app-credentials -n argo -o yaml
```

**If missing**: The GitHub App credentials secret is not installed. Run:

```bash
# This should be installed via the 1Password Connect Operator
kubectl describe onepassworditem gh-app-credentials -n argo
# Check if the secret is synced
kubectl get secret gh-app-credentials -n argo
```

### Check 4: Validate Credential Files in Container

```bash
# Exec into the rung-vlm-test pod to check credential files
POD=$(kubectl get pods -n argo -l workflows.argoproj.io/workflow=$(argo get @latest -n argo -o name | sed 's|workflow/||') -o name | grep "rung-vlm-test" | sed 's|pod/||')
kubectl exec -n argo "$POD" -- ls -la /etc/gh-app/
# CRITICAL: Use tr -d '\n\r ' not cat — K8s appends trailing newlines to secret files
kubectl exec -n argo "$POD" -- tr -d '\n\r ' < /etc/gh-app/github_app_id 2>/dev/null
kubectl exec -n argo "$POD" -- tr -d '\n\r ' < /etc/gh-app/github_app_installation_id 2>/dev/null
# Check if private key is present and readable
kubectl exec -n argo "$POD" -- wc -l /etc/gh-app/github_app_private_key 2>/dev/null
```

**If files missing**: The secret mount failed. Check if the secret exists and has the correct
permissions.

### Check 5: Verify JWT Generation Command

```bash
# Try running the JWT generation command manually inside the container
POD=$(kubectl get pods -n argo -l workflows.argoproj.io/workflow=$(argo get @latest -n argo -o name | sed 's|workflow/||') -o name | grep "rung-vlm-test" | sed 's|pod/||')
kubectl exec -n argo "$POD" -- bash -c '
  cd /tmp
  APP_ID=$(cat /etc/gh-app/github_app_id)
  INSTALL_ID=$(cat /etc/gh-app/github_app_installation_id)
  KEY_FILE="/etc/gh-app/github_app_private_key"
  echo "Testing JWT generation with APP_ID=$APP_ID, INSTALL_ID=$INSTALL_ID"
  python3 -c "
import jwt
import time
with open(\"$KEY_FILE\", \"r\") as f:
    private_key = f.read()
payload = {
    \"iat\": int(time.time()),
    \"exp\": int(time.time()) + 60,
    \"iss\": \"$APP_ID\"
}
jwt_token = jwt.encode(payload, private_key, algorithm=\"RS256\")
print(\"Successfully generated JWT:\")
print(jwt_token.decode(\"utf-8\"))
"
'
```

**If this fails**: Check the error message for clues about the failure reason (e.g., invalid private
key, missing dependencies).

## Pre-Flight Validation Failures

**Symptoms**: Workflow fails immediately with "ERROR: Image lacks VLM capability" or similar.

### Check 1: Image Has VLM Capability

```bash
POD=$(kubectl get pods -n isaac-ros -l app.kubernetes.io/component=workload \
  --field-selector=status.phase=Running -o name 2>/dev/null | sed 's|pod/||' | head -1)

# Check VLM provider
kubectl exec -n isaac-ros "$POD" -- bash -c '
  source /opt/ros/humble/setup.bash 2>/dev/null || true
  source /workspaces/isaac_ros-dev/install/setup.bash 2>/dev/null || true
  if ros2 pkg list 2>/dev/null | grep -q "isaac_ros_vlm"; then
    echo "VLM provider: isaac_ros_vlm"
  elif [ -f /workspaces/isaac_ros-dev/src/isaac_ros_custom/vlm_node.py ]; then
    echo "VLM provider: vlm_node"
  else
    echo "VLM provider: null"
  fi
'
```

**If null**: Image lacks VLM support. Rebuild with VLM packages:

```bash
# Rebuild image via buildah workflow
argo submit k3s/buildah-workflow.yaml -n isaac-ros \
  -p DOCKERFILE_COMMIT=$(git rev-parse --short HEAD)
```

### Check 2: Nav2 Packages (Nav2 Test Only)

```bash
kubectl exec -n isaac-ros "$POD" -- bash -c '
  source /opt/ros/humble/setup.bash
  ros2 pkg list 2>/dev/null | grep -E "nav2_bringup|nvblox_nav2"
'
```

**If empty**: Image lacks Nav2. The VLM Nav2 integration test requires Nav2 packages.

### Check 3: NVIDIA_API_KEY Missing

```bash
kubectl exec -n isaac-ros "$POD" -- bash -c '
  echo "NVIDIA_API_KEY set: ${NVIDIA_API_KEY:+yes}"
  echo "Key length: ${#NVIDIA_API_KEY}"
'
```

**If empty**: 1Password Connect secret not synced. Run:

```bash
sentinel nano1 --reinstall-onepassword
kubectl rollout restart deployment op-connect -n 1password
# Wait 60s for sync, then retry
```

### Check 4: MCAP Validation Failure

```bash
# MCAP size check failed - repo may not exist
oras repo ls 192.168.100.1:30500 | grep bags

# Verify MCAP downloaded correctly
kubectl exec -n isaac-ros "$POD" -- ls -la /tmp/mcap/
```

**If empty or <1KB**: MCAP repository doesn't exist in Zot. Use `test_mcap`.

## No VLM Response (Timeout)

**Symptoms**: VLM node running, query published, no response within timeout.

### Check 1: NVIDIA API Key (if not caught by pre-flight)

```bash
kubectl exec -n isaac-ros "$POD" -- bash -c '
  API_STATUS=$(curl -s -w "%{http_code}" -o /tmp/test.json \
    -H "Authorization: Bearer $NVIDIA_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"nvidia/llama-3.1-nemotron-nano-8b-v1\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":1}" \
    "https://integrate.api.nvidia.com/v1/chat/completions" 2>/dev/null || echo "000")
  echo "API HTTP status: $API_STATUS"
  cat /tmp/test.json 2>/dev/null
'
```

**If 401**: Key invalid or expired. Check 1Password vault. **If 000**: Network unreachable from pod.
Check cluster networking. **If 429**: Rate limited. Wait 60s and retry.

### Check 3: CF Tunnel (if using tunnel endpoint)

```bash
curl -s -w "\nHTTP %{http_code}\n" \
  -H "Authorization: Bearer $NVIDIA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"nvidia/llama-3.1-nemotron-nano-8b-v1","messages":[{"role":"user","content":"hi"}],"max_tokens":1}' \
  https://nvidia-bridge.rossollc.com/v1/chat/completions
```

**If DNS failure**: `dig nvidia-bridge.rossollc.com +short` -- if empty, CF Tunnel is down.

## VLM Node OOM

**Symptoms**: VLM node process killed, `OOMKilled` in events, pod restarts.

```bash
kubectl get events -n isaac-ros --field-selector reason=OOMKilled
kubectl logs -n isaac-ros "$POD" --previous | grep -i "memory\|oom\|killed"
```

### Recovery

1. Scale down ARC runner:

   ```bash
   # Via GitHub Actions
   gh workflow run 5-vlm-nav2-test.yml -f runner_label=nano1
   # Workflow handles ARC scaling automatically (steps 4 and 14)
   ```

2. Reduce VLM resolution:

   ```bash
   # CLI args override parameters
   python3 vlm_node.py --max-image-width 160 --max-image-height 120 --image-quality 30
   ```

3. Check pod memory:
   ```bash
   kubectl top pod "$POD" -n isaac-ros
   kubectl describe pod "$POD" -n isaac-ros | grep -A5 Limits
   ```

## Nav2 Not Ready

**Symptoms**: `navigate_to_pose` action not appearing, VLM goal fails.

```bash
kubectl exec -n isaac-ros "$POD" -- bash -c '
  source /opt/ros/humble/setup.bash
  source /workspaces/isaac_ros-dev/install/setup.bash 2>/dev/null || true
  ros2 action list 2>/dev/null
  ros2 topic list 2>/dev/null | grep -E "map|tf|costmap"
'
```

### Recovery

1. Check TF tree: `ros2 run tf2_tools view_frames.py` -- both `base_link→camera_link` and
   `camera_link→camera_optical_frame` required
2. Check costmap: `ros2 topic echo /global_costmap/costmap --once` -- should have data
3. Restart Nav2: kill Nav2 process, relaunch from `nav2_bringup_with_nvblox.launch.py`

## MCAP Download Failure

**Symptoms**: Empty MCAP file, ORAS pull fails, curl fails.

### Check 1: Validate Zot Repository Exists

```bash
# List all available repos
oras repo ls 192.168.100.1:30500

# Check specific bag
oras repo tags 192.168.100.1:30500/bags/test_mcap
```

**If repo not found**: The MCAP sequence does not exist in Zot. Use `test_mcap` only.

### Check 2: Network Connectivity

```bash
# Try WiFi fallback
ZOT_WIFI="192.168.1.86:30500"
curl -s "http://${ZOT_WIFI}/v2/bags/test_mcap/tags/list"

# Try Ethernet
curl -s "http://192.168.100.1:30500/v2/bags/test_mcap/manifests/latest" | jq

# Check catalog
curl -s "http://192.168.100.1:30500/v2/_catalog" | jq
```

### Check 3: ORAS Pull Output

```bash
# Verify MCAP downloaded correctly
kubectl exec -n isaac-ros "$POD" -- bash -c '
  ls -la /tmp/mcap/
  file /tmp/mcap/*.mcap 2>/dev/null || echo "No MCAP files found"
'
```

**If only `.` and `..`**: ORAS pull silently failed (repo not found). Check `mcap_sequence` input.

### Recovery

```bash
# Use validated bag
gh workflow run 5-vlm-mcap-test.yml \
  -f runner_label=nano1 \
  -f mcap_sequence=test_mcap

# Manual pull inside pod
kubectl exec -n isaac-ros "$POD" -- bash -c "
  oras pull 192.168.100.1:30500/bags/test_mcap:latest --allow-path-traversal
"
```

## Cloudflare Worker Failure

### Health Check

```bash
HEALTH=$(curl -s https://mcap-nim-shaper.kieran-3e9.workers.dev/health)
echo "$HEALTH" | jq '.version'
echo "$HEALTH" | jq '.status'
```

**If no response**: Worker crashed or CF account issue. Check CF dashboard. **If version mismatch**:
Worker updated but not deployed to edge. Wait 60s for propagation.

## Worker 400: H.264 Not Supported

**Symptoms**: NimSender sends H.264, Worker returns 400 "H.264 not supported".

### Recovery

1. Verify NimSender is sending JPEG, not H.264:

   ```bash
   # Check NimSender logs for payload type
   kubectl logs -n isaac-ros "$POD" --tail=50 | grep -i "encoding\|jpeg\|h264"
   ```

2. Switch to NITROS JPEG bridge:

   ```bash
   # Launch NITROS bridge instead of H.264 encoder
   ros2 launch isaac_ros_custom nitros_bridge.launch.py
   ```

3. Verify NimSender subscribes to `/vlm/jpeg_stream` (not `/vlm/h264_stream`):

   ```bash
   ros2 topic info /vlm/jpeg_stream --verbose
   ros2 topic echo /vlm/jpeg_stream sensor_msgs/CompressedImage --once
   ```

## Worker 400: Protobuf Decode Error

**Symptoms**: Worker receives Protobuf but fails to decode.

### Recovery

1. Check Content-Type header:

   ```bash
   # Ensure NimSender sends correct Content-Type
   kubectl logs -n isaac-ros "$POD" --tail=50 | grep "Content-Type"
   # Should be "application/x-protobuf"
   ```

2. Check Protobuf field 13 (memory_pressure) wire type:
   - **Bug**: Field 13 parsed as varint instead of float wire type 5
   - **Fix**: Worker's `handleProtobufRequest()` must use wire type 5 for float fields
   - Check `mcap-nim-shaper/src/rust/src/lib.rs` for correct prost struct definition

3. Fall back to JSON mode:

   ```bash
   ros2 run isaac_ros_custom nim_sender_node --ros-args -p use_protobuf:=false
   ```

## Semantic Distiller Failure

**Symptoms**: `/vlm/scene_state` topic empty or invalid JSON.

### Check 1: Source Topics Available

```bash
kubectl exec -n isaac-ros "$POD" -- bash -c '
  source /opt/ros/humble/setup.bash
  # Check cuVSLAM
  ros2 topic echo /visual_slam/odometry --once --timeout 3 2>/dev/null && \
    echo "cuVSLAM OK" || echo "cuVSLAM missing"
  # Check NVBLOX
  ros2 topic echo /nvblox_node/esdf_slice --once --timeout 3 2>/dev/null && \
    echo "NVBLOX OK" || echo "NVBLOX missing"
  # Check Nav2
  ros2 topic echo /global_costmap/costmap --once --timeout 3 2>/dev/null && \
    echo "Nav2 OK" || echo "Nav2 missing"
'
```

### Check 2: Scene State Output

```bash
kubectl exec -n isaac-ros "$POD" -- bash -c '
  ros2 topic echo /vlm/scene_state std_msgs/String --once --timeout 10 | jq .data
'
```

**If empty**: semantic_distiller_node.py not running or source topics missing. **If invalid JSON**:
Check distiller logs for Python exceptions.

### Check 3: Worker Scene State Handling

```bash
# Send mock scene_state directly to Worker
curl -s -X POST "https://mcap-nim-shaper.kieran-3e9.workers.dev" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $WORKER_API_KEY" \
  -d '{"scene_state": {"json_dag": "{}", "timestamp": 0, "is_persistent": false}, \
    "query_text": "test", "model": "qwen/qwen3.5-397b-a17b", "max_tokens": 10}' | jq
```

### Check 4: Unit Tests

```bash
kubectl exec -n isaac-ros "$POD" -- bash -c '
  cd /workspaces/isaac_ros-dev
  python3 -m pytest tests/unit/test_semantic_distiller.py -v
'
```

## NITROS JPEG Bridge Failure

**Symptoms**: `/vlm/jpeg_stream` topic not publishing, or NimSender not receiving data.

### Check 1: Launch File

```bash
kubectl exec -n isaac-ros "$POD" -- bash -c '
  source /opt/ros/humble/setup.bash
  ros2 launch isaac_ros_custom nitros_bridge.launch.py &
  sleep 5
  ros2 topic list | grep jpeg_stream
'
```

### Check 2: ImageCompressionNode

```bash
# Verify ImageCompressionNode is loaded (requires isaac_ros_image_pipeline)
kubectl exec -n isaac-ros "$POD" -- bash -c '
  ros2 component list 2>/dev/null | grep -i compression
  ros2 topic hz /vlm/jpeg_stream --spin-time 3
'
```

**If missing**: `isaac_ros_image_pipeline` not in Docker image. Run Buildah workflow.

### Check 3: ResizeNode Output

```bash
# Verify resize output exists
ros2 topic hz /vlm/resized_image --spin-time 3
```

**If no data**: Camera input not reaching ResizeNode. Check MCAP replay and topic remap.

## Tool Calling Returns Empty

**Symptoms**: VLM responds with text but no tool calls, Nav2 goal not sent.

### Recovery

1. Verify tools are enabled: check VLM logs for `"Tools: True"`
2. Qwen3.5 sometimes ignores tools for vision inputs -- this is expected
3. Fall back to text parsing: `enable_tools:=False` and use coordinate format `x=1.0 y=2.0`
4. Test parser standalone:
   ```bash
   python3 -c "
   from isaac_ros_custom.vlm_nav2_parser import parse_navigation_command
   print(parse_navigation_command('Navigate to x=1.5 y=2.3'))
   print(parse_navigation_command('Target y=3.1, x=-0.5'))
   "
   ```

## py3-cryptography Missing (`return self._algorithms[alg_name]`)

**Symptoms**: Rung 13 or 14 fails with `return self._algorithms[alg_name]` Python traceback, or
"FATAL: Failed to generate GitHub App installation token" with empty response body.

### Root Cause

`gh` CLI on Alpine depends on PyJWT, which needs the `cryptography` package for RS256 algorithm
support. Without it, PyJWT cannot sign or verify RS256 tokens. The error looks like a JWT issue but
is actually a missing dependency.

### Check 1: Verify py3-cryptography in Template

```bash
kubectl get wftmpl isaac-ros-ladder -n argo -o yaml | grep py3-cryptography
```

**If no output**: The template doesn't install `py3-cryptography`. Fix by updating `_install_pkgs`:

```bash
_install_pkgs curl kubectl github-cli openssl jq py3-cryptography >/dev/null
```

### Check 2: Verify in Shared Scripts

```bash
kubectl get configmap ladder-shared-scripts -n argo -o yaml | grep py3-cryptography
```

### Recovery

1. Add `py3-cryptography` to `_install_pkgs` in the rung template
2. Apply the updated template:
   `cat k3s/argo-ladder-workflow.yaml | ssh nano2 "kubectl apply -f - -n argo"`
3. Re-run the ladder with `quick_check=true`

## GitHub App Permissions 403 (`workflow_dispatch`)

**Symptoms**: Rung 13 or 14 fails with HTTP 403 "Resource not accessible by integration" when
calling `POST /repos/{owner}/{repo}/actions/workflows/{id}/dispatches`.

### Root Cause

The GitHub App installation has `actions: read` but needs `actions: write` to create
`workflow_dispatch` events. Permissions cannot be updated in-place -- reinstallation required.

### Check 1: Verify Current Permissions

```bash
# Generate JWT and check permissions
kubectl get secret github-arc-app -n argo -o jsonpath='{.data.github_app_private_key}' | \
  base64 -d > /tmp/app_key.pem
APP_ID=$(kubectl get secret github-arc-app -n argo -o jsonpath='{.data.github_app_id}')
# Sign JWT, exchange for token, query permissions
# ... (see shared-scripts/gh-auth.sh for full flow)
```

Or from GitHub UI: Settings > Integrations > Applications > [App Name] > App permissions

### Recovery

1. Reinstall the GitHub App with `actions: write` permission
2. Private key stays the same -- K8s secrets don't need updating
3. Allow 2-3 minutes for permission propagation after reinstallation
4. Verify with: `curl -s https://api.github.com/app/installations/<ID> | jq '.permissions'`
5. Re-run the ladder

## WorkflowTemplate Not Updated After Git Push

**Symptoms**: You commit a fix, push, run the ladder, and it still fails with the old bug. The
cluster continues running the old template.

### Root Cause

The `isaac-ros-ladder` WorkflowTemplate is a Kubernetes CRD. Pushing changes to git does NOT
automatically update the cluster template. Must `kubectl apply` explicitly.

### Check 1: Verify Template Generation

```bash
kubectl get workflowtemplate isaac-ros-ladder -n argo -o jsonpath='{.metadata.generation}'
```

### Check 2: Verify Specific Fix Present

```bash
kubectl get wftmpl isaac-ros-ladder -n argo -o yaml | grep py3-cryptography
```

**If no output**: Template is stale. Apply the latest version.

### Recovery

```bash
# Apply to both clusters
cat k3s/argo-ladder-workflow.yaml | ssh nano1 "kubectl apply -f - -n argo"
cat k3s/argo-ladder-workflow.yaml | ssh nano2 "kubectl apply -f - -n argo"

# Verify
kubectl get wftmpl isaac-ros-ladder -n argo -o yaml | grep py3-cryptography
```

## ARC Runner Not Scaled Back Up

**Symptoms**: After VLM Nav2 test, ARC runner still at 0 replicas.

### Recovery

```bash
# Manual scale up
kubectl scale deployment -n arc-systems arc-runner-nano1 --replicas=1

# Or via composite action (from workflow context)
# .github/actions/scale-runner with replicas=1
```

## JWT Null Byte Truncation (HTTP 401 "could not be decoded")

**Symptoms**: Rung 13 or 14 fails with HTTP 401 "A JSON web token could not be decoded" even though
the JWT appears to be generated correctly. May work on one Jetson but fail on another with identical
code.

### Root Cause

POSIX shell variables cannot hold null bytes. A 256-byte RSA signature stored in a shell variable
via `RAW_SIG=$(openssl dgst -sha256 -sign key -binary data)` gets truncated to ~253 bytes when null
bytes are stripped. The resulting JWT has a corrupted signature that GitHub rejects.

### Check 1: Verify Signature Size

```bash
# In the Argo rung pod, check the signature size
POD=$(kubectl get pods -n argo -l workflows.argoproj.io/workflow=$(argo get @latest -n argo -o name | sed 's|workflow/||') -o name | grep "rung-vlm-test" | sed 's|pod/||')
kubectl exec -n argo "$POD" -- sh -c '
  openssl dgst -sha256 -sign /etc/gh-app/github_app_private_key -binary /tmp/data_to_sign.txt 2>/dev/null | wc -c
'
# Expected: 256 (for 2048-bit key)
# If 253: null byte truncation confirmed
```

**If 253 bytes**: The old code stores binary in a shell variable. Fix by piping directly.

### Check 2: Verify Pipe-Based Code

```bash
kubectl get configmap ladder-shared-scripts -n argo -o yaml | grep "openssl.*base64"
# Should show: openssl ... | base64 -w0
# NOT: RAW_SIG=$(openssl ...); SIG=$(printf '%s' "$RAW_SIG" | base64 ...)
```

### Recovery

1. Update `gh-auth.sh` in `k3s/workflows/_base/shared-scripts-configmap.yaml` to pipe directly:
   ```sh
   _GH_SIG=$(openssl dgst -sha256 -sign "$_GH_KEY_FILE" -binary /tmp/data_to_sign.txt 2>/dev/null | base64 -w0 | tr '+/' '-_' | tr -d '=')
   ```
2. Apply ConfigMap to both clusters:
   ```bash
   kubectl apply -f k3s/workflows/_base/shared-scripts-configmap.yaml
   ```
3. Re-run the ladder with `quick_check=true`

## jq Parsing Failure (curl -w HTTP Code Noise)

**Symptoms**: Rung 13 or 14 fails with `Cannot index number with string 'token'` or empty GH_TOKEN.

### Root Cause

`curl -s -w "\n%{http_code}"` appends the HTTP status code as a new line after the response body. If
`$_GH_RESP` is used with `jq`, jq tries to parse the number (e.g., "201") as JSON and fails.

### Check 1: Verify Body Parsing

```bash
kubectl get configmap ladder-shared-scripts -n argo -o yaml | grep '_GH_BODY'
# Should show: _GH_BODY=$(echo "$_GH_RESP" | sed '$d')
# NOT: export GH_TOKEN=$(echo "$_GH_RESP" | jq -r '.token')
```

### Recovery

1. Parse body separately: `_GH_BODY=$(echo "$_GH_RESP" | sed '$d')`
2. Use body for jq: `export GH_TOKEN=$(echo "$_GH_BODY" | jq -r '.token')`
3. Apply and re-run

## YAML Block Scalar Indentation Break

**Symptoms**: GitHub Actions reports "unexpected key" or Argo reports "invalid YAML" but the file
looks visually correct. Workflow steps may silently not execute.

### Root Cause

In YAML `run: |` blocks, a comment or code at column 0 (or less indentation than the block scalar's
base) terminates the block. The remaining content becomes invalid YAML or gets silently dropped.

### Check 1: Lint the File

```bash
yamllint .github/workflows/5-vlm-mcap-test.yml
yamllint .github/workflows/5-vlm-nav2-test.yml
yamllint k3s/argo-ladder-workflow.yaml
actionlint .github/workflows/5-vlm-mcap-test.yml
actionlint .github/workflows/5-vlm-nav2-test.yml
```

**If errors found**: yamllint reports line numbers with indentation violations.

### Check 2: Visual Inspection

Look for any of these patterns inside `run: |` blocks:

- Comments at column 0 (should be indented to match block scalar base)
- `case` statements not indented enough
- `GITHUB_OUTPUT` or other shell variable assignments at wrong indent

### Recovery

1. Identify the block scalar's base indent from the first non-empty line after `run: |`
2. Ensure ALL content (including comments) is indented at least that much
3. Run `yamllint` to verify
4. Commit and push

## K8s Secret File Trailing Newlines

**Symptoms**: GitHub App ID or installation ID has unexpected whitespace, causing JWT `iss` claim
mismatch.

### Root Cause

Kubernetes appends a trailing newline (`\n`) to data values in secrets. Using `cat` to read these
files includes the newline in the variable value.

### Check 1: Verify File Contents

```bash
kubectl exec -n argo "$POD" -- xxd /etc/gh-app/github_app_id | tail -3
# If last bytes are "0a 00" or similar: trailing newline present
```

### Recovery

Use `tr -d '\n\r '` instead of `cat`:

```bash
# WRONG: APP_ID=$(cat /etc/gh-app/github_app_id)
# RIGHT: APP_ID=$(tr -d '\n\r ' < /etc/gh-app/github_app_id)
```


# === REFERENCE.md ===

# REFERENCE.md -- Testing VLM

Static reference data for VLM test infrastructure.

## Workflow Files

| File                                                 | Workflow               | Rung | Timeout | Status            |
| ---------------------------------------------------- | ---------------------- | ---- | ------- | ----------------- |
| `.github/workflows/5-vlm-mcap-test.yml`              | VLM MCAP               | 13   | 30 min  | Active            |
| `.github/workflows/5-vlm-nav2-test.yml`              | VLM Nav2               | 14   | 45 min  | Active            |
| `.github/workflows/5-vlm-memory-stress-test.yml`     | VLM Memory Stress      | 15   | 15 min  | Active            |
| `.github/workflows/6-semantic-distillation-test.yml` | Semantic Distill       | 16   | 15 min  | Plan 37 (blocked) |
| `.github/workflows/7-golden-mcap-validation.yml`     | Golden MCAP Validation | 17   | 15 min  | Plan 29 (blocked) |
| `.github/workflows/8-multi-scenario-vlm.yml`         | Multi-Scenario VLM     | 18   | 15 min  | Plan 29 (blocked) |

## Argo Ladder Integration

The VLM tests are integrated into the Argo ladder workflow (`k3s/argo-ladder-workflow.yaml`):

### Rung 13: VLM Test

- **Template Name**: `rung-vlm-test`
- **Description**: Runs all VLM test types in sequence: sanity, build, import, dependencies, query,
  mcap, cloudflare, semantic
- **Trigger Method**: GitHub Actions via gh CLI with GitHub App token
- **Quick Check**: Skips if not in failed list (rung 13 not in `ladder-failed-rungs` ConfigMap)
- **Capability Check**: Fails if `vlm-provider` is null, empty, "none", or "unknown"

### Rung 14: VLM Nav2 Test

- **Template Name**: `rung-vlm-nav2-test`
- **Description**: Runs VLM Nav2 integration test
- **Trigger Method**: GitHub Actions via gh CLI with GitHub App token
- **Quick Check**: Skips if not in failed list (rung 14 not in `ladder-failed-rungs` ConfigMap)
- **Capability Check**: Fails if `nav2-enabled` is false, empty, "none", or "unknown"

### Rung 15: VLM Memory Stress Test

- **Template Name**: `rung-vlm-memory-stress`
- **Description**: Validates three-tier memory model under 6GB pressure (stress-ng)
- **Trigger Method**: GitHub Actions via gh CLI with GitHub App token
- **Quick Check**: Skips if not in failed list (rung 15 not in `ladder-failed-rungs` ConfigMap)
- **Capability Check**: Fails if `vlm-provider` is null/unknown

### Rungs 16-18: Semantic Distillation, Golden MCAP, Multi-Scenario (Plan 37)

- **Status**: Rung 16 ready to implement; rungs 17-18 blocked on Plan 29 (golden MCAP generation)
- **Details**: See Plan 37 (`.claude/plans/37-argo-ladder-expansion-15-to-18.md`)

## API Endpoints

| Endpoint                                                 | Protocol | Use                         |
| -------------------------------------------------------- | -------- | --------------------------- |
| `https://integrate.api.nvidia.com/v1/chat/completions`   | HTTPS    | Direct NIM API (default)    |
| `https://nvidia-bridge.rossollc.com/v1/chat/completions` | HTTPS    | CF Tunnel (CI preferred)    |
| `https://mcap-nim-shaper.kieran-3e9.workers.dev`         | HTTPS    | CF Worker (JSON + Protobuf) |
| `https://mcap-nim-shaper.kieran-3e9.workers.dev/health`  | HTTPS    | Worker health check         |

## Zot MCAP Repositories

| Repository              | Content          | Pull Command                                                                 | Status          |
| ----------------------- | ---------------- | ---------------------------------------------------------------------------- | --------------- |
| `bags/test_mcap:latest` | Test MCAP (~4MB) | `oras pull 192.168.100.1:30500/bags/test_mcap:latest --allow-path-traversal` | **Use for VLM** |
| `bags/test_data:latest` | Tiny test file   | `oras pull 192.168.100.1:30500/bags/test_data:latest`                        | Not suitable    |

**WARNING**: `bags/nano2_migration_baseline` does NOT exist in Zot (only on local filesystem at
`/mnt/bigdata/rosbags/`). Do NOT use as default.

**WARNING**: `bags/nuscenes-mini`, `bags/r2b-galileo`, `bags/r2b-whitetunnel`, `bags/r2b-robotarm`
do NOT exist in Zot. Do NOT reference.

**Validate Zot catalog**:

```bash
# List all repositories
oras repo ls 192.168.100.1:30500

# Check specific bag tags
oras repo tags 192.168.100.1:30500/bags/test_mcap
```

## GitHub App Authentication

### Required Permissions

| Permission | Scope   | Purpose                           |
| ---------- | ------- | --------------------------------- |
| `actions`  | `write` | Create `workflow_dispatch` events |
| `contents` | `read`  | Access repository contents        |
| `metadata` | `read`  | Repository metadata               |

### Secret Keys (K8s Secret `github-arc-app`)

| Key                          | Purpose                            |
| ---------------------------- | ---------------------------------- |
| `github_app_id`              | App ID for JWT `iss` claim         |
| `github_app_installation_id` | Installation ID for token exchange |
| `github_app_private_key`     | PEM private key for JWT signing    |
| `token`                      | Fallback PAT (not used by ladder)  |

### Token Lifecycle

| Token Type      | Expiry | Generation Method                        |
| --------------- | ------ | ---------------------------------------- |
| JWT (App token) | 60s    | OpenSSL RS256 signing                    |
| Installation    | 1 hour | JWT → POST /app/installations/{id}/token |

### Two-Layer Auth Architecture

```
Alpine pod → _gh_auth() → OpenSSL JWT signing → POST /app/installations/{id}/token
                         ↑                                              ↓
                         |                                     Installation token
                         |                                              ↓
                         |  Pipe openssl → base64 (no intermediate var)  |
                         |  (null bytes truncate binary signatures)     |
                         |                                              ↓
Alpine pod → gh CLI → PyJWT RS256 verification → gh workflow run ...
                         ↑
                   Needs py3-cryptography

K8s secret files: use `tr -d '\n\r '` (not `cat`) — K8s appends trailing newlines
curl response: use `sed '$d'` to separate body from HTTP status (curl -w appends code)
jq parsing: use `$_GH_BODY` not `$_GH_RESP` (which includes the HTTP code)
```

### Common Failure Diagnostics

```bash
# Check App permissions (needs JWT)
curl -s https://api.github.com/app/installations/<ID> \
  -H "Authorization: Bearer $JWT" | jq '.permissions'

# Verify py3-cryptography installed in template
kubectl get wftmpl isaac-ros-ladder -n argo -o yaml | grep py3-cryptography

# Verify secret mount
kubectl get secret github-arc-app -n argo -o jsonpath='{.data.github_app_id}'

# Verify _gh_auth code uses pipe-based signing (not intermediate variable)
kubectl get configmap ladder-shared-scripts -n argo -o yaml | grep "openssl.*base64"
# Should show: openssl ... | base64 -w0 (pipe, no intermediate variable)

# Verify _gh_auth code uses tr -d for secret files (not cat)
kubectl get configmap ladder-shared-scripts -n argo -o yaml | grep "tr -d"
# Should show: tr -d '\n\r ' < /etc/gh-app/
```

### Linting Tools

```bash
# YAML linting (catches block scalar indentation, syntax errors)
yamllint .github/workflows/5-vlm-mcap-test.yml
yamllint .github/workflows/5-vlm-nav2-test.yml
yamllint k3s/argo-ladder-workflow.yaml

# GitHub Actions linting (catches workflow-specific issues)
actionlint .github/workflows/5-vlm-mcap-test.yml
actionlint .github/workflows/5-vlm-nav2-test.yml
```

## Argo WorkflowTemplate Management

The `isaac-ros-ladder` WorkflowTemplate is a CRD, not git-driven. Changes in git do NOT auto-apply.

### Apply Updated Template

```bash
# Apply to both clusters
cat k3s/argo-ladder-workflow.yaml | ssh nano1 "kubectl apply -f - -n argo"
cat k3s/argo-ladder-workflow.yaml | ssh nano2 "kubectl apply -f - -n argo"
```

### Verify Template Version

```bash
# Check generation
kubectl get workflowtemplate isaac-ros-ladder -n argo -o jsonpath='{.metadata.generation}'

# Verify specific fix present
kubectl get wftmpl isaac-ros-ladder -n argo -o yaml | grep py3-cryptography

# View full template
kubectl get wftmpl isaac-ros-ladder -n argo -o yaml | less
```

### Shared Scripts ConfigMap

```bash
# Apply shared scripts (all ladder templates depend on this)
kubectl apply -f k3s/workflows/_base/shared-scripts-configmap.yaml

# Verify scripts loaded
kubectl get configmap ladder-shared-scripts -n argo -o yaml | grep -c "_install_pkgs\|_gh_auth\|_should_skip_rung\|_setup_oras"
```

### Quick Check Mode

```bash
# Run only failed rungs from previous run
argo submit --from workflowtemplate/isaac-ros-ladder -n argo -p target=nano2 -p quick_check=true

# Seed specific rungs for targeted re-test
kubectl create configmap ladder-failed-rungs \
  --from-literal=failed="13 14" \
  -n argo --dry-run=client -o yaml | kubectl apply -f -

# Check current failed rungs ConfigMap
kubectl get configmap ladder-failed-rungs -n argo -o yaml
```

## OCI Image Annotations (from Buildah)

The buildah workflow (`k3s/buildah-workflow.yaml` lines 1508-1523) embeds capability annotations in
the image manifest. VLM tests validate these before running.

| Annotation                          | Values                                            | VLM Test Check                  |
| ----------------------------------- | ------------------------------------------------- | ------------------------------- |
| `org.ros.isaac.vlm.provider`        | `isaac_ros_vlm`, `nim_sender`, `vlm_node`, `null` | **FAIL** if null/unknown        |
| `org.ros.isaac.cuda.enabled`        | `true`, `false`                                   | Warn if false                   |
| `org.ros.isaac.nav2.enabled`        | Package list                                      | **FAIL** if missing (Nav2 test) |
| `org.ros.isaac.hardware.camera`     | `realsense`, `null`                               | Info only                       |
| `org.opencontainers.image.revision` | Git SHA                                           | Logged for provenance           |
| `org.opencontainers.image.created`  | ISO timestamp                                     | Info only                       |

**Validation in workflow**:

```bash
# Check VLM provider (run inside pod)
source /opt/ros/humble/setup.bash
if ros2 pkg list | grep -q "isaac_ros_vlm"; then
  echo "isaac_ros_vlm"
elif [ -f /workspaces/isaac_ros-dev/src/isaac_ros_custom/vlm_node.py ]; then
  echo "vlm_node"
else
  echo "null"  # Will fail the test
fi
```

**Provenance tracking**:

```bash
# Get commit SHA from image
kubectl exec -n isaac-ros "$POD" -- cat /etc/realsense_versions 2>/dev/null | grep commit
```

## VLM Node Parameters

### Core

| Parameter      | Default                                                | Description          |
| -------------- | ------------------------------------------------------ | -------------------- |
| `api_endpoint` | `https://integrate.api.nvidia.com/v1/chat/completions` | NVIDIA API URL       |
| `model`        | `qwen/qwen3.5-397b-a17b`                               | VLM model name       |
| `max_tokens`   | `16384`                                                | Max response tokens  |
| `temperature`  | `1.0`                                                  | Sampling temperature |

### Image Processing

| Parameter          | Default | Description                          |
| ------------------ | ------- | ------------------------------------ |
| `max_image_width`  | `160`   | Max width for VLM (160/320/640/1280) |
| `max_image_height` | `120`   | Max height for VLM (120/240/480/720) |
| `image_quality`    | `85`    | JPEG quality 1-100                   |
| `jpeg_quality_low` | `30`    | Quality in low-memory mode           |

### Memory Management

| Parameter               | Default | Description                                   |
| ----------------------- | ------- | --------------------------------------------- |
| `memory_threshold_high` | `85.0`  | % to trigger downsampling                     |
| `memory_threshold_low`  | `70.0`  | % to restore resolution (10s sticky)          |
| `gc_interval`           | `10.0`  | GC interval in seconds                        |
| `aggressive_gc`         | `True`  | Double gc.collect() after each request        |
| `frame_skip_count`      | `0`     | Frames to skip (0=off; auto-set to 2 at >85%) |

### Nav2 Integration

| Parameter          | Default | Description           |
| ------------------ | ------- | --------------------- |
| `enable_tools`     | `False` | Nav2 tool calling     |
| `enable_nav2`      | `False` | Nav2 action client    |
| `enable_streaming` | `True`  | Streaming responses   |
| `enable_thinking`  | `False` | Qwen3.5 thinking mode |

## NITROS JPEG Bridge Parameters

| Parameter                     | Default | Description                             |
| ----------------------------- | ------- | --------------------------------------- |
| ResizeNode output_size        | 640x480 | Multiple of 28 for Qwen patch alignment |
| ImageCompressionNode encoding | `jpeg`  | NVJPEG hardware encoder                 |
| ImageCompressionNode quality  | `75`    | JPEG quality (1-100)                    |

## NimSender Parameters

| Parameter      | Default            | Description                        |
| -------------- | ------------------ | ---------------------------------- |
| `use_protobuf` | `True`             | Send Protobuf (vs JSON)            |
| `input_topic`  | `/vlm/jpeg_stream` | CompressedImage from NITROS bridge |
| `node_id`      | `$RUNNER_HOSTNAME` | Node identifier for tracking       |
| `worker_url`   | CF Worker endpoint | Cloudflare Worker URL              |

## Semantic Distiller Topics

| Subscribed Topic                 | Source  | Data Extracted               |
| -------------------------------- | ------- | ---------------------------- |
| `/visual_slam/odometry`          | cuVSLAM | Robot pose (primary)         |
| `/visual_slam/tracking/odometry` | cuVSLAM | Tracking pose (fallback)     |
| `/nvblox_node/esdf_slice`        | NVBLOX  | Distance field values        |
| `/nvblox_node/mesh`              | NVBLOX  | Mesh for semantic extraction |
| `/global_costmap/costmap`        | Nav2    | Obstacle bounding boxes      |
| `/local_costmap/costmap`         | Nav2    | Local obstacle data          |
| `/plan`                          | Nav2    | Path waypoints               |
| `/amcl_pose`                     | Nav2    | AMCL pose (fallback)         |
| `/goal_pose`                     | Nav2    | Current goal                 |
| `/bt_navigator/bt_logs`          | Nav2    | Behavior tree state          |

## Protobuf Schema (`vlm_messages.proto`)

Key fields:

| Field | Type    | Name             | Purpose                                 |
| ----- | ------- | ---------------- | --------------------------------------- |
| 1     | uint64  | timestamp        | Unix nanoseconds                        |
| 2     | string  | node_id          | Sender identifier                       |
| 3     | bytes   | h264_payload     | H.264 data (DEPRECATED, Worker rejects) |
| 4     | bytes   | jpeg_payload     | JPEG data (canonical)                   |
| 5     | int32   | width            | Image width                             |
| 6     | int32   | height           | Image height                            |
| 7     | string  | encoding         | "jpeg" or "h264"                        |
| 8-12  | various | NIM params       | model, max_tokens, temperature          |
| 13    | float   | memory_pressure  | System memory % (wire type 5)           |
| 14    | int32   | frame_skip_count | Frame skip value                        |
| 15    | message | scene_state      | SceneState sub-message                  |

### SceneState (field 15)

| Sub-field     | Type   | Purpose                   |
| ------------- | ------ | ------------------------- |
| json_dag      | string | JSON-encoded causal graph |
| timestamp     | uint64 | Scene state timestamp     |
| is_persistent | bool   | Whether to cache in DO    |

## Camera Topics

Default color topics (NITROS zero-copy preferred):

- `/visual_slam/image_0` -- NITROS format (zero-copy)
- `/camera/camera/color/image_raw` -- Raw fallback
- `/front_stereo_camera/left/image_raw`
- `/front_stereo_camera/left/image_compressed`
- `/stereo/left/image_raw`

Default depth topics:

- `/visual_slam/camera_info_0` -- NITROS camera info
- `/camera/camera/depth/image_rect_raw`
- `/camera/depth/image_rect_raw`

## ROS2 Topics

| Topic                   | Type                          | Direction | Purpose                     |
| ----------------------- | ----------------------------- | --------- | --------------------------- |
| `/vlm/query`            | `std_msgs/String`             | Input     | User query to VLM           |
| `/vlm/response`         | `std_msgs/String`             | Output    | VLM response                |
| `/vlm/response/partial` | `std_msgs/String`             | Output    | Streaming partial           |
| `/vlm/jpeg_stream`      | `sensor_msgs/CompressedImage` | Output    | NITROS bridge JPEG output   |
| `/vlm/scene_state`      | `std_msgs/String`             | Output    | Semantic distiller JSON DAG |
| `/vlm/status`           | `std_msgs/String`             | Output    | NimSender status            |
| `/nitros/memory_status` | `std_msgs/String`             | Input     | Memory monitor              |

## Nav2 Action/Tool Interface

### Tools (OpenAI function calling format)

1. **navigate_to_pose** -- `x`, `y`, `theta` → sends `NavigateToPose` goal
2. **get_current_pose** -- no args → TF2 lookup at image timestamp
3. **check_obstacle** -- `target_x`, `target_y` → `ComputePathToPose` feasibility

### Nav2 Actions

| Action                  | Type                | Server         |
| ----------------------- | ------------------- | -------------- |
| `/navigate_to_pose`     | `NavigateToPose`    | Nav2 navigator |
| `/compute_path_to_pose` | `ComputePathToPose` | Nav2 planner   |

## VLM Nav2 Parser (`vlm_nav2_parser.py`)

Extracts `(x, y)` from free-form text. Supported patterns:

- `x=1.0 y=2.0` (equals)
- `x: 1.0, y: 2.0` (colon + comma)
- `x 1.0 y 2.0` (space-separated)
- Both `x...y` and `y...x` orderings

Not supported: `(1.0, 2.0)`, `coordinates: 1.0 2.0`, scientific notation.

## Key Source Files

| File                                                                              | Purpose                             |
| --------------------------------------------------------------------------------- | ----------------------------------- |
| `src/isaac_ros_custom/vlm_node.py`                                                | Main VLM node (1623 lines)          |
| `src/isaac_ros_custom/isaac_ros_custom/vlm_nav2_parser.py`                        | Coordinate text parser              |
| `src/isaac_ros_custom/nim_sender/nim_sender_node.py`                              | CF Worker client (Protobuf/JSON)    |
| `src/isaac_ros_custom/nim_sender/vlm_messages.proto`                              | Protobuf schema                     |
| `src/isaac_ros_custom/nim_sender/semantic_distiller_node.py`                      | Semantic distiller (causal DAG)     |
| `mcap-nim-shaper/src/index.ts`                                                    | CF Worker (Protobuf + JSON handler) |
| `mcap-nim-shaper/src/rust/src/lib.rs`                                             | Rust prost Protobuf structs         |
| `src/isaac_ros_custom/launch/phases/phase0_nitros_bridge/nitros_bridge.launch.py` | NITROS JPEG bridge launch           |
| `src/isaac_ros_vlm/isaac_ros_vlm/vlm_goal_publisher.py`                           | VLM → Nav2 PoseStamped converter    |
| `scripts/nvblox_legacy_relay.py`                                                  | nvblox topic relay for Nav2         |
| `src/isaac_ros_custom/launch/nav2_bringup_with_nvblox.launch.py`                  | Nav2+nvblox launch                  |
| `.github/actions/scale-runner/action.yml`                                         | ARC runner scaling composite action |
| `src/isaac_ros_custom/scripts/golden_mcap/generate_golden_mcap.py`                | Golden MCAP generator               |
| `src/isaac_ros_custom/scripts/golden_mcap/compute_vlm_objective.py`               | VLM objective metrics               |
| `tests/unit/test_semantic_distiller.py`                                           | 19 unit tests for distiller         |

## Composite Actions

| Action                         | Purpose                  | Key Input                                   |
| ------------------------------ | ------------------------ | ------------------------------------------- |
| `.github/actions/scale-runner` | Scale ARC runner up/down | `runner` (nano1/nano2), `replicas` (0 or 1) |

## Memory Budget (8GB Jetson)

### Three-Tier Allocation

| Tier           | Component       | K3s Resource | Memory Limit | Memory Request  |
| -------------- | --------------- | ------------ | ------------ | --------------- |
| 1 (Critical)   | cuVSLAM, camera | DaemonSet    | 3Gi          | 3Gi             |
| 2 (Adaptive)   | NVBLOX, Nav2    | Deployment   | 2Gi          | 2Gi             |
| 3 (Background) | VLM inference   | Workload Pod | 2Gi (max)    | 1Gi (scheduled) |

### Typical Component Usage

| Component           | Typical Usage |
| ------------------- | ------------- |
| Nav2 stack          | ~2.5GB        |
| VLM node (160x120)  | ~200MB        |
| VLM node (640x480)  | ~800MB        |
| VLM node (1280x720) | ~1.2GB        |
| cuVSLAM             | ~1.5GB        |
| NVBLOX              | ~800MB        |
| ARC runner (idle)   | ~1.5GB        |

**Rule**: Scale ARC runner to 0 before running VLM + Nav2 together.

## Semantic Distiller Priority Data Formats

| Priority | Source       | Format                | Size  | Purpose                   |
| -------- | ------------ | --------------------- | ----- | ------------------------- |
| 1        | cuVSLAM /tf  | GlobalPose JSON       | ~200B | Robot pose, velocity      |
| 2        | NVBLOX       | SparseBoundingBoxList | ~2KB  | Obstacle volumes          |
| 3        | Nav2 costmap | DangerPolygonList     | ~1KB  | Inflation zone polygons   |
| 4        | NVBLOX mesh  | CentroidSemanticGraph | ~3KB  | Causal DAG with parent_id |

Unified scene JSON payload: <5KB total.

## Worker Test Fixtures

5 scene-state fixtures in `mcap-nim-shaper/test/fixtures/scene-state-fixtures.ts`:

| Fixture               | Description                     |
| --------------------- | ------------------------------- |
| `office_with_laptop`  | Desk with laptop, walls         |
| `kitchen_with_cup`    | Table with cup, causal relation |
| `warehouse_shelves`   | Shelving with stacked boxes     |
| `empty_hallway`       | Minimal baseline                |
| `complex_multi_level` | Stairs + multi-height objects   |


# === TACTICAL.md ===

# TACTICAL.md -- Testing VLM

Non-prescriptive strategic know-how from past incidents.

## PID 1 Zombie Reaping (Critical Fix)

**Insight**: The K3s deployment used `tail -f /dev/null` as PID 1, which **does not call wait()** to
reap zombie processes. Zombie processes accumulate from `pkill`, `timeout`, and other commands,
eventually causing OOM failures.

**Problem discovered**: VLM tests were failing with exit code 137 (OOM). Investigation showed:

- VLM node alone: ~60MB (acceptable)
- VLM node in GitHub Actions workflow: OOMs immediately
- Root cause: **Zombie processes** from previous test runs were not being reaped

**The issue**: PID 1 (`tail -f /dev/null`) never calls `wait()` or `waitpid()`. When child processes
exit, they become zombies until their parent reaps them. Since PID 1 never reaps, zombies accumulate
indefinitely.

**Solution**: Replace `tail -f /dev/null` with a zombie-reaping init:

```bash
# In k3s/base/03-deployment.yaml, replace:
#   tail -f /dev/null
# With:
while true; do
  wait -n 2>/dev/null || true
  sleep 5
done
```

**Why this works**:

- `wait -n` (bash 4.3+) waits for any child process to exit and reaps it
- Returns 127 if no children exist (expected, suppressed with `|| true`)
- Reaps zombies every 5 seconds, preventing accumulation

**Verification**: After applying the fix:

```bash
# Create zombie processes
kubectl exec -n isaac-ros $POD -- bash -c 'pkill -9 -f test & sleep 1 && ps aux | grep defunct'
# Wait 10 seconds
sleep 10
# Check again - zombies should be gone
kubectl exec -n isaac-ros $POD -- ps aux | grep defunct
# (No output - zombies reaped)
```

**Deployment fix** (applied 2026-03-20):

```bash
kubectl patch deployment nano2-workload-isaac-ros-workload-nano2 -n isaac-ros \
  --type=json -p='[{"op": "replace", "path": "/spec/template/spec/containers/0/command",
  "value": ["/bin/bash", "-c", "source /opt/ros/humble/setup.bash && source /workspaces/isaac_ros-dev/install/setup.bash 2>/dev/null || true && echo [init] Zombie-reaping init started && while true; do wait -n 2>/dev/null || true; sleep 5; done"]}]'
```

**When this bites**:

- VLM test fails with OOM but VLM node alone uses <100MB
- `ps aux` shows multiple `<defunct>` processes in workload pod
- Memory pressure increases over time despite no active workloads

**Alternative solutions**:

1. Use `tini` (tiny init): `/tini -- tail -f /dev/null` (requires tini in image)
2. Use dumb-init: `dumb-init tail -f /dev/null` (requires dumb-init package)
3. Custom init script with SIGCHLD handler (more complex)

**Impact**: This fix reduced zombie accumulation from 13+ zombies after 11 days to 0 zombies. Memory
usage stabilized and VLM tests pass consistently.

---

## GitHub Actions Workflow Memory Overhead

**Insight**: The VLM node OOMs immediately when run from GitHub Actions workflow but works fine when
run directly. The root cause is the combined memory pressure of:

- ARC runner pod processes (~500-800MB)
- `kubectl exec` sessions (~100-200MB)
- Pod's base overhead (ROS workspace, Docker layer, etc.) (~1-1.5GB)
- VLM node initialization (~60MB)

This creates a temporary spike that exceeds the 3Gi cgroup memory limit.

**Strategic options**:

1. **Increase memory limit**: Raise from 3Gi to 4Gi for VLM test pod
2. **Optimize workflow setup**: Reduce number of `kubectl exec` calls and parallel steps
3. **Separate execution**: Run VLM node directly from host instead of GitHub Actions
4. **Reduce ARC runner memory**: Scale ARC runner to 0 before VLM test (already implemented)

**Memory breakdown (GitHub Actions context)**:

- ARC runner pod: 500-800MB
- Workflow steps (git, kubectl, etc.): 200-300MB
- Pod overhead: 1-1.5GB
- VLM node: 60-100MB
- **Total spike**: ~2.5-3.1GB (exceeds 3Gi limit)

**Fix**: Increase memory limit for the VLM test deployment:

```yaml
spec:
  template:
    spec:
      containers:
        - name: isaac-ros
          resources:
            limits:
              memory: '4Gi'
            requests:
              memory: '2Gi'
```

**When to implement**: If VLM OOMs persist in GitHub Actions but not locally, this is the most
effective fix.

---

## Edge Device Memory Discipline

**Insight**: The Jetson Orin Nano has 8GB shared CPU/GPU memory. Constantly increasing memory limits
instead of investigating root causes is a recipe for failure. Before adjusting any limit,
investigate what is consuming memory.

**Strategic options**:

- **Investigate first**: Use `ps aux --sort=-%mem | head -20` to find top consumers
- **Check for zombies**: Zombie processes (`<defunct>`) accumulate from improper cleanup and don't
  release memory
- **Stale pods**: Old pods with accumulated state consume memory; restart cleans them
- **OOM exit code 137**: Means SIGKILL from memory exhaustion — investigate before adjusting limits
- **Memory pressure tiers**: Tier 1 (cuVSLAM) never throttles, Tier 2 (NVBLOX/Nav2) adapts, Tier 3
  (VLM) pauses

**Anti-patterns**:

- Increasing `memory: 2Gi` to `6Gi` without checking what's using the memory
- Patching ResourceQuota to allow more memory instead of fixing leaks
- Ignoring zombie processes — they accumulate and exhaust resources

**Pattern for memory issues**:

1. Check available memory: `free -h`
2. List top consumers: `ps aux --sort=-%mem | head -20`
3. Check for zombies: `ps aux | grep defunct`
4. If zombies found, restart the pod: `kubectl rollout restart deployment/<name>`
5. Re-verify memory after restart
6. Only then consider limit adjustments if consumption is legitimate

**When this bites**: VLM test fails with exit code 137. Investigation shows 13+ zombie python
processes from previous runs. The fix was restarting the pod, not increasing memory.

**Memory reduction opportunities**:

Before increasing limits, check for reducible workloads:

1. **Test pods**: `kubectl get pods -A | grep test` — delete stray test pods
2. **Duplicate services**: Two Zot instances (default/zot-service and zot/zot) — one is enough
3. **Completed workflows**: `kubectl get workflows -n argo` — delete old workflows
4. **ARC runner**: Scale to 0 when not running CI:
   `kubectl patch autoscalingrunnerset nano2 -n arc-systems --type=merge -p '{"spec":{"minRunners":0,"maxRunners":0}}'`
5. **K3s server**: Uses ~2GB by default — cannot reduce, but be aware it's the largest consumer
6. **Agent processes**: This CLI can use 500-800MB — close unused sessions

**Investigation sequence**:

```bash
# 1. Check available memory
free -h

# 2. List top consumers on host
ps aux --sort=-%mem | head -20

# 3. Check all running pods
kubectl get pods -A | grep Running

# 4. Look for test/debug pods to delete
kubectl delete pod test-alpine-2 -n default

# 5. Check pod memory limits
kubectl get deployment <name> -n <namespace> -o jsonpath='{.spec.template.spec.containers[0].resources}'

# 6. After cleanup, recheck available memory
free -h
```

**When this bites**: VLM test OOM with 3.2GB available. Investigation showed test pod running 15h
and duplicate Zot services. Deleted test pod freed memory without adjusting limits.

## CronJob Hook Pod Isolation

**Insight**: Self-healing hooks (zombie-monitor, oom-monitor, etc.) run in their own pods, not
inside workload pods. A hook that uses `ps aux` directly only sees processes in its OWN pod, not
workload pod processes.

**Strategic options**:

- **Hook isolation**: CronJob pods are separate from workload pods
- **Use kubectl exec**: Hooks must use `kubectl exec -n <namespace> <pod> -- <command>` to check
  processes inside workload pods
- **Common mistake**: Writing hook scripts that assume they run inside the workload container
- **Detection**: If a hook reports "No X found" but manual `kubectl exec` shows X, the hook is
  checking the wrong pod

**Zombie hook bug example**:

```bash
# WRONG: Checks processes in the hook's own pod
ZOMBIES=$(ps aux | grep '<defunct>')

# CORRECT: Checks processes inside the workload pod
ZOMBIES=$(kubectl exec -n isaac-ros $POD -- ps aux | grep '<defunct>')
```

**When this bites**: Zombie-monitor CronJob ran every 10 minutes for 11 days, always reporting "No
zombie processes found" because it checked its own empty pod. Meanwhile, workload pod accumulated
13+ zombies from previous VLM test runs, causing OOM failures.

## Zombie Hook RBAC Permissions

**Insight**: Self-healing hooks require explicit RBAC permissions to access pods and exec into
workload containers. A hook using the `default` ServiceAccount fails with
`Forbidden: cannot list resource "pods"` even within its own namespace.

**Strategic options**:

- **Create dedicated ServiceAccount**: Don't use `default` - create `zombie-monitor` SA with
  specific Role and RoleBinding
- **Required permissions**: `pods: [get, list, watch]` and `pods/exec: [create]` for `kubectl exec`
- **Deployment access**: If auto-restart is needed, add `deployments: [get, update, patch]`
- **Label selector accuracy**: Use `app.kubernetes.io/component=workload` (not
  `app=isaac-ros-custom`) for accurate pod targeting
- **Tolerations**: Add both `disk-pressure` and `memory-pressure` tolerations - hooks must run when
  cluster is under pressure

**RBAC pattern for zombie hook**:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: zombie-monitor
  namespace: isaac-ros
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: zombie-monitor-role
  namespace: isaac-ros
rules:
  - apiGroups: ['']
    resources: ['pods', 'pods/exec']
    verbs: ['get', 'list', 'watch', 'create']
  - apiGroups: ['apps']
    resources: ['deployments']
    verbs: ['get', 'list', 'update', 'patch']
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: zombie-monitor-rolebinding
  namespace: isaac-ros
subjects:
  - kind: ServiceAccount
    name: zombie-monitor
    namespace: isaac-ros
roleRef:
  kind: Role
  name: zombie-monitor-role
  apiGroup: rbac.authorization.k8s.io
```

**Auto-remediation threshold**: If zombie count exceeds 5 after cleanup, restart the deployment:

```bash
DEPLOYMENT=$(kubectl get pod $POD -n $NAMESPACE -o jsonpath='{.metadata.ownerReferences[0].name}')
kubectl rollout restart deployment/$DEPLOYMENT -n $NAMESPACE
```

**When this bites**: Rung 13 VLM test failed with exit code 137 (OOM). Investigation found 3 zombie
Python processes in workload pod and zombie-monitor CronJob failing with RBAC permission errors. The
hook couldn't list pods because it used `default` ServiceAccount without RoleBinding. Fix: added
dedicated SA + Role + RoleBinding, fixed label selector, added auto-restart logic.

## Memory Pressure on 8GB Jetson

**Insight**: VLM + Nav2 + SLAM cannot all run simultaneously on 8GB shared memory. The test workflow
mitigates by scaling ARC runner to 0 replicas before starting Nav2.

**Strategic options**:

- Default VLM resolution (160x120) uses ~200MB; bumping to 640x480 uses ~800MB
- `aggressive_gc:=True` (default) runs double gc.collect() after each request
- Memory-aware downsampling kicks in at 85% and restores at 70% (with 10s sticky timer)
- `frame_skip_count` auto-set to 2 at >85% memory (skip 2 out of 3 frames)
- Three-tier model: Tier 1 (cuVSLAM/camera, never throttled), Tier 2 (NVBLOX/Nav2, adaptive), Tier 3
  (VLM, pauses/slows)
- For long-running VLM sessions, monitor `k describe pod` for memory approaching limits

**When to adjust resolution**: If responses are consistently empty or truncated, the image may be
too small for the VLM to understand. Try 320x240 as a middle ground.

## NITROS JPEG vs H.264

**Insight**: The NITROS bridge originally used H.264 (NVENC) encoding, but the Cloudflare Worker
rejects H.264 with 400 because it can't decode it without heavy WASM. The fix was to replace
EncoderNode with ImageCompressionNode (NVJPEG).

**Strategic options**:

- NITROS JPEG bridge is canonical: `ResizeNode → ImageCompressionNode → NimSender`
- No temp files, no subprocess (true NITROS zero-copy in `component_container_mt`)
- JPEG is better for per-frame VLM inference (no GOP/IDR overhead like H.264)
- `jpeg_quality` param (default 75) balances size and Qwen 3.5 understanding
- 640x480 output aligned to Qwen's patch-based vision (multiple of 28)
- vlm_node.py also sends JPEG (CPU-based PIL) -- this is the fallback path

## Protobuf vs JSON for Worker

**Insight**: The Cloudflare Worker now supports both JSON and Protobuf (`application/x-protobuf`).
Protobuf is more bandwidth-efficient but requires careful wire type handling.

**Strategic options**:

- Protobuf is default (`use_protobuf:=True` in NimSender) -- smaller payloads, faster serialization
- Field 13 (memory_pressure) must use wire type 5 (32-bit fixed), not varint -- this was a bug
- JSON mode is the fallback: `use_protobuf:=False` for debugging
- The Worker checks `Content-Type` header to decide which parser to use
- SceneState (field 15) is embedded in Protobuf and prepended to VLM query in both paths

**When to use JSON**: Debugging Worker issues, testing new fields, or when Protobuf decode fails and
you need a quick workaround.

## Semantic Distiller Strategy

**Insight**: The semantic distiller converts dense sensor data (point clouds, voxel grids, costmaps)
into compact JSON (<5KB) with a causal DAG that VLM can reason about. Standard ROS2 subscribers are
correct because cuVSLAM/NVBLOX/Nav2 publish standard messages, not NITROS types.

**Strategic options**:

- Distiller subscribes to 10 topics across cuVSLAM, NVBLOX, and Nav2
- Priority 1-4 data hierarchy: pose → obstacles → zones → causal graph
- NetworkX DAG with `parent_id` relationships enables "cup on table" physical reasoning
- Pure logic functions are unit-testable without ROS2 (19 tests)
- 5 scene-state fixtures in Worker test suite cover office, kitchen, warehouse, hallway, multi-level
- VLM objective metrics: Scene Graph F1 (0.4), Spatial Error (0.3), Node Accuracy (0.3)
- Composite score >= 0.7 is "pass" threshold

**When to use NITROS for distiller**: Only if CPU profiling shows bottleneck at >80% -- network
latency is the real bottleneck, not CPU-to-CPU JSON copy of small payloads.

## Tool Calling vs Text Parsing

**Insight**: Qwen3.5 sometimes ignores tool definitions for vision inputs. This is a known model
limitation, not a code bug.

**Strategic options**:

- Always test with `enable_tools:=False` first to verify text parsing works
- If tool calling works, great -- it's more reliable for complex commands
- The text parser handles `x=... y=...`, `x: ..., y: ...`, and both orderings
- The parser does NOT handle bare coordinates like "(1.0, 2.0)" -- prompt the VLM to use `x= y=`
  format explicitly
- In production, consider a two-pass approach: VLM generates text, parser extracts coords, separate
  Nav2 client sends goal

## CF Tunnel vs Direct NIM API

**Insight**: The CF Tunnel adds ~200-500ms latency but provides a stable endpoint that doesn't
change with NVIDIA API rotation.

**Strategic options**:

- CI workflows should use the tunnel (`nvidia-bridge.rossollc.com`) for deterministic endpoints
- Direct NIM API (`integrate.api.nvidia.com`) is faster but subject to rate limits and auth changes
- For local debugging, direct API is fine -- for CI, always use tunnel
- The Worker endpoint is a third option: sends Protobuf frames to CF Worker, which proxies to NIM

## MCAP Replay Timing

**Insight**: MCAP replay and VLM node startup must be sequenced carefully. If VLM starts before
camera frames arrive, it won't have images for the first query.

**Strategic options**:

- Always wait 8-10s after starting MCAP replay before querying VLM
- Use `ros2 topic hz` to verify camera frames are flowing before sending queries
- Use loop mode (`-l`) for MCAP replay during extended testing sessions
- The MCAP topic remap is critical:
  `--remap /camera/color/image_raw:=/camera/camera/color/image_raw`

## Streaming vs Non-Streaming

**Insight**: Streaming mode publishes partial responses to `/vlm/response/partial` but accumulates
tool call deltas across chunks. Non-streaming is simpler for debugging.

**Strategic options**:

- Use `enable_streaming:=False` for CI tests (deterministic, easier to capture full response)
- Use streaming for interactive/demo use (user sees partial responses)
- Streaming tool call accumulation requires `tool_call_chunks` dict -- verify this works if
  switching from non-streaming to streaming with tools

## Zot Network Fallback

**Insight**: Ethernet (`192.168.100.1:30500`) is preferred but WiFi (`192.168.1.86:30500`) works as
fallback. The MCAP test workflow auto-detects.

**Strategic options**:

- Always try Ethernet first (2-3x faster on Jetson)
- The workflow does a 2-second connect-timeout test before selecting Zot host
- If neither works, the pod may have DNS or firewall issues -- check with `curl -v`

## GitHub Actions Log Persistence

**Insight**: Logs persist even when workload pods are OOM killed or reaped because the ARC runner
streams logs to GitHub in real-time, not after completion.

**Strategic options**:

- **Runner architecture**: ARC runner pod executes `kubectl exec` against workload pod
- **Streaming flow**: workload pod stdout/stderr → runner pod → GitHub servers (real-time)
- **If pod dies**: Logs already captured by runner and uploaded on failure
- **Download logs locally**:

```bash
# Full run logs
gh run view <run-id> --log > /tmp/vlm_run.log

# Failed steps only
gh run view <run-id> --log-failed > /tmp/vlm_failed.log

# Web UI (for manual inspection)
open https://github.com/<org>/<repo>/actions/runs/<run-id>
```

- **No artifact uploads**: Workflows cannot use `actions/upload-artifact` (blocked by policy)
- **Exit code 137**: OOM kill (SIGKILL) -- check memory warning in logs before failure

## MCAP Sequence Validation

**Insight**: The workflow's `mcap_sequence` input must match an existing Zot repository. Using
non-existent repos causes silent failures (empty MCAP) followed by OOM.

**Strategic options**:

- **Valid Zot bags**: Only `bags/test_mcap:latest` (~4MB with camera topics) exists
- **Invalid defaults**: `nano2_migration_baseline` does NOT exist in Zot (only local filesystem)
- **Case statement bug**: Workflow maps `test-mcap` to `bags/test-mcap` but Zot uses underscore
- **Pre-flight check**: Always validate with `oras repo ls 192.168.100.1:30500`
- **Empty MCAP symptom**: `ls -la /tmp/mcap/` shows only `.` and `..` after pull
- **Fix**: Ensure case statement uses correct repo name:
  `test_mcp|test-mcap) export REPO="bags/test_mcap"`

## Memory Pressure Detection

**Insight**: The workflow includes pre-flight memory checks that warn when <2GB available. Heed
these warnings -- exit code 137 follows.

**Strategic options**:

- **Warning threshold**: `< 2048MB` triggers `::warning::Low memory`
- **1661MB observed**: Nano2 had only 1.6GB free before OOM kill
- **Root cause chain**: Low memory + empty MCAP download + VLM startup = OOM
- **Recovery**: Scale down ARC runner before VLM tests, or use smaller MCAP (test_mcap is ~4MB)

## Image Capability Validation

**Insight**: VLM tests now validate OCI annotations from the buildah workflow before running. This
prevents wasting 10+ minutes on images that lack VLM or Nav2 capabilities.

**Strategic options**:

- **VLM provider check**: Workflow fails fast if `vlm_provider` is `null` or `unknown`
- **Expected values**: `isaac_ros_vlm`, `nim_sender`, or `vlm_node`
- **Nav2 check**: VLM Nav2 test validates `nav2_bringup`/`nvblox_nav2` packages present
- **CUDA check**: Warns (doesn't fail) if CUDA not detected
- **Provenance**: Commit SHA logged from `/etc/realsense_versions` for debugging

**When capability validation fails**:

1. Check image was built with VLM support: `ros2 pkg list | grep vlm`
2. Rebuild image via buildah workflow with correct Dockerfile
3. Verify annotations in Zot manifest: `oras manifest get @<digest>`

## GitHub App Authentication

**Insight**: VLM test workflows use GitHub App auth via `_gh_auth()` (OpenSSL JWT + installation
token exchange). This is shared infrastructure used by rungs 13-14 of the Argo ladder.

**Strategic options**:

- **JWT Signing**: Use OpenSSL directly on Alpine/ARM64 — more reliable than Python libraries
- **Token Lifecycle**: JWT tokens have 1-minute expiry, installation tokens have 1-hour expiry
- **Two-layer auth**: `_gh_auth` generates the token (OpenSSL), then `gh` consumes it (PyJWT) — both
  need to work
- **`py3-cryptography` required**: `gh` CLI uses PyJWT internally for RS256 when dispatching
  `workflow_dispatch` events. Without it: `return self._algorithms[alg_name]` — misleading error
- **Pipe openssl directly to base64**: Never store binary RSA signature in shell variable (null
  bytes truncate signatures — see `Shell Variable Null Byte Limitation` in
  `running-isaac-ros-tests/TACTICAL.md`)

See `running-isaac-ros-tests/TACTICAL.md## GitHub App Token Generation` for full details.

## GitHub App Permissions: actions:write Required

**Insight**: The GitHub App installation needs `actions: write` permission to create
`workflow_dispatch` events. Default `actions: read` causes HTTP 403.

**Strategic options**:

- Verify:
  `curl -s https://api.github.com/app/installations/<ID> -H "Authorization: Bearer $JWT" | jq '.permissions'`
- Required: `{"actions": "write"}` for
  `POST /repos/{owner}/{repo}/actions/workflows/{id}/dispatches`
- Reinstallation needed to change permissions (private key stays the same)

See `running-isaac-ros-tests/TACTICAL.md## GitHub App Permissions` for full details.

## WorkflowTemplate is a CRD, Not Git-Driven

**Insight**: The `isaac-ros-ladder` WorkflowTemplate is a Kubernetes CRD installed on the cluster.
Pushing changes to `k3s/argo-ladder-workflow.yaml` in git does NOT automatically update the cluster
template. The cluster continues running the old template until `kubectl apply` is executed.

**Strategic options**: Apply to both clusters after every push to `k3s/argo-ladder-workflow.yaml`.

See `REFERENCE.md## Argo WorkflowTemplate Management` for apply commands and verification.

## Quick Check Mode is Evidence-Based

**Insight**: The `quick_check=true` mode reads the `ladder-failed-rungs` ConfigMap populated by the
previous full ladder run's results. Rungs not in the failed list are skipped.

See `REFERENCE.md## Quick Check Mode` for commands and ConfigMap seeding.

## Fail-Fast Pre-Flight Checks

**Insight**: The updated workflows validate requirements before starting VLM, catching configuration
issues in seconds rather than timing out after minutes.

**Strategic options**:

| Check                  | Time to Detect | Old Behavior     |
| ---------------------- | -------------- | ---------------- |
| NVIDIA_API_KEY missing | ~5s            | 60s timeout      |
| VLM capability absent  | ~30s           | 10min full run   |
| MCAP repo nonexistent  | Immediate      | 30s + OOM        |
| Nav2 packages missing  | ~30s           | 120s action wait |

**Pre-flight validation steps** (in order):

1. Image is `rs_humble:latest` from Zot
2. VLM provider annotation is non-null
3. NVIDIA_API_KEY environment variable set
4. MCAP file size >1KB (validates repo exists)
5. Nav2 packages present (for Nav2 tests only)

## Plan 37: Rung 15-18 Tactical Guidance

### Rung 15: Memory Stress Test

**Insight**: The three-tier memory model must be validated under real pressure, not just code
review. The `stress-ng` tool allocates memory that competes with ROS2 nodes, triggering the 85%
threshold.

**Strategic options**:

- **Safe pressure**: 6GB on 8GB Jetson leaves ~2GB for OS + ROS2, known safe from Plan 25 profiling
- **Unsafe pressure**: 7GB+ risks OOM kills on workload pod, invalidating the test
- **Monitoring**: Watch `free -h` and ROS2 topic hz in parallel to verify tier behavior
- **Restoration verification**: The 10s sticky timer means resolution restores ~15s after pressure
  release

**Common failure modes**:

- Downsampling activates but frame_skip doesn't: Check `_check_system_memory_and_adjust()` logs
- OOM kill (exit code 137): Pressure too high, reduce `--vm-bytes` from 6G to 5G
- No restoration: Memory didn't drop below 70% threshold, check with `free`

**When to skip**: If running on nano1 with limited headroom, reduce `memory_pressure_gb` to 4.

### Rung 16: Semantic Distillation (Real vs Mock)

**Insight**: Rung 13 validates the pipeline with mock scene_state. Rung 16 validates the actual
distiller node subscribing to live cuVSLAM/NVBLOX/Nav2 topics. This is a critical distinction.

**Strategic options**:

- **Mock (rung 13)**: Static JSON, predictable, fast validation
- **Real (rung 16)**: Dynamic DAG from live topics, validates topic subscription + graph
  construction
- **Topic dependency**: Requires cuVSLAM odometry, NVBLOX mesh, Nav2 costmaps publishing
- **DAG validation**: Check for nodes, edges, robot_pose, obstacles fields in JSON

**Common failure modes**:

- `/vlm/scene_state` empty: Distiller not running or source topics silent
- DAG missing edges: NVBLOX mesh not publishing (check `/nvblox_node/mesh`)
- JSON malformed: Python exception in distiller (check logs with `kubectl logs`)

**When to skip**: If running rosbag-only (no live SLAM), distiller has no topics to subscribe to.

### Rung 17: Golden MCAP Validation

**Insight**: This is the first quantitative VLM accuracy test. Scene Graph F1 >= 0.7 is the
threshold. The test FAILS (not skips) if golden MCAPs are missing, providing clear signal on Plan 29
progress.

**Strategic options**:

- **Ground truth**: Isaac Sim scenarios with known causal graphs (cup on table, etc.)
- **Prediction**: Distiller output from MCAP replay compared to ground truth
- **F1 threshold**: 0.7 is conservative; Qwen 3.5 400B achieves >0.8 on clean synthetic data
- **Per-scenario tuning**: Some scenarios harder than others, adjust threshold based on results

**Common failure modes**:

- Golden MCAP missing: Plan 29 Phase 2b not complete, expected failure
- F1 < 0.7: VLM reasoning quality issue or distiller graph extraction problem
- Spatial error high: NVBLOX mesh alignment issue or coordinate frame mismatch

**When to adjust threshold**: If initial results show F1 = 0.65 consistently, consider lowering to
0.65 and filing improvement tickets rather than blocking CI.

### Rung 18: Multi-Scenario VLM

**Insight**: Running all 4 scenarios validates generalization. A model that passes warehouse but
fails kitchen has overfit to one environment.

**Strategic options**:

- **Sequential execution**: Scenarios run one at a time to avoid memory pressure
- **Per-scenario reporting**: Each scenario has independent F1 score in logs
- **Aggregate pass**: ALL scenarios must pass F1 >= 0.7 (strict AND, not OR)
- **Failure isolation**: One failing scenario doesn't block others from running

**Scenario characteristics**:

| Scenario         | Difficulty | Common Failure               |
| ---------------- | ---------- | ---------------------------- |
| office_clutter   | Medium     | Occlusion handling           |
| warehouse_stacks | Easy       | Clear box boundaries         |
| hallway          | Hard       | Narrow spaces, few features  |
| kitchen_with_cup | Hard       | Causal reasoning (fall risk) |

**When to accept partial failure**: If 3/4 scenarios pass and kitchen is known edge case, consider
separate "core" vs "extended" scenario sets.

## YAML Block Scalar Indentation

**Insight**: GitHub Actions `run: |` blocks and Argo `args:` blocks use YAML block scalars. The
indentation of the first non-empty line determines the block's base indent. Any line with less
indentation ends the block. Comments at column 0 inside block scalars silently break YAML parsing.

**Strategic options**:

- **Rule**: All content inside `run: |` must be indented at least 2 spaces beyond the `run:` key
- **Comments**: Must be indented to the same level as code, never at column 0
- **Nested structures**: `case` statements, `if/else` blocks inside `kubectl exec` must maintain
  consistent indentation relative to the block scalar base
- **Detection**: `yamllint` catches these; `actionlint` catches some too
- **Common pattern**: A comment like `# Install dependencies` at column 0 inside a `run: |` block
  that starts at column 10 silently terminates the block

**Linting tools**:

```bash
yamllint .github/workflows/5-vlm-mcap-test.yml
yamllint .github/workflows/5-vlm-nav2-test.yml
yamllint k3s/argo-ladder-workflow.yaml
actionlint .github/workflows/5-vlm-mcap-test.yml
actionlint .github/workflows/5-vlm-nav2-test.yml
```

**When this bites**: GitHub Actions reports "unexpected key" or Argo reports "invalid YAML" but the
file looks fine visually. The indentation is correct for shell but not for YAML.

## Shell Variable Null Byte Limitation

**Insight**: POSIX shell variables cannot hold null bytes. Storing a 256-byte RSA signature in a
shell variable strips null bytes, producing a corrupted ~253-byte signature. This caused persistent
HTTP 401 errors in `_gh_auth()`.

**Fix**: Pipe directly to base64: `openssl ... | base64 -w0 | tr '+/' '-_' | tr -d '='`

See `running-isaac-ros-tests/TACTICAL.md## GitHub App Token Generation` for full details.

## workflow_dispatch.inputs Indentation

**Insight**: GitHub Actions requires input keys to be indented as children of `inputs:`, not as
siblings. An input at the same indentation level as `inputs:` causes GitHub to parse `inputs` as an
empty map, producing HTTP 422 "Unexpected inputs provided" when `gh workflow run -f key=value` is
called.

**Strategic options**:

- **Symptom**: `gh workflow run -f runner_label=nano2 -f test_type=sanity` returns HTTP 422
- **Root cause**: `runner_label:` at 4 spaces (sibling of `inputs:`) instead of 6 spaces (child)
- **Detection**: `actionlint` catches this; `yamllint` passes (valid YAML, wrong semantics)
- **Pattern**: Check ALL `workflow_dispatch.inputs` in VLM workflows after any edit

**When this bites**: Rung 13 fails with "VLM test timed out" because the workflow was never
dispatched. The Argo rung reports success (gh command ran) but no GitHub Actions run was created.

See `REFERENCE.md## Linting Tools` for the canonical linting sequence.

## gh CLI Pitfalls in Argo Pods

**Insight**: `gh` CLI behaves differently inside K8s pods than on a developer machine. Two issues
blocked rungs 13-15 from polling GitHub Actions workflow completion.

**Strategic options**:

- **`--repo` flag required**: Pods have no git repo context. Always use
  `--repo explicitcontextualunderstanding/isaac_ros_custom` on both `gh workflow run` and
  `gh run list`. Without it, `gh run list` returns empty results.
- **`--json databaseId` not `--json id`**: The `gh run list` API uses `databaseId` for run IDs.
  Using `--json id` returns null silently in scripts.
- **Correct pattern**:
  `gh run list --workflow 5-vlm-mcap-test.yml --limit 1 --json databaseId -q '.[0].databaseId' --repo explicitcontextualunderstanding/isaac_ros_custom`

See `running-isaac-ros-tests/TACTICAL.md## GitHub CLI in Workflow Pods` for full details.


# === WORKFLOWS.md ===

## Workflows for VLM OOM Recovery

### Scenario 1: VLM Node OOM on Startup (8GB Jetson)

**Problem**: The VLM node fails with OOM (exit code 137) shortly after startup.

**Solution 1: Use Minimal VLM Node**

```bash
# Run minimal node with optimized memory settings
python3 minimal_vlm_node.py --ros-args \
    -p max_image_width:=160 \
    -p max_image_height:=120 \
    -p image_quality:=30 \
    -p gc_interval:=1.0 \
    -p aggressive_gc:=true
```

**Solution 2: Check Available Resources First**

```bash
# Check memory before running
MEM_AVAIL=$(free -m | awk '/Mem:/ {print $7}')
if [ "$MEM_AVAIL" -lt 2048 ]; then
    echo "Low memory: ${MEM_AVAIL}MB. Use minimal node."
    python3 minimal_vlm_node.py --ros-args -p max_image_width:=160 -p max_image_height:=120
else
    python3 vlm_node.py
fi
```

### Scenario 2: OOM During MCAP Replay

**Problem**: Memory grows continuously during MCAP replay.

**Solution**: Enable memory-aware downsampling

```bash
python3 vlm_node.py --ros-args \
    -p max_image_width:=160 \
    -p max_image_height:=120 \
    -p image_quality:=30 \
    -p gc_interval:=5.0 \
    -p memory_threshold_high:=80.0 \
    -p memory_threshold_low:=70.0 \
    -p aggressive_gc:=true
```

### Workflow Modifications

For GitHub Actions workflows, update the test step to use minimal node:

```yaml
- name: 'Test VLM Query'
  if: ${{ github.event.inputs.test_type == 'sanity' || github.event.inputs.test_type == 'query' }}
  run: |
    export PATH="$HOME/.local/bin:$PATH"
    export ORCHESTRATOR=k3s
    RUNNER="${{ github.event.inputs.runner_label || 'nano2' }}"
    POD_NAME="$POD_NAME"

    kubectl exec -n isaac-ros $POD_NAME -- bash -c '
      export PATH=/opt/ros/humble/bin:$PATH
      export PYTHONPATH=/workspaces/isaac_ros-dev/src:$PYTHONPATH
      source /opt/ros/humble/setup.bash &&
      source /workspaces/isaac_ros-dev/install/setup.bash &&
      cd /workspaces/isaac_ros-dev/src/isaac_ros_custom &&

      pkill -9 -f "vlm_node.py" 2>/dev/null || true
      pkill -9 -f "ros2daemon" 2>/dev/null || true
      sleep 1

      export LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu/nvidia:$LD_LIBRARY_PATH

      # Use minimal node for sanity tests
      python3 minimal_vlm_node.py --ros-args -p max_image_width:=160 -p max_image_height:=120 -p image_quality:=30 -p gc_interval:=1.0 -p aggressive_gc:=true &
      VLM_PID=$!
      echo "VLM node started with PID: $VLM_PID"

      sleep 5

      if ps -p $VLM_PID > /dev/null; then
        echo "VLM node is running"
      else
        echo "VLM node failed to start"
        exit 1
      fi
    '
```

### Diagnostic Tools

#### Memory Profiler

```bash
kubectl exec -n isaac-ros <pod> -- bash -c '
    source /opt/ros/humble/setup.bash &&
    source /workspaces/isaac_ros-dev/install/setup.bash &&
    pip install objgraph psutil

    python3 - <<END
import os
import psutil
import objgraph

pid = int(os.popen("pgrep -f vlm_node.py").read().strip())
if pid:
    process = psutil.Process(pid)
    print(f"Memory usage: {process.memory_info().rss / 1024 / 1024:.1f}MB ({process.memory_percent():.1f}%)")

    import gc
    gc.collect()
    counts = objgraph.typestats()
    sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    print("\n=== Top 20 object types ===")
    for type_name, count in sorted_counts[:20]:
        print(f"{type_name:30} {count:8}")
END
'
```

