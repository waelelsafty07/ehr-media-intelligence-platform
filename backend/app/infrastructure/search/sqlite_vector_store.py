"""SQLite-backed VectorStore: vectors as float32 BLOBs, exact cosine similarity with numpy.

Patient/type/date filters run in SQL BEFORE ranking, so a patient-scoped query never even loads another
patient's vectors. Exact O(n) scan over the filtered rows: appropriate for thousands of vectors; beyond
that, swap in an ANN-backed adapter behind the same port. Vectors from different embedding models are never
compared (the model identifier is part of the WHERE clause).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.domain.errors import VectorStoreError
from app.domain.search import IndexEntry, IndexStats, SearchFilters, VectorHit
from app.infrastructure.persistence.tables import SearchIndexRow

logger = logging.getLogger(__name__)


def _unit(vector: list[float]) -> np.ndarray:
    v = np.asarray(vector, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else v


class SqliteVectorStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    def content_hashes(self, patient_id: str) -> dict[str, str]:
        try:
            with self._factory() as s:
                rows = s.execute(select(SearchIndexRow.doc_id, SearchIndexRow.content_hash)
                                 .where(SearchIndexRow.patient_id == patient_id)).all()
            return {doc_id: h for doc_id, h in rows}
        except SQLAlchemyError:
            raise self._fail("content_hashes") from None

    def upsert(self, entries: list[IndexEntry]) -> None:
        if not entries:
            return
        try:
            with self._factory() as s:
                for e in entries:
                    vec = _unit(e.embedding)
                    row = s.get(SearchIndexRow, e.doc_id) or SearchIndexRow(doc_id=e.doc_id)
                    row.patient_id, row.resource_type, row.resource_id = e.patient_id, e.resource_type, e.resource_id
                    row.source_record_id, row.record_date, row.text = e.source_record_id, e.record_date, e.text
                    row.content_hash, row.model_identifier = e.content_hash, e.model_identifier
                    row.dimension, row.embedding = int(vec.shape[0]), vec.tobytes()
                    row.indexed_at = e.indexed_at.astimezone(UTC).replace(tzinfo=None)
                    s.add(row)
                s.commit()  # one transaction: all entries of the batch or none
        except SQLAlchemyError:
            raise self._fail("upsert") from None

    def delete(self, doc_ids: list[str]) -> int:
        if not doc_ids:
            return 0
        try:
            with self._factory() as s:
                result = s.execute(delete(SearchIndexRow).where(SearchIndexRow.doc_id.in_(doc_ids)))
                s.commit()
                return int(result.rowcount or 0)
        except SQLAlchemyError:
            raise self._fail("delete") from None

    def query(self, vector: list[float], *, model_identifier: str, filters: SearchFilters,
              limit: int) -> list[VectorHit]:
        q = _unit(vector)
        stmt = select(SearchIndexRow).where(SearchIndexRow.model_identifier == model_identifier,
                                            SearchIndexRow.dimension == int(q.shape[0]))
        if filters.patient_id:
            stmt = stmt.where(SearchIndexRow.patient_id == filters.patient_id)
        if filters.resource_types:
            stmt = stmt.where(SearchIndexRow.resource_type.in_(filters.resource_types))
        if filters.date_from:
            stmt = stmt.where(SearchIndexRow.record_date >= filters.date_from)
        if filters.date_to:
            stmt = stmt.where(SearchIndexRow.record_date <= filters.date_to)
        try:
            with self._factory() as s:
                rows = list(s.scalars(stmt))
        except SQLAlchemyError:
            raise self._fail("query") from None
        if not rows:
            return []
        matrix = np.vstack([np.frombuffer(r.embedding, dtype=np.float32) for r in rows])
        scores = matrix @ q
        order = sorted(range(len(rows)), key=lambda i: (-float(scores[i]), rows[i].doc_id))[:limit]
        return [VectorHit(
            doc_id=rows[i].doc_id, patient_id=rows[i].patient_id, resource_type=rows[i].resource_type,
            resource_id=rows[i].resource_id, source_record_id=rows[i].source_record_id,
            record_date=rows[i].record_date, text=rows[i].text, content_hash=rows[i].content_hash,
            model_identifier=rows[i].model_identifier, score=float(scores[i])) for i in order]

    def stats(self) -> IndexStats:
        try:
            with self._factory() as s:
                by_type = dict(s.execute(select(SearchIndexRow.resource_type, func.count())
                                         .group_by(SearchIndexRow.resource_type)).all())
                by_model = dict(s.execute(select(SearchIndexRow.model_identifier, func.count())
                                          .group_by(SearchIndexRow.model_identifier)).all())
        except SQLAlchemyError:
            raise self._fail("stats") from None
        return IndexStats(total=sum(by_type.values()), by_resource_type=by_type, by_model=by_model)

    @staticmethod
    def _fail(op: str) -> VectorStoreError:
        logger.error("vector_store_failed", extra={"operation": op, "at": datetime.now(UTC).isoformat()})
        return VectorStoreError("The search index is unavailable.")
