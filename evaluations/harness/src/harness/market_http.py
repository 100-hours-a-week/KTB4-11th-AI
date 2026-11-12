from datetime import UTC, date, datetime

import httpx

from harness.contracts import DailyBar


class MarketHttpPriceSource:
    def __init__(self, client: httpx.Client):
        self._client = client

    def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        found: list[DailyBar] = []
        params: dict[str, str | int] = {"timeframe": "1d", "limit": 1000}
        while True:
            response = self._client.get(f"/bars/{symbol}", params=params)
            response.raise_for_status()
            body = response.json()
            page = [parse_bar(item) for item in body["items"]]
            found.extend(page)
            cursor = body["next_cursor"]
            if cursor is None or any(bar.trading_date < start for bar in page):
                break
            params = {**params, "cursor": cursor}
        return sorted(
            (bar for bar in found if start <= bar.trading_date <= end),
            key=lambda bar: bar.trading_date,
        )


def parse_bar(item: dict) -> DailyBar:
    return DailyBar(
        trading_date=datetime.fromisoformat(item["timestamp"]).astimezone(UTC).date(),
        open=float(item["open"]),
        high=float(item["high"]),
        low=float(item["low"]),
        close=float(item["close"]),
        volume=int(item["volume"]),
    )
