import asyncio
import json
import logging
import os
import socket
import tracemalloc
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path

from market_http.settings import Settings

logger = logging.getLogger(__name__)
MAX_PROFILE_BYTES = 128 * 1024 * 1024


def current_rss_bytes() -> int | None:
    try:
        with open("/proc/self/statm") as statm:
            resident_pages = int(statm.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE")
    except (IndexError, OSError, ValueError):
        return None


def _dump_snapshot(
    snapshot: tracemalloc.Snapshot,
    snapshot_path: Path,
    manifest_path: Path,
    manifest: dict[str, int | str | None],
) -> None:
    partial = snapshot_path.with_suffix(".snapshot.partial")
    snapshot.dump(str(partial))
    partial.replace(snapshot_path)
    partial_manifest = manifest_path.with_suffix(".json.partial")
    partial_manifest.write_text(json.dumps(manifest, separators=(",", ":")))
    partial_manifest.replace(manifest_path)


async def _capture_snapshot(run_dir: Path, sequence: int) -> int | None:
    snapshot_path = run_dir / f"{os.getpid()}-{sequence}.snapshot"
    manifest_path = run_dir / f"{os.getpid()}-{sequence}.json"
    current, peak = tracemalloc.get_traced_memory()
    manifest: dict[str, int | str | None] = {
        "utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "pid": os.getpid(),
        "sequence": sequence,
        "traced_current_bytes": current,
        "traced_peak_bytes": peak,
        "rss_bytes": current_rss_bytes(),
    }
    dump_task = asyncio.create_task(
        asyncio.to_thread(
            lambda: _dump_snapshot(
                tracemalloc.take_snapshot(), snapshot_path, manifest_path, manifest
            )
        )
    )
    try:
        await asyncio.shield(dump_task)
        return snapshot_path.stat().st_size
    except asyncio.CancelledError:
        with suppress(Exception):
            await asyncio.shield(dump_task)
        raise
    except OSError as error:
        logger.warning(
            "memory_profile_stopped",
            extra={"fields": {"reason": "dump_failed", "error": type(error).__name__}},
        )
        return None
    except Exception as error:
        logger.warning(
            "memory_profile_stopped",
            extra={"fields": {"reason": "snapshot_failed", "error": type(error).__name__}},
        )
        return None


@asynccontextmanager
async def _startup_profiling(
    settings: Settings | None, finished: asyncio.Event, burst_trigger: Path | None = None
) -> AsyncGenerator[None]:
    if settings is None or not settings.memory_profile_enabled:
        finished.set()
        yield
        return
    if tracemalloc.is_tracing():
        logger.warning(
            "memory_profile_skipped", extra={"fields": {"reason": "tracemalloc_already_active"}}
        )
        finished.set()
        yield
        return

    tracemalloc.start(5)
    run_dir = Path(settings.memory_profile_dir) / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        await asyncio.to_thread(run_dir.mkdir, parents=True, exist_ok=False)
    except OSError as error:
        logger.warning(
            "memory_profile_stopped",
            extra={"fields": {"reason": "directory_failed", "error": type(error).__name__}},
        )
        tracemalloc.stop()
        finished.set()
        yield
        return

    written = await _capture_snapshot(run_dir, 1)
    if written is None:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        finished.set()
        yield
        return

    async def collect() -> None:
        if burst_trigger is not None:
            try:
                async with asyncio.timeout(600):
                    while True:
                        try:
                            burst_trigger.rename(burst_trigger.with_suffix(".claimed"))
                            break
                        except FileNotFoundError:
                            await asyncio.sleep(0.25)
            except TimeoutError:
                logger.warning(
                    "memory_profile_stopped", extra={"fields": {"reason": "burst_trigger_timeout"}}
                )
                if tracemalloc.is_tracing():
                    tracemalloc.stop()
                return
            logger.info("memory_profile_burst_started")
        total_bytes = written
        for sequence in range(2, settings.memory_profile_max_snapshots + 1):
            if total_bytes >= MAX_PROFILE_BYTES:
                logger.warning(
                    "memory_profile_stopped", extra={"fields": {"reason": "disk_budget"}}
                )
                if tracemalloc.is_tracing():
                    tracemalloc.stop()
                return
            try:
                await asyncio.sleep(settings.memory_profile_interval_seconds)
            except asyncio.CancelledError:
                return
            size = await _capture_snapshot(run_dir, sequence)
            if size is None:
                if tracemalloc.is_tracing():
                    tracemalloc.stop()
                return
            total_bytes += size
            if total_bytes >= MAX_PROFILE_BYTES:
                logger.warning(
                    "memory_profile_stopped", extra={"fields": {"reason": "disk_budget"}}
                )
                if tracemalloc.is_tracing():
                    tracemalloc.stop()
                return
        if tracemalloc.is_tracing():
            tracemalloc.stop()

    async def collect_until_finished() -> None:
        try:
            await collect()
        finally:
            finished.set()

    collector = asyncio.create_task(collect_until_finished())
    try:
        yield
    finally:
        collector.cancel()
        with suppress(asyncio.CancelledError):
            await collector
        if tracemalloc.is_tracing():
            tracemalloc.stop()


def _write_state(path: Path, state: dict) -> None:
    temporary = path.with_suffix(".json.partial")
    temporary.write_text(json.dumps(state, separators=(",", ":")))
    temporary.replace(path)


@asynccontextmanager
async def memory_profiling(settings: Settings | None) -> AsyncGenerator[None]:
    if settings is None or not settings.memory_profile_enabled:
        yield
        return
    if settings.memory_profile_start_mode == "startup":
        async with _startup_profiling(settings, asyncio.Event()):
            yield
        return
    run_dir = Path(settings.memory_profile_dir) / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    state_path = run_dir / "profile-state.json"
    state = {
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "task_id": settings.task_id,
        "status": "armed",
        "utc": datetime.now(UTC).isoformat(),
        "trigger": "capture.trigger",
    }
    try:
        await asyncio.to_thread(run_dir.mkdir, parents=True, exist_ok=False)
        await asyncio.to_thread(_write_state, state_path, state)
    except OSError as error:
        logger.warning(
            "memory_profile_stopped",
            extra={"fields": {"reason": "arming_failed", "error": type(error).__name__}},
        )
        yield
        return
    logger.info("memory_profile_armed", extra={"fields": state | {"path": str(run_dir)}})

    async def wait_and_capture() -> None:
        trigger = run_dir / "capture.trigger"
        try:
            while True:
                try:
                    trigger.rename(run_dir / "capture.claimed")
                    break
                except FileNotFoundError:
                    await asyncio.sleep(0.25)
            state.update(status="capturing", utc=datetime.now(UTC).isoformat())
            await asyncio.to_thread(_write_state, state_path, state)
            logger.info("memory_profile_started", extra={"fields": state})
            finished = asyncio.Event()
            capture_settings = settings.model_copy(update={"memory_profile_dir": str(run_dir)})
            burst = run_dir / "capture.burst" if settings.memory_profile_burst_trigger else None
            async with _startup_profiling(capture_settings, finished, burst):
                if burst is not None and not finished.is_set():
                    state.update(status="baseline_ready", utc=datetime.now(UTC).isoformat())
                    await asyncio.to_thread(_write_state, state_path, state)
                await finished.wait()
            state.update(
                status="finished",
                utc=datetime.now(UTC).isoformat(),
                snapshots=len(list(run_dir.rglob("*.snapshot"))),
            )
            await asyncio.to_thread(_write_state, state_path, state)
        except asyncio.CancelledError:
            state.update(status="cancelled", utc=datetime.now(UTC).isoformat())
            with suppress(OSError):
                await asyncio.to_thread(_write_state, state_path, state)
            raise
        except Exception as error:
            state.update(status="failed", error=type(error).__name__)
            with suppress(OSError):
                await asyncio.to_thread(_write_state, state_path, state)
            logger.warning("memory_profile_stopped", extra={"fields": state})

    collector = asyncio.create_task(wait_and_capture())
    try:
        yield
    finally:
        collector.cancel()
        with suppress(asyncio.CancelledError):
            await collector
