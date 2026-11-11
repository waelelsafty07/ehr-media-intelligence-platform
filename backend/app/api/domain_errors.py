"""Maps domain errors to HTTP responses (status codes are a presentation concern)."""

from fastapi import FastAPI, Request

from app.core.errors import build_error_response
from app.domain.errors import (
    EmbeddingUnavailableError,
    FhirGenerationFailedError,
    FhirNotAvailableError,
    FhirValidationFailedError,
    FileTooLargeError,
    IngestionParseError,
    LLMNotConfiguredError,
    NoSourceRecordsError,
    PatientNotFoundError,
    SummaryGenerationFailedError,
    SummaryNotFoundError,
    UnsupportedFormatError,
    VectorStoreError,
)

_MAPPING = {
    IngestionParseError: (422, "ingestion_parse_error"),
    UnsupportedFormatError: (415, "unsupported_format"),
    FileTooLargeError: (413, "file_too_large"),
    PatientNotFoundError: (404, "patient_not_found"),
    FhirNotAvailableError: (409, "fhir_not_available"),
    FhirGenerationFailedError: (500, "fhir_generation_failed"),
    NoSourceRecordsError: (409, "no_source_records"),
    SummaryNotFoundError: (404, "summary_not_found"),
    LLMNotConfiguredError: (503, "ai_provider_not_configured"),
    EmbeddingUnavailableError: (503, "embedding_provider_unavailable"),
    VectorStoreError: (503, "search_index_unavailable"),
}


def register_domain_error_handlers(app: FastAPI) -> None:
    for exc_type, (status, code) in _MAPPING.items():

        def handler(_: Request, exc: Exception, status=status, code=code):
            return build_error_response(status, code, str(exc))

        app.add_exception_handler(exc_type, handler)


    def validation_failed(_: Request, exc: Exception):
        issues = [{"path": i.path, "code": i.code} for i in exc.issues]  # type: ignore[attr-defined]
        return build_error_response(422, "fhir_validation_failed", str(exc), issues)

    app.add_exception_handler(FhirValidationFailedError, validation_failed)

    def summary_failed(_: Request, exc: Exception):
        return build_error_response(502, "summary_generation_failed", str(exc),
                                    [{"reason": exc.reason}])  # type: ignore[attr-defined]

    app.add_exception_handler(SummaryGenerationFailedError, summary_failed)
