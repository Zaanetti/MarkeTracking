import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from marketracking.core.middleware import (
    BODY_TOO_LARGE_MESSAGE,
    RequestBodyLimitMiddleware,
    RequestBodyTooLarge,
)


def test_request_body_limit_rejects_oversized_chunk_without_content_length() -> None:
    inner = FastAPI()

    @inner.exception_handler(RequestBodyTooLarge)
    async def request_body_too_large_handler(_request: Request, _exc: RequestBodyTooLarge):
        return JSONResponse(status_code=413, content={"detail": BODY_TOO_LARGE_MESSAGE})

    @inner.post("/consume")
    async def consume(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    app = RequestBodyLimitMiddleware(inner, max_bytes=4)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/consume",
        "raw_path": b"/consume",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/octet-stream")],
        "server": ("test", 80),
        "client": ("test", 1),
    }
    messages = [{"type": "http.request", "body": b"12345", "more_body": False}]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))

    assert sent[0]["status"] == 413
    assert BODY_TOO_LARGE_MESSAGE.encode() in sent[1]["body"]
