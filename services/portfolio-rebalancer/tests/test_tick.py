"""The tick calls the other modules in order. The modules themselves are tested
elsewhere, so these fakes stand in for the datastores and the Backend."""

from datetime import UTC, date, datetime

from portfolio_rebalancer import tick as tick_module
from portfolio_rebalancer.decide.reservations import PRICE_BANDS, reservation_prices
from portfolio_rebalancer.portfolio import Exit, Holding, Portfolio
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


def recorded_row(stock_code, sent_at=None, created=MONDAY):
    """A rebalance_orders row as stored_orders returns it. created_at is what the cycle
    start -- and so the remaining days -- is derived from."""
    return {
        "stock_code": stock_code,
        "sent_at": sent_at,
        "created_at": datetime(created.year, created.month, created.day, 1, tzinfo=UTC),
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


def test_an_account_whose_plan_is_already_met_emits_nothing(monkeypatch):
    """A cycle keeps working until the plan is satisfied, and a satisfied plan asks for
    nothing: the account holds its target and has no cash left to spend."""
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    held = [{"stock_id": "005930", "total_price": 7_800_000, "amount": 100}]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=0.0, stocks=held)),
        recorded={11: already},
    )

    assert fakes.run(today=MONDAY) == 0
    assert fakes.recorded_rows == []
    assert fakes.sent == []


def test_an_outstanding_pair_is_amended_rather_than_recorded_again(monkeypatch):
    """A narrowing is one amended order; the unique constraint forbids a second."""
    low, high = reservation_prices(78_000.0, 0)
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(pending_orders=[pending(price=low), pending(price=high)])),
        recorded={11: already},
    )

    assert fakes.run() == 1
    assert fakes.recorded_rows == []
    assert [order.band for order in fakes.amended[0]] == [PRICE_BANDS[1]]
    assert fakes.calls.index("amend") < fakes.calls.index("send")


def test_a_pair_already_matching_the_plan_is_left_alone(monkeypatch):
    """The poll runs hourly. A pair at the planned quantity and today's band is re-quoted
    by nothing, or the Backend would see the same order every hour."""
    low, high = reservation_prices(78_000.0, 0)
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    outstanding = [pending(price=low, amount=52), pending(price=high, amount=52)]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=0.0, pending_orders=outstanding)),
        recorded={11: already},
    )

    assert fakes.run(today=MONDAY) == 0
    assert fakes.amended == []


def test_an_unmanaged_account_is_skipped_entirely(monkeypatch):
    fakes = Fakes(monkeypatch, users=polled(account() | {"is_ai_managed": False}))

    assert fakes.run() == 0
    assert "stored" not in fakes.calls


def test_each_account_is_decided_on_its_own(monkeypatch):
    """Two accounts with different cash get different quantities: nothing is shared."""
    fakes = Fakes(
        monkeypatch,
        users=polled(
            account(account_id=11, cash=1_000_000.0),
            account(account_id=12, cash=10_000_000.0),
        ),
    )

    fakes.run()

    by_account = {batch[0].account_id: batch[0].shares for batch in fakes.sent}
    assert by_account[11] < by_account[12]
    assert sorted(account_id for _, account_id in fakes.marked) == [11, 12]


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


def test_a_recorded_order_that_is_no_longer_on_the_market_is_put_back(monkeypatch):
    """The plan asks only for what is still missing, so if it still asks and nothing is
    outstanding, the order is not where it should be and goes out again -- amended, since
    the unique constraint allows one row per stock."""
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=10_000_000.0, pending_orders=[])),
        recorded={11: already},
    )

    assert fakes.run(today=MONDAY) > 0
    assert fakes.amended
    assert fakes.recorded_rows == []


def test_the_record_is_committed_before_the_send(monkeypatch):
    """Holding one transaction across the send would undo the point of recording first:
    a failed send rolls the record back, and if the request had already reached the
    Backend there is a live order nothing knows about."""
    fakes = Fakes(monkeypatch)

    fakes.run()

    assert fakes.calls.index("record") < fakes.calls.index("send") < fakes.calls.index("mark")


def test_an_unsent_record_whose_pair_is_outstanding_is_stamped_sent(monkeypatch):
    """All placeable orders go out in one request, so an outstanding pair means the
    request arrived and only the reply was lost."""
    low, high = reservation_prices(78_000.0, 0)
    unsent = [recorded_row("005930", sent_at=None)]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(pending_orders=[pending(price=low), pending(price=high)])),
        recorded={11: unsent},
    )

    fakes.run(today=TUESDAY)

    assert (42, 11) in fakes.marked
    assert fakes.discarded == []


def test_an_unsent_record_with_no_pair_is_discarded_and_decided_again(monkeypatch):
    """No pair at all means the Backend never took it, so replaying yesterday's decision
    would be worse than deciding again at today's prices."""
    unsent = [recorded_row("005930", sent_at=None)]
    fakes = Fakes(monkeypatch, recorded={11: unsent})

    sent = fakes.run(today=TUESDAY)

    assert fakes.discarded == [(42, 11)]
    assert sent == 1
    assert fakes.recorded_rows


def test_a_stamped_record_is_not_discarded(monkeypatch):
    """Nothing that reached the Backend is ever dropped from the history."""
    low, high = reservation_prices(78_000.0, 0)
    mixed = [
        recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC)),
        recorded_row("000660", sent_at=None),
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
    unsent = [recorded_row("005930", sent_at=None)]
    fakes = Fakes(monkeypatch, prices={"005930": 90_000.0}, recorded={11: unsent})

    fakes.run(today=TUESDAY)

    order = fakes.sent[0][0]
    assert order.reference == 90_000.0
    assert order.band == PRICE_BANDS[0]


def test_a_discarded_record_is_re_sized_at_the_new_price(monkeypatch):
    """The share count is decided again too, not carried over from the discarded record."""
    unsent = [recorded_row("005930", sent_at=None)]
    cheap = Fakes(monkeypatch, prices={"005930": 10_000.0}, recorded={11: [dict(unsent[0])]})
    cheap.run(today=TUESDAY)

    dear = Fakes(monkeypatch, prices={"005930": 500_000.0}, recorded={11: [dict(unsent[0])]})
    dear.run(today=TUESDAY)

    assert cheap.sent[0][0].shares > dear.sent[0][0].shares


def exits_only(cash=0.0, held=None, pending_orders=()):
    """A cycle whose buy can only be funded by selling."""
    return polled(
        account(cash=cash, stocks=list((held or {}).items()), pending_orders=pending_orders)
    )


def test_a_cycle_with_no_cash_places_only_the_sell(monkeypatch):
    plan = Portfolio(
        portfolio_id=42,
        cash_weight=0.0,
        holdings=[Holding(*SAMSUNG, weight=1.0, reason="사유")],
        exits=[Exit(*HYNIX, reason="퇴출")],
    )
    users = polled(
        account(cash=0.0, stocks=[{"stock_id": "000660", "total_price": 1, "amount": 10}])
    )
    fakes = Fakes(monkeypatch, model=plan, users=users)

    fakes.run(today=MONDAY)

    assert [order.action for order in fakes.sent[0]] == ["sell"]


def test_the_buy_arrives_once_the_sell_has_freed_the_cash(monkeypatch):
    """Nothing is bought on credit: the buy appears on the pass after the cash does."""
    plan = Portfolio(
        portfolio_id=42,
        cash_weight=0.0,
        holdings=[Holding(*SAMSUNG, weight=1.0, reason="사유")],
        exits=[Exit(*HYNIX, reason="퇴출")],
    )
    already = [recorded_row("000660", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    users = polled(account(cash=4_120_000.0))
    fakes = Fakes(monkeypatch, model=plan, users=users, recorded={11: already})

    fakes.run(today=MONDAY)

    buys = [order for order in fakes.sent[0] if order.action == "buy"]
    assert buys
    assert buys[0].shares * buys[0].high <= 4_120_000.0


def test_an_order_placed_after_a_day_of_selling_starts_narrow(monkeypatch):
    """Selling and buying share three trading days. A day spent selling leaves two, and
    two days is the 3% band -- the ladder does not restart."""
    plan = Portfolio(
        portfolio_id=42,
        cash_weight=0.0,
        holdings=[Holding(*SAMSUNG, weight=1.0, reason="사유")],
        exits=[Exit(*HYNIX, reason="퇴출")],
    )
    already = [recorded_row("000660", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    users = polled(account(cash=4_120_000.0))
    fakes = Fakes(monkeypatch, model=plan, users=users, recorded={11: already})

    fakes.run(today=TUESDAY)

    buys = [order for order in fakes.sent[0] if order.action == "buy"]
    assert buys[0].band == PRICE_BANDS[1]


def test_a_buy_placed_on_the_last_day_goes_straight_to_market(monkeypatch):
    """If the sell only filled at the deadline, the buy has no days left to chase a
    price, and the market rung is what guarantees it fills at all."""
    from datetime import date

    plan = Portfolio(
        portfolio_id=42,
        cash_weight=0.0,
        holdings=[Holding(*SAMSUNG, weight=1.0, reason="사유")],
        exits=[Exit(*HYNIX, reason="퇴출")],
    )
    already = [recorded_row("000660", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    users = polled(account(cash=4_120_000.0))
    fakes = Fakes(monkeypatch, model=plan, users=users, recorded={11: already})

    fakes.run(today=date(2026, 10, 1))

    buys = [order for order in fakes.sent[0] if order.action == "buy"]
    assert (buys[0].low, buys[0].high, buys[0].band) == (None, None, None)
