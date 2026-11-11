"""Patient display lookup. Thin: delegates to a use case.

Local synthetic-data demo only. This is a display convenience, NOT an authorization mechanism."""

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel

from app.api.dependencies import get_patient_display_use_case
from app.application.use_cases.get_patient_display import GetPatientDisplayUseCase

router = APIRouter(tags=["patients"])
PatientId = Path(..., min_length=1, max_length=64, description="Patient ID returned by /ingest")


class PatientDisplayResponse(BaseModel):
    """Only what a UI needs to label a patient. No date of birth, gender or clinical content."""

    patient_id: str
    patient_name: str | None
    mrn: str


@router.get("/patients/{patient_id}/display", response_model=PatientDisplayResponse)
def get_patient_display(patient_id: str = PatientId,
                        use_case: GetPatientDisplayUseCase = Depends(get_patient_display_use_case)
                        ) -> PatientDisplayResponse:
    """Name and MRN for one patient, fetched separately so /search results stay free of direct identifiers."""
    d = use_case.execute(patient_id)
    return PatientDisplayResponse(patient_id=d.patient_id, patient_name=d.patient_name, mrn=d.mrn)
