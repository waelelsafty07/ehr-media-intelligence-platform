"""Composition root for request-scoped dependencies (the only place wiring concrete classes)."""

from fastapi import Request

from app.application.ingestion.factory import IngestionStrategyFactory
from app.application.use_cases.generate_fhir_bundle import GenerateFHIRBundleUseCase
from app.application.use_cases.get_patient_fhir import (
    GetPatientFHIRBundleUseCase,
    GetPatientFHIRStatusUseCase,
)
from app.application.use_cases.ingest_patient_records import IngestPatientRecordsUseCase
from app.application.use_cases.resolve_patient_identity import ResolvePatientIdentityUseCase
from app.infrastructure.fhir.r4b_builder import R4BBundleBuilder
from app.infrastructure.fhir.r4b_validator import R4BSchemaValidator
from app.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork


def get_ingest_use_case(request: Request) -> IngestPatientRecordsUseCase:
    session_factory = request.app.state.session_factory
    return IngestPatientRecordsUseCase(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        strategies=IngestionStrategyFactory(),
        resolve_identity=ResolvePatientIdentityUseCase(),
    )


def _uow_factory(request: Request):
    session_factory = request.app.state.session_factory
    return lambda: SqlAlchemyUnitOfWork(session_factory)


def get_generate_fhir_use_case(request: Request) -> GenerateFHIRBundleUseCase:
    return GenerateFHIRBundleUseCase(_uow_factory(request), R4BBundleBuilder(), R4BSchemaValidator())


def get_fhir_bundle_use_case(request: Request) -> GetPatientFHIRBundleUseCase:
    return GetPatientFHIRBundleUseCase(_uow_factory(request), R4BBundleBuilder())


def get_fhir_status_use_case(request: Request) -> GetPatientFHIRStatusUseCase:
    return GetPatientFHIRStatusUseCase(_uow_factory(request))
