import pytest
import sqlalchemy as sa
from portfolio_rebalancer.portfolio import load_portfolio

from .test_portfolio import HYNIX, LGES, SAMSUNG, _portfolio


@pytest.fixture(autouse=True)
def participant_rows(engine):
    with engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE competition_participants CASCADE"))
    yield
    with engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE competition_participants CASCADE"))


def _link_competition_portfolio(conn, portfolio_id):
    participant_id = conn.execute(
        sa.text(
            "INSERT INTO competition_participants (competition_id, user_id, risk_profile)"
            " VALUES (1, 1, '위험중립형') RETURNING participant_id"
        )
    ).scalar_one()
    conn.execute(
        sa.text(
            "INSERT INTO competition_portfolios (participant_id, portfolio_id) VALUES (:p, :f)"
        ),
        {"p": participant_id, "f": portfolio_id},
    )


def test_load_portfolio_ignores_a_newer_competition_portfolio(engine):
    with engine.begin() as conn:
        common = _portfolio(
            conn,
            [(SAMSUNG, 0.5)],
            [],
            [(SAMSUNG, "buy", "삼성 사요"), (SAMSUNG, "sell", "삼성 줄여요")],
        )
        competition = _portfolio(
            conn,
            [(HYNIX, 0.6)],
            [],
            [(HYNIX, "buy", "하이닉스 사요"), (HYNIX, "sell", "하이닉스 줄여요")],
        )
        _link_competition_portfolio(conn, competition)

    assert common < competition
    portfolio = load_portfolio(engine)
    assert portfolio.id == common


def test_load_portfolio_returns_none_when_only_a_competition_portfolio_exists(engine):
    with engine.begin() as conn:
        competition = _portfolio(
            conn,
            [(SAMSUNG, 0.5)],
            [],
            [(SAMSUNG, "buy", "삼성 사요"), (SAMSUNG, "sell", "삼성 줄여요")],
        )
        _link_competition_portfolio(conn, competition)

    assert load_portfolio(engine) is None


def test_a_competition_portfolios_sell_reason_is_not_a_leftover(engine):
    with engine.begin() as conn:
        competition = _portfolio(conn, [], [LGES], [(LGES, "sell", "대결 포트폴리오 매도 사유")])
        _link_competition_portfolio(conn, competition)
        common = _portfolio(
            conn,
            [(SAMSUNG, 0.5)],
            [],
            [(SAMSUNG, "buy", "삼성 사요"), (SAMSUNG, "sell", "삼성 줄여요")],
        )

    assert competition < common
    portfolio = load_portfolio(engine)
    assert "373220" not in portfolio.leftovers
