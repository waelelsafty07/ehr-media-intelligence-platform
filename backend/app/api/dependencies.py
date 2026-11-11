"""Composition root for request-scoped dependencies (the only place wiring concrete classes)."""

from fastapi import Request

from app.application.ingestion.factory import IngestionStrategyFactory
from app.application.use_cases.generate_clinical_summary import (
    GenerateClinicalSummaryUseCase,
    SummaryConfig,
)
from app.application.use_cases.generate_fhir_bundle import GenerateFHIRBundleUseCase
from app.application.use_cases.get_patient_fhir import (
    GetPatientFHIRBundleUseCase,
    GetPatientFHIRStatusUseCase,
)
from app.application.use_cases.get_patient_summary import GetPatientSummaryUseCase
from app.application.use_cases.index_clinical_records import IndexClinicalRecordsUseCase
from app.application.use_cases.ingest_patient_records import IngestPatientRecordsUseCase
from app.application.use_cases.resolve_patient_identity import ResolvePatientIdentityUseCase
from app.application.use_cases.search_clinical_records import (
    GetSearchStatusUseCase,
    SearchClinicalRecordsUseCase,
)
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


def _summary_config(request: Request) -> SummaryConfig:
    st = request.app.state.settings
    return SummaryConfig(
        max_attempts=st.summary_max_attempts, retry_base_seconds=st.summary_retry_base_seconds,
        max_records=st.summary_max_context_records, max_chars_per_record=st.summary_max_chars_per_record,
        max_output_tokens=st.summary_max_output_tokens, temperature=st.openai_temperature)


def get_generate_summary_use_case(request: Request) -> GenerateClinicalSummaryUseCase:
    return GenerateClinicalSummaryUseCase(_uow_factory(request), request.app.state.llm_provider,
                                          _summary_config(request))


def get_summary_use_case(request: Request) -> GetPatientSummaryUseCase:
    return GetPatientSummaryUseCase(_uow_factory(request), request.app.state.llm_provider.model_identifier,
                                    _summary_config(request))


def get_index_use_case(request: Request) -> IndexClinicalRecordsUseCase:
    st = request.app.state
    return IndexClinicalRecordsUseCase(_uow_factory(request), st.embedding_provider, st.vector_store,
                                       get_summary_use_case(request))


def get_search_use_case(request: Request) -> SearchClinicalRecordsUseCase:
    st = request.app.state
    return SearchClinicalRecordsUseCase(_uow_factory(request), st.embedding_provider, st.vector_store,
                                        get_summary_use_case(request))


def get_search_status_use_case(request: Request) -> GetSearchStatusUseCase:
    st = request.app.state
    return GetSearchStatusUseCase(st.embedding_provider, st.vector_store)
