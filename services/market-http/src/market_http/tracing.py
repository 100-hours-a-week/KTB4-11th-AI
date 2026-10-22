from collections import deque
from contextvars import ContextVar

from opentelemetry.trace import Span

PENDING_SPANS: ContextVar[deque[Span | None] | None] = ContextVar("pending_spans", default=None)


class SSESendSpans:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].endswith("/stream"):
            return await self.app(scope, receive, send)
        pending = deque()
        token = PENDING_SPANS.set(pending)

        async def send_then_end(message):
            await send(message)
            body = message.get("body", b"")
            if (
                message["type"] == "http.response.body"
                and body
                and not body.startswith(b":")
                and pending
            ):
                span = pending.popleft()
                if span is not None:
                    span.end()

        try:
            await self.app(scope, receive, send_then_end)
        finally:
            PENDING_SPANS.reset(token)
            for span in pending:
                if span is not None:
                    span.end()
