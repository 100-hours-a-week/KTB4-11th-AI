import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import sqlalchemy as sa
from ktb_core.logging import get_logger

from news_preprocessor.sources.publishers.opendart.disclosures.dart import DartClient, report_period
from news_preprocessor.sources.publishers.opendart.disclosures.database import (
    disclosure_financial_facts,
    disclosure_sections,
)
from news_preprocessor.sources.publishers.opendart.disclosures.documents import (
    PARSER_VERSION,
    extract_documents,
)
from news_preprocessor.sources.publishers.opendart.disclosures.financials import (
    REPORT_CODES,
    SCOPES,
    financial_rows,
)
from news_preprocessor.sources.publishers.opendart.disclosures.sections import section_rows
from news_preprocessor.sources.publishers.opendart.disclosures.settings import Settings

log = get_logger(__name__)


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
        conn.execute(
            sa.text("UPDATE disclosures SET financials_collected=true WHERE receipt_id=:receipt"),
            {"receipt": report["receipt_id"]},
        )
    return len(rows)


def record_failure(engine: sa.Engine, receipt: str, error: Exception, max_attempts: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE disclosures SET attempts=LEAST(attempts+1,:max), "
                "status=CASE WHEN attempts+1>=:max THEN 'dead_letter' ELSE 'failed' END, "
                "error=:error,updated_at=now() WHERE receipt_id=:receipt"
            ),
            {"receipt": receipt, "error": type(error).__name__, "max": max_attempts},
        )
    log.warning("disclosure_failed", receipt_id=receipt, error_type=type(error).__name__)


def collect(engine: sa.Engine, client: httpx.Client, settings: Settings) -> dict[str, int]:
    from datetime import datetime

    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    dart = DartClient(
        client,
        settings.dart_api_key.get_secret_value(),
        settings.request_interval,
        settings.attempts,
        settings.max_archive_bytes,
    )
    result = {
        "ready": 0,
        "failed": 0,
        "unavailable": 0,
        "replaced": 0,
        "facts": 0,
    }
    refresh_sources(engine, dart, settings, today, result)
    with engine.connect() as conn:
        archived = (
            conn.execute(
                sa.text(
                    "SELECT r.receipt_id, d.archive_key, d.source_hash FROM disclosures r "
                    "JOIN LATERAL (SELECT archive_key, source_hash, parser_version, "
                    "extraction_status "
                    "FROM disclosure_documents WHERE receipt_id=r.receipt_id "
                    "ORDER BY id DESC LIMIT 1) d ON true "
                    "WHERE r.status IN ('pending','failed','ready','unavailable') "
                    "AND NOT (d.parser_version=:parser "
                    "AND d.extraction_status IN ('ready','unavailable')) "
                    "ORDER BY r.updated_at LIMIT :limit"
                ),
                {"parser": PARSER_VERSION, "limit": settings.document_limit},
            )
            .mappings()
            .all()
        )
    handled = set()
    for entry in archived:
        handled.add(entry["receipt_id"])
        try:
            archive = settings.archive_root / entry["archive_key"]
            content = (
                archive.read_bytes()
                if archive.is_file()
                else dart.get("document.xml", {"rcept_no": entry["receipt_id"]})
            )
            if archive.is_file() and hashlib.sha256(content).hexdigest() != entry["source_hash"]:
                raise ValueError("archive hash mismatch")
            result[store_document(engine, settings, entry["receipt_id"], content)] += 1
        except Exception as error:
            result["failed"] += 1
            record_failure(engine, entry["receipt_id"], error, settings.max_attempts)
    with engine.connect() as conn:
        pending = (
            conn.execute(
                sa.text(
                    "SELECT receipt_id, corp_code, title, detail_type FROM disclosures "
                    "WHERE status IN ('pending','failed') ORDER BY updated_at LIMIT :limit"
                ),
                {"limit": settings.document_limit},
            )
            .mappings()
            .all()
        )
    closing_months = {}
    for report in pending:
        if report["receipt_id"] in handled:
            continue
        try:
            closing = 12
            if re.search(r"\(\d{4}\.\d{2}\)", report["title"]):
                if report["corp_code"] not in closing_months:
                    closing_months[report["corp_code"]] = dart.closing_month(report["corp_code"])
                closing = closing_months[report["corp_code"]]
            year, kind, period = report_period(report["title"], closing, report["detail_type"])
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "UPDATE disclosures SET business_year=:year,kind=:kind,period_end=:period "
                        "WHERE receipt_id=:receipt"
                    ),
                    {"receipt": report["receipt_id"], "year": year, "kind": kind, "period": period},
                )
            content = dart.get("document.xml", {"rcept_no": report["receipt_id"]})
            result[store_document(engine, settings, report["receipt_id"], content)] += 1
        except Exception as error:
            result["failed"] += 1
            record_failure(engine, report["receipt_id"], error, settings.max_attempts)
    if settings.collect_financials:
        with engine.connect() as conn:
            pending = (
                conn.execute(
                    sa.text(
                        "SELECT receipt_id, stock_code, corp_code, business_year, kind, period_end "
                        "FROM disclosures WHERE status='ready' AND NOT financials_collected "
                        "AND kind IN ('annual','half','q1','q3') "
                        "AND (detail_type IN ('A001','A002','A003') OR detail_type IS NULL) "
                        "ORDER BY updated_at, receipt_id LIMIT :limit"
                    ),
                    {"limit": settings.document_limit},
                )
                .mappings()
                .all()
            )
        for report in pending:
            try:
                result["facts"] += store_financial_facts(engine, dart, settings, report)
            except Exception as error:
                result["failed"] += 1
                log.warning(
                    "disclosure_financials_failed",
                    receipt_id=report["receipt_id"],
                    error_type=type(error).__name__,
                )
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
                    "UPDATE disclosures SET status=:status, financials_collected=false, "
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
