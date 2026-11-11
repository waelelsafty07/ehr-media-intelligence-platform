"""GenerateFHIRBundleUseCase: map a patient's COMPLETED records to FHIR, validate, persist.

FHIR state is tracked in patient_fhir_state, separate from ingestion status. Flow, in one transaction:
  fingerprint unchanged and already GENERATED -> skip (idempotent, audited)
  map -> bundle -> domain checks (value sets, references, attachments) + library validation
  issues  -> state INVALID, NOTHING served, resources untouched
  clean   -> replace the patient's resources + state GENERATED atomically
An unexpected exception rolls back everything, then FAILED is recorded in a fresh transaction.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime

from app.application.dto import FhirGenerationResult
from app.domain.entities import AuditEvent, AuditEventType
from app.domain.errors import FhirBuildError, PatientNotFoundError
from app.domain.fhir import (
    FhirIssue,
    FhirStatus,
    PatientFhirState,
    bundle_id_for,
    check_bundle,
    compute_fingerprint,
)
from app.domain.ports.fhir import FhirBundleBuilder, FhirSchemaValidator
from app.domain.ports.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


class GenerateFHIRBundleUseCase:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        builder: FhirBundleBuilder,
        validator: FhirSchemaValidator,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._uow_factory = uow_factory
        self._builder = builder
        self._validator = validator
        self._clock = clock

    def execute(self, patient_id: str) -> FhirGenerationResult:
        try:
            with self._uow_factory() as uow:
                result = self._generate(uow, patient_id)
                uow.commit()
            return result
        except PatientNotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001 - any failure must end in a recorded FAILED state
            logger.error("fhir_generation_failed", extra={"patient_id": patient_id,
                                                         "failure_category": type(exc).__name__})
            return self._record_failure(patient_id, type(exc).__name__)

    # ------------------------------------------------------------------
    def _generate(self, uow: UnitOfWork, patient_id: str) -> FhirGenerationResult:
        patient = uow.patients.get(patient_id)
        if patient is None:
            raise PatientNotFoundError("Patient not found.")
        records = uow.source_records.list_completed_for_patient(patient_id)
        fingerprint = compute_fingerprint(patient, records)
        state = uow.fhir.get_state(patient_id)
        if state and state.status == FhirStatus.GENERATED and state.fingerprint == fingerprint:
            self._audit(uow, patient_id, AuditEventType.FHIR_UNCHANGED, details={"bundle_id": state.bundle_id})
            return self._result(state, unchanged=True, counts=_counts(uow.fhir.list_resources(patient_id)))

        now = self._clock()
        bundle_id = bundle_id_for(patient_id)
        issues: list[FhirIssue]
        try:
            resources = self._builder.build_resources(patient, records, now)
            bundle = self._builder.assemble_bundle(bundle_id, now, resources)
            issues = check_bundle(bundle) + self._validator.validate_bundle(bundle)
        except FhirBuildError as exc:
            resources, issues = [], list(exc.issues)

        if issues:
            state = PatientFhirState(patient_id=patient_id, status=FhirStatus.INVALID, fingerprint=fingerprint,
                                     issues=issues, updated_at=now)
            uow.fhir.save_state(state)
            self._audit(uow, patient_id, AuditEventType.FHIR_VALIDATION_FAILED, outcome="error",
                        details={"issues": [{"code": i.code, "path": i.path} for i in issues]})
            logger.warning("fhir_validation_failed", extra={"patient_id": patient_id, "issue_count": len(issues)})
            return self._result(state)

        uow.fhir.replace_resources(patient_id, resources)
        state = PatientFhirState(patient_id=patient_id, status=FhirStatus.GENERATED, fingerprint=fingerprint,
                                 bundle_id=bundle_id, bundle_timestamp=now, resource_count=len(resources),
                                 updated_at=now)
        uow.fhir.save_state(state)
        counts = _counts(resources)
        self._audit(uow, patient_id, AuditEventType.FHIR_GENERATED, details={"bundle_id": bundle_id, "counts": counts})
        logger.info("fhir_generated", extra={"patient_id": patient_id, "counts": counts})
        return self._result(state, counts=counts)

    def _record_failure(self, patient_id: str, category: str) -> FhirGenerationResult:
        try:
            with self._uow_factory() as uow:
                previous = uow.fhir.get_state(patient_id)
                state = PatientFhirState(patient_id=patient_id, status=FhirStatus.FAILED,
                                         fingerprint=previous.fingerprint if previous else "",
                                         failure_category=category, updated_at=self._clock())
                uow.fhir.save_state(state)
                self._audit(uow, patient_id, AuditEventType.FHIR_GENERATION_FAILED, outcome="error",
                            details={"failure_category": category})
                uow.commit()
            return self._result(state)
        except Exception:  # noqa: BLE001
            logger.error("fhir_failure_state_not_persisted", extra={"patient_id": patient_id})
            return FhirGenerationResult(patient_id=patient_id, status=FhirStatus.FAILED)

    def _audit(self, uow: UnitOfWork, patient_id: str, event_type: AuditEventType, *,
               outcome: str = "ok", details: dict | None = None) -> None:
        uow.audit.add(AuditEvent(occurred_at=self._clock(), event_type=event_type, patient_id=patient_id,
                                 outcome=outcome, details=details or {}))

    @staticmethod
    def _result(state: PatientFhirState, *, unchanged: bool = False,
                counts: dict[str, int] | None = None) -> FhirGenerationResult:
        return FhirGenerationResult(patient_id=state.patient_id, status=state.status, unchanged=unchanged,
                                    bundle_id=state.bundle_id, resource_counts=counts or {},
                                    issues=list(state.issues), updated_at=state.updated_at)


def _counts(resources: list) -> dict[str, int]:
    return dict(Counter(r.resource_type for r in resources))
