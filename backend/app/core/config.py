from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Bowdoin CED Job Tracker"
    environment: str = "development"

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/jobs_tracker"

    claude_api_key: str | None = None
    anthropic_model: str = "claude-3-5-sonnet-latest"

    csv_input_path: str = "employers.csv"
    csv_output_path: str = "data/processed/employers_with_urls.csv"

    enrichment_concurrency: int = 5
    request_timeout_seconds: int = 30
    max_retries: int = 3

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
