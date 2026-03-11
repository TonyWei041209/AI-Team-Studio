"""Anthropic (Claude) provider implementation."""

from __future__ import annotations

import time
from typing import Any

from .base import (
    BaseProvider,
    CompletionRequest,
    CompletionResponse,
    HealthCheckResult,
    Message,
    MessageRole,
    ModelInfo,
    TokenUsage,
)

# Known Claude models with metadata
_CLAUDE_MODELS = [
    ModelInfo(
        id="claude-sonnet-4-20250514",
        display_name="Claude Sonnet 4",
        provider="anthropic",
        max_tokens=8192,
    ),
    ModelInfo(
        id="claude-3-5-haiku-20241022",
        display_name="Claude 3.5 Haiku",
        provider="anthropic",
        max_tokens=8192,
    ),
    ModelInfo(
        id="claude-3-5-sonnet-20241022",
        display_name="Claude 3.5 Sonnet",
        provider="anthropic",
        max_tokens=8192,
    ),
]


class AnthropicProvider(BaseProvider):
    """Provider for Anthropic Claude models."""

    name = "anthropic"
    display_name = "Anthropic (Claude)"

    def __init__(self) -> None:
        self.api_key: str = ""

    def configure(self, api_key: str, **kwargs: Any) -> None:
        self.api_key = api_key

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if not self.api_key:
            raise ValueError("Anthropic API key not configured")

        try:
            import anthropic
        except ImportError:
            raise RuntimeError(
                "anthropic package not installed. Run: pip install anthropic"
            )

        client = anthropic.AsyncAnthropic(api_key=self.api_key)

        # Separate system prompt from messages (Anthropic API requirement)
        system_text = request.system_prompt or ""
        messages: list[dict[str, str]] = []
        for msg in request.messages:
            if msg.role == MessageRole.system:
                system_text = msg.content
            else:
                messages.append({"role": msg.role.value, "content": msg.content})

        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if system_text:
            kwargs["system"] = system_text

        response = await client.messages.create(**kwargs)

        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text

        return CompletionResponse(
            content=content,
            model=response.model,
            provider=self.name,
            usage=TokenUsage(
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
            ),
            finish_reason=response.stop_reason or "stop",
        )

    async def list_models(self) -> list[ModelInfo]:
        return list(_CLAUDE_MODELS)

    async def healthcheck(self) -> HealthCheckResult:
        if not self.api_key:
            return HealthCheckResult(
                ok=False,
                provider=self.name,
                message="API key not configured",
            )

        try:
            import anthropic
        except ImportError:
            return HealthCheckResult(
                ok=False,
                provider=self.name,
                message="anthropic package not installed",
            )

        start = time.monotonic()
        try:
            client = anthropic.AsyncAnthropic(api_key=self.api_key)
            # Minimal request to verify key validity
            await client.messages.create(
                model="claude-3-5-haiku-20241022",
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
        except anthropic.AuthenticationError:
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
