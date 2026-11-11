"""Search domain: query validation, extraction from stored resources, content hashes, snippets."""

import base64
from datetime import date

import pytest
from pydantic import ValidationError

from app.domain.search import (
    MAX_QUERY_CHARS,
    MAX_RESULTS,
    SearchQuery,
    build_snippet,
    document_date_from_resource,
    document_text_from_resource,
    index_content_hash,
    make_doc_id,
    summary_index_text,
    summary_record_date,
)
from app.domain.summary import assemble_summary, build_summary_context, parse_model_output
from tests.fhir_helpers import NOW, make_patient
from tests.summary_helpers import as_json, good_output, records


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


# ---------------------------------------------------------------- query validation
def test_query_is_normalized_and_defaults_apply():
    q = SearchQuery(query="  chest \n  pain  ")
    assert q.query == "chest pain" and q.limit == MAX_RESULTS == 5 and q.patient_id is None
    assert q.filters.resource_types is None and q.filters.patient_id is None


@pytest.mark.parametrize("bad", [
    {"query": ""}, {"query": "   \n "}, {"query": "x" * (MAX_QUERY_CHARS + 1)},
    {"query": "x", "limit": 0}, {"query": "x", "limit": MAX_RESULTS + 1}, {"query": "x", "limit": -1},
    {"query": "x", "resource_types": []}, {"query": "x", "resource_types": ["Patient"]},
    {"query": "x", "resource_types": ["DocumentReference", "Observation"]},
    {"query": "x", "date_from": "2024-05-01", "date_to": "2024-01-01"}, {"query": "x", "date_from": "not-a-date"},
    {"query": "x", "patient_id": ""}, {"query": "x", "patient_id": "p" * 65},
    {"query": "x", "min_score": -0.1}, {"query": "x", "min_score": 1.1}, {"query": "x", "unexpected": 1},
    {}, {"query": None},
])
def test_invalid_queries_are_rejected(bad):
    with pytest.raises(ValidationError):
        SearchQuery(**bad)


def test_valid_filters_roundtrip():
    q = SearchQuery(query="x", patient_id="p1", resource_types=["ClinicalSummary", "DocumentReference", "ClinicalSummary"],
                    date_from="2024-01-01", date_to="2024-01-01", limit=2, min_score=0.2)
    f = q.filters
    assert f.patient_id == "p1" and f.resource_types == ("ClinicalSummary", "DocumentReference")   # deduped, sorted
    assert f.date_from == f.date_to == date(2024, 1, 1) and q.limit == 2 and q.min_score == 0.2


def test_equal_date_bounds_are_allowed():
    assert SearchQuery(query="x", date_from="2024-02-02", date_to="2024-02-02")


# ---------------------------------------------------------------- extraction
def test_document_text_and_date_from_a_documentreference():
    res = {"resourceType": "DocumentReference",
           "content": [{"attachment": {"contentType": "text/plain", "data": b64("  Chest pain.  ")}}],
           "context": {"period": {"start": "2024-02-01"}}}
    assert document_text_from_resource(res) == "Chest pain."
    assert document_date_from_resource(res) == date(2024, 2, 1)


@pytest.mark.parametrize("res", [
    {}, {"content": []}, {"content": [{"attachment": {}}]}, {"content": [{"attachment": {"data": "%%%"}}]},
    {"content": [{"attachment": {"data": b64("   ")}}]}, {"content": [{"attachment": {"data": base64.b64encode(b"\xff\xfe").decode()}}]},
])
def test_unusable_attachments_give_none(res):
    assert document_text_from_resource(res) is None


@pytest.mark.parametrize("res,expected", [
    ({}, None), ({"context": {}}, None), ({"context": {"period": {"start": "garbage"}}}, None),
    ({"context": {"period": {"start": "2024-03-04T10:00:00Z"}}}, date(2024, 3, 4)),
])
def test_document_date_extraction(res, expected):
    assert document_date_from_resource(res) == expected


def test_summary_text_and_date():
    r1, r2 = records()
    ctx = build_summary_context(make_patient(), [r1, r2], max_records=20, max_chars=4000)
    s = assemble_summary(parse_model_output(as_json(good_output(r1.record_id, r2.record_id))), ctx,
                         model_identifier="m", prompt_version="p", generated_at=NOW)
    text = summary_index_text(s)
    assert text.startswith("Chief concern: Chest tightness on exertion.") and "Diagnoses: hypertension, angina." in text
    assert s.summary_text in text
    assert summary_record_date(s) == date(2024, 2, 1)
    undated = s.model_copy(update={"recent_records": [r.model_copy(update={"document_date": None}) for r in s.recent_records]})
    assert summary_record_date(undated) is None


# ---------------------------------------------------------------- identity and hashes
def test_doc_id_is_typed_and_stable():
    assert make_doc_id("DocumentReference", "abc") == "DocumentReference:abc"
    assert make_doc_id("ClinicalSummary", "abc") != make_doc_id("DocumentReference", "abc")


def test_content_hash_changes_with_model_type_date_or_text():
    base = index_content_hash("m1", "DocumentReference", date(2024, 1, 1), "text")
    assert base == index_content_hash("m1", "DocumentReference", date(2024, 1, 1), "text")
    for other in (index_content_hash("m2", "DocumentReference", date(2024, 1, 1), "text"),
                  index_content_hash("m1", "ClinicalSummary", date(2024, 1, 1), "text"),
                  index_content_hash("m1", "DocumentReference", date(2024, 1, 2), "text"),
                  index_content_hash("m1", "DocumentReference", None, "text"),
                  index_content_hash("m1", "DocumentReference", date(2024, 1, 1), "text2")):
        assert other != base


# ---------------------------------------------------------------- snippets
def test_short_text_is_returned_whole_and_whitespace_normalized():
    assert build_snippet("A  short\n note.", "note") == "A short note."


def test_snippet_is_centered_on_the_first_query_term_with_ellipses():
    text = ("filler " * 40) + "the patient reported palpitations overnight " + ("tail " * 40)
    s = build_snippet(text, "palpitations")
    assert "palpitations" in s and s.startswith("…") and s.endswith("…") and len(s) <= 206


def test_snippet_without_a_match_starts_at_the_beginning_and_is_bounded():
    s = build_snippet("alpha " * 100, "zzz")
    assert s.startswith("alpha") and s.endswith("…") and len(s) <= 203


def test_snippet_is_always_a_substring_of_the_source_text():
    text = "Seen for chest tightness on exertion. " * 20
    s = build_snippet(text, "tightness").strip("…")
    assert s in " ".join(text.split())
