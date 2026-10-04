import pytest
import sqlalchemy as sa

TABLES = "portfolio_reasons, portfolio_exits, portfolio_holdings, portfolios, corporations"


def _truncate(engine):
    with engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def engine(pg_engine):
    _truncate(pg_engine)
    with pg_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO corporations (stock_code, corp_code, name) VALUES"
                " ('005930', '00126380', '삼성전자'), ('000660', '00164779', 'SK하이닉스'),"
                " ('373220', '01515323', 'LG에너지솔루션')"
            )
        )
    yield pg_engine
    _truncate(pg_engine)
