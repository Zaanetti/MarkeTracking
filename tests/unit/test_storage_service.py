from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

from cryptography.fernet import Fernet
from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule

from marketracking.services.storage_service import StorageService, sanitize_failed_html


def test_sanitizer_removes_consumer_and_opaque_urls() -> None:
    html = """
    <html><script>private script state</script>
      <div id="infos">
        <div class="jqm-collapsible"><h4>Consumidor</h4>
          <div class="ui-collapsible-content">CPF 123.456.789-00</div>
        </div>
      </div>
      <a href="https://www.nfce.fazenda.sp.gov.br/qrcode?p=secret-value">consulta</a>
    </html>
    """

    sanitized = sanitize_failed_html(html)

    assert "Consumidor" not in sanitized
    assert "123.456.789-00" not in sanitized
    assert "private script state" not in sanitized
    assert "secret-value" not in sanitized


def test_failed_html_is_encrypted_and_lifecycle_is_configured(monkeypatch) -> None:
    key = Fernet.generate_key()

    class FakeMinio:
        def __init__(self) -> None:
            self.objects: dict[str, bytes] = {}
            self.lifecycle = LifecycleConfig(
                [Rule(rule_id="other-retention", status="Enabled", expiration=Expiration(days=90))]
            )
            self.applied_lifecycle = None
            self.created_bucket = False

        def bucket_exists(self, _bucket: str) -> bool:
            return self.created_bucket

        def make_bucket(self, _bucket: str) -> None:
            self.created_bucket = True

        def get_bucket_lifecycle(self, _bucket: str):
            return self.lifecycle

        def set_bucket_lifecycle(self, _bucket: str, lifecycle) -> None:
            self.applied_lifecycle = lifecycle

        def put_object(self, _bucket, object_key, data: BytesIO, length, **_kwargs) -> None:
            self.objects[object_key] = data.read(length)

    client = FakeMinio()
    monkeypatch.setattr(
        "marketracking.services.storage_service.get_settings",
        lambda: SimpleNamespace(
            storage_bucket="test",
            failed_html_retention_days=30,
            failed_html_encryption_key=key.decode(),
            storage_endpoint="localhost:9000",
            storage_access_key="test",
            storage_secret_key="test",
            storage_secure=False,
        ),
    )
    service = StorageService(client=client)
    processing_id = uuid4()

    artifact = service.store_failed_html(
        processing_id,
        '<html><div class="jqm-collapsible"><h4>Consumidor</h4><p>CPF 123.456.789-00</p></div></html>',
    )

    ciphertext = client.objects[artifact.object_key]
    assert b"Consumidor" not in ciphertext
    assert Fernet(key).decrypt(ciphertext).find(b"Consumidor") == -1
    assert artifact.object_key == f"failed/{processing_id}.html.fernet"
    assert artifact.expires_at > datetime.now(UTC)
    rule_ids = {rule.rule_id for rule in client.applied_lifecycle.rules}
    assert rule_ids == {"other-retention", "failed-html-retention"}
