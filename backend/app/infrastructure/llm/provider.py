"""Provider selection from settings (composition-root helper)."""

from __future__ import annotations

from app.core.config import Settings
from app.domain.errors import LLMNotConfiguredError
from app.domain.ports.llm import LLMProvider, LLMRequest, LLMResponse
from app.infrastructure.llm.openai_adapter import OpenAIAdapter


class UnconfiguredLLMProvider:
    """Used when no API key is set: the app still starts; summary generation answers 503."""

    def __init__(self, model_identifier: str) -> None:
        self.model_identifier = model_identifier

    def generate(self, request: LLMRequest) -> LLMResponse:
        raise LLMNotConfiguredError()


def build_llm_provider(settings: Settings) -> LLMProvider:
    key = settings.openai_api_key.get_secret_value().strip() if settings.openai_api_key else ""
    if not key:
        return UnconfiguredLLMProvider(settings.openai_model)
    return OpenAIAdapter(key, settings.openai_model, settings.openai_timeout_seconds)
