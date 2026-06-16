from pathlib import Path

from materials2textbook.llm.cache import CachingLLMProvider, build_llm_cache_key


class CountingLLM:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        return f"response-{self.calls}"


def test_cache_reuses_response_for_same_messages(tmp_path: Path) -> None:
    provider = CountingLLM()
    cached = CachingLLMProvider(provider, tmp_path / "llm_cache.json")
    messages = [{"role": "user", "content": "normalize this"}]

    first = cached.generate(messages)
    second = cached.generate(messages)

    assert first == "response-1"
    assert second == "response-1"
    assert provider.calls == 1
    assert cached.stats.hits == 1
    assert cached.stats.misses == 1


def test_cache_persists_across_instances(tmp_path: Path) -> None:
    cache_path = tmp_path / "llm_cache.json"
    messages = [{"role": "system", "content": "review"}, {"role": "user", "content": "draft"}]

    first_provider = CountingLLM()
    CachingLLMProvider(first_provider, cache_path).generate(messages)

    second_provider = CountingLLM()
    second_cache = CachingLLMProvider(second_provider, cache_path)

    assert second_cache.generate(messages) == "response-1"
    assert second_provider.calls == 0
    assert second_cache.stats.hits == 1


def test_cache_key_ignores_extra_message_fields() -> None:
    assert build_llm_cache_key([{"role": "user", "content": "x", "name": "ignored"}]) == build_llm_cache_key(
        [{"role": "user", "content": "x"}]
    )
