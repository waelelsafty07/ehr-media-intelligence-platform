"""Semantic search domain: query validation, indexed-document model, text/metadata extraction, snippets.

Search never infers identity from similarity: every indexed document carries the patient_id of the
SQLite record it was derived from, and results are re-validated against SQLite at query time.
Similarity scores are NOT clinical relevance, diagnostic confidence or probability.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.summary import ClinicalSummary

RESOURCE_DOCUMENT_REFERENCE = "DocumentReference"
RESOURCE_CLINICAL_SUMMARY = "ClinicalSummary"  # AI-generated; not a FHIR resource
ResourceType = Literal["DocumentReference", "ClinicalSummary"]
MAX_RESULTS = 5
MAX_QUERY_CHARS = 500
SNIPPET_CHARS = 200


class SearchFilters(BaseModel):
    """Filters applied inside the vector store BEFORE ranking (never after)."""

    model_config = ConfigDict(frozen=True)
    patient_id: str | None = None
    resource_types: tuple[str, ...] | None = None
    date_from: date | None = None
    date_to: date | None = None


class SearchQuery(BaseModel):
    """Validated search request (also the HTTP request body)."""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(description="Free text to search for")
    patient_id: str | None = Field(default=None, min_length=1, max_length=64,
                                   description="Restrict results to this patient")
    resource_types: list[ResourceType] | None = Field(default=None, min_length=1, max_length=10)
    date_from: date | None = Field(default=None, description="Inclusive. Records without a date are excluded when a date bound is set")
    date_to: date | None = None
    limit: int = Field(default=MAX_RESULTS, ge=1, le=MAX_RESULTS)
    min_score: float | None = Field(default=None, ge=0.0, le=1.0, description="Optional similarity floor (results with similarity <= 0 are never returned)")

    @field_validator("query")
    @classmethod
    def _query(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("query must not be blank")
        if len(v) > MAX_QUERY_CHARS:
            raise ValueError(f"query must be at most {MAX_QUERY_CHARS} characters")
        return v

    @model_validator(mode="after")
    def _dates(self) -> SearchQuery:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self

    @property
    def filters(self) -> SearchFilters:
        types = tuple(sorted(set(self.resource_types))) if self.resource_types else None
        return SearchFilters(patient_id=self.patient_id, resource_types=types,
                             date_from=self.date_from, date_to=self.date_to)


class IndexedDocument(BaseModel):
    """What the index stores about a document (without the vector)."""

    doc_id: str  # "<resource_type>:<resource_id>"
    patient_id: str
    resource_type: str
    resource_id: str
    source_record_id: str | None = None
    record_date: date | None = None
    text: str
    content_hash: str
    model_identifier: str


class IndexEntry(IndexedDocument):
    embedding: list[float]
    indexed_at: datetime


class VectorHit(IndexedDocument):
    score: float  # cosine similarity in [-1, 1]


class IndexStats(BaseModel):
    total: int
    by_resource_type: dict[str, int]
    by_model: dict[str, int]


def make_doc_id(resource_type: str, resource_id: str) -> str:
    return f"{resource_type}:{resource_id}"


def index_content_hash(model_identifier: str, resource_type: str, record_date: date | None, text: str) -> str:
    """Changes when the text, its date, its type, or the embedding model changes -> triggers re-embedding."""
    payload = "\x1f".join([model_identifier, resource_type, record_date.isoformat() if record_date else "", text])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- extraction from stored resources
def document_text_from_resource(resource: dict[str, Any]) -> str | None:
    """Decoded text of the first attachment of a DocumentReference JSON; None if absent/undecodable."""
    for content in resource.get("content") or []:
        data = ((content or {}).get("attachment") or {}).get("data")
        if not data:
            continue
        try:
            text = base64.b64decode(data, validate=True).decode("utf-8")
        except (binascii.Error, ValueError):
            return None
        return text.strip() or None
    return None


def document_date_from_resource(resource: dict[str, Any]) -> date | None:
    start = ((resource.get("context") or {}).get("period") or {}).get("start")
    try:
        return date.fromisoformat(str(start)[:10]) if start else None
    except ValueError:
        return None


def summary_index_text(summary: ClinicalSummary) -> str:
    parts = []
    if summary.chief_concern:
        parts.append(f"Chief concern: {summary.chief_concern.text}.")
    if summary.key_diagnoses:
        parts.append("Diagnoses: " + ", ".join(d.name for d in summary.key_diagnoses) + ".")
    parts.append(summary.summary_text)
    return " ".join(parts)


def summary_record_date(summary: ClinicalSummary) -> date | None:
    """Most recent source-record date covered by the summary (None if no source record is dated)."""
    dates = [r.document_date for r in summary.recent_records if r.document_date]
    return max(dates) if dates else None


# ---------------------------------------------------------------- snippets
_TERM = re.compile(r"\w{3,}")


def build_snippet(text: str, query: str, max_chars: int = SNIPPET_CHARS) -> str:
    """Deterministic excerpt of the stored text, centred on the first query word found (else the start)."""
    flat = " ".join(text.split())
    if len(flat) <= max_chars:
        return flat
    lower = flat.casefold()
    hits = [i for t in _TERM.findall(query.casefold()) if (i := lower.find(t)) >= 0]
    start = max(0, min(hits) - 60) if hits else 0
    if start > 0:
        space = flat.find(" ", start)
        start = space + 1 if 0 <= space < start + 20 else start
    end = min(len(flat), start + max_chars)
    if end < len(flat):
        space = flat.rfind(" ", start, end)
        end = space if space > start + max_chars // 2 else end
    return ("…" if start > 0 else "") + flat[start:end].strip() + ("…" if end < len(flat) else "")
