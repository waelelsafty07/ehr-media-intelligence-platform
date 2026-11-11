import itertools
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.application.dto import IngestCommand
from app.application.ingestion.factory import IngestionStrategyFactory
from app.application.use_cases.ingest_patient_records import IngestPatientRecordsUseCase
from app.application.use_cases.resolve_patient_identity import ResolvePatientIdentityUseCase
from app.domain.canonical import IssueCode, SourceFormat
from app.domain.canonical import ProcessingStatus as S
from app.domain.entities import AuditEventType as A
from app.domain.errors import IngestionParseError
from tests.fakes import FakeStore, FakeUnitOfWork


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def ingest(store):
    ticks = itertools.count()
    ids = itertools.count(1)
    uc = IngestPatientRecordsUseCase(
        uow_factory=lambda: FakeUnitOfWork(store), strategies=IngestionStrategyFactory(),
        resolve_identity=ResolvePatientIdentityUseCase(),
        clock=lambda: datetime(2025, 1, 1, tzinfo=UTC) + timedelta(seconds=next(ticks)),
        new_id=lambda: f"patient-{next(ids)}")

    def run(records, name="f.json", fmt=SourceFormat.JSON):
        content = records if isinstance(records, bytes) else json.dumps(records).encode()
        return uc.execute(IngestCommand(content, name, fmt))
    return run


ALEX = {"mrn": "MRN-000101", "name": "Alex Test", "dob": "1980-03-14", "sex": "M",
        "note": "Follow-up for chest tightness.", "date": "2024-02-01"}


def events(store, record_id=None):
    return [e.event_type for e in store.audit if record_id is None or e.record_id == record_id]


def test_clean_record_completes_and_creates_patient(ingest, store):
    r = ingest([ALEX]).results[0]
    assert r.status == S.COMPLETED and r.patient_id == "patient-1" and r.identity_rationale_code == "new_mrn_no_conflicting_match"
    assert store.patients["patient-1"].mrn == "000101"
    assert events(store) == [A.RECORD_RECEIVED, A.RECORD_NORMALIZED, A.IDENTITY_DECISION, A.PATIENT_CREATED,
                             A.RECORD_COMPLETED]


def test_same_patient_second_document_links_to_existing_patient(ingest, store):
    ingest([ALEX])
    r = ingest([{**ALEX, "note": "A later visit.", "date": "2024-03-01"}], name="g.json").results[0]
    assert r.status == S.COMPLETED and r.patient_id == "patient-1" and len(store.patients) == 1


def test_missing_dob_completes_with_warning(ingest):
    r = ingest([{"mrn": "000102", "name": "Sam Case"}]).results[0]
    assert r.status == S.COMPLETED
    assert any(i.code == IssueCode.MISSING_OPTIONAL and i.field == "date_of_birth" for i in r.issues)


def test_invalid_and_missing_required_are_rejected_without_patient(ingest, store):
    res = ingest([{"mrn": "000103", "dob": "31/02/1990"}, {"name": "No Mrn"}]).results
    assert [r.status for r in res] == [S.REJECTED, S.REJECTED] and store.patients == {}
    assert A.VALIDATION_FAILED in events(store)


def test_batch_continues_after_a_bad_record(ingest):
    res = ingest([{"mrn": "bad#", "dob": "1980-01-01"}, ALEX]).results
    assert [r.status for r in res] == [S.REJECTED, S.COMPLETED]


def test_duplicate_detected_by_normalized_content(ingest, store):
    first = ingest([ALEX]).results[0]
    dup = {"mrn": "000101", "name": "Alex Test", "dob": "03/14/1980", "sex": "male",
           "note": "Follow-up  for chest tightness.", "date": "2024-02-01"}
    r = ingest([dup], name="g.json").results[0]
    assert r.status == S.DUPLICATE and r.duplicate_of_record_id == first.record_id and r.patient_id is None
    assert any(i.code == IssueCode.DUPLICATE_RECORD for i in r.issues) and len(store.patients) == 1
    assert A.DUPLICATE_DETECTED in events(store, r.record_id)


def test_conflicting_identifier_goes_to_review_and_is_not_linked(ingest, store):
    ingest([ALEX])
    r = ingest([{**ALEX, "dob": "1991-07-09", "note": "Different person?"}], name="g.json").results[0]
    assert r.status == S.NEEDS_REVIEW and r.patient_id is None
    assert r.identity_rationale_code == "mrn_dob_mismatch"
    assert any(i.code == IssueCode.IDENTITY_CONFLICT for i in r.issues)
    assert len(store.patients) == 1
    assert [A.IDENTITY_DECISION, A.REVIEW_REQUIRED] == [e for e in events(store, r.record_id)][-2:]


def test_ambiguous_same_name_and_dob_new_mrn_not_merged(ingest, store):
    ingest([ALEX])
    r = ingest([{**ALEX, "mrn": "000555", "note": "Another note."}], name="g.json").results[0]
    assert r.status == S.NEEDS_REVIEW and r.identity_rationale_code == "same_name_dob_different_mrn"
    assert len(store.patients) == 1


def test_same_name_different_dob_is_a_separate_patient(ingest, store):
    ingest([ALEX])
    r = ingest([{**ALEX, "mrn": "000103", "dob": "1991-07-09", "note": "Other."}], name="g.json").results[0]
    assert r.status == S.COMPLETED and r.patient_id == "patient-2" and len(store.patients) == 2


def test_ambiguous_same_mrn_different_name(ingest):
    ingest([ALEX])
    r = ingest([{**ALEX, "name": "Totally Different", "note": "x"}], name="g.json").results[0]
    assert r.status == S.NEEDS_REVIEW and r.identity_rationale_code == "mrn_match_name_differs"


def test_reprocessing_identical_input_is_idempotent_and_audited(ingest, store):
    first = ingest([ALEX]).results[0]
    before = (len(store.patients), len(store.records))
    again = ingest([ALEX]).results[0]
    assert again.already_ingested and again.record_id == first.record_id and again.status == S.COMPLETED
    assert (len(store.patients), len(store.records)) == before
    assert events(store, first.record_id).count(A.REPROCESS_SKIPPED) == 1
    assert events(store, first.record_id)[:5] == [A.RECORD_RECEIVED, A.RECORD_NORMALIZED, A.IDENTITY_DECISION,
                                                  A.PATIENT_CREATED, A.RECORD_COMPLETED]  # history preserved


def test_reprocessing_rejected_record_does_not_duplicate(ingest, store):
    ingest([{"mrn": "000103", "dob": "31/02/1990"}])
    r = ingest([{"mrn": "000103", "dob": "31/02/1990"}]).results[0]
    assert r.already_ingested and len(store.records) == 1


def test_failure_rolls_back_and_marks_failed_then_retry_recovers(ingest, store):
    store.fail_on["source_records.update"] = 1  # fails after patient was added in the same transaction
    r = ingest([ALEX]).results[0]
    assert r.status == S.FAILED and store.patients == {}  # nothing partial
    stored = store.records[r.record_id]
    assert stored.processing.status == S.FAILED and stored.processing.failure_category == "RuntimeError"
    assert A.PROCESSING_FAILED in events(store, r.record_id)

    retry = ingest([ALEX]).results[0]
    assert retry.status == S.COMPLETED and retry.attempts == 2 and not retry.already_ingested
    assert len(store.patients) == 1 and len(store.records) == 1
    assert A.RECORD_RETRY_STARTED in events(store, r.record_id)


def test_failure_state_not_persisted_leaves_record_retryable(ingest, store):
    store.fail_on["source_records.update"] = 2  # processing fails AND failure marking fails
    r = ingest([ALEX]).results[0]
    assert r.status == S.FAILED
    assert store.records[r.record_id].processing.status == S.RECEIVED  # still retryable, never "completed"
    assert ingest([ALEX]).results[0].status == S.COMPLETED


def test_unparseable_file_raises_and_is_audited(ingest, store):
    with pytest.raises(IngestionParseError):
        ingest(b"{broken", name="bad.json")
    assert events(store) == [A.INGEST_REJECTED] and store.records == {}


def test_csv_ragged_row_is_rejected_per_record(ingest):
    res = ingest(b"mrn,name,dob\n000201,Jamie Fake\n000202,Riley Mock,1990-01-01\n", name="r.csv",
                 fmt=SourceFormat.CSV).results
    assert [r.status for r in res] == [S.REJECTED, S.COMPLETED]


def test_text_ingestion_end_to_end(ingest, store):
    r = ingest(b"Patient: Casey Test\nMRN: 000301\nDOB: 12 Jan 1968\nSex: Female\nCough for three weeks.\n",
               name="n.txt", fmt=SourceFormat.TEXT).results[0]
    assert r.status == S.COMPLETED
    assert store.patients[r.patient_id].date_of_birth.isoformat() == "1968-01-12"


def test_audit_details_contain_no_patient_content(ingest, store):
    ingest([ALEX, {"mrn": "000103", "name": "Zed Zebra", "dob": "31/02/1990", "note": "secret note"}])
    ingest([{**ALEX, "dob": "1991-07-09", "note": "conflict text"}], name="g.json")
    blob = json.dumps([e.details for e in store.audit])
    for sensitive in ("Alex", "Test", "Zebra", "secret", "chest", "1980", "1990", "000101"):
        assert sensitive not in blob
