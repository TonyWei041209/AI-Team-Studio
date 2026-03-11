"""Provider and model API endpoints (Phase 6A)."""

from fastapi import APIRouter, HTTPException

from models import (
    ProviderInfo,
    ProviderModelInfo,
    ProviderTestRequest,
    ProviderTestResult,
)
from providers.registry import get_registry

router = APIRouter(prefix="/api", tags=["providers"])


@router.get("/providers", response_model=list[ProviderInfo])
async def list_providers():
    """Return metadata for all registered LLM providers."""
    registry = get_registry()
    return [ProviderInfo(**p) for p in registry.list_providers()]


@router.get("/models", response_model=list[ProviderModelInfo])
async def list_models(provider: str | None = None):
    """Return available models, optionally filtered by provider name."""
    registry = get_registry()
    all_models = await registry.list_all_models()

    result = []
    for m in all_models:
        if provider and m.provider != provider:
            continue
        result.append(
            ProviderModelInfo(
                id=m.id,
                display_name=m.display_name,
                provider=m.provider,
                max_tokens=m.max_tokens,
                supports_system_prompt=m.supports_system_prompt,
            )
        )
    return result


@router.post("/provider-test", response_model=ProviderTestResult)
async def test_provider(body: ProviderTestRequest):
    """Test connectivity for a specific provider using its saved settings."""
    registry = get_registry()
    provider = registry.get(body.provider)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {body.provider}")

    result = await registry.healthcheck(body.provider)
    return ProviderTestResult(
        ok=result.ok,
        provider=result.provider,
        message=result.message,
        latency_ms=result.latency_ms,
    )
