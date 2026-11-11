"""Unit of work port: one transaction spanning several repositories."""

from __future__ import annotations

from typing import Protocol, Self

from app.domain.ports.repositories import (
    AuditLogRepository,
    PatientRepository,
    SourceRecordRepository,
)


class UnitOfWork(Protocol):
    """Leaving the context without commit() rolls back everything done inside it."""

    patients: PatientRepository
    source_records: SourceRecordRepository
    audit: AuditLogRepository

    def __enter__(self) -> Self: ...
    def __exit__(self, *exc_info: object) -> None: ...
    def commit(self) -> None: ...
