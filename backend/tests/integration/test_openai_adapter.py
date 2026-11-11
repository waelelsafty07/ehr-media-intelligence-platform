"""OpenAIAdapter against the REAL openai SDK over a mocked HTTP transport (no network, no credentials).
This verifies request building, response parsing and error mapping. It does not prove live-API behavior."""

import json

import httpx
import pytest
from openai import OpenAI

from app.core.config import Settings
from app.domain.errors import (
    LLMNotConfiguredError,
    LLMPermanentError,
    LLMTimeoutError,
    LLMTransientError,
)
from app.domain.ports.llm import LLMRequest
from app.infrastructure.llm.openai_adapter import OpenAIAdapter
from app.infrastructure.llm.provider import UnconfiguredLLMProvider, build_llm_provider

REQ = LLMRequest(system_prompt="SYS", user_prompt="USER JSON", max_output_tokens=123, temperature=0.0)
KEY = "sk-test-0123456789abcdef"  # FAKE test fixture, not a credential


def completion(content="{}", finish="stop", model="gpt-test-2025-01-01", usage=True):
    body = {"id": "c1", "object": "chat.completion", "created": 1, "model": model,
            "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}]}
    if usage:
        body["usage"] = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
    return body


def adapter(handler):
    client = OpenAI(api_key=KEY, timeout=5, max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return OpenAIAdapter(KEY, "gpt-test", client=client)


def test_success_builds_the_expected_request_and_parses_the_response():
    seen = {}

    def handler(request: httpx.Request):
        seen["body"], seen["path"], seen["auth"] = json.loads(request.content), request.url.path, request.headers["authorization"]
        return httpx.Response(200, json=completion('{"summary_text": "ok"}'))

    resp = adapter(handler).generate(REQ)
    assert resp.text == '{"summary_text": "ok"}' and resp.model == "gpt-test-2025-01-01"
    assert resp.finish_reason == "stop" and resp.total_tokens == 8
    body = seen["body"]
    assert seen["path"].endswith("/chat/completions") and seen["auth"] == f"Bearer {KEY}"
    assert body["model"] == "gpt-test" and body["temperature"] == 0.0 and body["max_completion_tokens"] == 123
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER JSON"}]


def test_temperature_and_json_mode_are_optional():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=completion())

    adapter(handler).generate(LLMRequest("s", "u", 10, temperature=None, expect_json=False))
    assert "temperature" not in seen and "response_format" not in seen


@pytest.mark.parametrize("status,exc", [
    (429, LLMTransientError), (500, LLMTransientError), (503, LLMTransientError), (408, LLMTransientError),
    (400, LLMPermanentError), (401, LLMPermanentError), (403, LLMPermanentError), (404, LLMPermanentError),
    (422, LLMPermanentError),
])
def test_http_errors_are_classified(status, exc):
    def handler(request):
        return httpx.Response(status, json={"error": {"message": f"Incorrect API key provided: {KEY}", "type": "x"}})

    with pytest.raises(exc) as e:
        adapter(handler).generate(REQ)
    assert type(e.value) is exc or isinstance(e.value, exc)
    assert KEY not in str(e.value) and "sk-" not in str(e.value) and e.value.__cause__ is None   # nothing leaks


def test_timeouts_and_connection_errors_are_transient():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(LLMTimeoutError) as e:
        adapter(timeout).generate(REQ)
    assert e.value.category == "provider_timeout" and isinstance(e.value, LLMTransientError)
    with pytest.raises(LLMTransientError) as e2:
        adapter(refused).generate(REQ)
    assert not isinstance(e2.value, LLMTimeoutError)


def test_empty_or_filtered_responses_are_returned_for_the_pipeline_to_reject():
    r = adapter(lambda req: httpx.Response(200, json={**completion(), "choices": []})).generate(REQ)
    assert r.text == "" and r.finish_reason == "no_choices"
    r = adapter(lambda req: httpx.Response(200, json=completion(content=None, finish="content_filter"))).generate(REQ)
    assert r.text == "" and r.finish_reason == "content_filter"
    r = adapter(lambda req: httpx.Response(200, json=completion("{}", finish="length", usage=False))).generate(REQ)
    assert r.finish_reason == "length" and r.total_tokens is None


def test_garbage_http_body_becomes_a_provider_error_not_a_crash():
    with pytest.raises(LLMTransientError) as e:
        adapter(lambda req: httpx.Response(200, content=b"<html>not json</html>")).generate(REQ)
    assert "html" not in str(e.value).lower()


def test_sdk_retries_are_disabled_so_the_use_case_owns_retry_policy():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500, json={"error": {"message": "x"}})

    with pytest.raises(LLMTransientError):
        adapter(handler).generate(REQ)
    assert len(calls) == 1


# ---------------------------------------------------------------- provider selection from settings
def test_no_api_key_gives_an_unconfigured_provider_that_fails_safely():
    p = build_llm_provider(Settings(_env_file=None))
    assert isinstance(p, UnconfiguredLLMProvider) and p.model_identifier == "gpt-4o-mini"
    with pytest.raises(LLMNotConfiguredError):
        p.generate(REQ)
    assert isinstance(build_llm_provider(Settings(openai_api_key="   ", _env_file=None)), UnconfiguredLLMProvider)


def test_api_key_creates_the_openai_adapter_and_never_appears_in_repr():
    s = Settings(openai_api_key=KEY, openai_model="my-model", _env_file=None)
    p = build_llm_provider(s)
    assert isinstance(p, OpenAIAdapter) and p.model_identifier == "my-model"
    assert KEY not in repr(s) and KEY not in str(s.openai_api_key)
