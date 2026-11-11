import logging
from datetime import date

import pytest

from app.domain.errors import EmbeddingUnavailableError, PatientNotFoundError, VectorStoreError
from app.domain.fhir import FhirStatus
from app.domain.search import IndexEntry, SearchQuery
from tests.fhir_helpers import make_record
from tests.search_helpers import (
    PID,
    FakeVectorStore,
    RecordingEmbedder,
    add_patient,
    build,
    default_world,
    generate_fhir,
)
from tests.summary_helpers import R1_RAW

OTHER = "22222222-2222-4222-8222-222222222222"


def run(search, **kw):
    kw.setdefault("query", "chest tightness")
    return search.execute(SearchQuery(**kw))


@pytest.fixture
def world():
    store = default_world()
    index, search, embedder, vstore = build(store)
    index.execute(PID)
    return store, index, search, embedder, vstore


def two_patient_world():
    """Alex (chest tightness) and Sam, whose note is EVEN MORE similar to the query than Alex's."""
    store = default_world()
    add_patient(store, OTHER, "000222", "Sam Other",
                [{"note": "Chest tightness chest tightness chest tightness on exertion.", "date": "2024-03-03"}])
    generate_fhir(store, OTHER)
    index, search, embedder, vstore = build(store)
    index.execute(PID)
    index.execute(OTHER)
    return store, index, search, embedder, vstore


def test_results_are_ranked_by_similarity_with_rank_numbers_and_no_direct_identifiers(world):
    _, _, search, _, _ = world
    out = run(search)
    assert [i.rank for i in out.items] == [1, 2, 3] and out.items == sorted(out.items, key=lambda i: -i.score)
    assert all(0 < i.score <= 1 for i in out.items) and out.embedding_kind == "lexical"
    top = out.items[0]
    assert top.patient_id == PID
    assert not hasattr(top, "patient_name") and not hasattr(top, "mrn")        # search results carry no name / MRN
    assert out.candidates_considered == 3 and out.stale_results_dropped == 0 and out.took_ms >= 0


def test_limit_is_respected_and_never_exceeds_five():
    store = default_world(summary=False)
    for i in range(8):
        add_patient(store, f"00000000-0000-4000-8000-{i:012d}", f"10000{i}", f"Patient {i}",
                    [{"note": f"Chest tightness case number {i}.", "date": f"2024-04-0{i + 1}"}])
        generate_fhir(store, f"00000000-0000-4000-8000-{i:012d}")
    index, search, _, _ = build(store)
    index.execute_all()
    assert len(run(search).items) == 5 and len(run(search, limit=2).items) == 2 and len(run(search, limit=1).items) == 1


def test_patient_filter_is_applied_before_ranking_so_other_patients_never_appear():
    _, _, search, _, _ = two_patient_world()
    unscoped = run(search)
    assert unscoped.items[0].patient_id == OTHER                    # Sam's text is the better match overall
    scoped = run(search, patient_id=PID)
    assert scoped.items and {i.patient_id for i in scoped.items} == {PID}
    assert {i.patient_id for i in run(search, patient_id=OTHER).items} == {OTHER}


def test_patient_scoped_search_cannot_leak_via_candidate_overfetch(world):
    """Even when the store is asked for many candidates, nothing outside the patient scope survives."""
    _, _, search, _, vstore = two_patient_world()
    scoped = run(search, patient_id=PID, limit=5)
    assert all(i.patient_id == PID for i in scoped.items)
    assert all(h.patient_id == PID for h in vstore.query([1.0] + [0.0] * 383, model_identifier="hashing-ngram-v1-384",
                                                         filters=SearchQuery(query="x", patient_id=PID).filters, limit=50))


def test_unknown_patient_filter_raises_not_found(world):
    with pytest.raises(PatientNotFoundError):
        run(world[2], patient_id="missing")


def test_resource_type_filter(world):
    _, _, search, _, _ = world
    assert {i.resource_type for i in run(search, resource_types=["DocumentReference"]).items} == {"DocumentReference"}
    summaries = run(search, resource_types=["ClinicalSummary"]).items
    assert [i.resource_type for i in summaries] == ["ClinicalSummary"] and summaries[0].ai_generated
    assert {i.resource_type for i in run(search, resource_types=["ClinicalSummary", "DocumentReference"]).items} == {
        "ClinicalSummary", "DocumentReference"}


def test_date_range_is_inclusive_and_excludes_undated_records(world):
    store, index, search, _, _ = world
    docs = lambda **kw: sorted(i.record_date for i in run(search, resource_types=["DocumentReference"], **kw).items)
    assert docs(date_from="2024-01-01") == [date(2024, 2, 1)]
    assert docs(date_to="2023-11-10") == [date(2023, 11, 10)]
    assert docs(date_from="2023-11-10", date_to="2024-02-01") == [date(2023, 11, 10), date(2024, 2, 1)]
    assert docs(date_from="2024-02-01", date_to="2024-02-01") == [date(2024, 2, 1)]
    assert docs(date_from="2025-01-01") == []
    undated = make_record({"mrn": "000101", "note": "Chest tightness, undated note."}, PID, 9)
    store.records[undated.record_id] = undated
    generate_fhir(store, PID)
    index.execute(PID)
    assert any(i.record_date is None for i in run(search, resource_types=["DocumentReference"]).items)
    assert all(i.record_date is not None for i in run(search, resource_types=["DocumentReference"], date_from="2000-01-01").items)


def test_empty_index_and_non_matching_queries_return_no_results():
    store = default_world()
    _, search, _, _ = build(store)                                    # nothing indexed yet
    out = run(search)
    assert out.items == [] and out.candidates_considered == 0
    index, search2, _, _ = build(store)
    index.execute(PID)
    assert run(search2, query="zzzz qqqq").items == []                # zero similarity is never a "result"


def test_min_score_can_only_raise_the_bar(world):
    _, _, search, _, _ = world
    everything = run(search).items
    high = run(search, min_score=everything[0].score).items
    assert [i.resource_id for i in high] == [everything[0].resource_id]
    assert run(search, min_score=0.9999).items == []


def test_embedding_and_vector_store_failures_are_raised_as_domain_errors(world):
    _, _, search, embedder, vstore = world
    vstore.fail_on["query"] = 1
    with pytest.raises(VectorStoreError):
        run(search)
    embedder.fail = True
    with pytest.raises(EmbeddingUnavailableError):
        run(search)


def test_hits_for_resources_no_longer_in_the_fhir_store_are_dropped_before_reindexing(world):
    store, _, search, _, _ = world
    gone = next(r for r in store.fhir_resources.values() if r.resource_type == "DocumentReference")
    del store.fhir_resources[(gone.resource_type, gone.resource_id)]
    out = run(search)
    assert gone.resource_id not in {i.resource_id for i in out.items} and out.stale_results_dropped == 1


def test_hits_whose_text_changed_since_indexing_are_dropped(world):
    store, _, search, _, _ = world
    import base64
    res = next(r for r in store.fhir_resources.values() if r.resource_type == "DocumentReference")
    res.content["content"][0]["attachment"]["data"] = base64.b64encode(b"Completely different content now.").decode()
    out = run(search)
    assert res.resource_id not in {i.resource_id for i in out.items} and out.stale_results_dropped == 1


def test_failed_or_invalid_fhir_state_hides_document_hits(world):
    store, _, search, _, _ = world
    store.fhir_state[PID] = store.fhir_state[PID].model_copy(update={"status": FhirStatus.INVALID})
    out = run(search)
    assert {i.resource_type for i in out.items} <= {"ClinicalSummary"} and out.stale_results_dropped == 2


def test_stale_summaries_are_not_presented_as_current_even_before_reindexing(world):
    store, _, search, _, vstore = world
    assert [i for i in run(search).items if i.resource_type == "ClinicalSummary"]
    new = make_record({"mrn": "000101", "note": "Later visit, symptom free.", "date": "2024-07-01"}, PID, 5)
    store.records[new.record_id] = new                              # source changed -> summary now stale; index untouched
    assert any(e.resource_type == "ClinicalSummary" for e in vstore.entries.values())
    out = run(search)
    assert not [i for i in out.items if i.resource_type == "ClinicalSummary"] and out.stale_results_dropped >= 1


def test_failed_summary_is_not_returned(world):
    store, _, search, _, _ = world
    for row in store.summaries.values():
        row.status = type(row.status)("failed")
    assert not [i for i in run(search).items if i.resource_type == "ClinicalSummary"]


def test_a_summary_replaced_by_a_newer_one_only_the_current_id_is_served(world):
    _, _, search, _, vstore = world
    old_ids = {e.resource_id for e in vstore.entries.values() if e.resource_type == "ClinicalSummary"}
    # simulate: index keeps an entry for a summary id that is no longer the latest generated one
    old = next(e for e in vstore.entries.values() if e.resource_type == "ClinicalSummary")
    vstore.entries["ClinicalSummary:old-id"] = old.model_copy(update={"doc_id": "ClinicalSummary:old-id", "resource_id": "old-id"})
    out = run(search, resource_types=["ClinicalSummary"])
    assert {i.resource_id for i in out.items} == old_ids and out.stale_results_dropped == 1


def test_an_index_entry_labelled_with_the_wrong_patient_is_dropped(world):
    """Identity comes from the stored record, never from the index metadata or similarity."""
    store, _, search, _, vstore = world
    add_patient(store, OTHER, "000222", "Sam Other", [{"note": "Unrelated sprain.", "date": "2024-03-03"}])
    victim = next(e for e in vstore.entries.values() if e.resource_type == "DocumentReference")
    vstore.entries[victim.doc_id] = victim.model_copy(update={"patient_id": OTHER})      # tampered metadata
    out = run(search, resource_types=["DocumentReference"])
    assert victim.resource_id not in {i.resource_id for i in out.items}
    assert all(i.patient_id != OTHER for i in run(search, patient_id=OTHER).items)


def test_entries_for_deleted_patients_are_not_returned(world):
    store, _, search, _, _ = world
    del store.patients[PID]
    assert run(search).items == []


def test_ties_are_broken_deterministically_by_date_then_id():
    store = default_world(summary=False)
    for r in list(store.records.values()):
        del store.records[r.record_id]
    for i, day in enumerate(("2024-01-01", "2024-03-03", "2024-02-02")):
        rec = make_record({"mrn": "000101", "note": "Identical words here.", "date": day}, PID, i)
        store.records[rec.record_id] = rec
    generate_fhir(store, PID)
    index, search, _, _ = build(store)
    index.execute(PID)
    a = run(search, query="identical words")
    assert len({i.score for i in a.items}) == 1                       # a genuine tie ...
    assert [i.record_date.isoformat() for i in a.items] == ["2024-03-03", "2024-02-02", "2024-01-01"]   # ... newest first
    assert [i.resource_id for i in a.items] == [i.resource_id for i in run(search, query="identical words").items]


def test_snippets_come_from_the_stored_text_and_summaries_are_flagged_ai_generated(world):
    _, _, search, _, _ = world
    out = run(search, query="possible angina stress test")
    doc = next(i for i in out.items if i.resource_type == "DocumentReference")
    assert "angina" in doc.snippet.lower() and doc.snippet.strip("…") in " ".join(R1_RAW["note"].split()) and not doc.ai_generated
    assert doc.source_record_id
    summary = next(i for i in out.items if i.resource_type == "ClinicalSummary")
    assert summary.ai_generated and summary.source_record_id is None


def test_the_query_text_is_never_logged(world, caplog):
    _, _, search, _, _ = world
    caplog.set_level(logging.DEBUG)
    run(search, query="Jane Doe 555-0100 chest pain")
    text = " ".join(str(v) for rec in caplog.records for v in (rec.getMessage(), *rec.__dict__.values())).lower()
    assert "jane doe" not in text and "555-0100" not in text and "search_completed" in text


def test_unrelated_embedding_models_are_never_compared(world):
    _, _, _, _, vstore = world
    other = RecordingEmbedder(dimension=64)
    store = default_world()
    _, search64, _, _ = build(store, embedder=other, vstore=vstore)      # store holds 384-d entries only
    assert run(search64).items == []
    assert all(isinstance(e, IndexEntry) for e in vstore.entries.values())


class LeakyStore(FakeVectorStore):
    """A buggy/hostile store that ignores the patient filter entirely."""

    def query(self, vector, *, model_identifier, filters, limit):
        return super().query(vector, model_identifier=model_identifier,
                             filters=filters.model_copy(update={"patient_id": None}), limit=limit)


def test_a_buggy_vector_store_that_ignores_the_patient_filter_still_cannot_leak_other_patients():
    store = default_world()
    add_patient(store, OTHER, "000222", "Sam Other", [{"note": "Chest tightness chest tightness on exertion.", "date": "2024-03-03"}])
    generate_fhir(store, OTHER)
    leaky = LeakyStore()
    index, search, _, _ = build(store, vstore=leaky)
    index.execute(PID)
    index.execute(OTHER)
    assert {e.patient_id for e in leaky.entries.values()} == {PID, OTHER}
    unscoped = run(search)
    assert OTHER in {i.patient_id for i in unscoped.items}                  # sanity: the other patient is findable ...
    scoped = run(search, patient_id=PID)
    assert scoped.items and {i.patient_id for i in scoped.items} == {PID}   # ... but never when scoped to PID
