"""Read side: the minimum a UI needs to label a patient (name + MRN). Deliberately excludes DOB, gender and notes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.domain.errors import PatientNotFoundError
from app.domain.ports.unit_of_work import UnitOfWork


@dataclass(frozen=True)
class PatientDisplay:
    patient_id: str
    patient_name: str | None
    mrn: str


class GetPatientDisplayUseCase:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def execute(self, patient_id: str) -> PatientDisplay:
        with self._uow_factory() as uow:
            patient = uow.patients.get(patient_id)
            if patient is None:
                raise PatientNotFoundError("Patient not found.")
            return PatientDisplay(patient_id=patient.patient_id, patient_name=patient.full_name, mrn=patient.mrn)
