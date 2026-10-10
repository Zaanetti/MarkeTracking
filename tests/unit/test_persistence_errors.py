import sqlite3

from sqlalchemy.exc import OperationalError

from marketracking.schemas.nfce import ErrorCode
from marketracking.services.persistence_service import persistence_failure


def test_database_lock_is_reported_as_retryable() -> None:
    error = OperationalError("SELECT 1", {}, sqlite3.OperationalError("database is locked"))

    failure = persistence_failure(error)

    assert failure.code == ErrorCode.PERSISTENCE_UNAVAILABLE
    assert failure.transient is True
    assert failure.retryable is True


def test_permanent_database_error_is_not_reported_as_retryable() -> None:
    error = OperationalError("SELECT 1", {}, sqlite3.OperationalError("no such table"))

    failure = persistence_failure(error)

    assert failure.transient is False
    assert failure.retryable is False
