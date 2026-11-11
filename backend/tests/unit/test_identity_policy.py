from datetime import UTC, date, datetime

from app.domain.canonical import Gender, PatientInfo
from app.domain.entities import Patient
from app.domain.identity import IdentityOutcome as O
from app.domain.identity import resolve_identity
from app.domain.normalization import name_key


def patient(pid="p1", mrn="000101", name="Alex Test", dob=date(1980, 3, 14), gender=Gender.MALE):
    return Patient(patient_id=pid, mrn=mrn, full_name=name, name_key=name_key(name) if name else None,
                   date_of_birth=dob, gender=gender, created_at=datetime(2025, 1, 1, tzinfo=UTC))


def info(mrn="000101", name="Alex Test", dob=date(1980, 3, 14), gender=Gender.MALE):
    return PatientInfo(mrn=mrn, full_name=name, date_of_birth=dob, gender=gender)


def test_new_patient_when_nothing_matches():
    d = resolve_identity(info(mrn="000999"), None, [])
    assert d.outcome == O.NEW_PATIENT and d.patient_id is None


def test_same_mrn_dob_name_matches():
    d = resolve_identity(info(name="TEST, Alex"), patient(), [])
    assert d.outcome == O.MATCHED and d.patient_id == "p1" and d.rationale_code == "mrn_dob_name_agree"


def test_same_mrn_missing_dob_matches_with_partial_rationale():
    d = resolve_identity(info(dob=None), patient(), [])
    assert d.outcome == O.MATCHED and d.rationale_code == "mrn_match_partial_demographics"


def test_same_mrn_different_dob_is_conflict_never_linked():
    d = resolve_identity(info(dob=date(1991, 7, 9)), patient(), [])
    assert d.outcome == O.CONFLICT and d.patient_id is None and d.related_patient_ids == ["p1"]


def test_same_mrn_different_name_is_ambiguous():
    d = resolve_identity(info(name="Someone Else"), patient(), [])
    assert d.outcome == O.AMBIGUOUS and d.rationale_code == "mrn_match_name_differs"


def test_same_mrn_different_gender_is_ambiguous():
    assert resolve_identity(info(gender=Gender.FEMALE), patient(), []).outcome == O.AMBIGUOUS


def test_unknown_gender_does_not_create_ambiguity():
    assert resolve_identity(info(gender=Gender.UNKNOWN), patient(), []).outcome == O.MATCHED


def test_same_name_and_dob_with_new_mrn_is_ambiguous_not_merged():
    d = resolve_identity(info(mrn="000555"), None, [patient()])
    assert d.outcome == O.AMBIGUOUS and d.patient_id is None and d.rationale_code == "same_name_dob_different_mrn"


def test_name_only_similarity_never_blocks_or_merges():
    # Repository finds no same-name-AND-same-DOB patient, so a name twin with another DOB is a NEW patient.
    d = resolve_identity(info(mrn="000103", dob=date(1991, 7, 9)), None, [])
    assert d.outcome == O.NEW_PATIENT and "Name-only" in d.rationale


def test_no_mrn_is_never_auto_resolved():
    assert resolve_identity(info(mrn=None), None, []).outcome == O.AMBIGUOUS
