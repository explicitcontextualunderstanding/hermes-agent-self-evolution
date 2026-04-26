---
name: s3-multi-machine-parallel-transfer
description: >-
  Distribute large S3-to-S3 copy workloads across multiple machines in a
  local fleet (Mac + Jetsons + other devices) when server-side copy is
  unavailable or bandwidth-constrained.
version: 1.0.0
author: Hermes Agent
---



# === SKILL.md ===

---
name: s3-multi-machine-parallel-transfer
description: >-
  Distribute large S3-to-S3 copy workloads across multiple machines in a
  local fleet (Mac + Jetsons + other devices) when server-side copy is
  unavailable or bandwidth-constrained.
version: 1.0.0
author: Hermes Agent
---

# Parallel Multi-Machine S3 Transfer Pattern

## The Problem

You need to copy a large dataset (20+ GB) between two S3-compatible endpoints, but:

- Server-side copy (temporary pod/VM in source network) is **unavailable** due to capacity constraints, policy, or no compute in source network
- Your local machine's internet bandwidth is too slow (hours to days)
- You have **multiple machines on the same local network** (Mac, Jetsons, PCs) that could share the load

## The Pattern

**Split the file list across machines, each downloads+uploads independently.** Total data through your internet is unchanged (still all files), but **wall-clock time** is reduced by the number of active machines (assuming router can handle concurrent transfers).

```
Machine 1:  download file A  →  upload A
Machine 2:  download file B  →  upload B
Machine 3:  download file C  →  upload C
      ↓
Local router handles 3× concurrent traffic (still total same bytes)
```

**Key insight:** Bandwidth sharing is additive — if your internet can sustain 10 MB/s for one stream, three simultaneous streams may achieve ~3–8 MB/s each (total 9–24 MB/s) depending on router and ISP.

## When to Use

✅ Use when:
- Server-side copy is blocked/unavailable
- You have 2+ machines on same LAN with AWS credentials
- Total data is >20 GB and single-machine transfer is too slow
- Machines have sufficient local disk space (≥ largest file)
- You can coordinate via SSH/scripting

❌ Don't use when:
- Machines are on different networks (WAN transfers, not LAN)
- Provider limits concurrent connections per IP (may trigger throttling)
- Files are extremely large (≥100 GB each) — single machine should handle one at a time anyway

## Step-by-Step

### 1. Inventory your fleet

Identify machines that can participate:
- OS: Linux/macOS with `aws` CLI
- Credentials: `~/.aws/credentials` with source + dest profiles
- Disk: Free space ≥ largest file to copy
- Network: On same LAN (low-latency, high-bandwidth local routing)
- SSH access: From coordinator machine (your Mac) via passwordless keys

Example fleet:
```bash
MACHINES=("local" "nano1" "nano2")
# local = the Mac you're sitting at
# nano1 = Jetson Orin Nano at 192.168.100.2
# nano2 = Jetson Orin Nano at 192.168.100.1
```

### 2. Generate the list of files to copy

Compute the **difference** between source and destination:

```bash
# Source objects (exclude temp files like .cache)
aws s3 ls --profile runpod-s3 --region us-nc-1 --endpoint-url https://s3api-us-nc-1.runpod.io s3://xssve1bbu4/ --recursive 2>/dev/null |
  awk '{print $4}' |
  grep -v ".cache" |
  sort > /tmp/source-objects.txt

# Dest objects (strip prefix)
aws s3 ls s3://isaac-sim-6-0-dev/runpod-backups/isaac-sim-6/ --recursive 2>/dev/null |
  awk '{print $4}' |
  sed 's|^runpod-backups/isaac-sim-6/||' |
  sort > /tmp/dest-objects.txt

# Objects in source but not in dest = work queue
comm -23 /tmp/source-objects.txt /tmp/dest-objects.txt > /tmp/missing-objects.txt
echo "Files to copy: $(wc -l < /tmp/missing-objects.txt)"
```

### 3. Distribute work across machines

**Round-robin assignment** (simple, equitable):

```bash
i=0
while IFS= read -r key; do
  MACHINE="${MACHINES[$((i % NUM_MACHINES))]}"
  echo "$key → $MACHINE" >> /tmp/work-queue.txt
  i=$((i + 1))
done < /tmp/missing-objects.txt
```

**Alternative: size-based assignment** (assign largest files to fastest machines):
```bash
# Get file sizes with keys, sort largest first
aws s3 ls --profile runpod-s3 --region us-nc-1 --endpoint-url https://s3api-us-nc-1.runpod.io s3://xssve1bbu4/ --recursive 2>/dev/null |
  awk '{print $3, $4}' |
  sort -rn > /tmp/source-with-sizes.txt

# Then distribute round-robin from this sorted list
```

### 4. Copy command per machine

Each machine runs **download-then-upload** (not streaming pipe) to avoid pipefail issues and allow resume:

```bash
# On machine X, for file KEY:
TEMP_FILE="/mnt/bigdata/s3-transfer/$(basename "$KEY")"

# Download
aws s3 cp --profile runpod-s3 --region us-nc-1 --endpoint-url https://s3api-us-nc-1.runpod.io "s3://xssve1bbu4/$KEY" "$TEMP_FILE"

# Upload
aws s3 cp "$TEMP_FILE" "s3://isaac-sim-6-0-dev/runpod-backups/isaac-sim-6/$KEY"

# Cleanup
rm -f "$TEMP_FILE"
```

**Why download-then-upload vs stream?**
- Download can resume partially (`aws s3 cp --continue`)
- Upload verifies local file size before sending (no empty partials)
- Each stage's exit code is independent (no pipefail ambiguity)

### 5. Orchestrate from coordinator (your Mac)

Use `ssh machine-cmd "command"` to launch remote jobs:

```bash
#!/bin/bash
# parallel-copy.sh — coordinator

FILE_LIST="/tmp/missing-objects.txt"
MACHINES=("local" "nano1" "nano2")
NUM=${#MACHINES[@]}

i=0
while IFS= read -r key; do
  MACH="${MACHINES[$((i % NUM))]}"

  case "$MACH" in
    local)
      nohup bash -c "copy_one_file '$key'" >/dev/null 2>&1 &
      ;;
    nano1|nano2)
      ssh "${MACH}-cmd" "copy_one_file '$key'" >/dev/null 2>&1 &
      ;;
  esac

  i=$((i + 1))
  sleep 0.1  # stagger starts
done < "$FILE_LIST"

echo "Dispatched $i jobs across $NUM machines"
echo "Monitor: tail -f /tmp/parallel-copy.log"
```

Each remote machine must have the `copy_one_file` function or script available.

### 6. Progress monitoring

Collect status from all machines:

```bash
# Each job appends to shared log (NFS or scp back)
echo "OK $key" >> /tmp/parallel-copy.log  # on each machine

# Coordinator fetches and aggregates
for mach in "${MACHINES[@]}"; do
  if [ "$mach" = "local" ]; then
    cat /tmp/parallel-copy.log 2>/dev/null
  else
    ssh "${mach}-cmd" "cat /tmp/parallel-copy.log 2>/dev/null" 2>/dev/null
  fi
done | sort | uniq -c
```

### 7. Verification

After all jobs finish:

```bash
~/workspace/nano2/scripts/aws/verify-copy.sh
```

Should show source count == dest count.

## Script Reference

**`parallel-copy-all-machines.sh`** — coordinator that:
- Computes missing files automatically
- Distributes round-robin across `local`, `nano1`, `nano2`
- Launches each copy in background
- Logs to `/tmp/parallel-copy.log`
- Provides monitoring commands

Assumptions:
- All machines have `~/.aws/credentials` with `runpod-s3` and `default` profiles
- SSH shortcuts `nano1-cmd`, `nano2-cmd` exist and work
- `/mnt/bigdata` or equivalent has enough space on each machine

## Pitfalls

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| Too many concurrent streams | Router saturates, all transfers slow down | Limit to 2–3 machines at once, or throttle per-machine bandwidth (`--expected-size` or `pv` limiter) |
| One machine fails mid-run | That machine's files missing in dest | Re-run just that machine's subset (use `/tmp/work-queue-$MACHINE.txt`) |
| Disk fills on remote machine | Transfer crashes, partial left | Pre-check free space; use partition-detection function |
| SSH connection drops | Remote job orphaned | Use `nohup` or `screen`/`tmux` on remote; log to file |
| Clock skew on one machine | `SignatureDoesNotMatch` only on that machine | Sync time: `sudo ntpdate -s time.apple.com` |

## When to Use vs. Alternatives

| Scenario | Best approach |
|----------|---------------|
| Server-side pod available | Use `copy-runpod-volume-to-aws.sh` (server-side, zero local bandwidth) |
| Server-side blocked + 2+ LAN machines | Use `parallel-copy-all-machines.sh` (parallelizes across LAN) |
| Server-side blocked + single machine | Use `copy-isaac-sim-6-tar.sh` (download→upload with resume) |
| Single file <1 GB | Direct stream: `aws s3 cp src - | aws s3 cp - dst` |

## Related Skills

- `s3-server-side-copy` — Primary pattern (pod/VM inside source network)
- `s3-resilient-transfer` — Handling individual file failures, retries, and verification

## References

- `aws s3 cp` vs `aws s3 sync` — `cp` is simpler for individual files; `sync` is bulk
- `rclone copy` with `--transfers N` — alternative to manual parallelization
- Asynchronous transfer patterns (Celery, Airflow) for large-scale migrations

