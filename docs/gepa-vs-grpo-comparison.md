# Cross-Pipeline Comparison: GEPA vs GRPO

## Decision Matrix

| Criterion | GEPA (Prompt Evolution) | GRPO (Texture Bridge) | Winner |
|-----------|-------------------------|----------------------|--------|
| **Domain** | Text prompts for MCP tool descriptions | Pixel-level texture optimization | N/A (different domains) |
| **Δ per iteration** | +0.375 per prompt (best) | +0.001 per gen (trend) | GEPA |
| **Cost per iteration** | $0.04 | $0.08 | GEPA |
| **Δ per dollar** | 9.4/prompt | 0.013/gen | GEPA (720×) |
| **Convergence** | 24% non-zero rate (new stack) | 0% edit ratio (flat at gen 3-5) | GEPA |
| **GPU required** | No | Yes (DINOv2, CLIP, pyrender) | GEPA |
| **API dependency** | Tinker (prompt eval via hermes) | Tinker (K2.6 generation) | Same |
| **Infrastructure surface** | Kanban drum + Hermes sessions | Bridge daemon + SD3 + pyrender | GEPA (simpler) |

## Decision Rules

### Rule 1: Domain determines pipeline
- **Text optimization** → GEPA. Always. GRPO has no mechanism to optimize text prompts.
- **Pixel/texture optimization** → GRPO. Always. GEPA has no mechanism to generate or evaluate pixel data.
- **Both needed?** → Run GEPA first (text), then GRPO (pixels). Never in parallel — orthogonal domains.

### Rule 2: Switch to GRPO from GEPA only if
- GEPA produces <5% non-zero rate for 3 consecutive tiers
- AND the problem involves pixel-level outcomes (text optimization can't reach)
- AND budget for Tinker API calls ($0.08/gen) is separately allocated

### Rule 3: Stop GRPO when
- 3 consecutive gens with identity_factor delta < 0.001 (converged — empirical finding)
- 50 gens completed without exceeding SDK baseline identity of 0.3984
- Budget exhausted (default $4.00 for 50 gens, max $8.00)

## Implementation Path

No code changes needed. The two pipelines already coexist in `compose-pkl/scripts/` with no shared infrastructure conflicts (GEPA uses `evolve_prompts.py`, GRPO uses `k2.6_texture_bridge.py`). The decision matrix serves as the dispatch rule for which task type goes to which pipeline.

## Verification

When a new prompt evolution task arrives:
1. Subject: MCP tool description text? → GEPA (kanban task with `--skill prompt-evolution-pipeline`)
2. Subject: 3D model texture? → GRPO (kanban task with `--skill prompt-evolution-pipeline --bg`)
3. Subject: both? → Two sequential kanban tasks, GEPA first
