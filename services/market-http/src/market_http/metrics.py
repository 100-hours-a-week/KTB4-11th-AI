import asyncio
import time
from collections.abc import AsyncIterator

from fastapi import Request
from ktb_core.logging import get_logger


class SSEConnectionMetrics:
    def __init__(
        self,
        *,
        namespace: str,
        cluster_name: str,
        service_name: str,
        interval: float = 60,
    ) -> None:
        self.namespace = namespace
        self.cluster_name = cluster_name
        self.service_name = service_name
        self.interval = interval
        self.active_connections = 0

    async def publish(self) -> None:
        while True:
            get_logger(__name__).info(
                "active_sse_connections",
                _aws={
                    "Timestamp": int(time.time() * 1000),
                    "CloudWatchMetrics": [
                        {
                            "Namespace": self.namespace,
                            "Dimensions": [["ClusterName", "ServiceName"]],
                            "Metrics": [{"Name": "ActiveSSEConnections", "Unit": "Count"}],
                        }
                    ],
                },
                ClusterName=self.cluster_name,
                ServiceName=self.service_name,
                ActiveSSEConnections=self.active_connections,
            )
            await asyncio.sleep(self.interval)


async def track_sse_connection(request: Request) -> AsyncIterator[None]:
    metrics: SSEConnectionMetrics | None = request.app.state.sse_metrics
    if metrics is None:
        yield
        return
    metrics.active_connections += 1
    try:
        yield
    finally:
        metrics.active_connections -= 1
