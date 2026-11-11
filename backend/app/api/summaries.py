"""Clinical summary endpoints. Thin: delegate to use cases."""

from fastapi import APIRouter, Depends, Path

from app.api.dependencies import get_generate_summary_use_case, get_summary_use_case
from app.api.schemas import SummaryResponse
from app.application.use_cases.generate_clinical_summary import GenerateClinicalSummaryUseCase
from app.application.use_cases.get_patient_summary import GetPatientSummaryUseCase

router = APIRouter(tags=["summaries"])
PatientId = Path(..., min_length=1, max_length=64, description="Patient ID returned by /ingest")


@router.post("/patients/{patient_id}/summary", response_model=SummaryResponse)
def generate_summary(patient_id: str = PatientId,
                     use_case: GenerateClinicalSummaryUseCase = Depends(get_generate_summary_use_case)
                     ) -> SummaryResponse:
    """Generate (or return the cached) grounded clinical summary. Requires a configured LLM provider."""
    result = use_case.execute(patient_id)
    return SummaryResponse(summary=result.summary, summary_id=result.summary_id, cache_hit=result.cache_hit)


@router.get("/patients/{patient_id}/summary", response_model=SummaryResponse)
def get_summary(patient_id: str = PatientId,
                use_case: GetPatientSummaryUseCase = Depends(get_summary_use_case)) -> SummaryResponse:
    """The latest generated summary, flagged stale if records, prompt version or model changed since."""
    view = use_case.execute(patient_id)
    return SummaryResponse(summary=view.summary, summary_id=view.summary_id, is_stale=view.is_stale,
                           stale_reasons=view.stale_reasons)
