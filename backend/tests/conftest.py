import os
import sys

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def _offline_embeddings(monkeypatch):
    """The normal suite is fully offline: block `sentence_transformers` so no test can load or download a real model.
    (The default embedding provider is semantic, so a test that forgets to choose a fake would otherwise reach for it.)
    Opt-in real-model tests set EHR_RUN_REAL_EMBEDDING_TESTS=1 and are the only ones that get the real library."""
    if os.environ.get("EHR_RUN_REAL_EMBEDDING_TESTS") != "1":
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)


@pytest.fixture
def client(tmp_path) -> TestClient:
    # Deterministic lexical embedder chosen EXPLICITLY: the application default is semantic (all-MiniLM-L6-v2).
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", embedding_provider="hashing", _env_file=None)
    return TestClient(create_app(settings), raise_server_exceptions=False)
