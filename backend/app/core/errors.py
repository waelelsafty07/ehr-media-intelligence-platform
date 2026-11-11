"""Consistent error envelope. Internal exception details are never sent to clients."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

logger = logging.getLogger(__name__)


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    details: list[dict] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


def build_error_response(status: int, code: str, message: str, details: list[dict] | None = None):
    body = ErrorResponse(
        error=ErrorBody(
            code=code, message=message, request_id=request_id_var.get(), details=details
        )
    )
    return JSONResponse(status_code=status, content=body.model_dump(exclude_none=True))


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        return build_error_response(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        # Report field location and message only; never echo submitted values.
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return build_error_response(422, "validation_error", "Request validation failed.", details)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        logger.error("unhandled_exception", exc_info=exc)
        return build_error_response(500, "internal_error", "An internal error occurred.")
