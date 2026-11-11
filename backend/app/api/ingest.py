"""POST /ingest: thin handler; validation of the upload, then delegate to the use case."""

import re
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.api.dependencies import get_ingest_use_case
from app.api.schemas import IngestResponse
from app.application.dto import IngestCommand
from app.application.ingestion.factory import resolve_format
from app.application.use_cases.ingest_patient_records import IngestPatientRecordsUseCase
from app.domain.errors import FileTooLargeError

router = APIRouter(tags=["ingestion"])


def _safe_name(filename: str | None) -> str:
    base = PurePosixPath((filename or "upload").replace("\\", "/")).name
    return re.sub(r"[^\w.\- ]", "_", base)[:200] or "upload"


@router.post("/ingest", response_model=IngestResponse)
def ingest(
    request: Request,
    file: UploadFile = File(..., description="A .json, .csv or .txt file of synthetic records"),
    format: str | None = Form(None, description="json | csv | text (default: from file extension)"),
    use_case: IngestPatientRecordsUseCase = Depends(get_ingest_use_case),
) -> IngestResponse:
    limit = request.app.state.settings.max_upload_bytes
    source_format = resolve_format(file.filename, format)
    content = file.file.read(limit + 1)
    if len(content) > limit:
        raise FileTooLargeError(f"Upload exceeds the {limit}-byte limit.")
    result = use_case.execute(IngestCommand(content, _safe_name(file.filename), source_format))
    return IngestResponse.from_result(result)
