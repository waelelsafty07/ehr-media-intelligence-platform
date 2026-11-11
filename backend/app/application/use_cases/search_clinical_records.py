"""SearchClinicalRecordsUseCase and search status.

Flow: embed query -> vector store query with patient/type/date filters applied BEFORE ranking (over-fetching
candidates) -> re-validate every hit against SQLite (source of truth): the resource must still exist, belong to
the hit's patient, have unchanged text, and (for summaries) still be the current non-stale summary -> rank ->
top `limit` with snippets. Stale index entries are therefore never shown, even before the next re-index.
Patient identity comes from the stored record, never from similarity.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from app.application.dto import SearchOutcome, SearchResultItem, SearchStatus
from app.application.use_cases.get_patient_summary import GetPatientSummaryUseCase
from app.domain.errors import PatientNotFoundError, SummaryNotFoundError
from app.domain.fhir import FhirStatus
from app.domain.ports.search import EmbeddingProvider, VectorStore
from app.domain.ports.unit_of_work import UnitOfWork
from app.domain.search import (
    RESOURCE_CLINICAL_SUMMARY,
    RESOURCE_DOCUMENT_REFERENCE,
    SearchQuery,
    VectorHit,
    build_snippet,
    document_date_from_resource,
    document_text_from_resource,
    index_content_hash,
    summary_index_text,
    summary_record_date,
)

logger = logging.getLogger(__name__)
_MIN_CANDIDATES = 20
_OVERFETCH = 4


class SearchClinicalRecordsUseCase:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], embedder: EmbeddingProvider, store: VectorStore,
                 summary_reader: GetPatientSummaryUseCase) -> None:
        self._uow_factory = uow_factory
        self._embedder = embedder
        self._store = store
        self._summaries = summary_reader

    def execute(self, query: SearchQuery) -> SearchOutcome:
        started = time.perf_counter()
        if query.patient_id is not None:
            with self._uow_factory() as uow:
                if uow.patients.get(query.patient_id) is None:
                    raise PatientNotFoundError("Patient not found.")

        vector = self._embedder.embed([query.query])[0]            # EmbeddingUnavailableError -> 503
        hits = self._store.query(vector, model_identifier=self._embedder.model_identifier, filters=query.filters,
                                 limit=max(_MIN_CANDIDATES, query.limit * _OVERFETCH))   # VectorStoreError -> 503
        # Non-positive similarity is never a match (it would present unrelated text as a hit); min_score may only raise the bar.
        hits = [h for h in hits if h.score > 0 and (query.min_score is None or h.score >= query.min_score)]

        current = self._validate(hits, query)
        current.sort(key=lambda h: (-h.score, -(h.record_date.toordinal() if h.record_date else 0), h.doc_id))
        items = [SearchResultItem(
            rank=i, resource_type=h.resource_type, resource_id=h.resource_id, patient_id=h.patient_id,
            record_date=h.record_date,
            score=round(h.score, 4), snippet=build_snippet(h.text, query.query), source_record_id=h.source_record_id,
            ai_generated=h.resource_type == RESOURCE_CLINICAL_SUMMARY)
            for i, h in enumerate(current[: query.limit], start=1)]
        took = round((time.perf_counter() - started) * 1000, 2)
        logger.info("search_completed", extra={            # never the query text: it may contain identifiers
            "returned": len(items), "candidates": len(hits), "dropped_stale": len(hits) - len(current),
            "patient_scoped": query.patient_id is not None, "took_ms": took})
        return SearchOutcome(items=items, candidates_considered=len(hits), stale_results_dropped=len(hits) - len(current),
                             took_ms=took, embedding_model=self._embedder.model_identifier,
                             embedding_kind=self._embedder.kind)

    # ------------------------------------------------------------------
    def _validate(self, hits: list[VectorHit], query: SearchQuery) -> list[VectorHit]:
        """Keep only hits that still match SQLite. The patient is loaded only to confirm it exists; no name/MRN is output."""
        valid: list[VectorHit] = []
        summary_hits: list[VectorHit] = []
        with self._uow_factory() as uow:
            for hit in hits:
                if query.patient_id is not None and hit.patient_id != query.patient_id:
                    continue                                    # defense in depth: the store already filtered
                if uow.patients.get(hit.patient_id) is None:
                    continue
                if hit.resource_type == RESOURCE_DOCUMENT_REFERENCE:
                    if not self._document_is_current(uow, hit):
                        continue
                elif hit.resource_type == RESOURCE_CLINICAL_SUMMARY:
                    summary_hits.append(hit)
                    continue
                else:
                    continue
                valid.append(hit)
        views: dict[str, object] = {}
        for hit in summary_hits:                                # summaries: current, non-stale, same content only
            if hit.patient_id not in views:
                try:
                    views[hit.patient_id] = self._summaries.execute(hit.patient_id)
                except (SummaryNotFoundError, PatientNotFoundError):
                    views[hit.patient_id] = None
            view = views[hit.patient_id]
            if view is None or view.is_stale or view.summary_id != hit.resource_id:  # type: ignore[attr-defined]
                continue
            expected = index_content_hash(hit.model_identifier, RESOURCE_CLINICAL_SUMMARY,
                                          summary_record_date(view.summary), summary_index_text(view.summary))  # type: ignore[attr-defined]
            if expected == hit.content_hash:
                valid.append(hit)
        return valid

    @staticmethod
    def _document_is_current(uow: UnitOfWork, hit: VectorHit) -> bool:
        state = uow.fhir.get_state(hit.patient_id)
        if state is None or state.status != FhirStatus.GENERATED:
            return False
        res = uow.fhir.get_resource(RESOURCE_DOCUMENT_REFERENCE, hit.resource_id)
        if res is None or res.patient_id != hit.patient_id:
            return False
        text = document_text_from_resource(res.content)
        if text is None:
            return False
        return hit.content_hash == index_content_hash(hit.model_identifier, RESOURCE_DOCUMENT_REFERENCE,
                                                     document_date_from_resource(res.content), text)


class GetSearchStatusUseCase:
    def __init__(self, embedder: EmbeddingProvider, store: VectorStore) -> None:
        self._embedder = embedder
        self._store = store

    def execute(self) -> SearchStatus:
        stats = self._store.stats()
        return SearchStatus(entries_total=stats.total, by_resource_type=stats.by_resource_type,
                            by_model=stats.by_model, embedding_model=self._embedder.model_identifier,
                            embedding_kind=self._embedder.kind, embedding_available=self._embedder.is_available())
