"""Shared builders for FHIR tests (synthetic data only)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.domain.canonical import (
    Gender,
    ProcessingInfo,
    ProcessingStatus,
    SourceFormat,
    SourceMetadata,
)
from app.domain.canonicalization import build_canonical, compute_source_hash, derive_record_id
from app.domain.entities import Patient, StoredSourceRecord
from app.domain.normalization import name_key

NOW = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
PATIENT_ID = "11111111-1111-4111-8111-111111111111"


def make_patient(patient_id=PATIENT_ID, mrn="000101", name="Alex Test", dob=date(1980, 3, 14),
                 gender=Gender.MALE) -> Patient:
    return Patient(patient_id=patient_id, mrn=mrn, full_name=name, name_key=name_key(name) if name else None,
                   date_of_birth=dob, gender=gender, created_at=NOW)


def make_record(raw: dict, patient_id=PATIENT_ID, index=0) -> StoredSourceRecord:
    h = compute_source_hash(raw)
    source = SourceMetadata(source_id=f"f#{index}", source_name="f", source_format=SourceFormat.JSON,
                            record_index=index, source_hash=h, received_at=NOW)
    canonical = build_canonical(raw, source, today=date(2025, 1, 1))
    return StoredSourceRecord(
        record_id=derive_record_id(h), source_id=source.source_id, source_name="f", source_format=SourceFormat.JSON,
        record_index=index, source_hash=h, content_hash=canonical.content_hash(), raw_payload=raw,
        canonical=canonical, processing=ProcessingInfo(status=ProcessingStatus.COMPLETED, updated_at=NOW),
        patient_id=patient_id, received_at=NOW)


NOTE = {"mrn": "000101", "note": "Follow-up for chest tightness.", "date": "2024-02-01"}
NOTE_WITH_DX = {**NOTE, "diagnosis": "Asthma; Hypertension"}
DX_ONLY = {"mrn": "000101", "diagnosis": "Hypertension"}
