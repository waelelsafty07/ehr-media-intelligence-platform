"""SQLAlchemy implementations of the domain repository ports."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.canonical import (
    CanonicalRecord,
    Gender,
    ProcessingInfo,
    ProcessingStatus,
    SourceFormat,
)
from app.domain.entities import AuditEvent, AuditEventType, Patient, StoredSourceRecord
from app.domain.fhir import FhirIssue, FhirResourceRecord, FhirStatus, PatientFhirState
from app.domain.summary import ClinicalSummary, StoredSummary, SummaryStatus
from app.infrastructure.persistence.tables import (
    AuditEventRow,
    ClinicalSummaryRow,
    FhirResourceRow,
    PatientFhirStateRow,
    PatientRow,
    SourceRecordRow,
)


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

    def get(self, patient_id: str) -> Patient | None:
        row = self._s.get(PatientRow, patient_id)
        return self._to_domain(row) if row else None

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

    def list_completed_for_patient(self, patient_id: str) -> list[StoredSourceRecord]:
        rows = self._s.scalars(
            select(SourceRecordRow)
            .where(SourceRecordRow.patient_id == patient_id,
                   SourceRecordRow.status == ProcessingStatus.COMPLETED.value)
            .order_by(SourceRecordRow.received_at, SourceRecordRow.record_id))
        return [self._to_domain(r) for r in rows]

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


class SqlAlchemyFhirRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    @staticmethod
    def _resource(r: FhirResourceRow) -> FhirResourceRecord:
        return FhirResourceRecord(resource_type=r.resource_type, resource_id=r.resource_id,
                                  patient_id=r.patient_id, source_record_id=r.source_record_id,
                                  position=r.position, content=r.content)

    def get_state(self, patient_id: str) -> PatientFhirState | None:
        r = self._s.get(PatientFhirStateRow, patient_id)
        if r is None:
            return None
        return PatientFhirState(
            patient_id=r.patient_id, status=FhirStatus(r.status), fingerprint=r.fingerprint,
            bundle_id=r.bundle_id, bundle_timestamp=_utc(r.bundle_timestamp) if r.bundle_timestamp else None,
            resource_count=r.resource_count, issues=[FhirIssue(**i) for i in r.issues],
            failure_category=r.failure_category, updated_at=_utc(r.updated_at))

    def save_state(self, state: PatientFhirState) -> None:
        row = self._s.get(PatientFhirStateRow, state.patient_id) or PatientFhirStateRow(patient_id=state.patient_id)
        row.status = state.status.value
        row.fingerprint = state.fingerprint
        row.bundle_id = state.bundle_id
        row.bundle_timestamp = _naive(state.bundle_timestamp) if state.bundle_timestamp else None
        row.resource_count = state.resource_count
        row.issues = [i.model_dump() for i in state.issues]
        row.failure_category = state.failure_category
        row.updated_at = _naive(state.updated_at)
        self._s.add(row)
        self._s.flush()

    def replace_resources(self, patient_id: str, resources: list[FhirResourceRecord]) -> None:
        self._s.execute(delete(FhirResourceRow).where(FhirResourceRow.patient_id == patient_id))
        now = _naive(datetime.now(UTC))
        self._s.add_all(FhirResourceRow(
            resource_type=r.resource_type, resource_id=r.resource_id, patient_id=patient_id,
            source_record_id=r.source_record_id, position=r.position, content=r.content, updated_at=now)
            for r in resources)
        self._s.flush()

    def list_resources(self, patient_id: str, resource_type: str | None = None) -> list[FhirResourceRecord]:
        stmt = select(FhirResourceRow).where(FhirResourceRow.patient_id == patient_id).order_by(FhirResourceRow.position)
        if resource_type:
            stmt = stmt.where(FhirResourceRow.resource_type == resource_type)
        return [self._resource(r) for r in self._s.scalars(stmt)]

    def get_resource(self, resource_type: str, resource_id: str) -> FhirResourceRecord | None:
        row = self._s.get(FhirResourceRow, (resource_type, resource_id))
        return self._resource(row) if row else None


class SqlAlchemyClinicalSummaryRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    @staticmethod
    def _to_domain(r: ClinicalSummaryRow) -> StoredSummary:
        return StoredSummary(
            summary_id=r.summary_id, patient_id=r.patient_id, content_hash=r.content_hash,
            prompt_version=r.prompt_version, model_identifier=r.model_identifier, status=SummaryStatus(r.status),
            summary=ClinicalSummary.model_validate(r.summary) if r.summary else None,
            failure_category=r.failure_category, attempts=r.attempts, created_at=_utc(r.created_at),
            updated_at=_utc(r.updated_at))

    def _by_key(self, patient_id: str, content_hash: str, prompt_version: str,
                model_identifier: str) -> ClinicalSummaryRow | None:
        return self._s.scalar(select(ClinicalSummaryRow).where(
            ClinicalSummaryRow.patient_id == patient_id, ClinicalSummaryRow.content_hash == content_hash,
            ClinicalSummaryRow.prompt_version == prompt_version,
            ClinicalSummaryRow.model_identifier == model_identifier))

    def get_by_key(self, patient_id: str, content_hash: str, prompt_version: str,
                   model_identifier: str) -> StoredSummary | None:
        row = self._by_key(patient_id, content_hash, prompt_version, model_identifier)
        return self._to_domain(row) if row else None

    def save(self, summary: StoredSummary) -> None:
        row = self._by_key(summary.patient_id, summary.content_hash, summary.prompt_version,
                           summary.model_identifier) or ClinicalSummaryRow(
            summary_id=summary.summary_id, patient_id=summary.patient_id, content_hash=summary.content_hash,
            prompt_version=summary.prompt_version, model_identifier=summary.model_identifier,
            created_at=_naive(summary.created_at))
        row.status = summary.status.value
        row.summary = summary.summary.model_dump(mode="json") if summary.summary else None
        row.failure_category = summary.failure_category
        row.attempts = summary.attempts
        row.updated_at = _naive(summary.updated_at)
        self._s.add(row)
        self._s.flush()

    def get_latest_generated(self, patient_id: str) -> StoredSummary | None:
        row = self._s.scalar(
            select(ClinicalSummaryRow)
            .where(ClinicalSummaryRow.patient_id == patient_id,
                   ClinicalSummaryRow.status == SummaryStatus.GENERATED.value)
            .order_by(ClinicalSummaryRow.updated_at.desc(), ClinicalSummaryRow.summary_id).limit(1))
        return self._to_domain(row) if row else None
