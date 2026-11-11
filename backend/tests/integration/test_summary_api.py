"""Summary endpoints end to end (real temp SQLite + real use cases) with an injected fake LLM provider."""

import json

import pytest

from app.domain.errors import LLMPermanentError, LLMTransientError
from tests.summary_helpers import R1_RAW, R2_RAW, ScriptedLLM, as_json, good_output

ROWS = [{**R1_RAW, "mrn": "MRN-000101", "name": "Alex Test", "dob": "1980-03-14"},
        {**R2_RAW, "name": "Alex Test", "dob": "1980-03-14"}]


def ingest(client, rows=ROWS, name="r.json"):
    r = client.post("/ingest", files={"file": (name, json.dumps(rows).encode())})
    assert r.status_code == 200
    return r.json()["results"]


@pytest.fixture
def patient(client):
    res = ingest(client)
    return res[0]["patient_id"]


def ok_for(client, pid):
    """Good model output citing this patient's real record ids (record ids come from the ingest response)."""
    ids = {r["source_id"].split("#")[1]: r["record_id"] for r in ingest(client)}
    return as_json(good_output(ids["0"], ids["1"]))


@pytest.fixture
def llm(client, patient):
    provider = ScriptedLLM(ok_for(client, patient))
    client.app.state.llm_provider = provider
    return provider


def test_without_an_api_key_generation_answers_503_and_everything_else_works(client, patient):
    r = client.post(f"/patients/{patient}/summary")
    assert r.status_code == 503 and r.json()["error"]["code"] == "ai_provider_not_configured"
    assert client.get("/health").status_code == 200
    assert client.get(f"/patients/{patient}/summary").status_code == 404


def test_generate_returns_a_complete_validated_summary(client, patient, llm):
    r = client.post(f"/patients/{patient}/summary")
    assert r.status_code == 200
    body = r.json()
    s = body["summary"]
    assert body["cache_hit"] is False and body["is_stale"] is False and body["summary_id"]
    assert s["patient_id"] == patient and s["model_identifier"] == "test-model"
    assert s["prompt_version"] == "clinical-summary-v1" and s["generated_at"]
    assert len(s["summary_text"].split()) <= 200
    assert s["chief_concern"]["source_record_ids"] and s["key_diagnoses"][1]["certainty"] == "uncertain"
    assert s["source_record_ids"] and s["recent_records"][0]["document_date"] == "2024-02-01"
    q = s["confidence_or_quality_indicator"]
    assert q["level"] in {"low", "medium", "high"} and "NOT a model confidence" in q["explanation"]
    assert "not medical advice" in s["disclaimer"] and s["missing_information"] == []
    assert s["source_content_hash"]


def test_second_post_is_a_cache_hit_and_does_not_call_the_model_again(client, patient, llm):
    first = client.post(f"/patients/{patient}/summary").json()
    second = client.post(f"/patients/{patient}/summary").json()
    assert second["cache_hit"] is True and llm.calls == 1 and second["summary"] == first["summary"]


def test_get_returns_the_stored_summary_and_flags_staleness_after_new_records(client, patient, llm):
    assert client.get(f"/patients/{patient}/summary").json()["error"]["code"] == "summary_not_found"
    posted = client.post(f"/patients/{patient}/summary").json()
    got = client.get(f"/patients/{patient}/summary").json()
    assert got["summary"] == posted["summary"] and got["is_stale"] is False and got["cache_hit"] is False
    new = {"mrn": "000101", "name": "Alex Test", "dob": "1980-03-14", "note": "Later visit, symptom free.", "date": "2024-07-01"}
    assert ingest(client, [new], "later.json")[0]["patient_id"] == patient
    stale = client.get(f"/patients/{patient}/summary").json()
    assert stale["is_stale"] is True and stale["stale_reasons"] == ["source_records_changed"]
    # regenerating: the model now sees 3 records; reuse a response that cites only the first two
    again = client.post(f"/patients/{patient}/summary")
    assert again.status_code == 200 and again.json()["cache_hit"] is False and llm.calls == 2
    assert client.get(f"/patients/{patient}/summary").json()["is_stale"] is False


def test_unknown_patient_and_patient_without_clinical_content(client, llm):
    for call in (lambda: client.post("/patients/nope/summary"), lambda: client.get("/patients/nope/summary")):
        r = call()
        assert r.status_code == 404 and r.json()["error"]["code"] == "patient_not_found"
    empty = ingest(client, [{"mrn": "000777", "name": "No Content"}], "empty.json")[0]["patient_id"]
    r = client.post(f"/patients/{empty}/summary")
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_source_records"
    assert llm.calls == 0


def test_provider_outage_returns_502_without_internal_details_and_is_retryable(client, patient):
    client.app.state.llm_provider = ScriptedLLM(LLMTransientError("Incorrect API key sk-abcdefghijklmnop"))
    client.app.state.settings.summary_retry_base_seconds = 0
    r = client.post(f"/patients/{patient}/summary")
    err = r.json()["error"]
    assert r.status_code == 502 and err["code"] == "summary_generation_failed"
    assert err["details"] == [{"reason": "provider_unavailable"}] and "sk-" not in r.text
    assert client.get(f"/patients/{patient}/summary").status_code == 404           # failure is not served
    client.app.state.llm_provider = ScriptedLLM(ok_for(client, patient))
    assert client.post(f"/patients/{patient}/summary").status_code == 200           # retry succeeds


def test_permanent_provider_error_and_malformed_output_return_502(client, patient):
    client.app.state.llm_provider = ScriptedLLM(LLMPermanentError("x"))
    r = client.post(f"/patients/{patient}/summary")
    assert r.status_code == 502 and r.json()["error"]["details"] == [{"reason": "provider_rejected"}]
    client.app.state.llm_provider = ScriptedLLM("I am sorry, I cannot do that.")
    r = client.post(f"/patients/{patient}/summary")
    assert r.status_code == 502 and r.json()["error"]["details"] == [{"reason": "invalid_output"}]
    assert "sorry" not in r.text


def test_hallucinated_diagnosis_is_never_returned_or_stored(client, patient):
    ids = {r["source_id"].split("#")[1]: r["record_id"] for r in ingest(client)}
    bad = good_output(ids["0"], ids["1"])
    bad["key_diagnoses"].append({"name": "diabetes", "certainty": "stated", "temporal": "current", "text": "Diabetes",
                                 "evidence_quote": "type 2 diabetes", "source_record_ids": [ids["0"]]})
    client.app.state.llm_provider = ScriptedLLM(as_json(bad))
    client.app.state.settings.summary_retry_base_seconds = 0
    r = client.post(f"/patients/{patient}/summary")
    assert r.status_code == 502 and "diabetes" not in r.text
    assert client.get(f"/patients/{patient}/summary").status_code == 404


def test_one_patients_summary_is_not_served_for_another(client, patient, llm):
    client.post(f"/patients/{patient}/summary")
    other = ingest(client, [{"mrn": "000999", "name": "Other Person", "dob": "1990-01-01", "note": "Mild cough.",
                             "date": "2024-01-01"}], "o.json")[0]["patient_id"]
    assert client.get(f"/patients/{other}/summary").status_code == 404


def test_overlong_patient_id_is_rejected_and_openapi_lists_the_endpoints(client):
    assert client.post(f"/patients/{'x' * 100}/summary").status_code == 422
    paths = client.get("/openapi.json").json()["paths"]
    assert {"post", "get"} <= set(paths["/patients/{patient_id}/summary"])
