"""Request/response schemas for the HTTP boundary."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.application.dto import IngestResult
from app.domain.canonical import IssueCode, ProcessingStatus, Severity, SourceFormat
from app.domain.summary import ClinicalSummary


class IssueSchema(BaseModel):
    code: IssueCode
    field: str
    severity: Severity
    message: str


class RecordResultSchema(BaseModel):
    source_id: str
    record_id: str
    status: ProcessingStatus
    attempts: int
    patient_id: str | None
    duplicate_of_record_id: str | None
    identity_rationale_code: str | None
    already_ingested: bool
    issues: list[IssueSchema]


class IngestResponse(BaseModel):
    source_name: str
    source_format: SourceFormat
    total: int
    counts: dict[str, int]
    results: list[RecordResultSchema]

    @classmethod
    def from_result(cls, r: IngestResult) -> IngestResponse:
        return cls(
            source_name=r.source_name, source_format=r.source_format, total=len(r.results), counts=r.counts,
            results=[RecordResultSchema(
                source_id=x.source_id, record_id=x.record_id, status=x.status, attempts=x.attempts,
                patient_id=x.patient_id, duplicate_of_record_id=x.duplicate_of_record_id,
                identity_rationale_code=x.identity_rationale_code, already_ingested=x.already_ingested,
                issues=[IssueSchema(code=i.code, field=i.field, severity=i.severity, message=i.message)
                        for i in x.issues]) for x in r.results])


class FhirIssueSchema(BaseModel):
    code: str
    path: str
    message: str


class FhirStatusResponse(BaseModel):
    """FHIR processing state: independent of the ingestion status of the patient's records."""

    patient_id: str
    status: str  # generated | invalid | failed | not_generated
    unchanged: bool = False
    bundle_id: str | None = None
    resource_counts: dict[str, int] = {}
    issues: list[FhirIssueSchema] = []
    updated_at: datetime | None = None

    @classmethod
    def from_result(cls, patient_id: str, r) -> FhirStatusResponse:
        if r is None:
            return cls(patient_id=patient_id, status="not_generated")
        return cls(patient_id=r.patient_id, status=r.status.value, unchanged=r.unchanged, bundle_id=r.bundle_id,
                   resource_counts=r.resource_counts, updated_at=r.updated_at,
                   issues=[FhirIssueSchema(code=i.code, path=i.path, message=i.message) for i in r.issues])


class SummaryResponse(BaseModel):
    """A validated clinical summary plus cache/staleness metadata. Not medical advice."""

    summary: ClinicalSummary
    summary_id: str
    cache_hit: bool = False
    is_stale: bool = False
    stale_reasons: list[str] = []
