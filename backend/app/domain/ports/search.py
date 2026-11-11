"""Ports for embeddings and the (derived, rebuildable) vector index."""

from __future__ import annotations

from typing import Literal, Protocol

from app.domain.search import IndexEntry, IndexStats, SearchFilters, VectorHit


class EmbeddingProvider(Protocol):
    model_identifier: str
    kind: Literal["lexical", "semantic"]  # reported to clients so scores are not misread

    def is_available(self) -> bool:
        """Cheap check (no model load, no network) that embed() can be expected to work."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """L2-normalized vectors, one per text. Raises EmbeddingUnavailableError on failure.
        Implementations must be local (or otherwise approved): only clinical text is ever passed in."""
        ...


class VectorStore(Protocol):
    """SQLite stays the source of truth; the index is derived and can be rebuilt at any time."""

    def content_hashes(self, patient_id: str) -> dict[str, str]:
        """doc_id -> content_hash of everything currently indexed for the patient."""
        ...

    def upsert(self, entries: list[IndexEntry]) -> None: ...

    def delete(self, doc_ids: list[str]) -> int: ...

    def query(self, vector: list[float], *, model_identifier: str, filters: SearchFilters,
              limit: int) -> list[VectorHit]:
        """Filters (patient/type/date) are applied BEFORE ranking. Highest cosine first; ties by doc_id."""
        ...

    def stats(self) -> IndexStats: ...
