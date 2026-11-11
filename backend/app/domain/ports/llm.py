"""LLM provider port. Implementations (OpenAI, ...) live in infrastructure and raise the
LLMProviderError family from app.domain.errors; no provider types cross this boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMRequest:
    system_prompt: str
    user_prompt: str
    max_output_tokens: int
    temperature: float | None = 0.0
    expect_json: bool = True


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str  # model that actually answered (providers may return a dated snapshot name)
    finish_reason: str | None = None  # "stop" is normal; "length" means the output was cut off
    total_tokens: int | None = None


class LLMProvider(Protocol):
    model_identifier: str  # configured model; part of the summary cache key

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Raises LLMTransientError / LLMTimeoutError / LLMPermanentError / LLMNotConfiguredError."""
        ...
