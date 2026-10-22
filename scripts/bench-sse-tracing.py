import argparse
import asyncio
import json
import os
import random
import resource
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from redis.asyncio import Redis

MODES = ("off", "sampled-out", "sampled-in")
CODES = [f"{code:06d}" for code in range(1, 27)]
PORT = 18001


def price(code: str, volume: int, trace_fields: dict[str, str]) -> str:
    return json.dumps(
        {
            "code": code,
            "price": 70000,
            "change": 100,
            "rate": 0.14,
            "volume": volume,
            "timestamp": "2026-10-22T10:00:00+09:00",
        }
        | trace_fields
    )


def serve(valkey_url: str, clients: int, spans_per_second: int) -> None:
    import uvicorn
    from ktb_telemetry.tracing import tracing
    from market_http.app import create_app
    from market_http.repositories import prices

    prices.questdb_prices = lambda conf, codes: []
    prices.current_day_price_limits = lambda conf, codes: {
        code: {"upper_limit_price": 91000, "lower_limit_price": 49000} for code in codes
    }
    app = create_app(
        Redis.from_url(valkey_url, decode_responses=True, max_connections=clients * 3 + 10),
        "unused",
        trace_spans_per_second=spans_per_second,
    )
    with tracing("market-http"):
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning", log_config=None)


class Sink(BaseHTTPRequestHandler):
    deliver_spans = 0

    def do_POST(self):
        request = ExportTraceServiceRequest.FromString(
            self.rfile.read(int(self.headers["Content-Length"]))
        )
        Sink.deliver_spans += sum(
            span.name == "deliver"
            for resource_spans in request.resource_spans
            for scope_spans in resource_spans.scope_spans
            for span in scope_spans.spans
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


async def client(http: httpx.AsyncClient, sent: dict[int, float], latencies: list[float], ready):
    snapshot = 0
    async with http.stream(
        "GET", f"http://127.0.0.1:{PORT}/prices/stream?codes={','.join(CODES)}"
    ) as response:
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            volume = json.loads(line[6:])["volume"]
            if volume == 0:
                snapshot += 1
                if snapshot == len(CODES):
                    ready.release()
            else:
                latencies.append((time.monotonic() - sent[volume]) * 1000)


async def run(args: argparse.Namespace) -> dict[str, object]:
    valkey = Redis.from_url(args.valkey_url, decode_responses=True)
    await valkey.flushdb()
    for code in CODES:
        await valkey.set(f"price:{code}", price(code, 0, {}))

    async with httpx.AsyncClient(timeout=None, limits=httpx.Limits(max_connections=None)) as http:
        for _ in range(100):
            try:
                await http.get(f"http://127.0.0.1:{PORT}/health")
                break
            except httpx.TransportError:
                await asyncio.sleep(0.1)
        sent: dict[int, float] = {}
        latencies: list[float] = []
        ready = asyncio.Semaphore(0)
        clients = [
            asyncio.create_task(client(http, sent, latencies, ready)) for _ in range(args.clients)
        ]
        async with asyncio.timeout(60):
            for _ in range(args.clients):
                await ready.acquire()

        rng = random.Random(args.seed)
        traced = 0
        start = time.monotonic()
        for volume in range(1, args.messages + 1):
            await asyncio.sleep(max(0.0, start + volume / args.rate - time.monotonic()))
            trace_fields = {}
            if args.mode == "sampled-in" and rng.random() < args.ratio:
                traced += 1
                trace_fields = {
                    "traceparent": f"00-{rng.getrandbits(128):032x}-{rng.getrandbits(64):016x}-01",
                    "published_at": datetime.now(UTC).isoformat(),
                }
            sent[volume] = time.monotonic()
            await valkey.publish(
                f"price:{CODES[volume % len(CODES)]}",
                price(CODES[volume % len(CODES)], volume, trace_fields),
            )
        deadline = time.monotonic() + 30
        while len(latencies) < args.messages * args.clients and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        for task in clients:
            task.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
    await valkey.aclose()
    if not latencies:
        raise SystemExit("no SSE events received")
    latencies.sort()
    return {
        "received": len(latencies),
        "expected": args.messages * args.clients,
        "traced_messages": traced,
        "p50_ms": round(latencies[len(latencies) // 2], 3),
        "p95_ms": round(latencies[int(len(latencies) * 0.95) - 1], 3),
        "p99_ms": round(latencies[int(len(latencies) * 0.99) - 1], 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("--clients", type=int, default=100)
    parser.add_argument("--rate", type=float, default=20)
    parser.add_argument("--messages", type=int, default=600)
    parser.add_argument("--ratio", type=float, default=0.01)
    parser.add_argument("--spans-per-second", type=int, default=100)
    parser.add_argument("--seed", type=int, default=368)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.valkey_url = os.environ["KTB_TEST_VALKEY_URL"]
    if args.serve:
        serve(args.valkey_url, args.clients, args.spans_per_second)
        return

    sink = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    threading.Thread(target=sink.serve_forever, daemon=True).start()
    env = {name: value for name, value in os.environ.items() if not name.startswith("OTEL_")}
    if args.mode != "off":
        env |= {
            "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{sink.server_port}",
            "OTEL_TRACES_SAMPLER": "parentbased_traceidratio",
            "OTEL_TRACES_SAMPLER_ARG": "0",
        }
    server = subprocess.Popen([sys.executable, __file__, *sys.argv[1:], "--serve"], env=env)
    try:
        result = asyncio.run(run(args))
    finally:
        server.send_signal(signal.SIGINT)
        server.wait(30)
    sink.shutdown()
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    deliveries = result["traced_messages"] * args.clients
    result |= {
        "spans_recorded": Sink.deliver_spans,
        "spans_not_exported": deliveries - Sink.deliver_spans,
        "server_cpu_s": round(usage.ru_utime + usage.ru_stime, 2),
        "server_max_rss_mb": round(usage.ru_maxrss / 1024, 1),
    }
    policy = {
        key: getattr(args, key)
        for key in ("mode", "clients", "rate", "messages", "ratio", "spans_per_second", "seed")
    }
    print(json.dumps(policy | result))


if __name__ == "__main__":
    main()
