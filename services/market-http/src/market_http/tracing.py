from collections import deque
from contextvars import ContextVar

from opentelemetry.trace import Span

PENDING_SPANS: ContextVar[deque[Span | None] | None] = ContextVar("pending_spans", default=None)


class SlidingWindowLimiter:
    def __init__(self, per_second: int):
        self.per_second = per_second
        self.second = 0
        self.current = 0
        self.previous = 0

    def allow(self, now: float) -> bool:
        second = int(now)
        if second != self.second:
            self.previous = self.current if second == self.second + 1 else 0
            self.current = 0
            self.second = second
        if self.previous * (1 - (now - second)) + self.current >= self.per_second:
            return False
        self.current += 1
        return True


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
