"""SQLAlchemy table definitions. Schema is created with create_all (no migrations yet; see docs)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PatientRow(Base):
    __tablename__ = "patients"
    patient_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mrn: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    name_key: Mapped[str | None] = mapped_column(String(255))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    __table_args__ = (Index("ix_patients_name_key_dob", "name_key", "date_of_birth"),)


class SourceRecordRow(Base):
    __tablename__ = "source_records"
    record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(300), index=True)
    source_name: Mapped[str] = mapped_column(String(255))
    source_format: Mapped[str] = mapped_column(String(8))
    record_index: Mapped[int] = mapped_column(Integer)
    source_hash: Mapped[str] = mapped_column(String(64), unique=True)  # idempotency key
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    canonical: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    failure_category: Mapped[str | None] = mapped_column(String(100))
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.patient_id"), index=True)
    duplicate_of_id: Mapped[str | None] = mapped_column(ForeignKey("source_records.record_id"))
    received_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    source_name: Mapped[str | None] = mapped_column(String(255))
    record_id: Mapped[str | None] = mapped_column(ForeignKey("source_records.record_id"), index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.patient_id"))
    outcome: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict[str, Any]] = mapped_column(JSON)




class FhirResourceRow(Base):
    """One generated FHIR resource (JSON). Replaced wholesale per patient on regeneration."""

    __tablename__ = "fhir_resources"
    resource_type: Mapped[str] = mapped_column(String(40), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.patient_id"), index=True)
    source_record_id: Mapped[str | None] = mapped_column(ForeignKey("source_records.record_id"))
    position: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    __table_args__ = (Index("ix_fhir_resources_patient_type", "patient_id", "resource_type"),)


class PatientFhirStateRow(Base):
    """FHIR processing state per patient, separate from source_records.status (ingestion)."""

    __tablename__ = "patient_fhir_state"
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.patient_id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    bundle_id: Mapped[str | None] = mapped_column(String(64))
    bundle_timestamp: Mapped[datetime | None] = mapped_column(DateTime)
    resource_count: Mapped[int] = mapped_column(Integer, default=0)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    failure_category: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class ClinicalSummaryRow(Base):
    """One summary per cache key; a failed row is replaced when a retry succeeds."""

    __tablename__ = "clinical_summaries"
    summary_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.patient_id"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(64))
    model_identifier: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16))
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    failure_category: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    __table_args__ = (UniqueConstraint("patient_id", "content_hash", "prompt_version", "model_identifier",
                                       name="uq_summary_cache_key"),)


class SearchIndexRow(Base):
    """Derived semantic-search index entry (rebuildable from fhir_resources + clinical_summaries)."""

    __tablename__ = "search_index_entries"
    doc_id: Mapped[str] = mapped_column(String(120), primary_key=True)  # "<resource_type>:<resource_id>"
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.patient_id"), index=True)
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[str] = mapped_column(String(64))
    source_record_id: Mapped[str | None] = mapped_column(String(36))
    record_date: Mapped[date | None] = mapped_column(Date)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    model_identifier: Mapped[str] = mapped_column(String(120), index=True)
    dimension: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)
    indexed_at: Mapped[datetime] = mapped_column(DateTime)
    __table_args__ = (Index("ix_search_patient_type_date", "patient_id", "resource_type", "record_date"),)
