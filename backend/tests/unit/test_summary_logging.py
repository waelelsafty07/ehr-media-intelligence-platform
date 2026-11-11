"""Logs must not contain API keys or clinical content (names, diagnoses, note text, model output)."""

import itertools
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from openai import OpenAI

from app.application.use_cases.generate_clinical_summary import (
    GenerateClinicalSummaryUseCase,
    SummaryConfig,
)
from app.core.logging import JsonFormatter
from app.domain.errors import LLMTransientError, SummaryGenerationFailedError
from app.domain.ports.llm import LLMRequest
from app.infrastructure.llm.openai_adapter import OpenAIAdapter
from tests.fakes import FakeStore, FakeUnitOfWork
from tests.fhir_helpers import make_patient
from tests.summary_helpers import ScriptedLLM, as_json, good_output, records

PID = "11111111-1111-4111-8111-111111111111"
KEY = "sk-live-looking-0123456789abcdef"  # FAKE test fixture, not a credential
CLINICAL = ["chest tightness", "hypertension", "angina", "stress test", "Alex", "Test", "1980", "000101"]


def everything_logged(caplog) -> str:
    """Rendered message + every extra field of every record, as raw strings, plus the JSON formatter output."""
    parts = []
    fmt = JsonFormatter()
    for rec in caplog.records:
        parts.append(rec.getMessage())
        parts.append(" ".join(str(v) for k, v in rec.__dict__.items() if k not in {"args", "msg"}))
        parts.append(fmt.format(rec))
        if rec.exc_info:
            parts.append(str(rec.exc_info[1]))
    return "\n".join(parts)


@pytest.fixture
def store():
    s = FakeStore()
    s.patients[PID] = make_patient()
    for r in records():
        s.records[r.record_id] = r
    return s


def use_case(store, llm):
    ticks = itertools.count()
    return GenerateClinicalSummaryUseCase(
        lambda: FakeUnitOfWork(store), llm, SummaryConfig(retry_base_seconds=0),
        clock=lambda: datetime(2025, 3, 1, tzinfo=UTC) + timedelta(seconds=next(ticks)), sleeper=lambda s: None)


def test_success_failure_and_rejected_output_paths_log_no_keys_or_clinical_content(store, caplog):
    r1, r2 = records()
    ok = as_json(good_output(r1.record_id, r2.record_id))
    bad = good_output(r1.record_id, r2.record_id)
    bad["key_diagnoses"][0]["evidence_quote"] = "severe hypertension crisis with chest pain"   # model output with clinical text
    caplog.set_level(logging.DEBUG)
    use_case(store, ScriptedLLM(ok)).execute(PID)                                              # miss + generate
    use_case(store, ScriptedLLM(ok)).execute(PID)                                              # cache hit
    with pytest.raises(SummaryGenerationFailedError):                                          # rejected model output
        use_case(store, ScriptedLLM(as_json(bad), model="m2")).execute(PID)
    with pytest.raises(SummaryGenerationFailedError):                                          # provider error echoing a key
        use_case(store, ScriptedLLM(LLMTransientError(f"Incorrect API key provided: {KEY}"), model="m3")).execute(PID)
    text = everything_logged(caplog).lower()
    assert caplog.records, "expected log output to inspect"
    assert KEY.lower() not in text and "sk-" not in text
    for term in CLINICAL:
        assert term.lower() not in text, term
    assert "summary_cache_hit" in text and "summary_output_rejected" in text      # events are logged, content is not


def test_openai_adapter_logs_no_key_or_response_text(caplog):
    def handler(request: httpx.Request):
        return httpx.Response(429, json={"error": {"message": f"Rate limit for key {KEY}: chest tightness", "type": "x"}})

    client = OpenAI(api_key=KEY, timeout=5, max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(LLMTransientError):
        OpenAIAdapter(KEY, "m", client=client).generate(LLMRequest("sys chest tightness", "user hypertension", 10))
    text = everything_logged(caplog).lower()
    assert KEY.lower() not in text and "chest tightness" not in text and "hypertension" not in text
