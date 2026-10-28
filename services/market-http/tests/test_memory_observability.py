import asyncio
import json
import os
import tracemalloc

import pytest
from market_http.observability import memory
from market_http.settings import Settings


@pytest.fixture(autouse=True)
def tracing_is_stopped():
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    yield
    if tracemalloc.is_tracing():
        tracemalloc.stop()


def _settings(tmp_path, **overrides):
    return Settings(
        questdb_conf="unused",
        postgres_dsn="postgresql+psycopg://ktb@localhost/ktb",
        valkey_host="localhost",
        memory_profile_dir=str(tmp_path),
        **overrides,
    )


def test_disabled_profiler_does_no_work(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    starts = snapshots = mkdirs = 0

    def start(*args):
        nonlocal starts
        starts += 1

    def take_snapshot():
        nonlocal snapshots
        snapshots += 1
        raise AssertionError("disabled profiling must not snapshot")

    def mkdir(*args, **kwargs):
        nonlocal mkdirs
        mkdirs += 1
        raise AssertionError("disabled profiling must not create directories")

    async def run():
        async with memory.memory_profiling(settings):
            await asyncio.sleep(0)

    monkeypatch.setattr(memory.tracemalloc, "start", start)
    monkeypatch.setattr(memory.tracemalloc, "take_snapshot", take_snapshot)
    monkeypatch.setattr(memory.Path, "mkdir", mkdir)
    asyncio.run(run())

    assert (starts, snapshots, mkdirs) == (0, 0, 0)


def test_profiler_produces_loadable_bounded_snapshots(tmp_path):
    settings = _settings(
        tmp_path,
        memory_profile_enabled=True,
        memory_profile_interval_seconds=1,
        memory_profile_max_snapshots=2,
    )

    async def run():
        async with memory.memory_profiling(settings):
            await asyncio.sleep(1.1)

    asyncio.run(run())

    snapshots = list(tmp_path.glob("*/*.snapshot"))
    manifests = list(tmp_path.glob("*/*.json"))
    assert len(snapshots) == len(manifests) == 2
    assert all(tracemalloc.Snapshot.load(snapshot) for snapshot in snapshots)
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text())
        assert manifest["utc"].endswith("Z")
        assert manifest["pid"] == os.getpid()
        assert manifest["sequence"] in (1, 2)
        assert isinstance(manifest["traced_current_bytes"], int)
        assert isinstance(manifest["traced_peak_bytes"], int)
        assert "rss_bytes" in manifest


def test_profiler_disk_failure_and_cancel_are_nonfatal(tmp_path, monkeypatch, caplog):
    settings = _settings(tmp_path, memory_profile_enabled=True)

    def fail_dump(*args, **kwargs):
        raise OSError("disk unavailable")

    async def run():
        async with memory.memory_profiling(settings):
            await asyncio.sleep(0.05)

    monkeypatch.setattr(memory, "_dump_snapshot", fail_dump)
    asyncio.run(run())

    assert "memory_profile_stopped" in caplog.text
    assert not tracemalloc.is_tracing()


def test_profiler_baseline_stat_failure_is_nonfatal(tmp_path, monkeypatch, caplog):
    settings = _settings(tmp_path, memory_profile_enabled=True)
    entered = False

    original_stat = memory.Path.stat

    def fail_stat(self, *args, **kwargs):
        if self.suffix == ".snapshot":
            raise OSError("metadata unavailable")
        return original_stat(self, *args, **kwargs)

    async def run():
        nonlocal entered
        async with memory.memory_profiling(settings):
            entered = True
            await asyncio.sleep(0)

    monkeypatch.setattr(memory.Path, "stat", fail_stat)
    asyncio.run(run())

    stopped = [record for record in caplog.records if record.message == "memory_profile_stopped"]
    assert entered
    assert len(stopped) == 1
    assert stopped[0].fields == {"reason": "dump_failed", "error": "OSError"}
    assert not tracemalloc.is_tracing()


def test_existing_tracemalloc_owner_is_not_stopped(tmp_path, caplog):
    tracemalloc.start(1)
    settings = _settings(tmp_path, memory_profile_enabled=True)

    async def run():
        async with memory.memory_profiling(settings):
            await asyncio.sleep(0)

    asyncio.run(run())

    assert tracemalloc.is_tracing()
    assert "memory_profile_skipped" in caplog.text


def test_profile_budget_and_single_flight(tmp_path, monkeypatch):
    settings = _settings(
        tmp_path,
        memory_profile_enabled=True,
        memory_profile_interval_seconds=1,
        memory_profile_max_snapshots=3,
    )
    active = maximum = dumps = 0

    def dump_snapshot(snapshot, snapshot_path, manifest_path, manifest):
        nonlocal active, maximum, dumps
        active += 1
        maximum = max(maximum, active)
        try:
            snapshot_path.write_bytes(b"x" * memory.MAX_PROFILE_BYTES)
            manifest_path.write_text(json.dumps(manifest))
            dumps += 1
        finally:
            active -= 1

    async def run():
        async with memory.memory_profiling(settings):
            await asyncio.sleep(2.2)

    monkeypatch.setattr(memory, "_dump_snapshot", dump_snapshot)
    asyncio.run(run())

    assert maximum == 1
    assert dumps == 1
    assert len(list(tmp_path.glob("*/*.snapshot"))) == 1
