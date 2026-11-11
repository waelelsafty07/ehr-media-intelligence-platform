"""Canonical -> FHIR R4 mapping through the real fhir.resources R4B adapter."""

import base64
import hashlib

import pytest
from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.documentreference import DocumentReference
from fhir.resources.R4B.patient import Patient as FhirPatient

from app.domain.canonical import Gender
from app.domain.errors import FhirBuildError
from app.domain.fhir import (
    bundle_id_for,
    check_bundle,
    diagnostic_report_id,
    document_reference_id,
    full_url,
)
from app.infrastructure.fhir.r4b_builder import R4BBundleBuilder
from app.infrastructure.fhir.r4b_validator import R4BSchemaValidator
from tests.fhir_helpers import (
    DX_ONLY,
    NOTE,
    NOTE_WITH_DX,
    NOW,
    PATIENT_ID,
    make_patient,
    make_record,
)

B = R4BBundleBuilder()


def by_type(resources, t):
    return [r for r in resources if r.resource_type == t]


def test_patient_mapping():
    (p,) = by_type(B.build_resources(make_patient(), [], NOW), "Patient")
    c = p.content
    assert p.resource_id == PATIENT_ID and c["id"] == PATIENT_ID and p.source_record_id is None
    ident = c["identifier"][0]
    assert ident["value"] == "000101" and ident["system"] == "urn:ehr-media-intelligence:mrn"
    assert ident["type"]["coding"][0]["code"] == "MR" and ident["use"] == "usual"
    assert c["name"] == [{"text": "Alex Test"}]          # text only: no invented given/family split
    assert c["gender"] == "male" and c["birthDate"] == "1980-03-14"
    FhirPatient.model_validate(c)


def test_patient_missing_optional_fields_are_omitted_not_invented():
    c = B.build_resources(make_patient(name=None, dob=None, gender=Gender.UNKNOWN), [], NOW)[0].content
    assert "name" not in c and "birthDate" not in c and c["gender"] == "unknown"


def test_document_reference_mapping():
    rec = make_record(NOTE)
    (d,) = by_type(B.build_resources(make_patient(), [rec], NOW), "DocumentReference")
    c = d.content
    assert d.resource_id == document_reference_id(rec.record_id) == c["id"] and d.source_record_id == rec.record_id
    assert c["status"] == "current" and c["subject"] == {"reference": full_url(PATIENT_ID), "type": "Patient"}
    att = c["content"][0]["attachment"]
    text = base64.b64decode(att["data"])
    assert text.decode() == "Follow-up for chest tightness." and att["size"] == len(text)
    assert att["hash"] == base64.b64encode(hashlib.sha1(text).digest()).decode()
    assert att["contentType"] == "text/plain"
    assert c["context"]["period"]["start"] == "2024-02-01"            # clinical date
    assert c["date"].startswith("2025-01-01")                          # R4 DocumentReference.date = creation time
    for not_in_source in ("type", "category", "docStatus", "description", "author"):
        assert not_in_source not in c                                  # nothing invented
    DocumentReference.model_validate(c)


def test_document_without_date_has_no_context_period():
    (d,) = by_type(B.build_resources(make_patient(), [make_record({"mrn": "000101", "note": "No date."})], NOW),
                   "DocumentReference")
    assert "context" not in d.content


def test_diagnostic_report_only_when_diagnoses_are_stated_and_nothing_is_coded():
    only_note = B.build_resources(make_patient(), [make_record(NOTE)], NOW)
    assert by_type(only_note, "DiagnosticReport") == []
    rec = make_record(NOTE_WITH_DX)
    (r,) = by_type(B.build_resources(make_patient(), [rec], NOW), "DiagnosticReport")
    c = r.content
    assert r.resource_id == diagnostic_report_id(rec.record_id)
    assert c["status"] == "unknown"                                    # source states no report status
    assert c["code"] == {"text": "Diagnoses documented in source clinical record"} and "coding" not in c["code"]
    assert c["conclusion"] == "Asthma; Hypertension"
    assert c["conclusionCode"] == [{"text": "Asthma"}, {"text": "Hypertension"}]
    assert c["effectiveDateTime"] == "2024-02-01" and c["subject"]["reference"] == full_url(PATIENT_ID)
    DiagnosticReport.model_validate(c)


def test_document_reference_links_to_its_diagnostic_report():
    rec = make_record(NOTE_WITH_DX)
    res = B.build_resources(make_patient(), [rec], NOW)
    doc = by_type(res, "DocumentReference")[0].content
    assert doc["context"]["related"] == [{"reference": full_url(diagnostic_report_id(rec.record_id)),
                                          "type": "DiagnosticReport"}]


def test_diagnoses_without_document_produce_report_only():
    res = B.build_resources(make_patient(), [make_record(DX_ONLY)], NOW)
    assert [r.resource_type for r in res] == ["Patient", "DiagnosticReport"]
    assert "effectiveDateTime" not in res[1].content


def test_patient_with_no_records_gets_patient_resource_only():
    assert [r.resource_type for r in B.build_resources(make_patient(), [], NOW)] == ["Patient"]


def test_mapping_is_deterministic_and_ordered():
    recs = [make_record({**NOTE, "note": "B", "date": "2024-03-01"}, index=1),
            make_record({**NOTE_WITH_DX, "note": "A", "date": "2024-01-01"}, index=0)]
    one = B.build_resources(make_patient(), recs, NOW)
    two = B.build_resources(make_patient(), list(reversed(recs)), NOW)
    assert [r.model_dump() for r in one] == [r.model_dump() for r in two]
    assert [r.position for r in one] == list(range(len(one)))
    assert [r.resource_type for r in one] == ["Patient", "DocumentReference", "DocumentReference", "DiagnosticReport"]
    assert by_type(one, "DocumentReference")[0].content["context"]["period"]["start"] == "2024-01-01"


def test_bundle_assembly_and_full_validation():
    res = B.build_resources(make_patient(), [make_record(NOTE_WITH_DX), make_record({**NOTE, "note": "Second."}, index=1)], NOW)
    bundle = B.assemble_bundle(bundle_id_for(PATIENT_ID), NOW, list(reversed(res)))    # order is restored
    assert bundle["resourceType"] == "Bundle" and bundle["type"] == "collection" and bundle["id"] == bundle_id_for(PATIENT_ID)
    assert next(e["resource"]["resourceType"] for e in bundle["entry"]) == "Patient"
    assert all(e["fullUrl"] == f"urn:uuid:{e['resource']['id']}" for e in bundle["entry"])
    assert check_bundle(bundle) == [] and R4BSchemaValidator().validate_bundle(bundle) == []
    Bundle.model_validate(bundle)


def test_every_reference_resolves_inside_the_bundle():
    res = B.build_resources(make_patient(), [make_record(NOTE_WITH_DX)], NOW)
    bundle = B.assemble_bundle("b", NOW, res)
    urls = {e["fullUrl"] for e in bundle["entry"]}
    refs = [e["resource"]["subject"]["reference"] for e in bundle["entry"] if "subject" in e["resource"]]
    assert refs and set(refs) <= urls


def test_invalid_mapped_data_raises_build_error_without_leaking_values():
    bad = make_patient(patient_id="not a valid id!")            # violates FHIR id pattern
    with pytest.raises(FhirBuildError) as e:
        B.build_resources(bad, [], NOW)
    assert e.value.issues and e.value.issues[0].code.startswith("schema:")
    assert "not a valid id" not in str([i.model_dump() for i in e.value.issues])


def test_schema_validator_reports_nested_problems_by_path_only():
    res = B.build_resources(make_patient(), [], NOW)
    bundle = B.assemble_bundle("b", NOW, res)
    bundle["entry"][0]["resource"]["birthDate"] = "1980-13-45"
    bundle["entry"][0]["resource"]["unexpected"] = "SECRET-VALUE"
    issues = R4BSchemaValidator().validate_bundle(bundle)
    assert {"schema:value_error", "schema:extra_forbidden"} <= {i.code for i in issues}
    assert "SECRET-VALUE" not in str(issues) and "1980-13-45" not in str(issues)


def test_missing_required_elements_are_caught_by_library_and_domain_rules_together():
    """fhir.resources validates in two stages (it reports `code` first and `status`/`type` only once
    that passes), so the use case combines the library result with the domain rules."""
    bundle = {"resourceType": "Bundle", "entry": [{"fullUrl": "urn:uuid:a", "resource": {
        "resourceType": "DiagnosticReport", "id": "a"}}]}
    library = {i.path for i in R4BSchemaValidator().validate_bundle(bundle)}
    domain = {i.path for i in check_bundle(bundle)}
    assert "entry.0.resource.code" in library
    assert {"type", "entry[0].resource.status"} <= domain
