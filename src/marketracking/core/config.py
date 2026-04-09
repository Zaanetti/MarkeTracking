from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "MarkeTracking"
    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    project_description: str = (
        "Plataforma para upload, identificacao e extracao de dados de cupons fiscais."
    )

    database_url: str = (
        "postgresql+psycopg://marketracking:marketracking@postgres:5432/marketracking"
    )

    storage_endpoint: str = "minio:9000"
    storage_access_key: str = "minioadmin"
    storage_secret_key: str = "minioadmin"
    storage_bucket: str = "receipts"
    storage_secure: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
