import calendar
import re
import time
from datetime import date
from typing import Any

import httpx

API = "https://opendart.fss.or.kr/api"


def report_period(
    title: str, closing_month: int, detail_type: str | None = None
) -> tuple[int | None, str, date | None]:
    match = re.search(r"\((\d{4})\.(\d{2})\)", title)
    if match is None and detail_type not in (None, "A001", "A002", "A003"):
        return None, detail_type, None
    if not match or not 1 <= closing_month <= 12:
        raise ValueError("missing or invalid report period")
    year, month = map(int, match.groups())
    end = date(year, month, calendar.monthrange(year, month)[1])
    fiscal_year = year if month <= closing_month else year + 1
    elapsed = (month - closing_month) % 12
    if detail_type not in (None, "A001", "A002", "A003"):
        kind = detail_type
    elif (detail_type == "A001" or detail_type is None and "사업보고서" in title) and elapsed == 0:
        kind = "annual"
    elif (detail_type == "A002" or detail_type is None and "반기보고서" in title) and elapsed == 6:
        kind = "half"
    elif (detail_type == "A003" or detail_type is None and "분기보고서" in title) and elapsed in (
        3,
        9,
    ):
        kind = "q1" if elapsed == 3 else "q3"
    else:
        raise ValueError("unsupported reporting period")
    return fiscal_year, kind, end


class DartClient:
    def __init__(
        self,
        client: httpx.Client,
        api_key: str,
        interval: float = 1,
        attempts: int = 3,
        max_bytes: int = 100_000_000,
    ):
        self.client = client
        self.api_key = api_key
        self.interval = interval
        self.attempts = attempts
        self.max_bytes = max_bytes
        self.last_request = 0.0

    def get(self, endpoint: str, params: dict[str, str]) -> bytes:
        for attempt in range(self.attempts):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                with self.client.stream(
                    "GET",
                    f"{API}/{endpoint}",
                    params={"crtfc_key": self.api_key, **params},
                    timeout=60,
                ) as response:
                    response.raise_for_status()
                    data = bytearray()
                    for part in response.iter_bytes():
                        data.extend(part)
                        if len(data) > self.max_bytes:
                            raise ValueError("DART response exceeds archive limit")
                    return bytes(data)
            except (httpx.HTTPError, ValueError):
                if attempt + 1 == self.attempts:
                    raise RuntimeError("DART request failed") from None
                time.sleep(min(2**attempt, 8))
        raise RuntimeError("DART request failed")

    def json(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        import json

        data = json.loads(self.get(endpoint, params))
        if data.get("status") not in ("000", "013"):
            raise RuntimeError(f"DART API status {data.get('status', 'unknown')}")
        return data

    def closing_month(self, corp_code: str) -> int:
        month = int(self.json("company.json", {"corp_code": corp_code})["acc_mt"])
        if not 1 <= month <= 12:
            raise ValueError("invalid closing month")
        return month

    def financial_statements(
        self, corp_code: str, year: int, report_code: str, scope: str
    ) -> list[dict[str, Any]]:
        """Every account of one statement set. Status 013 means the filing has none."""
        data = self.json(
            "fnlttSinglAcntAll.json",
            {
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": report_code,
                "fs_div": scope,
            },
        )
        return [] if data["status"] == "013" else data.get("list", [])
