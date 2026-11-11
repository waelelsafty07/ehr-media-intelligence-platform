"""SQLAlchemy table definitions. Schema is created with create_all (no migrations yet; see docs)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, String
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


