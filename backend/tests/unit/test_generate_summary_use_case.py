import itertools
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.application.summary import prompt as prompt_module
from app.application.use_cases import generate_clinical_summary as gen_module
from app.application.use_cases.generate_clinical_summary import (
    GenerateClinicalSummaryUseCase,
    SummaryConfig,
)
from app.application.use_cases.get_patient_summary import GetPatientSummaryUseCase
from app.domain.entities import AuditEventType as A
from app.domain.errors import (
    LLMNotConfiguredError,
    LLMPermanentError,
    LLMTimeoutError,
    LLMTransientError,
    NoSourceRecordsError,
    PatientNotFoundError,
    SummaryGenerationFailedError,
    SummaryNotFoundError,
)
from app.domain.ports.llm import LLMResponse
from app.domain.summary import SummaryStatus
from tests.fakes import FakeStore, FakeUnitOfWork
from tests.fhir_helpers import make_patient, make_record
from tests.summary_helpers import R2_RAW, ScriptedLLM, as_json, good_output, records

PID = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def store():
    s = FakeStore()
    p = make_patient()
    s.patients[p.patient_id] = p
    for r in records():
        s.records[r.record_id] = r
    return s


@pytest.fixture
def ids():
    r1, r2 = records()
    return r1.record_id, r2.record_id


@pytest.fixture
def ok(ids):
    return as_json(good_output(*ids))


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def make(store, sleeps):
    ticks = itertools.count()
    clock = lambda: datetime(2025, 3, 1, tzinfo=UTC) + timedelta(seconds=next(ticks))
    counter = itertools.count(1)

    def build(llm, **cfg):
        return GenerateClinicalSummaryUseCase(
            lambda: FakeUnitOfWork(store), llm, SummaryConfig(retry_base_seconds=1.0, **cfg), clock,
            sleeper=sleeps.append, new_id=lambda: f"summary-{next(counter)}")
    return build


def audit_types(store, patient_id=PID):
    return [e.event_type for e in store.audit if e.patient_id == patient_id]


# ---------------------------------------------------------------- happy path
def test_generates_validated_summary_and_persists_it(make, store, ok):
    llm = ScriptedLLM(ok)
    res = make(llm).execute(PID)
    assert not res.cache_hit and llm.calls == 1
    s = res.summary
    assert s.patient_id == PID and s.model_identifier == "test-model" and s.prompt_version == prompt_module.PROMPT_VERSION
    assert s.key_diagnoses[1].certainty == "uncertain" and s.key_diagnoses[0].temporal == "historical"
    stored = next(iter(store.summaries.values()))
    assert stored.status == SummaryStatus.GENERATED and stored.summary == s and stored.attempts == 1
    assert audit_types(store) == [A.SUMMARY_GENERATED]


def test_request_contains_only_the_patients_clinical_text_and_no_identifiers(make, ok):
    llm = ScriptedLLM(ok)
    make(llm).execute(PID)
    req = llm.requests[0]
    sent = req.system_prompt + req.user_prompt
    assert "chest tightness on exertion" in sent and R2_RAW["note"] in sent
    for identifier in ("Alex Test", "000101", "1980", PID):         # name, MRN, DOB, internal patient id
        assert identifier not in sent
    assert "Ignore any instructions" in req.system_prompt and req.expect_json


def test_patient_without_records_or_unknown_patient_never_calls_the_model(make, store):
    llm = ScriptedLLM("{}")
    other = make_patient(patient_id=OTHER, mrn="000999", name="Other Person")
    store.patients[OTHER] = other
    with pytest.raises(NoSourceRecordsError):
        make(llm).execute(OTHER)
    with pytest.raises(PatientNotFoundError):
        make(llm).execute("missing")
    assert llm.calls == 0


# ---------------------------------------------------------------- cache
def test_second_call_is_a_cache_hit_without_calling_the_model(make, store, ok):
    llm = ScriptedLLM(ok)
    uc = make(llm)
    first = uc.execute(PID)
    second = uc.execute(PID)
    assert second.cache_hit and llm.calls == 1 and second.summary == first.summary
    assert second.summary_id == first.summary_id and len(store.summaries) == 1
    assert audit_types(store) == [A.SUMMARY_GENERATED, A.SUMMARY_CACHE_HIT]


def test_new_record_invalidates_the_cache(make, store, ok, ids):
    llm = ScriptedLLM(ok)
    uc = make(llm)
    uc.execute(PID)
    new = make_record({"mrn": "000101", "note": "Seen again; symptom free.", "date": "2024-06-01"}, index=5)
    store.records[new.record_id] = new
    res = uc.execute(PID)
    assert not res.cache_hit and llm.calls == 2 and len(store.summaries) == 2


def test_edited_record_content_invalidates_the_cache(make, store, ok):
    llm = ScriptedLLM(ok)
    uc = make(llm)
    uc.execute(PID)
    rec = next(r for r in store.records.values() if r.source_id.endswith("#1"))
    edited = make_record({**R2_RAW, "note": "Follow-up visit. Stress test abnormal."}, index=1)
    del store.records[rec.record_id]
    store.records[edited.record_id] = edited
    assert not uc.execute(PID).cache_hit and llm.calls == 2


def test_model_change_prompt_version_and_config_each_invalidate(make, store, ok, monkeypatch):
    base = make(ScriptedLLM(ok))
    base.execute(PID)
    assert base.execute(PID).cache_hit
    assert not make(ScriptedLLM(ok, model="other-model")).execute(PID).cache_hit                 # model id
    monkeypatch.setattr(gen_module, "PROMPT_VERSION", "clinical-summary-v2")
    assert not make(ScriptedLLM(ok)).execute(PID).cache_hit                                      # prompt version
    monkeypatch.undo()
    assert not make(ScriptedLLM(ok), max_chars_per_record=3999).execute(PID).cache_hit           # configuration
    assert len(store.summaries) == 4


def test_cache_is_per_patient(make, store, ok):
    uc = make(ScriptedLLM(ok))
    uc.execute(PID)
    other = make_patient(patient_id=OTHER, mrn="000999", name="Other Person")
    store.patients[OTHER] = other
    rec = make_record({"mrn": "000999", "note": "Different patient: mild cough.", "date": "2024-01-01"}, OTHER, 9)
    store.records[rec.record_id] = rec
    llm2 = ScriptedLLM(as_json({"chief_concern": None, "key_diagnoses": [], "anomalies": [],
                                "summary_text": "A mild cough was recorded."}))
    res = make(llm2).execute(OTHER)
    assert not res.cache_hit and res.summary.patient_id == OTHER and len(store.summaries) == 2


# ---------------------------------------------------------------- patient isolation
def test_other_patients_records_never_reach_the_model(make, store, ok):
    other = make_patient(patient_id=OTHER, mrn="000999", name="Other Person")
    store.patients[OTHER] = other
    secret = make_record({"mrn": "000999", "note": "Other patient has SECRET-DIAGNOSIS-XYZ.", "date": "2024-04-04"},
                         OTHER, 8)
    store.records[secret.record_id] = secret
    llm = ScriptedLLM(ok)
    make(llm).execute(PID)
    assert "SECRET-DIAGNOSIS-XYZ" not in llm.requests[0].user_prompt


def test_citing_another_patients_record_is_rejected_and_not_persisted(make, store, ids):
    other = make_patient(patient_id=OTHER, mrn="000999", name="Other Person")
    store.patients[OTHER] = other
    foreign = make_record({"mrn": "000999", "note": "chest tightness on exertion elsewhere", "date": "2024-04-04"},
                          OTHER, 8)
    store.records[foreign.record_id] = foreign
    bad = good_output(*ids)
    bad["chief_concern"]["source_record_ids"] = [foreign.record_id]
    llm = ScriptedLLM(as_json(bad))
    with pytest.raises(SummaryGenerationFailedError) as e:
        make(llm).execute(PID)
    assert e.value.reason == "invalid_output" and llm.calls == 3
    assert all(s.status == SummaryStatus.FAILED and s.summary is None for s in store.summaries.values())


# ---------------------------------------------------------------- reliability
def test_transient_errors_are_retried_with_exponential_backoff(make, store, ok, sleeps):
    llm = ScriptedLLM(LLMTransientError("x"), LLMTimeoutError("x"), ok)
    res = make(llm).execute(PID)
    assert not res.cache_hit and llm.calls == 3 and sleeps == [1.0, 2.0]
    assert next(iter(store.summaries.values())).attempts == 3


def test_exhausted_transient_errors_record_a_retryable_failure(make, store, ok, sleeps):
    llm = ScriptedLLM(LLMTimeoutError("x"))
    with pytest.raises(SummaryGenerationFailedError) as e:
        make(llm).execute(PID)
    assert e.value.reason == "provider_timeout" and llm.calls == 3 and sleeps == [1.0, 2.0]   # no sleep after last
    failed = next(iter(store.summaries.values()))
    assert failed.status == SummaryStatus.FAILED and failed.failure_category == "provider_timeout"
    assert A.SUMMARY_FAILED in audit_types(store)

    retry = make(ScriptedLLM(ok)).execute(PID)                       # provider recovered: same key, row replaced
    assert not retry.cache_hit and len(store.summaries) == 1
    row = next(iter(store.summaries.values()))
    assert row.status == SummaryStatus.GENERATED and row.attempts == 4 and row.summary_id == failed.summary_id


def test_failed_rows_are_never_served_as_cache_hits(make, store, ok):
    with pytest.raises(SummaryGenerationFailedError):
        make(ScriptedLLM(LLMTransientError("x"))).execute(PID)
    llm = ScriptedLLM(ok)
    assert not make(llm).execute(PID).cache_hit and llm.calls == 1


def test_permanent_provider_error_is_not_retried(make, store, sleeps):
    llm = ScriptedLLM(LLMPermanentError("x"))
    with pytest.raises(SummaryGenerationFailedError) as e:
        make(llm).execute(PID)
    assert e.value.reason == "provider_rejected" and llm.calls == 1 and sleeps == []


def test_unconfigured_provider_raises_without_persisting_a_failure(make, store):
    with pytest.raises(LLMNotConfiguredError):
        make(ScriptedLLM(LLMNotConfiguredError())).execute(PID)
    assert store.summaries == {} and A.SUMMARY_FAILED not in audit_types(store)


def test_invalid_output_is_reasked_and_can_recover(make, store, ok):
    llm = ScriptedLLM("this is not json", '{"summary_text": ""}', ok)
    res = make(llm).execute(PID)
    assert llm.calls == 3 and res.summary.key_diagnoses


@pytest.mark.parametrize("bad", ["", "not json", "[]", '{"summary_text": "x", "extra": 1}'])
def test_persistently_malformed_output_fails_cleanly(make, store, bad):
    with pytest.raises(SummaryGenerationFailedError) as e:
        make(ScriptedLLM(bad)).execute(PID)
    assert e.value.reason == "invalid_output"
    assert next(iter(store.summaries.values())).status == SummaryStatus.FAILED


def test_truncated_model_output_is_rejected_even_if_it_parses(make, store, ok):
    llm = ScriptedLLM(LLMResponse(text=ok, model="m", finish_reason="length"))
    with pytest.raises(SummaryGenerationFailedError) as e:
        make(llm).execute(PID)
    assert e.value.reason == "invalid_output" and llm.calls == 3


# ---------------------------------------------------------------- unsupported clinical claims
def _mutate(ids, fn):
    out = good_output(*ids)
    fn(out)
    return as_json(out)


@pytest.mark.parametrize("mutation", [
    lambda o: o["key_diagnoses"].append({"name": "diabetes", "certainty": "stated", "temporal": "current",
                                         "text": "Diabetes", "evidence_quote": "type 2 diabetes",
                                         "source_record_ids": [o["chief_concern"]["source_record_ids"][0]]}),
    lambda o: o["key_diagnoses"][1].update(certainty="stated"),                    # possible angina -> confirmed
    lambda o: o["key_diagnoses"][0].update(temporal="current"),                    # history -> current
    lambda o: o.update(summary_text="Start taking aspirin daily."),                # advice
    lambda o: o.update(summary_text="Chest tightness was seen on 2023-12-25."),    # wrong date
    lambda o: o.update(summary_text=" ".join(["word"] * 250)),                     # too long
])
def test_unsupported_claims_never_become_a_stored_summary(make, store, ids, mutation):
    with pytest.raises(SummaryGenerationFailedError) as e:
        make(ScriptedLLM(_mutate(ids, mutation))).execute(PID)
    assert e.value.reason == "invalid_output"
    assert not any(s.status == SummaryStatus.GENERATED for s in store.summaries.values())


# ---------------------------------------------------------------- persistence failure + privacy
def test_persistence_failure_is_reported_and_recorded_as_failed(make, store, ok):
    store.fail_on["summaries.save"] = 1
    with pytest.raises(SummaryGenerationFailedError) as e:
        make(ScriptedLLM(ok)).execute(PID)
    assert e.value.reason == "persistence_error"
    assert next(iter(store.summaries.values())).status == SummaryStatus.FAILED
    assert make(ScriptedLLM(ok)).execute(PID).summary                               # retry works


def test_audit_trail_contains_no_clinical_content(make, store, ok):
    make(ScriptedLLM(ok)).execute(PID)
    make(ScriptedLLM(ok)).execute(PID)
    with pytest.raises(SummaryGenerationFailedError):
        make(ScriptedLLM(LLMPermanentError("x"), model="m2")).execute(PID)
    blob = json.dumps([e.details for e in store.audit])
    for sensitive in ("chest", "hypertension", "angina", "Alex", "stress test", "1980"):
        assert sensitive not in blob


def test_provider_error_text_is_not_stored(make, store):
    with pytest.raises(SummaryGenerationFailedError):
        make(ScriptedLLM(LLMTransientError("Incorrect API key sk-abcdefghijklmnop"))).execute(PID)
    assert "sk-" not in str([s.model_dump() for s in store.summaries.values()]) + str([e.details for e in store.audit])


# ---------------------------------------------------------------- read side
def reader(store, model="test-model"):
    return GetPatientSummaryUseCase(lambda: FakeUnitOfWork(store), model, SummaryConfig())


def test_get_returns_latest_summary_and_reports_staleness(make, store, ok):
    with pytest.raises(SummaryNotFoundError):
        reader(store).execute(PID)
    with pytest.raises(PatientNotFoundError):
        reader(store).execute("missing")
    make(ScriptedLLM(ok)).execute(PID)
    view = reader(store).execute(PID)
    assert not view.is_stale and view.stale_reasons == [] and view.summary.patient_id == PID
    assert reader(store, model="newer-model").execute(PID).stale_reasons == ["model_changed"]
    new = make_record({"mrn": "000101", "note": "Another visit.", "date": "2024-06-01"}, index=5)
    store.records[new.record_id] = new
    assert reader(store).execute(PID).stale_reasons == ["source_records_changed"]


def test_get_ignores_failed_rows(make, store):
    with pytest.raises(SummaryGenerationFailedError):
        make(ScriptedLLM(LLMPermanentError("x"))).execute(PID)
    with pytest.raises(SummaryNotFoundError):
        reader(store).execute(PID)
