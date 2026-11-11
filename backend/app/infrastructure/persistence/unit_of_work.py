"""SQLAlchemy unit of work: one session/transaction; rollback unless commit() was called."""

from __future__ import annotations

from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.persistence.repositories import (
    SqlAlchemyAuditLogRepository,
    SqlAlchemyPatientRepository,
    SqlAlchemySourceRecordRepository,
)


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    def __enter__(self) -> Self:
        self._session = self._factory()
        self.patients = SqlAlchemyPatientRepository(self._session)
        self.source_records = SqlAlchemySourceRecordRepository(self._session)
        self.audit = SqlAlchemyAuditLogRepository(self._session)
        return self

    def __exit__(self, *exc_info: object) -> None:
        try:
            self._session.rollback()  # no-op after commit; undoes everything otherwise
        finally:
            self._session.close()

    def commit(self) -> None:
        self._session.commit()
