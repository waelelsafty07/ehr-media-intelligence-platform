"""GET /health - liveness plus a cheap database readiness check."""

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    database: str
    version: str


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db = "ok"
    except Exception:  # noqa: BLE001 - report degraded, never leak details
        db = "unavailable"
    return HealthResponse(
        status="ok" if db == "ok" else "degraded", database=db, version=request.app.version
    )
