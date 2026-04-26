

# === SKILL.md ===

---
name: 1Password Connect Operator
type: procedural
description: |
  Manage 1Password Connect Operator in K3s for declarative secret sync into Kubernetes Secrets.
  Use when deploying or debugging OnePasswordItem resources, secret rotation, namespace placement,
  or Claude/API key failures tied to Connect. Especially relevant for Isaac ROS clusters where the
  live CRD only supports `spec.itemPath` and not advanced field remapping.
proficiency: 0.94
composition:
  upstream: []
  downstream: [testing-vlm, running-isaac-ros-tests]
latent_vars:
  credential_lifecycle_critical: true
  token_identity_binding: true
  split_brain_risk: true
  multi_host_sync_required: true
  claude_configuration_sensitive: true
  crd_schema_drift: true
---

# 1Password Connect Operator

Use this skill for K3s secret-sync work, not ad-hoc secret copying.

## File Map

- `WORKFLOWS.md` - exact rollout and verification sequences
- `TACTICAL.md` - strategic patterns and failure-avoidance
- `RECOVERY.md` - if/then recovery playbooks
- `REFERENCE.md` - cluster facts, examples, and Claude-host notes

## Critical Rules

1. Create each `OnePasswordItem` in the namespace where the workload consumes the secret.
2. Treat Connect identity and bearer token updates as multi-host changes: Mac, nano1, nano2, and K8s secrets must stay aligned.
3. On these Isaac ROS clusters, `OnePasswordItem.spec` supports `itemPath` only. Do **not** rely on `spec.mapping`.
4. If a pod shows `CreateContainerConfigError`, verify the target `Secret` exists before debugging the container image.
5. Prefer declarative fixes (`OnePasswordItem`, Helm values, cluster manifests) over manual secret copies.
6. **For AI agents (Kilo, Claude Code)**: Use `secrets.map` + `apple-container-secrets.sh` as the SSOT. Tokens are fetched from 1Password Connect API and stored in Mac Keychain/Linux Keyring.

## Secrets Architecture (April 2026)

```
secrets.map (SSOT)
    ↓ VARNAME=ITEM_ID:FIELD_ID
apple-container-secrets.sh
    ↓ 1Password Connect API (HA: nano1/nano2)
    ↓ Mac Keychain / Linux Kernel Keyring
agent_wrapper.sh
    ↓ RAM disk (~/.enclave)
    ↓ op run --env-file (process-isolated injection)
Kilo provider.env array
```

**Key Files:**

- `.appcontainer/secrets.map` - Single source of truth for all token mappings
- `.appcontainer/scripts/apple-container-secrets.sh` - Authority for fetching secrets from 1Password Connect
- `scripts/agent_wrapper.sh` - Agile Enclave v2 orchestrator, injects rotation pool

- `tests/security/test_agile_enclave.sh` - TDD suite for enclave security
- `.appcontainer/tests/test_deriver_enclave_integration.py` - Runtime validation for vsock + enclave

## Apple Container + vsock Integration (Plan 78)

Secrets flow through the enclave into Apple Container workloads:

```
1Password Connect API
    ↓ apple-container-secrets.sh --generate-exports
Mac Keychain (OP_CONNECT_TOKEN)
    ↓ eval "$(agile_enclave.sh --generate-exports)"
Container Runtime
    ↓ -e VAR="${VAR}" injection
8 Honcho Containers (db, hub, 4 derivers, codegraph, hermes)
```

**Validation**: 27 integration tests validate secret injection across all containers.

```bash
# Run full enclave + vsock validation
python3 -m pytest .appcontainer/tests/test_deriver_enclave_integration.py -v
# 27 tests: DB (2), Hub (3), Derivers (18), Codegraph (2), Unit (2)
```

**Critical**: WALG credentials only go to DB container. Derivers don't need backup keys.

## Quick Triage

1. `kubectl get onepassworditems -A`
2. `kubectl get secret -n <ns> <name>`
3. `kubectl describe onepassworditem <name> -n <ns>`
4. If the cluster rejects fields under `spec`, check CRD compatibility before changing app manifests.

## Operator Compatibility Rule

This environment has an important constraint learned from live rollout work:

- `kubectl explain onepassworditem.spec` shows only `itemPath`
- `spec.mapping` is rejected by the live CRD
- therefore workloads must consume the native field names produced by the operator

Example: if the 1Password item `z_ai` exposes a `glm` field, the Kubernetes pod must read secret key `glm` directly rather than expecting a remapped key like `anthropic-api-key`.

## When to Read More

- Read `WORKFLOWS.md` to deploy Connect, add items, or verify sync.
- Read `TACTICAL.md` when deciding between item renaming, secret naming, or cluster-specific rollout tactics.
- Read `RECOVERY.md` when sync fails, pods show 401s, or Claude cannot resolve API keys.
- Read `REFERENCE.md` for host/IP details and concrete manifest examples.


# === RECOVERY.md ===

# Recovery

## Symptom: `401 Unauthorized`

**STOP: Do not assume token expiry.** Token rotation is rare. Verify service reachability first.

Triage order (check each before moving to the next):

1. **Is the Connect service reachable?**
   ```bash
   curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://192.168.1.86:31633/v1/vaults
   # 000 = unreachable (service down, namespace deleted, network partition)
   # 401 = reachable but unauthenticated (service is UP)
   # 200 = reachable and authenticated
   ```
   - If `000`: check `kubectl get ns onepassword` and `kubectl get pods -n onepassword`
   - If the namespace is missing, the Connect deployment was deleted. Re-deploy before touching tokens.
   - `_op_connect_load.sh` now checks this automatically: look for `UNREACHABLE` vs `TOKEN REJECTED` in stderr.

2. **Token-host mismatch?** Primary token → nano1 (31633), fallback token → nano2 (31307). Sending the wrong token to the wrong host gets 401. `_op_connect_load.sh` auto-swaps — look for `[op-connect] WARNING: token got 401` in stderr.

3. **Connect redeployed?** Namespace deletion and recreation generates new signing keys. Old tokens won't authenticate against the new instance. Verify with:
   ```bash
   curl -s http://192.168.1.86:31633/v1/vaults -H "Authorization: Bearer $TOKEN"
   ```

4. **Stale shell environment?** On Mac, `.zshrc` sources `~/.op-connect-env` at startup. Existing shells retain old values after env file changes. Open a new shell.

Recovery:

```bash
# 1. Run the loader — checks connectivity first, then validates tokens
source .appcontainer/scripts/lib/_op_connect_load.sh
# Read stderr: UNREACHABLE, TOKEN REJECTED, or swap message

# 2. If UNREACHABLE: fix the service (redeploy, check namespace)
# 3. If TOKEN REJECTED: verify token-host pairing in ~/.op-connect-env
# 4. Never rotate tokens without first confirming the service is reachable
```

## Symptom: `OnePasswordItem` exists but `Secret` does not

Recovery:

```bash
kubectl describe onepassworditem <name> -n <namespace>
kubectl logs -n 1password -l app.kubernetes.io/name=connect-operator --tail=100
kubectl explain onepassworditem.spec
```

If the CRD exposes only `itemPath`, remove unsupported fields like `mapping`.

## Symptom: Pod `CreateContainerConfigError`

Recovery:

```bash
kubectl describe pod -n <namespace> <pod>
kubectl get secret -n <namespace> <secret-name>
```

Most often this is a missing secret or missing key, not a broken container image.

## Symptom: Sync pod never wakes up

Recovery:

```bash
curl -i -X GET http://192.168.100.2:31307/v1/vaults \
  -H "Authorization: Bearer $(keyctl print $(keyctl request user op-connect-token @s) | sed 's/^:hex://' | xxd -r -p)"
```

This triggers the lazy-load path.

## Symptom: Claude can use the wrapper in shell but not in-app

Recovery:

```bash
~/workspace/nano2/scripts/op_api_key_wrapper.sh --netcheck
claude -p "hello"
```

Then check:

- absolute `apiKeyHelper` path, not `~`
- no `~/.claude/settings.local.json` override stripping env
- correct `OP_CONNECT_HOST` for the current machine


# === REFERENCE.md ===

# Reference

## Cluster Facts

### Nano1

- Connect external: `http://192.168.1.86:31633`
- Connect internal: `http://192.168.100.1:31633`

### Nano2

- Connect external: `http://192.168.1.81:31307`
- Connect internal: `http://192.168.100.2:31307`

**Note**: Ports differ between clusters. Always verify live service with `kubectl get svc -n 1password`.

## Kernel-Backed Credential Security (Zero-Persistence)

### Architecture

Each cluster runs its own 1Password Connect instance with:

- **Separate `1password-credentials.json`** per instance (Primary/Backup)
- **Each credential scoped to a constrained vault** via `uniqueKey`
- **Credentials stored in Linux kernel keyring** (`@u`), never on disk

### Setup Commands

```bash
# Load credentials into kernel keyring (run once per boot)
cat 1password-credentials-primary.json | keyctl padd user op_primary_cred @u
cat 1password-credentials-backup.json | keyctl padd user op_backup_cred @u

# Securely wipe source files
shred -u 1password-credentials-*.json

# Verify keys exist
keyctl list @u
```

### Systemd Service Template

```ini
[Unit]
Description=1Password Connect Primary
After=network.target

[Service]
ExecStartPre=/bin/bash -c 'export RAW=$(keyctl pipe $(keyctl search @u user op_primary_cred)); \
  echo "OP_SESSION=$(echo -n $RAW | base64 -w0)" > /run/op-primary.env'
EnvironmentFile=/run/op-primary.env
ExecStart=/usr/bin/docker run --name op-connect-primary \
  -e OP_SESSION=${OP_SESSION} \
  -p 8080:8080 \
  1password/connect-api:latest
ExecStopPost=/bin/rm /run/op-primary.env
```

### Failover Logic (Client-Side)

```bash
#!/bin/bash
# Check Primary, fallback to Backup
if curl -s --fail http://localhost:8080/health; then
  export OP_CONNECT_HOST="http://localhost:8080"
else
  export OP_CONNECT_HOST="http://localhost:8081"
fi
op read "op://Limited-Vault/API-Key/password"
```

### Security Benefits

- **No plaintext on disk** — `shred` removes files; `keyctl` keeps data in RAM
- **Kernel protected** — Only the user who loaded keys (or root) can pipe them
- **Minimal footprint** — Uses native Ubuntu `keyutils` and `systemd`
- **Auditability** — 1Password.com shows logs for two distinct "devices"
- **Isolation** — If one Connect instance is compromised, attacker only gains access to its specific limited vault


# === TACTICAL.md ===

# Tactical Notes

## Kernel-Backed Credential Security (Zero-Persistence)

- **Two independent Connect instances (Primary/Backup) provide HA.** Each uses a unique `1password-credentials.json` scoped to a constrained vault. Credentials are loaded into the kernel keyring (`@u`), never stored on disk.
- **Credential lifecycle:** Load into keyring → Systemd pipes to `/run/` env file → Connect container consumes → Env file deleted on stop.
- **Isolation benefit:** If one Connect instance is compromised, attacker only accesses that instance's limited vault. The other instance remains secure.
- **Auditability:** 1Password.com shows two distinct "devices" in logs, making it clear which instance was used.
- **Reboot security:** On power loss, kernel keyring wipes. Credentials must be reloaded via secure remote injection or manual admin start.

## `keyctl padd` vs `keyctl add` — JWT Token Storage (Corrected 2026-03-28)

- **`keyctl padd` is the CORRECT method for JWT tokens.** It reads from stdin and stores as plain text. `keyctl print` returns the token directly.
- **`keyctl add` with hex encoding CORRUPTS JWT tokens.** The `xxd -p` hex encode → `xxd -r -p` decode round-trip silently changes the token. Symptoms: wrong length (641 vs 642 bytes), "Invalid bearer token" from Connect.
- **Keyring decoding must handle both formats.** `keyctl print` returns plain text from `keyctl padd`, or `:hex:`-prefixed from `keyctl add`. Readers must check the prefix:
  ```bash
  raw=$(keyctl print "$KEY_ID")
  if [[ "$raw" == :hex:* ]]; then
      TOKEN=$(echo -n "$raw" | sed 's/^:hex://' | xxd -r -p)
  else
      TOKEN="$raw"
  fi
  ```
- **On Jetson, `keyctl link @u @s` is required** for session possession rights. Without it, `keyctl request user op-connect-token @s` returns "Permission denied". The wrapper and library must call `keyctl new_session && keyctl link @u @s` before reading.

## Token-to-Connect Server Mapping

- **Each Connect instance has a unique signing key (`kid`).** Tokens are server-specific — testing a token against the wrong server produces "Invalid token signature" with KID mismatch details.
- **`op connect server list` CLI names map to specific Connect instances** (not necessarily the local one). Verify by testing generated tokens against both servers.
- **Current mapping (2026-03-28):**
  | CLI Server | ID | Connect Instance | Credentials `deviceUuid` |
  |---|---|---|---|
  | `nano1` | `KGNLATQS6NFY3LPY3ADDOLOLPE` | nano1 (192.168.100.1:31633) | `gbupqqxqvsmiokhiyjujtom3y4` |
  | `nanotoken2` | `POJF4NNRYNDCHKLIDNFEX2HGBM` | nano2 (192.168.100.2:31307) | `ye5sj4yei5zuc3slscad2bcy2u` |
- **If tokens fail after Connect redeployment:** The `1password-credentials.json` may have been replaced, changing the signing key. Regenerate tokens against the new instance.

## Strategic Option: Rename in 1Password vs. Adapt the Workload

When the operator CRD lacks field remapping support:

- Prefer adapting the workload to the native secret key name if the change is isolated.
- Prefer renaming the field in 1Password only if multiple workloads need the same stable key.

In this environment, the cheaper move was to keep the Kubernetes secret name `hermes-api-keys` but consume the native `glm` field directly.

## Namespace Placement Heuristic

Put the `OnePasswordItem` where the pod runs:

- `isaac-ros` for Isaac ROS/Hermes/Honcho workloads
- `arc-systems` for runner/controller secrets
- avoid `default` unless the workload truly runs there

## Split-Brain Risk

Identity/token mismatches look like application failures but are really Connect state failures. If one node rotates credentials or token material alone, expect `401` or missing secret sync across the fleet.

## Namespace Deletion Is Catastrophic (2026-04-01)

Deleting the `onepassword` namespace destroys:
- Connect pods and services (NodePorts become unbound)
- Connect API tokens (signing keys are regenerated on re-deploy)
- All synced Kubernetes Secrets

**Symptoms on Mac/Jetsons**: TLS handshake failures, connection refused, or `000` HTTP codes from curl. The `_op_connect_load.sh` connectivity gate now correctly reports `UNREACHABLE` instead of misleading `no valid token-host pairing`.

**Recovery**: Re-deploy Connect from manifests, then verify tokens still authenticate. If tokens get 401 after re-deploy, the new instance has different signing keys — update tokens in `~/.op-connect-env` and reload on all machines.

**Prevention**: Protect the namespace. Never run `kubectl delete ns onepassword` — use `kubectl delete` on individual resources if needed. Consider a `ResourceQuota` or `LimitRange` that doesn't prevent namespace use but makes deletion more deliberate.

## Cluster Drift Pattern

If repo manifests are correct but pods still fail:

1. inspect the live `OnePasswordItem`
2. inspect the live CRD schema
3. inspect the synced `Secret`
4. only then debug the consuming deployment

This avoids wasting time on image or pod logic when the real issue is operator compatibility or missing synced objects.


# === WORKFLOWS.md ===

# Workflows

## 1. Install or Reinstall Connect

```bash
kubectl create namespace 1password

helm install connect 1password/connect \
  --namespace 1password \
  --set-file connect.credentials=1password-credentials.json \
  --set operator.create=true \
  --set operator.token.value="$OP_CONNECT_TOKEN" \
  --set operator.pollingInterval=30
```

Then verify:

```bash
kubectl get pods -n 1password
kubectl logs -n 1password -l app.kubernetes.io/name=connect-operator --tail=50
```

## 2. Add a Secret Declaratively

Create a `OnePasswordItem` in the consumer namespace:

```yaml
apiVersion: onepassword.com/v1
kind: OnePasswordItem
metadata:
  name: honcho-admin-token
  namespace: isaac-ros
spec:
  itemPath: vaults/ckqn5qdoygn5wqrsggkquil4ei/items/optdrai7prjlzbbrkhf6c5xx4m
```

Apply and verify:

```bash
kubectl apply -f k3s/onepassword/onepassword-item-honcho-admin-token.yaml
kubectl get onepassworditem -n isaac-ros honcho-admin-token
kubectl get secret -n isaac-ros honcho-admin-token
kubectl describe onepassworditem -n isaac-ros honcho-admin-token
```

## 3. Verify CRD Capability Before Using Advanced Fields

```bash
kubectl explain onepassworditem.spec
```

If only `itemPath` appears, do not use `spec.mapping`. Adjust the workload to consume the secret's native key names.

## 4. Force a Fresh Sync

If the item exists but the secret does not:

```bash
kubectl get onepassworditems -A
kubectl describe onepassworditem <name> -n <namespace>
kubectl logs -n 1password -l app.kubernetes.io/name=connect-operator --tail=100
curl -i -X GET http://192.168.1.86:31307/v1/vaults \
  -H "Authorization: Bearer $(keyctl print $(keyctl request user op-connect-token @s) | sed 's/^:hex://' | xxd -r -p)"
```

## 5. Validate Workload Consumption

```bash
kubectl get secret -n isaac-ros hermes-api-keys -o jsonpath='{.data}'
kubectl describe pod -n isaac-ros <pod-name>
kubectl logs -n isaac-ros <pod-name> -c <container> --tail=100
```

If the pod is in `CreateContainerConfigError`, first confirm the referenced `Secret` and key both exist.
