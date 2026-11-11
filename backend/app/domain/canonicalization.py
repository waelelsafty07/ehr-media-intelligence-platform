"""Raw field mapping -> CanonicalRecord. Shared by every input format (JSON, CSV, text)."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any

from app.domain.canonical import (
    CanonicalRecord,
    ClinicalDocumentInfo,
    DiagnosticInfo,
    IssueCode,
    PatientInfo,
    Severity,
    SourceMetadata,
    ValidationIssue,
)
from app.domain.normalization import (
    Normalized,
    Outcome,
    normalize_date,
    normalize_gender,
    normalize_mrn,
    normalize_name,
)

PARSE_ERROR_KEY = "_parse_error"

ALIASES: dict[str, tuple[str, ...]] = {
    "mrn": ("mrn", "medical_record_number", "medical_record_no", "patient_mrn"),
    "name": ("name", "patient_name", "full_name", "fullname", "patient"),
    "dob": ("dob", "date_of_birth", "birth_date", "birthdate", "birthday"),
    "gender": ("gender", "sex"),
    "text": ("note", "notes", "document_text", "text", "clinical_note", "document", "narrative"),
    "doc_date": ("date", "document_date", "visit_date", "date_of_visit", "encounter_date",
                 "note_date"),
    "diagnosis": ("diagnosis", "diagnoses", "condition", "conditions"),
}
_LOOKUP = {alias: field for field, aliases in ALIASES.items() for alias in aliases}
_GIVEN = ("first_name", "given_name", "firstname")
_FAMILY = ("last_name", "family_name", "surname", "lastname")


def normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")


def field_for_key(key: str) -> str | None:
    """Canonical field name for a source key/label, or None if unknown."""
    return _LOOKUP.get(normalize_key(key))


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == []


def resolve_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Map heterogeneous source keys onto canonical field names (first non-blank value wins)."""
    out: dict[str, Any] = {}
    by_norm: dict[str, Any] = {}
    for key in sorted(raw, key=str):
        norm = normalize_key(str(key))
        if _blank(raw[key]):
            continue
        by_norm.setdefault(norm, raw[key])
        field = _LOOKUP.get(norm)
        if field and field not in out:
            out[field] = raw[key]
    if "name" not in out:
        given = next((by_norm[k] for k in _GIVEN if k in by_norm), None)
        family = next((by_norm[k] for k in _FAMILY if k in by_norm), None)
        parts = [str(p).strip() for p in (given, family) if p is not None]
        if parts:
            out["name"] = " ".join(parts)
    return out


def compute_source_hash(raw: Mapping[str, Any]) -> str:
    payload = json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def derive_record_id(source_hash: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ehr-record:{source_hash}"))


def _issue(code: IssueCode, field: str, severity: Severity, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, field=field, severity=severity, message=message)


def _issues_for(
    n: Normalized, field: str, label: str, *, required: bool, invalid_is_error: bool = True,
    missing_note: str = "",
) -> list[ValidationIssue]:
    if n.outcome == Outcome.MISSING:
        if required:
            return [_issue(IssueCode.MISSING_REQUIRED, field, Severity.ERROR, f"{label} is required but missing.")]
        return [_issue(IssueCode.MISSING_OPTIONAL, field, Severity.WARNING,
                       f"{label} is missing.{missing_note}")]
    if n.outcome == Outcome.INVALID:
        sev = Severity.ERROR if invalid_is_error else Severity.WARNING
        return [_issue(IssueCode.INVALID_VALUE, field, sev, f"{label} is invalid ({n.reason}).")]
    if n.outcome == Outcome.RECOVERED:
        return [_issue(IssueCode.RECOVERED_FORMAT, field, Severity.WARNING,
                       f"{label} was reformatted to canonical form.")]
    return []


def _diagnoses(raw: Any) -> list[DiagnosticInfo]:
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else re.split(r"[;|]", str(raw))
    out = [" ".join(str(i).split()) for i in items if not isinstance(i, (dict, list))]
    return [DiagnosticInfo(description=d) for d in out if d]


def build_canonical(raw: Mapping[str, Any], source: SourceMetadata, *, today: date) -> CanonicalRecord:
    """Normalize one raw record. Never raises on bad data; problems become ValidationIssues."""
    f = resolve_fields(raw)
    issues: list[ValidationIssue] = []

    mrn = normalize_mrn(f.get("mrn"))
    name = normalize_name(f.get("name"))
    dob = normalize_date(f.get("dob"), today=today)
    gender = normalize_gender(f.get("gender"))
    issues += _issues_for(mrn, "mrn", "MRN", required=True)
    issues += _issues_for(name, "name", "Patient name", required=False, invalid_is_error=False)
    issues += _issues_for(dob, "date_of_birth", "Date of birth", required=False,
                          missing_note=" Identity checks will rely on MRN only.")
    issues += _issues_for(gender, "gender", "Gender", required=False, invalid_is_error=False)

    text = f.get("text")
    document = None
    if isinstance(text, str) and text.strip():
        doc_date = normalize_date(f.get("doc_date"), today=today)
        issues += _issues_for(doc_date, "document_date", "Document date", required=False)
        document = ClinicalDocumentInfo(text=text.strip(), document_date=doc_date.value)
    elif "text" in f:
        issues.append(_issue(IssueCode.INVALID_VALUE, "document_text", Severity.ERROR,
                             "Document text has an unsupported type."))

    return CanonicalRecord(
        record_id=derive_record_id(source.source_hash),
        source=source,
        patient=PatientInfo(mrn=mrn.value, full_name=name.value, date_of_birth=dob.value,
                            gender=gender.value),
        document=document,
        diagnostics=_diagnoses(f.get("diagnosis")),
        issues=issues,
    )


def build_unparseable(source: SourceMetadata, reason: str) -> CanonicalRecord:
    """Canonical placeholder for a record whose structure could not be read (e.g. ragged CSV row)."""
    return CanonicalRecord(
        record_id=derive_record_id(source.source_hash),
        source=source,
        patient=PatientInfo(),
        issues=[_issue(IssueCode.INVALID_VALUE, "record", Severity.ERROR,
                       f"Record could not be parsed ({reason}).")],
    )
