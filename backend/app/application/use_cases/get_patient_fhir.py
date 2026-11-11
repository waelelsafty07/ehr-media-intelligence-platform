"""Read side: retrieve a patient's FHIR bundle or FHIR processing status."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from app.application.dto import FhirGenerationResult
from app.domain.errors import FhirNotAvailableError, PatientNotFoundError
from app.domain.fhir import FhirStatus
from app.domain.ports.fhir import FhirBundleBuilder
from app.domain.ports.unit_of_work import UnitOfWork


class GetPatientFHIRBundleUseCase:
    """Returns the stored Bundle only when state is GENERATED: stale or invalid data is never served."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork], builder: FhirBundleBuilder) -> None:
        self._uow_factory = uow_factory
        self._builder = builder

    def execute(self, patient_id: str) -> dict[str, Any]:
        with self._uow_factory() as uow:
            if uow.patients.get(patient_id) is None:
                raise PatientNotFoundError("Patient not found.")
            state = uow.fhir.get_state(patient_id)
            if state is None or state.status != FhirStatus.GENERATED or not (state.bundle_id and state.bundle_timestamp):
                raise FhirNotAvailableError(state.status.value if state else None)
            resources = uow.fhir.list_resources(patient_id)
        return self._builder.assemble_bundle(state.bundle_id, state.bundle_timestamp, resources)


class GetPatientFHIRStatusUseCase:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def execute(self, patient_id: str) -> FhirGenerationResult | None:
        """None = FHIR has never been generated for this (existing) patient."""
        with self._uow_factory() as uow:
            if uow.patients.get(patient_id) is None:
                raise PatientNotFoundError("Patient not found.")
            state = uow.fhir.get_state(patient_id)
            if state is None:
                return None
            counts = dict(Counter(r.resource_type for r in uow.fhir.list_resources(patient_id)))
        return FhirGenerationResult(patient_id=patient_id, status=state.status, bundle_id=state.bundle_id,
                                    resource_counts=counts if state.status == FhirStatus.GENERATED else {},
                                    issues=list(state.issues), updated_at=state.updated_at)
