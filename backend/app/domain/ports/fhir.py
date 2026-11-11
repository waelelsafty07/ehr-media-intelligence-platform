"""Ports for the FHIR library integration (adapter lives in infrastructure/fhir)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from app.domain.entities import Patient, StoredSourceRecord
from app.domain.fhir import FhirIssue, FhirResourceRecord


class FhirBundleBuilder(Protocol):
    def build_resources(
        self, patient: Patient, records: list[StoredSourceRecord], generated_at: datetime
    ) -> list[FhirResourceRecord]:
        """Deterministic canonical -> FHIR mapping. Raises FhirBuildError(issues) if a resource is invalid."""
        ...

    def assemble_bundle(
        self, bundle_id: str, timestamp: datetime, resources: list[FhirResourceRecord]
    ) -> dict[str, Any]:
        """FHIR JSON of a collection Bundle (entries ordered by `position`)."""
        ...


class FhirSchemaValidator(Protocol):
    def validate_bundle(self, bundle: dict[str, Any]) -> list[FhirIssue]:
        """Structural validation by the FHIR library (types, cardinality, formats). Empty list = valid."""
        ...
