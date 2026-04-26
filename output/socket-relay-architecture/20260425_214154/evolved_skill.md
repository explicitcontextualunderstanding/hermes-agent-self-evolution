---

---



# === SKILL.md ===

# Skill: socket-relay-architecture

## Overview

Bypass Apple Container's `vmnet` TCP routing bug using Unix Domain Sockets (UDS) and host-mediated socat relays. This architecture enables container-to-container and container-to-LAN communication without relying on the broken `192.168.64.1` gateway.

## When to Use

- Container-to-container TCP communication on Apple Container 0.11.0
- Container-to-LAN access (Jetsons, external services)
- Database connectivity from application containers
- Hub/Deriver/Code Graph MCP stack deployment

## Architecture

### The Problem

Apple Container 0.11.0's `bridge100` network has a fundamental routing bug:

- Container → `192.168.64.1` packets are routed to WiFi (`en0`), not the host
- Container-to-container TCP is impossible via the virtual bridge
- Route injection (`route add -net 192.168.64.0/24`) fails

### The Solution

Use the host as an L4 switch:

1. **Containers CAN reach host's WiFi IP** (`192.168.1.118`) via standard NAT
2. **Host runs socat relays** that forward TCP to Unix sockets
3. **Unix sockets published into containers** via `--publish-socket`

```
Hub Container ──TCP──▶ 192.168.1.118:5433
                              │
                        Host Socat
                              │
                        UNIX-CONNECT
                              │
                         /tmp/honcho-pg.sock
                              │
                        DB Container PostgreSQL
```

## Components

### 1. discover-host-ip.sh

Get active WiFi/LAN IP for container networking.

```bash
# Usage
./discover-host-ip.sh            # Plain: 192.168.1.118
./discover-host-ip.sh --json     # JSON: {"interface":"en0","ip":"192.168.1.118"}
```

### 2. socat-relay-manager.sh

Manage socat relay lifecycle with PID tracking.

```bash
# Commands
./socat-relay-manager.sh start   # Start all relays
./socat-relay-manager.sh stop    # Stop and cleanup
./socat-relay-manager.sh restart # Restart relays
./socat-relay-manager.sh status  # Show status

# Relays managed
- db-relay:     192.168.1.118:5433 → UNIX:/tmp/honcho-pg.sock
- hub-ingress:  0.0.0.0:8000        → Hub container :8000
- nano1-forward: 192.168.1.118:6444 → 192.168.1.86:6443
- nano2-forward: 192.168.1.118:6445 → 192.168.1.81:6443
```

### 3. Compose File Configuration

```yaml
services:
  honcho-db:
    image: walg-db:latest
    command: postgres -c unix_socket_directories='/var/run/postgresql'
    # Socket published via: --publish-socket /tmp/honcho-pg.sock:/var/run/postgresql/.s.PGSQL.5432
    
  honcho-hub:
    image: honcho:latest
    environment:
      # Connect via host relay (not container IP)
      DATABASE_URL: postgresql://postgres@${HOST_LAN_IP}:5433/honcho
```

## Network Flow

| Source | Destination | Path | Mechanism |
|--------|-------------|------|-----------|
| Hub Container | PostgreSQL | `192.168.1.118:5433` → `/tmp/pg.sock` | TCP→UDS relay |
| Hermes Container | Hub API | `192.168.1.118:8000` → Hub | TCP ingress bridge |
| Code Graph MCP | Hub API | `192.168.1.118:8000` → Hub | TCP ingress bridge |
| Hub Container | nano1 K3s | `192.168.1.118:6444` → nano1:6443 | TCP egress bridge |

## Quick Start

### Start the Stack

```bash
cd ~/workspace/isaac_ros_custom

# 1. Get host LAN IP
export HOST_LAN_IP=$(./.appcontainer/scripts/discover-host-ip.sh)

# 2. Start socat relays
./.appcontainer/scripts/socat-relay-manager.sh start

# 3. Start containers
container-compose -f .appcontainer/docker-compose.honcho.yml up -d

# 4. Verify relays
./.appcontainer/scripts/socat-relay-manager.sh status
```

### Stop the Stack

```bash
# 1. Stop containers
container-compose -f .appcontainer/docker-compose.honcho.yml down

# 2. Stop relays
./.appcontainer/scripts/socat-relay-manager.sh stop
```

## Troubleshooting

### Container cannot reach host IP

```bash
# Verify host IP
./discover-host-ip.sh

# Test from container
container exec <container-name> nc -zv 192.168.1.118 5433
```

### Relay not starting

```bash
# Check if socket file exists
ls -la /tmp/honcho-*.sock

# Check if port is in use
lsof -i :5433

# Kill stale processes
./socat-relay-manager.sh stop
./socat-relay-manager.sh start
```

### PostgreSQL socket connection failed

```bash
# Verify socket is published
container exec <db-container> ls -la /var/run/postgresql/

# Check relay status
./socat-relay-manager.sh status

# Test relay manually
socat -d -d TCP:192.168.1.118:5433 UNIX-CONNECT:/tmp/honcho-pg.sock
```

## DNS Integration

Use `host.container.internal` DNS name instead of hardcoded IPs:

```bash
# Create DNS record (requires sudo)
sudo container system dns create host.container.internal --localhost 192.168.64.1

# Use in compose files
DATABASE_URL: postgresql://postgres@host.container.internal:5433/honcho
```

## Benefits

1. **Zero Host Dependencies**: No Homebrew PostgreSQL, no host-installed services
2. **100% Container Encapsulation**: Everything managed via container-compose
3. **Bypasses vmnet Bug**: No dependency on broken `192.168.64.1` routing
4. **Ephemeral Relays**: Socat processes managed as lifecycle-bound resources
5. **Socket Security**: Unix sockets provide better isolation than TCP

## Risks

1. **Socket Path Collisions**: `/tmp/honcho-pg.sock` must be unique per project
2. **Host IP Changes**: WiFi roaming changes `${HOST_LAN_IP}`, requires restart
3. **Socket Leaks**: Crash without cleanup leaves `/tmp/*.sock` files
4. **Performance**: UDS→TCP relay adds ~1-2ms latency vs direct TCP

## References

- Plan 70: Socket-Relay Architecture
- Plan 67: Apple Container 0.11.0 Features (DNS findings)
- Plan 68: Socat Architecture Cleanup
- Apple Container `--publish-socket` documentation
- Socat UNIX-CONNECT and TCP-LISTEN documentation

