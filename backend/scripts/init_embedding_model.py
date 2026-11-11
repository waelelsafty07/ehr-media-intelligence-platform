"""Download (first run, needs network once) and verify the semantic embedding model, then exit.

    pip install -e "./backend[embeddings]"
    python backend/scripts/init_embedding_model.py

Exit code 0 = the model loads and produces normalized vectors; non-zero = semantic search will return 503 until fixed.
Uses the model configured by EHR_EMBEDDING_MODEL (default sentence-transformers/all-MiniLM-L6-v2). Runs locally.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.domain.errors import EmbeddingUnavailableError
from app.infrastructure.search.sentence_transformer_embedder import (
    SentenceTransformerEmbeddingProvider,
)


def main() -> int:
    model = Settings().embedding_model
    provider = SentenceTransformerEmbeddingProvider(model)
    if not provider.is_available():
        print("sentence-transformers is not installed. Run: pip install -e './backend[embeddings]'")
        return 2
    print(f"Loading {model} (the first run downloads it from the Hugging Face Hub; this can take a minute)...")
    started = time.perf_counter()
    try:
        vectors = provider.embed(["initialization check", "shortness of breath"])
    except EmbeddingUnavailableError as exc:
        print(f"FAILED: {exc}")
        return 1
    elapsed = time.perf_counter() - started
    norm = sum(x * x for x in vectors[0]) ** 0.5
    print(f"OK: model ready in {elapsed:.1f}s, dimension {len(vectors[0])}, unit norm {norm:.3f}. Semantic search is available.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
