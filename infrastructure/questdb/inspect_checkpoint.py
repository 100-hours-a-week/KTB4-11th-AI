import argparse
import json
import os
import re
from datetime import UTC, datetime, timedelta
from statistics import median
from threading import Event, Thread
from time import perf_counter
from urllib.request import urlopen

import questdb
from market_collector.store import CHECKPOINT_SQL


def _records(db, sql, binds=None):
    with db.query(sql, binds) as result:
        return result.to_pandas().to_dict("records")


def _metrics(url, samples, errors):
    if not url:
        return
    try:
        with urlopen(url, timeout=2) as response:
            lines = response.read().decode().splitlines()
        values = {}
        for line in lines:
            if line.startswith(("questdb_memory_", "questdb_jvm_")):
                name, value = line.rsplit(" ", 1)
                values[name] = float(value)
        if not values:
            raise ValueError("no QuestDB memory/GC metrics returned")
        if "questdb_memory_jvm_total" in values and "questdb_memory_jvm_free" in values:
            values["jvm_used_bytes"] = (
                values["questdb_memory_jvm_total"] - values["questdb_memory_jvm_free"]
            )
        samples.append(values)
    except (OSError, ValueError) as error:
        if str(error) not in errors:
            errors.append(str(error))


def _measure(db, sql, binds, repeats, metrics_url):
    plan = _records(db, f"EXPLAIN {sql}", binds)
    samples, errors, elapsed = [], [], []
    stop = Event()

    def sample():
        while not stop.wait(0.05):
            _metrics(metrics_url, samples, errors)

    _metrics(metrics_url, samples, errors)
    sampler = Thread(target=sample, daemon=True)
    if metrics_url:
        sampler.start()
    try:
        for _ in range(repeats):
            started = perf_counter()
            records = _records(db, sql, binds)
            elapsed.append(round((perf_counter() - started) * 1000, 3))
    finally:
        stop.set()
        if metrics_url:
            sampler.join()
        _metrics(metrics_url, samples, errors)
    return records, {
        "sql": sql,
        "binds": binds,
        "explain": plan,
        "elapsed_ms": elapsed,
        "median_ms": median(elapsed),
        "returned_rows": len(records),
        "metrics": {
            "sample_count": len(samples),
            "errors": errors,
            "before": samples[0] if samples else None,
            "after": samples[-1] if samples else None,
            "sampled_max": {
                name: max(snapshot[name] for snapshot in samples if name in snapshot)
                for name in {name for snapshot in samples for name in snapshot}
            },
        },
    }


def _boundaries(records):
    return {
        (r["symbol"], r["timeframe"]): (
            r["latest_ts"].replace(tzinfo=UTC)
            if r["latest_ts"].tzinfo is None
            else r["latest_ts"].astimezone(UTC)
        )
        for r in records
    }


def inspect_checkpoint(
    db,
    *,
    table="bars",
    minute_days=7,
    daily_days=100,
    repeats=3,
    compare_aggregate=False,
    metrics_url=None,
):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
        raise ValueError("table must be a SQL identifier")
    if min(minute_days, daily_days, repeats) < 1:
        raise ValueError("days and repeats must be positive")
    checked_at = _records(db, "SELECT now() AS checked_at")[0]["checked_at"]
    checked_at = checked_at.replace(tzinfo=UTC) if checked_at.tzinfo is None else checked_at
    cutoffs = [checked_at - timedelta(days=minute_days), checked_at - timedelta(days=daily_days)]
    latest_sql = CHECKPOINT_SQL.replace("FROM bars", f"FROM {table}")
    latest_rows, latest = _measure(db, latest_sql, None, repeats, metrics_url)
    bounded_sql = latest_sql.replace("WHERE timeframe = '1m'", "WHERE timeframe = '1m' AND ts > $1")
    bounded_sql = bounded_sql.replace(
        "WHERE timeframe = '1d'", "WHERE timeframe = '1d' AND ts > $2"
    )
    bounded_rows, bounded = _measure(db, bounded_sql, cutoffs, repeats, metrics_url)
    boundaries = _boundaries(latest_rows)
    bounded_boundaries = _boundaries(bounded_rows)
    missing = [
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "latest_ts": timestamp,
            "age_days": round((checked_at - timestamp).total_seconds() / 86400, 3),
        }
        for (symbol, timeframe), timestamp in sorted(boundaries.items())
        if (symbol, timeframe) not in bounded_boundaries
    ]
    report = {
        "checked_at": checked_at,
        "table": table,
        "partitions": _records(db, f"SELECT * FROM table_partitions('{table}')"),
        "latest": latest,
        "bounded_comparison": bounded,
        "cutoffs": {"1m": cutoffs[0], "1d": cutoffs[1]},
        "checkpoints_lost_with_cutoff": missing,
        "bounded_mapping_equal": boundaries == bounded_boundaries,
        "retained_boundaries_equal": all(
            boundaries.get(key) == value for key, value in bounded_boundaries.items()
        ),
    }
    if compare_aggregate:
        sql = f"SELECT symbol, timeframe, max(ts) AS latest_ts FROM {table}"
        sql += " WHERE timeframe IN ('1m', '1d') GROUP BY symbol, timeframe"
        aggregate_rows, report["aggregate"] = _measure(db, sql, None, repeats, metrics_url)
        report["aggregate_mapping_equal"] = boundaries == _boundaries(aggregate_rows)
    return report


def main():
    parser = argparse.ArgumentParser(description="Read-only checkpoint scan and cutoff comparison")
    parser.add_argument("--table", default="bars")
    parser.add_argument("--minute-days", type=int, default=7)
    parser.add_argument("--daily-days", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--compare-aggregate", action="store_true")
    parser.add_argument("--metrics-url")
    args = parser.parse_args()
    conf = os.environ.get("KTB_QUESTDB_CONF")
    if not conf:
        parser.error("KTB_QUESTDB_CONF is not set")
    with questdb.connect(conf) as db:
        report = inspect_checkpoint(db, **vars(args))
    print(
        json.dumps(
            report,
            default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
