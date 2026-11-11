"""OpenAI adapter for the LLMProvider port (Chat Completions, JSON mode).

* Retries are NOT done here (max_retries=0): the use case owns retry/backoff so it is explicit and testable.
* SDK/provider exception text is never propagated or logged: it can echo credentials (observed with a 429
  message containing a key-like fragment) or request content. Errors become generic categorized domain errors.
* Verified against the real SDK over a mocked HTTP transport in tests; it has NOT been exercised against the live API.
"""

from __future__ import annotations

import logging

import openai
from openai import OpenAI

from app.domain.errors import (
    LLMPermanentError,
    LLMProviderError,
    LLMQuotaExhaustedError,
    LLMTimeoutError,
    LLMTransientError,
)
from app.domain.ports.llm import LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

_TRANSIENT_STATUS = {408, 409, 425, 429}
# HTTP 429 is used for BOTH a temporary rate limit (retry) and exhausted billing/quota (never retry). Only the
# structured `code`/`type` fields tell them apart; the human-readable message is never inspected or propagated.
_QUOTA_CODES = {"insufficient_quota"}


def _structured(exc: openai.APIStatusError, field: str) -> str | None:
    """A short machine code from the SDK's parsed error body, or None. Never returns free text from the provider."""
    value = getattr(exc, field, None)
    if value is None and isinstance(exc.body, dict):
        value = exc.body.get(field)
    return value if isinstance(value, str) and len(value) <= 64 else None


def _is_quota_exhausted(exc: openai.APIStatusError) -> bool:
    return exc.status_code == 429 and bool({_structured(exc, "code"), _structured(exc, "type")} & _QUOTA_CODES)


class OpenAIAdapter:
    def __init__(self, api_key: str, model: str, timeout_seconds: float = 30.0,
                 client: OpenAI | None = None) -> None:
        self.model_identifier = model
        self._client = client or OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)

    def generate(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict = {
            "model": self.model_identifier,
            "messages": [{"role": "system", "content": request.system_prompt},
                         {"role": "user", "content": request.user_prompt}],
            "max_completion_tokens": request.max_output_tokens,
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.expect_json:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            completion = self._client.chat.completions.create(**kwargs)
        except openai.APITimeoutError:  # subclass of APIConnectionError: must be matched first
            raise LLMTimeoutError("The provider request timed out.") from None
        except openai.APIConnectionError:
            raise LLMTransientError("The provider could not be reached.") from None
        except openai.APIStatusError as exc:
            status = exc.status_code
            logger.warning("openai_status_error", extra={"status": status})
            if _is_quota_exhausted(exc):
                raise LLMQuotaExhaustedError("The provider account has no remaining quota or billing.") from None
            if status in _TRANSIENT_STATUS or status >= 500:
                raise LLMTransientError("The provider is temporarily unavailable.") from None
            raise LLMPermanentError("The provider rejected the request.") from None
        except openai.OpenAIError:
            raise LLMPermanentError("The provider request failed.") from None
        except Exception:  # noqa: BLE001 - never let an SDK-internal error leak unclassified
            raise LLMProviderError("Unexpected provider error.") from None

        try:
            choice = completion.choices[0] if completion.choices else None
            content = choice.message.content if choice and choice.message else None
            return LLMResponse(
                text=content or "", model=completion.model or self.model_identifier,
                finish_reason=choice.finish_reason if choice else "no_choices",
                total_tokens=completion.usage.total_tokens if completion.usage else None)
        except (AttributeError, TypeError, IndexError):
            # e.g. HTTP 200 with a non-JSON body (proxy error page): the SDK returns a plain string
            raise LLMTransientError("The provider returned an unreadable response.") from None
