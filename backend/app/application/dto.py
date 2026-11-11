"""Application DTOs (use case inputs/outputs)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.domain.canonical import ProcessingStatus, SourceFormat, ValidationIssue
from app.domain.fhir import FhirIssue, FhirStatus


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


@dataclass(frozen=True)
class FhirGenerationResult:
    patient_id: str
    status: FhirStatus
    unchanged: bool = False  # inputs identical to the last successful generation; nothing rewritten
    bundle_id: str | None = None
    resource_counts: dict[str, int] = field(default_factory=dict)
    issues: list[FhirIssue] = field(default_factory=list)
    updated_at: datetime | None = None


@dataclass(frozen=True)
class IndexResult:
    patient_id: str
    indexed: int  # newly embedded or re-embedded entries
    unchanged: int  # already current; not re-embedded
    removed: int  # stale entries deleted
    skipped: list[str] = field(default_factory=list)  # reason codes for documents that were not indexed


@dataclass(frozen=True)
class SearchResultItem:
    rank: int
    resource_type: str
    resource_id: str
    patient_id: str  # internal opaque id; no name/MRN/DOB is ever included in search results
    record_date: date | None
    score: float  # cosine similarity: NOT clinical relevance or probability
    snippet: str
    source_record_id: str | None
    ai_generated: bool


@dataclass(frozen=True)
class SearchOutcome:
    items: list[SearchResultItem]
    candidates_considered: int
    stale_results_dropped: int
    took_ms: float
    embedding_model: str
    embedding_kind: str


@dataclass(frozen=True)
class SearchStatus:
    entries_total: int
    by_resource_type: dict[str, int]
    by_model: dict[str, int]
    embedding_model: str
    embedding_kind: str
    embedding_available: bool  # False when the configured provider cannot currently be used (e.g. extra not installed)
