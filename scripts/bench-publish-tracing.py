import asyncio
import os
import resource
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta

from market_streamer.stream import publish
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON
from redis.asyncio import Redis

MODES = ("off", "sampled-out", "sampled-in")
WARMUP = 50
ITERATIONS = 1000
BASE = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)


def make_prices(round_number: int) -> list[dict[str, object]]:
    return [
        {
            "code": f"{code:06d}",
            "price": 70000 + code,
            "change": 100,
            "rate": 0.14,
            "volume": 1000 + round_number,
            "trade_value": 70000 * (1000 + round_number),
            "timestamp": (BASE + timedelta(seconds=round_number)).isoformat(),
        }
        for code in range(1, 27)
    ]


async def main(mode: str) -> None:
    if mode != "off":
        provider = TracerProvider(sampler=ALWAYS_ON if mode == "sampled-in" else ALWAYS_OFF)
        provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
        trace.set_tracer_provider(provider)
    valkey = Redis.from_url(os.environ["KTB_TEST_VALKEY_URL"])
    await valkey.flushdb()
    for round_number in range(WARMUP):
        await publish(valkey, make_prices(round_number))
    samples = []
    for round_number in range(WARMUP, WARMUP + ITERATIONS):
        prices = make_prices(round_number)
        start = time.perf_counter()
        await publish(valkey, prices)
        samples.append((time.perf_counter() - start) * 1000)
    await valkey.aclose()
    samples.sort()
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(
        f"mode={mode} mean_ms={statistics.mean(samples):.3f} "
        f"p95_ms={samples[int(len(samples) * 0.95) - 1]:.3f} max_rss_mb={rss_mb:.1f}"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in MODES:
        sys.exit(f"usage: bench-publish-tracing.py {'|'.join(MODES)}")
    asyncio.run(main(sys.argv[1]))
