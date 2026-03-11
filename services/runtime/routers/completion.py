"""Manual completion endpoint for testing providers (Phase 6B).

POST /api/completion — send a prompt to any configured provider and get
the raw response back.  This is a debug/test endpoint, NOT part of the
orchestrator pipeline.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from providers.base import CompletionRequest, Message, MessageRole
from providers.registry import get_registry

router = APIRouter(prefix="/api", tags=["completion"])


# ── Request / response schemas ─────────────────────────────────


class CompletionTestRequest(BaseModel):
    provider: str
    model: str
    prompt: str
    system_prompt: str = ""
    max_tokens: int = Field(1024, ge=1, le=16384)
    temperature: float = Field(0.7, ge=0.0, le=2.0)


class CompletionTestResponse(BaseModel):
    content: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"


# ── Endpoint ───────────────────────────────────────────────────


@router.post("/completion", response_model=CompletionTestResponse)
async def test_completion(body: CompletionTestRequest):
    """Send a completion request to a configured provider.

    Useful for manual testing of provider connectivity and model behaviour.
    Returns the raw model response.
    """
    registry = get_registry()
    provider = registry.get(body.provider)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {body.provider}")
    if not getattr(provider, "api_key", ""):
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{body.provider}' not configured. Set API key in Settings.",
        )

    request = CompletionRequest(
        model=body.model,
        messages=[Message(role=MessageRole.user, content=body.prompt)],
        system_prompt=body.system_prompt or None,
        max_tokens=body.max_tokens,
        temperature=body.temperature,
    )

    try:
        response = await provider.complete(request)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Provider error: {type(exc).__name__}: {exc}",
        )

    return CompletionTestResponse(
        content=response.content,
        model=response.model,
        provider=response.provider,
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
        finish_reason=response.finish_reason,
    )
