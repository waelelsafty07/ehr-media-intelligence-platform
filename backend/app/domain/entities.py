"""Persisted domain entities and audit events."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.domain.canonical import CanonicalRecord, Gender, ProcessingInfo, SourceFormat


class Patient(BaseModel):
    patient_id: str
    mrn: str  # normalized, unique
    full_name: str | None = None
    name_key: str | None = None
    date_of_birth: date | None = None
    gender: Gender = Gender.UNKNOWN
    created_at: datetime


class StoredSourceRecord(BaseModel):
    """Processing envelope: raw payload (stored once), normalized record, status and links."""

    record_id: str
    source_id: str
    source_name: str
    source_format: SourceFormat
    record_index: int
    source_hash: str
    content_hash: str | None = None
    raw_payload: dict[str, Any]
    canonical: CanonicalRecord | None = None
    processing: ProcessingInfo
    patient_id: str | None = None
    duplicate_of_id: str | None = None
    received_at: datetime


class AuditEventType(StrEnum):
    RECORD_RECEIVED = "record_received"
    RECORD_RETRY_STARTED = "record_retry_started"
    RECORD_NORMALIZED = "record_normalized"
    VALIDATION_FAILED = "validation_failed"
    DUPLICATE_DETECTED = "duplicate_detected"
    IDENTITY_DECISION = "identity_decision"
    PATIENT_CREATED = "patient_created"
    REVIEW_REQUIRED = "review_required"
    RECORD_COMPLETED = "record_completed"
    PROCESSING_FAILED = "processing_failed"
    REPROCESS_SKIPPED = "reprocess_skipped"
    INGEST_REJECTED = "ingest_rejected"
    FHIR_GENERATED = "fhir_generated"
    FHIR_UNCHANGED = "fhir_unchanged"
    FHIR_VALIDATION_FAILED = "fhir_validation_failed"
    FHIR_GENERATION_FAILED = "fhir_generation_failed"
    SUMMARY_GENERATED = "summary_generated"
    SUMMARY_CACHE_HIT = "summary_cache_hit"
    SUMMARY_FAILED = "summary_failed"
    INDEX_COMPLETED = "index_completed"
    INDEX_FAILED = "index_failed"


class AuditEvent(BaseModel):
    """Append-only. `details` holds codes, ids and counts only: no names, dates or clinical text."""

    id: int | None = None
    occurred_at: datetime
    event_type: AuditEventType
    source_name: str | None = None
    record_id: str | None = None
    patient_id: str | None = None
    outcome: str = "ok"
    details: dict[str, Any] = Field(default_factory=dict)
