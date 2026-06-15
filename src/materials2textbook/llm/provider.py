from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


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
    def from_env(cls, prefix: str = "ECNU_PLUS") -> "OpenAICompatibleConfig":
        return cls(
            api_key=os.getenv(f"{prefix}_API_KEY", ""),
            base_url=os.getenv(f"{prefix}_BASE_URL", ""),
            model=os.getenv(f"{prefix}_MODEL", "ecnu-plus"),
            temperature=float(os.getenv(f"{prefix}_TEMPERATURE", "0.2")),
            max_tokens=int(os.getenv(f"{prefix}_MAX_TOKENS", "4096")),
            timeout_seconds=int(os.getenv(f"{prefix}_TIMEOUT_SECONDS", "120")),
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


class OpenAICompatibleProvider:
    """Minimal OpenAI-compatible chat-completions client.

    This avoids locking the project to one SDK. It works with services that expose
    an OpenAI-style `/chat/completions` endpoint, including the planned ecnu-plus
    deployment when base URL, key, and model are provided.
    """

    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self.config = config

    def generate(self, messages: list[dict[str, str]]) -> str:
        if not self.config.is_configured:
            raise RuntimeError(
                "LLM provider is not configured. Set ECNU_PLUS_API_KEY, "
                "ECNU_PLUS_BASE_URL, and ECNU_PLUS_MODEL, or pass CLI options."
            )

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
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
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected LLM response shape: {data}") from exc
