"""Wraps a SKILL.md file as a DSPy module for optimization.

The key abstraction: a skill file becomes a parameterized DSPy module
where the skill text is the optimizable parameter. GEPA can then
mutate the skill text and evaluate the results.
"""

import hashlib
import re
from pathlib import Path
from typing import Optional

import dspy

from evolution.core.lm_tracker import LM_TRACKER

# ── Forward cache (mitigation: cache forward() by skill_text + task_input) ────
# GEPA may re-evaluate the same candidate multiple times (subsample → full eval).
# Caches the last 500 results keyed by (skill_text_hash, task_input).
# Cleared between GEPA compile() calls via clear_forward_cache().
_forward_cache: dict = {}
_FORWARD_CACHE_MAX = 500


def clear_forward_cache():
    """Call between GEPA compile() invocations to free memory."""
    _forward_cache.clear()


def _cache_key(skill_text: str, task_input: str) -> str:
    """Deterministic key for caching forward() results."""
    h = hashlib.sha256(skill_text.encode()).hexdigest()[:16]
    return f"{h}:{task_input}"


def load_skill(skill_path: Path) -> dict:
    """Load a skill directory and concatenate all .md files.

    Reads SKILL.md first, then all other .md files in the same directory,
    ordered by filename for determinism. Returns the combined corpus.

    Returns:
        {
            "path": Path,
            "raw": str (full file content),
            "frontmatter": str (YAML between --- markers),
            "body": str (markdown after frontmatter),
            "name": str,
            "description": str,
        }
    """
    skill_dir = skill_path.parent
    skill_files = sorted(skill_dir.glob("*.md"))

    # Ensure SKILL.md comes first if it exists
    if skill_path in skill_files:
        skill_files.remove(skill_path)
        skill_files.insert(0, skill_path)

    raw_parts = []
    for f in skill_files:
        raw_parts.append(f"\n\n# === {f.name} ===\n\n")
        raw_parts.append(f.read_text())
    raw = "".join(raw_parts)

    # Parse YAML frontmatter from SKILL.md only
    frontmatter = ""
    body = raw
    skill_raw = skill_path.read_text()
    if skill_raw.strip().startswith("---"):
        parts = skill_raw.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1].strip()

    # Extract name and description from frontmatter
    name = ""
    description = ""
    for line in frontmatter.split("\n"):
        if line.strip().startswith("name:"):
            name = line.split(":", 1)[1].strip().strip("'\"")
        elif line.strip().startswith("description:"):
            description = line.split(":", 1)[1].strip().strip("'\"")

    return {
        "path": skill_path,
        "raw": raw,
        "frontmatter": frontmatter,
        "body": raw,  # full corpus is the optimizable body
        "name": name,
        "description": description,
    }


def find_skill(skill_name: str, hermes_agent_path: Path) -> Optional[Path]:
    """Find a skill by name in the hermes-agent skills directory.

    Searches recursively for a SKILL.md in a directory matching the skill name.
    """
    skills_dir = hermes_agent_path / "skills"
    if not skills_dir.exists():
        return None

    # Direct match: skills/<category>/<skill_name>/SKILL.md
    for skill_md in skills_dir.rglob("SKILL.md"):
        if skill_md.parent.name == skill_name:
            return skill_md

    # Fuzzy match: check the name field in frontmatter
    for skill_md in skills_dir.rglob("SKILL.md"):
        try:
            content = skill_md.read_text()[:500]
            if f"name: {skill_name}" in content or f'name: "{skill_name}"' in content:
                return skill_md
        except Exception:
            continue

    return None


class SkillModule(dspy.Module):
    """A DSPy module that wraps a skill file for optimization.

    The skill text (body) is the parameter that GEPA optimizes.
    On each forward pass, the module:
    1. Uses the skill text as instructions
    2. Processes the task input
    3. Returns the agent's response

    When short_circuit=True (default), forward() returns the skill text
    directly without calling the LLM. This eliminates the n² evaluation
    explosion. The metric function scores the skill text against the
    expected rubric via heuristic + LLMJudge — a valid but less rich signal.

    When short_circuit=False, forward() calls dspy.Predict() which sends
    the full skill text to the LLM on every evaluation (original behavior).
    """

    # Module-level flag: when True, forward() skips the LLM call.
    # Set via SkillModule.short_circuit = True before compile().
    short_circuit: bool = True

    class TaskWithSkill(dspy.Signature):
        """Complete a task following the provided skill instructions.

        You are an AI agent following specific skill instructions to complete a task.
        Read the skill instructions carefully and follow the procedure described.
        """
        skill_instructions: str = dspy.InputField(desc="The skill instructions to follow")
        task_input: str = dspy.InputField(desc="The task to complete")
        output: str = dspy.OutputField(desc="Your response following the skill instructions")

    def __init__(self, skill_text: str):
        super().__init__()
        self.skill_text = skill_text
        self.predictor = dspy.Predict(self.TaskWithSkill)

    def forward(self, task_input: str, _iteration: Optional[int] = None,
                _candidate: Optional[int] = None, _example: Optional[int] = None) -> dspy.Prediction:
        # ── Cache check (mitigation: avoid redundant forward calls) ─────────
        key = _cache_key(self.skill_text, task_input)
        cached = _forward_cache.get(key)
        if cached is not None:
            return cached

        # ── Short-circuit mode (mitigation: eliminate n² inner loop) ────────
        if self.short_circuit:
            output = (
                f"Follow these skill instructions to complete the task:\n\n"
                f"{self.skill_text}\n\n"
                f"---\nTask: {task_input}\n---\n"
                f"Based on the skill above, here is my response:"
            )
            result = dspy.Prediction(output=output)
            # Cache and return — no LM call, no tracking
            if len(_forward_cache) < _FORWARD_CACHE_MAX:
                _forward_cache[key] = result
            return result

        # ── Full LLM evaluation path (original behavior) ────────────────────
        # Track this LM call — it's the n² inner loop
        model_name = ""
        try:
            import dspy
            if hasattr(dspy.settings, "lm") and dspy.settings.lm is not None:
                model_name = str(dspy.settings.lm.model or "")
        except Exception:
            pass
        LM_TRACKER.record(
            site="skill_module.forward",
            model=model_name,
            input_chars=len(self.skill_text) + len(task_input),
            iteration=_iteration,
            candidate=_candidate,
            example=_example,
        )
        result = self.predictor(
            skill_instructions=self.skill_text,
            task_input=task_input,
        )
        return dspy.Prediction(output=result.output)


def reassemble_skill(frontmatter: str, evolved_body: str) -> str:
    """Reassemble a skill file from frontmatter and evolved body.

    Preserves the original YAML frontmatter (name, description, metadata)
    and replaces only the body with the evolved version.
    """
    return f"---\n{frontmatter}\n---\n\n{evolved_body}\n"
