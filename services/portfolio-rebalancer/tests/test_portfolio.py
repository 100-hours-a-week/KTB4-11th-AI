import json
import uuid

import pytest
import sqlalchemy as sa
from portfolio_rebalancer.portfolio import Unready, load_portfolio

SAMSUNG, HYNIX, LGES = "00126380", "00164779", "01515323"


def _explanation(text):
    return {"reason": text, "reasonings": [{"label": "근거", "body": text}]}


def _portfolio(conn, holdings, exits, reasons, status="ready"):
    portfolio_id = conn.execute(
        sa.text(
            "INSERT INTO portfolios (cash_weight, commentary, model, status)"
            " VALUES (0.1, 'c', 'm', :s) RETURNING id"
        ),
        {"s": status},
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


def test_no_portfolio_loads_nothing(engine):
    assert load_portfolio(engine) is None


@pytest.mark.parametrize("status", ["explanation_pending", "explanation_failed"])
def test_an_unready_latest_portfolio_hides_the_older_ready_one(engine, status):
    with engine.begin() as conn:
        _portfolio(conn, [(HYNIX, 0.5)], [], [(HYNIX, "buy", "사요"), (HYNIX, "sell", "줄여요")])
        latest = _portfolio(conn, [(SAMSUNG, 0.9)], [HYNIX], [], status)

    assert load_portfolio(engine) == Unready(id=latest, status=status)


def test_the_latest_ready_portfolio_is_loaded_with_every_earlier_exit_reason(engine):
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

    portfolio = load_portfolio(engine)

    assert portfolio.id == explained
    assert first < explained
    assert portfolio.cash_weight == 0.1
    by_code = {t.stock_code: t for t in portfolio.targets}
    assert by_code["005930"].weight == 0.6
    assert by_code["005930"].exiting is False
    assert by_code["005930"].buy.reason == "삼성 사요"
    assert isinstance(by_code["005930"].buy.id, uuid.UUID)
    assert by_code["005930"].sell.reason == "삼성 줄여요"
    assert by_code["000660"].exiting is True
    assert by_code["000660"].buy is None
    assert by_code["000660"].sell.reasonings[0].body == "하이닉스 팔아요"
    assert isinstance(by_code["000660"].sell.id, uuid.UUID)
    assert isinstance(portfolio.leftovers["373220"].id, uuid.UUID)
    with engine.connect() as conn:
        stored_ids = dict(conn.execute(sa.text("SELECT reason, id FROM portfolio_reasons")).all())
    assert portfolio.leftovers["373220"].id == stored_ids["LG엔솔 팔아요"]
    assert portfolio.leftovers["000660"].id == stored_ids["하이닉스 팔아요"]
    assert by_code["005930"].buy.id == stored_ids["삼성 사요"]
    assert by_code["005930"].sell.id == stored_ids["삼성 줄여요"]
    assert by_code["000660"].sell.id == stored_ids["하이닉스 팔아요"]
    assert portfolio.names == {
        "005930": "삼성전자",
        "000660": "SK하이닉스",
        "373220": "LG에너지솔루션",
    }


@pytest.fixture
def competition(engine):
    with engine.connect() as conn:
        if conn.scalar(sa.text("SELECT to_regclass('competition_portfolios')")) is None:
            pytest.skip("requires competition schema from #484")
    with engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE competition_participants CASCADE"))
    return engine


def _participant(conn, participant_id, user_id):
    conn.execute(
        sa.text(
            "INSERT INTO competition_participants"
            " (participant_id, competition_id, user_id, risk_profile)"
            " VALUES (:p, 1, :u, '안정형')"
        ),
        {"p": participant_id, "u": user_id},
    )


def _link(conn, participant_id, portfolio_id):
    conn.execute(
        sa.text("INSERT INTO competition_portfolios VALUES (:p, :f, now())"),
        {"p": participant_id, "f": portfolio_id},
    )


def test_common_latest_and_leftovers_exclude_competition_portfolios(competition):
    with competition.begin() as conn:
        _participant(conn, 10, 1)
        personal = _portfolio(conn, [], [HYNIX], [(HYNIX, "sell", "private")])
        _link(conn, 10, personal)
        common = _portfolio(conn, [], [], [])
        latest = _portfolio(conn, [], [], [], "explanation_failed")
        _link(conn, 10, latest)
    loaded = load_portfolio(competition)
    assert loaded.id == common
    assert loaded.leftovers == {}


def test_participant_requires_owner_and_latest_link_without_common_fallback(competition):
    with competition.begin() as conn:
        _participant(conn, 10, 1)
        ready = _portfolio(conn, [], [], [])
        _link(conn, 10, ready)
        unready = _portfolio(conn, [], [], [], "explanation_pending")
        _link(conn, 10, unready)
        _portfolio(conn, [], [], [])
    assert load_portfolio(competition, 1, 10) == Unready(id=unready, status="explanation_pending")
    assert load_portfolio(competition, 2, 10) is None
    assert load_portfolio(competition, 1, 999) is None


def test_participant_leftovers_use_own_link_history_including_shared_portfolios(competition):
    with competition.begin() as conn:
        _participant(conn, 10, 1)
        _participant(conn, 11, 1)
        shared = _portfolio(conn, [], [LGES], [(LGES, "sell", "shared")])
        _link(conn, 10, shared)
        _link(conn, 11, shared)
        foreign = _portfolio(conn, [], [HYNIX], [(HYNIX, "sell", "other participant")])
        _link(conn, 11, foreign)
        own = _portfolio(conn, [], [], [])
        _link(conn, 10, own)
    loaded = load_portfolio(competition, 1, 10)
    assert loaded.id == own
    assert set(loaded.leftovers) == {"373220"}
    assert loaded.leftovers["373220"].reason == "shared"
