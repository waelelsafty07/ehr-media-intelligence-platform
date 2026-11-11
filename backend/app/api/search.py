"""Search and indexing endpoints. Thin: delegate to use cases."""

from fastapi import APIRouter, Depends, Path

from app.api.dependencies import get_index_use_case, get_search_status_use_case, get_search_use_case
from app.api.search_schemas import (
    IndexResponse,
    ReindexResponse,
    SearchResponse,
    SearchStatusResponse,
)
from app.application.use_cases.index_clinical_records import IndexClinicalRecordsUseCase
from app.application.use_cases.search_clinical_records import (
    GetSearchStatusUseCase,
    SearchClinicalRecordsUseCase,
)
from app.domain.search import SearchQuery

router = APIRouter(tags=["search"])
PatientId = Path(..., min_length=1, max_length=64, description="Patient ID returned by /ingest")


@router.post("/search", response_model=SearchResponse)
def search(body: SearchQuery, use_case: SearchClinicalRecordsUseCase = Depends(get_search_use_case)) -> SearchResponse:
    """Top results (max 5) by embedding similarity over indexed FHIR DocumentReference text and AI summaries.
    Patient/type/date filters are applied before ranking."""
    return SearchResponse.from_outcome(body.query, use_case.execute(body))


@router.get("/search/status", response_model=SearchStatusResponse)
def search_status(use_case: GetSearchStatusUseCase = Depends(get_search_status_use_case)) -> SearchStatusResponse:
    return SearchStatusResponse.from_status(use_case.execute())


@router.post("/patients/{patient_id}/index", response_model=IndexResponse)
def index_patient(patient_id: str = PatientId,
                  use_case: IndexClinicalRecordsUseCase = Depends(get_index_use_case)) -> IndexResponse:
    """Idempotently reconcile this patient's search index entries with the stored FHIR resources and summary."""
    return IndexResponse.from_result(use_case.execute(patient_id))


@router.post("/search/reindex", response_model=ReindexResponse)
def reindex_all(use_case: IndexClinicalRecordsUseCase = Depends(get_index_use_case)) -> ReindexResponse:
    """Idempotently reconcile the index for every patient (also rebuilds it after the index table is cleared)."""
    results = [IndexResponse.from_result(r) for r in use_case.execute_all()]
    return ReindexResponse(patients=len(results), indexed=sum(r.indexed for r in results),
                           unchanged=sum(r.unchanged for r in results), removed=sum(r.removed for r in results),
                           results=results)
