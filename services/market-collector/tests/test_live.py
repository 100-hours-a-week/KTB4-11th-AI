import asyncio
import json
from datetime import UTC, datetime

import numpy as np
import pytest
from market_collector.indicators import INDICATOR_FIELDS
from market_collector.live import (
    TICK_FIELDS,
    Aggregator,
    LiveCandle,
    Tick,
    TickBuffer,
    Window,
    backoff_for,
    candle_row,
    connection_plan,
    drain,
    login_message,
    parse_tick,
    register_message,
    stream,
    ticks_from,
)

DAY = datetime(2026, 9, 25, tzinfo=UTC)


def _values(time="090000", price="+70000", cum_volume="1000", cum_value="70000000"):
    return {
        TICK_FIELDS["time"]: time,
        TICK_FIELDS["price"]: price,
        TICK_FIELDS["cum_volume"]: cum_volume,
        TICK_FIELDS["cum_value"]: cum_value,
    }


def _tick(minute, second=0, price=70000.0, cum_volume=1000, cum_value=7e7, symbol="005930"):
    return Tick(
        symbol=symbol,
        ts=datetime(2026, 9, 25, 0, minute, second, tzinfo=UTC),
        price=price,
        cum_volume=cum_volume,
        cum_value=cum_value,
    )


# --------------------------------------------------------------- parse_tick


def test_parse_tick_reads_kst_time_as_utc_and_strips_the_price_sign():
    tick = parse_tick("005930", _values(time="091500", price="+70500"), DAY)

    # 09:15 KST is 00:15 UTC.
    assert tick.ts == datetime(2026, 9, 25, 0, 15, tzinfo=UTC)
    assert tick.price == 70500.0
    assert tick.symbol == "005930"


@pytest.mark.parametrize("missing", list(TICK_FIELDS.values()))
def test_parse_tick_raises_on_a_missing_field_rather_than_defaulting_to_zero(missing):
    # A candle built from zeros is indistinguishable from a minute in which the
    # price really was zero, so an unreadable tick must fail loudly. This is the
    # guard that makes the unverified field ids safe to ship.
    values = _values()
    del values[missing]

    with pytest.raises(ValueError, match="missing"):
        parse_tick("005930", values, DAY)


@pytest.mark.parametrize("bad", ["9000", "09000a", ""])
def test_parse_tick_raises_on_an_unreadable_time(bad):
    with pytest.raises(ValueError, match="time"):
        parse_tick("005930", _values(time=bad), DAY)


# --------------------------------------------------------------- Aggregator


def test_a_minute_closes_when_a_tick_arrives_for_the_next_minute():
    agg = Aggregator()

    assert agg.add(_tick(0, 1, cum_volume=100)) is None  # establishes the baseline
    assert agg.add(_tick(1, 0, cum_volume=150)) is None  # closes the connect minute
    agg.add(_tick(1, 30, price=70100.0, cum_volume=200))
    done = agg.add(_tick(2, 0, cum_volume=260))

    assert done is not None
    assert done.ts == datetime(2026, 9, 25, 0, 1, tzinfo=UTC)
    assert (done.open, done.close) == (70000.0, 70100.0)


def test_the_connect_minute_is_discarded_because_it_has_no_baseline():
    # Its volume would be the accumulated total at its end minus the total at
    # its start, and the start happened before the first tick was seen.
    agg = Aggregator()

    agg.add(_tick(0, 30, cum_volume=5000))
    first_close = agg.add(_tick(1, 0, cum_volume=5100))

    assert first_close is None
    assert agg.in_progress("005930") is not None  # the next minute is reportable


def test_volume_and_value_come_from_accumulated_differences():
    agg = Aggregator()
    agg.add(_tick(0, 0, cum_volume=1000, cum_value=1e6))
    agg.add(_tick(1, 0, cum_volume=1000, cum_value=1e6))  # baseline for minute 1

    agg.add(_tick(1, 30, cum_volume=1400, cum_value=1.4e6))
    done = agg.add(_tick(2, 0, cum_volume=1500, cum_value=1.5e6))

    # Minute 1 ran from the accumulated total carried into it (1000) to the
    # total on its own last tick (1400). The 2:00 tick's extra 100 belongs to
    # minute 2 -- we cannot know whether it traded at 1:45 or 2:00, and
    # attributing it forward never double-counts.
    assert done is not None
    assert done.volume == 400
    assert done.trade_value == pytest.approx(4e5)


def test_a_dropped_middle_tick_does_not_corrupt_the_volume():
    # Why accumulated counters are differenced instead of tick quantities being
    # summed. A tick in the middle of the minute never arrives, so the trades it
    # carried are invisible -- but the last tick's running total still includes
    # them, and the difference is right anyway. Summing the quantities of the
    # ticks that did arrive would under-report.
    agg = Aggregator()
    agg.add(_tick(0, 0, cum_volume=1000))
    agg.add(_tick(1, 0, cum_volume=1000))

    agg.add(_tick(1, 10, cum_volume=1200))
    # (a tick at 1:20 carrying 250 shares, cum_volume=1450, is dropped here)
    agg.add(_tick(1, 50, cum_volume=1500))
    done = agg.add(_tick(2, 0, cum_volume=1500))

    assert done is not None
    assert done.volume == 500


def test_the_boundary_uses_the_ticks_own_time_not_the_clock():
    agg = Aggregator()
    agg.add(_tick(0, 0, cum_volume=10))
    agg.add(_tick(1, 0, cum_volume=10))

    # Two ticks in the same minute, seconds apart, must not close anything.
    assert agg.add(_tick(1, 5)) is None
    assert agg.add(_tick(1, 59)) is None


def test_a_late_tick_for_a_closed_minute_is_ignored():
    agg = Aggregator()
    agg.add(_tick(0, 0, cum_volume=10))
    agg.add(_tick(1, 0, cum_volume=10))
    agg.add(_tick(2, 0, cum_volume=20))

    assert agg.add(_tick(1, 30)) is None
    pending = agg.in_progress("005930")
    assert pending is not None
    assert pending.ts == datetime(2026, 9, 25, 0, 2, tzinfo=UTC)


def test_symbols_are_aggregated_independently():
    agg = Aggregator()
    agg.add(_tick(0, 0, symbol="A", cum_volume=10))
    agg.add(_tick(0, 0, symbol="B", cum_volume=20))
    agg.add(_tick(1, 0, symbol="A", cum_volume=10))

    assert agg.in_progress("A") is not None
    assert agg.in_progress("B") is None  # still in B's connect minute


# ------------------------------------------------------------------- Window


def _candle(high=100.0, low=90.0, close=95.0, session="regular", symbol="005930"):
    return LiveCandle(
        ts=datetime(2026, 9, 25, 0, 1, tzinfo=UTC),
        symbol=symbol,
        session=session,
        open=95.0,
        high=high,
        low=low,
        close=close,
        volume=10,
        trade_value=950.0,
    )


def test_window_seeds_from_read_regular_candles_shape():
    window = Window(size=3)
    rows = [(datetime(2026, 9, 25, 0, i, tzinfo=UTC), 10.0 + i, 9.0 + i, 9.5 + i) for i in range(5)]

    window.seed("005930", rows)
    high, low, close = window.series_with(_candle())

    # maxlen=3 keeps the newest three seeded rows, plus the candle itself.
    assert len(close) == 4
    assert close[0] == pytest.approx(11.5)


def test_window_evicts_the_oldest_beyond_its_size():
    window = Window(size=2)
    for value in (1.0, 2.0, 3.0):
        window.append(_candle(high=value, low=value, close=value))

    _, _, close = window.series_with(_candle(close=9.0))

    assert list(close) == [2.0, 3.0, 9.0]


# --------------------------------------------------------------- candle_row


def _seeded_window(n=400):
    window = Window()
    rng = np.random.default_rng(3)
    price = 70000 + np.cumsum(rng.normal(0, 100, n))
    window.seed(
        "005930",
        [(datetime(2026, 9, 25, tzinfo=UTC), p + 50, p - 50, p) for p in price.tolist()],
    )
    return window


def test_a_regular_session_candle_carries_indicators_and_verdicts():
    row = candle_row(_seeded_window(), _candle(session="regular"))

    assert row.src == "ws"
    assert set(row.indicators) == set(INDICATOR_FIELDS)
    assert row.indicators["rsi"] is not None
    assert row.comments["rsi"] in {"OVERBOUGHT", "OVERSOLD", "NEUTRAL"}


def test_an_extended_session_candle_carries_no_indicators():
    # Same rule the REST path follows: verdicts are for the regular session.
    row = candle_row(_seeded_window(), _candle(session="extended"))

    assert row.indicators == {}
    assert row.comments == {}
    assert row.session == "extended"


def test_an_unwarmed_window_yields_none_rather_than_nan():
    # NaN would reach QuestDB as a number; None is omitted from the write.
    row = candle_row(Window(), _candle())

    assert row.indicators["rsi"] is None


# --------------------------------------------------------------- TickBuffer


def test_the_buffer_drops_the_oldest_and_counts_it():
    async def body():
        buffer = TickBuffer(maxsize=2)
        buffer.put(_tick(0, 0, price=1.0))
        buffer.put(_tick(0, 1, price=2.0))
        buffer.put(_tick(0, 2, price=3.0))

        assert buffer.dropped == 1
        assert (await buffer.get()).price == 2.0
        assert (await buffer.get()).price == 3.0

    asyncio.run(body())


def test_get_waits_for_a_tick_rather_than_spinning():
    async def body():
        buffer = TickBuffer()

        async def later():
            await asyncio.sleep(0)
            buffer.put(_tick(0, 0, price=7.0))

        _, tick = await asyncio.gather(later(), buffer.get())

        assert tick.price == 7.0

    asyncio.run(body())


# ----------------------------------------------------------- connection_plan


def test_two_hundred_symbols_fit_one_connection_of_two_groups():
    plan = connection_plan([f"s{i}" for i in range(200)], 100, 2)

    assert [[len(group) for group in conn] for conn in plan] == [[100, 100]]


def test_one_group_per_connection_gives_two_connections():
    # A2 is an assumption. If one connection cannot carry two groups, this is
    # the whole change: a setting, not a restructuring.
    plan = connection_plan([f"s{i}" for i in range(200)], 100, 1)

    assert [[len(group) for group in conn] for conn in plan] == [[100], [100]]


def test_a_smaller_group_cap_makes_more_groups():
    plan = connection_plan([f"s{i}" for i in range(200)], 50, 2)

    assert [[len(group) for group in conn] for conn in plan] == [[50, 50], [50, 50]]


@pytest.mark.parametrize(("per_group", "per_conn"), [(0, 2), (100, 0), (-1, 1)])
def test_a_non_positive_allocation_raises(per_group, per_conn):
    with pytest.raises(ValueError):
        connection_plan(["a"], per_group, per_conn)


# ---------------------------------------------------------------- ticks_from


def test_ticks_from_reads_every_trade_entry():
    frame = {
        "trnm": "REAL",
        "data": [
            {"type": "0B", "item": "005930", "values": _values(time="090100")},
            {"type": "0B", "item": "000660", "values": _values(time="090200")},
        ],
    }

    ticks = ticks_from(frame, DAY)

    assert [t.symbol for t in ticks] == ["005930", "000660"]


def test_ticks_from_ignores_other_realtime_types():
    # Registration asks for 0B only, but the server may send more.
    frame = {"trnm": "REAL", "data": [{"type": "0D", "item": "005930", "values": {"27": "1"}}]}

    assert ticks_from(frame, DAY) == []


# -------------------------------------------------------------------- stream


class FakeSocket:
    def __init__(self, replies, frames):
        self.replies = list(replies)
        self.frames = list(frames)
        self.sent = []

    async def send(self, message):
        self.sent.append(message)

    async def recv(self):
        return json.dumps(self.replies.pop(0))

    def __aiter__(self):
        async def frames():
            for frame in self.frames:
                yield frame

        return frames()


OK = {"return_code": 0}


def _connect(socket):
    async def connect(url):
        return socket

    return connect


def test_stream_logs_in_registers_every_group_and_buffers_ticks():
    async def body():
        frame = json.dumps(
            {"trnm": "REAL", "data": [{"type": "0B", "item": "005930", "values": _values()}]}
        )
        socket = FakeSocket([OK, OK, OK], [frame])
        buffer = TickBuffer()

        await stream("wss://x", "tok", [["005930"], ["000660"]], buffer, DAY, _connect(socket))

        assert json.loads(socket.sent[0]) == json.loads(login_message("tok"))
        assert json.loads(socket.sent[1]) == json.loads(register_message(1, ["005930"]))
        assert json.loads(socket.sent[2]) == json.loads(register_message(2, ["000660"]))
        assert len(buffer) == 1

    asyncio.run(body())


def test_stream_echoes_ping_unchanged():
    async def body():
        # A missed echo is how Kiwoom decides the client is gone.
        ping = json.dumps({"trnm": "PING", "nonce": "abc"})
        socket = FakeSocket([OK, OK], [ping])

        await stream("wss://x", "tok", [["005930"]], TickBuffer(), DAY, _connect(socket))

        assert socket.sent[-1] == ping

    asyncio.run(body())


def test_stream_raises_when_login_is_refused():
    async def body():
        socket = FakeSocket([{"return_code": 3, "return_msg": "IP not registered"}], [])

        with pytest.raises(RuntimeError, match="LOGIN refused"):
            await stream("wss://x", "tok", [["005930"]], TickBuffer(), DAY, _connect(socket))

    asyncio.run(body())


def test_stream_raises_when_a_group_registration_is_refused():
    async def body():
        # This is how an over-large group (A1 wrong) would announce itself.
        socket = FakeSocket([OK, {"return_code": 9, "return_msg": "too many items"}], [])

        with pytest.raises(RuntimeError, match="REG refused for group 1"):
            await stream(
                "wss://x",
                "tok",
                [[f"s{i}" for i in range(200)]],
                TickBuffer(),
                DAY,
                _connect(socket),
            )

    asyncio.run(body())


# --------------------------------------------------------------------- drain


def test_drain_writes_a_finalised_candle_immediately():
    async def body():
        buffer, agg, window = TickBuffer(), Aggregator(), _seeded_window()
        written = []
        ticks = (
            _tick(0, 0, cum_volume=10),
            _tick(1, 0, cum_volume=10),
            _tick(1, 30, cum_volume=90),
            _tick(2, 0, cum_volume=95),
        )
        for tick in ticks:
            buffer.put(tick)

        task = asyncio.create_task(
            drain(buffer, agg, window, lambda rows: written.extend(rows) or len(rows))
        )
        while len(buffer):
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()

        # One minute produces several writes: the in-progress candle as ticks
        # arrive, then the finalised one when the boundary passes. Dedup on
        # (ts, symbol) collapses them to one row in QuestDB, so what matters is
        # that the *last* write for the minute is the finished figure.
        minute_one = [row for row in written if row.ts == datetime(2026, 9, 25, 0, 1, tzinfo=UTC)]
        assert len(minute_one) > 1, "the in-progress candle was never written"
        assert minute_one[-1].volume == 80

    asyncio.run(body())


def test_drain_throttles_the_in_progress_candle():
    async def body():
        buffer, agg, window = TickBuffer(), Aggregator(), _seeded_window()
        written = []
        buffer.put(_tick(0, 0, cum_volume=10))
        buffer.put(_tick(1, 0, cum_volume=10))
        for second in range(2, 40):
            buffer.put(_tick(1, second, cum_volume=10 + second))

        task = asyncio.create_task(
            drain(
                buffer,
                agg,
                window,
                lambda rows: written.extend(rows) or len(rows),
                flush_interval=60.0,
            )
        )
        while len(buffer):
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()

        # 38 ticks inside one minute, one write: per-tick writing would be 38.
        assert len(written) == 1

    asyncio.run(body())


def test_backoff_grows_then_holds():
    assert [backoff_for(n) for n in (0, 1, 2)] == [1.0, 2.0, 4.0]
    assert backoff_for(99) == 30.0
