import json
import time
from contextlib import contextmanager

from prometheus_client import CollectorRegistry, Counter, Gauge, start_http_server


class MarketMetrics:
    def __init__(self):
        self.registry = CollectorRegistry()
        self.events = Counter(
            "market_streamer_market_events_received",
            "Original market entries before validation",
            ["kind"],
            registry=self.registry,
        )
        self.bytes = Counter(
            "market_streamer_market_events_received_bytes",
            "Original entries re-encoded as compact UTF-8 JSON; excludes socket framing",
            ["kind"],
            registry=self.registry,
        )
        self.age = Gauge(
            "market_streamer_market_data_age_seconds",
            "Seconds since last original entry; NaN before first receipt",
            ["kind"],
            registry=self.registry,
        )
        self.last_received: dict[str, float] = {}
        for kind in ("price", "orderbook"):
            self.events.labels(kind)
            self.bytes.labels(kind)
            self.age.labels(kind).set_function(
                lambda kind=kind: time.monotonic() - self.last_received.get(kind, float("nan"))
            )

    def receive(self, entries) -> None:
        received = time.monotonic()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            kind = {"0B": "price", "0D": "orderbook"}.get(entry.get("type"))
            if kind is None:
                continue
            self.events.labels(kind).inc()
            self.bytes.labels(kind).inc(
                len(json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            )
            self.last_received[kind] = received

    @contextmanager
    def serve(self, host: str, port: int):
        server, thread = start_http_server(port, addr=host, registry=self.registry)
        try:
            yield server
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
