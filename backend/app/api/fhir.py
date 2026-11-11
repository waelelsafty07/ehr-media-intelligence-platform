"""FHIR endpoints. Thin: delegate to use cases; map results to HTTP."""

from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse

from app.api.dependencies import (
    get_fhir_bundle_use_case,
    get_fhir_status_use_case,
    get_generate_fhir_use_case,
)
from app.api.schemas import FhirStatusResponse
from app.application.use_cases.generate_fhir_bundle import GenerateFHIRBundleUseCase
from app.application.use_cases.get_patient_fhir import (
    GetPatientFHIRBundleUseCase,
    GetPatientFHIRStatusUseCase,
)
from app.domain.errors import FhirGenerationFailedError, FhirValidationFailedError
from app.domain.fhir import FhirStatus

router = APIRouter(tags=["fhir"])
PatientId = Path(..., min_length=1, max_length=64, description="Patient ID returned by /ingest")


@router.post("/patients/{patient_id}/fhir", response_model=FhirStatusResponse)
def generate_fhir(patient_id: str = PatientId,
                  use_case: GenerateFHIRBundleUseCase = Depends(get_generate_fhir_use_case)) -> FhirStatusResponse:
    """Generate (or confirm up to date) the patient's FHIR R4 resources. Idempotent."""
    result = use_case.execute(patient_id)
    if result.status == FhirStatus.INVALID:
        raise FhirValidationFailedError(result.issues)
    if result.status == FhirStatus.FAILED:
        raise FhirGenerationFailedError()
    return FhirStatusResponse.from_result(patient_id, result)


@router.get("/patients/{patient_id}/fhir/status", response_model=FhirStatusResponse)
def fhir_status(patient_id: str = PatientId,
                use_case: GetPatientFHIRStatusUseCase = Depends(get_fhir_status_use_case)) -> FhirStatusResponse:
    return FhirStatusResponse.from_result(patient_id, use_case.execute(patient_id))


@router.get("/patients/{patient_id}/fhir", response_class=JSONResponse,
            responses={200: {"content": {"application/fhir+json": {}}}})
def get_fhir_bundle(patient_id: str = PatientId,
                    use_case: GetPatientFHIRBundleUseCase = Depends(get_fhir_bundle_use_case)) -> JSONResponse:
    """The patient's FHIR R4 collection Bundle (only when status is `generated`)."""
    return JSONResponse(content=use_case.execute(patient_id), media_type="application/fhir+json")
