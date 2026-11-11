"""Clinical summary domain: schemas, deterministic context building, grounding checks.

Design: the model writes only what needs language (chief concern, diagnoses, anomalies, prose). Every
statement must carry an exact quote from, and the IDs of, patient records. Everything verifiable
(recent records, missing information, source IDs, quality indicator) is built here deterministically.

The checks below reduce the risk of unsupported output; they do NOT prove clinical correctness. A quote
check shows the quoted words exist in a cited record, not that the paraphrase is faithful.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.entities import Patient, StoredSourceRecord
from app.domain.errors import PatientIsolationError, SummaryOutputError

MAX_SUMMARY_WORDS = 200
DISCLAIMER = ("AI-generated from the available source records. It requires review by a qualified clinician "
              "before any use, is not a verified diagnosis, is not medical advice, and has not been clinically "
              "validated.")
QUALITY_EXPLANATION = ("Heuristic evidence-coverage indicator computed from the source records (see factors). "
                       "It is NOT a model confidence, NOT a probability, and NOT clinical validation.")


# ------------------------------------------------------------------ context (the evidence)
class SourceDocument(BaseModel):
    model_config = ConfigDict(frozen=True)
    record_id: str
    document_date: date | None = None
    text: str = ""
    diagnoses: list[str] = Field(default_factory=list)
    truncated: bool = False

    @property
    def searchable_text(self) -> str:
        return " ".join([self.text, *self.diagnoses])


class SummaryContext(BaseModel):
    """Exactly what the model is shown, and the basis of the cache key."""

    patient_id: str
    documents: list[SourceDocument]
    omitted_count: int = 0  # eligible records left out by the record cap
    dob_known: bool = True
    content_hash: str


def _sort_key(d: SourceDocument) -> tuple[int, str, str]:
    # newest first; undated last; record_id keeps the order deterministic
    return (0 if d.document_date else 1, "".join(chr(255 - ord(c)) for c in (d.document_date.isoformat() if d.document_date else "")), d.record_id)


def build_summary_context(
    patient: Patient, records: list[StoredSourceRecord], *, max_records: int, max_chars: int
) -> SummaryContext:
    """Patient-scoped, deterministic selection. Raises PatientIsolationError if any record is foreign."""
    docs: list[SourceDocument] = []
    hashes: dict[str, str] = {}
    for rec in records:
        if rec.patient_id != patient.patient_id:  # defense in depth: the repository already filters
            raise PatientIsolationError("A record outside the patient scope reached the summary pipeline.")
        c = rec.canonical
        if c is None or not (c.document or c.diagnostics):
            continue
        text = c.document.text if c.document else ""
        docs.append(SourceDocument(
            record_id=rec.record_id, document_date=c.document.document_date if c.document else None,
            text=text[:max_chars], truncated=len(text) > max_chars,
            diagnoses=[d.description for d in c.diagnostics]))
        hashes[rec.record_id] = rec.content_hash or ""
    docs.sort(key=_sort_key)
    kept, omitted = docs[:max_records], max(0, len(docs) - max_records)
    payload = {"records": sorted((d.record_id, hashes[d.record_id], len(d.text)) for d in kept),
               "max_records": max_records, "max_chars": max_chars}
    return SummaryContext(patient_id=patient.patient_id, documents=kept, omitted_count=omitted,
                          dob_known=patient.date_of_birth is not None,
                          content_hash=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())


# ------------------------------------------------------------------ model output schema
class EvidencedStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=400)
    evidence_quote: str = Field(min_length=4, max_length=500)  # exact words from a cited record
    source_record_ids: list[str] = Field(min_length=1, max_length=10)


class DiagnosisStatement(EvidencedStatement):
    name: str = Field(min_length=1, max_length=150)  # must appear in evidence_quote
    certainty: Literal["stated", "uncertain"]
    temporal: Literal["current", "historical", "unspecified"]


def word_count(text: str) -> int:
    return len(text.split())


class LLMSummaryOutput(BaseModel):
    """What the model must return (JSON). Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")
    chief_concern: EvidencedStatement | None = None
    key_diagnoses: list[DiagnosisStatement] = Field(default_factory=list, max_length=10)
    anomalies: list[EvidencedStatement] = Field(default_factory=list, max_length=5)
    summary_text: str

    @field_validator("summary_text")
    @classmethod
    def _length(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("summary_text is empty")
        if word_count(v) > MAX_SUMMARY_WORDS:
            raise ValueError(f"summary_text exceeds {MAX_SUMMARY_WORDS} words")
        return v.strip()


# ------------------------------------------------------------------ final, validated summary
class RecentRecord(BaseModel):
    record_id: str
    document_date: date | None = None
    excerpt: str
    diagnoses: list[str] = Field(default_factory=list)


class QualityIndicator(BaseModel):
    level: Literal["low", "medium", "high"]
    factors: dict[str, bool]
    explanation: str = QUALITY_EXPLANATION


class ClinicalSummary(BaseModel):
    patient_id: str
    chief_concern: EvidencedStatement | None
    key_diagnoses: list[DiagnosisStatement]
    recent_records: list[RecentRecord]  # deterministic, not model-written
    anomalies: list[EvidencedStatement]
    missing_information: list[str]  # deterministic
    summary_text: str
    source_record_ids: list[str]  # every record shown to the model
    confidence_or_quality_indicator: QualityIndicator
    generated_at: datetime
    model_identifier: str
    prompt_version: str
    source_content_hash: str
    disclaimer: str = DISCLAIMER

    @field_validator("summary_text")
    @classmethod
    def _length(cls, v: str) -> str:
        if word_count(v) > MAX_SUMMARY_WORDS:
            raise ValueError("summary_text too long")
        return v


class SummaryStatus(StrEnum):
    GENERATED = "generated"
    FAILED = "failed"  # retryable; never served as a cache hit


class StoredSummary(BaseModel):
    summary_id: str
    patient_id: str
    content_hash: str
    prompt_version: str
    model_identifier: str
    status: SummaryStatus
    summary: ClinicalSummary | None = None
    failure_category: str | None = None
    attempts: int = 1
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------ safe parsing
def parse_model_output(text: str) -> LLMSummaryOutput:
    """JSON -> validated model. Never raises anything but SummaryOutputError (codes only, no content)."""
    candidate = (text or "").strip()
    if not candidate:
        raise SummaryOutputError(["empty_output"])
    try:
        data: Any = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")  # tolerate prose/code fences around the JSON
        try:
            data = json.loads(candidate[start:end + 1]) if 0 <= start < end else None
        except json.JSONDecodeError:
            data = None
        if data is None:
            raise SummaryOutputError(["malformed_json"]) from None
    if not isinstance(data, dict):
        raise SummaryOutputError(["output_not_an_object"])
    try:
        return LLMSummaryOutput.model_validate(data)
    except ValidationError as exc:  # report field locations and rule types only, never values
        codes = sorted({f"schema:{'.'.join(str(p) for p in e['loc'])}:{e['type']}" for e in exc.errors()}) or ["schema"]
        raise SummaryOutputError(codes) from None


# ------------------------------------------------------------------ grounding checks
class GroundingIssue(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: str
    location: str


_UNCERTAIN = re.compile(r"\b(possible|possibly|probable|probably|suspected|suspicion|likely|unlikely|"
                        r"rule[sd]? out|r/o|query|\?|cannot exclude|differential|questionable)\b", re.IGNORECASE)
_NEGATED = re.compile(r"\b(no evidence of|denies|denied|negative for|ruled out|absence of|without)\b", re.IGNORECASE)
_HISTORICAL = re.compile(r"\b(history of|h/o|hx of|previous|previously|prior|past|resolved|remote)\b", re.IGNORECASE)
_ADVICE = re.compile(r"\b(you should|should (?:start|stop|take|increase|decrease|consider)|recommend(?:s|ed)?|"
                     r"advis(?:e|ed|es)|prescrib(?:e|ed|es)|start taking|stop taking|consider (?:starting|stopping))\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _norm(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.casefold().split())


def _numbers(text: str) -> set[str]:
    return set(_NUMBER.findall(text))


def verify_grounding(output: LLMSummaryOutput, context: SummaryContext) -> list[GroundingIssue]:
    issues: list[GroundingIssue] = []
    docs = {d.record_id: _norm(d.searchable_text) for d in context.documents}

    def check_evidence(stmt: EvidencedStatement, where: str) -> None:
        unknown = [r for r in stmt.source_record_ids if r not in docs]
        if unknown:
            issues.append(GroundingIssue(code="unknown_source_record", location=where))
        known = [docs[r] for r in stmt.source_record_ids if r in docs]
        if known and not any(_norm(stmt.evidence_quote) in text for text in known):
            issues.append(GroundingIssue(code="quote_not_in_cited_source", location=where))

    if output.chief_concern:
        check_evidence(output.chief_concern, "chief_concern")
    for i, anomaly in enumerate(output.anomalies):
        check_evidence(anomaly, f"anomalies[{i}]")
    for i, dx in enumerate(output.key_diagnoses):
        where = f"key_diagnoses[{i}]"
        check_evidence(dx, where)
        quote = dx.evidence_quote
        if _norm(dx.name) not in _norm(quote):
            issues.append(GroundingIssue(code="diagnosis_not_in_quote", location=where))
        if _NEGATED.search(quote):
            issues.append(GroundingIssue(code="negated_diagnosis", location=where))
        if _UNCERTAIN.search(quote) and dx.certainty != "uncertain":
            issues.append(GroundingIssue(code="uncertainty_lost", location=where))
        if _HISTORICAL.search(quote) and dx.temporal == "current":
            issues.append(GroundingIssue(code="history_marked_current", location=where))

    all_text = " ".join(docs.values())
    allowed_numbers = _numbers(all_text) | _numbers(" ".join(d.document_date.isoformat() for d in context.documents if d.document_date))
    for token in sorted(_numbers(output.summary_text)):
        if len(token) >= 2 and token not in allowed_numbers:  # single digits (counts) are tolerated
            issues.append(GroundingIssue(code="summary_number_or_date_unsupported", location="summary_text"))
            break
    for match in _ADVICE.finditer(output.summary_text):
        if _norm(match.group(0)) not in all_text:
            issues.append(GroundingIssue(code="summary_advice_language", location="summary_text"))
            break
    return issues


# ------------------------------------------------------------------ deterministic parts
def build_recent_records(context: SummaryContext, limit: int = 5) -> list[RecentRecord]:
    out = []
    for d in context.documents[:limit]:  # already newest first
        excerpt = " ".join(d.text.split())
        out.append(RecentRecord(record_id=d.record_id, document_date=d.document_date,
                                excerpt=excerpt if len(excerpt) <= 160 else excerpt[:159] + "…",
                                diagnoses=d.diagnoses))
    return out


def build_missing_information(context: SummaryContext, output: LLMSummaryOutput) -> list[str]:
    notes: list[str] = []
    if not context.dob_known:
        notes.append("Date of birth is not recorded.")
    if output.chief_concern is None:
        notes.append("No chief concern is documented in the available records.")
    if not any(d.diagnoses for d in context.documents):
        notes.append("No diagnoses are explicitly stated in the available records.")
    undated = sum(1 for d in context.documents if d.document_date is None)
    if undated:
        notes.append(f"{undated} of {len(context.documents)} records have no document date.")
    if any(d.truncated for d in context.documents):
        notes.append("Some record text was truncated to fit processing limits.")
    if context.omitted_count:
        notes.append(f"{context.omitted_count} older record(s) were not included because of the record limit.")
    return notes


def compute_quality(context: SummaryContext, output: LLMSummaryOutput) -> QualityIndicator:
    factors = {
        "chief_concern_documented": output.chief_concern is not None,
        "diagnoses_documented": any(d.diagnoses for d in context.documents),
        "multiple_source_records": len(context.documents) >= 2,
        "all_records_dated": all(d.document_date for d in context.documents),
    }
    score = sum(factors.values())
    return QualityIndicator(level="low" if score <= 1 else "medium" if score <= 3 else "high", factors=factors)


def assemble_summary(output: LLMSummaryOutput, context: SummaryContext, *, model_identifier: str,
                     prompt_version: str, generated_at: datetime) -> ClinicalSummary:
    return ClinicalSummary(
        patient_id=context.patient_id, chief_concern=output.chief_concern, key_diagnoses=output.key_diagnoses,
        recent_records=build_recent_records(context), anomalies=output.anomalies,
        missing_information=build_missing_information(context, output), summary_text=output.summary_text,
        source_record_ids=[d.record_id for d in context.documents],
        confidence_or_quality_indicator=compute_quality(context, output), generated_at=generated_at,
        model_identifier=model_identifier, prompt_version=prompt_version,
        source_content_hash=context.content_hash)
