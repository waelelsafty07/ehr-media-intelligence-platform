"""IndexClinicalRecordsUseCase: reconcile the derived search index with SQLite for a patient.

Desired state for a patient = (a) every DocumentReference of a `generated` FHIR state, with decodable text, and
(b) the latest generated, NON-STALE clinical summary. The use case embeds only entries whose content hash
changed (idempotent), upserts them, then deletes index entries that are no longer desired. That single rule
handles updates (hash changes), deletions (FHIR regenerated / resource gone / FHIR invalid or failed) and
invalidation (summary stale, failed or missing). Retrying after a failure is always safe.

Only clinical text is embedded: never the patient's name, MRN or date of birth.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from app.application.dto import IndexResult
from app.application.use_cases.get_patient_summary import GetPatientSummaryUseCase
from app.domain.entities import AuditEvent, AuditEventType
from app.domain.errors import PatientNotFoundError, SearchInfrastructureError, SummaryNotFoundError
from app.domain.fhir import FhirStatus
from app.domain.ports.search import EmbeddingProvider, VectorStore
from app.domain.ports.unit_of_work import UnitOfWork
from app.domain.search import (
    RESOURCE_CLINICAL_SUMMARY,
    RESOURCE_DOCUMENT_REFERENCE,
    IndexedDocument,
    IndexEntry,
    document_date_from_resource,
    document_text_from_resource,
    index_content_hash,
    make_doc_id,
    summary_index_text,
    summary_record_date,
)

logger = logging.getLogger(__name__)


class IndexClinicalRecordsUseCase:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], embedder: EmbeddingProvider, store: VectorStore,
                 summary_reader: GetPatientSummaryUseCase,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._uow_factory = uow_factory
        self._embedder = embedder
        self._store = store
        self._summaries = summary_reader
        self._clock = clock

    def execute(self, patient_id: str) -> IndexResult:
        try:
            return self._run(patient_id)
        except PatientNotFoundError:
            raise
        except SearchInfrastructureError as exc:
            logger.error("index_failed", extra={"patient_id": patient_id, "failure_category": type(exc).__name__})
            self._audit(patient_id, AuditEventType.INDEX_FAILED, "error", {"failure_category": type(exc).__name__})
            raise

    def execute_all(self) -> list[IndexResult]:
        with self._uow_factory() as uow:
            ids = [p.patient_id for p in uow.patients.list_all()]
        return [self.execute(pid) for pid in ids]

    # ------------------------------------------------------------------
    def _run(self, patient_id: str) -> IndexResult:
        model = self._embedder.model_identifier
        desired: list[IndexedDocument] = []
        skipped: list[str] = []
        with self._uow_factory() as uow:
            if uow.patients.get(patient_id) is None:
                raise PatientNotFoundError("Patient not found.")
            state = uow.fhir.get_state(patient_id)
            if state is not None and state.status == FhirStatus.GENERATED:
                for res in uow.fhir.list_resources(patient_id, RESOURCE_DOCUMENT_REFERENCE):
                    text = document_text_from_resource(res.content)
                    if text is None:
                        skipped.append("document_without_text")
                        continue
                    rdate = document_date_from_resource(res.content)
                    desired.append(IndexedDocument(
                        doc_id=make_doc_id(RESOURCE_DOCUMENT_REFERENCE, res.resource_id), patient_id=patient_id,
                        resource_type=RESOURCE_DOCUMENT_REFERENCE, resource_id=res.resource_id,
                        source_record_id=res.source_record_id, record_date=rdate, text=text, model_identifier=model,
                        content_hash=index_content_hash(model, RESOURCE_DOCUMENT_REFERENCE, rdate, text)))
            else:
                skipped.append("fhir_not_generated")

        try:
            view = self._summaries.execute(patient_id)
        except SummaryNotFoundError:
            view = None
            skipped.append("summary_not_available")
        if view is not None and view.is_stale:
            skipped.append("summary_stale")  # stale summaries are never indexed (and are removed below)
        elif view is not None:
            text, rdate = summary_index_text(view.summary), summary_record_date(view.summary)
            desired.append(IndexedDocument(
                doc_id=make_doc_id(RESOURCE_CLINICAL_SUMMARY, view.summary_id), patient_id=patient_id,
                resource_type=RESOURCE_CLINICAL_SUMMARY, resource_id=view.summary_id, source_record_id=None,
                record_date=rdate, text=text, model_identifier=model,
                content_hash=index_content_hash(model, RESOURCE_CLINICAL_SUMMARY, rdate, text)))

        existing = self._store.content_hashes(patient_id)
        changed = [d for d in desired if existing.get(d.doc_id) != d.content_hash]
        wanted_ids = {d.doc_id for d in desired}
        stale_ids = [doc_id for doc_id in existing if doc_id not in wanted_ids]

        if changed:
            distinct = list(dict.fromkeys(d.text for d in changed))  # embed identical texts once
            vectors = dict(zip(distinct, self._embedder.embed(distinct), strict=True))
            now = self._clock()
            self._store.upsert([IndexEntry(**d.model_dump(), embedding=vectors[d.text], indexed_at=now)
                                for d in changed])
        removed = self._store.delete(stale_ids)  # after upsert: a failure never leaves the patient with less

        result = IndexResult(patient_id=patient_id, indexed=len(changed), unchanged=len(desired) - len(changed),
                             removed=removed, skipped=skipped)
        self._audit(patient_id, AuditEventType.INDEX_COMPLETED, "ok",
                    {"indexed": result.indexed, "unchanged": result.unchanged, "removed": removed,
                     "skipped": skipped, "model": model})
        logger.info("index_completed", extra={"patient_id": patient_id, "indexed": result.indexed,
                                              "unchanged": result.unchanged, "removed": removed})
        return result

    def _audit(self, patient_id: str, event_type: AuditEventType, outcome: str, details: dict) -> None:
        try:
            with self._uow_factory() as uow:
                uow.audit.add(AuditEvent(occurred_at=self._clock(), event_type=event_type, patient_id=patient_id,
                                         outcome=outcome, details=details))
                uow.commit()
        except Exception:  # noqa: BLE001 - auditing must never mask the real outcome
            logger.error("index_audit_failed", extra={"patient_id": patient_id})
