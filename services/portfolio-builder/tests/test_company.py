import json
from datetime import UTC, datetime
from uuid import uuid4

import numpy as np
import pytest
import sqlalchemy as sa
from portfolio_builder.company import Company, resolve_company
from portfolio_builder.errors import UnknownCompany
from portfolio_builder.measurement import Bars
from portfolio_builder.tools.technicals import technicals_tool


@pytest.fixture(params=["sqlite", "postgres"])
def company_engine(request):
    schema = None
    if request.param == "postgres":
        pg_engine = request.getfixturevalue("pg_engine")
        schema = f"company_test_{uuid4().hex}"
        with pg_engine.begin() as conn:
            conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
        engine = sa.create_engine(
            pg_engine.url, connect_args={"options": f"-csearch_path={schema}"}
        )
    else:
        engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE corporations (stock_code TEXT PRIMARY KEY, corp_code TEXT UNIQUE,"
                " name TEXT, eng_name TEXT)"
            )
        )
        conn.execute(
            sa.text("CREATE TABLE corporation_aliases (alias TEXT PRIMARY KEY, stock_code TEXT)")
        )
        conn.execute(
            sa.text(
                "INSERT INTO corporations VALUES"
                " ('000720', '00164742', '현대건설', 'Hyundai Engineering & Construction'),"
                " ('005930', '00126380', '삼성전자', 'Samsung Electronics'),"
                " ('123456', '12345678', '다른기업', NULL)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO corporation_aliases VALUES"
                " ('현대건설', '000720'), ('현건', '000720'), ('삼성전자', '005930'),"
                " ('000721', '123456'), ('00164743', '123456')"
            )
        )
    try:
        yield engine
    finally:
        engine.dispose()
        if schema is not None:
            with pg_engine.begin() as conn:
                conn.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))


@pytest.mark.parametrize(
    "identifier",
    [
        "000720",
        " 000720 ",
        "00164742",
        "현대건설",
        "HYUNDAI ENGINEERING & CONSTRUCTION",
        "현건",
        "㈜현대 건설",
    ],
)
def test_company_identifiers_resolve_to_the_same_listed_company(company_engine, identifier):
    assert resolve_company(company_engine, identifier) == Company("00164742", "현대건설", "000720")


def test_canonical_name_resolves_without_an_alias(company_engine):
    with company_engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM corporation_aliases WHERE stock_code = '000720'"))
    assert resolve_company(company_engine, "현대건설") == Company("00164742", "현대건설", "000720")


@pytest.mark.parametrize(
    ("identifier", "code_field"), [("000721", "stock_code"), ("00164743", "corp_code")]
)
def test_missing_codes_report_their_type_without_resolving_a_numeric_alias(
    company_engine, identifier, code_field
):
    with pytest.raises(UnknownCompany, match=rf'{code_field} "{identifier}"'):
        resolve_company(company_engine, identifier)


@pytest.mark.parametrize("identifier", ["", "   "])
def test_blank_identifier_is_rejected(company_engine, identifier):
    with pytest.raises(UnknownCompany, match="empty company identifier"):
        resolve_company(company_engine, identifier)


def test_partial_name_reports_candidates_instead_of_selecting_one(company_engine):
    with pytest.raises(UnknownCompany, match='no company matches "현대". Candidates: 현대건설'):
        resolve_company(company_engine, "현대")


def test_unknown_english_text_reports_matching_name_candidates(company_engine):
    with pytest.raises(UnknownCompany, match='no company matches "Samsung". Candidates: 삼성전자'):
        resolve_company(company_engine, "Samsung")


@pytest.mark.parametrize("identifier", ["%", "_", "없는기업"])
def test_unknown_text_is_literal_and_reports_no_candidates(company_engine, identifier):
    with pytest.raises(UnknownCompany, match="Candidates: none"):
        resolve_company(company_engine, identifier)


@pytest.mark.parametrize("identifier", ["000720", "00164742", "현대건설", "현건"])
def test_analyze_technicals_uses_the_resolved_stock_code(company_engine, identifier):
    class Market:
        def bars(self, symbol, timeframe):
            assert (symbol, timeframe) == ("000720", "1d")
            close = np.arange(1, 301, dtype=np.float64)
            return (
                Bars(close.copy(), close.copy(), close, np.full(300, 1000.0)),
                datetime(2026, 10, 2, 6, 30, tzinfo=UTC),
            )

        def universe_closes(self):
            return {"000720": np.arange(1, 301, dtype=np.float64)}

    result = json.loads(
        technicals_tool(company_engine, Market()).invoke({"name": identifier, "timeframe": "1d"})
    )
    assert result["company"] == "현대건설"
    assert result["company_id"] == "00164742"
    assert result["stock_code"] == "000720"
    assert result["bars"] == 300
    assert result["signals"]["trend"]["state"] == "established_uptrend"
