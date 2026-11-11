import itertools
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.application.dto import IngestCommand
from app.application.ingestion.factory import IngestionStrategyFactory
from app.application.use_cases.generate_fhir_bundle import GenerateFHIRBundleUseCase
from app.application.use_cases.get_patient_fhir import (
    GetPatientFHIRBundleUseCase,
    GetPatientFHIRStatusUseCase,
)
from app.application.use_cases.ingest_patient_records import IngestPatientRecordsUseCase
from app.application.use_cases.resolve_patient_identity import ResolvePatientIdentityUseCase
from app.domain.canonical import ProcessingStatus, SourceFormat
from app.domain.entities import AuditEventType as A
from app.domain.errors import FhirBuildError, FhirNotAvailableError, PatientNotFoundError
from app.domain.fhir import FhirIssue, FhirStatus, check_bundle
from app.infrastructure.fhir.r4b_builder import R4BBundleBuilder
from app.infrastructure.fhir.r4b_validator import R4BSchemaValidator
from tests.fakes import FakeStore, FakeUnitOfWork

ALEX = {"mrn": "MRN-000101", "name": "Alex Test", "dob": "1980-03-14", "sex": "M",
        "note": "Follow-up for chest tightness.", "date": "2024-02-01", "diagnosis": "Asthma"}


class StubBuilder(R4BBundleBuilder):
    """Real mapper with hooks to produce broken output or crash."""

    def __init__(self, mutate=None, build_error=None, boom=False):
        self.mutate, self.build_error, self.boom = mutate, build_error, boom

    def build_resources(self, patient, records, generated_at):
        if self.boom:
            raise RuntimeError("mapper crashed: secret-detail")
        if self.build_error:
            raise FhirBuildError(self.build_error)
        res = super().build_resources(patient, records, generated_at)
        if self.mutate:
            self.mutate(res)
        return res


class StubValidator:
    def __init__(self, issues=()):
        self.issues = list(issues)

    def validate_bundle(self, bundle):
        return self.issues


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def clock():
    ticks = itertools.count()
    return lambda: datetime(2025, 1, 1, tzinfo=UTC) + timedelta(seconds=next(ticks))


@pytest.fixture
def ingest(store, clock):
    ids = itertools.count(1)
    uc = IngestPatientRecordsUseCase(lambda: FakeUnitOfWork(store), IngestionStrategyFactory(),
                                     ResolvePatientIdentityUseCase(), clock,
                                     new_id=lambda: f"00000000-0000-4000-8000-{next(ids):012d}")
    return lambda records, name="f.json": uc.execute(
        IngestCommand(json.dumps(records).encode(), name, SourceFormat.JSON))


@pytest.fixture
def generate(store, clock):
    def make(builder=None, validator=None):
        return GenerateFHIRBundleUseCase(lambda: FakeUnitOfWork(store), builder or R4BBundleBuilder(),
                                         validator or R4BSchemaValidator(), clock)
    return make


def types(store, patient_id):
    return [r.resource_type for r in store.fhir_resources.values() if r.patient_id == patient_id]


def test_unknown_patient_raises_not_found(generate):
    with pytest.raises(PatientNotFoundError):
        generate().execute("nope")


def test_generates_bundle_persists_resources_and_state(ingest, generate, store):
    pid = ingest([ALEX]).results[0].patient_id
    r = generate().execute(pid)
    assert r.status == FhirStatus.GENERATED and not r.unchanged and r.issues == []
    assert r.resource_counts == {"Patient": 1, "DocumentReference": 1, "DiagnosticReport": 1}
    assert sorted(types(store, pid)) == ["DiagnosticReport", "DocumentReference", "Patient"]
    state = store.fhir_state[pid]
    assert state.status == FhirStatus.GENERATED and state.resource_count == 3 and state.bundle_id == r.bundle_id
    assert [e.event_type for e in store.audit if e.patient_id == pid][-1] == A.FHIR_GENERATED


def test_generation_is_idempotent(ingest, generate, store):
    pid = ingest([ALEX]).results[0].patient_id
    first = generate().execute(pid)
    snapshot = {k: v.model_dump() for k, v in store.fhir_resources.items()}
    state_before = store.fhir_state[pid].model_dump()
    second = generate().execute(pid)
    assert second.unchanged and second.status == FhirStatus.GENERATED and second.bundle_id == first.bundle_id
    assert {k: v.model_dump() for k, v in store.fhir_resources.items()} == snapshot      # nothing rewritten
    assert store.fhir_state[pid].model_dump() == state_before
    assert [e.event_type for e in store.audit if e.patient_id == pid][-1] == A.FHIR_UNCHANGED


def test_new_record_regenerates_without_duplicate_resources(ingest, generate, store):
    pid = ingest([ALEX]).results[0].patient_id
    generate().execute(pid)
    later = {**ALEX, "note": "A later visit.", "date": "2024-03-01", "diagnosis": ""}
    assert ingest([later], name="g.json").results[0].patient_id == pid
    r = generate().execute(pid)
    assert not r.unchanged and r.resource_counts == {"Patient": 1, "DocumentReference": 2, "DiagnosticReport": 1}
    keys = list(store.fhir_resources)
    assert len(keys) == len(set(keys)) == 4
    assert generate().execute(pid).unchanged


def test_only_completed_records_contribute(ingest, generate, store):
    first = ingest([ALEX]).results[0]
    pid = first.patient_id
    dup = {**ALEX, "mrn": "000101", "dob": "03/14/1980", "note": "Follow-up  for chest tightness."}
    conflict = {**ALEX, "dob": "1991-07-09", "note": "Different person?"}
    rejected = {"mrn": "000101", "dob": "31/02/1990", "note": "bad date"}
    statuses = [r.status for r in ingest([dup, conflict, rejected], name="g.json").results]
    assert statuses == [ProcessingStatus.DUPLICATE, ProcessingStatus.NEEDS_REVIEW, ProcessingStatus.REJECTED]
    r = generate().execute(pid)
    assert r.resource_counts == {"Patient": 1, "DocumentReference": 1, "DiagnosticReport": 1}


def test_each_patient_gets_only_their_own_resources(ingest, generate, store):
    res = ingest([ALEX, {**ALEX, "mrn": "000222", "name": "Sam Other", "dob": "1990-01-01", "note": "Other."}]).results
    p1, p2 = res[0].patient_id, res[1].patient_id
    generate().execute(p1), generate().execute(p2)
    assert {r.patient_id for r in store.fhir_resources.values() if r.resource_id in (p1,)} == {p1}
    for pid in (p1, p2):
        bundle = GetPatientFHIRBundleUseCase(lambda: FakeUnitOfWork(store), R4BBundleBuilder()).execute(pid)
        subjects = {e["resource"]["subject"]["reference"] for e in bundle["entry"] if "subject" in e["resource"]}
        assert subjects == {f"urn:uuid:{pid}"} and check_bundle(bundle) == []


def test_fhir_status_is_separate_from_ingestion_status(ingest, generate, store):
    rec = ingest([ALEX]).results[0]
    store.fail_on["fhir.replace_resources"] = 1
    assert generate().execute(rec.patient_id).status == FhirStatus.FAILED
    assert store.records[rec.record_id].processing.status == ProcessingStatus.COMPLETED   # ingestion untouched
    assert store.fhir_state[rec.patient_id].status == FhirStatus.FAILED


def test_failure_rolls_back_marks_failed_and_retry_recovers(ingest, generate, store):
    pid = ingest([ALEX]).results[0].patient_id
    store.fail_on["fhir.replace_resources"] = 1
    r = generate().execute(pid)
    assert r.status == FhirStatus.FAILED and store.fhir_resources == {}        # nothing partial
    assert store.fhir_state[pid].failure_category == "RuntimeError"
    assert A.FHIR_GENERATION_FAILED in [e.event_type for e in store.audit]
    retry = generate().execute(pid)
    assert retry.status == FhirStatus.GENERATED and len(store.fhir_resources) == 3


def test_unexpected_mapper_crash_is_recorded_without_leaking_details(ingest, generate, store):
    pid = ingest([ALEX]).results[0].patient_id
    r = generate(builder=StubBuilder(boom=True)).execute(pid)
    assert r.status == FhirStatus.FAILED
    assert "secret-detail" not in json.dumps([e.details for e in store.audit]) + str(store.fhir_state[pid])


def test_failure_state_not_persisted_still_returns_failed(ingest, generate, store):
    pid = ingest([ALEX]).results[0].patient_id
    store.fail_on["fhir.replace_resources"] = 1
    store.fail_on["fhir.save_state"] = 1       # first save is skipped by the crash; failure marking also fails
    assert generate().execute(pid).status == FhirStatus.FAILED


def test_dangling_reference_makes_bundle_invalid_and_nothing_is_served(ingest, generate, store):
    pid = ingest([ALEX]).results[0].patient_id

    def break_reference(resources):
        next(r for r in resources if r.resource_type == "DiagnosticReport").content["subject"]["reference"] = "urn:uuid:ghost"

    r = generate(builder=StubBuilder(mutate=break_reference)).execute(pid)
    assert r.status == FhirStatus.INVALID and {"dangling_reference", "subject_not_bundle_patient"} <= {i.code for i in r.issues}
    assert store.fhir_resources == {} and store.fhir_state[pid].status == FhirStatus.INVALID
    assert A.FHIR_VALIDATION_FAILED in [e.event_type for e in store.audit]
    with pytest.raises(FhirNotAvailableError) as e:
        GetPatientFHIRBundleUseCase(lambda: FakeUnitOfWork(store), R4BBundleBuilder()).execute(pid)
    assert e.value.status == "invalid"


def test_invalid_value_set_code_is_rejected(ingest, generate, store):
    pid = ingest([ALEX]).results[0].patient_id

    def bad_gender(resources):
        resources[0].content["gender"] = "banana"          # accepted by the library, rejected by our rules

    r = generate(builder=StubBuilder(mutate=bad_gender)).execute(pid)
    assert r.status == FhirStatus.INVALID and [i.code for i in r.issues] == ["invalid_code_value"]


def test_library_validation_failures_are_reported(ingest, generate):
    pid = ingest([ALEX]).results[0].patient_id
    v = StubValidator([FhirIssue(code="schema:extra_forbidden", path="entry.0.resource.foo", message="x")])
    r = generate(validator=v).execute(pid)
    assert r.status == FhirStatus.INVALID and r.issues[0].path == "entry.0.resource.foo"


def test_build_error_becomes_invalid_with_issues(ingest, generate):
    pid = ingest([ALEX]).results[0].patient_id
    issue = FhirIssue(code="schema:string_pattern_mismatch", path="mapping.id", message="x")
    r = generate(builder=StubBuilder(build_error=[issue])).execute(pid)
    assert r.status == FhirStatus.INVALID and r.issues == [issue]


def test_invalid_then_fixed_regenerates(ingest, generate, store):
    pid = ingest([ALEX]).results[0].patient_id
    assert generate(validator=StubValidator([FhirIssue(code="c", path="p", message="m")])).execute(pid).status == FhirStatus.INVALID
    r = generate().execute(pid)
    assert r.status == FhirStatus.GENERATED and not r.unchanged and len(store.fhir_resources) == 3


def test_bundle_retrieval_requires_generated_state(ingest, generate, store):
    pid = ingest([ALEX]).results[0].patient_id
    get = GetPatientFHIRBundleUseCase(lambda: FakeUnitOfWork(store), R4BBundleBuilder())
    with pytest.raises(FhirNotAvailableError) as e:
        get.execute(pid)
    assert e.value.status is None
    with pytest.raises(PatientNotFoundError):
        get.execute("nope")
    generate().execute(pid)
    b = get.execute(pid)
    assert b["resourceType"] == "Bundle" and len(b["entry"]) == 3 and b["id"] == store.fhir_state[pid].bundle_id


def test_status_use_case(ingest, generate, store):
    pid = ingest([ALEX]).results[0].patient_id
    status = GetPatientFHIRStatusUseCase(lambda: FakeUnitOfWork(store))
    assert status.execute(pid) is None
    generate().execute(pid)
    s = status.execute(pid)
    assert s.status == FhirStatus.GENERATED and s.resource_counts["Patient"] == 1
    with pytest.raises(PatientNotFoundError):
        status.execute("nope")


def test_audit_details_for_fhir_contain_no_patient_content(ingest, generate, store):
    pid = ingest([ALEX]).results[0].patient_id
    generate().execute(pid)
    generate(validator=StubValidator([FhirIssue(code="schema:x", path="a.b", message="m")])).execute(pid)
    blob = json.dumps([e.details for e in store.audit if e.event_type.value.startswith("fhir")])
    for sensitive in ("Alex", "Test", "Asthma", "chest", "1980", "000101"):
        assert sensitive not in blob
