from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np
import questdb
import sqlalchemy as sa

from portfolio_builder.measurement import Array, Bars

VIEWS = {"1m": "bars_1m", "15m": "bars_15m", "1h": "bars_1h", "1d": "bars_1d"}
SESSION_FILTERED = {"1m", "1d"}
LIMIT = 300
KOSPI200 = "KOSPI200"
UNIVERSE_DAYS = 400


class Market(ABC):
    @abstractmethod
    def bars(self, symbol: str, timeframe: str) -> tuple[Bars, datetime | None]: ...

    @abstractmethod
    def universe_closes(self) -> dict[str, Array]: ...


class QuestDBMarket(Market):
    def __init__(self, conf: str, engine: sa.Engine) -> None:
        self._conf = conf
        self._engine = engine

    def _records(self, sql: str, binds: list[Any] | None = None) -> list[dict[str, Any]]:
        with questdb.connect(self._conf) as db, db.query(sql, binds) as result:
            return result.to_pandas().to_dict("records")

    def bars(self, symbol: str, timeframe: str) -> tuple[Bars, datetime | None]:
        view = VIEWS[timeframe]
        session = " AND session = 'regular'" if timeframe in SESSION_FILTERED else ""
        records = self._records(
            f"SELECT ts, high, low, close, volume FROM {view} WHERE symbol = $1{session} LIMIT $2",
            [symbol, -LIMIT],
        )

        def column(name: str) -> Array:
            return np.array([r[name] for r in records], dtype=np.float64)

        bars = Bars(column("high"), column("low"), column("close"), column("volume"))
        return bars, (records[-1]["ts"] if records else None)

    def universe_closes(self) -> dict[str, Array]:
        with self._engine.connect() as conn:
            members = set(
                conn.execute(
                    sa.text("SELECT stock_code FROM corporation_indices WHERE index_name = :name"),
                    {"name": KOSPI200},
                ).scalars()
            )
        closes: dict[str, list[float]] = defaultdict(list)
        for r in self._records(
            "SELECT symbol, ts, close FROM bars_1d WHERE session = 'regular'"
            f" AND ts > dateadd('d', -{UNIVERSE_DAYS}, now()) ORDER BY ts"
        ):
            if r["symbol"] in members:
                closes[r["symbol"]].append(r["close"])
        return {symbol: np.array(values, dtype=np.float64) for symbol, values in closes.items()}
