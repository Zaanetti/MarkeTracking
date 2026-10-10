import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from marketracking.core.config import Settings


def _production_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "production",
        "database_url": "postgresql+psycopg://app:private-password@db:5432/marketracking",
        "storage_access_key": "marketracking-app",
        "storage_secret_key": "private-storage-password",
        "failed_html_encryption_key": Fernet.generate_key().decode("ascii"),
    }
    values.update(overrides)
    return Settings(**values)


def test_production_settings_require_encryption_key() -> None:
    with pytest.raises(ValidationError, match="FAILED_HTML_ENCRYPTION_KEY"):
        _production_settings(failed_html_encryption_key="")


def test_production_settings_reject_debug_and_scaffold_credentials() -> None:
    with pytest.raises(ValidationError, match="APP_DEBUG"):
        _production_settings(app_debug=True)
    with pytest.raises(ValidationError, match="development credentials"):
        _production_settings(
            database_url="postgresql+psycopg://marketracking:marketracking@db:5432/marketracking"
        )
    with pytest.raises(ValidationError, match="Storage credentials"):
        _production_settings(storage_secret_key="minioadmin")


def test_production_settings_accept_non_default_secrets() -> None:
    settings = _production_settings()

    assert settings.app_env == "production"
