"""Base provider interface and shared data types.

All LLM providers implement BaseProvider to ensure a consistent
complete / list_models / healthcheck contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Data types ────────────────────────────────────────────────────

class MessageRole(str, Enum):
    system = "system"
    user = "user"
    assistant = "assistant"


@dataclass(frozen=True)
class Message:
    role: MessageRole
    content: str


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class CompletionRequest:
    """Unified request sent to any provider."""
    model: str
    messages: list[Message]
    max_tokens: int = 4096
    temperature: float = 0.7
    system_prompt: str | None = None


@dataclass(frozen=True)
class CompletionResponse:
    """Unified response returned by any provider."""
    content: str
    model: str
    provider: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str = "stop"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelInfo:
    """Metadata about a model offered by a provider."""
    id: str
    display_name: str
    provider: str
    max_tokens: int = 4096
    supports_system_prompt: bool = True


@dataclass(frozen=True)
class HealthCheckResult:
    """Result of a provider connectivity test."""
    ok: bool
    provider: str
    message: str = ""
    latency_ms: float = 0.0


# ── Abstract base ─────────────────────────────────────────────────

class BaseProvider(ABC):
    """Abstract base class for LLM providers.

    Subclasses must implement complete(), list_models(), healthcheck().
    """

    name: str = ""              # e.g. "anthropic"
    display_name: str = ""      # e.g. "Anthropic (Claude)"

    @abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Send a completion request and return the response."""

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Return the list of models available from this provider."""

    @abstractmethod
    async def healthcheck(self) -> HealthCheckResult:
        """Test connectivity and API key validity."""

    def configure(self, api_key: str, **kwargs: Any) -> None:
        """Apply runtime configuration (API key, base URL, etc.).

        Called by the registry when settings are loaded.
        """
        self.api_key = api_key
        for k, v in kwargs.items():
            setattr(self, k, v)


def mask_api_key(key: str | None) -> str:
    """Return a masked version of an API key for display.

    Examples:
        sk-ant-abc...xyz  → sk-****xyz
        short             → ****
        None / ""         → (not set)
    """
    if not key:
        return "(not set)"
    if len(key) <= 8:
        return "****"
    return key[:3] + "****" + key[-4:]
