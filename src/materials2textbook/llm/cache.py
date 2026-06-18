from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from materials2textbook.llm.provider import LLMProvider


@dataclass
class LLMCacheStats:
    hits: int = 0
    misses: int = 0


class CachingLLMProvider:
    """Persist deterministic chat responses for repeatable multi-agent runs."""

    def __init__(self, provider: LLMProvider, cache_path: Path) -> None:
        self.provider = provider
        self.cache_path = cache_path
        self.stats = LLMCacheStats()
        self._cache: dict[str, str] = self._load()

    def generate(self, messages: list[dict[str, str]]) -> str:
        key = build_llm_cache_key(messages)
        if key in self._cache:
            self.stats.hits += 1
            return self._cache[key]

        self.stats.misses += 1
        response = self.provider.generate(messages)
        if not isinstance(response, str) or not response.strip():
            raise RuntimeError("LLM cache received an empty response and will not store it.")
        self._cache[key] = response
        self._save()
        return response

    def _load(self) -> dict[str, str]:
        if not self.cache_path.exists():
            return {}
        data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"LLM cache must be a JSON object: {self.cache_path}")
        return {str(key): value for key, value in data.items() if isinstance(value, str) and value.strip()}

    def _save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def build_llm_cache_key(messages: list[dict[str, str]]) -> str:
    normalized: list[dict[str, Any]] = [
        {
            "role": str(message.get("role", "")),
            "content": str(message.get("content", "")),
        }
        for message in messages
    ]
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
