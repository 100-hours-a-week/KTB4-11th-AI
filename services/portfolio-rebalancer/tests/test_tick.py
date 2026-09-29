"""The tick calls the other modules in order. The modules themselves are tested
elsewhere, so these fakes stand in for the datastores and the Backend."""

from datetime import UTC, date, datetime

import pytest
from portfolio_rebalancer import tick as tick_module
from portfolio_rebalancer.decide.reservations import PRICE_BANDS, reservation_prices
from portfolio_rebalancer.portfolio import Holding, Portfolio
from portfolio_rebalancer.request.prices import Price
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
        self.discarded: list[tuple] = []
        self.today = TUESDAY

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
            lambda conn, pid, aid: (
                note("mark") or self.marked.append((pid, aid)) or self._stamp(aid)
            ),
        )
        monkeypatch.setattr(
            tick_module,
            "discard_unsent",
            lambda conn, pid, aid: (
                note("discard")
                or self.discarded.append((pid, aid))
                or self._recorded.pop(aid, None)
            ),
        )

    def _stamp(self, account_id):
        """The real mark_sent stamps now(), so a stamped order counts as sent today."""
        for row in self._recorded.get(account_id, []):
            if row["sent_at"] is None:
                row["sent_at"] = datetime(
                    self.today.year, self.today.month, self.today.day, tzinfo=UTC
                )

    def run(self, today=TUESDAY):
        self.today = today
        return tick(_Engine(), "db", "client", "a-token", today=today)


class _Conn:
    def __enter__(self):
        return "conn"

    def __exit__(self, *args):
        return None


class _Engine:
    """Every begin() is its own transaction, which is the point of the split."""

    def begin(self):
        return _Conn()


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


def test_the_record_is_committed_before_the_send():
    """Holding one transaction across the send would undo the point of recording first:
    a failed send rolls the record back, and if the request had already reached the
    Backend there is a live order nothing knows about."""
    import inspect

    source = inspect.getsource(tick_module._open)
    record_at = source.index("record_orders")
    send_at = source.index("send_orders")
    between = source[record_at:send_at]

    assert "engine.begin()" in source[:record_at]
    # The transaction that wrote the record must close before the send.
    assert between.count("with engine.begin()") == 0


def test_an_unsent_record_whose_pair_is_outstanding_is_stamped_sent(monkeypatch):
    """All placeable orders go out in one request, so an outstanding pair means the
    request arrived and only the reply was lost."""
    low, high = reservation_prices(78_000.0, 0)
    unsent = [{"stock_code": "005930", "sent_at": None}]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(pending_orders=[pending(price=low), pending(price=high)])),
        recorded={11: unsent},
    )

    fakes.run(today=TUESDAY)

    assert fakes.marked == [(42, 11)]
    assert fakes.discarded == []
    assert fakes.recorded_rows == []


def test_an_unsent_record_with_no_pair_is_discarded_and_decided_again(monkeypatch):
    """No pair at all means the Backend never took it, so replaying yesterday's decision
    would be worse than deciding again at today's prices."""
    unsent = [{"stock_code": "005930", "sent_at": None}]
    fakes = Fakes(monkeypatch, recorded={11: unsent})

    sent = fakes.run(today=TUESDAY)

    assert fakes.discarded == [(42, 11)]
    assert sent == 1
    assert fakes.recorded_rows


def test_a_stamped_record_is_not_discarded(monkeypatch):
    """Nothing that reached the Backend is ever dropped from the history."""
    low, high = reservation_prices(78_000.0, 0)
    mixed = [
        {"stock_code": "005930", "sent_at": datetime(2026, 9, 28, tzinfo=UTC)},
        {"stock_code": "000660", "sent_at": None},
    ]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(pending_orders=[pending(price=low), pending(price=high)])),
        recorded={11: mixed},
    )

    fakes.run(today=TUESDAY)

    assert fakes.discarded == []


def test_the_poll_mirror_commits_separately_from_the_orders(monkeypatch):
    """A send that fails must not roll back the mirror: the next pass needs it to work out
    what happened."""
    import inspect

    source = inspect.getsource(tick_module.tick)

    assert source.count("with engine.begin()") == 2


def test_a_discarded_record_gets_a_new_reference_from_current_prices(monkeypatch):
    """A placed order keeps the reference its first pair fixed. A record the Backend never
    took has no first pair, so there is nothing to keep: the whole decision is made again
    at today's price, back at the widest band."""
    unsent = [{"stock_code": "005930", "sent_at": None}]
    fakes = Fakes(monkeypatch, prices={"005930": 90_000.0}, recorded={11: unsent})

    fakes.run(today=TUESDAY)

    order = fakes.sent[0][0]
    assert order.reference == 90_000.0
    assert order.band == PRICE_BANDS[0]


def test_a_discarded_record_is_re_sized_at_the_new_price(monkeypatch):
    """The share count is decided again too, not carried over from the discarded record."""
    unsent = [{"stock_code": "005930", "sent_at": None}]
    cheap = Fakes(monkeypatch, prices={"005930": 10_000.0}, recorded={11: [dict(unsent[0])]})
    cheap.run(today=TUESDAY)

    dear = Fakes(monkeypatch, prices={"005930": 500_000.0}, recorded={11: [dict(unsent[0])]})
    dear.run(today=TUESDAY)

    assert cheap.sent[0][0].shares > dear.sent[0][0].shares
