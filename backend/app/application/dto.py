"""Application DTOs (use case inputs/outputs)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.canonical import ProcessingStatus, SourceFormat, ValidationIssue


@dataclass(frozen=True)
class IngestCommand:
    content: bytes
    source_name: str
    source_format: SourceFormat


@dataclass(frozen=True)
class RecordResult:
    source_id: str
    record_id: str
    status: ProcessingStatus
    attempts: int
    patient_id: str | None = None
    duplicate_of_record_id: str | None = None
    identity_rationale_code: str | None = None
    issues: list[ValidationIssue] = field(default_factory=list)
    already_ingested: bool = False  # identical raw content was processed before; nothing re-created


@dataclass(frozen=True)
class IngestResult:
    source_name: str
    source_format: SourceFormat
    results: list[RecordResult]

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r.status.value] = out.get(r.status.value, 0) + 1
        return out
