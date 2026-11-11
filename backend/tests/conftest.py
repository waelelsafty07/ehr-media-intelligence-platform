import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", _env_file=None)
    return TestClient(create_app(settings), raise_server_exceptions=False)
