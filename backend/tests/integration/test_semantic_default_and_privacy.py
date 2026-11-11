"""Assessment-ready defaults and privacy of search responses (offline: the semantic model is faked, never downloaded)."""

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.search_helpers import install_fake_sentence_transformers
from tests.summary_helpers import R1_RAW, R2_RAW, ScriptedLLM, as_json, good_output

ROWS = [{**R1_RAW, "mrn": "MRN-000101", "name": "Alex Test", "dob": "1980-03-14", "sex": "M"},
        {**R2_RAW, "name": "Alex Test", "dob": "1980-03-14", "sex": "M"}]
SAM = [{"mrn": "000222", "name": "Sam Other", "dob": "1990-01-01", "date": "2024-03-03",
        "note": "Chest tightness chest tightness on exertion after running."}]
IDENTIFIERS = ["Alex", "Test", "Sam Other", "Sam", "Other", "000101", "000222", "MRN-000101", "1980-03-14", "1990-01-01"]


def make_client(tmp_path, **kw):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'x.db'}", _env_file=None, **kw)
    return TestClient(create_app(settings), raise_server_exceptions=False)


def ingest_and_prepare(client, rows, name, summary=False):
    res = client.post("/ingest", files={"file": (name, json.dumps(rows).encode())}).json()["results"]
    pid = res[0]["patient_id"]
    assert client.post(f"/patients/{pid}/fhir").status_code == 200
    if summary:
        ids = {r["source_id"].split("#")[1]: r["record_id"] for r in res}
        client.app.state.llm_provider = ScriptedLLM(as_json(good_output(ids["0"], ids["1"])))
        assert client.post(f"/patients/{pid}/summary").status_code == 200
    return pid


def keys_recursive(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from keys_recursive(v)
    elif isinstance(node, list):
        for v in node:
            yield from keys_recursive(v)


# ------------------------------------------------------------ semantic is the default
def test_default_configuration_runs_semantic_mode_end_to_end_with_the_fake_model(tmp_path, monkeypatch):
    rec = install_fake_sentence_transformers(monkeypatch)
    client = make_client(tmp_path)                                         # NO provider chosen: the application default
    assert client.get("/search/status").json()["embedding"] == {
        "model_identifier": "sentence-transformers/all-MiniLM-L6-v2", "kind": "semantic", "available": True}
    pid = ingest_and_prepare(client, ROWS, "a.json", summary=True)
    assert rec.constructed == 0                                            # nothing loaded until first use
    assert client.post(f"/patients/{pid}/index").json()["indexed"] == 3
    body = client.post("/search", json={"query": "chest tightness"}).json()
    assert body["embedding"] == {"model_identifier": "sentence-transformers/all-MiniLM-L6-v2", "kind": "semantic"}
    assert body["returned"] == 3 and rec.constructed == 1 and rec.devices == ["cpu"]
    assert client.get("/search/status").json()["by_model"] == {"sentence-transformers/all-MiniLM-L6-v2": 3}


# ------------------------------------------------------------ missing model: clear error, never a silent fallback
def test_semantic_default_without_the_model_returns_a_clear_503_and_never_lexical_results(tmp_path):
    client = make_client(tmp_path)                                         # the autouse guard hides sentence_transformers
    pid = ingest_and_prepare(client, ROWS, "a.json")
    status = client.get("/search/status")
    assert status.status_code == 200 and status.json()["embedding"] == {
        "model_identifier": "sentence-transformers/all-MiniLM-L6-v2", "kind": "semantic", "available": False}

    for call in (lambda: client.post("/search", json={"query": "chest tightness"}),
                 lambda: client.post(f"/patients/{pid}/index"), lambda: client.post("/search/reindex")):
        r = call()
        err = r.json()["error"]
        assert r.status_code == 503 and err["code"] == "embedding_provider_unavailable"
        assert "pip install -e './backend[embeddings]'" in err["message"] and "EHR_EMBEDDING_PROVIDER=hashing" in err["message"]
        assert "Traceback" not in r.text and "results" not in r.json()
    after = client.get("/search/status").json()
    assert after["entries_total"] == 0 and after["by_model"] == {}         # nothing was indexed with a lexical stand-in


def test_unavailable_model_after_a_load_failure_is_also_a_503_with_no_provider_details(tmp_path, monkeypatch):
    install_fake_sentence_transformers(monkeypatch, fail_load=True)
    client = make_client(tmp_path)
    ingest_and_prepare(client, ROWS, "a.json")
    r = client.post("/search", json={"query": "chest tightness"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "embedding_provider_unavailable"
    assert "SECRET-PROVIDER-DETAIL" not in r.text and "huggingface" not in r.text.lower()


# ------------------------------------------------------------ explicit lexical fallback
def test_lexical_fallback_is_an_explicit_choice_and_is_labelled_lexical(tmp_path):
    client = make_client(tmp_path, embedding_provider="hashing")
    ingest_and_prepare(client, ROWS, "a.json")
    assert client.get("/search/status").json()["embedding"] == {
        "model_identifier": "hashing-ngram-v1-384", "kind": "lexical", "available": True}
    assert client.post("/search/reindex").json()["indexed"] == 2
    body = client.post("/search", json={"query": "chest tightness"}).json()
    assert body["embedding"] == {"model_identifier": "hashing-ngram-v1-384", "kind": "lexical"} and body["returned"] == 2


def test_switching_provider_does_not_mix_vectors_from_different_models(tmp_path, monkeypatch):
    path = tmp_path / "shared.db"
    lexical = TestClient(create_app(Settings(database_url=f"sqlite:///{path}", embedding_provider="hashing", _env_file=None)))
    pid = ingest_and_prepare(lexical, ROWS, "a.json")
    lexical.post(f"/patients/{pid}/index")
    install_fake_sentence_transformers(monkeypatch)
    semantic = TestClient(create_app(Settings(database_url=f"sqlite:///{path}", _env_file=None)))
    assert semantic.post("/search", json={"query": "chest tightness"}).json()["returned"] == 0     # old lexical entries ignored
    assert semantic.post(f"/patients/{pid}/index").json()["indexed"] == 2                           # re-embedded with the new model
    assert semantic.post("/search", json={"query": "chest tightness"}).json()["returned"] == 2


# ------------------------------------------------------------ search responses expose no direct patient identifiers
@pytest.fixture
def two_patients(client):
    a = ingest_and_prepare(client, ROWS, "alex.json", summary=True)
    b = ingest_and_prepare(client, SAM, "sam.json")
    assert client.post("/search/reindex").status_code == 200
    return a, b


def test_search_results_contain_no_names_mrns_or_dates_of_birth(client, two_patients):
    a, b = two_patients
    for body in ({"query": "chest tightness"}, {"query": "chest tightness", "patient_id": a}, {"query": "tightness", "patient_id": b},
                 {"query": "chest", "resource_types": ["ClinicalSummary"]}, {"query": "visit", "date_from": "2020-01-01"}):
        r = client.post("/search", json=body)
        assert r.status_code == 200 and r.json()["returned"] >= 1
        text = r.text
        for identifier in IDENTIFIERS:
            assert identifier not in text, f"{identifier!r} leaked in {body}"
        for result in r.json()["results"]:
            assert set(result) == {"rank", "resource_type", "resource_id", "fhir_reference", "link", "patient_id",
                                   "record_date", "score", "snippet", "source_record_id", "ai_generated"}
        assert not {"patient_name", "name", "mrn", "dob", "date_of_birth", "birthDate"} & set(keys_recursive(r.json()))


def test_status_and_index_responses_also_contain_no_direct_identifiers(client, two_patients):
    a, _ = two_patients
    for r in (client.get("/search/status"), client.post(f"/patients/{a}/index"), client.post("/search/reindex")):
        assert r.status_code == 200
        for identifier in IDENTIFIERS:
            assert identifier not in r.text


def test_the_openapi_contract_has_no_name_or_mrn_in_search_results(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    props = set(schemas["SearchResultSchema"]["properties"])
    assert props == {"rank", "resource_type", "resource_id", "fhir_reference", "link", "patient_id", "record_date",
                     "score", "snippet", "source_record_id", "ai_generated"}


def test_the_api_description_states_it_is_a_demo_without_authentication(client):
    desc = client.get("/openapi.json").json()["info"]["description"]
    assert "DEMO" in desc and "NO authentication or authorization" in desc and "real patient data" in desc


# ------------------------------------------------------------ preserved behavior (isolation, top-5, filters)
def test_patient_scoping_is_still_enforced_and_unscoped_search_still_spans_patients(client, two_patients):
    a, b = two_patients
    everyone = client.post("/search", json={"query": "chest tightness"}).json()["results"]
    assert {r["patient_id"] for r in everyone} == {a, b}
    for pid in (a, b):
        scoped = client.post("/search", json={"query": "chest tightness", "patient_id": pid}).json()["results"]
        assert scoped and {r["patient_id"] for r in scoped} == {pid}
    assert client.post("/search", json={"query": "x", "patient_id": "nope"}).status_code == 404


def test_top_five_resource_type_and_date_filters_still_work(client, two_patients):
    for i in range(6):
        ingest_and_prepare(client, [{"mrn": f"50000{i}", "name": f"Extra {i}", "dob": "1980-01-01", "date": f"2024-05-0{i + 1}",
                                     "note": f"Chest tightness number {i}."}], f"e{i}.json")
    client.post("/search/reindex")
    assert client.post("/search", json={"query": "chest tightness"}).json()["returned"] == 5
    assert client.post("/search", json={"query": "chest tightness", "limit": 6}).status_code == 422
    docs = client.post("/search", json={"query": "chest", "resource_types": ["DocumentReference"]}).json()["results"]
    assert {r["resource_type"] for r in docs} == {"DocumentReference"}
    dated = client.post("/search", json={"query": "chest tightness", "date_from": "2024-05-03", "date_to": "2024-05-04"}).json()["results"]
    assert dated and all("2024-05-03" <= r["record_date"] <= "2024-05-04" for r in dated)
