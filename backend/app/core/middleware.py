"""Request-ID middleware: correlates every log line and response with one ID."""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.logging import request_id_var

logger = logging.getLogger("app.access")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = uuid.uuid4().hex
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "request_completed",
                extra={"method": request.method, "path": request.url.path, "ms": elapsed_ms},
            )
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response
