"""OpenAI-compatible provider implementation.

Supports OpenAI, DeepSeek, Kimi, MiniMax, and any provider
that implements the OpenAI chat completions API.
"""

from __future__ import annotations

import time
from typing import Any

from .base import (
    BaseProvider,
    CompletionRequest,
    CompletionResponse,
    HealthCheckResult,
    MessageRole,
    ModelInfo,
    TokenUsage,
)

# Pre-configured provider profiles
_PROFILES: dict[str, dict[str, Any]] = {
    "openai": {
        "display_name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": [
            ModelInfo(id="gpt-4o", display_name="GPT-4o", provider="openai", max_tokens=4096),
            ModelInfo(id="gpt-4o-mini", display_name="GPT-4o Mini", provider="openai", max_tokens=4096),
            ModelInfo(id="gpt-4-turbo", display_name="GPT-4 Turbo", provider="openai", max_tokens=4096),
        ],
    },
    "deepseek": {
        "display_name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": [
            ModelInfo(id="deepseek-chat", display_name="DeepSeek Chat", provider="deepseek", max_tokens=4096),
            ModelInfo(id="deepseek-reasoner", display_name="DeepSeek Reasoner", provider="deepseek", max_tokens=4096),
        ],
    },
    "kimi": {
        "display_name": "Kimi (Moonshot)",
        "base_url": "https://api.moonshot.cn/v1",
        "models": [
            ModelInfo(id="moonshot-v1-8k", display_name="Moonshot v1 8K", provider="kimi", max_tokens=4096),
            ModelInfo(id="moonshot-v1-32k", display_name="Moonshot v1 32K", provider="kimi", max_tokens=4096),
        ],
    },
    "minimax": {
        "display_name": "MiniMax",
        "base_url": "https://api.minimax.chat/v1",
        "models": [
            ModelInfo(id="abab6.5s-chat", display_name="ABAB 6.5s", provider="minimax", max_tokens=4096),
        ],
    },
}


class OpenAICompatibleProvider(BaseProvider):
    """Provider for any OpenAI-compatible API.

    Uses the openai Python SDK with a configurable base_url.
    """

    def __init__(self, profile: str = "openai") -> None:
        cfg = _PROFILES.get(profile, _PROFILES["openai"])
        self.name: str = profile
        self.display_name: str = cfg["display_name"]
        self.base_url: str = cfg["base_url"]
        self._default_models: list[ModelInfo] = cfg["models"]
        self.api_key: str = ""

    def configure(self, api_key: str, **kwargs: Any) -> None:
        self.api_key = api_key
        if "base_url" in kwargs and kwargs["base_url"]:
            self.base_url = kwargs["base_url"]

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if not self.api_key:
            raise ValueError(f"{self.display_name} API key not configured")

        try:
            import openai
        except ImportError:
            raise RuntimeError(
                "openai package not installed. Run: pip install openai"
            )

        client = openai.AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        for msg in request.messages:
            messages.append({"role": msg.role.value, "content": msg.content})

        response = await client.chat.completions.create(
            model=request.model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )

        choice = response.choices[0]
        usage = response.usage

        return CompletionResponse(
            content=choice.message.content or "",
            model=response.model,
            provider=self.name,
            usage=TokenUsage(
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
            ),
            finish_reason=choice.finish_reason or "stop",
        )

    async def list_models(self) -> list[ModelInfo]:
        return list(self._default_models)

    async def healthcheck(self) -> HealthCheckResult:
        if not self.api_key:
            return HealthCheckResult(
                ok=False,
                provider=self.name,
                message="API key not configured",
            )

        try:
            import openai
        except ImportError:
            return HealthCheckResult(
                ok=False,
                provider=self.name,
                message="openai package not installed",
            )

        start = time.monotonic()
        try:
            client = openai.AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
            await client.chat.completions.create(
                model=self._default_models[0].id if self._default_models else "gpt-4o-mini",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            latency = (time.monotonic() - start) * 1000
            return HealthCheckResult(
                ok=True,
                provider=self.name,
                message="Connected",
                latency_ms=round(latency, 1),
            )
        except openai.AuthenticationError:
            return HealthCheckResult(
                ok=False,
                provider=self.name,
                message="Invalid API key",
            )
        except Exception as exc:
            return HealthCheckResult(
                ok=False,
                provider=self.name,
                message=f"Connection failed: {type(exc).__name__}",
            )
