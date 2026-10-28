from __future__ import annotations

import asyncio
import json
import logging
import os
import tracemalloc
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
    snapshot.dump(str(snapshot_path))
    manifest_path.write_text(json.dumps(manifest, separators=(",", ":")))


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
async def memory_profiling(settings: Settings | None) -> AsyncGenerator[None]:
    if settings is None or not settings.memory_profile_enabled:
        yield
        return
    if tracemalloc.is_tracing():
        logger.warning(
            "memory_profile_skipped", extra={"fields": {"reason": "tracemalloc_already_active"}}
        )
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
        yield
        return

    written = await _capture_snapshot(run_dir, 1)
    if written is None:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        yield
        return

    async def collect() -> None:
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

    collector = asyncio.create_task(collect())
    try:
        yield
    finally:
        collector.cancel()
        with suppress(asyncio.CancelledError):
            await collector
        if tracemalloc.is_tracing():
            tracemalloc.stop()
