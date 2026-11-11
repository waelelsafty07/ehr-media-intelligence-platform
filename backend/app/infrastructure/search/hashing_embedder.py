"""Deterministic LEXICAL embedder (signed feature hashing of word uni/bigrams).

Not semantic: texts are similar only when they share (lightly stemmed) words. It needs no download, is
fully deterministic, and is the default and the test double. Use the sentence-transformers provider for
genuine semantic matching (e.g. "difficulty breathing" ~ "shortness of breath").
"""

from __future__ import annotations

import hashlib
import math
import re
from itertools import pairwise
from typing import Literal

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(["a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "had", "has", "have", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "was", "were", "with"])


def _stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    return token[:-1] if token.endswith("e") and len(token) > 4 else token  # improved/improve -> improv


class HashingEmbeddingProvider:
    kind: Literal["lexical", "semantic"] = "lexical"

    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension
        self.model_identifier = f"hashing-ngram-v1-{dimension}"

    def is_available(self) -> bool:
        return True

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        tokens = [_stem(t) for t in _TOKEN.findall(text.casefold()) if t not in _STOP]
        counts: dict[str, float] = {}
        for tok in tokens:
            counts[tok] = counts.get(tok, 0.0) + 1.0
        for a, b in pairwise(tokens):
            key = f"{a} {b}"
            counts[key] = counts.get(key, 0.0) + 0.5
        vec = [0.0] * self.dimension
        for feature, tf in counts.items():
            h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
            vec[h % self.dimension] += (1.0 if (h >> 63) & 1 else -1.0) * (1.0 + math.log(tf))
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm else vec
