"""Runtime configuration, read from the environment.

Blue and green run the identical image; only APP_COLOR and DATABASE_URL differ.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # Which half of the blue/green pair this process is.
    app_color: str = "blue"
    app_version: str = "0.0.0"

    database_url: str = "postgresql+psycopg2://app_user:app_pw@db-blue:5432/appdb"
    redis_url: str = "redis://redis:6379/0"

    # Mock OIDC provider (stands in for Auth0 / Clerk).
    oidc_issuer: str = "http://oidc:9000"
    oidc_audience: str = "blue-green-lab-api"
    # Internal URL used for JWKS fetch; issuer value stays the public one.
    oidc_internal_url: str = "http://oidc:9000"

    # Backpressure: reject new work above this queue depth. Deliberately
    # modest for a 2-worker-per-color demo pool at ~0.25s/job (~8 jobs/s
    # combined) -- large enough that ordinary traffic never hits it, small
    # enough that loadgen/burst_load.py's burst reliably exceeds it instead
    # of racing the workers to drain first.
    queue_max_depth: int = 150
    # Simulated AI inference cost per job, in seconds.
    job_latency_seconds: float = 0.25


@lru_cache
def get_settings() -> Settings:
    return Settings()
