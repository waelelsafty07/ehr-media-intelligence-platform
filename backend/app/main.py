"""Application factory. Wiring only; business logic lives in service modules."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import fhir, health, ingest, patients, search, summaries
from app.api.domain_errors import register_domain_error_handlers
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware
from app.infrastructure.llm.provider import build_llm_provider
from app.infrastructure.persistence.session import (
    create_db_engine,
    get_session_factory,
    init_schema,
)
from app.infrastructure.search.provider import build_embedding_provider
from app.infrastructure.search.sqlite_vector_store import SqliteVectorStore

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=("LOCAL ASSESSMENT/DEMO SYSTEM. Synthetic data only: do not use with real patient data. "
                     "There is NO authentication or authorization: anyone who can reach this API can read all data."),
    )
    app.state.settings = settings
    app.state.engine = create_db_engine(settings.database_url)
    init_schema(app.state.engine)
    app.state.session_factory = get_session_factory(app.state.engine)
    app.state.embedding_provider = build_embedding_provider(settings)  # model loads lazily on first use
    app.state.vector_store = SqliteVectorStore(app.state.session_factory)
    if not app.state.embedding_provider.is_available():
        logger.warning("embedding_provider_unavailable", extra={"provider": settings.embedding_provider})
    app.state.llm_provider = build_llm_provider(settings)  # UnconfiguredLLMProvider when no API key

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    register_domain_error_handlers(app)
    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(fhir.router)
    app.include_router(summaries.router)
    app.include_router(search.router)
    app.include_router(patients.router)

    logger.info("app_started", extra={"environment": settings.environment})
    return app


app = create_app()
