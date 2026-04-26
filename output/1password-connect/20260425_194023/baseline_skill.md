

# === SKILL.md ===

---
name: 1password-connect
proficiency: 0.85
latent_vars:
  - op_connect_reachable: [true, false]
  - kernel_keyring_loaded: [true, false]
---

# 1Password Connect — Kernel Keyring Storage

OP_CONNECT_TOKEN is stored in the **Linux kernel keyring** for zero-persistence security on Jetson
hosts (nano1, nano2).

## Architecture

```
Kernel Keyring (@u + @s)
├── op-connect-token (primary, nano1)
└── op-connect-fallback-token (fallback, nano2)
        ↓
Claude/Kilo Wrappers
├── Read from kernel keyring (@s first)
├── Authenticate to 1Password Connect API
└── Fetch API keys from 1Password vault
```

## Setup (After Each Boot)

```bash
# Load tokens into kernel keyring
~/workspace/isaac_ros_custom/scripts/load-op-tokens-to-keyring.sh
```

This script:

1. Initializes session keyring: `keyctl new_session`
2. Links user keyring to session: `keyctl link @u @s`
3. Loads primary token (nano1): `op-connect-token`
4. Loads fallback token (nano2): `op-connect-fallback-token`

## Files

- **Load script**: `scripts/load-op-tokens-to-keyring.sh`
- **Wrapper**: `~/workspace/nano2/scripts/op_api_key_wrapper.sh`
- **Claude symlink**: `~/.claude/wrappers/op_api_key_wrapper.sh`
- **Secrets mapping**: `~/.1password-secrets.env`
- **Primary token file**: `~/.op-connect-env` (fallback, will be removed)
- **Fallback token file**: `~/.op-connect-fallback-env` (fallback, will be removed)

## Jetson-Specific: Session Linking Required

Jetson Linux uses **restricted kernel keyring permissions** by default. To read keys, you need
**possession rights**:

```bash
# Without session linking - Permission denied
keyctl request user op-connect-token @u
keyctl print <KEY_ID>  # ❌ keyctl_read_alloc: Permission denied

# With session linking - Works!
keyctl new_session
keyctl link @u @s
keyctl request user op-connect-token @s
keyctl print <KEY_ID>  # ✅ :hex:65794a6862...
```

## High Availability

The wrapper checks tokens in order:

1. **Primary**: `op-connect-token` (nano1 at `http://192.168.100.1:31633`)
2. **Fallback**: `op-connect-fallback-token` (nano2 at `http://192.168.100.2:31307`)

If nano1's 1Password Connect service fails, the wrapper automatically uses nano2's token.

## Security Benefits

| Aspect              | Kernel Keyring       | File-Based       |
| ------------------- | -------------------- | ---------------- |
| **Persistence**     | Wiped on reboot      | Persists on disk |
| **Access Control**  | Kernel ACL + session | File permissions |
| **Audit Trail**     | Kernel logging       | None             |
| **Memory Location** | Kernel memory        | Disk             |

## Verification

```bash
# Check tokens are loaded
keyctl show @s | grep op-connect

# Test wrapper
DIAG_SOURCE=1 ~/.claude/wrappers/op_api_key_wrapper.sh --check
# Expected: SOURCE=kernel-keyring FOUND len=642

# Test API key retrieval
~/.claude/wrappers/op_api_key_wrapper.sh ANTHROPIC_API_KEY
```

## Integration with Claude Code

Claude Code uses:

- **Wrapper**: `~/.claude/wrappers/op_api_key_wrapper.sh` (symlink to `~/workspace/nano2/scripts/`)
- **Secrets mapping**: `~/.1password-secrets.env`
- **API keys**: Retrieved from 1Password Connect service

Workflow:

1. Claude Code calls wrapper for `ANTHROPIC_API_KEY`
2. Wrapper reads `OP_CONNECT_TOKEN` from kernel keyring
3. Wrapper authenticates to 1Password Connect (`http://192.168.100.1:31633`)
4. 1Password returns API key from vault `tokens` → `z_ai` → `glm`
5. Wrapper returns API key to Claude Code
