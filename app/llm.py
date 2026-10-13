"""LLM providers + circuit breaker + fallback.

Hosted (Anthropic) vs self-hosted (vLLM) is just a config switch: LLM_PROVIDER.
"""
import logging
import time
from dataclasses import dataclass

import httpx

from .config import settings
from .metrics import BREAKER_OPEN, LLM_CALLS, LLM_COST, LLM_TOKENS

log = logging.getLogger("llm")

FALLBACK_MESSAGE = (
    "The AI service is temporarily unavailable. Please try again in a minute."
)


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class CircuitBreaker:
    """closed -> (fail_max failures) -> open -> (reset_s passes) -> half_open -> one trial call."""

    def __init__(self, fail_max: int, reset_s: float):
        self.fail_max = fail_max
        self.reset_s = reset_s
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if time.monotonic() - self.opened_at >= self.reset_s:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        return self.state != "open"

    def success(self) -> None:
        self.failures = 0
        self.opened_at = None
        BREAKER_OPEN.set(0)

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.fail_max:
            self.opened_at = time.monotonic()
            BREAKER_OPEN.set(1)


class MockProvider:
    name = "mock"

    def generate(self, prompt: str) -> LLMResult:
        return LLMResult(f"[mock answer] {prompt}", len(prompt.split()), 5)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self) -> None:
        import anthropic

        self.client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.llm_timeout_s,
            max_retries=1,
        )

    def generate(self, prompt: str) -> LLMResult:
        msg = self.client.messages.create(
            model=settings.llm_model,
            max_tokens=settings.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text")
        return LLMResult(text, msg.usage.input_tokens, msg.usage.output_tokens)


class VLLMProvider:
    """Self-hosted model served by vLLM's OpenAI-compatible server (needs an NVIDIA GPU)."""

    name = "vllm"

    def generate(self, prompt: str) -> LLMResult:
        r = httpx.post(
            f"{settings.vllm_base_url}/chat/completions",
            json={
                "model": settings.llm_model,
                "max_tokens": settings.max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=settings.llm_timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage", {})
        return LLMResult(
            data["choices"][0]["message"]["content"],
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )


def build_provider():
    return {"anthropic": AnthropicProvider, "vllm": VLLMProvider}.get(
        settings.llm_provider, MockProvider
    )()


class LLMService:
    def __init__(self, provider=None, breaker: CircuitBreaker | None = None):
        self.provider = provider or build_provider()
        self.breaker = breaker or CircuitBreaker(settings.cb_fail_max, settings.cb_reset_s)

    def generate(self, prompt: str) -> tuple[str, str]:
        """Returns (text, source). source is the provider name or 'fallback'."""
        if not self.breaker.allow():
            LLM_CALLS.labels(self.provider.name, "fallback").inc()
            return FALLBACK_MESSAGE, "fallback"
        try:
            res = self.provider.generate(prompt)
        except Exception as e:  # noqa: BLE001 - any provider failure trips the breaker
            log.warning("LLM call failed: %s", type(e).__name__)
            self.breaker.failure()
            LLM_CALLS.labels(self.provider.name, "error").inc()
            return FALLBACK_MESSAGE, "fallback"
        self.breaker.success()
        LLM_CALLS.labels(self.provider.name, "success").inc()
        LLM_TOKENS.labels("input").inc(res.input_tokens)
        LLM_TOKENS.labels("output").inc(res.output_tokens)
        LLM_COST.inc(
            res.input_tokens / 1e6 * settings.input_price_per_mtok
            + res.output_tokens / 1e6 * settings.output_price_per_mtok
        )
        return res.text, self.provider.name
