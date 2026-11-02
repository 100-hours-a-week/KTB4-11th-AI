import asyncio
import errno
import json
import signal

import pytest
from market_http.market_subscription import MarketSubscription
from redis.asyncio import Redis


class EventLoopBlocked(BaseException):
    pass


def bulk(value: str) -> bytes:
    encoded = value.encode()
    return b"$%d\r\n%s\r\n" % (len(encoded), encoded)


class RespServer:
    def __init__(self) -> None:
        self.writers: list[asyncio.StreamWriter] = []
        self.subscriptions = 0
        self.subscribed = asyncio.Condition()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        try:
            while line := await reader.readline():
                count = int(line[1:])
                args = []
                for _ in range(count):
                    size = int((await reader.readline())[1:])
                    args.append((await reader.readexactly(size + 2))[:-2].decode())
                command = args[0].upper()
                if command == "HELLO":
                    writer.write(b"%1\r\n" + bulk("proto") + b":3\r\n")
                elif command == "SUBSCRIBE":
                    for index, channel in enumerate(args[1:], start=1):
                        writer.write(
                            b">3\r\n" + bulk("subscribe") + bulk(channel) + b":%d\r\n" % index
                        )
                    async with self.subscribed:
                        self.subscriptions += 1
                        self.subscribed.notify_all()
                else:
                    writer.write(b"+OK\r\n")
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    def publish(self, channel: str, data: str) -> None:
        self.writers[-1].write(b">3\r\n" + bulk("message") + bulk(channel) + bulk(data))

    async def wait_for_subscriptions(self, count: int) -> None:
        async with self.subscribed:
            await self.subscribed.wait_for(lambda: self.subscriptions >= count)


class CapturingRedis(Redis):
    def pubsub(self, **kwargs):
        self.captured = super().pubsub(**kwargs)
        return self.captured


def interrupt_blocked_loop(signum, frame):
    raise EventLoopBlocked


def test_pubsub_reconnects_after_kernel_etimedout_without_blocking_event_loop():
    async def scenario():
        server = RespServer()
        listener = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        valkey = CapturingRedis(host="127.0.0.1", port=port, decode_responses=True)
        subscription = MarketSubscription(valkey)
        try:
            async with subscription.run(), subscription.subscribe(["price:005930"]) as queue:
                await server.wait_for_subscriptions(1)
                reader = valkey.captured.connection._reader
                reader.set_exception(TimeoutError(errno.ETIMEDOUT, "Connection timed out"))

                await asyncio.wait_for(server.wait_for_subscriptions(2), 2)
                data = json.dumps({"code": "005930"})
                server.publish("market:prices", data)
                event = await asyncio.wait_for(queue.get(), 2)
                assert event is not None and event.data == data
        finally:
            await valkey.aclose()
            listener.close()
            await listener.wait_closed()

    previous = signal.signal(signal.SIGALRM, interrupt_blocked_loop)
    signal.setitimer(signal.ITIMER_REAL, 5)
    try:
        asyncio.run(scenario())
    except EventLoopBlocked:
        pytest.fail("Pub/Sub listener spun on ETIMEDOUT without yielding the event loop")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
