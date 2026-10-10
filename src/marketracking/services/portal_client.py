from __future__ import annotations

import ipaddress
import random
import socket
import ssl
import time
from collections.abc import Callable, Iterable
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from marketracking.core.config import get_settings
from marketracking.schemas.nfce import ErrorCode, ProcessingState
from marketracking.services.consultation_url import (
    SEFAZ_SP_HOST,
    ValidatedConsultationUrl,
    validate_consultation_url,
)
from marketracking.services.errors import ProcessingFailure


Resolver = Callable[[str, int], Iterable[str]]
Sleep = Callable[[float], None]
AttemptStart = Callable[[int], None]
AttemptFinish = Callable[[int, ProcessingFailure | None], None]


def resolve_host(host: str, port: int) -> list[str]:
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses:
        raise socket.gaierror("DNS returned no addresses")
    return addresses


def _public_addresses(resolver: Resolver, host: str, port: int) -> list[str]:
    if host.lower() != SEFAZ_SP_HOST:
        raise ProcessingFailure(
            ErrorCode.INVALID_CONSULTATION_URL,
            "A consulta foi direcionada para um endereço não autorizado.",
            ProcessingState.VALIDATING_URL,
        )
    try:
        addresses = list(dict.fromkeys(resolver(host, port)))
    except (OSError, socket.gaierror) as exc:
        raise ProcessingFailure(
            ErrorCode.PORTAL_UNAVAILABLE,
            "Não foi possível localizar o portal fiscal neste momento.",
            ProcessingState.FETCHING_PORTAL,
            transient=True,
            retryable=True,
            technical_cause=type(exc).__name__,
        ) from exc

    if not addresses:
        raise ProcessingFailure(
            ErrorCode.PORTAL_UNAVAILABLE,
            "O portal fiscal não possui endereços públicos disponíveis.",
            ProcessingState.FETCHING_PORTAL,
            transient=True,
            retryable=True,
        )

    try:
        parsed = [ipaddress.ip_address(address) for address in addresses]
    except ValueError as exc:
        raise ProcessingFailure(
            ErrorCode.PORTAL_UNAVAILABLE,
            "A resolução do portal fiscal retornou um endereço inválido.",
            ProcessingState.FETCHING_PORTAL,
            technical_cause=type(exc).__name__,
        ) from exc
    if any(not address.is_global for address in parsed):
        raise ProcessingFailure(
            ErrorCode.INVALID_CONSULTATION_URL,
            "A consulta foi direcionada para um endereço de rede não permitido.",
            ProcessingState.VALIDATING_URL,
        )
    return addresses


class _PinnedNetworkBackend(httpcore.SyncBackend):
    def __init__(self, resolver: Resolver) -> None:
        self._resolver = resolver

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.NetworkStream:
        addresses = _public_addresses(self._resolver, host, port)
        address = addresses[0]
        return super().connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class _PinnedHttpTransport(httpx.HTTPTransport):
    """Pin the TCP connection to a validated public DNS answer while retaining TLS SNI."""

    def __init__(self, resolver: Resolver) -> None:
        super().__init__(verify=True, trust_env=False, retries=0)
        self._pool.close()
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=5,
            max_keepalive_connections=2,
            keepalive_expiry=5.0,
            http1=True,
            http2=False,
            network_backend=_PinnedNetworkBackend(resolver),
        )


class SefazSpPortalClient:
    def __init__(
        self,
        *,
        resolver: Resolver = resolve_host,
        transport_factory: Callable[[], httpx.BaseTransport] | None = None,
        sleep: Sleep = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        settings = get_settings()
        self._resolver = resolver
        self._transport_factory = transport_factory
        self._sleep = sleep
        self._jitter = jitter
        self._timeout = httpx.Timeout(
            connect=settings.portal_connect_timeout,
            read=settings.portal_read_timeout,
            write=settings.portal_read_timeout,
            pool=settings.portal_connect_timeout,
        )
        self._max_attempts = settings.portal_max_attempts
        self._max_redirects = settings.portal_max_redirects
        self._max_response_bytes = settings.portal_max_response_bytes
        self._user_agent = settings.portal_user_agent

    def fetch(
        self,
        consultation: ValidatedConsultationUrl,
        *,
        on_attempt_start: AttemptStart | None = None,
        on_attempt_finish: AttemptFinish | None = None,
    ) -> str:
        for attempt in range(1, self._max_attempts + 1):
            if on_attempt_start:
                on_attempt_start(attempt)
            try:
                html = self._fetch_once(consultation.url)
            except ProcessingFailure as exc:
                if on_attempt_finish:
                    on_attempt_finish(attempt, exc)
                if not exc.retryable or attempt == self._max_attempts:
                    raise
                delay = min(0.5 * (2 ** (attempt - 1)), 4.0) + self._jitter(0.0, 0.25)
                self._sleep(delay)
            else:
                if on_attempt_finish:
                    on_attempt_finish(attempt, None)
                return html
        raise AssertionError("portal retry loop exited unexpectedly")

    def _fetch_once(self, url: str) -> str:
        current = url
        transport = self._transport_factory() if self._transport_factory else _PinnedHttpTransport(self._resolver)
        try:
            with httpx.Client(
                transport=transport,
                timeout=self._timeout,
                follow_redirects=False,
                trust_env=False,
                headers={"User-Agent": self._user_agent, "Accept": "text/html,application/xhtml+xml"},
            ) as client:
                for redirect_count in range(self._max_redirects + 1):
                    validated = validate_consultation_url(current)
                    parsed = urlsplit(validated.url)
                    _public_addresses(self._resolver, parsed.hostname or "", parsed.port or 443)
                    with client.stream("GET", validated.url) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("location")
                            if not location or redirect_count == self._max_redirects:
                                raise ProcessingFailure(
                                    ErrorCode.UNEXPECTED_PORTAL_RESPONSE,
                                    "O portal retornou um redirecionamento inválido.",
                                    ProcessingState.VALIDATING_RESPONSE,
                                )
                            next_url = urljoin(validated.url, location)
                            current = validate_consultation_url(next_url).url
                            continue

                        response_body = self._read_limited(response)
                        if response.status_code in (500, 502, 503, 504):
                            raise ProcessingFailure(
                                ErrorCode.PORTAL_UNAVAILABLE,
                                "O portal fiscal está temporariamente indisponível.",
                                ProcessingState.FETCHING_PORTAL,
                                transient=True,
                                retryable=True,
                                technical_cause=f"HTTP{response.status_code}",
                                raw_html=_decode_body(response_body, response.encoding),
                            )
                        if response.status_code == 404:
                            raise ProcessingFailure(
                                ErrorCode.DOCUMENT_NOT_FOUND,
                                "A NFC-e não foi localizada no portal fiscal.",
                                ProcessingState.VALIDATING_RESPONSE,
                                raw_html=_decode_body(response_body, response.encoding),
                            )
                        if response.status_code in (403, 429):
                            raise ProcessingFailure(
                                ErrorCode.PORTAL_BLOCKED,
                                "O portal fiscal bloqueou temporariamente a consulta.",
                                ProcessingState.FETCHING_PORTAL,
                                transient=True,
                                retryable=False,
                                technical_cause=f"HTTP{response.status_code}",
                                raw_html=_decode_body(response_body, response.encoding),
                            )
                        if response.status_code != 200:
                            raise ProcessingFailure(
                                ErrorCode.UNEXPECTED_PORTAL_RESPONSE,
                                "O portal fiscal retornou uma resposta incompatível.",
                                ProcessingState.VALIDATING_RESPONSE,
                                technical_cause=f"HTTP{response.status_code}",
                                raw_html=_decode_body(response_body, response.encoding),
                            )

                        content_type = response.headers.get("content-type", "").lower()
                        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                            raise ProcessingFailure(
                                ErrorCode.UNEXPECTED_PORTAL_RESPONSE,
                                "O portal fiscal não retornou uma página HTML.",
                                ProcessingState.VALIDATING_RESPONSE,
                                raw_html=_decode_body(response_body, response.encoding),
                            )
                        html = _decode_body(response_body, response.encoding)
                        normalized = _normalize_html_text(html)
                        if any(term in normalized for term in ("captcha", "acesso negado", "sou humano")):
                            raise ProcessingFailure(
                                ErrorCode.PORTAL_BLOCKED,
                                "O portal fiscal bloqueou temporariamente a consulta.",
                                ProcessingState.VALIDATING_RESPONSE,
                                transient=True,
                                retryable=False,
                                raw_html=html,
                            )
                        if any(term in normalized for term in ("documento nao encontrado", "nfce nao localizada")):
                            raise ProcessingFailure(
                                ErrorCode.DOCUMENT_NOT_FOUND,
                                "A NFC-e não foi localizada no portal fiscal.",
                                ProcessingState.VALIDATING_RESPONSE,
                                raw_html=html,
                            )
                        return html
        except ProcessingFailure:
            raise
        except (httpx.TimeoutException, httpx.NetworkError, socket.gaierror) as exc:
            raise ProcessingFailure(
                ErrorCode.PORTAL_UNAVAILABLE,
                "Não foi possível consultar o portal fiscal neste momento.",
                ProcessingState.FETCHING_PORTAL,
                transient=True,
                retryable=True,
                technical_cause=type(exc).__name__,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProcessingFailure(
                ErrorCode.PORTAL_UNAVAILABLE,
                "Não foi possível consultar o portal fiscal neste momento.",
                ProcessingState.FETCHING_PORTAL,
                transient=True,
                retryable=True,
                technical_cause=type(exc).__name__,
            ) from exc
        finally:
            transport.close()
        raise AssertionError("redirect loop exited unexpectedly")

    def _read_limited(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > self._max_response_bytes:
                raise ProcessingFailure(
                    ErrorCode.UNEXPECTED_PORTAL_RESPONSE,
                    "A resposta do portal fiscal excede o limite permitido.",
                    ProcessingState.VALIDATING_RESPONSE,
                )
            chunks.append(chunk)
        return b"".join(chunks)


def _decode_body(body: bytes, encoding: str | None) -> str:
    try:
        return body.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _normalize_html_text(html: str) -> str:
    from bs4 import BeautifulSoup

    import unicodedata

    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
