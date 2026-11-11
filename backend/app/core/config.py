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

    # AI summaries. SecretStr keeps the key out of repr()/logs; no key = summaries return 503.
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = 30.0
    openai_temperature: float | None = 0.0  # None omits the parameter (some models only allow the default)
    summary_max_attempts: int = 3  # total provider calls per request (transient errors + invalid output)
    summary_retry_base_seconds: float = 1.0  # exponential backoff base for transient errors
    summary_max_context_records: int = 20
    summary_max_chars_per_record: int = 4000
    summary_max_output_tokens: int = 1200


@lru_cache
def get_settings() -> Settings:
    return Settings()
