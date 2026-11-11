"""Embedding configuration: semantic is the default, lexical is an explicit fallback, nothing falls back silently."""

import logging
import sys

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.domain.errors import EmbeddingUnavailableError
from app.infrastructure.search.hashing_embedder import HashingEmbeddingProvider
from app.infrastructure.search.provider import build_embedding_provider
from app.infrastructure.search.sentence_transformer_embedder import (
    SentenceTransformerEmbeddingProvider,
)
from app.main import create_app
from tests.search_helpers import install_fake_sentence_transformers

MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def settings(**kw):
    return Settings(_env_file=None, **kw)


def test_the_default_embedding_provider_is_semantic_all_minilm():
    s = settings()
    assert s.embedding_provider == "sentence-transformers" and s.embedding_model == MODEL
    p = build_embedding_provider(s)
    assert isinstance(p, SentenceTransformerEmbeddingProvider)
    assert p.kind == "semantic" and p.model_identifier == MODEL


def test_lexical_mode_is_an_explicit_choice():
    p = build_embedding_provider(settings(embedding_provider="hashing"))
    assert isinstance(p, HashingEmbeddingProvider) and p.kind == "lexical" and p.model_identifier == "hashing-ngram-v1-384"


def test_provider_is_selected_from_the_environment(monkeypatch):
    monkeypatch.setenv("EHR_EMBEDDING_PROVIDER", "hashing")
    assert Settings(_env_file=None).embedding_provider == "hashing"
    monkeypatch.setenv("EHR_EMBEDDING_PROVIDER", "sentence-transformers")
    monkeypatch.setenv("EHR_EMBEDDING_MODEL", "some/other-model")
    s = Settings(_env_file=None)
    assert build_embedding_provider(s).model_identifier == "some/other-model"


@pytest.mark.parametrize("bad", ["lexical", "chroma", "", "openai", "SENTENCE-TRANSFORMERS"])
def test_unknown_provider_names_fail_fast_at_configuration(bad):
    with pytest.raises(ValidationError):
        settings(embedding_provider=bad)


def test_building_the_semantic_provider_never_loads_the_model(monkeypatch):
    rec = install_fake_sentence_transformers(monkeypatch)
    p = build_embedding_provider(settings())
    assert rec.constructed == 0 and p._model is None                   # loading is lazy: no download at startup/import


def test_semantic_provider_loads_once_runs_on_cpu_and_normalizes(monkeypatch):
    rec = install_fake_sentence_transformers(monkeypatch)
    p = build_embedding_provider(settings())
    a = p.embed(["chest pain"])
    b = p.embed(["chest pain", "ankle"])
    assert rec.constructed == 1 and rec.names == [MODEL] and rec.devices == ["cpu"]
    assert all(c["normalize_embeddings"] is True for c in rec.calls) and len(a) == 1 and len(b) == 2
    assert pytest.approx(sum(x * x for x in a[0]), rel=1e-4) == 1.0


def test_availability_check_is_cheap_and_does_not_load_the_model(monkeypatch):
    p = SentenceTransformerEmbeddingProvider()
    assert p.is_available() is False                                   # the autouse guard blocks the library
    rec = install_fake_sentence_transformers(monkeypatch)
    assert p.is_available() is True and rec.constructed == 0
    assert HashingEmbeddingProvider().is_available() is True


def test_missing_library_is_a_clear_error_that_mentions_the_fix_and_the_explicit_fallback():
    p = SentenceTransformerEmbeddingProvider()
    with pytest.raises(EmbeddingUnavailableError) as e:
        p.embed(["x"])
    msg = str(e.value)
    assert "pip install -e './backend[embeddings]'" in msg and "EHR_EMBEDDING_PROVIDER=hashing" in msg and "unavailable" in msg.lower()


def test_unloadable_model_is_a_clear_error_without_provider_details(monkeypatch):
    install_fake_sentence_transformers(monkeypatch, fail_load=True)
    with pytest.raises(EmbeddingUnavailableError) as e:
        SentenceTransformerEmbeddingProvider().embed(["x"])
    assert "SECRET-PROVIDER-DETAIL" not in str(e.value) and "huggingface" not in str(e.value).lower()
    assert "init_embedding_model.py" in str(e.value) and "EHR_EMBEDDING_PROVIDER=hashing" in str(e.value)


def test_embedding_failures_are_generic(monkeypatch):
    install_fake_sentence_transformers(monkeypatch, fail_encode=True)
    with pytest.raises(EmbeddingUnavailableError) as e:
        SentenceTransformerEmbeddingProvider().embed(["x"])
    assert "SECRET-PROVIDER-DETAIL" not in str(e.value)


def test_the_semantic_provider_never_silently_returns_lexical_vectors():
    """If the model is unavailable it raises: it must not produce hashing vectors under a semantic label."""
    p = SentenceTransformerEmbeddingProvider()
    with pytest.raises(EmbeddingUnavailableError):
        p.embed(["shortness of breath"])
    assert p.kind == "semantic"


def test_startup_logs_a_warning_when_the_configured_provider_is_unavailable(tmp_path):
    seen: list[str] = []

    class Capture(logging.Handler):                       # create_app() resets root handlers, so attach to the app logger
        def emit(self, record):
            seen.append(record.getMessage())

    logger = logging.getLogger("app.main")
    handler = Capture(level=logging.WARNING)
    logger.addHandler(handler)
    try:
        create_app(settings(database_url=f"sqlite:///{tmp_path / 'w.db'}"))
        assert "embedding_provider_unavailable" in seen
        seen.clear()
        create_app(settings(database_url=f"sqlite:///{tmp_path / 'w2.db'}", embedding_provider="hashing"))
        assert "embedding_provider_unavailable" not in seen
    finally:
        logger.removeHandler(handler)


def test_importing_the_application_does_not_import_or_download_a_model():
    assert sys.modules.get("sentence_transformers") is None            # blocked by the guard; the app imported fine
