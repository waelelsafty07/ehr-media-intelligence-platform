import math

import pytest

from app.infrastructure.search.hashing_embedder import HashingEmbeddingProvider

H = HashingEmbeddingProvider()


def dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_vectors_have_the_declared_dimension_and_unit_norm():
    (v,) = H.embed(["Chest tightness on exertion"])
    assert len(v) == 384 and math.isclose(dot(v, v), 1.0, rel_tol=1e-9)
    assert H.model_identifier == "hashing-ngram-v1-384" and H.kind == "lexical"


def test_embedding_is_deterministic_and_batch_equals_single():
    texts = ["chest pain", "stress test normal", "chest pain"]
    assert H.embed(texts) == [H.embed([t])[0] for t in texts] and H.embed(["x y z"]) == H.embed(["x y z"])
    assert HashingEmbeddingProvider().embed(["chest pain"]) == H.embed(["chest pain"])     # no per-process state


def test_related_texts_are_closer_than_unrelated_ones():
    a, b, c = H.embed(["Chest tightness on exertion", "exertional chest tightness", "broken ankle after a fall"])
    assert dot(a, b) > 0.5 and dot(a, c) == pytest.approx(0.0, abs=1e-9)


def test_case_punctuation_stopwords_and_simple_plurals_do_not_matter():
    a, b = H.embed(["The Symptoms were IMPROVED!", "symptom improve"])
    assert dot(a, b) > 0.9


def test_it_is_lexical_not_semantic():
    """Documented limitation: synonyms with no shared words are NOT similar."""
    a, b = H.embed(["dyspnea", "shortness of breath"])         # same meaning, no shared words
    assert dot(a, b) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("text", ["", "   ", "the a of", "!!!"])
def test_empty_or_stopword_only_text_gives_a_zero_vector_not_an_error(text):
    (v,) = H.embed([text])
    assert len(v) == 384 and all(x == 0.0 for x in v)


def test_custom_dimension_changes_the_model_identifier():
    small = HashingEmbeddingProvider(64)
    assert small.model_identifier == "hashing-ngram-v1-64" and len(small.embed(["x"])[0]) == 64
