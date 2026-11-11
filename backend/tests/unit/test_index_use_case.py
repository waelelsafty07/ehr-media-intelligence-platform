import json

import pytest

from app.domain.entities import AuditEventType as A
from app.domain.errors import EmbeddingUnavailableError, PatientNotFoundError, VectorStoreError
from app.domain.fhir import FhirStatus
from app.domain.search import make_doc_id
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


def types(vstore, patient_id=PID):
    return sorted(e.resource_type for e in vstore.entries.values() if e.patient_id == patient_id)


def audit(store, kind):
    return [e for e in store.audit if e.event_type == kind]


def test_indexes_documentreferences_and_the_current_summary_with_metadata():
    store = default_world()
    index, _, _, vstore = build(store)
    r = index.execute(PID)
    assert (r.indexed, r.unchanged, r.removed, r.skipped) == (3, 0, 0, [])
    assert types(vstore) == ["ClinicalSummary", "DocumentReference", "DocumentReference"]
    by_type = {}
    for e in vstore.entries.values():
        by_type.setdefault(e.resource_type, []).append(e)
        assert e.patient_id == PID and e.model_identifier == "hashing-ngram-v1-384" and len(e.embedding) == 384
        assert e.doc_id == make_doc_id(e.resource_type, e.resource_id) and e.text
    docs = sorted(by_type["DocumentReference"], key=lambda e: e.record_date)
    assert [d.record_date.isoformat() for d in docs] == ["2023-11-10", "2024-02-01"]
    assert all(d.source_record_id for d in docs) and by_type["ClinicalSummary"][0].source_record_id is None
    assert by_type["ClinicalSummary"][0].record_date.isoformat() == "2024-02-01"
    assert "Possible angina" in docs[0].text                      # the original document text, kept with its metadata


def test_indexing_is_idempotent_and_does_not_re_embed_unchanged_entries():
    store = default_world()
    index, _, embedder, vstore = build(store)
    index.execute(PID)
    before = {k: v.model_dump() for k, v in vstore.entries.items()}
    batches = embedder.batches
    r = index.execute(PID)
    assert (r.indexed, r.unchanged, r.removed) == (0, 3, 0)
    assert embedder.batches == batches and {k: v.model_dump() for k, v in vstore.entries.items()} == before


def test_new_record_embeds_only_the_new_document_and_removes_the_now_stale_summary():
    store = default_world()
    index, _, embedder, vstore = build(store)
    index.execute(PID)
    new = make_record({"mrn": "000101", "note": "Brand new visit, feeling well.", "date": "2024-07-01"}, PID, 5)
    store.records[new.record_id] = new
    generate_fhir(store, PID)
    embedder.texts.clear()
    r = index.execute(PID)
    assert (r.indexed, r.unchanged, r.removed) == (1, 2, 1) and "summary_stale" in r.skipped
    assert embedder.texts == ["Brand new visit, feeling well."]
    assert types(vstore) == ["DocumentReference"] * 3               # the stale AI summary is gone, not "current"


def test_resources_removed_by_fhir_regeneration_are_removed_from_the_index():
    store = default_world(summary=False)
    index, _, _, vstore = build(store)
    index.execute(PID)
    gone = next(r for r in store.records.values() if r.source_id.endswith("#1"))
    del store.records[gone.record_id]
    generate_fhir(store, PID)
    r = index.execute(PID)
    assert (r.indexed, r.unchanged, r.removed) == (0, 1, 1) and len(vstore.entries) == 1


@pytest.mark.parametrize("status", [FhirStatus.INVALID, FhirStatus.FAILED])
def test_invalid_or_failed_fhir_state_removes_documents_and_indexes_none(status):
    store = default_world(summary=False)
    index, _, _, vstore = build(store)
    index.execute(PID)
    store.fhir_state[PID] = store.fhir_state[PID].model_copy(update={"status": status})
    r = index.execute(PID)
    assert r.removed == 2 and "fhir_not_generated" in r.skipped and vstore.entries == {}


def test_patient_without_generated_fhir_is_skipped_not_failed():
    store = default_world(summary=False)
    del store.fhir_state[PID]
    index, _, _, vstore = build(store)
    r = index.execute(PID)
    assert r.indexed == 0 and "fhir_not_generated" in r.skipped and vstore.entries == {}


def test_missing_stale_or_failed_summaries_are_never_indexed():
    store = default_world(summary=False)
    index, _, _, vstore = build(store)
    assert "summary_not_available" in index.execute(PID).skipped and types(vstore) == ["DocumentReference"] * 2
    from tests.search_helpers import generate_summary
    generate_summary(store)
    assert index.execute(PID).indexed == 1 and "ClinicalSummary" in types(vstore)
    for row in store.summaries.values():                              # a failed summary is not "available"
        row.status = type(row.status)("failed")
    r = index.execute(PID)
    assert r.removed == 1 and "summary_not_available" in r.skipped and "ClinicalSummary" not in types(vstore)


def test_documents_without_decodable_text_are_skipped():
    store = default_world(summary=False)
    res = next(r for r in store.fhir_resources.values() if r.resource_type == "DocumentReference")
    res.content["content"][0]["attachment"]["data"] = "%%%not-base64"
    index, _, _, _ = build(store)
    r = index.execute(PID)
    assert r.indexed == 1 and r.skipped == ["document_without_text", "summary_not_available"]


def test_changing_the_embedding_model_re_embeds_everything():
    store = default_world()
    index, _, _, vstore = build(store)
    index.execute(PID)
    index2, _, _, _ = build(store, embedder=RecordingEmbedder(dimension=64), vstore=vstore)
    r = index2.execute(PID)
    assert (r.indexed, r.unchanged) == (3, 0)
    assert {e.model_identifier for e in vstore.entries.values()} == {"hashing-ngram-v1-64"} and len(vstore.entries) == 3


def test_only_clinical_text_reaches_the_embedder_never_identifiers():
    store = default_world()
    index, _, embedder, _ = build(store)
    index.execute(PID)
    blob = "\n".join(embedder.texts)
    assert "chest tightness" in blob.lower()
    for identifier in ("Alex Test", "Alex", "000101", "1980", "1980-03-14", PID):
        assert identifier not in blob


def test_identical_texts_are_embedded_once_per_batch():
    store = default_world(summary=False)
    for r in list(store.records.values()):
        del store.records[r.record_id]
    for i, day in enumerate(("2024-01-01", "2024-02-02")):
        rec = make_record({"mrn": "000101", "note": "Same words in both notes.", "date": day}, PID, i)
        store.records[rec.record_id] = rec
    generate_fhir(store, PID)
    index, _, embedder, vstore = build(store)
    assert index.execute(PID).indexed == 2 and embedder.texts == ["Same words in both notes."] and len(vstore.entries) == 2


def test_embedding_failure_is_audited_nothing_is_indexed_and_retry_succeeds():
    store = default_world()
    embedder = RecordingEmbedder(fail=True)
    index, _, _, vstore = build(store, embedder=embedder)
    with pytest.raises(EmbeddingUnavailableError):
        index.execute(PID)
    assert vstore.entries == {} and audit(store, A.INDEX_FAILED)[0].details == {"failure_category": "EmbeddingUnavailableError"}
    embedder.fail = False
    assert index.execute(PID).indexed == 3 and len(audit(store, A.INDEX_COMPLETED)) == 1


def test_vector_store_failure_is_audited_and_retry_is_safe():
    store = default_world()
    vstore = FakeVectorStore()
    vstore.fail_on["upsert"] = 1
    index, _, _, _ = build(store, vstore=vstore)
    with pytest.raises(VectorStoreError):
        index.execute(PID)
    assert audit(store, A.INDEX_FAILED) and vstore.entries == {}
    assert index.execute(PID).indexed == 3 and index.execute(PID).unchanged == 3


def test_a_failing_delete_never_leaves_the_patient_with_fewer_entries_than_before():
    store = default_world()
    index, _, _, vstore = build(store)
    index.execute(PID)
    new = make_record({"mrn": "000101", "note": "Another visit.", "date": "2024-07-01"}, PID, 5)
    store.records[new.record_id] = new
    generate_fhir(store, PID)
    vstore.fail_on["delete"] = 1                       # upsert of the new doc succeeds, deleting the stale summary fails
    with pytest.raises(VectorStoreError):
        index.execute(PID)
    assert len(vstore.entries) == 4                    # new document added; nothing lost
    assert index.execute(PID).removed == 1             # retry completes the cleanup


def test_unknown_patient_raises_and_does_not_touch_the_store():
    index, _, _, vstore = build(default_world())
    with pytest.raises(PatientNotFoundError):
        index.execute("missing")
    assert vstore.calls == []


def test_indexing_one_patient_never_touches_another_patients_entries():
    store = default_world(summary=False)
    add_patient(store, OTHER, "000222", "Sam Other", [{"note": "Sprained ankle after football.", "date": "2024-03-03"}])
    generate_fhir(store, OTHER)
    index, _, _, vstore = build(store)
    index.execute(PID)
    index.execute(OTHER)
    other_ids = {k for k, e in vstore.entries.items() if e.patient_id == OTHER}
    assert len(other_ids) == 1
    store.fhir_state[PID] = store.fhir_state[PID].model_copy(update={"status": FhirStatus.FAILED})
    index.execute(PID)                                            # removes all of PID's entries...
    assert {k for k, e in vstore.entries.items() if e.patient_id == OTHER} == other_ids   # ...and only PID's


def test_execute_all_indexes_every_patient_idempotently():
    store = default_world(summary=False)
    add_patient(store, OTHER, "000222", "Sam Other", [{"note": "Sprained ankle.", "date": "2024-03-03"}])
    generate_fhir(store, OTHER)
    index, _, _, _ = build(store)
    first = index.execute_all()
    assert {r.patient_id for r in first} == {PID, OTHER} and sum(r.indexed for r in first) == 3
    assert sum(r.indexed for r in index.execute_all()) == 0


def test_audit_trail_for_indexing_holds_counts_not_clinical_content():
    store = default_world()
    index, _, _, _ = build(store)
    index.execute(PID)
    blob = json.dumps([e.details for e in store.audit if e.event_type.value.startswith("index")])
    for sensitive in ("chest", "hypertension", "angina", "Alex", R1_RAW["note"][:20]):
        assert sensitive.lower() not in blob.lower()
    assert audit(store, A.INDEX_COMPLETED)[0].details["indexed"] == 3
