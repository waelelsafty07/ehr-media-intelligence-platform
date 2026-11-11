"""FHIR-side domain concepts that are independent of any FHIR library.

Holds: deterministic IDs, processing state, a fingerprint for idempotent generation, and the
library-independent validation rules (value sets we populate, reference integrity, attachment
consistency). The FHIR library itself is only used in infrastructure/fhir.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from collections.abc import Iterator, Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.entities import Patient, StoredSourceRecord

MAPPER_VERSION = "fhir-r4b-mapper-1"  # change when mapping rules change: forces regeneration
MRN_SYSTEM = "urn:ehr-media-intelligence:mrn"
SOURCE_RECORD_SYSTEM = "urn:ehr-media-intelligence:source-record"
RESOURCE_ORDER = {"Patient": 0, "DocumentReference": 1, "DiagnosticReport": 2}


class FhirStatus(StrEnum):
    """FHIR processing state, independent of ingestion status. Absence of a row = never generated."""

    GENERATED = "generated"
    INVALID = "invalid"  # mapped, but validation failed; nothing served
    FAILED = "failed"  # unexpected error; retry by generating again


class FhirIssue(BaseModel):
    """Client-safe validation finding: location and rule only, never the offending value."""

    model_config = ConfigDict(frozen=True)
    code: str
    path: str
    message: str


class FhirResourceRecord(BaseModel):
    resource_type: str
    resource_id: str
    patient_id: str
    source_record_id: str | None = None  # None for the Patient resource
    position: int  # order of the entry inside the bundle
    content: dict[str, Any]


class PatientFhirState(BaseModel):
    patient_id: str
    status: FhirStatus
    fingerprint: str
    bundle_id: str | None = None
    bundle_timestamp: datetime | None = None
    resource_count: int = 0
    issues: list[FhirIssue] = Field(default_factory=list)
    failure_category: str | None = None
    updated_at: datetime


# ------------------------------------------------------------------ deterministic identifiers
def full_url(resource_id: str) -> str:
    return f"urn:uuid:{resource_id}"


def patient_resource_id(patient_id: str) -> str:
    return patient_id


def document_reference_id(record_id: str) -> str:
    return record_id


def diagnostic_report_id(record_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ehr-fhir-diagnostic-report:{record_id}"))


def bundle_id_for(patient_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ehr-fhir-bundle:{patient_id}"))


def compute_fingerprint(patient: Patient, records: list[StoredSourceRecord]) -> str:
    """Hash of everything that determines the generated FHIR content (idempotency key)."""
    payload = {
        "mapper": MAPPER_VERSION,
        "patient": [patient.mrn, patient.full_name,
                    patient.date_of_birth.isoformat() if patient.date_of_birth else None,
                    patient.gender.value],
        "records": sorted((r.record_id, r.content_hash or "") for r in records),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()




# ------------------------------------------------------------------ validation rules (pure)
GENDER_CODES = {"male", "female", "other", "unknown"}
DOCUMENT_REFERENCE_STATUS = {"current", "superseded", "entered-in-error"}
DIAGNOSTIC_REPORT_STATUS = {"registered", "partial", "preliminary", "final", "amended", "corrected",
                            "appended", "cancelled", "entered-in-error", "unknown"}
BUNDLE_TYPES = {"document", "message", "transaction", "transaction-response", "batch",
                "batch-response", "history", "searchset", "collection"}


def _issue(code: str, path: str, message: str) -> FhirIssue:
    return FhirIssue(code=code, path=path, message=message)


def _walk_references(node: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if key == "reference" and isinstance(value, str):
                yield child, value
            else:
                yield from _walk_references(value, child)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_references(value, f"{path}[{i}]")


def check_bundle(bundle: Mapping[str, Any]) -> list[FhirIssue]:
    """Library-independent checks: value sets, identity, reference integrity, attachments."""
    issues: list[FhirIssue] = []
    if bundle.get("type") not in BUNDLE_TYPES:
        issues.append(_issue("invalid_code_value", "type", "Bundle.type is not a valid code."))
    entries = bundle.get("entry") or []
    if not isinstance(entries, list) or not entries:
        return issues + [_issue("empty_bundle", "entry", "Bundle has no entries.")]

    full_urls: dict[str, int] = {}
    seen_ids: set[tuple[str, str]] = set()
    patient_urls: list[str] = []
    for i, entry in enumerate(entries):
        base = f"entry[{i}]"
        resource = entry.get("resource") if isinstance(entry, Mapping) else None
        if not isinstance(resource, Mapping) or "resourceType" not in resource:
            issues.append(_issue("missing_resource", base, "Entry has no resource."))
            continue
        rtype, rid, url = resource["resourceType"], resource.get("id"), entry.get("fullUrl")
        if not rid:
            issues.append(_issue("missing_id", f"{base}.resource.id", f"{rtype} has no id."))
        if (rtype, rid) in seen_ids:
            issues.append(_issue("duplicate_resource_id", f"{base}.resource.id", f"Duplicate {rtype} id."))
        seen_ids.add((rtype, rid))
        if not url:
            issues.append(_issue("missing_full_url", f"{base}.fullUrl", "Entry has no fullUrl."))
        else:
            if url in full_urls:
                issues.append(_issue("duplicate_full_url", f"{base}.fullUrl", "Duplicate fullUrl."))
            full_urls[url] = i
            if rid and url != full_url(rid):
                issues.append(_issue("full_url_id_mismatch", f"{base}.fullUrl", "fullUrl does not match resource id."))
        if rtype == "Patient":
            patient_urls.append(url or "")
            if resource.get("gender") not in (None, *GENDER_CODES):
                issues.append(_issue("invalid_code_value", f"{base}.resource.gender", "Patient.gender is not a valid code."))
        elif rtype == "DocumentReference":
            if resource.get("status") not in DOCUMENT_REFERENCE_STATUS:
                issues.append(_issue("invalid_code_value", f"{base}.resource.status", "DocumentReference.status is not a valid code."))
            issues += _check_attachments(resource, f"{base}.resource")
        elif rtype == "DiagnosticReport" and resource.get("status") not in DIAGNOSTIC_REPORT_STATUS:
            issues.append(_issue("invalid_code_value", f"{base}.resource.status", "DiagnosticReport.status is not a valid code."))

    if len(patient_urls) != 1:
        issues.append(_issue("patient_count", "entry", "Bundle must contain exactly one Patient."))
    for i, entry in enumerate(entries):
        resource = entry.get("resource") if isinstance(entry, Mapping) else None
        if not isinstance(resource, Mapping):
            continue
        for path, ref in _walk_references(resource, f"entry[{i}].resource"):
            if ref.startswith("#"):
                continue
            if ref not in full_urls:
                issues.append(_issue("dangling_reference", path, "Reference does not resolve to a bundle entry."))
        if resource.get("resourceType") in ("DocumentReference", "DiagnosticReport"):
            subject = (resource.get("subject") or {}).get("reference")
            if len(patient_urls) == 1 and subject != patient_urls[0]:
                issues.append(_issue("subject_not_bundle_patient", f"entry[{i}].resource.subject.reference",
                                     "Subject must be the bundle's Patient."))
    return issues


def _check_attachments(resource: Mapping[str, Any], base: str) -> list[FhirIssue]:
    out: list[FhirIssue] = []
    for j, content in enumerate(resource.get("content") or []):
        att = (content or {}).get("attachment") or {}
        if "data" not in att:
            continue
        path = f"{base}.content[{j}].attachment"
        try:
            raw = base64.b64decode(att["data"], validate=True)
        except (binascii.Error, ValueError):
            out.append(_issue("invalid_base64", f"{path}.data", "Attachment data is not valid base64."))
            continue
        if att.get("size") is not None and att["size"] != len(raw):
            out.append(_issue("attachment_size_mismatch", f"{path}.size", "Attachment size does not match data."))
        if att.get("hash") is not None and att["hash"] != base64.b64encode(hashlib.sha1(raw).digest()).decode():
            out.append(_issue("attachment_hash_mismatch", f"{path}.hash", "Attachment hash does not match data."))
    return out
