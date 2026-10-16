import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

type Message = dict[str, Any]
type Scope = dict[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class SSEConnectionMetrics:
    def __init__(
        self,
        app: ASGIApp,
        *,
        namespace: str,
        cluster_name: str,
        service_name: str,
        interval: float = 60,
    ) -> None:
        self.app = app
        self.namespace = namespace
        self.cluster_name = cluster_name
        self.service_name = service_name
        self.interval = interval
        self.active_connections = 0

    async def publish(self) -> None:
        while True:
            print(
                json.dumps(
                    {
                        "_aws": {
                            "Timestamp": int(time.time() * 1000),
                            "CloudWatchMetrics": [
                                {
                                    "Namespace": self.namespace,
                                    "Dimensions": [["ClusterName", "ServiceName"]],
                                    "Metrics": [{"Name": "ActiveSSEConnections", "Unit": "Count"}],
                                }
                            ],
                        },
                        "ClusterName": self.cluster_name,
                        "ServiceName": self.service_name,
                        "ActiveSSEConnections": self.active_connections,
                    }
                ),
                flush=True,
            )
            await asyncio.sleep(self.interval)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            publisher = None

            async def lifespan_send(message: Message) -> None:
                nonlocal publisher
                if message["type"] == "lifespan.startup.complete":
                    publisher = asyncio.create_task(self.publish())
                await send(message)

            try:
                await self.app(scope, receive, lifespan_send)
            finally:
                if publisher is not None:
                    publisher.cancel()
                    with suppress(asyncio.CancelledError):
                        await publisher
            return

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        counted = False

        async def response_send(message: Message) -> None:
            nonlocal counted
            if message["type"] == "http.response.start" and not counted:
                content_type = dict(message.get("headers", [])).get(b"content-type", b"")
                if content_type.split(b";", 1)[0].strip().lower() == b"text/event-stream":
                    self.active_connections += 1
                    counted = True
            await send(message)

        try:
            await self.app(scope, receive, response_send)
        finally:
            if counted:
                self.active_connections -= 1
