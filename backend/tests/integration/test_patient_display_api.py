"""GET /patients/{id}/display: minimal name + MRN lookup; /search stays free of direct identifiers."""

import json

ROWS = [{"mrn": "MRN-000101", "name": "Alex Test", "dob": "1980-03-14", "sex": "M", "date": "2024-01-02",
         "note": "Chest tightness on exertion."}]


def prepare(client):
    res = client.post("/ingest", files={"file": ("a.json", json.dumps(ROWS).encode())}).json()["results"]
    pid = res[0]["patient_id"]
    client.post(f"/patients/{pid}/fhir")
    client.post("/search/reindex")
    return pid


def test_display_returns_name_and_mrn(client):
    pid = prepare(client)
    r = client.get(f"/patients/{pid}/display")
    assert r.status_code == 200
    assert r.json() == {"patient_id": pid, "patient_name": "Alex Test", "mrn": r.json()["mrn"]}
    assert "000101" in r.json()["mrn"]


def test_display_contains_only_approved_fields(client):
    pid = prepare(client)
    body = client.get(f"/patients/{pid}/display").json()
    assert set(body) == {"patient_id", "patient_name", "mrn"}
    text = json.dumps(body)
    for forbidden in ("1980", "dob", "birth", "gender", "sex", "Chest", "tightness"):
        assert forbidden not in text


def test_unknown_patient_is_a_safe_404(client):
    r = client.get("/patients/does-not-exist/display")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "patient_not_found"
    assert "request_id" in r.json()["error"]


def test_oversized_patient_id_is_rejected(client):
    assert client.get("/patients/" + "x" * 65 + "/display").status_code == 422


def test_search_response_still_excludes_names_and_mrns(client):
    prepare(client)
    raw = client.post("/search", json={"query": "chest tightness"}).text
    assert "Alex" not in raw and "000101" not in raw and "1980" not in raw
    item = json.loads(raw)["results"][0]
    assert not {"patient_name", "name", "mrn", "dob"} & set(item)


def test_display_endpoint_is_in_openapi_with_minimal_schema(client):
    spec = client.get("/openapi.json").json()
    assert "/patients/{patient_id}/display" in spec["paths"]
    props = set(spec["components"]["schemas"]["PatientDisplayResponse"]["properties"])
    assert props == {"patient_id", "patient_name", "mrn"}
