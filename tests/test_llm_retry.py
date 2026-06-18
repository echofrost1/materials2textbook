import pytest

from materials2textbook.llm.retry import RetryingLLMProvider


class FlakyLLM:
    def __init__(self, failures_before_success: int) -> None:
        self.failures_before_success = failures_before_success
        self.calls = 0

    def generate(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise RuntimeError("temporary failure")
        return "ok"


def test_retrying_llm_provider_retries_then_returns_response() -> None:
    provider = FlakyLLM(failures_before_success=2)
    retrying = RetryingLLMProvider(provider, max_retries=2, backoff_seconds=0)

    assert retrying.generate([{"role": "user", "content": "hello"}]) == "ok"
    assert provider.calls == 3
    assert retrying.stats.attempts == 3
    assert retrying.stats.retries == 2
    assert retrying.stats.failures == 0


def test_retrying_llm_provider_raises_after_last_attempt() -> None:
    provider = FlakyLLM(failures_before_success=3)
    retrying = RetryingLLMProvider(provider, max_retries=1, backoff_seconds=0)

    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        retrying.generate([{"role": "user", "content": "hello"}])

    assert provider.calls == 2
    assert retrying.stats.failures == 1
