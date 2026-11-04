from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from disclosure_retrieval.financials import read_financials
from disclosure_retrieval.tables import disclosure_financial_facts, disclosures, metadata

ACCOUNTS = [
    ("CFS", "BS", "ifrs-full_Assets", "자산총계", "1000.00", "1,000", True),
    ("OFS", "BS", "ifrs-full_Assets", "자산총계", "900.00", "900", True),
    ("CFS", "IS", "ifrs-full_Revenue", "매출액", None, "해당사항 없음", False),
]


@pytest.fixture
def facts(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO corporations(stock_code,corp_code,name) VALUES "
                "('005930','0005930','회사') ON CONFLICT DO NOTHING"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO disclosures(receipt_id,stock_code,corp_code,company_name,title,"
                "business_year,kind,period_end,published_date,raw_payload,status) VALUES "
                "('fin-1','005930','0005930','회사','사업보고서',2025,'annual','2025-12-31',"
                "'2026-03-01','{}','ready')"
            )
        )
        for ordinal, (scope, statement, account, name, value, source, ok) in enumerate(ACCOUNTS):
            conn.execute(
                sa.text(
                    "INSERT INTO disclosure_financial_facts(receipt_id,stock_code,business_year,"
                    "kind,period_end,scope,statement_type,account_id,account_name,currency,"
                    "ordinal,source_value,value,normalized) VALUES "
                    "('fin-1','005930',2025,'annual','2025-12-31',:scope,:statement,:account,"
                    ":name,'KRW',:ordinal,:source,CAST(:value AS numeric),:ok)"
                ),
                {
                    "scope": scope,
                    "statement": statement,
                    "account": account,
                    "name": name,
                    "ordinal": ordinal,
                    "source": source,
                    "value": value,
                    "ok": ok,
                },
            )
    return engine


def test_financials_require_an_explicit_period(facts):
    with facts.connect() as conn, pytest.raises(ValueError, match="period_end"):
        read_financials(conn, "005930")


def test_financials_are_readable_by_business_year(facts):
    with facts.connect() as conn:
        rows = read_financials(conn, "005930", business_year=2025)
    assert {row.scope for row in rows} == {"CFS", "OFS"}
    assert all(row.receipt_id == "fin-1" and row.period_end == date(2025, 12, 31) for row in rows)


def test_financials_never_merge_scopes(facts):
    with facts.connect() as conn:
        rows = read_financials(conn, "005930", business_year=2025, account_id="ifrs-full_Assets")
    assert sorted((row.scope, row.value) for row in rows) == [
        ("CFS", Decimal("1000.00")),
        ("OFS", Decimal("900.00")),
    ]


def test_a_requested_scope_excludes_the_other(facts):
    with facts.connect() as conn:
        rows = read_financials(conn, "005930", period_end=date(2025, 12, 31), scope="OFS")
    assert [row.scope for row in rows] == ["OFS"]


def test_an_unparseable_amount_is_served_as_its_source_string(facts):
    with facts.connect() as conn:
        (row,) = read_financials(conn, "005930", business_year=2025, statement_type="IS")
    assert row.value is None and row.source_value == "해당사항 없음" and row.normalized is False


def test_financials_of_another_company_are_not_returned(facts):
    with facts.connect() as conn:
        assert read_financials(conn, "000660", business_year=2025) == []


def test_a_financial_read_makes_no_embedding_call(facts, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected embedding")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    with facts.connect() as conn:
        assert read_financials(conn, "005930", business_year=2025)


@pytest.fixture
def sqlite_financials():
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as conn:
        yield conn
    engine.dispose()


def insert_filing(
    conn,
    receipt_id,
    published_date,
    values,
    *,
    title="사업보고서",
    stock_code="005930",
    kind="annual",
    detail_type=None,
    status="ready",
    business_year=2025,
    period_end=date(2025, 12, 31),
):
    report = {
        "receipt_id": receipt_id,
        "title": title,
        "stock_code": stock_code,
        "business_year": business_year,
        "kind": kind,
        "detail_type": detail_type,
        "period_end": period_end,
        "published_date": published_date,
        "status": status,
    }
    conn.execute(disclosures.insert(), report)
    for ordinal, (scope, account, value) in enumerate(values):
        conn.execute(
            disclosure_financial_facts.insert(),
            {
                "id": conn.scalar(
                    sa.select(sa.func.count()).select_from(disclosure_financial_facts)
                )
                + 1,
                "receipt_id": receipt_id,
                "stock_code": stock_code,
                "business_year": business_year,
                "kind": kind,
                "period_end": period_end,
                "scope": scope,
                "statement_type": "BS",
                "account_id": account,
                "account_name": account,
                "currency": "KRW",
                "ordinal": ordinal,
                "source_value": value if value is not None else "해당사항 없음",
                "value": Decimal(value) if value is not None else None,
                "normalized": value is not None,
            },
        )


def test_latest_ready_filing_replaces_original_before_limit(sqlite_financials):
    conn = sqlite_financials
    insert_filing(conn, "original", date(2026, 3, 1), [("CFS", "assets", "100")])
    insert_filing(
        conn,
        "correction",
        date(2026, 3, 2),
        [("CFS", "assets", "200")],
        detail_type="A001",
        title="[기재정정]사업보고서",
    )
    (row,) = read_financials(conn, "005930", business_year=2025, limit=1)
    assert (row.receipt_id, row.value) == ("correction", Decimal("200"))
    assert row.published_date == date(2026, 3, 2)
    assert row.title == "[기재정정]사업보고서"
    assert row.previous_receipt_id == "original"


def test_latest_filing_uses_date_then_receipt_id(sqlite_financials):
    conn = sqlite_financials
    insert_filing(conn, "999", date(2026, 3, 1), [("CFS", "assets", "100")])
    insert_filing(conn, "001", date(2026, 3, 2), [("CFS", "assets", "200")])
    insert_filing(conn, "002", date(2026, 3, 2), [("CFS", "assets", "300")])
    rows = read_financials(conn, "005930", business_year=2025)
    assert [(row.receipt_id, row.value) for row in rows] == [("002", Decimal("300"))]
    assert rows[0].previous_receipt_id == "001"


def test_missing_account_in_latest_filing_does_not_fall_back(sqlite_financials):
    conn = sqlite_financials
    insert_filing(conn, "original", date(2026, 3, 1), [("CFS", "assets", "100")])
    insert_filing(conn, "latest", date(2026, 3, 2), [("CFS", "liabilities", "200")])
    assert read_financials(conn, "005930", business_year=2025, account_id="assets") == []


def test_latest_ready_filing_with_no_facts_does_not_fall_back(sqlite_financials):
    conn = sqlite_financials
    insert_filing(conn, "original", date(2026, 3, 1), [("CFS", "assets", "100")])
    insert_filing(conn, "latest", date(2026, 3, 2), [])
    assert read_financials(conn, "005930", business_year=2025) == []


def test_filing_families_and_companies_do_not_supersede_each_other(sqlite_financials):
    conn = sqlite_financials
    for receipt, options in [
        ("annual", {}),
        ("half", {"kind": "half"}),
        ("other-detail", {"detail_type": "F002"}),
        ("other-company", {"stock_code": "000660"}),
        ("other-year", {"business_year": 2024}),
        ("other-period", {"period_end": date(2025, 6, 30)}),
    ]:
        insert_filing(conn, receipt, date(2026, 3, 1), [("CFS", "assets", "100")], **options)
    rows = read_financials(conn, "005930", business_year=2025, period_end=date(2025, 12, 31))
    assert {row.receipt_id for row in rows} == {"annual", "half", "other-detail"}
    assert all(row.previous_receipt_id is None for row in rows)


def test_correction_keeps_scopes_and_null_sources_separate(sqlite_financials):
    conn = sqlite_financials
    insert_filing(conn, "original", date(2026, 3, 1), [("OFS", "assets", "100")])
    insert_filing(
        conn,
        "latest",
        date(2026, 3, 2),
        [("CFS", "assets", "200"), ("OFS", "assets", None)],
    )
    rows = read_financials(conn, "005930", business_year=2025, account_id="assets")
    assert [(row.scope, row.value) for row in rows] == [("CFS", Decimal("200")), ("OFS", None)]
    (row,) = read_financials(conn, "005930", business_year=2025, scope="OFS")
    assert row.source_value == "해당사항 없음" and row.normalized is False
    assert row.receipt_id == "latest"


@pytest.mark.parametrize("status", ["pending", "unavailable", "failed"])
def test_newer_unusable_filing_suppresses_stale_facts(sqlite_financials, status):
    conn = sqlite_financials
    insert_filing(conn, "original", date(2026, 3, 1), [("CFS", "assets", "100")])
    insert_filing(conn, "latest", date(2026, 3, 2), [], status=status)
    assert read_financials(conn, "005930", business_year=2025) == []


def test_previous_receipt_is_nearest_stored_filing_even_if_unavailable(sqlite_financials):
    conn = sqlite_financials
    insert_filing(conn, "original", date(2026, 3, 1), [("CFS", "assets", "100")])
    insert_filing(conn, "unavailable", date(2026, 3, 2), [], status="unavailable")
    insert_filing(conn, "latest", date(2026, 3, 3), [("CFS", "assets", "200")])
    (row,) = read_financials(conn, "005930", business_year=2025)
    assert row.previous_receipt_id == "unavailable"


def test_missing_scope_in_latest_filing_does_not_fall_back(sqlite_financials):
    conn = sqlite_financials
    insert_filing(conn, "original", date(2026, 3, 1), [("OFS", "assets", "100")])
    insert_filing(conn, "latest", date(2026, 3, 2), [("CFS", "assets", "200")])
    assert read_financials(conn, "005930", business_year=2025, scope="OFS") == []
