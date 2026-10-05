import io
import json
import zipfile
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from news_preprocessor.sources import FeedEntry, NewsItem
from news_preprocessor.sources.publishers.opendart.parser import parse_document_body

OPENDART_API = "https://opendart.fss.or.kr/api"
KST = timezone(timedelta(hours=9))


class OpenDart:
    source = "opendart"
    feed_url = f"{OPENDART_API}/list.json"
    # Periodic, audit and registration filings are too long to embed; see #184 and #185.
    long_detail_types = (
        "A001", "A002", "A003", "F001", "F002", "F003",
        "C001", "C002", "C003", "C004", "C005", "J004", "H002",
    )  # fmt: skip

    def __init__(self, client: httpx.Client, api_key: str, stock_codes: set[str]) -> None:
        self._client = client
        self._api_key = api_key
        self._stock_codes = stock_codes

    def entries(self) -> list[FeedEntry]:
        if not self._stock_codes:
            raise RuntimeError("corporation_indices has no KOSPI200 rows; run market-syncer")
        today = datetime.now(KST).date()
        window = {
            "bgn_de": f"{today - timedelta(days=1):%Y%m%d}",
            "end_de": f"{today:%Y%m%d}",
            "corp_cls": "Y",
        }
        rows = self._list(window)
        long = {
            row["rcept_no"]
            for detail_type in self.long_detail_types
            for row in self._list({**window, "pblntf_detail_ty": detail_type})
        }
        return [
            FeedEntry(
                source=self.source,
                external_id=row["rcept_no"],
                url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={row['rcept_no']}",
                title=" ".join(f"{row['corp_name']} {row['report_nm']}".split()),
                published_at=datetime.strptime(row["rcept_dt"], "%Y%m%d").replace(tzinfo=KST),
                raw_payload=json.dumps(row, ensure_ascii=False),
            )
            for row in rows
            if row["rcept_no"] not in long and row["stock_code"] in self._stock_codes
        ]

    def article(self, entry: FeedEntry) -> NewsItem:
        response = self._client.get(
            f"{OPENDART_API}/document.xml",
            params={"crtfc_key": self._api_key, "rcept_no": entry.external_id},
            timeout=60,
        )
        content = io.BytesIO(response.raise_for_status().content)
        if not zipfile.is_zipfile(content):
            status = BeautifulSoup(content.getvalue(), "xml").find("status")
            status = status.get_text(strip=True) if status else "unknown"
            # 014: DART keeps no document for the filing, like an attachment-only correction.
            if status != "014":
                raise RuntimeError(f"DART document.xml status {status}")
            return NewsItem(**asdict(entry), body="")
        with zipfile.ZipFile(content) as archive:
            names = archive.namelist()
            main = f"{entry.external_id}.xml"
            document = archive.read(main if main in names else names[0])
        return NewsItem(**asdict(entry), body=parse_document_body(document))

    def _list(self, params: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_no = 1
        while True:
            response = self._client.get(
                self.feed_url,
                params={
                    "crtfc_key": self._api_key,
                    "page_count": "100",
                    "page_no": str(page_no),
                    **params,
                },
                timeout=30,
            )
            data = response.raise_for_status().json()
            if data["status"] == "013":  # 013: No data
                return rows
            if data["status"] != "000":
                raise RuntimeError(f"DART list.json status {data['status']}: {data['message']}")
            rows.extend(data["list"])
            if page_no >= data["total_page"]:
                return rows
            page_no += 1
