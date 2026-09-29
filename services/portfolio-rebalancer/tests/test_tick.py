"""The tick calls the other modules in order. The modules themselves are tested
elsewhere, so these fakes stand in for the datastores and the Backend."""

from datetime import UTC, date, datetime

import pytest
from portfolio_rebalancer import tick as tick_module
from portfolio_rebalancer.prices import Price
from portfolio_rebalancer.rebalance import Holding, Portfolio
from portfolio_rebalancer.reservations import PRICE_BANDS, reservation_prices
from portfolio_rebalancer.tick import market_today, tick

MONDAY = date(2026, 9, 28)
TUESDAY = date(2026, 9, 29)
SAMSUNG = ("00126380", "005930")
HYNIX = ("00164779", "000660")


def portfolio(holdings=None, exits=()):
    if holdings is None:
        holdings = [Holding(*SAMSUNG, weight=1.0, reason="사유")]
    return Portfolio(portfolio_id=42, cash_weight=0.0, holdings=holdings, exits=list(exits))


def pending(stock_code="005930", order_type="buy", price=74_100.0, amount=100):
    return {
        "order_type": order_type,
        "status": "pending",
        "stock_code": stock_code,
        "price": price,
        "amount": amount,
    }


def account(account_id=11, cash=10_000_000.0, stocks=(), pending_orders=()):
    return {
        "account_id": account_id,
        "account_name": "계좌",
        "is_ai_managed": True,
        "is_duel_account": False,
        "is_active": True,
        "cash_balance": cash,
        "stocks": list(stocks),
        "pending_orders": list(pending_orders),
    }


def polled(*accounts):
    return [
        {
            "user_id": 1,
            "nickname": "스톡스푼",
            "state": "active",
            "accounts": list(accounts) or [account()],
        }
    ]


class Fakes:
    """Records every call the tick makes, in order."""

    def __init__(self, monkeypatch, *, model=None, users=None, prices=None, recorded=None):
        self.calls: list[str] = []
        self.sent: list[list] = []
        self.recorded_rows: list[list] = []
        self.amended: list[list] = []
        self.marked: list[tuple] = []
        self.saved: list[list] = []
        self._recorded = recorded if recorded is not None else {}

        model = portfolio() if model is None else model
        users = polled() if users is None else users
        prices = {"005930": 78_000.0, "000660": 412_000.0} if prices is None else prices

        def note(name, value=None):
            self.calls.append(name)
            return value

        monkeypatch.setattr(tick_module, "latest_portfolio", lambda conn: note("portfolio", model))
        monkeypatch.setattr(tick_module, "fetch_accounts", lambda c, t: note("fetch", users))
        monkeypatch.setattr(
            tick_module, "save_poll", lambda conn, u: note("save") or self.saved.append(u)
        )
        monkeypatch.setattr(
            tick_module,
            "latest_prices",
            lambda db, codes: (
                note("prices")
                or {
                    code: Price(close=prices[code], ts=datetime(2026, 9, 29, tzinfo=UTC))
                    for code in codes
                    if code in prices
                }
            ),
        )
        monkeypatch.setattr(
            tick_module,
            "stored_orders",
            lambda conn, pid, aid: note("stored") or self._recorded.get(aid, []),
        )
        monkeypatch.setattr(
            tick_module,
            "record_orders",
            lambda conn, pid, orders: note("record") or self.recorded_rows.append(list(orders)),
        )
        monkeypatch.setattr(
            tick_module,
            "amend_orders",
            lambda conn, pid, orders: note("amend") or self.amended.append(list(orders)),
        )
        monkeypatch.setattr(
            tick_module,
            "send_orders",
            lambda c, t, orders: note("send") or self.sent.append(list(orders)),
        )
        monkeypatch.setattr(
            tick_module,
            "mark_sent",
            lambda conn, pid, aid: note("mark") or self.marked.append((pid, aid)),
        )

    def run(self, today=TUESDAY):
        return tick("conn", "db", "client", "a-token", today=today)


def test_no_model_portfolio_polls_nothing_and_sends_nothing(monkeypatch):
    """Polling the Backend to then decide nothing would be a wasted round trip."""
    fakes = Fakes(monkeypatch, model=None)
    monkeypatch.setattr(tick_module, "latest_portfolio", lambda conn: None)

    assert fakes.run() == 0
    assert "fetch" not in fakes.calls


def test_the_poll_is_mirrored_before_anything_is_decided(monkeypatch):
    fakes = Fakes(monkeypatch)

    fakes.run()

    assert fakes.calls.index("save") < fakes.calls.index("stored")
    assert fakes.saved == [polled()]


def test_a_fresh_account_is_recorded_before_it_is_sent(monkeypatch):
    """A crash between the two has to leave a record, not a silent order."""
    fakes = Fakes(monkeypatch)

    fakes.run()

    assert fakes.calls.index("record") < fakes.calls.index("send") < fakes.calls.index("mark")


def test_a_fresh_account_gets_the_widest_band(monkeypatch):
    fakes = Fakes(monkeypatch)

    fakes.run()

    assert [order.band for order in fakes.sent[0]] == [PRICE_BANDS[0]]


def test_a_skip_is_recorded_but_not_placed(monkeypatch):
    """ "We could not buy this" is part of the decision, but there is nothing to place."""
    dear = [
        Holding(*SAMSUNG, weight=0.5, reason="사유"),
        Holding(*HYNIX, weight=0.5, reason="사유"),
    ]
    fakes = Fakes(
        monkeypatch,
        model=portfolio(holdings=dear),
        prices={"005930": 78_000.0, "000660": 90_000_000.0},
    )

    fakes.run()

    assert "skip" in {order.action for order in fakes.recorded_rows[0]}
    assert "skip" not in {order.action for order in fakes.sent[0]}


def test_an_account_already_rebalanced_is_not_rebalanced_again(monkeypatch):
    """The recorded orders are what make a repeated tick a no-op."""
    already = [{"stock_code": "005930", "sent_at": datetime(2026, 9, 29, tzinfo=UTC)}]
    fakes = Fakes(monkeypatch, recorded={11: already})

    assert fakes.run() == 0
    assert fakes.recorded_rows == []
    assert fakes.sent == []


def test_an_outstanding_pair_is_amended_rather_than_recorded_again(monkeypatch):
    """A narrowing is one amended order; the unique constraint forbids a second."""
    low, high = reservation_prices(78_000.0, 0)
    already = [{"stock_code": "005930", "sent_at": datetime(2026, 9, 28, tzinfo=UTC)}]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(pending_orders=[pending(price=low), pending(price=high)])),
        recorded={11: already},
    )

    assert fakes.run() == 1
    assert fakes.recorded_rows == []
    assert [order.band for order in fakes.amended[0]] == [PRICE_BANDS[1]]
    assert fakes.calls.index("amend") < fakes.calls.index("send")


def test_an_outstanding_pair_sent_today_is_left_alone(monkeypatch):
    low, high = reservation_prices(78_000.0, 0)
    already = [{"stock_code": "005930", "sent_at": datetime(2026, 9, 29, tzinfo=UTC)}]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(pending_orders=[pending(price=low), pending(price=high)])),
        recorded={11: already},
    )

    assert fakes.run() == 0
    assert fakes.amended == []


def test_an_unmanaged_account_is_skipped_entirely(monkeypatch):
    fakes = Fakes(monkeypatch, users=polled(account() | {"is_ai_managed": False}))

    assert fakes.run() == 0
    assert "stored" not in fakes.calls


def test_each_account_is_decided_on_its_own(monkeypatch):
    """One account already out, one fresh: the fresh one still gets its orders."""
    already = [{"stock_code": "005930", "sent_at": datetime(2026, 9, 29, tzinfo=UTC)}]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(account_id=11), account(account_id=12)),
        recorded={11: already},
    )

    fakes.run()

    assert fakes.marked == [(42, 12)]


def test_the_held_stocks_are_priced_too_not_just_the_portfolios(monkeypatch):
    """A holding has to be valued to know whether it is above or below its target."""
    asked = []
    fakes = Fakes(
        monkeypatch,
        users=polled(account(stocks=[{"stock_id": "000660", "total_price": 1, "amount": 2}])),
    )
    original = tick_module.latest_prices
    monkeypatch.setattr(
        tick_module,
        "latest_prices",
        lambda db, codes: asked.append(list(codes)) or original(db, codes),
    )

    fakes.run()

    assert "000660" in asked[0]


def test_market_today_is_the_exchanges_date():
    """The service may run anywhere; the market is in Seoul."""
    assert isinstance(market_today(), date)


@pytest.mark.parametrize("empty", [[], None])
def test_an_account_with_no_pending_orders_and_a_record_does_nothing(monkeypatch, empty):
    already = [{"stock_code": "005930", "sent_at": datetime(2026, 9, 28, tzinfo=UTC)}]
    fakes = Fakes(
        monkeypatch,
        users=polled(account() | {"pending_orders": empty}),
        recorded={11: already},
    )

    assert fakes.run() == 0
