"""Library-independent FHIR rules (app.domain.fhir): ids, fingerprint, value sets, references."""

import base64
import copy
import hashlib

import pytest

from app.domain import fhir as F
from app.domain.fhir import check_bundle
from app.infrastructure.fhir.r4b_builder import R4BBundleBuilder
from tests.fhir_helpers import NOTE_WITH_DX, NOW, PATIENT_ID, make_patient, make_record

URN = F.full_url


@pytest.fixture
def bundle():
    b = R4BBundleBuilder()
    res = b.build_resources(make_patient(), [make_record(NOTE_WITH_DX)], NOW)
    return b.assemble_bundle(F.bundle_id_for(PATIENT_ID), NOW, res)


def codes(issues):
    return {i.code for i in issues}


def entry(bundle, rtype):
    return next(e for e in bundle["entry"] if e["resource"]["resourceType"] == rtype)


def test_valid_bundle_has_no_issues(bundle):
    assert check_bundle(bundle) == []


def test_deterministic_ids():
    assert F.bundle_id_for("p") == F.bundle_id_for("p") != F.bundle_id_for("q")
    assert F.diagnostic_report_id("r") == F.diagnostic_report_id("r") != "r"
    assert F.full_url("abc") == "urn:uuid:abc"


def test_value_set_violations_are_detected_even_though_library_accepts_them(bundle):
    entry(bundle, "Patient")["resource"]["gender"] = "banana"
    entry(bundle, "DocumentReference")["resource"]["status"] = "final"       # DiagnosticReport code, not DocRef
    entry(bundle, "DiagnosticReport")["resource"]["status"] = "nonsense"
    bundle["type"] = "pile"
    assert codes(check_bundle(bundle)) == {"invalid_code_value"}
    assert len(check_bundle(bundle)) == 4


def test_dangling_reference_is_detected_with_path_not_value(bundle):
    entry(bundle, "DocumentReference")["resource"]["context"]["related"][0]["reference"] = "urn:uuid:missing"
    issues = check_bundle(bundle)
    assert codes(issues) == {"dangling_reference"}
    assert issues[0].path.endswith("context.related[0].reference") and "missing" not in issues[0].message


def test_subject_must_be_the_bundles_patient(bundle):
    entry(bundle, "DiagnosticReport")["resource"]["subject"]["reference"] = URN("someone-else")
    assert {"dangling_reference", "subject_not_bundle_patient"} <= codes(check_bundle(bundle))


def test_duplicate_full_url_and_resource_id_are_detected(bundle):
    bundle["entry"].append(copy.deepcopy(bundle["entry"][1]))
    assert {"duplicate_full_url", "duplicate_resource_id"} <= codes(check_bundle(bundle))


def test_full_url_must_match_resource_id(bundle):
    bundle["entry"][1]["fullUrl"] = URN("other")
    assert "full_url_id_mismatch" in codes(check_bundle(bundle))


def test_exactly_one_patient_required(bundle):
    no_patient = {**bundle, "entry": [e for e in bundle["entry"] if e["resource"]["resourceType"] != "Patient"]}
    assert "patient_count" in codes(check_bundle(no_patient))
    two = {**bundle, "entry": bundle["entry"] + [copy.deepcopy(bundle["entry"][0])]}
    assert "patient_count" in codes(check_bundle(two))


def test_empty_bundle_and_missing_fields(bundle):
    assert "empty_bundle" in codes(check_bundle({"type": "collection", "entry": []}))
    del bundle["entry"][0]["fullUrl"]
    del bundle["entry"][1]["resource"]["id"]
    assert {"missing_full_url", "missing_id"} <= codes(check_bundle(bundle))


def test_attachment_consistency_checks(bundle):
    att = entry(bundle, "DocumentReference")["resource"]["content"][0]["attachment"]
    att["size"] += 1
    att["hash"] = base64.b64encode(hashlib.sha1(b"other").digest()).decode()
    assert {"attachment_size_mismatch", "attachment_hash_mismatch"} == codes(check_bundle(bundle))
    att["data"] = "%%%"
    assert "invalid_base64" in codes(check_bundle(bundle))


def test_fingerprint_changes_with_content_and_mapper_version(monkeypatch):
    p, r = make_patient(), make_record(NOTE_WITH_DX)
    base = F.compute_fingerprint(p, [r])
    assert base == F.compute_fingerprint(p, [r])
    assert base != F.compute_fingerprint(p, [])                                  # record set changed
    assert base != F.compute_fingerprint(make_patient(name="Other Name"), [r])   # patient changed
    monkeypatch.setattr(F, "MAPPER_VERSION", "fhir-r4b-mapper-2")
    assert base != F.compute_fingerprint(p, [r])                                 # mapping rules changed
