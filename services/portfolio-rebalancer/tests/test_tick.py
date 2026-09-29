"""The tick calls the other modules in order. The modules themselves are tested
elsewhere, so these fakes stand in for the datastores and the Backend."""

from datetime import UTC, date, datetime, timedelta, timezone

from portfolio_rebalancer import tick as tick_module
from portfolio_rebalancer.decide.reservations import PRICE_BANDS, limit_and_trigger
from portfolio_rebalancer.portfolio import Exit, Holding, Portfolio
from portfolio_rebalancer.request.prices import Price
from portfolio_rebalancer.tick import market_now, tick

# 2026-09-28 週 has five sessions, so a Monday cycle starts with five days.
MONDAY = date(2026, 9, 28)
TUESDAY = date(2026, 9, 29)
THURSDAY = date(2026, 10, 1)
FRIDAY = date(2026, 10, 2)
SAMSUNG = ("00126380", "005930")
HYNIX = ("00164779", "000660")


KST = timezone(timedelta(hours=9))


def noon(day):
    """A KST datetime in the middle of the session, well before the 14:30 cutoff."""
    return datetime(day.year, day.month, day.day, 11, 0, tzinfo=KST)


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


def recorded_row(
    stock_code, sent_at=None, created=MONDAY, reference=78_000.0, trigger=None, side="buy"
):
    """A rebalance_orders row as stored_orders returns it.

    created_at is what the cycle start -- and so the remaining days -- is derived from,
    and reference_price is what a single limit price cannot say for itself.
    """
    if trigger is None and reference:
        trigger = limit_and_trigger(reference, 3, side)[1]
    return {
        "stock_code": stock_code,
        "sent_at": sent_at,
        "created_at": datetime(created.year, created.month, created.day, 1, tzinfo=UTC),
        "reference_price": reference,
        "trigger_price": trigger,
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

    def run(self, now=None):
        now = now or noon(TUESDAY)
        self.today = now.date()
        return tick(_Engine(), "db", "client", "a-token", now=now)


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

    assert fakes.run(now=noon(MONDAY)) == 0
    assert fakes.recorded_rows == []
    assert fakes.sent == []


def test_an_outstanding_pair_is_amended_rather_than_recorded_again(monkeypatch):
    """A narrowing is one amended order; the unique constraint forbids a second."""
    limit, _ = limit_and_trigger(78_000.0, 5, "buy")
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(pending_orders=[pending(price=limit)])),
        recorded={11: already},
    )

    assert fakes.run(now=noon(THURSDAY)) == 1
    assert fakes.recorded_rows == []
    assert [order.band for order in fakes.amended[0]] == [PRICE_BANDS[1]]
    assert fakes.calls.index("amend") < fakes.calls.index("send")


def test_a_pair_already_matching_the_plan_is_left_alone(monkeypatch):
    """The poll runs hourly. A pair at the planned quantity and today's band is re-quoted
    by nothing, or the Backend would see the same order every hour."""
    limit, _ = limit_and_trigger(78_000.0, 5, "buy")
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    outstanding = [pending(price=limit, amount=52)]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=0.0, pending_orders=outstanding)),
        recorded={11: already},
    )

    assert fakes.run(now=noon(MONDAY)) == 0
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


def test_market_now_is_on_the_exchanges_clock():
    """The service may run anywhere; the market is in Seoul, and the 15:30 close is what
    the last day's market order has to beat."""
    now = market_now()

    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(hours=9)


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

    assert fakes.run(now=noon(MONDAY)) > 0
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
    limit, _ = limit_and_trigger(78_000.0, 5, "buy")
    unsent = [recorded_row("005930", sent_at=None)]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(pending_orders=[pending(price=limit)])),
        recorded={11: unsent},
    )

    fakes.run(now=noon(TUESDAY))

    assert (42, 11) in fakes.marked
    assert fakes.discarded == []


def test_an_unsent_record_with_no_pair_is_discarded_and_decided_again(monkeypatch):
    """No pair at all means the Backend never took it, so replaying yesterday's decision
    would be worse than deciding again at today's prices."""
    unsent = [recorded_row("005930", sent_at=None)]
    fakes = Fakes(monkeypatch, recorded={11: unsent})

    sent = fakes.run(now=noon(TUESDAY))

    assert fakes.discarded == [(42, 11)]
    assert sent == 1
    assert fakes.recorded_rows


def test_a_stamped_record_is_not_discarded(monkeypatch):
    """Nothing that reached the Backend is ever dropped from the history."""
    limit, _ = limit_and_trigger(78_000.0, 5, "buy")
    mixed = [
        recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC)),
        recorded_row("000660", sent_at=None),
    ]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(pending_orders=[pending(price=limit)])),
        recorded={11: mixed},
    )

    fakes.run(now=noon(TUESDAY))

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

    fakes.run(now=noon(TUESDAY))

    order = fakes.sent[0][0]
    assert order.reference == 90_000.0
    assert order.band == PRICE_BANDS[0]


def test_a_discarded_record_is_re_sized_at_the_new_price(monkeypatch):
    """The share count is decided again too, not carried over from the discarded record."""
    unsent = [recorded_row("005930", sent_at=None)]
    cheap = Fakes(monkeypatch, prices={"005930": 10_000.0}, recorded={11: [dict(unsent[0])]})
    cheap.run(now=noon(TUESDAY))

    dear = Fakes(monkeypatch, prices={"005930": 500_000.0}, recorded={11: [dict(unsent[0])]})
    dear.run(now=noon(TUESDAY))

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

    fakes.run(now=noon(MONDAY))

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

    fakes.run(now=noon(MONDAY))

    buys = [order for order in fakes.sent[0] if order.action == "buy"]
    assert buys
    assert buys[0].shares * buys[0].trigger <= 4_120_000.0


def test_an_order_placed_late_in_the_week_starts_narrow(monkeypatch):
    """Selling and buying share the week's five sessions. Placed on the Thursday there are
    two left, and two days is the 3% band -- the ladder does not restart."""
    plan = Portfolio(
        portfolio_id=42,
        cash_weight=0.0,
        holdings=[Holding(*SAMSUNG, weight=1.0, reason="사유")],
        exits=[Exit(*HYNIX, reason="퇴출")],
    )
    already = [recorded_row("000660", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    users = polled(account(cash=4_120_000.0))
    fakes = Fakes(monkeypatch, model=plan, users=users, recorded={11: already})

    fakes.run(now=noon(THURSDAY))

    buys = [order for order in fakes.sent[0] if order.action == "buy"]
    assert buys[0].band == PRICE_BANDS[1]


def test_a_buy_placed_after_the_cutoff_goes_straight_to_market(monkeypatch):
    """KRX closes at 15:30. Past the cutoff on the last session there is no day left to
    chase a price with, and the market rung is what guarantees it fills at all."""
    plan = Portfolio(
        portfolio_id=42,
        cash_weight=0.0,
        holdings=[Holding(*SAMSUNG, weight=1.0, reason="사유")],
        exits=[Exit(*HYNIX, reason="퇴출")],
    )
    already = [recorded_row("000660", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    users = polled(account(cash=4_120_000.0))
    fakes = Fakes(monkeypatch, model=plan, users=users, recorded={11: already})

    fakes.run(now=datetime(2026, 10, 2, 15, 0, tzinfo=KST))

    buys = [order for order in fakes.sent[0] if order.action == "buy"]
    assert (buys[0].limit, buys[0].trigger, buys[0].band) == (None, None, None)


def working_buy(reference=78_000.0, days_left=5, amount=52):
    """What the poll reports for a buy reservation: one order, at the limit."""
    limit, _ = limit_and_trigger(reference, days_left, "buy")
    return [pending(price=limit, amount=amount)]


def test_a_buy_goes_to_market_when_the_price_rises_to_its_trigger(monkeypatch):
    """The dip is not coming. Waiting on a limit below the market stops being worth it."""
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    _, trigger = limit_and_trigger(78_000.0, 3, "buy")
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=0.0, pending_orders=working_buy())),
        prices={"005930": trigger},
        recorded={11: already},
    )

    fakes.run(now=noon(MONDAY))

    struck = fakes.amended[0][0]
    assert (struck.limit, struck.trigger, struck.band) == (None, None, None)
    assert struck.action == "buy"


def test_a_buy_below_its_trigger_keeps_waiting(monkeypatch):
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=0.0, pending_orders=working_buy())),
        prices={"005930": 78_000.0},
        recorded={11: already},
    )

    assert fakes.run(now=noon(MONDAY)) == 0
    assert fakes.amended == []


def test_a_sell_goes_to_market_when_the_price_falls_to_its_trigger(monkeypatch):
    """The chance of selling high is gone, so getting out at market beats holding on."""
    plan = Portfolio(
        portfolio_id=42,
        cash_weight=0.0,
        holdings=[Holding(*SAMSUNG, weight=1.0, reason="사유")],
        exits=[Exit(*HYNIX, reason="퇴출")],
    )
    limit, trigger = limit_and_trigger(412_000.0, 5, "sell")
    already = [
        recorded_row(
            "000660",
            sent_at=datetime(2026, 9, 28, tzinfo=UTC),
            reference=412_000.0,
            trigger=limit_and_trigger(412_000.0, 3, "sell")[1],
            side="sell",
        )
    ]
    outstanding = [pending(stock_code="000660", order_type="sell", price=limit, amount=10)]
    fakes = Fakes(
        monkeypatch,
        model=plan,
        users=polled(account(cash=0.0, pending_orders=outstanding)),
        prices={"005930": 78_000.0, "000660": trigger},
        recorded={11: already},
    )

    fakes.run(now=noon(MONDAY))

    struck = next(o for batch in fakes.amended for o in batch if o.stock_code == "000660")
    assert (struck.limit, struck.trigger, struck.band) == (None, None, None)
    assert struck.action == "sell"


def test_a_struck_order_is_amended_not_recorded_again(monkeypatch):
    """One row per stock: the unique constraint forbids a second."""
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    _, trigger = limit_and_trigger(78_000.0, 3, "buy")
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=0.0, pending_orders=working_buy())),
        prices={"005930": trigger},
        recorded={11: already},
    )

    fakes.run(now=noon(MONDAY))

    assert fakes.recorded_rows == []
    assert fakes.amended
    assert fakes.calls.index("amend") < fakes.calls.index("send")


def test_a_struck_order_keeps_the_outstanding_quantity(monkeypatch):
    """A partial fill left less, and that is what the market order asks for."""
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    _, trigger = limit_and_trigger(78_000.0, 3, "buy")
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=0.0, pending_orders=working_buy(amount=21))),
        prices={"005930": trigger},
        recorded={11: already},
    )

    fakes.run(now=noon(MONDAY))

    assert fakes.amended[0][0].shares == 21
