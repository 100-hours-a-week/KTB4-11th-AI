import calendar
import json
import re
import time
from datetime import date
from typing import Any

import httpx
from bs4 import BeautifulSoup
from ktb_core.logging import get_logger

API = "https://opendart.fss.or.kr/api"
log = get_logger(__name__)
REQUEST_FIELDS = {
    "rcept_no": "receipt_id",
    "corp_code": "corp_code",
    "bsns_year": "business_year",
    "reprt_code": "report_code",
    "fs_div": "scope",
    "bgn_de": "start_date",
    "end_de": "end_date",
    "page_no": "page_no",
    "pblntf_detail_ty": "detail_type",
}


class DartAPIError(RuntimeError):
    def __init__(self, status: str):
        self.status = status
        super().__init__(f"DART API status {status}")


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
        request_log = log.bind(
            endpoint=endpoint,
            **{
                REQUEST_FIELDS[key]: value for key, value in params.items() if key in REQUEST_FIELDS
            },
        )
        for attempt in range(self.attempts):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                with self.client.stream(
                    "GET",
                    f"{API}/{endpoint}",
                    params={"crtfc_key": self.api_key, **params},
                    timeout=30 if endpoint == "list.json" else 60,
                ) as response:
                    response.raise_for_status()
                    data = bytearray()
                    for part in response.iter_bytes():
                        data.extend(part)
                        if len(data) > self.max_bytes:
                            raise ValueError("DART response exceeds archive limit")
                    content = bytes(data)
                if endpoint.endswith(".json"):
                    payload = json.loads(content)
                    if not isinstance(payload, dict):
                        raise ValueError("DART JSON response must be an object")
                    status = payload.get("status", "unknown")
                    if status not in ("000", "013"):
                        raise DartAPIError(str(status))
                elif endpoint == "document.xml" and not content.startswith(b"PK"):
                    result = BeautifulSoup(content, "xml").find("result")
                    status = result.find("status") if result is not None else None
                    if status is not None and status.get_text(strip=True) not in ("000", "014"):
                        raise DartAPIError(status.get_text(strip=True))
                return content
            except (httpx.HTTPError, ValueError, DartAPIError) as error:
                exhausted = attempt + 1 == self.attempts
                (request_log.error if exhausted else request_log.warning)(
                    "dart_request_failed" if exhausted else "dart_request_retry",
                    attempt=attempt + 1,
                    max_attempts=self.attempts,
                    error_type=type(error).__name__,
                    http_status=(
                        error.response.status_code
                        if isinstance(error, httpx.HTTPStatusError)
                        else None
                    ),
                    dart_status=error.status if isinstance(error, DartAPIError) else None,
                    reason=(
                        "api_status"
                        if isinstance(error, DartAPIError)
                        else "invalid_response"
                        if isinstance(error, ValueError)
                        else "http_error"
                    ),
                )
                if exhausted:
                    if isinstance(error, DartAPIError):
                        request_log.error("dart_api_failed", dart_status=error.status)
                        raise error from None
                    raise RuntimeError("DART request failed") from None
                time.sleep(min(2**attempt, 8))
        raise RuntimeError("DART request failed")

    def json(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        return json.loads(self.get(endpoint, params))

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
