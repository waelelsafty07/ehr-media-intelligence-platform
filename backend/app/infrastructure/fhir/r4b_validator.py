"""Structural validation with the FHIR library (types, cardinality, formats, unknown fields).

NOT a complete FHIR validator: fhir.resources does not enforce value-set bindings, FHIRPath invariants
or reference resolution. Those we cover (for the fields we populate) in app.domain.fhir.check_bundle.
"""

from __future__ import annotations

from typing import Any

from fhir.resources.R4B.bundle import Bundle
from pydantic import ValidationError

from app.domain.fhir import FhirIssue


def issues_from_validation_error(exc: ValidationError, prefix: str = "") -> list[FhirIssue]:
    """Location + rule only. The offending input value is never included."""
    out = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        path = f"{prefix}.{loc}" if prefix and loc else (prefix or loc)
        out.append(FhirIssue(code=f"schema:{err['type']}", path=path, message="FHIR structure rule violated."))
    return out


class R4BSchemaValidator:
    def validate_bundle(self, bundle: dict[str, Any]) -> list[FhirIssue]:
        try:
            Bundle.model_validate(bundle)  # entry resources are validated by their resourceType
        except ValidationError as exc:
            return issues_from_validation_error(exc)
        return []
