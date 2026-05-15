import os
import time

BACKEND = os.environ.get("LLM_BACKEND", "claude")
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
OLLAMA_MODEL = "qwen3:8b"

_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 529}


def call_claude(system: str, user_message: str, max_tokens: int, log_error=None) -> str:
    import anthropic
    client = anthropic.Anthropic()
    for attempt in range(5):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text.strip()
        except (anthropic.RateLimitError, anthropic.APIStatusError) as e:
            if isinstance(e, anthropic.APIStatusError) and e.status_code not in _TRANSIENT_STATUS_CODES:
                raise
            delay = 2 ** attempt
            if log_error:
                log_error(f"transient API error (attempt {attempt+1}/5): {e} — retrying in {delay}s")
            time.sleep(delay)
    raise RuntimeError("Claude API unavailable after 5 retries")


def call_ollama(prompt: str, num_ctx: int = 4096) -> str:
    import ollama
    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.1, "num_ctx": num_ctx},
        keep_alive="10m",
    )
    return response["message"]["content"].strip()
