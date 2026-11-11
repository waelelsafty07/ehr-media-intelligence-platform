"""Canonical, source-independent record model.

Nothing here knows about JSON/CSV/text layouts, FHIR, or the database. Only *normalized* values
are held; the original payload is stored once, on the persisted source record.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SourceFormat(StrEnum):
    JSON = "json"
    CSV = "csv"
    TEXT = "text"


class Gender(StrEnum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"
    UNKNOWN = "unknown"


class ProcessingStatus(StrEnum):
    RECEIVED = "received"  # stored, not yet processed (retryable)
    COMPLETED = "completed"  # normalized, identity resolved, persisted
    REJECTED = "rejected"  # failed validation; kept for correction, not retried
    DUPLICATE = "duplicate"  # same normalized content as an earlier record
    NEEDS_REVIEW = "needs_review"  # identity conflict/ambiguity; nothing linked or merged
    FAILED = "failed"  # processing error; retryable


RETRYABLE_STATUSES = frozenset({ProcessingStatus.RECEIVED, ProcessingStatus.FAILED})


class IssueCode(StrEnum):
    MISSING_REQUIRED = "missing_required"
    MISSING_OPTIONAL = "missing_optional"
    INVALID_VALUE = "invalid_value"
    RECOVERED_FORMAT = "recovered_format"
    DUPLICATE_RECORD = "duplicate_record"
    IDENTITY_CONFLICT = "identity_conflict"
    AMBIGUOUS_MATCH = "ambiguous_match"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    """One finding. `message` never contains the offending value (it may be clinical data)."""

    model_config = ConfigDict(frozen=True)
    code: IssueCode
    field: str
    severity: Severity
    message: str


class SourceMetadata(BaseModel):
    source_id: str  # "<source_name>#<record_index>"
    source_name: str
    source_format: SourceFormat
    record_index: int
    source_hash: str  # sha256 of the raw record content (idempotency key)
    received_at: datetime


class PatientInfo(BaseModel):
    mrn: str | None = None  # normalized
    full_name: str | None = None
    date_of_birth: date | None = None
    gender: Gender = Gender.UNKNOWN


class ClinicalDocumentInfo(BaseModel):
    text: str
    document_date: date | None = None


class DiagnosticInfo(BaseModel):
    """A diagnosis exactly as stated in the source. No coding or dates are inferred."""

    description: str


class CanonicalRecord(BaseModel):
    record_id: str  # deterministic from source_hash; stable across reprocessing
    source: SourceMetadata
    patient: PatientInfo
    document: ClinicalDocumentInfo | None = None
    diagnostics: list[DiagnosticInfo] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == Severity.ERROR for i in self.issues)

    def content_hash(self) -> str:
        """Hash of normalized clinical content: equal for records that differ only in formatting."""
        payload = {
            "mrn": self.patient.mrn,
            "dob": self.patient.date_of_birth.isoformat() if self.patient.date_of_birth else None,
            "doc_date": (
                self.document.document_date.isoformat()
                if self.document and self.document.document_date
                else None
            ),
            "text": " ".join(self.document.text.split()).casefold() if self.document else None,
            "dx": sorted(" ".join(d.description.split()).casefold() for d in self.diagnostics),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class ProcessingInfo(BaseModel):
    status: ProcessingStatus
    attempts: int = 1
    failure_category: str | None = None  # exception class name only; no details
    updated_at: datetime
