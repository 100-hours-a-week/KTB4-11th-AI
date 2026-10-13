from datetime import UTC, datetime
from typing import Any

import pandas as pd


def records(db: Any, sql: str, binds: list[object]) -> list[dict[str, Any]]:
    with db.query(sql, binds) as result:
        return result.to_pandas().to_dict("records")


def as_utc(value: object) -> datetime:
    ts = pd.Timestamp(value).to_pydatetime()
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def questdb_time(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
