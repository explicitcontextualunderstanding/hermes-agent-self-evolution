"""Evolve a Hermes Agent skill using DSPy + GEPA.

Usage:
    python -m evolution.skills.evolve_skill --skill github-code-review --iterations 10
    python -m evolution.skills.evolve_skill --skill arxiv --eval-source golden --dataset datasets/skills/arxiv/
"""

import json
import logging
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

import click
import dspy
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from evolution.core.config import EvolutionConfig, get_hermes_agent_path
from evolution.core.dataset_builder import SyntheticDatasetBuilder, EvalDataset, GoldenDatasetLoader
from evolution.core.external_importers import build_dataset_from_external
from evolution.core.fitness import skill_fitness_metric, LLMJudge, FitnessScore, configure_sub_sampling
from evolution.core.constraints import ConstraintValidator
from evolution.skills.skill_module import (
    SkillModule,
    load_skill,
    find_skill,
    reassemble_skill,
)

console = Console()


class ProgressLogger:
    """Writes structured JSONL checkpoints so a run can be monitored and resumed."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self._start = time.time()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, record: dict):
        if not self.path:
            return
        record["_ts"] = datetime.utcnow().isoformat() + "Z"
        record["_elapsed"] = round(time.time() - self._start, 2)
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def log(self, phase: str, data: dict):
        self._write({"phase": phase, **data})


class MiproCheckpointHandler(logging.Handler):
    """Intercepts MIPROv2 log lines and writes trial scores to a JSONL progress file."""

    def __init__(self, progress: ProgressLogger):
        super().__init__()
        self.progress = progress
        self.scores = []
        self.best_score = None

    def emit(self, record):
        msg = record.getMessage()
        # Catch: "Score: 51.57 with parameters ['Predictor 0: Instruction 2', ...]"
        if "Score:" in msg and "with parameters" in msg:
            try:
                score_part = msg.split("Score:")[1].split("with parameters")[0].strip()
                score = float(score_part)
                self.scores.append(score)
                if self.best_score is None or score > self.best_score:
                    self.best_score = score
                params = msg.split("with parameters")[1].strip()
                self.progress.log(
                    "trial",
                    {
                        "trial": len(self.scores),
                        "score": score,
                        "best_score": self.best_score,
                        "params": params,
                    },
                )
            except Exception:
                pass
        # Catch: "Best score so far: 51.57"
        elif "Best score so far:" in msg:
            try:
                best = float(msg.split("Best score so far:")[1].strip())
                self.progress.log("best_score_update", {"best_score": best})
            except Exception:
                pass


def evolve(
    skill_name: str,
    iterations: int = 10,
    eval_source: str = "synthetic",
    dataset_path: Optional[str] = None,
    optimizer_model: str = "openai/gpt-4o-mini",
    eval_model: str = "openai/gpt-4o-mini",
    hermes_repo: Optional[str] = None,
    skill_path: Optional[str] = None,
    run_tests: bool = False,
    dry_run: bool = False,
    num_trials: int = 20,
    num_candidates: int = 10,
    num_threads: int = 5,
    auto_mode: Optional[str] = None,
    progress_file: Optional[str] = None,
):
    """Main evolution function — orchestrates the full optimization loop."""

    config = EvolutionConfig(
        iterations=iterations,
        optimizer_model=optimizer_model,
        eval_model=eval_model,
        judge_model=eval_model,  # Use same model for dataset generation
        run_pytest=run_tests,
    )
    if hermes_repo:
        config.hermes_agent_path = Path(hermes_repo)

    progress = ProgressLogger(Path(progress_file) if progress_file else None)
    progress.log("start", {"skill": skill_name, "model": eval_model, "auto_mode": auto_mode})

    # ── 1. Find and load the skill ──────────────────────────────────────
    console.print(f"\n[bold cyan]🧬 Hermes Agent Self-Evolution[/bold cyan] — Evolving skill: [bold]{skill_name}[/bold]\n")

    if skill_path:
        skill_path_obj = Path(skill_path).expanduser()
        if not skill_path_obj.exists():
            console.print(f"[red]✗ Skill path '{skill_path}' not found[/red]")
            sys.exit(1)
    else:
        skill_path_obj = find_skill(skill_name, config.hermes_agent_path)
        if not skill_path_obj:
            console.print(f"[red]✗ Skill '{skill_name}' not found in {config.hermes_agent_path / 'skills'}[/red]")
            sys.exit(1)
    skill_path = skill_path_obj

    skill = load_skill(skill_path)
    try:
        console.print(f"  Loaded: {skill_path.relative_to(config.hermes_agent_path)}")
    except ValueError:
        console.print(f"  Loaded: {skill_path}")
    console.print(f"  Name: {skill['name']}")
    console.print(f"  Size: {len(skill['raw']):,} chars")
    console.print(f"  Description: {skill['description'][:80]}...")

    progress.log("skill_loaded", {"name": skill['name'], "size": len(skill['raw'])})

    if dry_run:
        console.print(f"\n[bold green]DRY RUN — setup validated successfully.[/bold green]")
        console.print(f"  Would generate eval dataset (source: {eval_source})")
        console.print(f"  Would run GEPA optimization ({iterations} iterations)")
        console.print(f"  Would validate constraints and create PR")
        return

    # ── 2. Build or load evaluation dataset ─────────────────────────────
    console.print(f"\n[bold]Building evaluation dataset[/bold] (source: {eval_source})")

    if eval_source == "golden" and dataset_path:
        dataset = GoldenDatasetLoader.load(Path(dataset_path))
        console.print(f"  Loaded golden dataset: {len(dataset.all_examples)} examples")
    elif eval_source == "sessiondb":
        save_path = Path(dataset_path) if dataset_path else Path("datasets") / "skills" / skill_name
        dataset = build_dataset_from_external(
            skill_name=skill_name,
            skill_text=skill["raw"],
            sources=["claude-code", "copilot", "hermes"],
            output_path=save_path,
            model=eval_model,
        )
        if not dataset.all_examples:
            console.print("[red]✗ No relevant examples found from session history[/red]")
            sys.exit(1)
        console.print(f"  Mined {len(dataset.all_examples)} examples from session history")
    elif eval_source == "synthetic":
        builder = SyntheticDatasetBuilder(config)
        dataset = builder.generate(
            artifact_text=skill["raw"],
            artifact_type="skill",
        )
        # Save for reuse
        save_path = Path("datasets") / "skills" / skill_name
        dataset.save(save_path)
        console.print(f"  Generated {len(dataset.all_examples)} synthetic examples")
        console.print(f"  Saved to {save_path}/")
    elif dataset_path:
        dataset = EvalDataset.load(Path(dataset_path))
        console.print(f"  Loaded dataset: {len(dataset.all_examples)} examples")
    else:
        console.print("[red]✗ Specify --dataset-path or use --eval-source synthetic[/red]")
        sys.exit(1)

    console.print(f"  Split: {len(dataset.train)} train / {len(dataset.val)} val / {len(dataset.holdout)} holdout")
    progress.log("dataset_ready", {"train": len(dataset.train), "val": len(dataset.val), "holdout": len(dataset.holdout)})

    # ── 3. Validate constraints on baseline ─────────────────────────────
    console.print(f"\n[bold]Validating baseline constraints[/bold]")
    validator = ConstraintValidator(config)
    baseline_constraints = validator.validate_all(skill["body"], "skill")
    all_pass = True
    for c in baseline_constraints:
        icon = "✓" if c.passed else "✗"
        color = "green" if c.passed else "red"
        console.print(f"  [{color}]{icon} {c.constraint_name}[/{color}]: {c.message}")
        if not c.passed:
            all_pass = False

    progress.log("constraints_checked", {"passed": all_pass, "checks": [{"name": c.constraint_name, "passed": c.passed} for c in baseline_constraints]})

    if not all_pass:
        console.print("[yellow]⚠ Baseline skill has constraint violations — proceeding anyway[/yellow]")

    # ── 4. Set up DSPy + GEPA optimizer ─────────────────────────────────
    console.print(f"\n[bold]Configuring optimizer[/bold]")
    console.print(f"  Optimizer: GEPA ({iterations} iterations)")
    console.print(f"  Optimizer model: {optimizer_model}")
    console.print(f"  Eval model: {eval_model}")

    # Configure sub-sampled LLM-as-judge for the fitness function
    # Uses heuristic gating (0.4-0.7 uncertainty zone) + 10% random sampling
    # to reduce LLMJudge calls while preserving gradient for GEPA
    configure_sub_sampling(
        enabled=True,
        sample_rate=0.10,
        uncertainty_min=0.4,
        uncertainty_max=0.7,
    )
    console.print(f"  Judge: sub-sampled (10% in 0.4-0.7 uncertainty zone)")

    # Configure DSPy
    lm = dspy.LM(model=eval_model, temperature=1.0, max_tokens=32000)
    dspy.configure(lm=lm)

    # Create the baseline skill module
    baseline_module = SkillModule(skill["body"])

    # Prepare DSPy examples
    skill_body = skill["body"]
    trainset = dataset.to_dspy_examples("train", skill_text=skill_body)
    valset = dataset.to_dspy_examples("val", skill_text=skill_body)

    # ── 5. Run GEPA optimization ────────────────────────────────────────
    console.print(f"\n[bold cyan]Running GEPA optimization ({iterations} iterations)...[/bold cyan]\n")

    # Wire up MIPROv2 trial capture to progress logger
    mipro_handler = MiproCheckpointHandler(progress)
    mipro_handler.setLevel(logging.INFO)
    logging.getLogger("dspy.teleprompt.mipro_optimizer_v2").addHandler(mipro_handler)

    start_time = time.time()
    progress.log("optimization_start", {"trials_planned": num_trials if not auto_mode else 10})

    try:
        # reflection_lm: strong model for GEPA's reflection phase
        reflection_lm = dspy.LM(
            model=eval_model,
            temperature=1.0,
            max_tokens=32000,
        )
        optimizer = dspy.GEPA(
            metric=skill_fitness_metric,
            max_full_evals=iterations,
            reflection_lm=reflection_lm,
        )

        optimized_module = optimizer.compile(
            baseline_module,
            trainset=trainset,
            valset=valset,
        )
    except Exception as e:
        # Fall back to MIPROv2 if GEPA isn't available in this DSPy version
        console.print(f"[yellow]GEPA not available ({e}), falling back to MIPROv2[/yellow]")
        if auto_mode:
            optimizer = dspy.MIPROv2(
                metric=skill_fitness_metric,
                auto=auto_mode,
            )
            optimized_module = optimizer.compile(
                baseline_module,
                trainset=trainset,
                valset=valset,
                requires_permission_to_run=False,
            )
        else:
            optimizer = dspy.MIPROv2(
                metric=skill_fitness_metric,
                auto=None,
                num_candidates=num_candidates,
                num_threads=num_threads,
            )
            optimized_module = optimizer.compile(
                baseline_module,
                trainset=trainset,
                valset=valset,
                num_trials=num_trials,
                minibatch=False,
                requires_permission_to_run=False,
            )

    elapsed = time.time() - start_time
    console.print(f"\n  Optimization completed in {elapsed:.1f}s")
    progress.log("optimization_complete", {"elapsed": elapsed, "trials_completed": len(mipro_handler.scores), "best_score": mipro_handler.best_score})

    # ── 6. Extract evolved skill text ───────────────────────────────────
    # The optimized module's instructions contain the evolved skill text
    evolved_body = optimized_module.skill_text
    evolved_full = reassemble_skill(skill["frontmatter"], evolved_body)

    # Save the optimized prompt wrapper (instruction + few-shot) for inspection
    wrapper_path = Path("output") / skill_name / "evolved_prompt_wrapper.json"
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        predictor = optimized_module.predictor
        wrapper_data = {
            "signature_doc": predictor.signature.__doc__,
            "signature_instructions": getattr(predictor.signature, 'instructions', None),
            "demos": [],
        }
        if hasattr(predictor, 'demos') and predictor.demos:
            for demo in predictor.demos:
                wrapper_data["demos"].append({
                    "skill_instructions": getattr(demo, 'skill_instructions', '')[:200] + "...",
                    "task_input": getattr(demo, 'task_input', '')[:200] + "...",
                    "output": getattr(demo, 'output', '')[:200] + "...",
                })
        wrapper_path.write_text(json.dumps(wrapper_data, indent=2))
        console.print(f"  Saved prompt wrapper to {wrapper_path}")
    except Exception as e:
        console.print(f"[yellow]Could not extract prompt wrapper: {e}[/yellow]")

    # ── 7. Validate evolved skill ───────────────────────────────────────
    console.print(f"\n[bold]Validating evolved skill[/bold]")
    evolved_constraints = validator.validate_all(evolved_body, "skill", baseline_text=skill["body"])
    all_pass = True
    for c in evolved_constraints:
        icon = "✓" if c.passed else "✗"
        color = "green" if c.passed else "red"
        console.print(f"  [{color}]{icon} {c.constraint_name}[/{color}]: {c.message}")
        if not c.passed:
            all_pass = False

    progress.log("evolved_constraints", {"passed": all_pass})

    if not all_pass:
        console.print("[red]✗ Evolved skill FAILED constraints — not deploying[/red]")
        # Still save for inspection
        output_path = Path("output") / skill_name / "evolved_FAILED.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(evolved_full)
        console.print(f"  Saved failed variant to {output_path}")
        progress.log("failed", {"reason": "constraints"})
        return

    # ── 8. Evaluate on holdout set ──────────────────────────────────────
    console.print(f"\n[bold]Evaluating on holdout set ({len(dataset.holdout)} examples)[/bold]")

    holdout_examples = dataset.to_dspy_examples("holdout", skill_text=skill_body)

    baseline_scores = []
    evolved_scores = []
    for ex in holdout_examples:
        # Score baseline
        with dspy.context(lm=lm):
            baseline_pred = baseline_module(task_input=ex.task_input)
            baseline_score = skill_fitness_metric(ex, baseline_pred)
            baseline_scores.append(baseline_score)

            evolved_pred = optimized_module(task_input=ex.task_input)
            evolved_score = skill_fitness_metric(ex, evolved_pred)
            evolved_scores.append(evolved_score)

    avg_baseline = sum(baseline_scores) / max(1, len(baseline_scores))
    avg_evolved = sum(evolved_scores) / max(1, len(evolved_scores))
    improvement = avg_evolved - avg_baseline
    progress.log("holdout", {"baseline": avg_baseline, "evolved": avg_evolved, "improvement": improvement})

    # ── 9. Report results ───────────────────────────────────────────────
    table = Table(title="Evolution Results")
    table.add_column("Metric", style="bold")
    table.add_column("Baseline", justify="right")
    table.add_column("Evolved", justify="right")
    table.add_column("Change", justify="right")

    change_color = "green" if improvement > 0 else "red"
    table.add_row(
        "Holdout Score",
        f"{avg_baseline:.3f}",
        f"{avg_evolved:.3f}",
        f"[{change_color}]{improvement:+.3f}[/{change_color}]",
    )
    table.add_row(
        "Skill Size",
        f"{len(skill['body']):,} chars",
        f"{len(evolved_body):,} chars",
        f"{len(evolved_body) - len(skill['body']):+,} chars",
    )
    table.add_row("Time", "", f"{elapsed:.1f}s", "")
    table.add_row("Iterations", "", str(iterations), "")

    console.print()
    console.print(table)

    # ── 10. Save output ─────────────────────────────────────────────────
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path("output") / skill_name / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save evolved skill
    (output_dir / "evolved_skill.md").write_text(evolved_full)

    # Save baseline for comparison
    (output_dir / "baseline_skill.md").write_text(skill["raw"])

    # Save metrics
    metrics = {
        "skill_name": skill_name,
        "timestamp": timestamp,
        "iterations": iterations,
        "optimizer_model": optimizer_model,
        "eval_model": eval_model,
        "baseline_score": avg_baseline,
        "evolved_score": avg_evolved,
        "improvement": improvement,
        "baseline_size": len(skill["body"]),
        "evolved_size": len(evolved_body),
        "train_examples": len(dataset.train),
        "val_examples": len(dataset.val),
        "holdout_examples": len(dataset.holdout),
        "elapsed_seconds": elapsed,
        "constraints_passed": all_pass,
    }
    (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))

    # Also write progress file into output dir for archival
    if progress.path:
        (output_dir / "progress.jsonl").write_text(progress.path.read_text())

    console.print(f"\n  Output saved to {output_dir}/")
    progress.log("saved", {"output_dir": str(output_dir)})

    if improvement > 0:
        console.print(f"\n[bold green]✓ Evolution improved skill by {improvement:+.3f} ({improvement/max(0.001, avg_baseline)*100:+.1f}%)[/bold green]")
        console.print(f"  Review the diff: diff {output_dir}/baseline_skill.md {output_dir}/evolved_skill.md")
        progress.log("complete", {"status": "improved", "improvement": improvement})
    else:
        console.print(f"\n[yellow]⚠ Evolution did not improve skill (change: {improvement:+.3f})[/yellow]")
        console.print("  Try: more iterations, better eval dataset, or different optimizer model")
        progress.log("complete", {"status": "no_improvement", "improvement": improvement})


@click.command()
@click.option("--skill", required=True, help="Name of the skill to evolve")
@click.option("--iterations", default=10, help="Number of GEPA iterations")
@click.option("--eval-source", default="synthetic", type=click.Choice(["synthetic", "golden", "sessiondb"]),
              help="Source for evaluation dataset")
@click.option("--dataset-path", default=None, help="Path to existing eval dataset (JSONL)")
@click.option("--optimizer-model", default="openai/google-proxy/gemma-4-31b-it", help="Model for GEPA reflections")
@click.option("--eval-model", default="openai/google-proxy/gemma-4-31b-it", help="Model for evaluations")
@click.option("--hermes-repo", default=None, help="Path to hermes-agent repo")
@click.option("--run-tests", is_flag=True, help="Run full pytest suite as constraint gate")
@click.option("--dry-run", is_flag=True, help="Validate setup without running optimization")
@click.option("--num-trials", default=20, help="Number of MIPROv2 optimization trials")
@click.option("--num-candidates", default=10, help="Number of instruction/few-shot candidates for MIPROv2")
@click.option("--num-threads", default=5, help="Parallel threads for MIPROv2 evaluation")
@click.option("--auto", "auto_mode", default=None, type=click.Choice(["light", "medium", "heavy"]), help="MIPROv2 auto mode (overrides num_trials/num_candidates)")
@click.option("--skill-path", default=None, help="Direct path to SKILL.md (bypasses repo search)")
@click.option("--progress-file", default=None, help="Path to JSONL progress checkpoint file")
def main(skill, iterations, eval_source, dataset_path, optimizer_model, eval_model, hermes_repo, run_tests, dry_run, num_trials, num_candidates, num_threads, auto_mode, skill_path, progress_file):
    """Evolve a Hermes Agent skill using DSPy + GEPA optimization."""
    evolve(
        skill_name=skill,
        iterations=iterations,
        eval_source=eval_source,
        dataset_path=dataset_path,
        optimizer_model=optimizer_model,
        eval_model=eval_model,
        hermes_repo=hermes_repo,
        skill_path=skill_path,
        run_tests=run_tests,
        dry_run=dry_run,
        num_trials=num_trials,
        num_candidates=num_candidates,
        num_threads=num_threads,
        auto_mode=auto_mode,
        progress_file=progress_file,
    )


if __name__ == "__main__":
    main()
