import calendar
import hashlib
import io
import json
import os
import tempfile
import zipfile
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import sqlalchemy as sa
from ktb_core.logging import get_logger

from disclosure_indexer.dart import DartClient, report_period
from disclosure_indexer.database import disclosure_financial_facts, disclosure_sections
from disclosure_indexer.documents import PARSER_VERSION, extract_documents
from disclosure_indexer.financials import REPORT_CODES, SCOPES, financial_rows
from disclosure_indexer.sections import section_rows
from disclosure_indexer.settings import Settings

log = get_logger(__name__)


def collection_windows(
    today: date, closing: int, settings: Settings
) -> tuple[int, list[tuple[date, date]]]:
    """The 90-day DART listing windows covering the business year under collection."""
    last_close = date(today.year, closing, calendar.monthrange(today.year, closing)[1])
    completed = today.year if last_close < today else today.year - 1
    year = settings.business_year or completed
    previous_close = date(year - 1, closing, calendar.monthrange(year - 1, closing)[1])
    start = previous_close + timedelta(days=1)
    end = (
        today
        if settings.business_year is None
        else min(today, date(year + 1, closing, calendar.monthrange(year + 1, closing)[1]))
    )
    windows = []
    while start <= end:
        finish = min(end, start + timedelta(days=89))
        windows.append((start, finish))
        start = finish + timedelta(days=1)
    return year, windows


def refresh_sources(
    engine: sa.Engine, dart: DartClient, settings: Settings, today: date, result: dict[str, int]
) -> None:
    """Re-fetch recently published receipts and re-store any whose source bytes changed.

    A disclosure re-published under the same receipt number is invisible otherwise: the
    receipt is already `ready`, so nothing downloads it again to notice the new hash.
    """
    if not settings.refresh_days:
        return
    with engine.connect() as conn:
        recent = (
            conn.execute(
                sa.text("""
                    SELECT r.receipt_id, d.source_hash
                    FROM disclosures r
                    JOIN LATERAL (
                        SELECT source_hash FROM disclosure_documents
                        WHERE receipt_id = r.receipt_id ORDER BY id DESC LIMIT 1
                    ) d ON true
                    WHERE r.status IN ('ready', 'unavailable')
                        AND r.published_date >= :cutoff
                    ORDER BY r.published_date DESC, r.receipt_id DESC
                    LIMIT :limit
                """),
                {
                    "cutoff": today - timedelta(days=settings.refresh_days),
                    "limit": settings.document_limit,
                },
            )
            .mappings()
            .all()
        )
    for entry in recent:
        try:
            content = dart.get("document.xml", {"rcept_no": entry["receipt_id"]})
            if hashlib.sha256(content).hexdigest() == entry["source_hash"]:
                continue
            result[store_document(engine, settings, entry["receipt_id"], content)] += 1
            result["replaced"] += 1
            log.info("disclosure_source_replaced", receipt_id=entry["receipt_id"])
        except Exception as error:
            result["failed"] += 1
            log.warning(
                "disclosure_refresh_failed",
                receipt_id=entry["receipt_id"],
                error_type=type(error).__name__,
            )


def store_financial_facts(
    engine: sa.Engine, dart: DartClient, settings: Settings, report: Mapping[str, Any]
) -> int:
    """Fetch and replace one filing's structured accounts, consolidated and separate.

    Read without a GPU, so a fetch failure must not change the filing's own status: the
    documents and sections are already usable.
    """
    report_code = REPORT_CODES[report["kind"]]
    rows = []
    for scope in SCOPES:
        payload = dart.financial_statements(
            report["corp_code"], report["business_year"], report_code, scope
        )
        rows.extend(
            {
                **row,
                "receipt_id": report["receipt_id"],
                "stock_code": report["stock_code"],
                "business_year": report["business_year"],
                "kind": report["kind"],
                "period_end": report["period_end"],
            }
            for row in financial_rows(payload, scope)
        )
    with engine.begin() as conn:
        conn.execute(
            sa.delete(disclosure_financial_facts).where(
                disclosure_financial_facts.c.receipt_id == report["receipt_id"]
            )
        )
        if rows:
            conn.execute(sa.insert(disclosure_financial_facts), rows)
    return len(rows)


def collect(engine: sa.Engine, client: httpx.Client, settings: Settings) -> dict[str, int]:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    dart = DartClient(
        client,
        settings.dart_api_key.get_secret_value(),
        settings.request_interval,
        settings.attempts,
        settings.max_archive_bytes,
    )
    with engine.connect() as conn:
        companies = (
            conn.execute(
                sa.text(
                    "SELECT c.stock_code, c.corp_code, c.name FROM corporations c JOIN"
                    " corporation_indices i USING(stock_code) WHERE i.index_name = "
                    "'KOSPI200' ORDER BY c.stock_code"
                )
            )
            .mappings()
            .all()
        )
    if not companies:
        raise RuntimeError("no KOSPI200 companies; run market-syncer")
    result = {
        "ready": 0,
        "failed": 0,
        "skipped": 0,
        "unavailable": 0,
        "unclassified": 0,
        "replaced": 0,
        "facts": 0,
    }
    # Reconcile what is already stored: a parser upgrade, an interrupted parse, or a lost
    # archive file. Bounded like the indexing loop so one run cannot re-parse everything.
    with engine.connect() as conn:
        archived = (
            conn.execute(
                sa.text("""
                    SELECT r.receipt_id, d.archive_key, d.source_hash, d.parser_version,
                        d.extraction_status
                    FROM disclosures r
                    JOIN LATERAL (
                        SELECT id, archive_key, source_hash, parser_version, extraction_status
                        FROM disclosure_documents
                        WHERE receipt_id = r.receipt_id ORDER BY id DESC LIMIT 1
                    ) d ON true
                    WHERE r.status IN ('pending', 'failed', 'ready', 'unavailable')
                        AND NOT (
                            d.parser_version = :parser
                            AND d.extraction_status IN ('ready', 'unavailable')
                        )
                    ORDER BY r.updated_at
                    LIMIT :limit
                """),
                {"parser": PARSER_VERSION, "limit": settings.document_limit},
            )
            .mappings()
            .all()
        )
    for entry in archived:
        try:
            archive = settings.archive_root / entry["archive_key"]
            content = (
                archive.read_bytes()
                if archive.is_file()
                else dart.get("document.xml", {"rcept_no": entry["receipt_id"]})
            )
            if len(content) > settings.max_archive_bytes:
                raise ValueError("archive exceeds configured limit")
            if hashlib.sha256(content).hexdigest() != entry["source_hash"]:
                if archive.is_file():
                    raise ValueError("archive hash mismatch")
            result[store_document(engine, settings, entry["receipt_id"], content)] += 1
        except Exception as error:
            result["failed"] += 1
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "UPDATE disclosures SET attempts=LEAST(attempts+1,:max),"
                        "status=CASE WHEN attempts+1>=:max THEN 'dead_letter' ELSE 'failed' END,"
                        "error=:error,updated_at=now() WHERE receipt_id=:receipt"
                    ),
                    {
                        "receipt": entry["receipt_id"],
                        "error": type(error).__name__,
                        "max": settings.max_attempts,
                    },
                )
            log.warning(
                "disclosure_reparse_failed",
                receipt_id=entry["receipt_id"],
                error_type=type(error).__name__,
            )
    for company in companies:
        try:
            closing = dart.closing_month(company["corp_code"])
            year, windows = collection_windows(today, closing, settings)
        except Exception as error:
            result["failed"] += 1
            log.warning(
                "disclosure_company_failed",
                stock_code=company["stock_code"],
                error_type=type(error).__name__,
            )
            continue
        # One window per try: a transient listing failure costs that window, not the rest.
        for begin, finish in windows:
            try:
                with engine.connect() as conn:
                    done = conn.execute(
                        sa.text(
                            "SELECT complete FROM disclosure_collection_progress WHERE "
                            "stock_code=:stock AND start_date=:start AND end_date=:end "
                            "AND business_year=:year AND include_current=:current"
                        ),
                        {
                            "stock": company["stock_code"],
                            "start": begin,
                            "end": finish,
                            "year": year,
                            "current": settings.business_year is None,
                        },
                    ).scalar()
                if done and finish < today - timedelta(days=settings.recheck_days):
                    continue
                complete = True
                for row in dart.reports(company["corp_code"], begin, finish):
                    try:
                        fiscal_year, kind, period = report_period(row["report_nm"], closing)
                    except ValueError:
                        # The closing month is read fresh each run, so a company that changed
                        # its fiscal year stops matching its own history. Never silent.
                        result["unclassified"] += 1
                        log.warning(
                            "disclosure_unclassified",
                            stock_code=company["stock_code"],
                            title=row["report_nm"],
                            closing_month=closing,
                        )
                        continue
                    if (
                        fiscal_year < year
                        or settings.business_year is not None
                        and fiscal_year != year
                    ):
                        continue
                    receipt = row["rcept_no"]
                    params = {
                        "receipt": receipt,
                        "stock": company["stock_code"],
                        "corp": company["corp_code"],
                        "name": company["name"],
                        "title": row["report_nm"],
                        "year": fiscal_year,
                        "kind": kind,
                        "period": period,
                        "date": date.fromisoformat(
                            f"{row['rcept_dt'][:4]}-{row['rcept_dt'][4:6]}-{row['rcept_dt'][6:8]}"
                        ),
                        "payload": json.dumps(row, ensure_ascii=False),
                    }
                    params_row = {
                        "receipt_id": receipt,
                        "stock_code": company["stock_code"],
                        "corp_code": company["corp_code"],
                        "business_year": fiscal_year,
                        "kind": kind,
                        "period_end": period,
                    }
                    with engine.begin() as conn:
                        conn.execute(
                            sa.text(
                                "INSERT INTO "
                                "disclosures(receipt_id,stock_code,corp_code,company_name,tit"
                                "le,business_year,kind,period_end,published_date,raw_payload)"
                                " VALUES "
                                "(:receipt,:stock,:corp,:name,:title,:year,:kind,:period,:date,CAST(:payload"
                                " AS jsonb)) ON CONFLICT(receipt_id) DO NOTHING"
                            ),
                            params,
                        )
                        status = conn.execute(
                            sa.text("SELECT status FROM disclosures WHERE receipt_id=:receipt"),
                            params,
                        ).scalar_one()
                    if status in ("ready", "unavailable", "dead_letter"):
                        result["skipped"] += 1
                        continue
                    try:
                        with engine.connect() as conn:
                            archived = conn.execute(
                                sa.text(
                                    "SELECT archive_key FROM disclosure_documents WHERE "
                                    "receipt_id=:receipt ORDER BY id DESC LIMIT 1"
                                ),
                                {"receipt": receipt},
                            ).scalar()
                        if archived:
                            content = (settings.archive_root / archived).read_bytes()
                            if not zipfile.is_zipfile(io.BytesIO(content)):
                                try:
                                    extract_documents(content, settings.max_uncompressed_bytes)
                                except ValueError:
                                    content = dart.get("document.xml", {"rcept_no": receipt})
                        else:
                            content = dart.get("document.xml", {"rcept_no": receipt})
                        status = store_document(engine, settings, receipt, content)
                        result[status] += 1
                        if status == "ready" and settings.collect_financials:
                            try:
                                stored = store_financial_facts(engine, dart, settings, params_row)
                                result["facts"] += stored
                            except Exception as error:
                                result["failed"] += 1
                                log.warning(
                                    "disclosure_financials_failed",
                                    receipt_id=receipt,
                                    error_type=type(error).__name__,
                                )
                    except Exception as error:
                        complete = False
                        result["failed"] += 1
                        with engine.begin() as conn:
                            conn.execute(
                                sa.text(
                                    "UPDATE disclosures SET attempts=LEAST(attempts+1,:max), "
                                    "status=CASE WHEN attempts+1>=:max THEN 'dead_letter' "
                                    "ELSE 'failed' END,error=:error,updated_at=now() "
                                    "WHERE receipt_id=:receipt"
                                ),
                                {
                                    "receipt": receipt,
                                    "error": type(error).__name__,
                                    "max": settings.max_attempts,
                                },
                            )
                        log.warning(
                            "disclosure_failed", receipt_id=receipt, error_type=type(error).__name__
                        )
                with engine.begin() as conn:
                    conn.execute(
                        sa.text(
                            "INSERT INTO "
                            "disclosure_collection_progress(stock_code,start_date,end_dat"
                            "e,business_year,include_current,complete)"
                            " VALUES (:stock,:start,:end,:year,:current,:complete) ON "
                            "CONFLICT(stock_code,start_date,end_date,business_year,includ"
                            "e_current) DO UPDATE SET "
                            "complete=excluded.complete, checked_at=now()"
                        ),
                        {
                            "stock": company["stock_code"],
                            "start": begin,
                            "end": finish,
                            "year": year,
                            "current": settings.business_year is None,
                            "complete": complete,
                        },
                    )
            except Exception as error:
                result["failed"] += 1
                log.warning(
                    "disclosure_window_failed",
                    stock_code=company["stock_code"],
                    start_date=begin.isoformat(),
                    end_date=finish.isoformat(),
                    error_type=type(error).__name__,
                )
    refresh_sources(engine, dart, settings, today, result)
    return result


def store_document(engine: sa.Engine, settings: Settings, receipt: str, content: bytes) -> str:
    if len(content) > settings.max_archive_bytes:
        raise ValueError("archive exceeds configured limit")
    if not zipfile.is_zipfile(io.BytesIO(content)):
        extract_documents(content, settings.max_uncompressed_bytes)
    digest = hashlib.sha256(content).hexdigest()
    suffix = "zip" if zipfile.is_zipfile(io.BytesIO(content)) else "xml"
    key = f"{digest[:2]}/{digest}.{suffix}"
    target = settings.archive_root / key
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as file:
            file.write(content)
            temporary = Path(file.name)
        os.replace(temporary, target)
    with engine.begin() as conn:
        document_id = conn.execute(
            sa.text("""
            INSERT INTO disclosure_documents(receipt_id,archive_key,source_hash,
                parser_version,documents,extraction_status)
            VALUES (:receipt,:key,:hash,:parser,'[]','pending')
            ON CONFLICT(receipt_id,source_hash,parser_version)
            DO UPDATE SET archive_key=excluded.archive_key RETURNING id
        """),
            {"receipt": receipt, "key": key, "hash": digest, "parser": PARSER_VERSION},
        ).scalar_one()
    try:
        docs = extract_documents(content, settings.max_uncompressed_bytes)
        docs = [
            {key: value for key, value in document.items() if key != "body"} for document in docs
        ]
        status = "ready" if any(doc["status"] == "ready" for doc in docs) else "unavailable"
        rows = section_rows(docs)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "UPDATE disclosure_documents SET documents=CAST(:docs AS "
                    "jsonb),extraction_status=:status WHERE id=:id"
                ),
                {"docs": json.dumps(docs, ensure_ascii=False), "status": status, "id": document_id},
            )
            # Sections are readable without embeddings, so they land with the parse and are
            # replaced wholesale when this document version is parsed again.
            conn.execute(
                sa.delete(disclosure_sections).where(
                    disclosure_sections.c.document_id == document_id
                )
            )
            if rows:
                conn.execute(
                    sa.insert(disclosure_sections),
                    [
                        {
                            **row,
                            "document_id": document_id,
                            "receipt_id": receipt,
                            "parser_version": PARSER_VERSION,
                        }
                        for row in rows
                    ],
                )
            conn.execute(
                sa.text(
                    "UPDATE disclosures SET status=:status, "
                    "attempts=0,error=NULL,updated_at=now() WHERE "
                    "receipt_id=:receipt"
                ),
                {"status": status, "receipt": receipt},
            )
        return status
    except Exception:
        with engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE disclosure_documents SET extraction_status='failed' WHERE id=:id"),
                {"id": document_id},
            )
        raise
