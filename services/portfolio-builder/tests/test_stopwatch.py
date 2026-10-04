from portfolio_builder import stopwatch
from portfolio_builder.stopwatch import Stopwatch


def _clock(monkeypatch, *readings):
    ticks = iter(readings)
    monkeypatch.setattr(stopwatch.time, "perf_counter_ns", lambda: next(ticks))


def test_elapsed_time_in_every_unit(monkeypatch):
    _clock(monkeypatch, 1_000_000_000, 3_500_000_000)
    watch = Stopwatch.start().stop()

    assert watch.elapsed_ns == 2_500_000_000
    assert watch.elapsed_ms == 2500
    assert watch.elapsed_seconds == 2.5


def test_a_stopped_watch_no_longer_moves(monkeypatch):
    _clock(monkeypatch, 0, 5_000_000, 9_000_000)
    watch = Stopwatch.start()
    watch.stop()
    watch.stop()

    assert watch.elapsed_ms == 5


def test_a_running_watch_reads_the_clock_each_time(monkeypatch):
    _clock(monkeypatch, 0, 1_000_000, 4_000_000)
    watch = Stopwatch.start()

    assert watch.elapsed_ms == 1
    assert watch.elapsed_ms == 4
