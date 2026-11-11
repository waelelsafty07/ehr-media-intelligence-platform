"""Application factory. Wiring only; business logic lives in service modules."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, ingest
from app.api.domain_errors import register_domain_error_handlers
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware
from app.infrastructure.persistence.session import (
    create_db_engine,
    get_session_factory,
    init_schema,
)

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.state.settings = settings
    app.state.engine = create_db_engine(settings.database_url)
    init_schema(app.state.engine)
    app.state.session_factory = get_session_factory(app.state.engine)

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

    logger.info("app_started", extra={"environment": settings.environment})
    return app


app = create_app()
