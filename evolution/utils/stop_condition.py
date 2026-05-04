#!/usr/bin/env python3
"""
stop_condition.py — Dead Man's Switch for GEPA evolution loops.

Detects stalled evolution runs and triggers graceful exit before the
process hangs indefinitely. Wired into evolve_prompts.py at the
single-prompt and tier level.
"""

import time
import signal
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class GEPAStopper:
    """Dead Man's Switch for GEPA optimization loops.

    Monitors wall-clock time and per-prompt iteration progress.
    Triggers graceful exit when:
      - A single prompt exceeds max_seconds_per_prompt (default: 1200s = 20 min)
      - The total run exceeds max_total_seconds (default: 7200s = 2 hr)

    Also installs a SIGALRM handler for hard-interrupt on stuck processes.
    """

    def __init__(
        self,
        max_seconds_per_prompt: int = 1200,
        max_total_seconds: int = 7200,
        install_sigalrm: bool = True,
    ):
        self.max_per_prompt = max_seconds_per_prompt
        self.max_total = max_total_seconds
        self._run_start: Optional[float] = None
        self._prompt_start: Optional[float] = None
        self._current_prompt: Optional[str] = None
        self._triggered = False

        if install_sigalrm:
            self._install_sigalrm()

    def _install_sigalrm(self):
        """Install SIGALRM handler for hard interrupt on stuck processes.

        On macOS, SIGALRM can interrupt blocking system calls (like
        subprocess.wait or socket reads) that normal signals cannot.
        """
        def _handler(signum, frame):
            logger.warning(
                "SIGALRM received — process appears stuck. "
                f"Current prompt: {self._current_prompt}. "
                "Forcing interrupt."
            )
            raise TimeoutError(
                f"GEPA Stopper SIGALRM: prompt '{self._current_prompt}' "
                f"exceeded {self.max_per_prompt}s timeout"
            )

        signal.signal(signal.SIGALRM, _handler)

    def start_run(self):
        """Call when the evolution run starts."""
        self._run_start = time.time()
        self._prompt_start = None
        self._current_prompt = None
        self._triggered = False

    def start_prompt(self, prompt_id: str):
        """Call when a new prompt evolution begins."""
        self._prompt_start = time.time()
        self._current_prompt = prompt_id

        # Set SIGALRM for hard interrupt
        signal.alarm(self.max_per_prompt)

    def end_prompt(self):
        """Call when a prompt evolution completes."""
        self._current_prompt = None
        self._prompt_start = None
        signal.alarm(0)  # Cancel pending alarm

    def check(self) -> bool:
        """Check all stop conditions. Returns True if should stop."""
        if self._triggered:
            return True

        now = time.time()

        # Check per-prompt timeout
        if self._prompt_start is not None and self._current_prompt is not None:
            elapsed = now - self._prompt_start
            if elapsed > self.max_per_prompt:
                logger.warning(
                    f"Prompt '{self._current_prompt}' exceeded "
                    f"{self.max_per_prompt}s timeout ({elapsed:.0f}s)."
                )
                self._triggered = True
                signal.alarm(0)
                return True

        # Check total run timeout
        if self._run_start is not None:
            total_elapsed = now - self._run_start
            if total_elapsed > self.max_total:
                logger.warning(
                    f"Total run exceeded {self.max_total}s timeout "
                    f"({total_elapsed:.0f}s)."
                )
                self._triggered = True
                signal.alarm(0)
                return True

        return False

    def was_triggered(self) -> bool:
        return self._triggered
