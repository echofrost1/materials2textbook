from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


_THINK_PATTERN = re.compile(r"<think>.*?</think>\s*", flags=re.DOTALL)
_THINK_UNCLOSED = re.compile(r"<think>.*$", flags=re.DOTALL)


def strip_think_blocks(text: str) -> str:
    """Remove Qwen3 <think>...</think> reasoning blocks from LLM output."""
    text = _THINK_PATTERN.sub("", text)
    text = _THINK_UNCLOSED.sub("", text)
    return text.strip()


class LLMProvider(Protocol):
    def generate(self, messages: list[dict[str, str]]) -> str:
        """Generate text from chat messages."""


@dataclass
class OpenAICompatibleConfig:
    api_key: str
    base_url: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout_seconds: int = 120

    @classmethod
    def from_env(cls, prefix: str = "OPENAI") -> "OpenAICompatibleConfig":
        legacy_prefix = "ECNU_PLUS"

        def env_value(name: str, default: str = "") -> str:
            return os.getenv(f"{prefix}_{name}", os.getenv(f"{legacy_prefix}_{name}", default))

        return cls(
            api_key=env_value("API_KEY"),
            base_url=env_value("BASE_URL"),
            model=env_value("MODEL"),
            temperature=float(env_value("TEMPERATURE", "0.2")),
            max_tokens=int(env_value("MAX_TOKENS", "4096")),
            timeout_seconds=int(env_value("TIMEOUT_SECONDS", "120")),
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


class OpenAICompatibleProvider:
    """Minimal OpenAI-compatible chat-completions client.

    This avoids locking the project to one SDK. It works with services that expose
    an OpenAI-style `/chat/completions` endpoint when base URL, key, and model are
    provided.
    """

    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self.config = config

    def generate(self, messages: list[dict[str, str]]) -> str:
        if not self.config.is_configured:
            raise RuntimeError(
                "LLM provider is not configured. Set OPENAI_API_KEY, "
                "OPENAI_BASE_URL, and OPENAI_MODEL, or pass CLI options."
            )

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        request = urllib.request.Request(
            url=f"{self.config.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM request failed: HTTP {exc.code}: {body[:1000]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected LLM response shape: {data}") from exc
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"Unexpected empty LLM response content: {data}")
        return strip_think_blocks(content)
