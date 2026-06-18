from __future__ import annotations

import time
from dataclasses import dataclass

from materials2textbook.llm.provider import LLMProvider


@dataclass
class LLMRetryStats:
    attempts: int = 0
    retries: int = 0
    failures: int = 0


class RetryingLLMProvider:
    """Retry transient LLM failures before surfacing the last error."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
    ) -> None:
        self.provider = provider
        self.max_retries = max(0, max_retries)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.stats = LLMRetryStats()

    def generate(self, messages: list[dict[str, str]]) -> str:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.stats.attempts += 1
            try:
                return self.provider.generate(messages)
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    self.stats.failures += 1
                    break
                self.stats.retries += 1
                if self.backoff_seconds:
                    time.sleep(self.backoff_seconds * (2**attempt))
        assert last_error is not None
        raise RuntimeError(f"LLM request failed after {self.max_retries + 1} attempts: {last_error}") from last_error
