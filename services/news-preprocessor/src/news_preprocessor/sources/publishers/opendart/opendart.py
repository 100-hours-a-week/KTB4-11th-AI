import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from news_preprocessor.sources import FeedEntry, NewsItem
from news_preprocessor.sources.publishers.opendart.dart import DartClient, report_period
from news_preprocessor.sources.publishers.opendart.financials import (
    REPORT_CODES,
    SCOPES,
    financial_rows,
)
from news_preprocessor.sources.publishers.opendart.parser import (
    extract_documents,
    parse_document_body,
)
from news_preprocessor.sources.publishers.opendart.settings import Settings

OPENDART_API = "https://opendart.fss.or.kr/api"
KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class DisclosureItem(NewsItem):
    detail_type: str
    business_year: int | None
    kind: str
    period_end: date | None
    archive_key: str
    source_hash: str
    documents: list[dict[str, Any]]
    financial_facts: list[dict[str, Any]]


class OpenDart:
    source = "opendart"
    feed_url = f"{OPENDART_API}/list.json"
    long_detail_types = (
        "A001", "A002", "A003", "F001", "F002", "F003",
        "C001", "C002", "C003", "C004", "C005", "J004", "H002",
    )  # fmt: skip

    def __init__(
        self,
        client: httpx.Client,
        api_key: str,
        stock_codes: set[str],
        settings: Settings | None = None,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._stock_codes = stock_codes
        self._settings = settings

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
            row["rcept_no"]: detail_type
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
                raw_payload=json.dumps(
                    {**row, "pblntf_detail_ty": long[row["rcept_no"]]}
                    if row["rcept_no"] in long
                    else row,
                    ensure_ascii=False,
                ),
            )
            for row in rows
            if row["stock_code"] in self._stock_codes
        ]

    def article(self, entry: FeedEntry) -> NewsItem:
        payload = json.loads(entry.raw_payload)
        detail_type = payload.get("pblntf_detail_ty")
        if detail_type in self.long_detail_types:
            settings = self._settings or Settings()
            dart = DartClient(
                self._client,
                self._api_key,
                settings.request_interval,
                settings.attempts,
                settings.max_archive_bytes,
            )
            content = dart.get("document.xml", {"rcept_no": entry.external_id})
            documents = extract_documents(content, settings.max_uncompressed_bytes)
            closing = (
                dart.closing_month(payload["corp_code"])
                if re.search(r"\(\d{4}\.\d{2}\)", payload["report_nm"])
                else 12
            )
            year, kind, period = report_period(payload["report_nm"], closing, detail_type)
            facts = []
            if (
                year is not None
                and period is not None
                and kind in REPORT_CODES
                and any(doc["status"] == "ready" for doc in documents)
            ):
                for scope in SCOPES:
                    accounts = dart.financial_statements(
                        payload["corp_code"], year, REPORT_CODES[kind], scope
                    )
                    facts.extend(financial_rows(accounts, scope))
            digest = hashlib.sha256(content).hexdigest()
            suffix = "zip" if zipfile.is_zipfile(io.BytesIO(content)) else "xml"
            archive_key = f"{digest[:2]}/{digest}.{suffix}"
            target = settings.archive_root / archive_key
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as file:
                    file.write(content)
                    temporary = Path(file.name)
                os.replace(temporary, target)
            return DisclosureItem(
                **asdict(entry),
                body="",
                detail_type=detail_type,
                business_year=year,
                kind=kind,
                period_end=period,
                archive_key=archive_key,
                source_hash=digest,
                documents=[
                    {key: value for key, value in document.items() if key != "body"}
                    for document in documents
                ],
                financial_facts=facts,
            )
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
