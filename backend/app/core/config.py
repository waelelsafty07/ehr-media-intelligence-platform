"""Application settings loaded from environment variables (never hardcode secrets)."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_prefix="EHR_", extra="ignore"
    )

    app_name: str = "EHR Media Intelligence Platform"
    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = f"sqlite:///{BACKEND_DIR / 'data' / 'ehr.db'}"
    max_upload_bytes: int = 5 * 1024 * 1024
    cors_origins: list[str] = ["http://localhost:5173"]

    # Used from Phase 4; SecretStr keeps the value out of repr()/logs.
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"


@lru_cache
def get_settings() -> Settings:
    return Settings()
