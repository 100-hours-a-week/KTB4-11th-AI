import asyncio
import logging

import pytest
from market_http.observability.memory import current_rss_bytes
from market_http.observability.queues import QueueMetrics
from market_http.observability.redis import SubscriptionObserver, observe_pubsub
from prometheus_client import CollectorRegistry
from redis.asyncio.client import PubSub


class FakeEncoder:
    decode_responses = True


class FakePool:
    connection_kwargs = {"decode_responses": True}

    def __init__(self, connection):
        self.connection = connection

    async def get_connection(self):
        return self.connection


class FakeRetry:
    def get_retries(self):
        return 3


class FakeConnection:
    def __init__(self):
        self.retry = FakeRetry()
        self.db = 0
        self.host = "localhost"
        self.port = 6379
        self.credential_provider = None
        self.callbacks = []
        self.calls = []
        self.connected = False
        self.failure = None

    @property
    def is_connected(self):
        return self.connected

    def register_connect_callback(self, callback):
        self.callbacks.append(callback)

    def deregister_connect_callback(self, callback):
        if callback in self.callbacks:
            self.callbacks.remove(callback)

    async def connect(self):
        self.calls.append("connect")
        if self.failure is not None:
            raise self.failure
        self.connected = True

    async def disconnect(self, **kwargs):
        self.calls.append(("disconnect", kwargs))
        self.connected = False


def records(caplog, event):
    return [record for record in caplog.records if record.message == event]


def test_adapter_preserves_retry_calls_arguments_and_exception_identity():
    async def scenario():
        connection = FakeConnection()
        pubsub = PubSub(FakePool(connection), encoder=FakeEncoder())
        original_retry = connection.retry
        observer = SubscriptionObserver(listener_id="listener", task_id=None, queues=None)
        observe_pubsub(pubsub, observer)

        error = ConnectionError("redacted")
        connection.failure = error
        with pytest.raises(ConnectionError) as caught:
            await pubsub._reconnect(connection, error, 2, 1.0, "READ")
        assert caught.value is error
        assert connection.calls == [("disconnect", {"error": error, "failure_count": 2}), "connect"]
        assert connection.retry is original_retry

        cancelled = asyncio.CancelledError()
        connection.failure = cancelled
        with pytest.raises(asyncio.CancelledError) as caught_cancel:
            await pubsub._reconnect(connection, cancelled, 1, 1.0, "READ")
        assert caught_cancel.value is cancelled

    asyncio.run(scenario())


def test_connection_then_both_generation_acks(caplog):
    observer = SubscriptionObserver(listener_id="listener", task_id="task", queues=None)
    with caplog.at_level(logging.INFO):
        observer.transition("reconnect_started", reason="connection_lost", error=ConnectionError())
        observer.transition("connected", reason="connected")
        observer.transition("resubscribing", reason="on_connect")
        observer.acknowledge("market:prices")
        observer.acknowledge("market:prices")
        observer.acknowledge("unknown")
        assert not records(caplog, "market_subscription_recovered")
        observer.acknowledge("market:orderbooks")

    events = [
        record.message
        for record in caplog.records
        if record.message.startswith("market_subscription_")
    ]
    assert events == [
        "market_subscription_reconnect_started",
        "market_subscription_connected",
        "market_subscription_resubscribing",
        "market_subscription_recovered",
    ]


def test_known_acks_without_reconnect_generation_do_not_start_recovery(caplog):
    observer = SubscriptionObserver(listener_id="listener", task_id=None, queues=None)
    with caplog.at_level(logging.INFO):
        observer.ready()
        observer.acknowledge("market:prices")
        observer.acknowledge("market:orderbooks")

    assert not records(caplog, "market_subscription_resubscribing")
    assert not records(caplog, "market_subscription_recovered")


def test_reconnect_and_resubscribe_failure_are_distinct(caplog):
    observer = SubscriptionObserver(listener_id="listener", task_id=None, queues=None)
    with caplog.at_level(logging.INFO):
        observer.transition(
            "reconnect_failed", reason="connect_failed", error=ConnectionError("secret")
        )
        observer.transition(
            "resubscribe_failed", reason="resubscribe_failed", error=TimeoutError("payload")
        )

    reconnect, resubscribe = [record.fields for record in caplog.records]
    assert reconnect["phase"] == "connection"
    assert reconnect["error_type"] == "ConnectionError"
    assert resubscribe["phase"] == "resubscribe"
    assert resubscribe["error_type"] == "TimeoutError"
    assert not records(caplog, "market_subscription_ready")
    assert not records(caplog, "market_subscription_recovered")


def test_shutdown_and_noop_connect_do_not_emit_outage(caplog):
    async def scenario():
        connection = FakeConnection()
        connection.connected = True
        pubsub = PubSub(FakePool(connection), encoder=FakeEncoder())
        pubsub.connection = connection
        observer = SubscriptionObserver(listener_id="listener", task_id=None, queues=None)
        observe_pubsub(pubsub, observer)
        await pubsub.connect()

    with caplog.at_level(logging.INFO):
        asyncio.run(scenario())
    assert not records(caplog, "market_subscription_reconnect_started")
    assert not records(caplog, "market_subscription_reconnect_failed")


def test_transition_fields_safe_without_task_or_proc(monkeypatch, caplog):
    monkeypatch.setattr("market_http.observability.redis.current_rss_bytes", lambda: None)
    queues = QueueMetrics(CollectorRegistry())
    observer = SubscriptionObserver(listener_id="listener", task_id=None, queues=queues)
    with caplog.at_level(logging.INFO):
        observer.transition(
            "connected",
            reason="connected",
            error=ConnectionError("redis://user:pass@host payload-contents"),
        )

    fields = caplog.records[-1].fields
    assert fields["listener_id"] == "listener"
    assert fields["task_id"] is None
    assert fields["pid"] > 0
    assert fields["queues"] == 0
    assert fields["rss_bytes"] is None
    assert fields["error_type"] == "ConnectionError"
    assert "redis://" not in str(fields)
    assert "payload-contents" not in str(fields)


def test_current_rss_returns_none_when_statm_is_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("unsupported")

    monkeypatch.setattr("builtins.open", unavailable)
    assert current_rss_bytes() is None
