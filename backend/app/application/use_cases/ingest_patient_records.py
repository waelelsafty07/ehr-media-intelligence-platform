"""IngestPatientRecordsUseCase: orchestrates parse -> store -> normalize -> dedupe -> identity.

Transaction design (per record):
  A. store raw record as RECEIVED (+audit) and commit  -> the record can never be silently lost
  B. normalize, dedupe, resolve identity, write patient/status/audit in ONE transaction
  C. if B raises: roll back B entirely, then mark FAILED (+audit) in a fresh transaction
RECEIVED and FAILED records are retried when the same content is submitted again.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.application.dto import IngestCommand, IngestResult, RecordResult
from app.application.ingestion.base import ParsedRecord
from app.application.ingestion.factory import IngestionStrategyFactory
from app.application.use_cases.resolve_patient_identity import ResolvePatientIdentityUseCase
from app.domain.canonical import (
    RETRYABLE_STATUSES,
    IssueCode,
    ProcessingInfo,
    ProcessingStatus,
    Severity,
    SourceMetadata,
    ValidationIssue,
)
from app.domain.canonicalization import (
    PARSE_ERROR_KEY,
    build_canonical,
    build_unparseable,
    compute_source_hash,
    derive_record_id,
)
from app.domain.entities import AuditEvent, AuditEventType, Patient, StoredSourceRecord
from app.domain.errors import IngestionParseError
from app.domain.identity import IdentityDecision, IdentityOutcome, candidate_name_key
from app.domain.ports.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


class IngestPatientRecordsUseCase:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        strategies: IngestionStrategyFactory,
        resolve_identity: ResolvePatientIdentityUseCase,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._uow_factory = uow_factory
        self._strategies = strategies
        self._resolve_identity = resolve_identity
        self._clock = clock
        self._new_id = new_id

    # ------------------------------------------------------------------ public
    def execute(self, cmd: IngestCommand) -> IngestResult:
        logger.info("ingest_started", extra={"source_name": cmd.source_name, "format": cmd.source_format.value})
        strategy = self._strategies.for_format(cmd.source_format)
        try:
            parsed = strategy.parse(cmd.content)
        except IngestionParseError:
            self._audit_file_rejected(cmd)
            raise
        results = [self._ingest_one(cmd, p) for p in parsed]
        result = IngestResult(cmd.source_name, cmd.source_format, results)
        logger.info("ingest_completed", extra={"source_name": cmd.source_name, "total": len(results),
                                               "counts": result.counts})
        return result

    # ------------------------------------------------------------------ per record
    def _ingest_one(self, cmd: IngestCommand, parsed: ParsedRecord) -> RecordResult:
        source_id = f"{cmd.source_name}#{parsed.index}"
        raw = ({PARSE_ERROR_KEY: parsed.parse_error, "_source_id": source_id}
               if parsed.parse_error else parsed.fields)
        source_hash = compute_source_hash(raw)
        record_id = derive_record_id(source_hash)

        # A. durable receipt (or skip / retry decision)
        with self._uow_factory() as uow:
            existing = uow.source_records.get_by_source_hash(source_hash)
            now = self._clock()
            if existing and existing.processing.status not in RETRYABLE_STATUSES:
                self._audit(uow, AuditEventType.REPROCESS_SKIPPED, cmd.source_name, record_id,
                            details={"status": existing.processing.status.value, "source_id": source_id})
                uow.commit()
                return self._result(existing, already_ingested=True)
            if existing:
                existing.processing = ProcessingInfo(status=ProcessingStatus.RECEIVED,
                                                     attempts=existing.processing.attempts + 1, updated_at=now)
                uow.source_records.update(existing)
                self._audit(uow, AuditEventType.RECORD_RETRY_STARTED, cmd.source_name, record_id,
                            details={"attempt": existing.processing.attempts})
            else:
                uow.source_records.add(StoredSourceRecord(
                    record_id=record_id, source_id=source_id, source_name=cmd.source_name,
                    source_format=cmd.source_format, record_index=parsed.index, source_hash=source_hash,
                    raw_payload=raw, processing=ProcessingInfo(status=ProcessingStatus.RECEIVED, updated_at=now),
                    received_at=now))
                self._audit(uow, AuditEventType.RECORD_RECEIVED, cmd.source_name, record_id,
                            details={"source_id": source_id, "format": cmd.source_format.value})
            uow.commit()

        # B. atomic processing; C. recorded failure
        try:
            with self._uow_factory() as uow:
                stored, rationale = self._process(uow, record_id)
                uow.commit()
            return self._result(stored, rationale=rationale)
        except Exception as exc:  # noqa: BLE001 - any failure must end in a recorded FAILED state
            logger.error("record_processing_failed", extra={"record_id": record_id,
                                                           "failure_category": type(exc).__name__})
            return self._record_failure(cmd, record_id, exc)

    def _process(self, uow: UnitOfWork, record_id: str) -> tuple[StoredSourceRecord, str | None]:
        stored = uow.source_records.get(record_id)
        assert stored is not None
        now = self._clock()
        source = SourceMetadata(source_id=stored.source_id, source_name=stored.source_name,
                                source_format=stored.source_format, record_index=stored.record_index,
                                source_hash=stored.source_hash, received_at=stored.received_at)
        if PARSE_ERROR_KEY in stored.raw_payload:
            canonical = build_unparseable(source, str(stored.raw_payload[PARSE_ERROR_KEY]))
        else:
            canonical = build_canonical(stored.raw_payload, source, today=now.date())
        stored.canonical = canonical
        name, rid = stored.source_name, stored.record_id

        warnings = [i for i in canonical.issues if i.severity == Severity.WARNING]
        self._audit(uow, AuditEventType.RECORD_NORMALIZED, name, rid, outcome="warning" if warnings else "ok",
                    details={"issues": _issue_summary(warnings)})

        if canonical.has_errors:
            errors = [i for i in canonical.issues if i.severity == Severity.ERROR]
            self._finish(uow, stored, ProcessingStatus.REJECTED, now)
            self._audit(uow, AuditEventType.VALIDATION_FAILED, name, rid, outcome="error",
                        details={"issues": _issue_summary(errors)})
            return stored, None

        stored.content_hash = canonical.content_hash()
        original = uow.source_records.find_original_by_content_hash(stored.content_hash, rid)
        if original is not None:
            canonical.issues.append(_issue(IssueCode.DUPLICATE_RECORD, "record", Severity.WARNING,
                                           "Normalized content duplicates an earlier record."))
            stored.duplicate_of_id = original.record_id
            self._finish(uow, stored, ProcessingStatus.DUPLICATE, now)
            self._audit(uow, AuditEventType.DUPLICATE_DETECTED, name, rid, outcome="warning",
                        details={"duplicate_of_record_id": original.record_id})
            return stored, None

        decision = self._resolve_identity.execute(uow.patients, canonical.patient)
        self._audit(uow, AuditEventType.IDENTITY_DECISION, name, rid, patient_id=decision.patient_id,
                    outcome="ok" if decision.outcome in (IdentityOutcome.MATCHED, IdentityOutcome.NEW_PATIENT)
                    else "review",
                    details={"outcome": decision.outcome.value, "rationale_code": decision.rationale_code,
                             "related_patient_ids": decision.related_patient_ids})
        if decision.outcome in (IdentityOutcome.CONFLICT, IdentityOutcome.AMBIGUOUS):
            code = (IssueCode.IDENTITY_CONFLICT if decision.outcome == IdentityOutcome.CONFLICT
                    else IssueCode.AMBIGUOUS_MATCH)
            canonical.issues.append(_issue(code, "patient", Severity.ERROR, decision.rationale))
            self._finish(uow, stored, ProcessingStatus.NEEDS_REVIEW, now)
            self._audit(uow, AuditEventType.REVIEW_REQUIRED, name, rid, outcome="review",
                        details={"rationale_code": decision.rationale_code})
            return stored, decision.rationale_code

        stored.patient_id = self._link_or_create_patient(uow, stored, decision, now)
        self._finish(uow, stored, ProcessingStatus.COMPLETED, now)
        self._audit(uow, AuditEventType.RECORD_COMPLETED, name, rid, patient_id=stored.patient_id)
        return stored, decision.rationale_code

    def _link_or_create_patient(self, uow: UnitOfWork, stored: StoredSourceRecord,
                                decision: IdentityDecision, now: datetime) -> str:
        if decision.outcome == IdentityOutcome.MATCHED:
            assert decision.patient_id
            return decision.patient_id
        info = stored.canonical.patient  # type: ignore[union-attr]
        patient = Patient(patient_id=self._new_id(), mrn=info.mrn or "", full_name=info.full_name,
                          name_key=candidate_name_key(info), date_of_birth=info.date_of_birth,
                          gender=info.gender, created_at=now)
        uow.patients.add(patient)
        self._audit(uow, AuditEventType.PATIENT_CREATED, stored.source_name, stored.record_id,
                    patient_id=patient.patient_id)
        return patient.patient_id

    def _finish(self, uow: UnitOfWork, stored: StoredSourceRecord, status: ProcessingStatus,
                now: datetime) -> None:
        stored.processing = ProcessingInfo(status=status, attempts=stored.processing.attempts, updated_at=now)
        uow.source_records.update(stored)

    # ------------------------------------------------------------------ failure / audit helpers
    def _record_failure(self, cmd: IngestCommand, record_id: str, exc: Exception) -> RecordResult:
        category = type(exc).__name__
        try:
            with self._uow_factory() as uow:
                stored = uow.source_records.get(record_id)
                assert stored is not None
                stored.processing = ProcessingInfo(status=ProcessingStatus.FAILED,
                                                   attempts=stored.processing.attempts,
                                                   failure_category=category, updated_at=self._clock())
                uow.source_records.update(stored)
                self._audit(uow, AuditEventType.PROCESSING_FAILED, cmd.source_name, record_id, outcome="error",
                            details={"failure_category": category, "attempt": stored.processing.attempts})
                uow.commit()
            return self._result(stored)
        except Exception:  # noqa: BLE001 - record stays RECEIVED (also retryable); surface as FAILED
            logger.error("failure_state_not_persisted", extra={"record_id": record_id})
            return RecordResult(source_id=f"{cmd.source_name}", record_id=record_id,
                                status=ProcessingStatus.FAILED, attempts=0)

    def _audit_file_rejected(self, cmd: IngestCommand) -> None:
        with self._uow_factory() as uow:
            self._audit(uow, AuditEventType.INGEST_REJECTED, cmd.source_name, None, outcome="error",
                        details={"format": cmd.source_format.value, "reason": "unparseable_input"})
            uow.commit()

    def _audit(self, uow: UnitOfWork, event_type: AuditEventType, source_name: str | None,
               record_id: str | None, *, patient_id: str | None = None, outcome: str = "ok",
               details: dict | None = None) -> None:
        uow.audit.add(AuditEvent(occurred_at=self._clock(), event_type=event_type, source_name=source_name,
                                 record_id=record_id, patient_id=patient_id, outcome=outcome,
                                 details=details or {}))

    @staticmethod
    def _result(stored: StoredSourceRecord, *, rationale: str | None = None,
                already_ingested: bool = False) -> RecordResult:
        return RecordResult(
            source_id=stored.source_id, record_id=stored.record_id, status=stored.processing.status,
            attempts=stored.processing.attempts, patient_id=stored.patient_id,
            duplicate_of_record_id=stored.duplicate_of_id, identity_rationale_code=rationale,
            issues=list(stored.canonical.issues) if stored.canonical else [], already_ingested=already_ingested)


def _issue(code: IssueCode, field: str, severity: Severity, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, field=field, severity=severity, message=message)


def _issue_summary(issues: list[ValidationIssue]) -> list[dict[str, str]]:
    return [{"code": i.code.value, "field": i.field} for i in issues]
