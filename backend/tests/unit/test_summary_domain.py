"""Summary domain: context building, safe parsing, grounding checks, deterministic parts."""

import copy
import json
from datetime import UTC, date, datetime

import pytest

from app.domain.errors import PatientIsolationError, SummaryOutputError
from app.domain.summary import (
    DISCLAIMER,
    assemble_summary,
    build_summary_context,
    parse_model_output,
    verify_grounding,
    word_count,
)
from tests.fhir_helpers import NOW, PATIENT_ID, make_patient, make_record
from tests.summary_helpers import R2_RAW, as_json, good_output, records

NOW2 = datetime(2025, 2, 2, tzinfo=UTC)


def ctx(recs=None, patient=None, **kw):
    r1, r2 = records()
    return build_summary_context(patient or make_patient(), recs or [r1, r2],
                                 max_records=kw.get("max_records", 20), max_chars=kw.get("max_chars", 4000))


def issue_codes(output_dict, context=None):
    context = context or ctx()
    return {i.code for i in verify_grounding(parse_model_output(as_json(output_dict)), context)}


@pytest.fixture
def ids():
    r1, r2 = records()
    return r1.record_id, r2.record_id


@pytest.fixture
def good(ids):
    return good_output(*ids)


# ---------------------------------------------------------------- context
def test_context_is_newest_first_and_hash_is_stable():
    r1, r2 = records()
    a, b = ctx([r1, r2]), ctx([r2, r1])
    assert [d.record_id for d in a.documents] == [r2.record_id, r1.record_id]
    assert a.content_hash == b.content_hash and a.documents == b.documents


def test_undated_records_sort_last_and_empty_records_are_skipped():
    undated = make_record({"mrn": "000101", "note": "No date here."}, index=2)
    empty = make_record({"mrn": "000101"}, index=3)
    r1, r2 = records()
    c = ctx([undated, empty, r1, r2])
    assert [d.record_id for d in c.documents][-1] == undated.record_id and len(c.documents) == 3


def test_caps_truncate_and_flag():
    r1, r2 = records()
    c = ctx([r1, r2], max_records=1, max_chars=20)
    assert len(c.documents) == 1 and c.omitted_count == 1
    assert c.documents[0].truncated and len(c.documents[0].text) == 20


def test_hash_changes_with_content_and_with_generation_limits():
    r1, r2 = records()
    base = ctx([r1, r2]).content_hash
    assert base != ctx([r1]).content_hash                                   # source set changed
    assert base != ctx([r1, r2], max_chars=50).content_hash                 # configuration changed
    assert base != ctx([r1, r2], max_records=1).content_hash
    edited = make_record({**R2_RAW, "note": "Follow-up visit. Stress test abnormal."}, index=1)
    assert base != ctx([r1, edited]).content_hash                           # content edited


def test_foreign_record_is_rejected_by_isolation_check():
    r1, _ = records()
    foreign = make_record(R2_RAW, patient_id="99999999-9999-4999-8999-999999999999", index=1)
    with pytest.raises(PatientIsolationError):
        ctx([r1, foreign])


# ---------------------------------------------------------------- safe parsing
def test_valid_output_parses(good):
    out = parse_model_output(as_json(good))
    assert out.chief_concern.text == "Chest tightness on exertion" and len(out.key_diagnoses) == 2


@pytest.mark.parametrize("text,code", [
    ("", "empty_output"), ("   ", "empty_output"), ("not json at all", "malformed_json"),
    ("{broken", "malformed_json"), ("[1, 2]", "output_not_an_object"),
    ('"a string"', "output_not_an_object"),
])
def test_unparseable_outputs_raise_with_codes_only(text, code):
    with pytest.raises(SummaryOutputError) as e:
        parse_model_output(text)
    assert e.value.codes == [code]
    assert not text.strip() or text.strip() not in str(e.value)       # content is never echoed


def test_json_wrapped_in_prose_or_fences_is_tolerated(good):
    assert parse_model_output("Here you go:\n```json\n" + as_json(good) + "\n```").summary_text


def test_schema_violations_are_reported_by_location_and_rule(good):
    bad = copy.deepcopy(good)
    bad["unexpected"] = "x"
    del bad["summary_text"]
    bad["key_diagnoses"][0]["certainty"] = "definitely"
    with pytest.raises(SummaryOutputError) as e:
        parse_model_output(as_json(bad))
    joined = " ".join(e.value.codes)
    assert "unexpected" in joined and "summary_text" in joined and "certainty" in joined
    assert "definitely" not in joined


def test_summary_over_200_words_is_rejected(good):
    good["summary_text"] = " ".join(["word"] * 201)
    with pytest.raises(SummaryOutputError):
        parse_model_output(as_json(good))
    good["summary_text"] = " ".join(["word"] * 200)
    assert word_count(parse_model_output(as_json(good)).summary_text) == 200


def test_empty_summary_text_and_empty_citations_are_rejected(good):
    with pytest.raises(SummaryOutputError):
        parse_model_output(as_json({**good, "summary_text": "  "}))
    bad = copy.deepcopy(good)
    bad["chief_concern"]["source_record_ids"] = []
    with pytest.raises(SummaryOutputError):
        parse_model_output(as_json(bad))


# ---------------------------------------------------------------- grounding
def test_faithful_output_has_no_grounding_issues(good):
    assert issue_codes(good) == set()


def test_citing_a_record_outside_the_patient_context_is_flagged(good):
    foreign = make_record(R2_RAW, patient_id="99999999-9999-4999-8999-999999999999", index=7)
    good["chief_concern"]["source_record_ids"] = [foreign.record_id]
    assert "unknown_source_record" in issue_codes(good, ctx([records()[0]]))


def test_quote_must_exist_in_the_cited_record(good, ids):
    good["chief_concern"]["evidence_quote"] = "severe crushing chest pain radiating to the arm"
    assert "quote_not_in_cited_source" in issue_codes(good)
    good = good_output(*ids)
    good["chief_concern"]["source_record_ids"] = [ids[1]]          # quote exists, but in the OTHER record
    assert "quote_not_in_cited_source" in issue_codes(good)


def test_invented_diagnosis_is_flagged(good):
    good["key_diagnoses"].append({"name": "diabetes", "certainty": "stated", "temporal": "current",
                                  "text": "Diabetes", "evidence_quote": "History of hypertension",
                                  "source_record_ids": [good["chief_concern"]["source_record_ids"][0]]})
    assert "diagnosis_not_in_quote" in issue_codes(good)
    good["key_diagnoses"][-1]["evidence_quote"] = "type 2 diabetes mellitus"
    assert "quote_not_in_cited_source" in issue_codes(good)


def test_uncertain_finding_presented_as_confirmed_is_flagged(good):
    good["key_diagnoses"][1]["certainty"] = "stated"
    assert "uncertainty_lost" in issue_codes(good)


def test_historical_finding_presented_as_current_is_flagged(good):
    good["key_diagnoses"][0]["temporal"] = "current"
    assert "history_marked_current" in issue_codes(good)


def test_negated_diagnosis_is_flagged():
    rec = make_record({"mrn": "000101", "note": "No evidence of pneumonia on review.", "date": "2024-03-01"})
    out = {"chief_concern": None, "anomalies": [], "summary_text": "No pneumonia was found.",
           "key_diagnoses": [{"name": "pneumonia", "certainty": "stated", "temporal": "current", "text": "Pneumonia",
                              "evidence_quote": "No evidence of pneumonia", "source_record_ids": [rec.record_id]}]}
    assert "negated_diagnosis" in issue_codes(out, ctx([rec]))


def test_unsupported_dates_and_numbers_in_prose_are_flagged(good):
    good["summary_text"] = "Chest tightness was documented on 2023-12-25."
    assert "summary_number_or_date_unsupported" in issue_codes(good)
    good["summary_text"] = "Blood pressure was 180 over 110."
    assert "summary_number_or_date_unsupported" in issue_codes(good)
    good["summary_text"] = "There were 2 visits on 2023-11-10 and 2024-02-01."        # single digits / source dates fine
    assert issue_codes(good) == set()


def test_advice_language_is_flagged_unless_the_source_says_it(good):
    good["summary_text"] = "The patient should start taking a statin."
    assert "summary_advice_language" in issue_codes(good)
    rec = make_record({"mrn": "000101", "note": "Doctor recommended rest. Mild cough.", "date": "2024-03-01"})
    out = {"chief_concern": None, "key_diagnoses": [], "anomalies": [],
           "summary_text": "A doctor recommended rest."}
    assert issue_codes(out, ctx([rec])) == set()         # a documented fact, not new advice


# ---------------------------------------------------------------- deterministic parts
def test_assembled_summary_has_deterministic_parts_and_metadata(good):
    context = ctx()
    s = assemble_summary(parse_model_output(as_json(good)), context, model_identifier="m", prompt_version="p1",
                         generated_at=NOW2)
    assert s.patient_id == PATIENT_ID and s.model_identifier == "m" and s.prompt_version == "p1"
    assert s.generated_at == NOW2 and s.disclaimer == DISCLAIMER and s.source_content_hash == context.content_hash
    assert s.source_record_ids == [d.record_id for d in context.documents]
    assert [r.document_date for r in s.recent_records] == [date(2024, 2, 1), date(2023, 11, 10)]
    assert s.recent_records[1].diagnoses == ["Hypertension"]
    assert word_count(s.summary_text) <= 200
    q = s.confidence_or_quality_indicator
    assert q.level == "high" and all(q.factors.values()) and "NOT a model confidence" in q.explanation


def test_missing_information_is_reported_not_guessed(good):
    r1, _ = records()
    undated = make_record({"mrn": "000101", "note": "A note without a date."}, index=2)
    out = {"chief_concern": None, "key_diagnoses": [], "anomalies": [], "summary_text": "Limited information."}
    s = assemble_summary(parse_model_output(as_json(out)), ctx([undated], patient=make_patient(dob=None)),
                         model_identifier="m", prompt_version="p", generated_at=NOW)
    assert set(s.missing_information) >= {
        "Date of birth is not recorded.", "No chief concern is documented in the available records.",
        "No diagnoses are explicitly stated in the available records.", "1 of 1 records have no document date."}
    assert s.confidence_or_quality_indicator.level == "low"
    full = assemble_summary(parse_model_output(as_json(good)), ctx(), model_identifier="m", prompt_version="p",
                            generated_at=NOW)
    assert full.missing_information == []
    assert r1.record_id  # fixture sanity


def test_truncation_and_omission_are_disclosed(good):
    s = assemble_summary(parse_model_output(as_json(good)), ctx(max_records=1, max_chars=10), model_identifier="m",
                         prompt_version="p", generated_at=NOW)
    assert any("truncated" in m for m in s.missing_information) and any("not included" in m for m in s.missing_information)


def test_recent_record_excerpt_is_shortened():
    long_rec = make_record({"mrn": "000101", "note": "word " * 100, "date": "2024-05-05"})
    s = assemble_summary(parse_model_output(as_json({"summary_text": "x"})), ctx([long_rec]),
                         model_identifier="m", prompt_version="p", generated_at=NOW)
    assert len(s.recent_records[0].excerpt) == 160 and s.recent_records[0].excerpt.endswith("…")


def test_json_roundtrip_of_final_summary(good):
    s = assemble_summary(parse_model_output(as_json(good)), ctx(), model_identifier="m", prompt_version="p",
                         generated_at=NOW)
    assert type(s).model_validate(json.loads(s.model_dump_json())) == s
