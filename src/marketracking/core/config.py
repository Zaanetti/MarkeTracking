from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "MarkeTracking"
    app_env: str = "development"
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    project_description: str = (
        "Plataforma para upload, identificacao e extracao de dados de cupons fiscais."
    )

    database_url: str = (
        "postgresql+psycopg://marketracking:marketracking@postgres:5432/marketracking"
    )

    image_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0, le=50 * 1024 * 1024)
    image_max_dimension: int = Field(default=10_000, gt=0, le=20_000)
    image_max_pixels: int = Field(default=40_000_000, gt=0, le=100_000_000)

    portal_connect_timeout: float = Field(default=5.0, gt=0, le=30)
    portal_read_timeout: float = Field(default=15.0, gt=0, le=60)
    portal_max_attempts: int = Field(default=3, ge=1, le=3)
    portal_max_redirects: int = Field(default=3, ge=0, le=5)
    portal_max_response_bytes: int = Field(default=5 * 1024 * 1024, gt=0, le=20 * 1024 * 1024)
    portal_user_agent: str = "MarkeTracking/0.1 NFC-e consultation"

    storage_endpoint: str = "minio:9000"
    storage_access_key: str = "minioadmin"
    storage_secret_key: str = "minioadmin"
    storage_bucket: str = "receipts"
    storage_secure: bool = False
    failed_html_encryption_key: str = ""
    failed_html_retention_days: int = Field(default=30, ge=1, le=30)

    @field_validator("failed_html_encryption_key")
    @classmethod
    def validate_failed_html_encryption_key(cls, value: str) -> str:
        if value:
            try:
                Fernet(value.encode("ascii"))
            except (ValueError, UnicodeEncodeError) as exc:
                raise ValueError("FAILED_HTML_ENCRYPTION_KEY must be a valid Fernet key") from exc
        return value

    @model_validator(mode="after")
    def validate_production_settings(self) -> Settings:
        if self.app_env.lower() != "production":
            return self
        if self.app_debug:
            raise ValueError("APP_DEBUG must be false in production")
        if not self.failed_html_encryption_key:
            raise ValueError("FAILED_HTML_ENCRYPTION_KEY is required in production")
        database_url = make_url(self.database_url)
        if database_url.username == "marketracking" and database_url.password == "marketracking":
            raise ValueError("DATABASE_URL must not use the development credentials in production")
        if self.storage_access_key == "minioadmin" or self.storage_secret_key == "minioadmin":
            raise ValueError("Storage credentials must be changed in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
