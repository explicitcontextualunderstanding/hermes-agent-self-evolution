#!/usr/bin/env python3
"""direct_proxy_lm — drop-in replacement for make_hermes_lm in otel_adapter.py.

Calls the local kilo-proxy (or any OpenAI-compatible proxy) directly via HTTP
instead of spawning a `hermes chat -q` subprocess. This eliminates the CLI
startup/banner overhead (~6 KB of stdout parsing) and the API-key
authentication errors that occur when hermes routes to the wrong provider.

Environment:
    PROXY_URL     – proxy base URL (default http://localhost:8080/v1)
    PROXY_MODEL   – model identifier WITH provider prefix (default
                    nvidia-proxy/deepseek-ai/deepseek-v4-flash)
"""
import json
import os
import time
from pathlib import Path

try:
    import requests  # type: ignore
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

from evolution.env_config import COST_TRACKER

DEFAULT_PROXY_URL = os.environ.get("PROXY_URL", "http://localhost:8080/v1")
DEFAULT_PROXY_MODEL = os.environ.get(
    "PROXY_MODEL",
    "nvidia-proxy/deepseek-ai/deepseek-v4-flash",
)

# Global cost tracker path (lazy-initialized)
_cost_log: Path | None = None


def make_direct_proxy_lm(
    proxy_url: str = DEFAULT_PROXY_URL,
    model: str = DEFAULT_PROXY_MODEL,
    max_turns: int = 1,
    timeout: int = 120,
) -> callable:
    """Create a GEPA-compatible LanguageModel callable that hits the proxy directly.

    Returns a callable: prompt_text -> response_text
    """

    if requests is None:
        raise ImportError(
            "requests is required for make_direct_proxy_lm. "
            "Install: pip install requests"
        )

    def _proxy_lm(prompt: str | list[dict]) -> str:
        if isinstance(prompt, list):
            # GEPA sometimes passes a list of message dicts
            messages = prompt
        else:
            messages = [{"role": "user", "content": prompt}]

        body = {
            "model": model,
            "messages": messages,
            "max_tokens": 2048,
            "temperature": 0.7,
        }

        _start = time.time()
        _success = False
        try:
            r = requests.post(
                f"{proxy_url}/chat/completions",
                json=body,
                timeout=timeout,
            )
            _success = r.status_code == 200
            if _success:
                data = r.json()
                choice = data.get("choices", [{}])[0]
                msg = choice.get("message", {})
                content = msg.get("content", "")
                # Strip stray markdown fences that reflection models love to emit
                content = content.strip()
                if content.startswith("```"):
                    lines = content.splitlines()
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    content = "\n".join(lines).strip()
                return content
            else:
                return f"[PROXY ERROR {r.status_code}] {r.text[:500]}"
        except Exception as exc:
            return f"[PROXY EXCEPTION] {exc}"
        finally:
            _elapsed = time.time() - _start
            _cost_entry = {
                "metric": "reflection_lm_cost",
                "model": model,
                "latency_s": round(_elapsed, 2),
                "success": _success,
                "prompt_chars": sum(len(str(m.get("content", ""))) for m in messages),
                "_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            global _cost_log
            if _cost_log is None:
                _cost_log = COST_TRACKER
                _cost_log.parent.mkdir(parents=True, exist_ok=True)
            with open(_cost_log, "a") as _f:
                _f.write(json.dumps(_cost_entry) + "\n")

    return _proxy_lm


# ── Monkey-patch compatibility ──────────────────────────────────────────────
# If imported into otel_adapter.py, just replace the make_hermes_lm call with
# make_direct_proxy_lm(...).

if __name__ == "__main__":
    lm = make_direct_proxy_lm()
    print(lm("Say OK"))
