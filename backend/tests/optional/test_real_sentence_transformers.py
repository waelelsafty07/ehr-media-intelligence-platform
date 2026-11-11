"""OPTIONAL: exercises the REAL all-MiniLM-L6-v2 model. Skipped by default (needs the `embeddings` extra, a model
download and EHR_RUN_REAL_EMBEDDING_TESTS=1). Run with:
    pip install -e "./backend[embeddings]" && EHR_RUN_REAL_EMBEDDING_TESTS=1 pytest backend/tests/optional -v
"""

import os

import pytest

pytest.importorskip("sentence_transformers")
pytestmark = pytest.mark.skipif(os.environ.get("EHR_RUN_REAL_EMBEDDING_TESTS") != "1",
                                reason="set EHR_RUN_REAL_EMBEDDING_TESTS=1 to run the real-model test")

from app.infrastructure.search.sentence_transformer_embedder import (
    SentenceTransformerEmbeddingProvider,
)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_real_model_is_semantic_normalized_and_deterministic():
    p = SentenceTransformerEmbeddingProvider()
    a, b, c = p.embed(["shortness of breath", "difficulty breathing", "broken ankle after a fall"])
    assert p.kind == "semantic" and len(a) == 384 and abs(dot(a, a) - 1.0) < 1e-4
    assert dot(a, b) > 0.5 > dot(a, c) + 0.3                                   # synonyms close, unrelated far
    assert p.embed(["shortness of breath"])[0] == pytest.approx(a, abs=1e-5)
