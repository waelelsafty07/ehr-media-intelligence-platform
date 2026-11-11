from datetime import UTC, date, datetime

from app.domain.canonical import IssueCode, Severity, SourceFormat, SourceMetadata
from app.domain.canonicalization import (
    build_canonical,
    build_unparseable,
    compute_source_hash,
    resolve_fields,
)

NOW = datetime(2025, 1, 1, tzinfo=UTC)


def _src(raw):
    return SourceMetadata(source_id="f#0", source_name="f", source_format=SourceFormat.JSON, record_index=0,
                          source_hash=compute_source_hash(raw), received_at=NOW)


def build(raw):
    return build_canonical(raw, _src(raw), today=date(2025, 1, 1))


def codes(rec, severity=None):
    return {(i.code, i.field) for i in rec.issues if severity is None or i.severity == severity}


def test_clean_record_has_no_issues():
    rec = build({"mrn": "000101", "name": "Alex Test", "dob": "1980-03-14", "sex": "M",
                 "note": "Cough.", "date": "2024-02-01"})
    assert rec.issues == [] and not rec.has_errors
    assert rec.patient.mrn == "000101" and rec.document.text == "Cough."


def test_missing_dob_is_warning_not_error_and_not_invented():
    rec = build({"mrn": "000102", "name": "Sam Case"})
    assert (IssueCode.MISSING_OPTIONAL, "date_of_birth") in codes(rec, Severity.WARNING)
    assert not rec.has_errors and rec.patient.date_of_birth is None


def test_missing_mrn_is_required_error():
    rec = build({"name": "No Mrn", "dob": "1980-01-01"})
    assert (IssueCode.MISSING_REQUIRED, "mrn") in codes(rec, Severity.ERROR) and rec.has_errors


def test_invalid_dob_is_error_and_message_hides_value():
    rec = build({"mrn": "000103", "dob": "31/02/1990"})
    assert (IssueCode.INVALID_VALUE, "date_of_birth") in codes(rec, Severity.ERROR)
    assert all("1990" not in i.message for i in rec.issues)


def test_ambiguous_dob_is_error():
    assert build({"mrn": "000103", "dob": "03/04/2020"}).has_errors


def test_recovered_formats_are_warnings():
    rec = build({"mrn": "MRN-000104", "dob": "03/14/1980"})
    assert {(IssueCode.RECOVERED_FORMAT, "mrn"), (IssueCode.RECOVERED_FORMAT, "date_of_birth")} <= codes(rec)
    assert not rec.has_errors


def test_alias_mapping_and_split_name():
    rec = build({"Medical Record Number": "000105", "first_name": "Ada", "surname": "Lovelace",
                 "Birth Date": "1990-01-01", "SEX": "female", "clinical_note": "Text"})
    assert (rec.patient.mrn, rec.patient.full_name, rec.patient.gender.value) == ("000105", "Ada Lovelace", "female")


def test_diagnoses_are_split_but_never_invented():
    assert [d.description for d in build({"mrn": "000106", "diagnosis": "Asthma; Hypertension"}).diagnostics] == [
        "Asthma", "Hypertension"]
    assert build({"mrn": "000106"}).diagnostics == []


def test_content_hash_ignores_formatting_but_not_content():
    a = build({"mrn": "MRN-000101", "dob": "03/14/1980", "note": "Chest  pain.", "date": "2024-02-01"})
    b = build({"mrn": "000101", "dob": "1980-03-14", "note": "chest pain.", "date": "2024-02-01"})
    c = build({"mrn": "000101", "dob": "1980-03-14", "note": "different", "date": "2024-02-01"})
    assert a.content_hash() == b.content_hash() != c.content_hash()


def test_source_hash_is_order_independent_and_record_id_stable():
    assert compute_source_hash({"a": 1, "b": 2}) == compute_source_hash({"b": 2, "a": 1})
    assert build({"mrn": "000101"}).record_id == build({"mrn": "000101"}).record_id


def test_unparseable_placeholder_is_rejected():
    assert build_unparseable(_src({"x": 1}), "too_few_columns").has_errors


def test_resolve_fields_ignores_blank_values():
    assert resolve_fields({"mrn": "", "MRN ": "000107"}) == {"mrn": "000107"}
