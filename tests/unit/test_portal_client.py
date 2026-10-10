import httpx
import pytest

from marketracking.schemas.nfce import ErrorCode
from marketracking.services.consultation_url import validate_consultation_url
from marketracking.services.errors import ProcessingFailure
from marketracking.services.portal_client import SefazSpPortalClient


def public_resolver(_host: str, _port: int) -> list[str]:
    return ["8.8.8.8"]


def test_fetches_official_html_and_records_attempt_callbacks(consultation_url: str) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><body>Consulta NFC-e</body></html>",
            request=request,
        )
    )
    events: list[tuple[str, int]] = []
    client = SefazSpPortalClient(
        resolver=public_resolver,
        transport_factory=lambda: transport,
        sleep=lambda _: None,
    )

    html = client.fetch(
        validate_consultation_url(consultation_url),
        on_attempt_start=lambda attempt: events.append(("start", attempt)),
        on_attempt_finish=lambda attempt, _error: events.append(("finish", attempt)),
    )

    assert "Consulta NFC-e" in html
    assert events == [("start", 1), ("finish", 1)]


def test_follows_only_validated_same_host_redirect(consultation_url: str) -> None:
    responses = iter(
        [
            lambda request: httpx.Response(
                302,
                headers={
                    "location": consultation_url.replace(
                        "/NFCeConsultaPublica/Paginas/ConsultaQRCode.aspx", "/qrcode"
                    )
                },
                request=request,
            ),
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html>ok</html>",
                request=request,
            ),
        ]
    )
    transport = httpx.MockTransport(lambda request: next(responses)(request))
    client = SefazSpPortalClient(
        resolver=public_resolver,
        transport_factory=lambda: transport,
        sleep=lambda _: None,
    )

    assert client.fetch(validate_consultation_url(consultation_url)) == "<html>ok</html>"


def test_rejects_redirect_to_unapproved_host(consultation_url: str) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302,
            headers={"location": "https://example.com/"},
            request=request,
        )
    )
    client = SefazSpPortalClient(
        resolver=public_resolver,
        transport_factory=lambda: transport,
        sleep=lambda _: None,
    )

    with pytest.raises(ProcessingFailure) as error:
        client.fetch(validate_consultation_url(consultation_url))

    assert error.value.code == ErrorCode.INVALID_CONSULTATION_URL


def test_retries_transient_http_status(consultation_url: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        status = 503 if calls == 1 else 200
        return httpx.Response(
            status,
            headers={"content-type": "text/html"},
            text="<html>indisponível</html>" if status == 503 else "<html>ok</html>",
            request=request,
        )

    sleeps: list[float] = []
    client = SefazSpPortalClient(
        resolver=public_resolver,
        transport_factory=lambda: httpx.MockTransport(handler),
        sleep=sleeps.append,
        jitter=lambda _low, _high: 0.0,
    )

    html = client.fetch(validate_consultation_url(consultation_url))

    assert html == "<html>ok</html>"
    assert calls == 2
    assert sleeps == [0.5]


def test_rejects_private_dns_resolution_before_request(consultation_url: str) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, request=request)

    client = SefazSpPortalClient(
        resolver=lambda _host, _port: ["127.0.0.1"],
        transport_factory=lambda: httpx.MockTransport(handler),
        sleep=lambda _: None,
    )

    with pytest.raises(ProcessingFailure) as error:
        client.fetch(validate_consultation_url(consultation_url))

    assert error.value.code == ErrorCode.INVALID_CONSULTATION_URL
    assert not called
