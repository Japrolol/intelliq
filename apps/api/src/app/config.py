"""Environment configuration; secret fields are excluded from representations."""

from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "postgresql+psycopg://intelliq:intelliq_dev_only@127.0.0.1:5434/intelliq"
INTELLIQ_WORKSPACE_ORIGIN = "http://intelliq.localhost:8082"


class Settings(BaseSettings):
    """Configuration loaded from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(env_file=(".env", "apps/api/.env"), extra="ignore")

    data_mode: str = Field(default="fixture", pattern="^(fixture|live)$")
    timecue_api_url: str = "http://localhost:8000"
    # SQLite remains available when an isolated test passes it explicitly.
    database_url: str = Field(default=DEFAULT_DATABASE_URL, min_length=1)
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    cookie_secure: bool = False
    cookie_samesite: str = Field(default="lax", pattern="^(lax|strict|none)$")
    cookie_domain: str | None = None
    timecue_access_cookie: str = "access_token"
    timecue_refresh_cookie: str = "refresh_token"
    simulation_default_samples: int = Field(default=100, ge=1, le=500)
    simulation_max_samples: int = Field(default=500, ge=1, le=500)
    simulation_max_projects: int = Field(default=0, ge=0)
    simulation_max_workers: int = Field(default=0, ge=0)
    simulation_max_tasks: int = Field(default=0, ge=0)
    redis_url: str = "redis://127.0.0.1:6381/0"
    jobs_enabled: bool = False
    simulation_process_enabled: bool = True
    simulation_timeout_seconds: float = Field(default=180, gt=0, le=480)
    session_encryption_key: str = Field(default="", repr=False)
    weatherapi_api_key: str = Field(default="", repr=False)
    openrouteservice_api_key: str = Field(default="", repr=False)
    weather_forecast_days: int = Field(default=7, ge=1, le=14)
    llm_api_key: str = Field(
        default="", repr=False, validation_alias=AliasChoices("LLM_API_KEY", "OPENAI_API_KEY")
    )
    llm_api_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4.1-mini"
    nlp_enabled: bool = True
    allow_external_text_processing: bool = False
    upload_dir: str = "./data/uploads"
    s3_endpoint_url: str = "http://127.0.0.1:19000"
    s3_bucket: str = "intelliq-uploads"
    s3_region: str = "us-east-1"
    s3_access_key_id: str = Field(default="", repr=False)
    s3_secret_access_key: str = Field(default="", repr=False)
    s3_create_bucket: bool = False

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        """Reject an empty database URL instead of silently selecting another backend."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("DATABASE_URL must not be empty")
        return normalized

    @property
    def origin_allowlist(self) -> frozenset[str]:
        """Return normalized origins accepted for browser mutations."""

        origins = {
            origin.strip().rstrip("/")
            for origin in self.allowed_origins.split(",")
            if origin.strip()
        }
        origins.add(INTELLIQ_WORKSPACE_ORIGIN)
        return frozenset(origins)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load process configuration once for the default application instance."""

    return Settings()


__all__ = ["DEFAULT_DATABASE_URL", "INTELLIQ_WORKSPACE_ORIGIN", "Settings", "get_settings"]
