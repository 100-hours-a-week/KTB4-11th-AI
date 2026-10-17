from datetime import UTC, datetime
from typing import Any

import pandas as pd


def records(db: Any, sql: str, binds: list[object]) -> list[dict[str, Any]]:
    with db.query(sql, binds) as result:
        return result.to_pandas().to_dict("records")


def as_utc(value: object) -> datetime:
    timestamp = pd.Timestamp(value).to_pydatetime()
    return timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)


def questdb_time(timestamp: datetime) -> str:
    return timestamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
