"""Search + indexing endpoints end to end (real temp SQLite, real use cases, hashing embedder, fake LLM)."""

import json
import logging

import pytest

from app.domain.errors import EmbeddingUnavailableError, VectorStoreError
from tests.summary_helpers import R1_RAW, R2_RAW, ScriptedLLM, as_json, good_output

ALEX_ROWS = [{**R1_RAW, "mrn": "MRN-000101", "name": "Alex Test", "dob": "1980-03-14"},
             {**R2_RAW, "name": "Alex Test", "dob": "1980-03-14"}]
SAM_ROWS = [{"mrn": "000222", "name": "Sam Other", "dob": "1990-01-01", "date": "2024-03-03",
             "note": "Chest tightness chest tightness chest tightness on exertion after running."}]
ANKLE_ROWS = [{"mrn": "000333", "name": "Ann Ankle", "dob": "1985-05-05", "date": "2024-04-04",
               "note": "Sprained ankle after a football match, swelling and bruising."}]


def post(client, path, **kw):
    return client.post(path, **kw)


def ingest(client, rows, name):
    r = client.post("/ingest", files={"file": (name, json.dumps(rows).encode())})
    assert r.status_code == 200
    return r.json()["results"]


def patient_with_fhir(client, rows, name):
    res = ingest(client, rows, name)
    pid = res[0]["patient_id"]
    assert client.post(f"/patients/{pid}/fhir").status_code == 200
    return pid, res


def add_summary(client, pid, res):
    ids = {r["source_id"].split("#")[1]: r["record_id"] for r in res}
    client.app.state.llm_provider = ScriptedLLM(as_json(good_output(ids["0"], ids["1"])))
    assert client.post(f"/patients/{pid}/summary").status_code == 200


def search(client, **body):
    body.setdefault("query", "chest tightness")
    return client.post("/search", json=body)


@pytest.fixture
def alex(client):
    pid, res = patient_with_fhir(client, ALEX_ROWS, "alex.json")
    add_summary(client, pid, res)
    return pid


def test_status_before_any_indexing(client):
    r = client.get("/search/status")
    assert r.status_code == 200 and r.json() == {"entries_total": 0, "by_resource_type": {}, "by_model": {},
                                                 "embedding": {"model_identifier": "hashing-ngram-v1-384", "kind": "lexical",
                                                               "available": True}}
    out = search(client).json()
    assert out["results"] == [] and out["returned"] == 0 and out["embedding"]["kind"] == "lexical"


def test_index_then_search_returns_the_documented_result_shape(client, alex):
    r = client.post(f"/patients/{alex}/index")
    assert r.status_code == 200 and r.json() == {"patient_id": alex, "indexed": 3, "unchanged": 0, "removed": 0, "skipped": []}
    body = search(client).json()
    assert body["query"] == "chest tightness" and body["returned"] == len(body["results"]) == 3
    assert body["embedding"] == {"model_identifier": "hashing-ngram-v1-384", "kind": "lexical"}
    assert "not clinical relevance" in body["note"] and body["took_ms"] >= 0
    docs = [x for x in body["results"] if x["resource_type"] == "DocumentReference"]
    top = body["results"][0]
    assert [x["rank"] for x in body["results"]] == [1, 2, 3] and top["score"] >= body["results"][-1]["score"] > 0
    d = docs[0]
    assert d["fhir_reference"] == f"DocumentReference/{d['resource_id']}" and d["link"] == f"/patients/{alex}/fhir"
    assert d["patient_id"] == alex and d["ai_generated"] is False
    assert d["record_date"] in ("2023-11-10", "2024-02-01") and d["source_record_id"] and "chest tightness" in d["snippet"].lower()
    s = next(x for x in body["results"] if x["resource_type"] == "ClinicalSummary")
    assert s["ai_generated"] is True and s["fhir_reference"] is None and s["link"] == f"/patients/{alex}/summary" and s["source_record_id"] is None
    assert set(d) == {"rank", "resource_type", "resource_id", "fhir_reference", "link", "patient_id",
                      "record_date", "score", "snippet", "source_record_id", "ai_generated"}      # no name / MRN / DOB


def test_the_linked_fhir_resource_actually_exists(client, alex):
    client.post(f"/patients/{alex}/index")
    d = next(x for x in search(client).json()["results"] if x["resource_type"] == "DocumentReference")
    bundle = client.get(d["link"]).json()
    assert d["resource_id"] in {e["resource"]["id"] for e in bundle["entry"] if e["resource"]["resourceType"] == "DocumentReference"}


def test_indexing_and_reindexing_are_idempotent_and_status_reflects_the_index(client, alex):
    client.post(f"/patients/{alex}/index")
    again = client.post(f"/patients/{alex}/index").json()
    assert (again["indexed"], again["unchanged"], again["removed"]) == (0, 3, 0)
    patient_with_fhir(client, ANKLE_ROWS, "ankle.json")
    first = client.post("/search/reindex").json()
    assert first["patients"] == 2 and first["indexed"] == 1 and first["unchanged"] == 3 and len(first["results"]) == 2
    assert client.post("/search/reindex").json()["indexed"] == 0
    assert client.get("/search/status").json()["entries_total"] == 4
    assert client.get("/search/status").json()["by_resource_type"] == {"DocumentReference": 3, "ClinicalSummary": 1}


def test_patient_filter_isolates_results_even_when_another_patient_matches_better(client, alex):
    sam, _ = patient_with_fhir(client, SAM_ROWS, "sam.json")
    client.post("/search/reindex")
    everyone = search(client).json()["results"]
    assert everyone[0]["patient_id"] == sam and {r["patient_id"] for r in everyone} == {alex, sam}
    scoped = search(client, patient_id=alex).json()["results"]
    assert scoped and {r["patient_id"] for r in scoped} == {alex} and "Sam" not in json.dumps(scoped)
    assert {r["patient_id"] for r in search(client, patient_id=sam).json()["results"]} == {sam}


def test_resource_type_and_date_filters(client, alex):
    client.post(f"/patients/{alex}/index")
    docs = search(client, resource_types=["DocumentReference"]).json()["results"]
    assert {r["resource_type"] for r in docs} == {"DocumentReference"} and len(docs) == 2
    assert [r["resource_type"] for r in search(client, resource_types=["ClinicalSummary"]).json()["results"]] == ["ClinicalSummary"]
    recent = search(client, date_from="2024-01-01").json()["results"]
    assert recent and all(r["record_date"] >= "2024-01-01" for r in recent)
    assert search(client, date_from="2023-11-10", date_to="2023-11-10", resource_types=["DocumentReference"]).json()["returned"] == 1
    assert search(client, date_from="2030-01-01").json()["results"] == []


def test_a_summary_goes_stale_when_records_change_and_is_dropped_then_removed(client, alex):
    client.post(f"/patients/{alex}/index")
    assert any(r["resource_type"] == "ClinicalSummary" for r in search(client).json()["results"])
    new = [{"mrn": "000101", "name": "Alex Test", "dob": "1980-03-14", "note": "Later visit, symptom free.", "date": "2024-07-01"}]
    assert ingest(client, new, "later.json")[0]["patient_id"] == alex
    out = search(client).json()
    assert not any(r["resource_type"] == "ClinicalSummary" for r in out["results"]) and out["stale_results_dropped"] >= 1
    client.post(f"/patients/{alex}/fhir")
    ix = client.post(f"/patients/{alex}/index").json()
    assert ix["removed"] == 1 and ix["indexed"] == 1 and "summary_stale" in ix["skipped"]
    assert search(client).json()["stale_results_dropped"] == 0


def test_patient_without_fhir_is_reported_as_skipped_not_an_error(client):
    res = ingest(client, ANKLE_ROWS, "ankle.json")
    r = client.post(f"/patients/{res[0]['patient_id']}/index")
    assert r.status_code == 200 and r.json()["indexed"] == 0 and "fhir_not_generated" in r.json()["skipped"]


@pytest.mark.parametrize("body", [
    {"query": ""}, {"query": "   "}, {"query": "x" * 501}, {"query": "x", "limit": 6}, {"query": "x", "limit": 0},
    {"query": "x", "resource_types": ["Patient"]}, {"query": "x", "resource_types": []},
    {"query": "x", "date_from": "2024-05-01", "date_to": "2024-01-01"}, {"query": "x", "date_from": "yesterday"},
    {"query": "x", "min_score": 2}, {"query": "x", "extra": True}, {}, {"patient_id": "p"},
])
def test_invalid_requests_get_a_consistent_422_envelope_without_echoing_input(client, body):
    r = client.post("/search", json=body)
    err = r.json()["error"]
    assert r.status_code == 422 and err["code"] == "validation_error" and err["request_id"] and err["details"]
    assert all(set(d) == {"loc", "msg"} for d in err["details"])


def test_malformed_json_and_wrong_content_type_are_rejected(client):
    assert client.post("/search", content=b"{not json", headers={"content-type": "application/json"}).status_code == 422
    assert client.post("/search", content=b"query=x", headers={"content-type": "text/plain"}).status_code == 422


def test_unknown_patient_filter_is_a_404(client):
    r = search(client, patient_id="does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "patient_not_found"
    assert client.post("/patients/does-not-exist/index").status_code == 404


def test_limit_defaults_to_five_and_caps_at_five(client):
    for i in range(7):
        patient_with_fhir(client, [{"mrn": f"90000{i}", "name": f"Pat {i}", "dob": "1980-01-01", "date": f"2024-05-0{i + 1}",
                                    "note": f"Chest tightness episode {i}."}], f"p{i}.json")
    client.post("/search/reindex")
    assert search(client).json()["returned"] == 5 and search(client, limit=3).json()["returned"] == 3


class _FailingEmbedder:
    model_identifier, kind = "x", "lexical"

    def is_available(self):
        return True

    def embed(self, texts):
        raise EmbeddingUnavailableError("Embedding failed.")


class _FailingStore:
    def query(self, *a, **k):
        raise VectorStoreError("The search index is unavailable.")

    def stats(self):
        raise VectorStoreError("The search index is unavailable.")

    content_hashes = upsert = delete = query


def test_embedding_provider_failure_is_a_safe_503(client, alex):
    client.app.state.embedding_provider = _FailingEmbedder()
    r = search(client)
    assert r.status_code == 503 and r.json()["error"]["code"] == "embedding_provider_unavailable"
    assert client.post(f"/patients/{alex}/index").status_code == 503


def test_vector_store_failure_is_a_safe_503_and_never_a_500(client, alex):
    client.app.state.vector_store = _FailingStore()
    for call in (lambda: search(client), lambda: client.get("/search/status"), lambda: client.post(f"/patients/{alex}/index")):
        r = call()
        assert r.status_code == 503 and r.json()["error"]["code"] == "search_index_unavailable"
        assert "sqlalchemy" not in r.text.lower() and "Traceback" not in r.text


def test_the_search_query_is_not_written_to_the_logs(client, alex, caplog):
    client.post(f"/patients/{alex}/index")
    caplog.set_level(logging.DEBUG)
    search(client, query="Jane Doe SSN 123-45-6789 chest")
    text = " ".join(str(v) for rec in caplog.records for v in (rec.getMessage(), *rec.__dict__.values()))
    assert "Jane Doe" not in text and "123-45-6789" not in text


def test_openapi_documents_the_search_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "post" in paths["/search"] and "get" in paths["/search/status"]
    assert "post" in paths["/search/reindex"] and "post" in paths["/patients/{patient_id}/index"]
    props = client.get("/openapi.json").json()["components"]["schemas"]["SearchQuery"]["properties"]
    assert props["limit"]["maximum"] == 5 and props["limit"]["default"] == 5
