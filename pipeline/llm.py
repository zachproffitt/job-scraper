"""Unified LLM interface for the pipeline.

Callers use `chat(system, user_message, max_tokens)` without knowing which
backend is configured. Set LLM_BACKEND=claude (default) or LLM_BACKEND=ollama.

Adding a new backend (e.g. openai, llama.cpp) means: implement a class with a
`chat()` method matching the LLMBackend Protocol, then register it in the
_BACKENDS dict. No changes needed at any call site.

Each backend owns its own state (rate limiter, usage counters), so the module
itself stays free of globals beyond the chosen backend singleton.
"""

import os
import threading
import time
from typing import Callable, Protocol

LogError = Callable[[str], None]

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
OLLAMA_MODEL = "qwen3:8b"

# Pricing per million tokens (Haiku 4.5).
# Source: https://www.anthropic.com/pricing — verified 2026-05-19.
# Anthropic does not expose pricing via API; update these when announcements ship.
CLAUDE_PRICE_INPUT = 1.00
CLAUDE_PRICE_OUTPUT = 5.00
CLAUDE_PRICE_CACHE_WRITE = 1.25
CLAUDE_PRICE_CACHE_READ = 0.10


# --- Helpers -----------------------------------------------------------------


class Usage:
    """Thread-safe accumulator for Anthropic-style token counters."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict = {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }

    def add(self, response_usage) -> None:
        with self._lock:
            self._counts["requests"] += 1
            self._counts["input_tokens"] += response_usage.input_tokens
            self._counts["output_tokens"] += response_usage.output_tokens
            self._counts["cache_creation_input_tokens"] += getattr(response_usage, "cache_creation_input_tokens", 0) or 0
            self._counts["cache_read_input_tokens"] += getattr(response_usage, "cache_read_input_tokens", 0) or 0

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._counts)


class RateLimiter:
    """Token-bucket limiter. The lock is held across the sleep to serialize
    dispatch across worker threads — otherwise threads compute identical waits,
    sleep in parallel, and burst together when they wake.
    """

    def __init__(self, tokens_per_min: float, tokens_per_request: float) -> None:
        self._tokens_per_min = tokens_per_min
        self._tokens_per_request = tokens_per_request
        self._tokens = 0.0  # start empty to avoid a burst on first dispatch
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                self._tokens_per_min,
                self._tokens + elapsed / 60.0 * self._tokens_per_min,
            )
            self._last_refill = now
            if self._tokens < self._tokens_per_request:
                wait = (self._tokens_per_request - self._tokens) / (self._tokens_per_min / 60.0)
                time.sleep(wait)
                self._tokens = 0.0
                self._last_refill = time.monotonic()
            else:
                self._tokens -= self._tokens_per_request


# --- Backend interface and implementations -----------------------------------


class LLMBackend(Protocol):
    """Any backend that can run a single-turn chat completion.

    Structural typing — implementations don't need to inherit from this. As long
    as a class has `chat()` and `get_usage()` with matching signatures, it
    satisfies the Protocol.
    """

    def chat(self, system: str, user_message: str, max_tokens: int,
             log_error: LogError | None = None) -> str: ...

    def get_usage(self) -> dict: ...


class ClaudeBackend:
    """Anthropic Claude backend. Manages its own rate limiter and usage counters."""

    _TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 529}

    def __init__(self) -> None:
        self._usage = Usage()
        # Anthropic org limit is 50k input tokens/minute; throttle at 40k for
        # headroom. ~4,000 tokens per request (2,545 system + ~1,100 user + buffer)
        # gives ~10 requests/minute.
        self._rate_limiter = RateLimiter(tokens_per_min=40_000, tokens_per_request=4_000)

    def chat(self, system: str, user_message: str, max_tokens: int,
             log_error: LogError | None = None) -> str:
        import anthropic
        self._rate_limiter.acquire()
        client = anthropic.Anthropic()
        kwargs: dict = {
            "model": CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user_message}],
        }
        if system:
            kwargs["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

        for attempt in range(5):
            try:
                response = client.messages.create(**kwargs)
                self._usage.add(response.usage)
                return response.content[0].text.strip()
            except (anthropic.RateLimitError, anthropic.APIStatusError) as e:
                if isinstance(e, anthropic.APIStatusError) and e.status_code not in self._TRANSIENT_STATUS_CODES:
                    raise
                delay = 2 ** attempt
                if log_error:
                    log_error(f"transient API error (attempt {attempt+1}/5): {e} — retrying in {delay}s")
                time.sleep(delay)
        raise RuntimeError("Claude API unavailable after 5 retries")

    def get_usage(self) -> dict:
        return self._usage.snapshot()


class OllamaBackend:
    """Local Ollama backend. No rate limiting; minimal usage tracking."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests = 0

    def chat(self, system: str, user_message: str, max_tokens: int,
             log_error: LogError | None = None) -> str:
        del log_error  # required by Protocol; Ollama has no retry loop to log
        import ollama
        # qwen3 supports /no_think to skip chain-of-thought for faster classification.
        prefix = "/no_think\n"
        prompt = prefix + (system + "\n\n" if system else "") + user_message
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1, "num_ctx": 4096, "num_predict": max_tokens},
            keep_alive="10m",
        )
        with self._lock:
            self._requests += 1
        return response["message"]["content"].strip()

    def get_usage(self) -> dict:
        with self._lock:
            return {"requests": self._requests}


# --- Module-level singleton --------------------------------------------------


_BACKENDS: dict[str, type[LLMBackend]] = {
    "claude": ClaudeBackend,
    "ollama": OllamaBackend,
}

BACKEND = os.environ.get("LLM_BACKEND", "claude")
if BACKEND not in _BACKENDS:
    raise ValueError(f"Unknown LLM_BACKEND: {BACKEND!r} (expected one of {sorted(_BACKENDS)})")

_backend: LLMBackend = _BACKENDS[BACKEND]()


def chat(system: str, user_message: str, max_tokens: int,
         log_error: LogError | None = None) -> str:
    """Send a chat request to the configured backend, return the response text."""
    return _backend.chat(system, user_message, max_tokens, log_error)


def get_usage() -> dict:
    """Snapshot of token usage accumulated across all chat() calls this process."""
    return _backend.get_usage()


def estimate_cost(usage: dict) -> float:
    """Estimated USD cost for a usage dict from get_usage(). Returns 0 for Ollama."""
    return (
        usage.get("input_tokens", 0) * CLAUDE_PRICE_INPUT / 1_000_000
        + usage.get("output_tokens", 0) * CLAUDE_PRICE_OUTPUT / 1_000_000
        + usage.get("cache_creation_input_tokens", 0) * CLAUDE_PRICE_CACHE_WRITE / 1_000_000
        + usage.get("cache_read_input_tokens", 0) * CLAUDE_PRICE_CACHE_READ / 1_000_000
    )
