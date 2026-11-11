from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.application.dto import IngestCommand
from app.application.ingestion.factory import IngestionStrategyFactory
from app.application.use_cases.ingest_patient_records import IngestPatientRecordsUseCase
from app.application.use_cases.resolve_patient_identity import ResolvePatientIdentityUseCase
from app.domain.canonical import ProcessingStatus, SourceFormat
from app.domain.entities import AuditEvent, AuditEventType, Patient
from app.domain.normalization import name_key
from app.infrastructure.persistence.session import (
    create_db_engine,
    get_session_factory,
    init_schema,
)
from app.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork

NOW = datetime(2025, 1, 1, tzinfo=UTC)


@pytest.fixture
def factory(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_schema(engine)
    return get_session_factory(engine)


def _patient(pid="p1", mrn="000101"):
    return Patient(patient_id=pid, mrn=mrn, full_name="Alex Test", name_key=name_key("Alex Test"),
                   date_of_birth=date(1980, 3, 14), created_at=NOW)


def test_patient_roundtrip_and_lookups(factory):
    with SqlAlchemyUnitOfWork(factory) as uow:
        uow.patients.add(_patient())
        uow.commit()
    with SqlAlchemyUnitOfWork(factory) as uow:
        assert uow.patients.get_by_mrn("000101").patient_id == "p1"
        assert uow.patients.find_by_name_key_and_dob(name_key("test alex"), date(1980, 3, 14))[0].patient_id == "p1"
        assert uow.patients.find_by_name_key_and_dob(name_key("Alex Test"), date(1991, 1, 1)) == []


def test_unique_mrn_constraint_enforced(factory):
    with SqlAlchemyUnitOfWork(factory) as uow:
        uow.patients.add(_patient())
        with pytest.raises(IntegrityError):
            uow.patients.add(_patient(pid="p2"))


def test_uncommitted_work_is_rolled_back(factory):
    with SqlAlchemyUnitOfWork(factory) as uow:
        uow.patients.add(_patient())
    with SqlAlchemyUnitOfWork(factory) as uow:
        assert uow.patients.list_all() == []


def test_foreign_keys_are_enforced(factory):
    with SqlAlchemyUnitOfWork(factory) as uow, pytest.raises(IntegrityError):
        uow.audit.add(AuditEvent(occurred_at=NOW, event_type=AuditEventType.RECORD_COMPLETED,
                                 record_id="no-such-record"))


def test_full_ingest_persists_and_reprocess_is_idempotent(factory):
    uc = IngestPatientRecordsUseCase(lambda: SqlAlchemyUnitOfWork(factory), IngestionStrategyFactory(),
                                     ResolvePatientIdentityUseCase())
    cmd = IngestCommand(b'[{"mrn":"MRN-000101","name":"Alex Test","dob":"03/14/1980","note":"Cough."}]',
                        "a.json", SourceFormat.JSON)
    first = uc.execute(cmd).results[0]
    second = uc.execute(cmd).results[0]
    assert first.status == ProcessingStatus.COMPLETED and second.already_ingested
    with SqlAlchemyUnitOfWork(factory) as uow:
        assert len(uow.patients.list_all()) == 1
        rec = uow.source_records.get(first.record_id)
        assert rec.canonical.patient.mrn == "000101" and rec.processing.attempts == 1
        types = [e.event_type for e in uow.audit.list_events(first.record_id)]
        assert types[0] == AuditEventType.RECORD_RECEIVED and types[-1] == AuditEventType.REPROCESS_SKIPPED
