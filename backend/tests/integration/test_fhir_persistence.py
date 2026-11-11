"""FHIR persistence against real SQLite (temp file per test)."""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.application.dto import IngestCommand
from app.application.ingestion.factory import IngestionStrategyFactory
from app.application.use_cases.generate_fhir_bundle import GenerateFHIRBundleUseCase
from app.application.use_cases.get_patient_fhir import GetPatientFHIRBundleUseCase
from app.application.use_cases.ingest_patient_records import IngestPatientRecordsUseCase
from app.application.use_cases.resolve_patient_identity import ResolvePatientIdentityUseCase
from app.domain.canonical import SourceFormat
from app.domain.fhir import (
    FhirIssue,
    FhirResourceRecord,
    FhirStatus,
    PatientFhirState,
    check_bundle,
)
from app.infrastructure.fhir.r4b_builder import R4BBundleBuilder
from app.infrastructure.fhir.r4b_validator import R4BSchemaValidator
from app.infrastructure.persistence.session import (
    create_db_engine,
    get_session_factory,
    init_schema,
)
from app.infrastructure.persistence.tables import FhirResourceRow
from app.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.fhir_helpers import NOW, make_patient

RECORDS = [
    {"mrn": "MRN-000101", "name": "Alex Test", "dob": "1980-03-14", "sex": "M",
     "note": "Follow-up for chest tightness.", "date": "2024-02-01", "diagnosis": "Asthma"},
    {"mrn": "000101", "name": "Alex Test", "dob": "1980-03-14", "note": "Later visit.", "date": "2024-03-01"},
]


@pytest.fixture
def factory(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'f.db'}")
    init_schema(engine)
    return get_session_factory(engine)


@pytest.fixture
def uow(factory):
    return lambda: SqlAlchemyUnitOfWork(factory)


def ingest(uow):
    uc = IngestPatientRecordsUseCase(uow, IngestionStrategyFactory(), ResolvePatientIdentityUseCase())
    return uc.execute(IngestCommand(json.dumps(RECORDS).encode(), "r.json", SourceFormat.JSON)).results


def res(rid, rtype="Patient", pid="p1", pos=0, content=None):
    return FhirResourceRecord(resource_type=rtype, resource_id=rid, patient_id=pid, position=pos,
                              content=content or {"resourceType": rtype, "id": rid})


def seed_patient(uow, pid="p1", mrn="000101"):
    with uow() as u:
        u.patients.add(make_patient(patient_id=pid, mrn=mrn))
        u.commit()


def test_state_roundtrip_and_upsert(uow):
    seed_patient(uow)
    state = PatientFhirState(patient_id="p1", status=FhirStatus.INVALID, fingerprint="f" * 64, updated_at=NOW,
                             issues=[FhirIssue(code="c", path="a.b", message="m")])
    with uow() as u:
        u.fhir.save_state(state)
        u.commit()
    with uow() as u:
        got = u.fhir.get_state("p1")
        assert got.status == FhirStatus.INVALID and got.issues == state.issues and got.updated_at == NOW
        u.fhir.save_state(state.model_copy(update={"status": FhirStatus.GENERATED, "issues": [], "bundle_id": "b",
                                                   "bundle_timestamp": NOW, "resource_count": 2}))
        u.commit()
    with uow() as u:
        got = u.fhir.get_state("p1")
        assert got.status == FhirStatus.GENERATED and got.issues == [] and got.bundle_timestamp == NOW
        assert u.fhir.get_state("missing") is None


def test_replace_resources_replaces_all_and_orders_by_position(uow):
    seed_patient(uow)
    with uow() as u:
        u.fhir.replace_resources("p1", [res("a", "DiagnosticReport", pos=2), res("b", pos=0), res("c", "DocumentReference", pos=1)])
        u.commit()
    with uow() as u:
        assert [r.resource_id for r in u.fhir.list_resources("p1")] == ["b", "c", "a"]
        assert [r.resource_id for r in u.fhir.list_resources("p1", "DocumentReference")] == ["c"]
        assert u.fhir.get_resource("Patient", "b").content == {"resourceType": "Patient", "id": "b"}
        u.fhir.replace_resources("p1", [res("b", pos=0)])
        u.commit()
    with uow() as u:
        assert [r.resource_id for r in u.fhir.list_resources("p1")] == ["b"]      # old rows gone, no duplicates
        assert u.fhir.get_resource("DiagnosticReport", "a") is None


def test_resources_are_scoped_to_their_patient(uow):
    seed_patient(uow, "p1", "000101")
    seed_patient(uow, "p2", "000102")
    with uow() as u:
        u.fhir.replace_resources("p1", [res("a", pid="p1")])
        u.fhir.replace_resources("p2", [res("b", pid="p2")])
        u.fhir.replace_resources("p1", [])
        u.commit()
    with uow() as u:
        assert u.fhir.list_resources("p1") == [] and [r.resource_id for r in u.fhir.list_resources("p2")] == ["b"]


def test_foreign_keys_enforced_for_fhir_tables(uow):
    with uow() as u, pytest.raises(IntegrityError):
        u.fhir.replace_resources("ghost", [res("a", pid="ghost")])


def test_primary_key_prevents_duplicate_resource_rows(uow):
    seed_patient(uow)
    with uow() as u, pytest.raises(IntegrityError):
        u.fhir.replace_resources("p1", [res("a"), res("a")])


def test_rollback_keeps_previous_resources(uow):
    seed_patient(uow)
    with uow() as u:
        u.fhir.replace_resources("p1", [res("old")])
        u.commit()
    with uow() as u:
        u.fhir.replace_resources("p1", [res("new")])   # no commit
    with uow() as u:
        assert [r.resource_id for r in u.fhir.list_resources("p1")] == ["old"]


def test_end_to_end_generation_persists_and_reloads_valid_bundle(uow, factory):
    pid = ingest(uow)[0].patient_id
    gen = GenerateFHIRBundleUseCase(uow, R4BBundleBuilder(), R4BSchemaValidator())
    first = gen.execute(pid)
    assert first.status == FhirStatus.GENERATED
    assert first.resource_counts == {"Patient": 1, "DocumentReference": 2, "DiagnosticReport": 1}

    second = gen.execute(pid)                                    # idempotent against real SQLite
    assert second.unchanged and second.bundle_id == first.bundle_id
    with factory() as s:
        assert s.query(FhirResourceRow).count() == 4

    bundle = GetPatientFHIRBundleUseCase(uow, R4BBundleBuilder()).execute(pid)
    assert check_bundle(bundle) == [] and R4BSchemaValidator().validate_bundle(bundle) == []
    assert bundle["timestamp"].startswith(first.updated_at.strftime("%Y-%m-%dT%H:%M"))
    assert bundle["id"] == first.bundle_id


def test_ingestion_status_untouched_by_fhir_generation(uow):
    results = ingest(uow)
    pid = results[0].patient_id
    GenerateFHIRBundleUseCase(uow, R4BBundleBuilder(), R4BSchemaValidator()).execute(pid)
    with uow() as u:
        assert {r.processing.status.value for r in u.source_records.list_completed_for_patient(pid)} == {"completed"}
        assert u.source_records.list_completed_for_patient("nobody") == []


def test_timestamps_roundtrip_as_utc(uow):
    seed_patient(uow)
    t = datetime(2025, 5, 5, 5, 5, 5, tzinfo=UTC)
    with uow() as u:
        u.fhir.save_state(PatientFhirState(patient_id="p1", status=FhirStatus.FAILED, fingerprint="", updated_at=t,
                                           failure_category="X"))
        u.commit()
    with uow() as u:
        assert u.fhir.get_state("p1").updated_at == t
