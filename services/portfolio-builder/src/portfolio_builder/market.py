from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np
import questdb

from portfolio_builder.evidence import Array, Bars

VIEWS = {"1m": "bars_1m", "15m": "bars_15m", "1h": "bars_1h", "1d": "bars_1d"}
# bars_15m and bars_1h are materialized views without a session column (see issue #47).
SESSION_FILTERED = {"1m", "1d"}
LIMIT = 300
KOSPI200 = "201"
# 400 calendar days hold more than the 253 trading days that 12-month momentum needs.
UNIVERSE_DAYS = 400


class QuestDBMarket:
    def __init__(self, conf: str) -> None:
        self._conf = conf

    # A new connection per call: tools run on ToolNode worker threads and a QuestDB query result
    # is bound to the thread that created it.
    def _records(self, sql: str, binds: list[Any] | None = None) -> list[dict[str, Any]]:
        with questdb.connect(self._conf) as db, db.query(sql, binds) as result:
            return result.to_pandas().to_dict("records")

    def bars(self, symbol: str, timeframe: str) -> tuple[Bars, datetime | None]:
        view = VIEWS[timeframe]
        session = " AND session = 'regular'" if timeframe in SESSION_FILTERED else ""
        records = self._records(
            f"SELECT ts, high, low, close, volume FROM {view} "
            f"WHERE symbol = $1{session} ORDER BY ts DESC LIMIT $2",
            [symbol, LIMIT],
        )
        records.reverse()

        def column(name: str) -> Array:
            return np.array([r[name] for r in records], dtype=np.float64)

        bars = Bars(column("high"), column("low"), column("close"), column("volume"))
        return bars, (records[-1]["ts"] if records else None)

    def universe_closes(self) -> dict[str, Array]:
        members = {
            r["symbol"]
            for r in self._records(
                "SELECT symbol FROM universe_members WHERE index_code = $1"
                " AND ts = (SELECT max(ts) FROM universe_members WHERE index_code = $1)",
                [KOSPI200],
            )
        }
        closes: dict[str, list[float]] = defaultdict(list)
        for r in self._records(
            "SELECT symbol, ts, close FROM bars_1d WHERE session = 'regular'"
            f" AND ts > dateadd('d', -{UNIVERSE_DAYS}, now()) ORDER BY symbol, ts"
        ):
            if r["symbol"] in members:
                closes[r["symbol"]].append(r["close"])
        return {symbol: np.array(values, dtype=np.float64) for symbol, values in closes.items()}
