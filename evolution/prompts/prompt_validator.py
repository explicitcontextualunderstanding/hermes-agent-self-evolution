#!/usr/bin/env python3
"""
prompt_validator.py — Quality gates for evolved prompts before write-back.

Detects four classes of prompt corruption that occur during GEPA evolution:

1. Prompt Overfitting Bloat — evolved text exceeds max_length
2. Reflective Append Syndrome — GEPA's own analysis text leaked into the prompt
3. Trace Leakage — raw MCP tool output, hermes session IDs, or JSON-RPC data
4. Heuristic Feedback Loops — dimension tables, "What changed" sections
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Reflective/GEPA analysis markers — text that should NEVER appear in a prompt ──
REFLECTIVE_PATTERNS = [
    r"⚠ Iteration budget reached",
    r"Key structural improvements",
    r"What changed and why",
    r"Dimension\s*\|\s*Before",
    r"Dimension\s*\|\s*Score",
    r"\|.*tool_correctness.*parameter_validity",
    r"\|.*resource_lifecycle.*state_agreement",
    r"Evaluated with ProxyStateTracker",
    r"improvement\)",
    r"GEPA Optimization",
    r"Rollout #\d+",
    r"hermes --resume \d{8}",
    r"mcp_hermes_agent_backend_",
    r"cmd: echo",
    r"New subsample score",
    r"Valset score for new program",
    r"Objective aggregate scores",
    r"Pareto front",
    r"Budget exhausted",
    r"iteration budget",
]

# ── Trace leakage patterns — hermes session artifacts ──
TRACE_PATTERNS = [
    r"hermes --resume \S+",
    r"SESSION_ID_RE",
    r"duration_ms",
    r"api_call_count",
    r"token_usage",
    r'"final_status"',
    r"response_text",
    r"full_assistant_response",
]

# ── Heuristic feedback loop patterns — scorer's own prose ──
HEURISTIC_PATTERNS = [
    r"Baseline score",
    r"Original length",
    r"Evolved length",
    r"composite_score",
    r"pass_score",
    r"efficiency_score",
    r"tool_efficiency",
    r"token_efficiency",
    r"CLARITY.*0\.\d",
    r"RESILIENCE.*0\.\d",
    r"SELF-CONTAINMENT.*0\.\d",
]


class PromptValidationError(Exception):
    """Raised when an evolved prompt fails validation."""
    pass


def validate_evolved_prompt(
    evolved_text: str,
    original_text: str = "",
    max_length: int = 1500,
    max_bloat_ratio: float = 3.0,
) -> dict:
    """Validate an evolved prompt against all quality gates.

    Args:
        evolved_text: The evolved prompt text to validate.
        original_text: The original prompt text (for bloat ratio check).
        max_length: Maximum allowed length in characters.
        max_bloat_ratio: Maximum allowed ratio of evolved/original length.

    Returns:
        dict with keys: passed (bool), failures (list of str),
                        warnings (list of str), metrics (dict)
    """
    failures = []
    warnings = []
    metrics = {
        "evolved_length": len(evolved_text),
        "original_length": len(original_text) if original_text else 0,
        "bloat_ratio": round(len(evolved_text) / max(1, len(original_text)), 2) if original_text else 0,
    }

    # ── Gate 1: Length cap ────────────────────────────────────────────
    if len(evolved_text) > max_length:
        failures.append(
            f"Prompt overfitting bloat: {len(evolved_text)} chars exceeds max {max_length} "
            f"(threshold: {max_length})"
        )

    # ── Gate 2: Bloat ratio ───────────────────────────────────────────
    if original_text and len(evolved_text) > len(original_text) * max_bloat_ratio:
        warnings.append(
            f"Bloat ratio {metrics['bloat_ratio']}x exceeds threshold {max_bloat_ratio}x "
            f"({len(evolved_text)} vs {len(original_text)} chars)"
        )

    # ── Gate 3: Reflective text detection ─────────────────────────────
    for pattern in REFLECTIVE_PATTERNS:
        m = re.search(pattern, evolved_text, re.IGNORECASE | re.MULTILINE)
        if m:
            # Extract a readable sample
            start = max(0, m.start() - 20)
            end = min(len(evolved_text), m.end() + 40)
            sample = evolved_text[start:end].replace("\n", " ").strip()
            failures.append(
                f"Reflective append syndrome: found '{m.group()[:40]}' "
                f"(context: ...{sample}...)"
            )
            break  # One failure per reflective pattern category

    # ── Gate 4: Trace leakage ─────────────────────────────────────────
    for pattern in TRACE_PATTERNS:
        m = re.search(pattern, evolved_text, re.IGNORECASE)
        if m:
            start = max(0, m.start() - 20)
            end = min(len(evolved_text), m.end() + 40)
            sample = evolved_text[start:end].replace("\n", " ").strip()
            failures.append(
                f"Trace leakage: found '{m.group()[:40]}' "
                f"(context: ...{sample}...)"
            )
            break

    # ── Gate 5: Heuristic feedback loops ──────────────────────────────
    for pattern in HEURISTIC_PATTERNS:
        m = re.search(pattern, evolved_text, re.IGNORECASE)
        if m:
            start = max(0, m.start() - 20)
            end = min(len(evolved_text), m.end() + 40)
            sample = evolved_text[start:end].replace("\n", " ").strip()
            warnings.append(
                f"Heuristic feedback leakage: found '{m.group()[:40]}' "
                f"(context: ...{sample}...)"
            )
            break

    # ── Gate 6: Code fence integrity ──────────────────────────────────
    # The prompt should be inside a ``` block. If the evolved text
    # itself contains markdown headers, it's likely corrupted.
    if re.search(r"^### ", evolved_text, re.MULTILINE):
        failures.append(
            "Structural integrity failure: evolved text contains markdown headers"
        )

    # Check that the evolved text doesn't contain its own outer ``` markers
    # (the write-back code handles outer fences; the evolved text should
    # be just the inner content)
    if "```" in evolved_text.strip():
        warnings.append(
            "Evolved text contains code fence markers — may nest incorrectly"
        )

    return {
        "passed": len(failures) == 0,
        "failures": failures,
        "warnings": warnings,
        "metrics": metrics,
    }


def strip_reflective_sections(text: str) -> str:
    """Attempt to strip reflective/GEPA analysis sections from evolved text.

    This is a recovery mechanism — if the evolved text includes analysis
    sections appended to the prompt body, this function tries to extract
    just the prompt body. Returns the stripped text, or the original if
    no recovery was possible.
    """
    # Look for reflective section markers
    markers = [
        r"\nKey structural improvements.*",
        r"\nWhat changed and why.*",
        r"\nEvaluated with ProxyStateTracker.*",
        r"\n⚠ Iteration budget reached.*",
    ]
    best = text
    for marker in markers:
        m = re.search(marker, best, re.DOTALL)
        if m:
            candidate = best[:m.start()].strip()
            if len(candidate) > 50:  # Must have meaningful content
                best = candidate
    return best if best != text else text


def safe_write_evolved(
    evolved_text: str,
    original_text: str,
    prompt_num: int,
    max_length: int = 1500,
    max_bloat_ratio: float = 3.0,
    auto_strip: bool = True,
) -> tuple[bool, str]:
    """Write-or-reject evolved prompt text.

    Returns:
        (accepted, sanitized_text_or_error_message)
    """
    # Try stripping reflective sections first if auto_strip is enabled
    if auto_strip:
        cleaned = strip_reflective_sections(evolved_text)
    else:
        cleaned = evolved_text

    result = validate_evolved_prompt(
        cleaned, original_text,
        max_length=max_length,
        max_bloat_ratio=max_bloat_ratio,
    )

    if result["passed"]:
        return True, cleaned

    # Build error message
    msg = f"Prompt #{prompt_num} rejected: "
    msg += "; ".join(result["failures"])
    if result["warnings"]:
        msg += " [warnings: " + "; ".join(result["warnings"]) + "]"
    return False, msg
