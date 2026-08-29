from __future__ import annotations

import json

import pytest

from materials2textbook.llm.provider import (
    DEFAULT_CONTEXT_WINDOW,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _provider(monkeypatch, max_model_len: int) -> OpenAICompatibleProvider:
    config = OpenAICompatibleConfig(
        api_key="test",
        base_url="http://example.test/v1",
        model="qwen3-32b-awq",
    )
    provider = OpenAICompatibleProvider(config)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _Response(
            {"data": [{"id": "qwen3-32b-awq", "max_model_len": max_model_len}]}
        ),
    )
    return provider


def test_default_context_window_matches_32k_server_contract() -> None:
    assert DEFAULT_CONTEXT_WINDOW == 32768
    assert OpenAICompatibleConfig("k", "u", "m").context_window == 32768


def test_server_context_validation_accepts_matching_limit(monkeypatch) -> None:
    provider = _provider(monkeypatch, 32768)

    assert provider.validate_server_context() == 32768
    assert provider.last_server_capability["server_max_model_len"] == 32768


def test_server_context_validation_fails_closed_on_mismatch(monkeypatch) -> None:
    provider = _provider(monkeypatch, 8192)

    with pytest.raises(RuntimeError, match="MODEL_CONTEXT_CAPABILITY_MISMATCH"):
        provider.validate_server_context()
