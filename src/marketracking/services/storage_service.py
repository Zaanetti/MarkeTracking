from minio import Minio

from marketracking.core.config import get_settings


class StorageService:
    """Wrapper simples para futura integracao com S3/MinIO."""

    def __init__(self) -> None:
        settings = get_settings()
        self.bucket = settings.storage_bucket
        self.client = Minio(
            endpoint=settings.storage_endpoint,
            access_key=settings.storage_access_key,
            secret_key=settings.storage_secret_key,
            secure=settings.storage_secure,
        )

    def get_client(self) -> Minio:
        return self.client
