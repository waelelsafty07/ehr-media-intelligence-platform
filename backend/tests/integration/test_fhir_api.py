"""FHIR endpoints end to end (ingest -> generate -> retrieve) with a real temp SQLite database."""

from pathlib import Path

import pytest
from fastapi import Request
from fhir.resources.R4B.bundle import Bundle

from app.api import dependencies as deps
from app.application.use_cases.generate_fhir_bundle import GenerateFHIRBundleUseCase
from app.domain.fhir import FhirIssue, check_bundle
from app.infrastructure.fhir.r4b_builder import R4BBundleBuilder
from app.infrastructure.fhir.r4b_validator import R4BSchemaValidator
from app.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork

SAMPLE = Path(__file__).resolve().parents[3] / "data" / "samples" / "records_with_diagnoses.json"


@pytest.fixture
def patients(client):
    r = client.post("/ingest", files={"file": ("records_with_diagnoses.json", SAMPLE.read_bytes())})
    assert r.status_code == 200 and r.json()["counts"] == {"completed": 3}
    res = r.json()["results"]
    return {"morgan": res[0]["patient_id"], "jordan": res[2]["patient_id"]}


def test_not_generated_status_then_409_on_retrieval(client, patients):
    pid = patients["morgan"]
    assert client.get(f"/patients/{pid}/fhir/status").json()["status"] == "not_generated"
    r = client.get(f"/patients/{pid}/fhir")
    assert r.status_code == 409 and r.json()["error"]["code"] == "fhir_not_available"


def test_generate_then_retrieve_valid_r4_bundle(client, patients):
    pid = patients["morgan"]
    r = client.post(f"/patients/{pid}/fhir")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "generated" and not body["unchanged"] and body["issues"] == []
    assert body["resource_counts"] == {"Patient": 1, "DocumentReference": 2, "DiagnosticReport": 1}

    g = client.get(f"/patients/{pid}/fhir")
    assert g.status_code == 200 and g.headers["content-type"].startswith("application/fhir+json")
    bundle = g.json()
    assert bundle["resourceType"] == "Bundle" and bundle["type"] == "collection" and bundle["id"] == body["bundle_id"]
    Bundle.model_validate(bundle)                                     # independent library parse (R4B)
    assert check_bundle(bundle) == []
    patient = bundle["entry"][0]["resource"]
    assert patient["resourceType"] == "Patient" and patient["id"] == pid
    assert patient["identifier"][0]["value"] == "000401" and patient["birthDate"] == "1972-05-20"
    assert {e["resource"]["subject"]["reference"] for e in bundle["entry"][1:]} == {f"urn:uuid:{pid}"}
    assert client.get(f"/patients/{pid}/fhir/status").json()["status"] == "generated"


def test_generate_twice_is_idempotent(client, patients):
    pid = patients["morgan"]
    first = client.post(f"/patients/{pid}/fhir").json()
    second = client.post(f"/patients/{pid}/fhir").json()
    assert second["unchanged"] and second["bundle_id"] == first["bundle_id"]
    assert client.get(f"/patients/{pid}/fhir").json()["entry"] == client.get(f"/patients/{pid}/fhir").json()["entry"]


def test_patient_with_diagnosis_only_gets_report_without_document(client, patients):
    pid = patients["jordan"]
    assert client.post(f"/patients/{pid}/fhir").json()["resource_counts"] == {"Patient": 1, "DiagnosticReport": 1}


def test_patients_are_isolated(client, patients):
    for pid in patients.values():
        client.post(f"/patients/{pid}/fhir")
    bundle = client.get(f"/patients/{patients['jordan']}/fhir").json()
    blob = str(bundle)
    assert patients["morgan"] not in blob and "000401" not in blob


def test_reingest_new_document_triggers_regeneration(client, patients):
    pid = patients["morgan"]
    client.post(f"/patients/{pid}/fhir")
    extra = b'[{"mrn":"000401","name":"Morgan Fixture","dob":"1972-05-20","note":"Brand new note.","date":"2024-07-01"}]'
    assert client.post("/ingest", files={"file": ("extra.json", extra)}).json()["results"][0]["patient_id"] == pid
    again = client.post(f"/patients/{pid}/fhir").json()
    assert not again["unchanged"] and again["resource_counts"]["DocumentReference"] == 3


def test_unknown_patient_returns_404_everywhere(client):
    for call in (lambda: client.post("/patients/nope/fhir"), lambda: client.get("/patients/nope/fhir"),
                 lambda: client.get("/patients/nope/fhir/status")):
        r = call()
        assert r.status_code == 404 and r.json()["error"]["code"] == "patient_not_found"


def test_overlong_patient_id_is_rejected_by_validation(client):
    assert client.get(f"/patients/{'x' * 200}/fhir").status_code == 422


def _override(client, builder=None, validator=None):
    def make(request: Request):
        sf = request.app.state.session_factory
        return GenerateFHIRBundleUseCase(lambda: SqlAlchemyUnitOfWork(sf), builder or R4BBundleBuilder(),
                                         validator or R4BSchemaValidator())
    client.app.dependency_overrides[deps.get_generate_fhir_use_case] = make


def test_validation_failure_returns_422_with_issue_paths_and_serves_nothing(client, patients):
    pid = patients["morgan"]

    class BadValidator:
        def validate_bundle(self, bundle):
            return [FhirIssue(code="schema:extra_forbidden", path="entry.0.resource.foo", message="x")]

    _override(client, validator=BadValidator())
    r = client.post(f"/patients/{pid}/fhir")
    err = r.json()["error"]
    assert r.status_code == 422 and err["code"] == "fhir_validation_failed"
    assert err["details"] == [{"path": "entry.0.resource.foo", "code": "schema:extra_forbidden"}]
    assert client.get(f"/patients/{pid}/fhir/status").json()["status"] == "invalid"
    assert client.get(f"/patients/{pid}/fhir").status_code == 409


def test_unexpected_failure_returns_500_without_details_and_can_be_retried(client, patients):
    pid = patients["morgan"]

    class Boom(R4BBundleBuilder):
        def build_resources(self, *a, **k):
            raise RuntimeError("internal secret-detail")

    _override(client, builder=Boom())
    r = client.post(f"/patients/{pid}/fhir")
    assert r.status_code == 500 and r.json()["error"]["code"] == "fhir_generation_failed"
    assert "secret-detail" not in r.text
    assert client.get(f"/patients/{pid}/fhir/status").json()["status"] == "failed"
    client.app.dependency_overrides.clear()                                  # the fault is fixed
    assert client.post(f"/patients/{pid}/fhir").json()["status"] == "generated"


def test_openapi_documents_fhir_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/patients/{patient_id}/fhir", "/patients/{patient_id}/fhir/status"} <= set(paths)
    assert "post" in paths["/patients/{patient_id}/fhir"] and "get" in paths["/patients/{patient_id}/fhir"]
