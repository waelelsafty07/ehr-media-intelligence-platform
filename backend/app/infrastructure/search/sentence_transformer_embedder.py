"""Local semantic embeddings with sentence-transformers (default model: all-MiniLM-L6-v2).

OPTIONAL: install with `pip install -e "./backend[embeddings]"`. The model is downloaded from the Hugging
Face Hub on first use (network needed once) and loaded lazily, so importing this module costs nothing.
Runs locally: texts are never sent to an external inference service. Inputs longer than the model's
sequence limit (256 word pieces for all-MiniLM-L6-v2) are truncated by the model; there is no chunking yet.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from typing import Any, Literal

from app.domain.errors import EmbeddingUnavailableError

logger = logging.getLogger(__name__)


class SentenceTransformerEmbeddingProvider:
    kind: Literal["lexical", "semantic"] = "semantic"

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.model_identifier = model_name
        self._model: Any = None
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        """True if the library is importable. Does not load the model or touch the network."""
        try:
            return importlib.util.find_spec("sentence_transformers") is not None
        except (ValueError, ImportError):
            return False

    def _load(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise EmbeddingUnavailableError(
                    "Semantic search is unavailable: sentence-transformers is not installed. Install it with "
                    "pip install -e './backend[embeddings]' (see the README), or explicitly choose the lexical fallback "
                    "with EHR_EMBEDDING_PROVIDER=hashing."
                ) from None
            try:
                self._model = SentenceTransformer(self.model_identifier, device="cpu")
            except Exception:  # noqa: BLE001 - download/IO/format errors: generic, no provider text
                logger.error("embedding_model_load_failed")
                raise EmbeddingUnavailableError(
                    "Semantic search is unavailable: the embedding model could not be loaded. Run "
                    "python backend/scripts/init_embedding_model.py (needs network once), or explicitly choose the "
                    "lexical fallback with EHR_EMBEDDING_PROVIDER=hashing."
                ) from None
            return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        try:
            vectors = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True,
                                   show_progress_bar=False, batch_size=32)
        except Exception:  # noqa: BLE001
            logger.error("embedding_failed")
            raise EmbeddingUnavailableError("Embedding failed.") from None
        return [list(map(float, v)) for v in vectors]
