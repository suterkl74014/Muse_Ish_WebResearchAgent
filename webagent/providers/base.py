from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol, Any

@dataclass
class Completion:
    text: str
    provider: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)

@dataclass
class ModelInfo:
    provider: str
    id: str
    context_tokens: int | None = None
    max_output_tokens: int | None = None
    input_tokens: int | None = None
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    free: bool | None = None
    source: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)

class ProviderError(RuntimeError):
    def __init__(self, message: str, *, provider: str = "", model: str = "", status: int | None = None,
                 retry_after: float | None = None, kind: str = "provider_error"):
        super().__init__(message)
        self.provider=provider; self.model=model; self.status=status; self.retry_after=retry_after; self.kind=kind

class RateLimitError(ProviderError): pass
class ContextLimitError(ProviderError): pass
class AuthenticationError(ProviderError): pass
class TransientProviderError(ProviderError): pass
class ModelUnavailableError(ProviderError): pass

class Provider(Protocol):
    name: str
    def models(self) -> list[str]: ...
    def model_info(self, model: str) -> ModelInfo: ...
    def complete(self, prompt: str, model: str | None = None, system: str | None = None) -> Completion: ...
