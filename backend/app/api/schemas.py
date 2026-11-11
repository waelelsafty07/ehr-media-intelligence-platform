"""Request/response schemas for the HTTP boundary."""

from __future__ import annotations

from pydantic import BaseModel

from app.application.dto import IngestResult
from app.domain.canonical import IssueCode, ProcessingStatus, Severity, SourceFormat


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
