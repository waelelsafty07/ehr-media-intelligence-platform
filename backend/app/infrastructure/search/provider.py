"""Embedding provider selection from settings (composition-root helper)."""

from __future__ import annotations

from app.core.config import Settings
from app.domain.ports.search import EmbeddingProvider
from app.infrastructure.search.hashing_embedder import HashingEmbeddingProvider
from app.infrastructure.search.sentence_transformer_embedder import (
    SentenceTransformerEmbeddingProvider,
)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "sentence-transformers":
        return SentenceTransformerEmbeddingProvider(settings.embedding_model)
    return HashingEmbeddingProvider()
