"""SQLAlchemy implementations of the domain repository ports."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.canonical import (
    CanonicalRecord,
    Gender,
    ProcessingInfo,
    ProcessingStatus,
    SourceFormat,
)
from app.domain.entities import AuditEvent, AuditEventType, Patient, StoredSourceRecord
from app.infrastructure.persistence.tables import AuditEventRow, PatientRow, SourceRecordRow


def _utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo; every stored timestamp is UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _naive(dt: datetime) -> datetime:
    return _utc(dt).replace(tzinfo=None)


class SqlAlchemyPatientRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    @staticmethod
    def _to_domain(r: PatientRow) -> Patient:
        return Patient(patient_id=r.patient_id, mrn=r.mrn, full_name=r.full_name, name_key=r.name_key,
                       date_of_birth=r.date_of_birth, gender=Gender(r.gender), created_at=_utc(r.created_at))

    def get_by_mrn(self, mrn: str) -> Patient | None:
        row = self._s.scalar(select(PatientRow).where(PatientRow.mrn == mrn))
        return self._to_domain(row) if row else None

    def find_by_name_key_and_dob(self, name_key: str, dob: date) -> list[Patient]:
        rows = self._s.scalars(select(PatientRow).where(PatientRow.name_key == name_key,
                                                         PatientRow.date_of_birth == dob)
                               .order_by(PatientRow.created_at))
        return [self._to_domain(r) for r in rows]

    def add(self, patient: Patient) -> None:
        self._s.add(PatientRow(patient_id=patient.patient_id, mrn=patient.mrn, full_name=patient.full_name,
                               name_key=patient.name_key, date_of_birth=patient.date_of_birth,
                               gender=patient.gender.value, created_at=_naive(patient.created_at)))
        self._s.flush()  # surface constraint violations inside the transaction

    def list_all(self) -> list[Patient]:
        return [self._to_domain(r) for r in self._s.scalars(select(PatientRow).order_by(PatientRow.created_at))]


class SqlAlchemySourceRecordRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    @staticmethod
    def _to_domain(r: SourceRecordRow) -> StoredSourceRecord:
        return StoredSourceRecord(
            record_id=r.record_id, source_id=r.source_id, source_name=r.source_name,
            source_format=SourceFormat(r.source_format), record_index=r.record_index,
            source_hash=r.source_hash, content_hash=r.content_hash, raw_payload=r.raw_payload,
            canonical=CanonicalRecord.model_validate(r.canonical) if r.canonical else None,
            processing=ProcessingInfo(status=ProcessingStatus(r.status), attempts=r.attempts,
                                      failure_category=r.failure_category, updated_at=_utc(r.updated_at)),
            patient_id=r.patient_id, duplicate_of_id=r.duplicate_of_id, received_at=_utc(r.received_at))

    @staticmethod
    def _apply(row: SourceRecordRow, rec: StoredSourceRecord) -> None:
        row.content_hash = rec.content_hash
        row.canonical = rec.canonical.model_dump(mode="json") if rec.canonical else None
        row.status = rec.processing.status.value
        row.attempts = rec.processing.attempts
        row.failure_category = rec.processing.failure_category
        row.patient_id = rec.patient_id
        row.duplicate_of_id = rec.duplicate_of_id
        row.updated_at = _naive(rec.processing.updated_at)

    def get(self, record_id: str) -> StoredSourceRecord | None:
        row = self._s.get(SourceRecordRow, record_id)
        return self._to_domain(row) if row else None

    def get_by_source_hash(self, source_hash: str) -> StoredSourceRecord | None:
        row = self._s.scalar(select(SourceRecordRow).where(SourceRecordRow.source_hash == source_hash))
        return self._to_domain(row) if row else None

    def find_original_by_content_hash(self, content_hash: str, exclude_record_id: str) -> StoredSourceRecord | None:
        row = self._s.scalar(
            select(SourceRecordRow)
            .where(SourceRecordRow.content_hash == content_hash, SourceRecordRow.record_id != exclude_record_id,
                   SourceRecordRow.status.in_([ProcessingStatus.COMPLETED.value,
                                               ProcessingStatus.NEEDS_REVIEW.value]))
            .order_by(SourceRecordRow.received_at, SourceRecordRow.record_id).limit(1))
        return self._to_domain(row) if row else None

    def add(self, rec: StoredSourceRecord) -> None:
        row = SourceRecordRow(record_id=rec.record_id, source_id=rec.source_id, source_name=rec.source_name,
                              source_format=rec.source_format.value, record_index=rec.record_index,
                              source_hash=rec.source_hash, raw_payload=rec.raw_payload,
                              received_at=_naive(rec.received_at))
        self._apply(row, rec)
        self._s.add(row)
        self._s.flush()

    def update(self, rec: StoredSourceRecord) -> None:
        row = self._s.get(SourceRecordRow, rec.record_id)
        if row is None:
            raise LookupError("source record not found")
        self._apply(row, rec)
        self._s.flush()


class SqlAlchemyAuditLogRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def add(self, event: AuditEvent) -> None:
        self._s.add(AuditEventRow(occurred_at=_naive(event.occurred_at), event_type=event.event_type.value,
                                  source_name=event.source_name, record_id=event.record_id,
                                  patient_id=event.patient_id, outcome=event.outcome, details=event.details))
        self._s.flush()

    def list_events(self, record_id: str | None = None) -> list[AuditEvent]:
        stmt = select(AuditEventRow).order_by(AuditEventRow.id)
        if record_id:
            stmt = stmt.where(AuditEventRow.record_id == record_id)
        return [AuditEvent(id=r.id, occurred_at=_utc(r.occurred_at), event_type=AuditEventType(r.event_type),
                           source_name=r.source_name, record_id=r.record_id, patient_id=r.patient_id,
                           outcome=r.outcome, details=r.details) for r in self._s.scalars(stmt)]
