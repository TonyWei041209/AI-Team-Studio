"""Provider registry — discovers, configures, and exposes all providers."""

from __future__ import annotations

from typing import Any

from .base import BaseProvider, HealthCheckResult, ModelInfo
from .anthropic_provider import AnthropicProvider
from .openai_compatible_provider import OpenAICompatibleProvider
from .gemini_provider import GeminiProvider


def _build_default_providers() -> dict[str, BaseProvider]:
    """Instantiate one instance per logical provider."""
    providers: dict[str, BaseProvider] = {}

    # Anthropic
    providers["anthropic"] = AnthropicProvider()

    # OpenAI-compatible family
    for profile in ("openai", "deepseek", "kimi", "minimax"):
        providers[profile] = OpenAICompatibleProvider(profile=profile)

    # Gemini
    providers["gemini"] = GeminiProvider()

    return providers


class ProviderRegistry:
    """Central registry for all LLM providers.

    Usage:
        registry = ProviderRegistry()
        registry.configure("anthropic", api_key="sk-...")
        provider = registry.get("anthropic")
        result = await provider.complete(request)
    """

    def __init__(self) -> None:
        self._providers = _build_default_providers()

    # ── Query ──────────────────────────────────────────────────────

    def list_providers(self) -> list[dict[str, str]]:
        """Return metadata for every registered provider."""
        return [
            {
                "name": p.name,
                "display_name": p.display_name,
            }
            for p in self._providers.values()
        ]

    def get(self, name: str) -> BaseProvider | None:
        """Look up a provider by name."""
        return self._providers.get(name)

    def names(self) -> list[str]:
        """Return all registered provider names."""
        return list(self._providers.keys())

    async def list_all_models(self) -> list[ModelInfo]:
        """Aggregate model lists from all providers."""
        models: list[ModelInfo] = []
        for provider in self._providers.values():
            try:
                models.extend(await provider.list_models())
            except Exception:
                pass  # skip providers that fail to list models
        return models

    # ── Configuration ──────────────────────────────────────────────

    def configure(self, name: str, api_key: str, **kwargs: Any) -> bool:
        """Configure a provider with its API key and optional settings.

        Returns True if the provider was found and configured.
        """
        provider = self._providers.get(name)
        if provider is None:
            return False
        provider.configure(api_key=api_key, **kwargs)
        return True

    def configure_all(self, settings: dict[str, dict[str, Any]]) -> None:
        """Bulk-configure providers from a settings dict.

        Expected format:
            {"anthropic": {"api_key": "sk-...", "enabled": True}, ...}
        """
        for name, cfg in settings.items():
            api_key = cfg.get("api_key", "")
            extra = {k: v for k, v in cfg.items() if k not in ("api_key", "enabled")}
            self.configure(name, api_key=api_key, **extra)

    # ── Health ─────────────────────────────────────────────────────

    async def healthcheck(self, name: str) -> HealthCheckResult:
        """Run a health check on a specific provider."""
        provider = self._providers.get(name)
        if provider is None:
            return HealthCheckResult(
                ok=False,
                provider=name,
                message=f"Unknown provider: {name}",
            )
        return await provider.healthcheck()


# Module-level singleton — initialized once, shared across the app.
_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    """Return the global ProviderRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry
