"""GET /patients/{id}/fhir/resources/{type}/{rid}: focused, patient-scoped, minimal."""

import json

ROWS_A = [{"mrn": "000101", "name": "Alex Test", "dob": "1980-03-14", "date": "2024-01-02", "note": "Chest tightness on exertion."}]
ROWS_B = [{"mrn": "000202", "name": "Bea Other", "dob": "1990-05-05", "date": "2024-02-02", "note": "Sprained ankle after football."}]


def prepare(client, rows, name, fhir=True):
    pid = client.post("/ingest", files={"file": (name, json.dumps(rows).encode())}).json()["results"][0]["patient_id"]
    if fhir:
        assert client.post(f"/patients/{pid}/fhir").status_code == 200
    return pid


def doc_id(client, pid):
    return next(e["resource"]["id"] for e in client.get(f"/patients/{pid}/fhir").json()["entry"]
                if e["resource"]["resourceType"] == "DocumentReference")


def url(pid, rtype, rid):
    return f"/patients/{pid}/fhir/resources/{rtype}/{rid}"


def test_returns_exactly_the_requested_resource(client):
    pid = prepare(client, ROWS_A, "a.json")
    rid = doc_id(client, pid)
    r = client.get(url(pid, "DocumentReference", rid))
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/fhir+json")
    body = r.json()
    assert body["resourceType"] == "DocumentReference" and body["id"] == rid
    assert "entry" not in body  # not a Bundle
    stored = next(e["resource"] for e in client.get(f"/patients/{pid}/fhir").json()["entry"] if e["resource"]["id"] == rid)
    assert body == stored  # identical to the validated stored resource


def test_response_contains_no_patient_demographics(client):
    pid = prepare(client, ROWS_A, "a.json")
    text = client.get(url(pid, "DocumentReference", doc_id(client, pid))).text
    for forbidden in ("Alex", "000101", "1980", "birthDate"):
        assert forbidden not in text


def test_patient_resource_is_not_retrievable_individually(client):
    pid = prepare(client, ROWS_A, "a.json")
    r = client.get(url(pid, "Patient", pid))
    assert r.status_code == 404 and r.json()["error"]["code"] == "fhir_resource_not_found"


def test_another_patients_resource_is_a_404_indistinguishable_from_missing(client):
    a, b = prepare(client, ROWS_A, "a.json"), prepare(client, ROWS_B, "b.json")
    foreign = client.get(url(a, "DocumentReference", doc_id(client, b)))
    missing = client.get(url(a, "DocumentReference", "no-such-id"))
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["code"] == missing.json()["error"]["code"] == "fhir_resource_not_found"
    assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]
    assert "Bea" not in foreign.text and "Sprained" not in foreign.text


def test_wrong_resource_type_and_unknown_type_are_404(client):
    pid = prepare(client, ROWS_A, "a.json")
    rid = doc_id(client, pid)
    assert client.get(url(pid, "DiagnosticReport", rid)).status_code == 404
    assert client.get(url(pid, "Observation", rid)).status_code == 404


def test_unknown_patient_404_and_fhir_not_generated_409(client):
    assert client.get(url("nope", "DocumentReference", "x")).json()["error"]["code"] == "patient_not_found"
    pid = prepare(client, ROWS_A, "a.json", fhir=False)
    r = client.get(url(pid, "DocumentReference", "x"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "fhir_not_available"


def test_oversized_ids_are_rejected(client):
    assert client.get(url("p", "DocumentReference", "x" * 65)).status_code == 422
