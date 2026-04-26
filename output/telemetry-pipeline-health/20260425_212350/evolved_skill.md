---
name: telemetry-pipeline-health
description: Unified diagnostic for the kilo-proxy → ingester → DB telemetry pipeline on macOS
---



# === SKILL.md ===

---
name: telemetry-pipeline-health
description: Unified diagnostic for the kilo-proxy → ingester → DB telemetry pipeline on macOS
---

# Telemetry Pipeline Health Diagnostic

## When to Use

- After restarting any component of the telemetry stack (proxy, ingester, socat, DB container)
- After editing compose.yml or updating DB URLs
- When ingester shows no new events in DB
- When proxy is running but telemetry is stale
- When Jetsons can't reach the DB (LAN bridge issue)

## Before You Start

**ALWAYS load `proxy-telemetry-health` first.** It contains the network rules, DB URL
conventions, and failure mode catalog. This skill covers the diagnostic script that wraps
those checks into a single command.

## Architecture (Host-Level)

```
kilo-proxy.py :8080
    → token-rotation.log
        → proxy-telemetry-ingester.py
            → socat (vmnet or LAN)
                → honcho-db container :5432
```

Each component has a corresponding LaunchAgent for persistence.

## Quick Run

```bash
telemetry-pipeline-health.sh           # human-readable
telemetry-pipeline-health.sh --json    # JSON for automation
telemetry-pipeline-health.sh --quick   # skip DB reachability test
```

Exit codes: 0=healthy, 1=warnings, 2=critical

## What It Checks (5 Sections)

### 1. Proxy
- Process running (pgrep)
- Log file freshness (last event timestamp, warns if >5min old)

### 2. Ingester
- Process running
- `.ingester_db_url` content validation (forbidden IPs: 127.0.0.1, dynamic container IPs)
- Recent errors in ingester log

### 3. Socat Bridges
- vmnet relay bound to `192.168.64.1:5432`
- LAN bridge bound to `192.168.1.118:5432` (for Jetsons)
- No stale bridges on old container IPs
- No orphaned bridges on deprecated ports (e.g., old 15433)

### 4. LaunchAgents
- `com.user.kilo-ingester` — persistent daemon (has PID)
- `com.user.kilo-proxy-watchdog` — interval task (no PID between runs, checks exit status)
- `com.user.socat-db-lan-bridge` — persistent daemon
- `com.user.honcho-compose` — persistent daemon

### 5. DB Reachability
- TCP connect to `192.168.64.1:5432` (vmnet, for Mac services)
- TCP connect to `192.168.1.118:5432` (LAN, for Jetsons)
- TCP connect to container's current IP (direct)

## Key Pitfalls

### bash 3.2 — No Associative Arrays

macOS ships bash 3.2. Use parallel arrays instead of `declare -A`:

```bash
# WRONG (bash 4+)
declare -A AGENTS=(["kilo-ingester"]="com.user.kilo-ingester")

# RIGHT (bash 3.2)
labels=("kilo-ingester" "socat-db-lan-bridge")
plist_names=("com.user.kilo-ingester" "com.user.socat-db-lan-bridge")
for i in "${!labels[@]}"; do
    name="${labels[$i]}"
    label="${plist_names[$i]}"
done
```

### Container IP Parsing

Don't use `awk '{print $N}'` on `container list` output — column positions change
between versions. Use regex:

```bash
# WRONG — may grab "arm64" or other columns
container_ip=$(container list | grep honcho-db | awk '{print $4}' | tr -d '/')

# RIGHT — extract IP pattern
container_ip=$(container list | grep "honcho-db" | grep -oE '192\.168\.64\.[0-9]+' | head -1)
```

### Interval vs Daemon LaunchAgents

Interval tasks (StartInterval) have NO PID between runs. `launchctl list` shows
LastExitStatus but no PID — this is normal, not a failure.

```bash
if grep -q "StartInterval" "$plist" 2>/dev/null; then
    # Interval task — check LastExitStatus, not PID
    log "interval task (exit=$last_exit)"
else
    # Daemon — must have PID
    warn "no PID — may have exited"
fi
```

### Empty Arrays with set -u

`${ARRAY[@]:-}` FAILS under `set -u`. Use `${ARRAY[@]+"${ARRAY[@]}"}` instead:

```bash
# WRONG — unbound variable error
printf '%s\n' "${ISSUES[@]:-}"

# RIGHT — safe under set -u
printf '%s\n' "${ISSUES[@]+"${ISSUES[@]}"}"
```

## Relationship to Other Skills

| Skill | Scope | Covers Telemetry? |
|-------|-------|-------------------|
| proxy-telemetry-health | DB health queries, failure modes, network rules | Partial (DB + ingester) |
| container-compose-config-drift | Container env var drift | No (containers only) |
| container-health-monitor.sh | Container liveness, WAL-G, auto-recovery | No (host services) |
| **telemetry-pipeline-health** | Full host-level pipeline | **Yes** |

## Key Files

| File | Path |
|------|------|
| Diagnostic script | `~/workspace/nano2/scripts/telemetry-pipeline-health.sh` |
| Proxy log | `~/.local/share/kilo/log/token-rotation.log` |
| Ingester DB URL | `~/.local/share/kilo/.ingester_db_url` |
| Ingester log (mac) | `~/.local/share/kilo/ingester-launchd.log` |
| Ingester log (nano1) | `~/.local/share/kilo/ingester-nano1.log` |
| Ingester log (nano2) | `~/.local/share/kilo/ingester-nano2.log` |
| Socat bridge plist | `~/Library/LaunchAgents/com.user.socat-db-lan-bridge.plist` |

