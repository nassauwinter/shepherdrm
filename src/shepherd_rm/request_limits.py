"""Enforce transport-level request limits before endpoint processing."""

from __future__ import annotations

import logging
from collections import deque

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from shepherd_rm.models import Problem
from shepherd_rm.request_context import select_correlation_id

LOGGER = logging.getLogger(__name__)


class RequestBodyLimitMiddleware:
    """Buffer request bodies up to a fixed byte limit before endpoint processing."""

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        correlation_id = select_correlation_id(headers.get("x-correlation-id"))
        content_length = self._content_length(headers.get("content-length"))
        if content_length is not None and content_length > self.max_body_bytes:
            await self._reject(scope, receive, send, correlation_id)
            return

        received_bytes = 0
        buffered_messages: deque[Message] = deque()
        while True:
            message = await receive()
            buffered_messages.append(message)
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_body_bytes:
                    await self._reject(scope, receive, send, correlation_id)
                    return
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break

        async def replay_receive() -> Message:
            if buffered_messages:
                return buffered_messages.popleft()
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    def _content_length(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        correlation_id: str,
    ) -> None:
        problem = Problem(
            title="Payload too large",
            status=413,
            detail="The request body exceeds the configured size limit",
            correlation_id=correlation_id,
        )
        response = JSONResponse(
            status_code=413,
            content=problem.model_dump(exclude_none=True),
            headers={"x-correlation-id": correlation_id},
            media_type="application/problem+json",
        )
        LOGGER.info(
            "request rejected",
            extra={
                "correlation_id": correlation_id,
                "method": scope.get("method"),
                "path": scope.get("path"),
                "status_code": 413,
            },
        )
        await response(scope, receive, send)
