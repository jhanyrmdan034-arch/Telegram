"""Typed application configuration, sourced exclusively from environment variables."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://messenger:messenger@localhost:5432/messenger"
    db_pool_size: int = 10
    db_max_overflow: int = 10

    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = Field(default="dev-insecure-secret-change-me", min_length=8)
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 2_592_000

    storage_backend: str = "local"
    storage_dir: Path = Path("/var/lib/messenger/files")
    max_file_size_bytes: int = 200 * 1024 * 1024
    public_base_url: str = "http://localhost:8000"

    rate_limit_enabled: bool = True
    rate_limit_requests: int = 300
    rate_limit_window_seconds: int = 60
    login_rate_limit_requests: int = 10
    login_rate_limit_window_seconds: int = 300

    # TURN/SFU credentials handed to clients for call media relay.
    turn_url: str | None = None
    turn_username: str | None = None
    turn_password: str | None = None

    # Web admin console (static SPA, served by this API process at /admin)
    admin_dir: Path = Path(__file__).resolve().parent.parent.parent / "admin"

    bootstrap_admin_username: str | None = None

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        if value.startswith("postgresql://"):
            raise ValueError("DATABASE_URL must use the asyncpg driver: postgresql+asyncpg://")
        return value

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    def validate_for_production(self) -> None:
        """Fail fast instead of silently running production with dev defaults."""
        if not self.is_production:
            return
        problems = []
        if self.jwt_secret == "dev-insecure-secret-change-me":
            problems.append("JWT_SECRET is still the development default")
        if len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be at least 32 characters in production")
        if "CHANGE_ME" in self.database_url:
            problems.append("DATABASE_URL still contains a placeholder password")
        if self.debug:
            problems.append("DEBUG must be false in production")
        if problems:
            raise RuntimeError("Invalid production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
