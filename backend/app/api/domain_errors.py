"""Maps domain errors to HTTP responses (status codes are a presentation concern)."""

from fastapi import FastAPI, Request

from app.core.errors import build_error_response
from app.domain.errors import FileTooLargeError, IngestionParseError, UnsupportedFormatError

_MAPPING = {
    IngestionParseError: (422, "ingestion_parse_error"),
    UnsupportedFormatError: (415, "unsupported_format"),
    FileTooLargeError: (413, "file_too_large"),
}


def register_domain_error_handlers(app: FastAPI) -> None:
    for exc_type, (status, code) in _MAPPING.items():

        def handler(_: Request, exc: Exception, status=status, code=code):
            return build_error_response(status, code, str(exc))

        app.add_exception_handler(exc_type, handler)
