import pytest
import sqlalchemy as sa
from portfolio_rebalancer.portfolio import find_latest_portfolio

pytestmark = pytest.mark.usefixtures("migrated")


def test_no_portfolio_reads_as_none_rather_than_an_empty_one(conn):
    """An empty Portfolio would look like "sell everything"; None means "nothing decided yet"."""
    assert find_latest_portfolio(conn) is None


def test_the_newest_portfolio_wins(conn):
    older, newer = (
        conn.execute(
            sa.text(
                "INSERT INTO portfolios (created_at, cash_weight, commentary, model)"
                f" VALUES ('{when}', {weight}, '', 'test') RETURNING id"
            )
        ).scalar()
        for when, weight in (("2026-09-20T00:00:00Z", 0.1), ("2026-09-27T00:00:00Z", 0.2))
    )

    portfolio = find_latest_portfolio(conn)

    assert portfolio.portfolio_id == newer != older
    assert portfolio.cash_weight == 0.2


def test_a_holding_carries_the_stock_code_joined_in_from_corporations(conn, portfolio_id):
    """company_id is DART's corp_code, which no exchange accepts as an order identifier."""
    conn.execute(
        sa.text(
            "INSERT INTO corporations (stock_code, corp_code, name)"
            " VALUES ('005930', '00126380', '삼성전자')"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight, reason)"
            " VALUES (:pid, '00126380', 0.3, '반도체 업황 반등')"
        ),
        {"pid": portfolio_id},
    )

    holding = find_latest_portfolio(conn).holdings[0]

    assert (holding.company_id, holding.stock_code) == ("00126380", "005930")
    assert holding.weight == 0.3
    assert holding.reason == "반도체 업황 반등"


def test_a_holding_with_no_reason_upstream_carries_none(conn, portfolio_id):
    conn.execute(
        sa.text(
            "INSERT INTO corporations (stock_code, corp_code, name)"
            " VALUES ('005930', '00126380', '삼성전자')"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight)"
            " VALUES (:pid, '00126380', 0.3)"
        ),
        {"pid": portfolio_id},
    )

    assert find_latest_portfolio(conn).holdings[0].reason is None


def test_an_exit_carries_its_reason(conn, portfolio_id):
    conn.execute(
        sa.text(
            "INSERT INTO corporations (stock_code, corp_code, name)"
            " VALUES ('000660', '00164779', 'SK하이닉스')"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO portfolio_exits (portfolio_id, company_id, reason)"
            " VALUES (:pid, '00164779', '비중 축소')"
        ),
        {"pid": portfolio_id},
    )

    left = find_latest_portfolio(conn).exits[0]

    assert (left.stock_code, left.reason) == ("000660", "비중 축소")
