"""All settings come from environment variables (or a .env file).

Every variable starts with BASELINE_, for example BASELINE_DATABASE_URL.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BASELINE_", env_file=".env", extra="ignore")

    # Postgres connection used by the app. This role must NOT be a superuser,
    # otherwise row-level security is bypassed.
    database_url: str = "postgresql+psycopg://baseline:baseline@localhost:5432/baseline"

    # "local" stores files in a folder on disk; "s3" uses S3 (or LocalStack).
    storage: str = "local"
    local_storage_dir: Path = Path(".baseline_storage")

    s3_bucket: str = "baseline-files"
    s3_endpoint_url: str | None = None  # http://localhost:4566 for LocalStack
    aws_region: str = "us-east-1"

    # Where trained models are saved.
    model_dir: Path = Path("models")

    # Analytics thresholds.
    float_erosion_threshold_days: float = 5.0
    slip_horizon_weeks: int = 4
    slip_tolerance_days: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()