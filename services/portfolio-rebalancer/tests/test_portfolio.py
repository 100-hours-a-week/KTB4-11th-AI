import json

import sqlalchemy as sa
from portfolio_rebalancer.portfolio import Explanation, load_portfolio

SAMSUNG, HYNIX, LGES = "00126380", "00164779", "01515323"


def _explanation(text):
    return {"reason": text, "reasonings": [{"label": "근거", "body": text}]}


def _portfolio(conn, holdings, exits, reasons):
    portfolio_id = conn.execute(
        sa.text(
            "INSERT INTO portfolios (cash_weight, commentary, model) VALUES (0.1, 'c', 'm')"
            " RETURNING id"
        )
    ).scalar_one()
    for company, weight in holdings:
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight)"
                " VALUES (:p, :c, :w)"
            ),
            {"p": portfolio_id, "c": company, "w": weight},
        )
    for company in exits:
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_exits (portfolio_id, company_id, reason)"
                " VALUES (:p, :c, 'r')"
            ),
            {"p": portfolio_id, "c": company},
        )
    for company, side, text in reasons:
        explanation = _explanation(text)
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_reasons"
                " (portfolio_id, company_id, side, reason, reasonings)"
                " VALUES (:p, :c, :s, :r, CAST(:j AS jsonb))"
            ),
            {
                "p": portfolio_id,
                "c": company,
                "s": side,
                "r": explanation["reason"],
                "j": json.dumps(explanation["reasonings"]),
            },
        )
    return portfolio_id


def test_nothing_explained_yet_loads_nothing(engine):
    with engine.begin() as conn:
        _portfolio(conn, [(SAMSUNG, 0.9)], [], [])

    assert load_portfolio(engine) is None


def test_the_latest_explained_portfolio_is_loaded_and_an_unexplained_newer_one_skipped(engine):
    with engine.begin() as conn:
        first = _portfolio(
            conn,
            [(HYNIX, 0.5)],
            [LGES],
            [
                (HYNIX, "buy", "하이닉스 사요"),
                (HYNIX, "sell", "하이닉스 줄여요"),
                (LGES, "sell", "LG엔솔 팔아요"),
            ],
        )
        explained = _portfolio(
            conn,
            [(SAMSUNG, 0.6)],
            [HYNIX],
            [
                (SAMSUNG, "buy", "삼성 사요"),
                (SAMSUNG, "sell", "삼성 줄여요"),
                (HYNIX, "sell", "하이닉스 팔아요"),
            ],
        )
        _portfolio(conn, [(LGES, 0.9)], [SAMSUNG], [])

    portfolio = load_portfolio(engine)

    assert portfolio.id == explained
    assert first < explained
    by_code = {t.stock_code: t for t in portfolio.targets}
    assert by_code["005930"].weight == 0.6
    assert by_code["005930"].exiting is False
    assert by_code["005930"].buy.reason == "삼성 사요"
    assert by_code["005930"].sell.reason == "삼성 줄여요"
    assert by_code["000660"].exiting is True
    assert by_code["000660"].buy is None
    assert by_code["000660"].sell.reasonings[0].body == "하이닉스 팔아요"
    assert portfolio.leftovers == {
        "373220": Explanation.model_validate(_explanation("LG엔솔 팔아요")),
        "000660": Explanation.model_validate(_explanation("하이닉스 팔아요")),
    }
