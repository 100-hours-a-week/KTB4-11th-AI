import json
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import sqlalchemy as sa

from portfolio_builder.measurement import Array, Bars

TIMEFRAMES = {"1m", "15m", "1h", "1d"}
LIMIT = 300
KOSPI200 = "KOSPI200"


class Market(ABC):
    @abstractmethod
    def bars(self, symbol: str, timeframe: str) -> tuple[Bars, datetime | None]: ...

    @abstractmethod
    def universe_closes(self) -> dict[str, Array]: ...


class HttpMarket(Market):
    def __init__(self, base_uri: str, engine: sa.Engine, timeout: float = 10.0) -> None:
        self._base_uri = base_uri.rstrip("/")
        self._engine = engine
        self._timeout = timeout

    def _oldest_first(self, symbol: str, timeframe: str) -> list[dict[str, Any]]:
        if timeframe not in TIMEFRAMES:
            raise KeyError(timeframe)
        query = urlencode({"timeframe": timeframe, "limit": LIMIT})
        request = Request(
            f"{self._base_uri}/bars/{symbol}?{query}", headers={"Accept": "application/json"}
        )
        with urlopen(request, timeout=self._timeout) as response:
            return list(reversed(json.loads(response.read())["items"]))

    def bars(self, symbol: str, timeframe: str) -> tuple[Bars, datetime | None]:
        rows = self._oldest_first(symbol, timeframe)

        def column(name: str) -> Array:
            return np.array([row[name] for row in rows], dtype=np.float64)

        bars = Bars(column("high"), column("low"), column("close"), column("volume"))
        return bars, (datetime.fromisoformat(rows[-1]["timestamp"]) if rows else None)

    def universe_closes(self) -> dict[str, Array]:
        with self._engine.connect() as conn:
            members = sorted(
                set(
                    conn.execute(
                        sa.text(
                            "SELECT stock_code FROM corporation_indices WHERE index_name = :name"
                        ),
                        {"name": KOSPI200},
                    ).scalars()
                )
            )
        closes = {code: self._oldest_first(code, "1d") for code in members}
        return {
            code: np.array([row["close"] for row in rows], dtype=np.float64)
            for code, rows in closes.items()
            if rows
        }
