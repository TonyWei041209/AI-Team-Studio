"""Google Gemini provider implementation."""

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

_GEMINI_MODELS = [
    ModelInfo(
        id="gemini-2.5-flash",
        display_name="Gemini 2.5 Flash",
        provider="gemini",
        max_tokens=8192,
    ),
    ModelInfo(
        id="gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        provider="gemini",
        max_tokens=8192,
    ),
    ModelInfo(
        id="gemini-3.1-pro-preview",
        display_name="Gemini 3.1 Pro Preview",
        provider="gemini",
        max_tokens=8192,
    ),
]

# Healthcheck model — use a lightweight, widely-available model
_HEALTHCHECK_MODEL = "gemini-2.5-flash"


class GeminiProvider(BaseProvider):
    """Provider for Google Gemini models."""

    name = "gemini"
    display_name = "Google Gemini"

    def __init__(self) -> None:
        self.api_key: str = ""

    def configure(self, api_key: str, **kwargs: Any) -> None:
        self.api_key = api_key

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if not self.api_key:
            raise ValueError("Gemini API key not configured")

        try:
            from google import genai
        except ImportError:
            raise RuntimeError(
                "google-genai package not installed. Run: pip install google-genai"
            )

        client = genai.Client(api_key=self.api_key)

        # Build contents list — Gemini uses "user" / "model" roles
        contents: list[dict[str, Any]] = []
        for msg in request.messages:
            if msg.role == MessageRole.system:
                # Gemini handles system via config, skip here
                continue
            role = "model" if msg.role == MessageRole.assistant else "user"
            contents.append({"role": role, "parts": [{"text": msg.content}]})

        config: dict[str, Any] = {
            "max_output_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        # For thinking models (2.5-flash, 2.5-pro), disable thinking to ensure
        # all output tokens are used for the actual response content.
        # Without this, thinking tokens consume most of the budget and
        # structured JSON output gets truncated.
        if "2.5" in request.model:
            try:
                from google.genai.types import ThinkingConfig
                config["thinking_config"] = ThinkingConfig(thinking_budget=0)
            except ImportError:
                pass  # older SDK version, skip
        if request.system_prompt:
            config["system_instruction"] = request.system_prompt

        response = await client.aio.models.generate_content(
            model=request.model,
            contents=contents,
            config=config,
        )

        content = response.text or ""
        usage_meta = getattr(response, "usage_metadata", None)

        return CompletionResponse(
            content=content,
            model=request.model,
            provider=self.name,
            usage=TokenUsage(
                prompt_tokens=getattr(usage_meta, "prompt_token_count", 0) or 0,
                completion_tokens=getattr(usage_meta, "candidates_token_count", 0) or 0,
            ),
            finish_reason="stop",
        )

    async def list_models(self) -> list[ModelInfo]:
        return list(_GEMINI_MODELS)

    async def healthcheck(self) -> HealthCheckResult:
        if not self.api_key:
            return HealthCheckResult(
                ok=False,
                provider=self.name,
                message="API key not configured",
            )

        try:
            from google import genai
        except ImportError:
            return HealthCheckResult(
                ok=False,
                provider=self.name,
                message="google-genai package not installed",
            )

        start = time.monotonic()
        try:
            client = genai.Client(api_key=self.api_key)
            await client.aio.models.generate_content(
                model=_HEALTHCHECK_MODEL,
                contents="ping",
                config={"max_output_tokens": 1},
            )
            latency = (time.monotonic() - start) * 1000
            return HealthCheckResult(
                ok=True,
                provider=self.name,
                message="Connected",
                latency_ms=round(latency, 1),
            )
        except Exception as exc:
            msg = str(exc)
            if "API_KEY_INVALID" in msg or "401" in msg:
                return HealthCheckResult(
                    ok=False,
                    provider=self.name,
                    message="Invalid API key",
                )
            return HealthCheckResult(
                ok=False,
                provider=self.name,
                message=f"Connection failed: {type(exc).__name__}",
            )
