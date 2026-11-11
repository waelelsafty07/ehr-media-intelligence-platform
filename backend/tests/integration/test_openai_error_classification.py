"""OpenAI error classification, end to end through the real use case (mocked HTTP transport; NO network, NO live API).

HTTP 429 is ambiguous: `insufficient_quota` is permanent (never retried) while a genuine rate limit is transient (bounded retry)."""

import json
import logging

import httpx
import pytest
from openai import OpenAI

from app.application.use_cases.generate_clinical_summary import (
    GenerateClinicalSummaryUseCase,
    SummaryConfig,
)
from app.domain.errors import (
    LLMPermanentError,
    LLMQuotaExhaustedError,
    LLMTimeoutError,
    LLMTransientError,
    SummaryGenerationFailedError,
)
from app.domain.ports.llm import LLMRequest
from app.infrastructure.llm.openai_adapter import OpenAIAdapter
from tests.fakes import FakeStore, FakeUnitOfWork
from tests.fhir_helpers import make_patient
from tests.summary_helpers import as_json, good_output, records

KEY = "sk-test-0123456789abcdef"  # FAKE fixture
SECRET_TEXT = "SECRET-PROVIDER-DETAIL org-abc123 req_999"
REQ = LLMRequest(system_prompt="s", user_prompt="u", max_output_tokens=10)
ATTEMPTS = 3


def err(status, code=None, typ="x"):
    body = {"error": {"message": f"{SECRET_TEXT} {KEY}", "type": typ, "code": code}}
    return lambda request: httpx.Response(status, json=body)


def adapter(handler, calls):
    def counting(request):
        calls.append(1)
        return handler(request)

    client = OpenAI(api_key=KEY, timeout=5, max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(counting)))
    return OpenAIAdapter(KEY, "gpt-test", client=client)


@pytest.fixture
def run():
    """Runs the real use case against the real adapter; returns (result_or_exc, provider_calls, sleeps)."""
    def go(handler):
        store = FakeStore()
        p = make_patient()
        store.patients[p.patient_id] = p
        for r in records():
            store.records[r.record_id] = r
        calls, sleeps = [], []
        uc = GenerateClinicalSummaryUseCase(lambda: FakeUnitOfWork(store), adapter(handler, calls),
                                            SummaryConfig(max_attempts=ATTEMPTS, retry_base_seconds=1.0), sleeper=sleeps.append)
        try:
            out = uc.execute(p.patient_id)
        except SummaryGenerationFailedError as exc:
            out = exc
        return out, len(calls), sleeps
    return go


# ------------------------------------------------------------------ adapter-level classification
@pytest.mark.parametrize("status,code,typ,exc", [
    (429, "insufficient_quota", "insufficient_quota", LLMQuotaExhaustedError),
    (429, "insufficient_quota", "x", LLMQuotaExhaustedError),           # code alone is enough
    (429, None, "insufficient_quota", LLMQuotaExhaustedError),          # type alone is enough
    (429, "rate_limit_exceeded", "requests", LLMTransientError),
    (429, None, "x", LLMTransientError),                                 # unknown 429 stays retryable
    (401, "invalid_api_key", "invalid_request_error", LLMPermanentError),
    (403, "unsupported_country_region_territory", "x", LLMPermanentError),
    (404, "model_not_found", "invalid_request_error", LLMPermanentError),
    (500, None, "server_error", LLMTransientError),
    (503, None, "x", LLMTransientError),
])
def test_status_and_structured_code_classification(status, code, typ, exc):
    with pytest.raises(exc) as e:
        adapter(err(status, code, typ), []).generate(REQ)
    assert type(e.value).__mro__[0] is exc or isinstance(e.value, exc)
    if exc is LLMTransientError:
        assert not isinstance(e.value, LLMPermanentError)
    if exc is LLMPermanentError:
        assert not isinstance(e.value, LLMTransientError)
    for secret in (KEY, "SECRET-PROVIDER-DETAIL", "org-abc123", "req_999"):
        assert secret not in str(e.value) and secret not in repr(e.value)
    assert e.value.__cause__ is None


def test_quota_exhaustion_is_permanent_not_transient():
    with pytest.raises(LLMQuotaExhaustedError) as e:
        adapter(err(429, "insufficient_quota"), []).generate(REQ)
    assert isinstance(e.value, LLMPermanentError) and not isinstance(e.value, LLMTransientError)
    assert e.value.category == "provider_quota_exhausted"


def test_non_json_or_non_string_error_fields_are_handled_safely():
    for handler in (lambda r: httpx.Response(429, text="<html>busy</html>"),
                    lambda r: httpx.Response(429, json={"error": {"code": {"nested": 1}, "type": 5}}),
                    lambda r: httpx.Response(429, json={"error": {"code": "x" * 500}})):
        with pytest.raises(LLMTransientError):
            adapter(handler, []).generate(REQ)


def test_timeout_and_malformed_success_body():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMTimeoutError):
        adapter(timeout, []).generate(REQ)
    with pytest.raises(LLMTransientError):
        adapter(lambda r: httpx.Response(200, text="<html>proxy error</html>"), []).generate(REQ)


# ------------------------------------------------------------------ retry policy through the real use case
@pytest.mark.parametrize("status,code,typ", [
    (429, "insufficient_quota", "insufficient_quota"), (401, "invalid_api_key", "x"), (403, None, "x"),
    (404, "model_not_found", "x"), (400, None, "x"),
])
def test_permanent_failures_make_exactly_one_provider_attempt_and_never_sleep(run, status, code, typ):
    out, calls, sleeps = run(err(status, code, typ))
    assert isinstance(out, SummaryGenerationFailedError)
    assert calls == 1 and sleeps == []
    assert out.reason == ("provider_quota_exhausted" if code == "insufficient_quota" else "provider_rejected")


@pytest.mark.parametrize("handler,reason", [
    (err(429, "rate_limit_exceeded", "requests"), "provider_unavailable"),
    (err(500, None, "server_error"), "provider_unavailable"),
    (err(503), "provider_unavailable"),
    (lambda r: (_ for _ in ()).throw(httpx.ConnectError("down", request=r)), "provider_unavailable"),
    (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)), "provider_timeout"),
    (lambda r: httpx.Response(200, text="<html>bad</html>"), "provider_unavailable"),
])
def test_transient_failures_use_the_bounded_retry_policy(run, handler, reason):
    out, calls, sleeps = run(handler)
    assert isinstance(out, SummaryGenerationFailedError) and out.reason == reason
    assert calls == ATTEMPTS                       # bounded: exactly max_attempts, no more
    assert sleeps == [1.0, 2.0]                    # exponential backoff, none after the last attempt


def test_transient_then_success_still_works(run):
    state = {"n": 0}
    r1, r2 = records()
    good = as_json(good_output(r1.record_id, r2.record_id))

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded", "type": "requests", "message": "m"}})
        return httpx.Response(200, json={"id": "c", "object": "chat.completion", "created": 1, "model": "gpt-test",
                                         "choices": [{"index": 0, "finish_reason": "stop",
                                                      "message": {"role": "assistant", "content": good}}]})

    out, calls, _ = run(handler)
    assert not isinstance(out, Exception) and calls == 2


# ------------------------------------------------------------------ no provider details leak
def test_no_provider_details_reach_exceptions_or_logs(run, caplog):
    caplog.set_level(logging.DEBUG)
    for status, code in [(429, "insufficient_quota"), (429, "rate_limit_exceeded"), (401, "invalid_api_key"), (500, None)]:
        out, _, _ = run(err(status, code))
        assert isinstance(out, SummaryGenerationFailedError)
        assert SECRET_TEXT.split()[0] not in str(out) and KEY not in str(out)
    logged = " ".join(f"{r.getMessage()} {json.dumps(getattr(r, '__dict__', {}), default=str)}" for r in caplog.records)
    assert caplog.records
    for secret in (KEY, "SECRET-PROVIDER-DETAIL", "org-abc123", "req_999"):
        assert secret not in logged


def test_quota_failure_surfaces_a_safe_reason_through_the_api(client):
    from tests.summary_helpers import R1_RAW, R2_RAW
    rows = [{**R1_RAW, "name": "Alex Test", "dob": "1980-03-14"}, {**R2_RAW, "name": "Alex Test", "dob": "1980-03-14"}]
    pid = client.post("/ingest", files={"file": ("a.json", json.dumps(rows).encode())}).json()["results"][0]["patient_id"]
    client.app.state.llm_provider = adapter(err(429, "insufficient_quota", "insufficient_quota"), calls := [])
    r = client.post(f"/patients/{pid}/summary")
    assert r.status_code == 502 and r.json()["error"]["code"] == "summary_generation_failed"
    assert r.json()["error"]["details"] == [{"reason": "provider_quota_exhausted"}]
    assert len(calls) == 1
    for secret in (KEY, "SECRET-PROVIDER-DETAIL", "org-abc123", "req_999"):
        assert secret not in r.text
