"""Composition root for request-scoped dependencies (the only place wiring concrete classes)."""

from fastapi import Request

from app.application.ingestion.factory import IngestionStrategyFactory
from app.application.use_cases.ingest_patient_records import IngestPatientRecordsUseCase
from app.application.use_cases.resolve_patient_identity import ResolvePatientIdentityUseCase
from app.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork


def get_ingest_use_case(request: Request) -> IngestPatientRecordsUseCase:
    session_factory = request.app.state.session_factory
    return IngestPatientRecordsUseCase(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        strategies=IngestionStrategyFactory(),
        resolve_identity=ResolvePatientIdentityUseCase(),
    )
