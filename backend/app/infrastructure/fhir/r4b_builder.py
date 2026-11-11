"""Canonical -> FHIR R4 mapping using fhir.resources.R4B. Deterministic; invents no clinical facts."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime
from typing import Any

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.documentreference import DocumentReference
from fhir.resources.R4B.patient import Patient as FhirPatient
from pydantic import ValidationError

from app.domain.entities import Patient, StoredSourceRecord
from app.domain.errors import FhirBuildError
from app.domain.fhir import (
    MRN_SYSTEM,
    RESOURCE_ORDER,
    SOURCE_RECORD_SYSTEM,
    FhirResourceRecord,
    diagnostic_report_id,
    document_reference_id,
    full_url,
    patient_resource_id,
)
from app.infrastructure.fhir.r4b_validator import issues_from_validation_error

DIAGNOSTIC_REPORT_CODE_TEXT = "Diagnoses documented in source clinical record"


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=True)


def _patient_ref(patient_id: str) -> dict[str, str]:
    return {"reference": full_url(patient_resource_id(patient_id)), "type": "Patient"}


class R4BBundleBuilder:
    def build_resources(
        self, patient: Patient, records: list[StoredSourceRecord], generated_at: datetime
    ) -> list[FhirResourceRecord]:
        drafts: list[tuple[str, str, str | None, dict[str, Any]]] = []  # (type, id, source_record_id, json)
        try:
            drafts.append(("Patient", patient_resource_id(patient.patient_id), None, self._patient(patient)))
            doc_refs, reports = [], []
            for rec in sorted(records, key=lambda r: (self._doc_date(r) or "", r.record_id)):
                canonical = rec.canonical
                if canonical is None:
                    continue
                report_id = diagnostic_report_id(rec.record_id) if canonical.diagnostics else None
                if canonical.document:
                    doc_refs.append(("DocumentReference", document_reference_id(rec.record_id), rec.record_id,
                                     self._document_reference(patient, rec, report_id, generated_at)))
                if report_id:
                    reports.append(("DiagnosticReport", report_id, rec.record_id,
                                    self._diagnostic_report(patient, rec)))
            drafts += doc_refs + reports
        except ValidationError as exc:
            raise FhirBuildError(issues_from_validation_error(exc, "mapping")) from exc
        return [FhirResourceRecord(resource_type=t, resource_id=i, patient_id=patient.patient_id,
                                   source_record_id=s, position=pos, content=c)
                for pos, (t, i, s, c) in enumerate(drafts)]

    def assemble_bundle(self, bundle_id: str, timestamp: datetime, resources: list[FhirResourceRecord]) -> dict[str, Any]:
        ordered = sorted(resources, key=lambda r: (RESOURCE_ORDER.get(r.resource_type, 99), r.position))
        try:
            bundle = Bundle(
                id=bundle_id, type="collection", timestamp=timestamp,
                entry=[{"fullUrl": full_url(r.resource_id), "resource": r.content} for r in ordered])
        except ValidationError as exc:
            raise FhirBuildError(issues_from_validation_error(exc, "bundle")) from exc
        return _dump(bundle)

    # ---------------------------------------------------------------- resource mappers
    @staticmethod
    def _doc_date(rec: StoredSourceRecord) -> str | None:
        d = rec.canonical.document.document_date if rec.canonical and rec.canonical.document else None
        return d.isoformat() if d else None

    @staticmethod
    def _patient(p: Patient) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": patient_resource_id(p.patient_id),
            "identifier": [{
                "use": "usual",
                "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "MR"}]},
                "system": MRN_SYSTEM, "value": p.mrn}],
            "gender": p.gender.value,  # canonical values are FHIR administrative-gender codes
        }
        if p.full_name:
            data["name"] = [{"text": p.full_name}]  # no given/family split: that would be a guess
        if p.date_of_birth:
            data["birthDate"] = p.date_of_birth.isoformat()
        return _dump(FhirPatient(**data))

    @staticmethod
    def _document_reference(p: Patient, rec: StoredSourceRecord, report_id: str | None,
                            generated_at: datetime) -> dict[str, Any]:
        doc = rec.canonical.document  # type: ignore[union-attr]
        raw = doc.text.encode("utf-8")
        context: dict[str, Any] = {}
        if doc.document_date:
            context["period"] = {"start": doc.document_date.isoformat()}  # clinical date, not creation time
        if report_id:
            context["related"] = [{"reference": full_url(report_id), "type": "DiagnosticReport"}]
        data: dict[str, Any] = {
            "id": document_reference_id(rec.record_id),
            "identifier": [{"system": SOURCE_RECORD_SYSTEM, "value": rec.record_id}],
            "status": "current",
            "subject": _patient_ref(p.patient_id),
            "date": rec.received_at,  # R4: when this DocumentReference was created (ingestion time)
            "content": [{"attachment": {
                "contentType": "text/plain", "data": base64.b64encode(raw).decode(), "size": len(raw),
                "hash": base64.b64encode(hashlib.sha1(raw).digest()).decode()}}],
        }
        if context:
            data["context"] = context
        return _dump(DocumentReference(**data))

    @staticmethod
    def _diagnostic_report(p: Patient, rec: StoredSourceRecord) -> dict[str, Any]:
        canonical = rec.canonical
        diagnoses = [d.description for d in canonical.diagnostics]  # type: ignore[union-attr]
        data: dict[str, Any] = {
            "id": diagnostic_report_id(rec.record_id),
            "identifier": [{"system": SOURCE_RECORD_SYSTEM, "value": rec.record_id}],
            "status": "unknown",  # the source does not state a report status
            "code": {"text": DIAGNOSTIC_REPORT_CODE_TEXT},  # uncoded: no code is invented
            "subject": _patient_ref(p.patient_id),
            "conclusion": "; ".join(diagnoses),
            "conclusionCode": [{"text": d} for d in diagnoses],
        }
        if canonical.document and canonical.document.document_date:  # type: ignore[union-attr]
            data["effectiveDateTime"] = canonical.document.document_date.isoformat()  # type: ignore[union-attr]
        return _dump(DiagnosticReport(**data))
