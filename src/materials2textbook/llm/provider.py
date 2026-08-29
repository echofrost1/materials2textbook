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
DEFAULT_CONTEXT_WINDOW = 32768


def strip_think_blocks(text: str) -> str:
    """Remove Qwen3 <think>...</think> reasoning blocks from LLM output."""
    text = _THINK_PATTERN.sub("", text)
    text = _THINK_UNCLOSED.sub("", text)
    return text.strip()


class LLMProvider(Protocol):
    def generate(self, messages: list[dict[str, str]], *, max_tokens: int | None = None) -> str:
        """Generate text from chat messages."""


@dataclass
class OpenAICompatibleConfig:
    api_key: str
    base_url: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout_seconds: int = 120
    context_window: int = DEFAULT_CONTEXT_WINDOW

    @classmethod
    def from_env(cls, prefix: str = "OPENAI") -> "OpenAICompatibleConfig":
        def env_value(name: str, default: str = "") -> str:
            return os.getenv(f"{prefix}_{name}", default)

        return cls(
            api_key=env_value("API_KEY"),
            base_url=env_value("BASE_URL"),
            model=env_value("MODEL"),
            temperature=float(env_value("TEMPERATURE", "0.2")),
            max_tokens=int(env_value("MAX_TOKENS", "4096")),
            context_window=int(env_value("CONTEXT_WINDOW", str(DEFAULT_CONTEXT_WINDOW))),
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
        # Retain response metadata for production audit consumers without
        # changing the provider's string-returning protocol.
        self.last_response_metadata: dict[str, Any] = {}
        self.last_server_capability: dict[str, Any] = {}

    def validate_server_context(self) -> int:
        """Fail fast when the configured client window exceeds the server.

        vLLM exposes the effective limit through ``/v1/models``.  This check is
        intentionally performed by the official entry before any generation;
        it does not alter the request or infer a limit from a model name.
        """
        if not self.config.is_configured:
            raise RuntimeError(
                "Cannot validate model context capability before the LLM provider is configured."
            )
        request = urllib.request.Request(
            url=f"{self.config.base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"MODEL_CONTEXT_CAPABILITY_CHECK_FAILED: HTTP {exc.code}: {body[:500]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"MODEL_CONTEXT_CAPABILITY_CHECK_FAILED: {exc}") from exc

        models = data.get("data") if isinstance(data, dict) else None
        if not isinstance(models, list) or not models:
            raise RuntimeError("MODEL_CONTEXT_CAPABILITY_UNKNOWN: /models returned no models")
        selected = next(
            (
                item
                for item in models
                if isinstance(item, dict)
                and str(item.get("id") or "") == self.config.model
            ),
            models[0],
        )
        server_limit = selected.get("max_model_len") if isinstance(selected, dict) else None
        try:
            server_limit = int(server_limit)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("MODEL_CONTEXT_CAPABILITY_UNKNOWN: /models has no max_model_len") from exc
        self.last_server_capability = {
            "model": selected.get("id") if isinstance(selected, dict) else None,
            "server_max_model_len": server_limit,
            "client_context_window": self.config.context_window,
        }
        if self.config.context_window > server_limit:
            raise RuntimeError(
                "MODEL_CONTEXT_CAPABILITY_MISMATCH: "
                f"client_context_window={self.config.context_window} "
                f"server_max_model_len={server_limit}"
            )
        return server_limit

    def generate(self, messages: list[dict[str, str]], *, max_tokens: int | None = None) -> str:
        if not self.config.is_configured:
            raise RuntimeError(
                "LLM provider is not configured. Set OPENAI_API_KEY, "
                "OPENAI_BASE_URL, and OPENAI_MODEL, or pass CLI options."
            )

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
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

        choice = data.get("choices", [{}])[0] if isinstance(data, dict) else {}
        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        self.last_response_metadata = {
            "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
            "response_id": data.get("id") if isinstance(data, dict) else None,
            "prompt_tokens": usage.get("prompt_tokens") if isinstance(usage, dict) else None,
            "completion_tokens": usage.get("completion_tokens") if isinstance(usage, dict) else None,
            "total_tokens": usage.get("total_tokens") if isinstance(usage, dict) else None,
        }

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected LLM response shape: {data}") from exc
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"Unexpected empty LLM response content: {data}")
        return strip_think_blocks(content)
