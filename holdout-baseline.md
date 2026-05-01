# Holdout Baseline — Pre-Evolution Scores

| # | Tier | Tool | Composite | Clarity | Coverage | Resilience | Self-Cont | Verifiable | Length |
|---|---|---|---|---|---|---|---|---|---|
| 6 | 1 | delete_container | 0.325 | 0.700 | 0.000 | 0.000 | 1.000 | 0.000 | 69 |
| 7 | 1 | list_containers | 0.325 | 0.700 | 0.000 | 0.000 | 1.000 | 0.000 | 90 |
| 9 | 1 | inspect_container | 0.400 | 1.000 | 0.000 | 0.000 | 1.000 | 0.000 | 110 |
| 48 | 2 | check_action | 0.325 | 0.400 | 0.000 | 0.000 | 1.000 | 0.500 | 206 |
| 56 | 2 | get_ltl_state, analyze_traces | 0.295 | 0.400 | 0.000 | 0.000 | 1.000 | 0.300 | 328 |
| 87 | 3 | ingest_trace | 0.400 | 0.700 | 0.000 | 0.000 | 1.000 | 0.500 | 294 |

## Prompt 6 — delete_container

```
Delete the container named "test-dev". Force delete if it is running.
```

Score details: {
  "clarity": 0.7,
  "coverage": 0.0,
  "resilience": 0.0,
  "self_containment": 1.0,
  "verifiability": 0.0
}

Composite: 0.3250

## Prompt 7 — list_containers

```
List all containers, including stopped ones. Show me their names, IDs, images, and status.
```

Score details: {
  "clarity": 0.7,
  "coverage": 0.0,
  "resilience": 0.0,
  "self_containment": 1.0,
  "verifiability": 0.0
}

Composite: 0.3250

## Prompt 9 — inspect_container

```
Inspect the container named "test-nginx". Show me its full configuration, network settings, and current state.
```

Score details: {
  "clarity": 1.0,
  "coverage": 0.0,
  "resilience": 0.0,
  "self_containment": 1.0,
  "verifiability": 0.0
}

Composite: 0.4000

## Prompt 48 — check_action

```
Attempt to create a container with "role: FleetAgent" but override the volume mount to request 'ReadWrite' access. 
Verify that the Pkl-evaluator rejects the configuration before the VM boot process starts.
```

Score details: {
  "clarity": 0.4,
  "coverage": 0.0,
  "resilience": 0.0,
  "self_containment": 1.0,
  "verifiability": 0.5
}

Composite: 0.3250

## Prompt 56 — get_ltl_state, analyze_traces

```
Manually inject a "Red" state violation into the LTL Runtime Monitor (e.g., attempt to Shutdown an Active container without completing a clean stop). 
Trigger the OptimizerAgent to:
1. Analyze the trace data.
2. Propose a "Mutation Recipe" to return the state machine to Green.
3. Apply the Pkl mutation and re-verify the state.
```

Score details: {
  "clarity": 0.4,
  "coverage": 0.0,
  "resilience": 0.0,
  "self_containment": 1.0,
  "verifiability": 0.3
}

Composite: 0.2950

## Prompt 87 — ingest_trace

```
Execute the "clip-vit" model 100 times consecutively using rapid slab recycling.
Track mlx.core.metal GPU memory usage before, during, and after the test.
Verify that GPU memory plateaus rather than growing linearly — this is a baseline test; record the plateau value for regression comparison.
```

Score details: {
  "clarity": 0.7,
  "coverage": 0.0,
  "resilience": 0.0,
  "self_containment": 1.0,
  "verifiability": 0.5
}

Composite: 0.4000
