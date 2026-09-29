import pytest
import sqlalchemy as sa
from market_collector.symbols import EmptyIndexError, load_symbols

TABLES = "corporation_indices, corporations"


@pytest.fixture
def engine(pg_engine):
    with pg_engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {TABLES} CASCADE"))
    yield pg_engine
    with pg_engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {TABLES} CASCADE"))


def add_members(engine, index_name: str, *stock_codes: str) -> None:
    with engine.begin() as conn:
        for stock_code in stock_codes:
            conn.execute(
                sa.text(
                    "INSERT INTO corporations (stock_code, name, corp_code)"
                    " VALUES (:code, :code, 'dart' || :code) ON CONFLICT DO NOTHING"
                ),
                {"code": stock_code},
            )
            conn.execute(
                sa.text("INSERT INTO corporation_indices VALUES (:code, :index_name)"),
                {"code": stock_code, "index_name": index_name},
            )


def test_returns_the_sorted_members_of_the_named_index(engine, pg_dsn):
    add_members(engine, "KOSPI200", "005930", "0126Z0", "000660")
    add_members(engine, "OTHER", "035420", "005930")

    assert load_symbols(pg_dsn, "KOSPI200") == ["000660", "005930", "0126Z0"]
    assert load_symbols(pg_dsn, "OTHER") == ["005930", "035420"]


def test_an_index_without_rows_raises(engine, pg_dsn):
    add_members(engine, "OTHER", "005930")

    with pytest.raises(EmptyIndexError, match="KOSPI200"):
        load_symbols(pg_dsn, "KOSPI200")
