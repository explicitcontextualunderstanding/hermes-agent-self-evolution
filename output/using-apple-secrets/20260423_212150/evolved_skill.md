---
name: using-apple-secrets
type: procedural
description: |
  Configure x-apple-secrets in Container-Compose for declarative, zero-persistence secret mounts
  from the Agile Enclave. Use when adding secrets to container services, filtering secrets per
  container, or migrating from --print-export workflows. Integrates with Plan 78 enclave and
  Plan 86 tmpfs mounts for secure secret injection without shell exposure.
proficiency: 0.85
composition:
  upstream: [1password-connect-operator]
  downstream: [running-container-compose-tests, secrets-chain-debugging]
latent_vars:
  enclave_mounted: false
  secrets_available: []
  tmpfs_size_mb: 1
  cleanup_policy: immediate
---



# === SKILL.md ===

---
name: using-apple-secrets
type: procedural
description: |
  Configure x-apple-secrets in Container-Compose for declarative, zero-persistence secret mounts
  from the Agile Enclave. Use when adding secrets to container services, filtering secrets per
  container, or migrating from --print-export workflows. Integrates with Plan 78 enclave and
  Plan 86 tmpfs mounts for secure secret injection without shell exposure.
proficiency: 0.85
composition:
  upstream: [1password-connect-operator]
  downstream: [running-container-compose-tests, secrets-chain-debugging]
latent_vars:
  enclave_mounted: false
  secrets_available: []
  tmpfs_size_mb: 1
  cleanup_policy: immediate
---

# Using Apple Secrets (x-apple-secrets)

Use this skill for declarative secret management in Container-Compose, not manual environment exports.

**Companion skill**: [`secrets-chain-debugging`](../../.hermes/profiles/operations/skills/devops/secrets-chain-debugging) — when containers fail with credential errors, use that skill to trace the full chain from 1Password Connect through the enclave to container runtime. This skill covers configuration; that skill covers debugging.

## File Map

- `WORKFLOWS.md` - exact configuration sequences and validation steps
- `TACTICAL.md` - security patterns and migration strategies
- `RECOVERY.md` - if/then recovery for mount failures and secrets.map field ID mismatches
- `REFERENCE.md` - YAML schema, examples, and enclave paths

## Critical Rules

1. **Enclave must exist first** - Verify `/Volumes/AGENT_SECRETS/` (Mac) or `/run/user/$(id -u)/agent_secrets/` (Jetson) is mounted.
2. **Filter secrets per container** - Each service should only receive secrets it needs (horizontal isolation).
3. **Default mount is `/run/secrets`** - Container entrypoint reads files and exports to environment.
4. **Never mix with --print-export** - Choose one: declarative (x-apple-secrets) OR legacy (shell export).
5. **Cleanup is immediate by default** - tmpfs unmounts after container start for zero persistence.
6. **Files are uppercase** - Filter names like `API_KEY` match `api_key.txt` in enclave.
7. **Linux keyring is primary** - On Jetson, `load_connect_token()` checks kernel keyring before files.

## Prerequisites (Plan 78)

```bash
# 1. Verify enclave is mounted
ls /Volumes/AGENT_SECRETS/
# Expected: *.txt files (lowercase names)

# 2. Check available secrets
ls /Volumes/AGENT_SECRETS/*.txt | xargs -n1 basename -s .txt
```

## Quick Start

### 1. Basic Configuration

```yaml
# docker-compose.yml
version: '3.8'
services:
  my-app:
    image: myapp:latest
    x-apple-secrets:
      mount: /run/secrets
      filter:
        - API_KEY
        - DATABASE_PASSWORD
```

### 2. Container Entrypoint (Automatic)

```bash
# Inside container - secrets loaded automatically
$ env | grep API_KEY
API_KEY=sk-abc123...

$ ls /run/secrets/
API_KEY  DATABASE_PASSWORD
```

### 3. Application Usage

```python
# Python
import os
api_key = os.environ['API_KEY']

# Node.js
const apiKey = process.env.API_KEY;
```

## Security Guarantees

| Feature | Configuration | Benefit |
|---------|--------------|---------|
| Read-only | `read_only: true` | Secrets cannot be modified |
| No execution | `noexec: true` | Cannot execute from mount |
| No SUID | `nosuid: true` | No privilege escalation |
| Immediate cleanup | `cleanup: immediate` | RAM-only, deleted on unmount |
| Restricted permissions | `mode=0400` | Owner read-only |

## Workflow: Add Secrets to New Service

```bash
# 1. Identify needed secrets from enclave
ls /Volumes/AGENT_SECRETS/
# api_key.txt  db_password.txt  aws_credentials.txt

# 2. Add x-apple-secrets to compose file
cat >> docker-compose.yml << 'EOF'
  new-service:
    image: myapp:latest
    x-apple-secrets:
      filter:
        - API_KEY          # matches api_key.txt
        - DB_PASSWORD      # matches db_password.txt
EOF

# 3. Start service
container-compose up -d new-service

# 4. Verify secrets mounted
docker exec new-service ls /run/secrets/
# API_KEY  DB_PASSWORD

# 5. Verify environment exported
docker exec new-service env | grep API_KEY
```

## Migration from --print-export

**Before (vulnerable):**
```bash
export API_KEY=$(agile_enclave.sh --get API_KEY)
container-compose up -d  # Secret in shell env
```

**After (secure):**
```yaml
services:
  app:
    x-apple-secrets:
      filter: [API_KEY]
```

## Validation Checklist

- [ ] Enclave mounted at `/Volumes/AGENT_SECRETS/`
- [ ] Secret files exist in enclave (lowercase .txt)
- [ ] Filter names match files (uppercase in YAML)
- [ ] Service has `x-apple-secrets` configuration
- [ ] Container entrypoint exports secrets
- [ ] No secrets in parent shell environment
- [ ] tmpfs unmounted after start (immediate cleanup)

## References

- Plan 78: Unified SSOT Agile Enclave
- Plan 86: x-apple-secrets Extension
- Container-Compose YAML Extensions
- SECURITY_CONTAINER.md

## See Also

- `WORKFLOWS.md` - Step-by-step configuration workflows
- `TACTICAL.md` - Security patterns and strategies
- `RECOVERY.md` - Troubleshooting mount failures and secrets.map field ID mismatches
- `REFERENCE.md` - Complete YAML schema reference
- `secrets-chain-debugging` skill (Hermes) - Debug credential failures by tracing the secrets chain from 1Password Connect through enclave to container runtime

## Orchestrator: Read Secrets Directly From Enclave

The orchestrator (`apple-container-honcho-compose.sh`) needs env vars for compose `${VAR}`
substitution. Two approaches — only one is correct.

### WRONG: `apple-container-secrets.sh --generate-exports`
Calls 1P Connect API. Hangs if hosts unreachable or token stale. Blocks entire orchestrator.
```bash
eval "$($SECRETS_SCRIPT --generate-exports)"   # BLOCKS — do not use
```

### CORRECT: Direct enclave file reads
Instant. No network. No 1P Connect dependency.
```bash
ENCLAVE_DIR="${HOME}/.enclave"
[[ -f "$ENCLAVE_DIR/aws_access_key.txt" ]] && \
  export WALG_AWS_ACCESS_KEY_ID="$(cat "$ENCLAVE_DIR/aws_access_key.txt" | tr -d '\n')"
[[ -f "$ENCLAVE_DIR/aws_secret_key.txt" ]] && \
  export WALG_AWS_SECRET_ACCESS_KEY="$(cat "$ENCLAVE_DIR/aws_secret_key.txt" | tr -d '\n')"
```

### Why
- `agile_enclave.sh` populates `~/.enclave/*.txt` before orchestrator runs
- Compose `x-apple-secrets` reads from the same enclave for container injection
- Orchestrator env vars fill `${VAR}` placeholders in compose YAML
- No 1P Connect API call needed — enclave is source of truth

### Also: Make `_op_connect_load.sh` non-fatal
```bash
source "${SCRIPT_DIR}/lib/_op_connect_load.sh" 2>/dev/null || {
  echo "[orchestrator] WARNING: 1P Connect unavailable — using enclave directly" >&2
}
```
Do NOT `exit 1` on Connect failure. The orchestrator doesn't need it.

## Token Rotation Recovery

When API tokens are incorrectly suspended (e.g., 403 misclassified as bans), recovery requires
changes in three layers:

### 1. Proxy Code (`kilo-proxy.py`)

The `PROVIDERS` table has `token_files` and `token_env_vars` per provider. To restore tokens:

```python
# Before suspension:
"token_files": ["nvidia_1.txt", "nvidia_2.txt", "nvidia_3.txt", "nvidia_4.txt"],

# During suspension (BROKEN — proxy has zero tokens):
"token_files": [],  # ALL TOKENS SUSPENDED

# Recovery: restore the list
"token_files": ["nvidia_1.txt", "nvidia_2.txt", "nvidia_3.txt", "nvidia_4.txt"],
```

Also update the header comment to reflect current state (suspension comments confuse future debugging).

### 2. Secrets Map (`secrets.map`)

Container env vars are injected from `secrets.map`. Commented entries mean containers won't see them:

```bash
# Uncomment and remove DEPRECATED notes
sed -i '' 's/^# LLM_NVIDIA_API_KEY=/LLM_NVIDIA_API_KEY=/' secrets.map
```

### 3. Compose.yml (`compose.yml`)

If env vars were hardcoded as commented-out in compose, restore them:

```bash
# Uncomment deriver env vars
sed -i '' 's/# LLM_VLLM_API_KEY: SUSPENDED.*/LLM_VLLM_API_KEY: '\''${LLM_VLLM_API_KEY}'\''/' compose.yml
```

### Verification

```bash
# 1. Proxy is running with tokens
tail -10 ~/.local/share/kilo/log/token-rotation.log | grep "Loaded"

# 2. Secrets are uncommented
grep "NVIDIA" secrets.map | grep -v "^#"

# 3. Compose has env vars
grep "LLM_VLLM_API_KEY" compose.yml | grep -v "^#"

# 4. Token files exist and are readable
ls -la ~/.enclave/nvidia_*.txt
```

### Token File Locations (Platform-Specific)

Token files live in different directories depending on the platform. When adding new tokens,
you must copy to **all** locations.

| Platform | ENCLAVE_DIR | Example |
|----------|-------------|---------|
| **Mac** | `~/.enclave/` | `~/.enclave/nvidia_5.txt` |
| **Jetsons (nano1/nano2)** | `/run/user/1000/agent_secrets/` | `/run/user/1000/agent_secrets/nvidia_5.txt` |

**Why different**: The proxy's `ENCLAVE_DIR` fallback chain is:
1. `~/.enclave/` (primary, Mac always has this)
2. `/run/user/{uid}/agent_secrets/` (Jetson systemd tmpfs, used by 1Password Connect)
3. `/dev/shm/{uid}_agile_enclave/` (last resort)

On Jetsons, the proxy uses path #2. On Mac, path #1.

### Adding New Token Files

```bash
# 1. Save to Mac
echo "nvapi-XXXXX" > ~/.enclave/nvidia_N.txt
chmod 600 ~/.enclave/nvidia_N.txt

# 2. Add to proxy token_files list in kilo-proxy.py
# "token_files": [..., "nvidia_N.txt"],

# 3. Copy to Jetsons (must use correct ENCLAVE_DIR path)
ssh nano1-cmd "echo 'nvapi-XXXXX' > /run/user/1000/agent_secrets/nvidia_N.txt && chmod 600 /run/user/1000/agent_secrets/nvidia_N.txt"
ssh nano2-cmd "echo 'nvapi-XXXXX' > /run/user/1000/agent_secrets/nvidia_N.txt && chmod 600 /run/user/1000/agent_secrets/nvidia_N.txt"

# 4. Restart proxies on all machines
# Mac:
kill -9 $(lsof -ti :8080) 2>/dev/null; sleep 2
/opt/homebrew/bin/python3 -u ~/workspace/nano2/scripts/kilo-proxy.py >> ~/.local/share/kilo/log/token-rotation.log 2>&1 &

# Jetsons:
ssh nano1-cmd "fuser -k 8080/tcp 2>/dev/null; sleep 2; nohup python3 -u ~/workspace/nano2/scripts/kilo-proxy.py >> ~/.local/share/kilo/log/token-rotation.log 2>&1 &"
ssh nano2-cmd "fuser -k 8080/tcp 2>/dev/null; sleep 2; nohup python3 -u ~/workspace/nano2/scripts/kilo-proxy.py >> ~/.local/share/kilo/log/token-rotation.log 2>&1 &"

# 5. Verify all machines loaded the new token
grep "nvidia.*Loaded" ~/.local/share/kilo/log/token-rotation.log | tail -1
ssh nano1-cmd "grep 'nvidia.*Loaded' ~/.local/share/kilo/log/token-rotation.log | tail -1"
ssh nano2-cmd "grep 'nvidia.*Loaded' ~/.local/share/kilo/log/token-rotation.log | tail -1"
```

### Proxy Restart (macOS Pitfalls)

- **Must kill old process first** — port 8080 stays bound after `pkill`. Use:
  `kill -9 $(lsof -ti :8080) 2>/dev/null; sleep 2`
  (macOS has no `fuser`; use `lsof -ti :8080`)
- **Must use Homebrew Python** — `/opt/homebrew/bin/python3` (3.14+), NOT `/usr/bin/python3` (3.9.6).
  The proxy uses `list[str] | None` syntax requiring Python 3.10+.
- **LaunchAgent handles this** — `com.user.kilo-proxy.plist` already specifies `/opt/homebrew/bin/python3`.
  Use `launchctl load/unload` for persistent management.

### Script Consolidation: agent_wrapper.sh vs agile_enclave.sh

**Status**: CONSOLIDATED (Apr 18, 2026). All four entry points are now symlinks to `agile_enclave.sh`.

```
agile_enclave.sh (canonical, 20KB)
├── agent_wrapper.sh -> agile_enclave.sh          [symlink]
├── op_api_key_wrapper.sh -> agile_enclave.sh      [symlink]
├── ~/.claude/wrappers/op_api_key_wrapper.sh -> agile_enclave.sh  [symlink]
└── isaac_ros_custom/.appcontainer/scripts/apple-container-secrets.sh -> agile_enclave.sh  [symlink]
```

All `sync_secrets_to_enclave()` calls route through the single canonical script. No more
token count divergence. Old copies preserved as `.bak` files in `scripts/`.

**Portable shebang**: Uses `#!/usr/bin/env bash` for cross-platform compatibility (Mac + Jetson).

### Adding New Secrets: secrets.map is Source of Truth

`sync_secrets_to_enclave()` dynamically builds its secret list from `secrets.map` via
`_build_secrets_from_map()`. You only need to update **ONE file**:

1. **`secrets.map`** — `VARNAME=ITEM_ID:FIELD_ID` (1Password references)

The script auto-derives enclave filenames from variable names via `_varname_to_filename()`.
Numbered tokens (e.g., `LLM_NVIDIA_API_KEY_2` → `nvidia_2.txt`) are handled by special-case
mapping in the function.

**Example — adding a new secret:**
```bash
# Add to secrets.map (in isaac_ros_custom/.appcontainer/secrets.map)
echo 'MY_NEW_API_KEY=abc123def456:api_key' >> secrets.map

# Sync
bash scripts/agile_enclave.sh --sync
```

**Fallback array**: If `secrets.map` is missing or empty, `sync_secrets_to_enclave()` falls back
to a hardcoded `secrets=()` array in the script. This is for bootstrapping only — the normal
path is dynamic read from `secrets.map`.

### Discovering 1Password Items for secrets.map

When you know the vault and item titles but not the IDs, query the 1Password Connect API:

```bash
# 1. Get token from keyring (Jetson) or env file (Mac)
TOKEN=$(keyctl pipe $(keyctl request user op-connect-token @s 2>/dev/null) 2>/dev/null)
HOST="http://192.168.1.81:31307"  # or nano1 at 192.168.1.86:31633
VAULT="ckqn5qdoygn5wqrsggkquil4ei"

# 2. List items in vault, filter by title
curl -s -H "Authorization: Bearer $TOKEN" \
  "$HOST/v1/vaults/$VAULT/items" | \
  python3 -c "import json,sys; [print(f\"{i['title']}  ID={i['id']}\") for i in json.load(sys.stdin) if 'gemma' in i.get('title','').lower()]"

# 3. For each item, get field details
curl -s -H "Authorization: Bearer $TOKEN" \
  "$HOST/v1/vaults/$VAULT/items/$ITEM_ID" | \
  python3 -c "import json,sys; d=json.load(sys.stdin); [print(f\"  id={f['id']} label={f.get('label',''):20s} type={f['type']} len={len(f.get('value',''))}\") for f in d.get('fields',[])]"

# 4. Construct secrets.map entry using the field ID (NOT the label)
# Standard fields:  password, username, notesPlain → use label
# Custom fields:   api_key, key_value, key_id → use the field UUID (id=...)
#
# Example:
#   id=u5v46bczhmqqrsoghdmadma5qy label=key_value         → use u5v46bczhmqqrsoghdmadma5qy
#   id=password                  label=password [51 chars] → use password
```

**Key rule**: Use the `id` field for custom fields; use the label for standard fields (`password`,
`username`). The `_fetch_secret()` function matches both `f['id']` and `f['label']`, but custom
field labels are inconsistent across items. Always verify with a live `--get` test.

### Verifying a New Secret

```bash
# Test on-demand fetch (confirms secrets.map is correct)
~/workspace/nano2/scripts/agile_enclave.sh --get LLM_GEMMA_API_KEY
# Expected: actual API key value (not "ERROR: field '...'")

# Test bulk sync (confirms agile_enclave.sh secrets array is correct)
~/workspace/nano2/scripts/agile_enclave.sh --sync 2>&1 | grep -i "WARNING\|error"
# Expected: no warnings for the new secret

# Verify file was populated
wc -c ~/.enclave/gemma_1.txt
# Expected: >0 bytes (40 bytes for Google AI keys, 51+ for NVIDIA)
```

**sync_secrets_to_enclave error handling pitfall**: The function redirects both stdout AND stderr to the output file (`> "${ENCLAVE_PATH}/${filename}" 2>&1`). If a fetch fails, the error message ("ERROR: field not found") gets written to the token file instead of the key, and the file size looks correct. Always check `wc -c` AND verify the content isn't an error string.

**Why not derive from secrets.map?**
- `secrets.map` has no filename info — just 1Password item/field references
- Numbered tokens (e.g., `nvidia_1.txt` through `nvidia_5.txt`) can't be auto-derived from variable names
- New secrets are rare (~once every few months), so hardcoding is pragmatic

### Lessons Learned

- **Removed keys (Apr 2026)**: `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, `LLM_ANTHROPIC_API_KEY`
  (z_ai item removed from vault), and `LLM_GEMINI_API_KEY` (fleet switched to NVIDIA NIM + GEMMA).
- **Added keys (Apr 2026)**: `LLM_GEMMA_API_KEY` – `LLM_GEMMA5_API_KEY` for Google AI Studio
  free-tier rotation via `google-proxy` in `kilo-proxy.py`.
- **Field ID vs label**: Use field `id` (UUID) for custom fields (`api_key`, `key_value`); use
  label for standard fields (`password`, `username`). Labels are inconsistent across items.
- **Linux keyring priority**: `load_connect_token()` checks kernel keyring before files. On Jetson,
  `keyctl link @u @s` in `~/.bashrc` ensures session access to user keyring tokens.
- **403 ≠ BAN** on NVIDIA NIM free tier — rate limits return 403 with no distinguishing header
- **Verify on dashboard first** — always check provider dashboard before suspending tokens
- **Token files should NOT be renamed** to `.banned` for temporary issues
- **Proxy classification logic** prevents future false suspensions — patch `kilo-proxy.py` to
  distinguish `RATE LIMITED` (silent rotation) from `AUTH BLOCKED` (real ban notification)
- **Three layers must all be restored**: proxy code, secrets.map, compose.yml — missing any
  one breaks a different part of the stack
- **Jetson ENCLAVE_DIR is `/run/user/1000/agent_secrets/`** — not `~/.enclave/`. Always verify
  with `grep 'ENCLAVE_DIR' kilo-proxy.py` and check the fallback chain.
- **NVIDIA NIM `id` field normalization** — models can return `id: null` or integer. The proxy's
  `_normalize_chunk()` patches this before kilo sees it.
- **1Password Connect field ID vs label mismatch** — `_fetch_secret()` in `agile_enclave.sh` matched
  fields by `f['id']` (a unique UUID like `p4uzwe6cuobtlgdkhwtz7ev6f4`), but `secrets.map` references
  fields by label (e.g., `api_key`). These never matched for custom fields, causing error strings to be
  written to enclave token files instead of real keys. Fix: always use field `id` for custom fields,
  label for standard fields. If a token file contains `ERROR: field '...'` instead of a key, this
  mismatch is the likely cause.


# === RECOVERY.md ===

# Using Apple Secrets - Recovery Playbooks

If/then recovery paths for common x-apple-secrets failures.

---

## Recovery 1: Enclave Not Mounted

### Symptoms
```
Error: SecretsMountManager failed
enclaveNotMounted(path: "/Volumes/AGENT_SECRETS")
```

### Diagnosis
```bash
ls /Volumes/AGENT_SECRETS/
# ls: /Volumes/AGENT_SECRETS/: No such file or directory
```

### Recovery Steps

**Step 1: Check if RAM disk exists**
```bash
diskutil list | grep AGENT_SECRETS
# No output = not mounted
```

**Step 2: Create and mount enclave (Plan 78)**
```bash
# Run Plan 78 setup
~/workspace/nano2/scripts/agile_enclave.sh --create

# Verify
ls /Volumes/AGENT_SECRETS/
```

**Step 3: Populate secrets**
```bash
# From 1Password Connect
~/workspace/nano2/scripts/apple-container-secrets.sh --generate-exports

# Manual
printf "secret-value" > /Volumes/AGENT_SECRETS/api_key.txt
```

**Step 4: Verify**
```bash
ls /Volumes/AGENT_SECRETS/*.txt
# Should show secret files
```

### Bypass Path
```bash
# If enclave unavailable, use legacy method temporarily
export API_KEY="manual-value"
container-compose up -d
```

---

## Recovery 2: Secret Not Found in Enclave / secrets.map Field ID Mismatch

### Symptoms
```
Warning: Secret API_KEY not found in enclave
Container started with 0 secrets
```

Or, from 1Password Connect:
```
ERROR: field 'tklq' not found
```

### Diagnosis
```bash
ls /Volumes/AGENT_SECRETS/
# Shows: other_secret.txt (not api_key.txt)
```

Or, check secrets.map for truncated field IDs:
```bash
# Inspect raw secrets.map (read_file redacts secrets)
python3 -c "
with open('.appcontainer/secrets.map') as f:
    for line in f:
        if 'HONCHO' in line:
            item, field = line.strip().split('=')[1].split(':')
            print(f'Stored field ID: {field} (len={len(field)})')
"
# If length < 10, the field ID is truncated (1Password field IDs are ~25-30 chars)
```

### Recovery Steps

**Step 1: Check actual enclave files**
```bash
ls /Volumes/AGENT_SECRETS/*.txt | xargs basename -s .txt
# Output: OTHER_SECRET (uppercase filter name)
```

**Step 2a: Fix filter name (enclave mismatch)**
```yaml
# Before (wrong)
x-apple-secrets:
  filter: [API_KEY]  # Looking for api_key.txt

# After (correct)
x-apple-secrets:
  filter: [OTHER_SECRET]  # Matches other_secret.txt
```

**Step 2b: Fix secrets.map field ID (truncated ID)**
```bash
# Verify actual field IDs from 1Password
op item get ITEM_ID --vault VAULT_ID --format json | \
  python3 -c "import sys,json; d=json.load(sys.stdin); [print(f['id'], f['label']) for f in d.get('fields',[])]"

# Update secrets.map with full field ID
# Before: HONCHO_ADMIN_TOKEN=ITEM_ID:tklq
# After:  HONCHO_ADMIN_TOKEN=ITEM_ID:qdbzhc3izxejnemsze4zxntklq
```

**Step 3: Add missing secret**
```bash
# Create missing file in enclave
printf "secret-value" > /Volumes/AGENT_SECRETS/api_key.txt
```

**See Also**: For full chain debugging from 1Password Connect through enclave to container runtime, see `secrets-chain-debugging` skill.

---

## Recovery 3: Mount Permission Denied

### Symptoms
```
Error: mountFailed(underlying: ...)
Operation not permitted
```

### Diagnosis
```bash
# Check if running as root
whoami
# Not root = permission issue

# Check SIP status (macOS)
csrutil status
# System Integrity Protection is on (normal)
```

### Recovery Steps

**Step 1: Use container user**
```yaml
services:
  app:
    user: "1000:1000"  # Non-root user
    x-apple-secrets:
      mount: /run/secrets
```

**Step 2: Grant permissions**
```bash
# On macOS - tmpfs requires specific entitlements
# Ensure container runtime has:
# - com.apple.security.hypervisor
# - com.apple.security.cs.allow-jit
```

**Step 3: Check mount options**
```bash
# Try mounting manually
sudo mkdir -p /tmp/test-mount
sudo mount -t tmpfs -o size=1m tmpfs /tmp/test-mount

# If this works, the issue is container-specific
```

---

## Recovery 4: Secrets Not Exported to Environment

### Symptoms
```bash
container exec my-service env | grep API_KEY
# No output - secret not in environment
```

But:
```bash
container exec my-service cat /run/secrets/API_KEY
# Shows secret content
```

### Diagnosis
Entrypoint not loading secrets.

### Recovery Steps

**Step 1: Check entrypoint**
```bash
container inspect my-service --format '{{.Config.Entrypoint}}'
# Should include: /entrypoint-secrets-loader.sh
```

**Step 2: Verify entrypoint exists**
```bash
container exec my-service ls -la /entrypoint-secrets-loader.sh
# Not found = missing entrypoint
```

**Step 3: Add entrypoint to Dockerfile**
```dockerfile
# Dockerfile
COPY entrypoint-secrets-loader.sh /entrypoint-secrets-loader.sh
RUN chmod +x /entrypoint-secrets-loader.sh
ENTRYPOINT ["/entrypoint-secrets-loader.sh"]
CMD ["original-command"]
```

**Step 4: Manual export (workaround)**
```bash
# Inside container
for f in /run/secrets/*; do
  export "$(basename $f)=$(cat $f)"
done
```

---

## Recovery 5: Container Can't Read Mounted Files

### Symptoms
```
/bin/sh: can't open '/run/secrets/API_KEY': Permission denied
```

### Diagnosis
```bash
container exec my-service ls -la /run/secrets/
# Shows: ---------- 1 root root ...
```

### Recovery Steps

**Step 1: Check file permissions**
```bash
# Should be 0400 (owner read)
container exec my-service stat -f "%Lp" /run/secrets/API_KEY
# Shows: 400 (correct)
```

**Step 2: Check container user**
```bash
container exec my-service whoami
# Shows: appuser (not root)
```

**Step 3: Fix ownership**
```yaml
services:
  app:
    user: "0:0"  # Run as root (temporary fix)
    x-apple-secrets:
      filter: [API_KEY]
```

**Step 4: Proper fix**
```dockerfile
# Dockerfile - adjust permissions
RUN chmod 444 /run/secrets/*  # Allow read for all
```

**Note:** x-apple-secrets sets 0400 by default. Consider:
- Running container as same user that mounts
- Using 0444 if multiple users need access (security trade-off)

---

## Recovery 6: Cleanup Not Working

### Symptoms
```bash
ls /tmp/container-secrets-*
# Shows: container-secrets-xxx (should be cleaned)
```

### Diagnosis
Cleanup policy not triggering.

### Recovery Steps

**Step 1: Check cleanup policy**
```yaml
x-apple-secrets:
  cleanup: immediate  # Should auto-cleanup
```

**Step 2: Manual cleanup**
```bash
# List mounts
df -h | grep container-secrets

# Unmount
sudo umount /tmp/container-secrets-xxx

# Remove
sudo rm -rf /tmp/container-secrets-*
```

**Step 3: Verify process**
```bash
# Check if cleanup task is running
ps aux | grep cleanup
```

---

## Recovery 7: Wrong Secrets Mounted

### Symptoms
```bash
container exec my-service env | grep SECRET
# Shows: WRONG_SECRET=***
```

### Diagnosis
Filter mismatch or wrong enclave.

### Recovery Steps

**Step 1: Check filter**
```yaml
# Current filter
filter: [API_KEY]

# Check enclave
ls /Volumes/AGENT_SECRETS/
# Shows: other_secret.txt (not api_key.txt)
```

**Step 2: Fix filter**
```yaml
filter: [OTHER_SECRET]  # Match actual enclave files
```

**Step 3: Check for multiple enclaves**
```bash
# Wrong enclave mounted?
df -h | grep AGENT
# /Volumes/AGENT_SECRETS_DEV (wrong!)
# Should be: /Volumes/AGENT_SECRETS
```

**Step 4: Switch enclave**
```bash
# Unmount wrong
sudo umount /Volumes/AGENT_SECRETS_DEV

# Mount correct
~/workspace/nano2/scripts/agile_enclave.sh --mount
```

---

## Manual Override

### Bypass x-apple-secrets

If all else fails, use legacy method temporarily:

```bash
# 1. Get secret from enclave manually
API_KEY=$(cat /Volumes/AGENT_SECRETS/api_key.txt)

# 2. Export to environment
export API_KEY

# 3. Start container
container-compose up -d

# 4. Verify (accepting shell exposure)
env | grep API_KEY
```

**Security Note:** This bypasses zero-persistence guarantees. Use only for emergency recovery.

---

## Recovery Checklist

- [ ] Enclave mounted at `/Volumes/AGENT_SECRETS/`
- [ ] Secret files exist with .txt extension
- [ ] Filter names match enclave files (uppercase)
- [ ] Container has entrypoint-secrets-loader.sh
- [ ] Entrypoint is executable
- [ ] Container user can read /run/secrets/
- [ ] Mount shows tmpfs with ro,noexec,nosuid
- [ ] Cleanup policy working as expected
- [ ] No shell exposure in parent process


# === REFERENCE.md ===

# Using Apple Secrets - Reference

YAML schema, file paths, and configuration reference.

---

## YAML Schema Reference

### Service-Level Configuration

```yaml
services:
  <service-name>:
    x-apple-secrets:
      mount: <string> # Container path (default: /run/secrets)
      filter: [<string>, ...] # Secret names (default: all)
      read_only: <bool> # Read-only mount (default: true)
      noexec: <bool> # No execution (default: true)
      nosuid: <bool> # No setuid (default: true)
      cleanup: <enum> # immediate | on_stop | manual (default: immediate)
```

### Global Configuration

```yaml
x-apple-secrets:
  version: <string> # Extension version (default: "1.0")
  enclave: <path> # Source enclave (default: /Volumes/AGENT_SECRETS)
  default_mount: <path> # Default mount path (default: /run/secrets)
  format: <enum> # files (default: files)
  permissions: <string> # File mode (default: "0400")
  cleanup: <enum> # Default cleanup (default: immediate)
```

---

## Configuration Examples

### Example 1: Minimal Configuration

```yaml
version: "3.8"
services:
  app:
    image: myapp:latest
    x-apple-secrets:
      filter: [API_KEY]
```

**Result:**

- Mount: /run/secrets
- Secrets: API_KEY only
- Read-only: true
- Noexec: true
- Nosuid: true
- Cleanup: immediate

### Example 2: Full Configuration

```yaml
version: "3.8"

x-apple-secrets:
  version: "1.0"
  enclave: /Volumes/AGENT_SECRETS
  default_mount: /run/secrets
  cleanup: immediate

services:
  api:
    image: myapi:latest
    x-apple-secrets:
      mount: /run/secrets
      filter:
        - API_KEY
        - DB_PASSWORD
        - JWT_SECRET
      read_only: true
      noexec: true
      nosuid: true
      cleanup: immediate
```

### Example 3: Multiple Services

```yaml
version: "3.8"
services:
  web:
    image: nginx
    x-apple-secrets:
      filter:
        - TLS_CERT
        - TLS_KEY

  api:
    image: myapp
    x-apple-secrets:
      filter:
        - API_KEY
        - DB_PASSWORD

  worker:
    image: myworker
    x-apple-secrets:
      filter:
        - DB_PASSWORD
        - REDIS_PASSWORD
```

### Example 4: Development vs Production

**docker-compose.yml (base)**

```yaml
services:
  app:
    image: myapp
    x-apple-secrets:
      filter: [API_KEY, DB_PASSWORD]
```

**docker-compose.dev.yml**

```yaml
services:
  app:
    x-apple-secrets:
      cleanup: on_stop # Keep for debugging
```

**docker-compose.prod.yml**

```yaml
services:
  app:
    x-apple-secrets:
      cleanup: immediate # Remove immediately
```

---

## File Paths

| Path                           | Description                   |
| ------------------------------ | ----------------------------- |
| `/Volumes/AGENT_SECRETS/`      | Source enclave (Plan 78)      |
| `/run/secrets/`                | Default container mount point |
| `/tmp/container-secrets-*/`    | Host tmpfs mount points       |
| `entrypoint-secrets-loader.sh` | Container entrypoint script   |

---

## Environment Variables

### Inside Container (Exported)

```bash
$ env | grep -E "(API_KEY|DB_PASSWORD)"
API_KEY=sk-abc123...
DB_PASSWORD=secret456...
```

### Files in Container

```bash
$ ls -la /run/secrets/
-r-------- 1 root root 12 Jan 1 00:00 API_KEY
-r-------- 1 root root 16 Jan 1 00:00 DB_PASSWORD

$ cat /run/secrets/API_KEY
sk-abc123...
```

---

## Mount Options

### Default Options

| Option | Value | Purpose             |
| ------ | ----- | ------------------- |
| type   | tmpfs | RAM-only filesystem |
| size   | 1m    | Maximum 1MB         |
| mode   | 0400  | Owner read-only     |
| noexec | -     | Prevent execution   |
| nosuid | -     | Prevent setuid      |

### Verification

```bash
# Check mount
container exec my-service mount | grep /run/secrets
tmpfs on /run/secrets type tmpfs (ro,mode=400,size=1m,noexec,nosuid)
```

---

## Secret File Format

### Enclave Files (Source)

```
/Volumes/AGENT_SECRETS/
├── api_key.txt           # Contains: sk-abc123
├── db_password.txt       # Contains: secret456
└── jwt_secret.txt        # Contains: eyJhbGci...
```

- Format: Plain text files
- Extension: .txt
- Name: Lowercase with underscores
- Content: Single value per file

### Filter Names (YAML)

```yaml
filter:
  - API_KEY # Matches api_key.txt
  - DB_PASSWORD # Matches db_password.txt
  - JWT_SECRET # Matches jwt_secret.txt
```

- Format: Uppercase with underscores
- Case-insensitive matching

---

## Cleanup Policies

| Policy      | Behavior                     | Use Case               |
| ----------- | ---------------------------- | ---------------------- |
| `immediate` | Unmount 2s after start       | Production, CI/CD      |
| `on_stop`   | Unmount when container stops | Development, debugging |
| `manual`    | Never auto-unmount           | Special workflows      |

---

## Integration Points

### With Plan 78 (Agile Enclave)

```
1Password Connect API
    ↓
agile_enclave.sh --generate-exports
    ↓
/Volumes/AGENT_SECRETS/*.txt
    ↓
x-apple-secrets filter
    ↓
Container tmpfs at /run/secrets/
```

### With Plan 84 (vSock Relays)

```yaml
services:
  db:
    x-apple-relays:
      - type: vsock-db
        port: 5432
    x-apple-secrets:
      filter: [DB_PASSWORD]
```

### With Plan 85 (Security Gates)

```
Container Start Request
    ↓
SecretsMountValidator
    ├── AMFI validation
    ├── Horizontal isolation
    └── ESF logging
    ↓
SecretsMountManager.createSecretsMount()
    ↓
Container starts with secrets
```

---

## Common Patterns

### Pattern: Secret Rotation

```yaml
# Version secrets
services:
  api:
    x-apple-secrets:
      filter:
        - API_KEY_V2 # New key
```

### Pattern: Service-Specific Prefixes

```yaml
services:
  staging-api:
    x-apple-secrets:
      filter:
        - STAGING_API_KEY

  prod-api:
    x-apple-secrets:
      filter:
        - PROD_API_KEY
```

### Pattern: Shared Secrets

```yaml
services:
  web:
    x-apple-secrets:
      filter: [SHARED_SECRET]

  api:
    x-apple-secrets:
      filter: [SHARED_SECRET, API_KEY]
```

---

## Troubleshooting Reference

| Symptom           | Cause                 | Solution                              |
| ----------------- | --------------------- | ------------------------------------- |
| Enclave not found | Plan 78 not setup     | Run agile_enclave.sh --create         |
| Secret not found  | Filter mismatch       | Check enclave file names              |
| Permission denied | User mismatch         | Run container as root or adjust perms |
| Mount failed      | No tmpfs support      | Check container runtime               |
| Cleanup failed    | Process still running | Kill process or manual cleanup        |

---

## API Reference

### SecretsMountManager

```swift
actor SecretsMountManager {
    init(enclavePath: String, logger: Logger)

    func createSecretsMount(
        for containerID: String,
        config: XAppleSecretsConfig
    ) async throws -> SecretsMount

    func cleanupMount(for containerID: String) async throws
    func loadSecrets(filter: [String]?) async throws -> [String: String]
}
```

### SecretsMountValidator

```swift
actor SecretsMountValidator {
    func validateSecretsMount(
        config: XAppleSecretsConfig,
        containerCID: Int
    ) async -> SecurityValidationResult
}
```

---

## Token Refresh Patterns (NVIDIA, etc.)

### Why secrets.map over op CLI

The 1Password CLI (`op read "op://vault/item/field"`) uses **path-based references** which fail if item names change. The secrets.map uses **Connect REST API with UUIDs** which always works.

```bash
# CLI - FAILS if item renamed
op read "op://tokens/build.nvidia.com/password"

# Connect API - ALWAYS WORKS (UUIDs: item_id:field_id)
# Format in secrets.map: VARIABLE_NAME=ITEM_ID:FIELD_ID
LLM_NVIDIA_API_KEY=67uqaq...word
```

### Resolve Field IDs by Label (Recommended)

When you know the field label (e.g., `admin_token`, `db_password`, `api_key`) but not the UUID field ID:

```bash
# Get field ID by label — works with both 1Password CLI and Connect
op item get ITEM_ID --vault VAULT_ID --format json --field "admin_token" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('field id:', d.get('id'))"

# List all fields with labels for an item
op item get ITEM_ID --vault VAULT_ID --format json | \
  python3 -c "import sys,json; d=json.load(sys.stdin); [print(f['id'], f['label']) for f in d.get('fields',[])]"
```

This is the authoritative way to resolve field IDs. Use the label (human-readable) to query, get the UUID back, then update `secrets.map`.

### Refresh via Connect API (Legacy)

```bash
cd ~/workspace/isaac_ros_custom_clean/.appcontainer/scripts
source ./lib/_op_connect_load.sh
MAPPING_FILE=../secrets.map bash -c '
  source "$MAPPING_FILE"

  # Fetch each NVIDIA token
  for var in LLM_NVIDIA_API_KEY LLM_NVIDIA_API_KEY_2 LLM_NVIDIA_API_KEY_3 LLM_NVIDIA_API_KEY_4; do
    IFS=":" read -r item_id field_id <<< "${!var}"
    val=$(curl -s -H "Authorization: Bearer $OP_CO...KEN" \
      "http://192.168.64.1:31307/v1/vaults/ckqn5qdoygn5wqrsggkquil4ei/items/$item_id" | \
      python3 -c "
import json, sys
d = json.load(sys.stdin)
for f in d.get('fields', []):
    if f.get('id') == '$field_id':
        print(f.get('value', ''))
        break
")
    num=$(echo $var | tr -d 'LLM_NVIDIA_API_KEY_')
    [ -z "$num" ] && num=1
    echo "$val" > ~/.enclave/nvidia_$num.txt
  done
'
```

### Token Testing

```bash
for i in 1 2 3 4; do
  token=$(cat ~/.enclave/nvidia_$i.txt)
  code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "https://integrate.api.nvidia.com/v1/chat/completions" \
    -H "Authorization: Bearer $token" \
    -d '{"model":"meta/llama-3.1-8b-instruct","messages":[{"role":"user","content":"OK"}],"max_tokens":5}')
  echo "nvidia_$i: $code"
done
```

**Note:** 403 responses are often rate limiting, not bans. Keys showing as ACTIVE on NVIDIA dashboard but returning 403 is typically temporary rate limiting.

## See Also

- Plan 78: Unified SSOT Agile Enclave
- Plan 84: vSock Native Relay Finalization
- Plan 85: Security Hardening
- Plan 86: x-apple-secrets Extension
- Container-Compose Documentation


# === TACTICAL.md ===

# Using Apple Secrets - Tactical Patterns

Strategic patterns and security considerations for x-apple-secrets deployment.

---

## Pattern 1: Horizontal Secret Isolation

### Principle: Least Privilege

Each container receives only the secrets it requires. This prevents:
- Container A from accessing Container B's credentials
- Blast radius expansion if one container is compromised
- Accidental secret exposure through shared mounts

### Example: Honcho Stack

```yaml
# Database: Only DB credentials
honcho-db:
  image: walg-db:vsock
  x-apple-secrets:
    filter:
      - HONCHO_DB_PASSWORD
      - WALG_AWS_ACCESS_KEY_ID
      - WALG_AWS_SECRET_ACCESS_KEY

# Hub: Only API tokens
honcho-hub:
  image: honcho:latest
  x-apple-secrets:
    filter:
      - HONCHO_ADMIN_TOKEN
      - LLM_ANTHROPIC_API_KEY

# Deriver: Only AI API key
honcho-deriver:
  image: honcho:latest
  x-apple-secrets:
    filter:
      - LLM_ANTHROPIC_API_KEY
```

**Result:** Three isolated secret sets, no overlap.

---

## Pattern 2: Cleanup Policy Strategy

### immediate (Default)

**Use for:** Production, CI/CD

```yaml
x-apple-secrets:
  cleanup: immediate
```

- tmpfs unmounts after container start
- Secrets available only during init
- Maximum security

### on_stop

**Use for:** Development, debugging

```yaml
x-apple-secrets:
  cleanup: on_stop
```

- tmpfs persists until container stops
- Can inspect `/run/secrets/` while running
- Good for troubleshooting

### manual

**Use for:** Specialized workflows

```yaml
x-apple-secrets:
  cleanup: manual
```

- Explicit cleanup required
- Full control over lifecycle

---

## Pattern 3: Migration Strategy

### Phase Approach

**Phase 1 (Week 1-2): Parallel Operation**
```yaml
# Keep existing, add x-apple-secrets
services:
  app:
    image: myapp
    environment:
      API_KEY: ${API_KEY}  # Legacy - keep
    x-apple-secrets:
      filter: [API_KEY]      # New - added
```

**Phase 2 (Week 3-4): Switch Over**
```yaml
# Remove legacy, keep new
services:
  app:
    image: myapp
    # environment section removed
    x-apple-secrets:
      filter: [API_KEY]
```

**Phase 3 (Week 5+): Cleanup**
```bash
# Remove from shell export scripts
# Remove from .env files
# Update documentation
```

---

## Pattern 4: Secret Naming Convention

### Enclave Files (Lowercase)

```
/Volumes/AGENT_SECRETS/
├── api_key.txt
├── db_password.txt
├── aws_access_key_id.txt
├── aws_secret_access_key.txt
└── stripe_webhook_secret.txt
```

### Filter Names (Uppercase in YAML)

```yaml
filter:
  - API_KEY                    # api_key.txt
  - DB_PASSWORD                # db_password.txt
  - AWS_ACCESS_KEY_ID          # aws_access_key_id.txt
  - AWS_SECRET_ACCESS_KEY      # aws_secret_access_key.txt
  - STRIPE_WEBHOOK_SECRET      # stripe_webhook_secret.txt
```

### Strategy

1. Use descriptive names
2. Include service name prefix if needed
3. Match environment variable names
4. Be consistent across projects

---

## Pattern 5: Multi-Environment Configuration

### Base + Override Pattern

**docker-compose.yml (base)**
```yaml
services:
  api:
    image: myapp:latest
    x-apple-secrets:
      filter:
        - API_KEY
        - DB_PASSWORD
```

**docker-compose.prod.yml (production)**
```yaml
services:
  api:
    x-apple-secrets:
      cleanup: immediate
```

**docker-compose.dev.yml (development)**
```yaml
services:
  api:
    x-apple-secrets:
      cleanup: on_stop
```

### Usage

```bash
# Production
container-compose -f docker-compose.yml -f docker-compose.prod.yml up

# Development
container-compose -f docker-compose.yml -f docker-compose.dev.yml up
```

---

## Security Considerations

### Threat Model

| Threat | Mitigation | Verification |
|--------|-----------|------------|
| Shell exposure | Secrets never in parent process | `env \| grep SECRET` (empty) |
| Process listing | No env in `ps eww` output | `ps eww \| grep SECRET` (empty) |
| Core dumps | tmpfs excluded from dumps | `ulimit -c 0` |
| Container escape | Read-only, noexec mount | `mount \| grep secrets` |
| Privilege escalation | nosuid, 0400 permissions | `ls -la /run/secrets/` |

### Defense in Depth

1. **Enclave Level**: /Volumes/AGENT_SECRETS is RAM-only
2. **Mount Level**: tmpfs with ro,noexec,nosuid
3. **File Level**: 0400 permissions (owner read)
4. **Cleanup Level**: immediate unmount
5. **Access Level**: filtered per container

---

## Anti-Patterns

### ❌ Don't: Mount All Secrets Everywhere

```yaml
# Wrong: All containers get all secrets
services:
  web:
    x-apple-secrets:
      filter: null  # or omitted
  db:
    x-apple-secrets:
      filter: null
```

### ✅ Do: Filter Per Service

```yaml
# Right: Each service gets minimum required
services:
  web:
    x-apple-secrets:
      filter: [API_KEY]
  db:
    x-apple-secrets:
      filter: [DB_PASSWORD]
```

### ❌ Don't: Mix Both Methods

```yaml
# Wrong: Using both --print-export AND x-apple-secrets
services:
  app:
    environment:
      API_KEY: ${API_KEY}  # From shell export
    x-apple-secrets:
      filter: [API_KEY]      # From enclave
```

### ✅ Do: Choose One Method

```yaml
# Right: x-apple-secrets only
services:
  app:
    x-apple-secrets:
      filter: [API_KEY]
```

---

## Performance Considerations

### Startup Time

- tmpfs creation: ~5ms
- Secret loading: ~1ms per secret
- Total overhead: <50ms for typical service

### Memory Usage

- tmpfs size: 1MB default
- Per secret: ~bytes to KB
- Total per container: <100KB typical

### Cleanup

- `immediate`: Slight delay (2s) for container startup
- `on_stop`: Memory held until container stop
- `manual`: Developer responsibility

---

## Integration Patterns

### With 1Password Connect

```
1Password Vault
    ↓
op connect token
    ↓
agile_enclave.sh --generate-exports
    ↓
/Volumes/AGENT_SECRETS/
    ↓
x-apple-secrets filter
    ↓
Container tmpfs
```

### With Honcho

```
x-apple-secrets filter
    ↓
Honcho container
    ↓
Honcho admin token available
    ↓
Agent authentication
```

### With vsock (Plan 84)

```yaml
services:
  db:
    x-apple-relays:
      - type: vsock-db
        port: 5432
    x-apple-secrets:
      filter: [DB_PASSWORD]
```

Both extensions work together seamlessly.


# === WORKFLOWS.md ===

# Using Apple Secrets - Workflows

Sequential workflows for configuring x-apple-secrets in Container-Compose.

---

## Workflow 1: Configure Secrets for a New Service

### Prerequisites
```bash
# Verify Plan 78 enclave is mounted
ls /Volumes/AGENT_SECRETS/
# Should show: *.txt files

# Check available secrets
ls /Volumes/AGENT_SECRETS/*.txt | xargs -n1 basename -s .txt
```

### Steps

**Step 1: Identify Required Secrets**
```bash
# List secrets available in enclave
ls /Volumes/AGENT_SECRETS/

# Example output:
# api_key.txt
# db_password.txt
# aws_access_key.txt
# aws_secret_key.txt
```

**Step 2: Add x-apple-secrets to Compose File**
```yaml
# docker-compose.yml
version: '3.8'
services:
  my-service:
    image: myapp:latest
    x-apple-secrets:
      mount: /run/secrets          # Optional: defaults to /run/secrets
      filter:                      # Optional: defaults to all secrets
        - API_KEY                  # Matches api_key.txt (case-insensitive)
        - DB_PASSWORD              # Matches db_password.txt
      read_only: true             # Optional: defaults to true
      noexec: true                # Optional: defaults to true
      nosuid: true                # Optional: defaults to true
      cleanup: immediate          # Optional: defaults to immediate
```

**Step 3: Update Container Entrypoint (if needed)**
```bash
#!/bin/bash
# entrypoint.sh - automatically done by entrypoint-secrets-loader.sh

# Secrets are already exported as environment variables
exec "$@"
```

**Step 4: Start Service**
```bash
# Start the service
container-compose up -d my-service
```

**Step 5: Verify Secrets Mounted**
```bash
# Check secrets are mounted
container exec my-service ls /run/secrets/
# Expected: API_KEY DB_PASSWORD

# Check environment variables
container exec my-service env | grep -E "(API_KEY|DB_PASSWORD)"
# Expected: API_KEY=*** DB_PASSWORD=***
```

---

## Workflow 2: Migrate from --print-export

### Before (Legacy)
```bash
# deploy.sh
export API_KEY=$(agile_enclave.sh --get API_KEY)
export DB_PASSWORD=$(agile_enclave.sh --get DB_PASSWORD)
container-compose up -d
# Secrets exposed in shell
```

### After (Secure)
```bash
# deploy.sh - no exports needed
container-compose up -d
# Secrets never touch shell
```

```yaml
# docker-compose.yml
services:
  my-service:
    image: myapp:latest
    # Remove: environment section with secret references
    x-apple-secrets:
      filter:
        - API_KEY
        - DB_PASSWORD
```

### Migration Steps

1. **Add x-apple-secrets configuration**
2. **Remove environment variables** from compose file
3. **Remove shell exports** from deployment scripts
4. **Update application** to read from env (no code change needed)
5. **Test** with `container-compose up`
6. **Verify** no secrets in shell: `env | grep API_KEY` (should be empty)

---

## Workflow 3: Filter Secrets Per Container

### Use Case: Database vs Web Service

**Database (needs DB credentials):**
```yaml
services:
  database:
    image: postgres:14
    x-apple-secrets:
      filter:
        - POSTGRES_USER
        - POSTGRES_PASSWORD
```

**Web Service (needs API keys):**
```yaml
services:
  web:
    image: nginx
    x-apple-secrets:
      filter:
        - API_KEY
        - JWT_SECRET
```

### Verification
```bash
# Database has DB secrets only
container exec database ls /run/secrets/
# POSTGRES_USER POSTGRES_PASSWORD

# Web has API secrets only
container exec web ls /run/secrets/
# API_KEY JWT_SECRET
```

---

## Workflow 4: Configure Global Defaults

### Global Configuration
```yaml
# docker-compose.yml
version: '3.8'

x-apple-secrets:
  version: "1.0"
  enclave: /Volumes/AGENT_SECRETS
  default_mount: /run/secrets
  cleanup: immediate

services:
  service-a:
    image: app-a
    x-apple-secrets:
      filter: [API_KEY_A]  # Uses global defaults

  service-b:
    image: app-b
    x-apple-secrets:
      mount: /custom/secrets  # Overrides global
      filter: [API_KEY_B]
```

---

## Workflow 5: Troubleshoot Mount Failures

### Symptom: Container fails to start

**Step 1: Check Enclave**
```bash
ls /Volumes/AGENT_SECRETS/
# If empty: Enclave not mounted (Plan 78 issue)
```

**Step 2: Check Container Logs**
```bash
container logs my-service
# Look for: "Enclave not found" or "Mount failed"
```

**Step 3: Verify Secret Names**
```bash
# Check filter matches enclave files
ls /Volumes/AGENT_SECRETS/*.txt
# api_key.txt -> filter: API_KEY (correct)
# api-key.txt -> filter: API-KEY (wrong!)
```

**Step 4: Check Mount Options**
```bash
# Inside container
container exec my-service mount | grep /run/secrets
# Should show: tmpfs with ro,noexec,nosuid
```

**Step 5: Manual Mount Test**
```bash
# Try mounting manually
sudo mkdir -p /tmp/test-secrets
sudo mount -t tmpfs -o size=1m,mode=0400,noexec,nosuid tmpfs /tmp/test-secrets
```

---

## Workflow 6: Validate Security Configuration

### Checklist

```bash
# 1. No secrets in parent shell
env | grep -E "(API_KEY|PASSWORD|SECRET)"
# Expected: No output

# 2. Secrets in container only
container exec my-service env | grep -E "(API_KEY|PASSWORD)"
# Expected: Shows secrets

# 3. Mount is read-only
container exec my-service touch /run/secrets/test 2>&1
# Expected: Read-only file system

# 4. Mount is tmpfs
container exec my-service mount | grep /run/secrets
# Expected: tmpfs

# 5. Files have restricted permissions
container exec my-service ls -la /run/secrets/
# Expected: -r--------

# 6. Cleanup occurred (if immediate)
ls /tmp/container-secrets-* 2>/dev/null
# Expected: No files (cleaned up)
```

---

## Validation Commands

| Check | Command | Expected |
|-------|---------|----------|
| Enclave mounted | `ls /Volumes/AGENT_SECRETS/` | *.txt files |
| Secrets available | `ls *.txt \| xargs basename -s .txt` | Secret names |
| Compose valid | `container-compose config` | No errors |
| Secrets mounted | `container exec svc ls /run/secrets/` | Filtered secrets |
| Env exported | `container exec svc env \| grep KEY` | Values shown |
| Mount options | `container exec svc mount \| grep secrets` | ro,noexec,nosuid |
| No shell exposure | `env \| grep SECRET` | Empty |

