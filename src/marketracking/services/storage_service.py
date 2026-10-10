from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import UUID

from bs4 import BeautifulSoup
from cryptography.fernet import Fernet
from minio import Minio
from minio.commonconfig import Filter
from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule

from marketracking.core.config import get_settings


@dataclass(frozen=True)
class StoredHtmlArtifact:
    object_key: str
    sha256: str
    expires_at: datetime


class StorageService:
    def __init__(self, client: Minio | None = None) -> None:
        settings = get_settings()
        self.bucket = settings.storage_bucket
        self.retention_days = settings.failed_html_retention_days
        key = settings.failed_html_encryption_key
        self._cipher = Fernet(key.encode("ascii")) if key else None
        self.client = client or Minio(
            endpoint=settings.storage_endpoint,
            access_key=settings.storage_access_key,
            secret_key=settings.storage_secret_key,
            secure=settings.storage_secure,
        )

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
        lifecycle = self.client.get_bucket_lifecycle(self.bucket)
        rules = [
            rule
            for rule in (lifecycle.rules if lifecycle is not None else [])
            if rule.rule_id != "failed-html-retention"
        ]
        rules.append(
            Rule(
                rule_id="failed-html-retention",
                status="Enabled",
                rule_filter=Filter(prefix="failed/"),
                expiration=Expiration(days=self.retention_days),
            )
        )
        self.client.set_bucket_lifecycle(
            self.bucket,
            LifecycleConfig(rules),
        )

    def store_failed_html(self, processing_id: UUID, html: str) -> StoredHtmlArtifact:
        if self._cipher is None:
            raise ValueError("FAILED_HTML_ENCRYPTION_KEY is required for diagnostic HTML storage")
        sanitized = sanitize_failed_html(html).encode("utf-8")
        encrypted = self._cipher.encrypt(sanitized)
        object_key = f"failed/{processing_id}.html.fernet"
        self.ensure_bucket()
        self.client.put_object(
            self.bucket,
            object_key,
            BytesIO(encrypted),
            length=len(encrypted),
            content_type="application/octet-stream",
            metadata={"sha256": hashlib.sha256(sanitized).hexdigest()},
        )
        return StoredHtmlArtifact(
            object_key=object_key,
            sha256=hashlib.sha256(sanitized).hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(days=self.retention_days),
        )

    def delete_failed_html(self, object_key: str) -> None:
        if object_key.startswith("failed/"):
            self.client.remove_object(self.bucket, object_key)

    def get_client(self) -> Minio:
        return self.client


def sanitize_failed_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for section in soup.select(".jqm-collapsible, [data-role='collapsible']"):
        heading = section.select_one("h1, h2, h3, h4, .ui-collapsible-heading")
        if heading and "consumidor" in _plain_text(heading.get_text(" ", strip=True)):
            section.decompose()

    for element in soup.select("script, input, textarea"):
        element.decompose()

    for element in soup.find_all(True):
        for attribute in ("href", "src", "action", "data-url"):
            value = element.get(attribute)
            if isinstance(value, str) and "?" in value:
                element[attribute] = value.split("?", maxsplit=1)[0]

    cleaned = str(soup)
    cleaned = re.sub(r"(?i)([?&]p=)[^&\s\"'<>]+", r"\1[REDACTED]", cleaned)
    return re.sub(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)", "[REDACTED_CPF]", cleaned)


def _plain_text(value: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii").lower()
