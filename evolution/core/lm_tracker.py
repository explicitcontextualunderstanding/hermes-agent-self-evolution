"""LM call tracker — instruments every LLM call site with structured logging.

Provides a singleton LmCallTracker that records:
- Which site made the call (skill_module.forward, llm_judge.score, reflection, dataset_gen)
- Model used
- Input character count (proxy for token cost)
- Iteration/candidate/example indices (when available)
- Elapsed time

Call summary is logged to stdout and progress.jsonl at the end of evolution.

Usage:
    from evolution.core.lm_tracker import LM_TRACKER

    # Before an LM call:
    LM_TRACKER.record(site="skill_module.forward", model=model_name,
                      input_chars=len(skill_text), iteration=i,
                      candidate=j, example=k)

    # At end of evolution:
    LM_TRACKER.summary()  # returns dict with totals
    LM_TRACKER.reset()    # for next run
"""

import json
import os
import time
from typing import Optional


class LmCallTracker:
    """Thread-safe singleton for tracking all LM calls during evolution."""

    _instance: Optional["LmCallTracker"] = None

    def __new__(cls) -> "LmCallTracker":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._start = time.time()
            cls._instance._calls: list[dict] = []
            cls._instance._progress_logger = None
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the tracker for a new evolution run."""
        if cls._instance is not None:
            cls._instance._calls.clear()
            cls._instance._start = time.time()

    @classmethod
    def bind_progress(cls, progress_logger) -> None:
        """Attach a ProgressLogger so call events are also written to progress.jsonl."""
        if cls._instance is not None:
            cls._instance._progress_logger = progress_logger

    def record(
        self,
        site: str,
        model: str = "",
        input_chars: int = 0,
        iteration: Optional[int] = None,
        candidate: Optional[int] = None,
        example: Optional[int] = None,
        phase: Optional[str] = None,
    ) -> dict:
        """Record an LM call and return the record dict.

        Also writes to progress.jsonl if a ProgressLogger is bound.
        """
        record = {
            "phase": "lm_call",
            "site": site,
            "model": model,
            "input_chars": input_chars,
            "iteration": iteration,
            "candidate": candidate,
            "example": example,
            "_elapsed": round(time.time() - self._start, 2),
        }
        self._calls.append(record)

        # Also write to bound progress logger (emits to progress.jsonl)
        if self._progress_logger is not None and hasattr(self._progress_logger, "_write"):
            self._progress_logger._write(record)

        # Always print a compact one-line summary to stderr so it's visible in cron logs
        loc = f"iter={iteration}" if iteration is not None else ""
        if candidate is not None:
            loc += f"/cand={candidate}"
        if example is not None:
            loc += f"/ex={example}"
        chars_k = round(input_chars / 1000, 1)
        tag = phase or "lm"
        print(
            f"[LMTRACK] {tag} {site} | model={model.split('/')[-1][:30]} "
            f"| {chars_k}k chars | {loc} | elapsed={record['_elapsed']:.1f}s",
            file=__import__("sys").stderr,
        )

        return record

    def summary(self) -> dict:
        """Aggregate call statistics for final reporting.

        Returns dict with:
            total_lm_calls: int
            total_input_chars: int
            calls_by_site: dict[str, int]
            chars_by_site: dict[str, int]
            elapsed: float
        """
        total_calls = len(self._calls)
        total_chars = sum(c.get("input_chars", 0) for c in self._calls)
        calls_by_site: dict[str, int] = {}
        chars_by_site: dict[str, int] = {}

        for c in self._calls:
            site = c.get("site", "unknown")
            calls_by_site[site] = calls_by_site.get(site, 0) + 1
            chars_by_site[site] = chars_by_site.get(site, 0) + c.get("input_chars", 0)

        elapsed = round(time.time() - self._start, 2)

        result = {
            "phase": "call_summary",
            "total_lm_calls": total_calls,
            "total_input_chars": total_chars,
            "estimated_tokens_premium": total_chars // 4,  # rough estimate
            "calls_by_site": calls_by_site,
            "chars_by_site": chars_by_site,
            "_elapsed": elapsed,
        }

        print(
            f"\n{'='*60}\n"
            f"LM CALL TRACKER SUMMARY\n"
            f"{'='*60}\n"
            f"  Total LM calls : {total_calls}\n"
            f"  Total input    : {total_chars:,} chars (~{total_chars//4:,} tokens)\n"
            f"  Elapsed        : {elapsed:.1f}s\n"
            f"  By site:\n",
            file=__import__("sys").stderr,
        )
        for site in sorted(calls_by_site.keys()):
            pct = calls_by_site[site] / max(1, total_calls) * 100
            print(
                f"    {site:40s} {calls_by_site[site]:>5d} calls "
                f"({pct:4.1f}%) {chars_by_site[site]:>10,} chars",
                file=__import__("sys").stderr,
            )
        print(f"{'='*60}\n", file=__import__("sys").stderr)

        return result

    def summary_log(self) -> str:
        """Return summary as a single JSON line for progress.jsonl."""
        return json.dumps(self.summary())

    @classmethod
    def get(cls) -> "LmCallTracker":
        """Get the singleton instance, creating it if needed."""
        if cls._instance is None:
            cls._instance = cls.__new__(cls)
        return cls._instance


# Module-level singleton — import this everywhere
LM_TRACKER = LmCallTracker()
