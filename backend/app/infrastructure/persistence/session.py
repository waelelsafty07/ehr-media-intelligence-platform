"""SQLAlchemy engine/session factory. SQLite is the source of truth."""

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def create_db_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, connect_args={"check_same_thread": False})

    if url.get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _):  # SQLite ignores FKs unless enabled
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
    finally:
        session.close()


def init_schema(engine: Engine) -> None:
    """Create tables if missing. (No migration tool yet: see docs/DATABASE notes in ARCHITECTURE.)"""
    from app.infrastructure.persistence.tables import Base

    Base.metadata.create_all(engine)
