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
from ktb_core.logging import get_logger

from news_preprocessor.sources import FeedEntry, NewsItem
from news_preprocessor.sources.publishers.opendart.dart import (
    DartAPIError,
    DartClient,
    report_period,
)
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
HISTORY_DAYS = 365
HISTORY_LIMIT = 1000
log = get_logger(__name__)


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
        self._stock_codes = stock_codes
        self._settings = settings or Settings()
        self._dart = DartClient(
            client,
            api_key,
            self._settings.request_interval,
            self._settings.attempts,
            self._settings.max_archive_bytes,
        )

    def entries(self) -> list[FeedEntry]:
        if not self._stock_codes:
            raise RuntimeError("corporation_indices has no KOSPI200 rows; run market-syncer")
        today = datetime.now(KST).date()
        begin = self._settings.start_date or today - timedelta(days=1)
        end = self._settings.end_date or today
        if end > today:
            raise ValueError("historical end_date must not be in the future")
        if begin < today - timedelta(days=HISTORY_DAYS):
            raise ValueError("historical start_date must be within the last 365 days")
        historical = self._settings.start_date is not None
        entries: dict[str, FeedEntry] = {}
        cursor = end
        while cursor >= begin and (not historical or len(entries) < HISTORY_LIMIT):
            window_begin = max(begin, cursor - timedelta(days=89))
            window = {
                "bgn_de": f"{window_begin:%Y%m%d}",
                "end_de": f"{cursor:%Y%m%d}",
                "corp_cls": "Y",
                "sort": "date",
                "sort_mth": "desc",
            }
            rows = self._list(window, HISTORY_LIMIT - len(entries) if historical else None)
            if not rows:
                cursor = window_begin - timedelta(days=1)
                continue
            if historical:
                window["bgn_de"] = min(row["rcept_dt"] for row in rows)
            long = {
                row["rcept_no"]: detail_type
                for detail_type in self.long_detail_types
                for row in self._list({**window, "pblntf_detail_ty": detail_type})
            }
            for row in rows:
                if row["stock_code"] not in self._stock_codes:
                    continue
                entries[row["rcept_no"]] = FeedEntry(
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
            cursor = window_begin - timedelta(days=1)
        long_count = sum("pblntf_detail_ty" in json.loads(e.raw_payload) for e in entries.values())
        log.info(
            "opendart_listing_complete",
            start_date=begin.isoformat(),
            end_date=end.isoformat(),
            historical=historical,
            filings=len(entries),
            long_filings=long_count,
            short_filings=len(entries) - long_count,
        )
        return list(entries.values())

    def article(self, entry: FeedEntry) -> NewsItem:
        payload = json.loads(entry.raw_payload)
        detail_type = payload.get("pblntf_detail_ty")
        if detail_type in self.long_detail_types:
            settings = self._settings
            dart = self._dart
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
        content = io.BytesIO(self._dart.get("document.xml", {"rcept_no": entry.external_id}))
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

    def _list(self, params: dict[str, str], limit: int | None = None) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        page_no = 1
        while True:
            data: dict[str, Any] = {}
            try:
                data = self._dart.json(
                    "list.json", {"page_count": "100", "page_no": str(page_no), **params}
                )
            except Exception as error:
                log.error(
                    "opendart_listing_failed",
                    source=self.source,
                    endpoint="list.json",
                    start_date=params["bgn_de"],
                    end_date=params["end_de"],
                    detail_type=params.get("pblntf_detail_ty"),
                    page_no=page_no,
                    error_type=type(error).__name__,
                    http_status=(
                        error.response.status_code
                        if isinstance(error, httpx.HTTPStatusError)
                        else None
                    ),
                    dart_status=(
                        error.status
                        if isinstance(error, DartAPIError)
                        else data.get("status")
                        if isinstance(data, dict)
                        else None
                    ),
                )
                raise
            if data["status"] == "013":  # 013: No data
                return list(rows.values())
            for row in data["list"]:
                if row["stock_code"] not in self._stock_codes:
                    continue
                rows[row["rcept_no"]] = row
                if limit is not None and len(rows) >= limit:
                    return list(rows.values())
            if page_no >= data["total_page"]:
                return list(rows.values())
            page_no += 1
