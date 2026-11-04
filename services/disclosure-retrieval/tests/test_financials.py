from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from disclosure_retrieval.financials import read_financials

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
