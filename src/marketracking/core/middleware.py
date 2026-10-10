from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


BODY_TOO_LARGE_MESSAGE = "O corpo da requisição excede o limite permitido."


class RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = next(
            (value for name, value in scope.get("headers", []) if name.lower() == b"content-length"),
            None,
        )
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = 0
            if declared_size > self.max_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={"detail": BODY_TOO_LARGE_MESSAGE},
                )
                await response(scope, receive, send)
                return

        received_size = 0

        async def limited_receive():
            nonlocal received_size
            message = await receive()
            if message["type"] == "http.request":
                received_size += len(message.get("body", b""))
                if received_size > self.max_bytes:
                    raise RequestBodyTooLarge
            return message

        await self.app(scope, limited_receive, send)
