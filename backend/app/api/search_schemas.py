"""Response schemas for search and indexing."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.application.dto import IndexResult, SearchOutcome, SearchStatus

SCORE_NOTE = ("Scores are embedding similarity, not clinical relevance, diagnostic confidence or a probability. "
              "ClinicalSummary results are AI-generated and require clinician review.")


class EmbeddingInfo(BaseModel):
    model_identifier: str
    kind: str  # "lexical" (hashing, no semantic understanding) | "semantic" (sentence-transformers)


class SearchResultSchema(BaseModel):
    rank: int
    resource_type: str  # DocumentReference | ClinicalSummary
    resource_id: str
    fhir_reference: str | None  # "DocumentReference/<id>"; null for AI summaries (not FHIR resources)
    link: str  # API path of the underlying FHIR bundle / summary
    patient_id: str  # opaque internal id; search results never contain name, MRN or date of birth
    record_date: date | None
    score: float
    snippet: str
    source_record_id: str | None
    ai_generated: bool


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResultSchema]
    returned: int
    candidates_considered: int
    stale_results_dropped: int
    took_ms: float
    embedding: EmbeddingInfo
    note: str = SCORE_NOTE

    @classmethod
    def from_outcome(cls, query: str, o: SearchOutcome) -> SearchResponse:
        results = [SearchResultSchema(
            rank=i.rank, resource_type=i.resource_type, resource_id=i.resource_id,
            fhir_reference=f"DocumentReference/{i.resource_id}" if i.resource_type == "DocumentReference" else None,
            link=(f"/patients/{i.patient_id}/fhir" if i.resource_type == "DocumentReference"
                  else f"/patients/{i.patient_id}/summary"),
            patient_id=i.patient_id, record_date=i.record_date,
            score=i.score, snippet=i.snippet, source_record_id=i.source_record_id, ai_generated=i.ai_generated)
            for i in o.items]
        return cls(query=query, results=results, returned=len(results), candidates_considered=o.candidates_considered,
                   stale_results_dropped=o.stale_results_dropped, took_ms=o.took_ms,
                   embedding=EmbeddingInfo(model_identifier=o.embedding_model, kind=o.embedding_kind))


class IndexResponse(BaseModel):
    patient_id: str
    indexed: int
    unchanged: int
    removed: int
    skipped: list[str]

    @classmethod
    def from_result(cls, r: IndexResult) -> IndexResponse:
        return cls(patient_id=r.patient_id, indexed=r.indexed, unchanged=r.unchanged, removed=r.removed,
                   skipped=r.skipped)


class ReindexResponse(BaseModel):
    patients: int
    indexed: int
    unchanged: int
    removed: int
    results: list[IndexResponse]


class EmbeddingStatus(EmbeddingInfo):
    available: bool  # false = configured provider cannot be used now; searches will return 503 (no silent fallback)


class SearchStatusResponse(BaseModel):
    entries_total: int
    by_resource_type: dict[str, int]
    by_model: dict[str, int]
    embedding: EmbeddingStatus

    @classmethod
    def from_status(cls, s: SearchStatus) -> SearchStatusResponse:
        return cls(entries_total=s.entries_total, by_resource_type=s.by_resource_type, by_model=s.by_model,
                   embedding=EmbeddingStatus(model_identifier=s.embedding_model, kind=s.embedding_kind,
                                             available=s.embedding_available))
