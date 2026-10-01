"""The tick calls the other modules in order. The modules themselves are tested
elsewhere, so these fakes stand in for the datastores and the Backend."""

import logging
from datetime import UTC, date, datetime, timedelta, timezone

from ktb_core.logging import bind_logger
from portfolio_rebalancer import tick as tick_module
from portfolio_rebalancer.market import Price
from portfolio_rebalancer.order.reservations import PRICE_BANDS, limit_and_trigger
from portfolio_rebalancer.portfolio import Exit, Holding, Portfolio
from portfolio_rebalancer.tick import market_now, tick

# Each side's ladder runs three sessions forward from the day its first order was
# recorded, so a cycle that begins on the Monday is on rung three by the Wednesday.
MONDAY = date(2026, 9, 28)
TUESDAY = date(2026, 9, 29)
WEDNESDAY = date(2026, 9, 30)
THURSDAY = date(2026, 10, 1)
FRIDAY = date(2026, 10, 2)
SAMSUNG = ("00126380", "005930")
HYNIX = ("00164779", "000660")


KST = timezone(timedelta(hours=9))


def noon(day):
    """A KST datetime in the middle of the session, well before the 15:00 cutoff.

    New orders are not placed at this hour; only the opening pass places.
    """
    return datetime(day.year, day.month, day.day, 11, 0, tzinfo=KST)


def opening(day):
    """The 09:00 pass, which is the only one that places a new order."""
    return datetime(day.year, day.month, day.day, 9, 0, tzinfo=KST)


def portfolio(holdings=None, exits=()):
    if holdings is None:
        holdings = [Holding(*SAMSUNG, weight=1.0, reason="사유")]
    return Portfolio(portfolio_id=42, cash_weight=0.0, holdings=holdings, exits=list(exits))


def pending(stock_code="005930", order_side="buy", price=74_100.0, amount=100, quote=None):
    """A pending order as the snapshot spells it.

    `quote` is the Backend's live price, which rides here rather than on the holding.
    """
    return {
        "order_id": abs(hash((stock_code, order_side, price, amount))) % 10**9,
        "stock_code": stock_code,
        "order_side": order_side,
        "order_status": "pending",
        "order_type": "limit",
        "limit_price": price,
        "quantity": amount,
        "current_stock_price": quote,
    }


def account(account_id=11, cash=10_000_000.0, stocks=(), pending_orders=()):
    return {
        "account_id": account_id,
        "account_name": "계좌",
        "is_active": True,
        "cash_balance": cash,
        "stocks": list(stocks),
        "pending_orders": list(pending_orders),
    }


def recorded_row(
    stock_code,
    sent_at=None,
    created=MONDAY,
    reference=78_000.0,
    trigger=None,
    side="buy",
    status="reserved",
):
    """A rebalance_orders row as find_orders returns it.

    `side` and `created_at` together are where that side's ladder starts, and
    reference_price is what a single limit price cannot say for itself.
    """
    if trigger is None and reference:
        trigger = limit_and_trigger(reference, 1, side)[1]
    return {
        "stock_code": stock_code,
        "side": side,
        "sent_at": sent_at,
        "created_at": datetime(created.year, created.month, created.day, 1, tzinfo=UTC),
        "reference_price": reference,
        "trigger_price": trigger,
        "status": status,
    }


def _quote(users, quoted):
    """Put the Backend's live price on the pending orders it reports.

    The snapshot quotes a stock only while an order on it is outstanding, so a holding
    with nothing pending has none and QuestDB's close stands in.
    """
    for user in users:
        for account_ in user["accounts"]:
            for entry in account_["pending_orders"]:
                price = quoted.get(str(entry["stock_code"]))
                if price is not None:
                    entry["current_stock_price"] = price
    return users


def polled(*accounts):
    return [
        {
            "user_id": 1,
            "accounts": list(accounts) or [account()],
        }
    ]


class Fakes:
    """Records every call the tick makes, in order."""

    def __init__(
        self, monkeypatch, *, model=None, users=None, prices=None, quoted=None, recorded=None
    ):
        self.calls: list[str] = []
        self.sent: list[list] = []
        self.recorded_rows: list[list] = []
        self.amended: list[list] = []
        self.marked: list[tuple] = []
        self.saved: list[list] = []
        self._recorded = recorded if recorded is not None else {}
        self._monkeypatch = monkeypatch
        self.discarded: list[tuple] = []
        self.today = TUESDAY

        model = portfolio() if model is None else model
        prices = {"005930": 78_000.0, "000660": 412_000.0} if prices is None else prices
        self.users = _quote(polled() if users is None else users, quoted or {})
        users = self.users
        self.asked = []

        def note(name, value=None):
            self.calls.append(name)
            return value

        monkeypatch.setattr(
            tick_module, "find_latest_portfolio", lambda conn: note("portfolio", model)
        )
        monkeypatch.setattr(tick_module, "fetch_accounts", lambda c: note("fetch", users))
        monkeypatch.setattr(
            tick_module, "write_poll", lambda conn, u: note("save") or self.saved.append(u)
        )
        monkeypatch.setattr(
            tick_module,
            "latest_prices",
            lambda db, codes: (
                note("prices")
                or self.asked.append(list(codes))
                or {
                    code: Price(close=prices[code], ts=datetime(2026, 9, 29, tzinfo=UTC))
                    for code in codes
                    if code in prices
                }
            ),
        )
        monkeypatch.setattr(
            tick_module,
            "find_orders",
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
            lambda c, orders: note("send") or self.sent.append(list(orders)),
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

    def run(self, now=None, log=None):
        """`now` still reads as an argument at every call site, but it is applied by
        patching the clock rather than by threading a parameter through `tick`."""
        now = now or opening(TUESDAY)
        self.today = now.date()
        self._monkeypatch.setattr(tick_module, "market_now", lambda: now)
        return tick(
            _Engine(),
            "db",
            "client",
            log=log or bind_logger(logging.getLogger("portfolio_rebalancer")),
        )


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
    monkeypatch.setattr(tick_module, "find_latest_portfolio", lambda conn: None)

    assert fakes.run() == 0
    assert "fetch" not in fakes.calls


def test_the_poll_is_mirrored_before_anything_is_decided(monkeypatch):
    fakes = Fakes(monkeypatch)

    fakes.run()

    assert fakes.calls.index("save") < fakes.calls.index("stored")
    assert fakes.saved == [fakes.users]


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
    held = [{"stock_code": "005930", "total_cost": 7_800_000, "quantity": 100}]
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
    limit, _ = limit_and_trigger(78_000.0, 1, "buy")
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(pending_orders=[pending(price=limit)])),
        recorded={11: already},
    )

    assert fakes.run(now=noon(TUESDAY)) == 1
    assert fakes.recorded_rows == []
    assert [order.band for order in fakes.amended[0]] == [PRICE_BANDS[1]]
    assert fakes.calls.index("amend") < fakes.calls.index("send")


def test_a_pair_already_matching_the_plan_is_left_alone(monkeypatch):
    """The poll runs hourly. A pair at the planned quantity and today's band is re-quoted
    by nothing, or the Backend would see the same order every hour."""
    limit, _ = limit_and_trigger(78_000.0, 1, "buy")
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
    fakes = Fakes(monkeypatch, users=polled(account() | {"is_active": False}))

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


def test_a_first_purchase_is_priced_from_questdb(monkeypatch):
    """The poll quotes only what the account holds, so a name it has never held has no
    price there -- and that is exactly the case of buying one for the first time."""
    plan = Portfolio(
        portfolio_id=42,
        cash_weight=0.0,
        holdings=[Holding(*HYNIX, weight=1.0, reason="사유")],
        exits=[],
    )
    fakes = Fakes(
        monkeypatch,
        model=plan,
        users=polled(account(cash=10_000_000.0, stocks=[])),
        prices={"000660": 412_000.0},
    )

    fakes.run()

    assert [o.stock_code for o in fakes.sent[0]] == ["000660"]
    assert fakes.sent[0][0].reference == 412_000.0


def test_every_price_comes_from_questdb_even_when_the_backend_quotes(monkeypatch):
    """The Backend's `current_stock_price` is not used: an order is placed from the close
    QuestDB holds, whatever the poll carries."""
    plan = Portfolio(
        portfolio_id=42,
        cash_weight=0.0,
        holdings=[
            Holding(*SAMSUNG, weight=0.5, reason="사유"),
            Holding(*HYNIX, weight=0.5, reason="사유"),
        ],
        exits=[],
    )
    fakes = Fakes(
        monkeypatch,
        model=plan,
        users=polled(account(cash=10_000_000.0)),
        prices={"005930": 78_000.0, "000660": 412_000.0},
    )
    fakes.users[0]["accounts"][0]["pending_orders"] = []

    fakes.run(now=opening(TUESDAY))

    assert {order.stock_code: order.reference for order in fakes.sent[0]} == {
        "005930": 78_000.0,
        "000660": 412_000.0,
    }


def test_questdb_is_read_once_per_tick_for_every_account(monkeypatch):
    """One read covers the portfolio and every stock any account holds or has pending."""
    first = account(
        account_id=11, stocks=[{"stock_code": "035420", "total_cost": 1, "quantity": 1}]
    )
    second = account(
        account_id=12,
        pending_orders=[pending(stock_code="068270", quote=200_000.0)],
    )
    fakes = Fakes(monkeypatch, users=polled(first, second))

    fakes.run()

    assert fakes.asked == [["005930", "035420", "068270"]]


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

    assert fakes.run(now=opening(MONDAY)) > 0
    assert fakes.amended
    assert fakes.recorded_rows == []


def test_a_recorded_order_off_the_book_goes_back_at_once(monkeypatch):
    """Only a fresh cycle waits for the opening pass; a cycle already under way reacts on
    the pass that sees the change."""
    events = Events()
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=10_000_000.0, pending_orders=[])),
        recorded={11: already},
    )

    assert fakes.run(now=noon(MONDAY), log=events) > 0
    assert fakes.amended
    assert events.of("placing_deferred") == []


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
    limit, _ = limit_and_trigger(78_000.0, 1, "buy")
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

    sent = fakes.run(now=opening(TUESDAY))

    assert fakes.discarded == [(42, 11)]
    assert sent == 1
    assert fakes.recorded_rows


def test_a_stamped_record_is_not_discarded(monkeypatch):
    """Nothing that reached the Backend is ever dropped from the history."""
    limit, _ = limit_and_trigger(78_000.0, 1, "buy")
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

    fakes.run(now=opening(TUESDAY))

    order = fakes.sent[0][0]
    assert order.reference == 90_000.0
    assert order.band == PRICE_BANDS[0]


def test_a_discarded_record_is_re_sized_at_the_new_price(monkeypatch):
    """The share count is decided again too, not carried over from the discarded record."""
    unsent = [recorded_row("005930", sent_at=None)]
    cheap = Fakes(monkeypatch, prices={"005930": 10_000.0}, recorded={11: [dict(unsent[0])]})
    cheap.run(now=opening(TUESDAY))

    dear = Fakes(monkeypatch, prices={"005930": 500_000.0}, recorded={11: [dict(unsent[0])]})
    dear.run(now=opening(TUESDAY))

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
        account(cash=0.0, stocks=[{"stock_code": "000660", "total_cost": 1, "quantity": 10}])
    )
    fakes = Fakes(monkeypatch, model=plan, users=users)

    fakes.run(now=opening(MONDAY))

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

    fakes.run(now=opening(MONDAY))

    buys = [order for order in fakes.sent[0] if order.action == "buy"]
    assert buys
    assert buys[0].shares * buys[0].trigger <= 4_120_000.0


def test_an_order_placed_late_in_the_cycle_starts_narrow(monkeypatch):
    """Selling and buying share one ladder. A buy first placed on the cycle's second
    session goes out on the second rung rather than restarting on the widest."""
    plan = Portfolio(
        portfolio_id=42,
        cash_weight=0.0,
        holdings=[Holding(*SAMSUNG, weight=1.0, reason="사유")],
        exits=[Exit(*HYNIX, reason="퇴출")],
    )
    already = [recorded_row("000660", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    users = polled(account(cash=4_120_000.0))
    fakes = Fakes(monkeypatch, model=plan, users=users, recorded={11: already})

    fakes.run(now=opening(TUESDAY))

    buys = [order for order in fakes.sent[0] if order.action == "buy"]
    assert buys[0].band == PRICE_BANDS[1]


def test_a_buy_the_sells_funded_goes_out_at_once(monkeypatch):
    """The sells freeing the cash is the signal the buy was waiting for. Holding it to
    the next morning would spend a session of the buy's own ladder on nothing."""
    plan = Portfolio(
        portfolio_id=42,
        cash_weight=0.0,
        holdings=[Holding(*SAMSUNG, weight=1.0, reason="사유")],
        exits=[Exit(*HYNIX, reason="퇴출")],
    )
    already = [recorded_row("000660", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    users = polled(account(cash=4_120_000.0))
    fakes = Fakes(monkeypatch, model=plan, users=users, recorded={11: already})

    events = Events()
    fakes.run(now=noon(TUESDAY), log=events)

    assert [order.stock_code for order in fakes.sent[0]] == ["005930"]
    assert events.of("placing_deferred") == []


def test_a_fresh_cycle_waits_for_the_opening_pass_too(monkeypatch):
    """A new portfolio lands before the open, so its first order belongs at 09:00."""
    events = Events()
    fakes = Fakes(monkeypatch)

    assert fakes.run(now=noon(TUESDAY), log=events) == 0
    assert fakes.recorded_rows == []
    assert events.of("placing_deferred")[0]["cycle"] == "open"


def test_the_opening_pass_does_place(monkeypatch):
    """The gate is on the hour, not on placing at all."""
    events = Events()
    fakes = Fakes(monkeypatch)

    assert fakes.run(now=opening(TUESDAY), log=events) > 0
    assert events.of("placing_deferred") == []


def working_buy(reference=78_000.0, day=1, amount=52, quote=None):
    """What the poll reports for a buy reservation: one order, at the limit.

    `quote` is the Backend's live price, which decides the trigger and rides on the
    order itself.
    """
    limit, _ = limit_and_trigger(reference, day, "buy")
    return [pending(price=limit, amount=amount, quote=quote)]


def test_a_buy_struck_at_its_trigger_is_blocked_rather_than_sent_at_market(monkeypatch):
    """The dip is not coming, so waiting stops being worth it -- but the Backend takes
    only limit orders, so the order is held back and the block is logged."""
    events = Events()
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    _, trigger = limit_and_trigger(78_000.0, 1, "buy")
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=0.0, pending_orders=working_buy(quote=trigger))),
        prices={"005930": trigger},
        recorded={11: already},
    )

    assert fakes.run(now=noon(MONDAY), log=events) == 0
    assert fakes.amended == []
    assert events.of("orders_blocked")[0]["codes"] == ["005930"]


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
    limit, trigger = limit_and_trigger(412_000.0, 1, "sell")
    already = [
        recorded_row(
            "000660",
            sent_at=datetime(2026, 9, 28, tzinfo=UTC),
            reference=412_000.0,
            trigger=limit_and_trigger(412_000.0, 1, "sell")[1],
            side="sell",
        )
    ]
    outstanding = [
        pending(stock_code="000660", order_side="sell", price=limit, amount=10, quote=trigger)
    ]
    events = Events()
    fakes = Fakes(
        monkeypatch,
        model=plan,
        users=polled(account(cash=0.0, pending_orders=outstanding)),
        prices={"005930": 78_000.0, "000660": trigger},
        recorded={11: already},
    )

    fakes.run(now=noon(MONDAY), log=events)

    assert not any(o.stock_code == "000660" for batch in fakes.amended for o in batch)
    assert "000660" in events.of("orders_blocked")[0]["codes"]


def test_a_blocked_order_is_neither_recorded_nor_amended(monkeypatch):
    """Half a state is worse than none: the rung already on the book keeps standing."""
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    _, trigger = limit_and_trigger(78_000.0, 1, "buy")
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=0.0, pending_orders=working_buy(quote=trigger))),
        quoted={"005930": trigger},
        recorded={11: already},
    )

    fakes.run(now=noon(MONDAY))

    assert fakes.recorded_rows == []
    assert fakes.amended == []
    assert fakes.sent == []


def test_a_block_names_the_stock_it_held_back(monkeypatch):
    """So an operator can see which order stopped moving, and why."""
    events = Events()
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    _, trigger = limit_and_trigger(78_000.0, 1, "buy")
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=0.0, pending_orders=working_buy(amount=21, quote=trigger))),
        prices={"005930": trigger},
        recorded={11: already},
    )

    fakes.run(now=noon(MONDAY), log=events)

    blocked = events.of("orders_blocked")[0]
    assert blocked["codes"] == ["005930"]
    assert "limit" in blocked["reason"]


def test_the_trigger_is_judged_on_the_questdb_close_not_the_backends_quote(monkeypatch):
    """The close has reached the trigger while the Backend still quotes far above it: the
    close is what decides."""
    plan = Portfolio(
        portfolio_id=42,
        cash_weight=0.0,
        holdings=[Holding(*SAMSUNG, weight=1.0, reason="사유")],
        exits=[Exit(*HYNIX, reason="퇴출")],
    )
    limit, trigger = limit_and_trigger(412_000.0, 1, "sell")
    already = [
        recorded_row(
            "000660",
            sent_at=datetime(2026, 9, 28, tzinfo=UTC),
            reference=412_000.0,
            trigger=trigger,
            side="sell",
        )
    ]
    held = [{"stock_code": "000660", "total_cost": 1, "quantity": 10}]
    outstanding = [
        pending(stock_code="000660", order_side="sell", price=limit, amount=10, quote=999_999.0)
    ]
    events = Events()
    fakes = Fakes(
        monkeypatch,
        model=plan,
        users=polled(account(cash=0.0, stocks=held, pending_orders=outstanding)),
        prices={"005930": 78_000.0, "000660": trigger},
        recorded={11: already},
    )

    fakes.run(now=noon(MONDAY), log=events)

    # QuestDB's 999,999 would never reach a sell trigger. The Backend's quote does, and
    # it is the only price the trigger is judged against.
    assert "000660" in events.of("orders_blocked")[0]["codes"]


def test_an_outstanding_buy_on_a_stock_not_held_is_watched_from_the_close(monkeypatch):
    """QuestDB is asked for every pending order's stock, so a buy on a stock the account
    does not hold yet is still watched."""
    events = Events()
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    _, trigger = limit_and_trigger(78_000.0, 1, "buy")
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=0.0, stocks=[], pending_orders=working_buy(quote=trigger))),
        prices={"005930": trigger},
        recorded={11: already},
    )

    fakes.run(now=noon(MONDAY), log=events)

    assert events.of("orders_blocked")[0]["codes"] == ["005930"]


def test_an_order_already_at_market_is_not_sent_again(monkeypatch):
    """The market rung is the end of the ladder, not a band that keeps being re-quoted.

    A market order that stayed on the book is still outstanding at the next poll, and it
    has no band left to move to, so re-quoting it would post the same order again.
    """
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC), status="market")]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=0.0, pending_orders=working_buy())),
        recorded={11: already},
    )

    assert fakes.run(now=datetime(2026, 10, 2, 15, 0, tzinfo=KST)) == 0
    assert fakes.amended == []


# ---- structured logging: what an operator has to be able to see in CloudWatch ----


class Events:
    """Collects the structured events one tick emits, in order.

    The signature mirrors ktb_core.logging.bind_logger's returned callable.
    """

    def __init__(self):
        self.seen: list[tuple] = []

    def __call__(self, event, level=logging.INFO, **fields):
        self.seen.append((event, level, fields))

    @property
    def names(self):
        return [event for event, _, _ in self.seen]

    def of(self, name):
        return [fields for event, _, fields in self.seen if event == name]

    def levels(self, name):
        return [level for event, level, _ in self.seen if event == name]


def test_every_pass_brackets_itself_so_the_hourly_cadence_is_readable(monkeypatch):
    """The gap between two tick_start lines is the polling cadence, so each pass has to
    say when it began and how it ended."""
    events = Events()
    Fakes(monkeypatch).run(log=events)

    assert events.names[0] == "tick_start"
    assert events.names[-1] == "tick_end"
    assert events.of("tick_start")[0]["at"].startswith("2026-09-29")


def test_the_poll_reports_what_it_carried(monkeypatch):
    events = Events()
    Fakes(monkeypatch).run(log=events)

    poll = events.of("backend_poll")[0]
    assert (poll["users"], poll["accounts"], poll["managed_accounts"]) == (1, 1, 1)
    assert poll["accounts_missing_id_or_cash"] == []


def test_an_empty_poll_is_a_warning(monkeypatch):
    """An empty poll must not pass silently: every later step would find nothing to do
    and say nothing about why."""
    events = Events()
    fakes = Fakes(monkeypatch, users=[])

    assert fakes.run(log=events) == 0
    assert events.levels("backend_poll") == [logging.WARNING]
    assert events.of("backend_poll")[0]["users"] == 0


def test_the_poll_reports_the_whole_snapshot(monkeypatch):
    """The poll is every user's accounts, holdings and pending orders, not a price feed."""
    events = Events()
    held = [{"stock_code": "005930", "total_cost": 7_800_000, "quantity": 100}]
    outstanding = [pending(), pending(stock_code="000660", price=400_000.0)]
    Fakes(monkeypatch, users=polled(account(stocks=held, pending_orders=outstanding))).run(
        log=events
    )

    poll = events.of("backend_poll")[0]
    assert (poll["users"], poll["accounts"], poll["holdings"], poll["pending_orders"]) == (
        1,
        1,
        1,
        2,
    )


def test_the_portfolio_reports_its_reasons(monkeypatch):
    """PostgreSQL hands over the portfolio, and a holding with no reason is named."""
    events = Events()
    plan = portfolio(holdings=[Holding(*SAMSUNG, weight=1.0, reason=None)])
    Fakes(monkeypatch, model=plan).run(log=events)

    loaded = events.of("portfolio_loaded")[0]
    assert loaded["portfolio_id"] == 42
    assert loaded["holding_codes"] == ["005930"]
    assert loaded["holdings_missing_reason"] == ["005930"]


def test_a_portfolio_that_never_arrives_is_a_warning(monkeypatch):
    events = Events()
    fakes = Fakes(monkeypatch)
    monkeypatch.setattr(tick_module, "find_latest_portfolio", lambda conn: None)

    assert fakes.run(log=events) == 0
    assert events.levels("portfolio_missing") == [logging.WARNING]


def test_an_order_that_goes_out_carries_its_numbers(monkeypatch):
    """Orders actually go, and never with blank values."""
    events = Events()
    fakes = Fakes(monkeypatch)

    assert fakes.run(log=events) > 0
    sending = events.of("orders_sending")[0]
    assert sending["count"] == len(fakes.sent[0])
    order = sending["orders"][0]
    assert order["shares"] > 0
    assert order["reference"] > 0
    assert order["has_reason"] is True
    assert events.of("orders_sent")[0]["count"] == sending["count"]
    assert "orders_incomplete" not in events.names


def test_an_order_already_on_the_book_is_logged_as_suppressed(monkeypatch):
    """No duplicate order: a poll that brings an unfilled order back does not place it a
    second time, and the line says so."""
    events = Events()
    limit, _ = limit_and_trigger(78_000.0, 1, "buy")
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=10_000_000.0, pending_orders=[pending(price=limit, amount=52)])),
        recorded={11: already},
    )

    fakes.run(now=noon(MONDAY), log=events)

    assert events.of("duplicate_suppressed")[0]["codes"] == ["005930"]
    assert events.of("recorded_orders")[0]["still_working"] == ["005930"]


def test_the_ladder_step_records_the_band_it_moved_to(monkeypatch):
    """As the sessions run out the order really does narrow, one rung per line."""
    events = Events()
    limit, _ = limit_and_trigger(78_000.0, 1, "buy")
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=0.0, pending_orders=[pending(price=limit, amount=52)])),
        recorded={11: already},
    )

    fakes.run(now=noon(TUESDAY), log=events)

    step = events.of("ladder_step")[0]
    assert step["ladder_day"]["buy"] == 2
    assert step["steps"][0]["band"] == PRICE_BANDS[1]
    assert step["steps"][0]["at_market"] is False


def test_a_filled_order_is_reported_as_done_and_not_re_ordered(monkeypatch):
    """An order that already filled has left pending_orders and become a holding, so the
    plan asks for nothing and nothing is sent again."""
    events = Events()
    already = [recorded_row("005930", sent_at=datetime(2026, 9, 28, tzinfo=UTC))]
    held = [{"stock_code": "005930", "total_cost": 7_800_000, "quantity": 100}]
    fakes = Fakes(monkeypatch, users=polled(account(cash=0.0, stocks=held)), recorded={11: already})

    assert fakes.run(now=noon(MONDAY), log=events) == 0
    recorded = events.of("recorded_orders")[0]
    assert recorded["filled_or_done"] == ["005930"]
    assert recorded["re_placed"] == []
    assert fakes.sent == []


def test_a_record_that_never_reached_the_backend_is_reported(monkeypatch):
    """Re-deciding after a lost reply must not duplicate; which way it went is on the
    line."""
    events = Events()
    unsent = [recorded_row("005930", sent_at=None)]
    fakes = Fakes(monkeypatch, users=polled(account(cash=0.0)), recorded={11: unsent})

    fakes.run(now=noon(MONDAY), log=events)

    reconciled = events.of("unsent_reconciled")[0]
    assert reconciled["outcome"] == "discarded"
    assert events.levels("unsent_reconciled") == [logging.WARNING]


# ---- one ladder per side ----


def test_each_side_is_on_its_own_rung(monkeypatch):
    """The sells began with the cycle; the buy was placed a session later and is on its
    own first rung, not on the sell's second."""
    events = Events()
    plan = Portfolio(
        portfolio_id=42,
        cash_weight=0.0,
        holdings=[Holding(*SAMSUNG, weight=1.0, reason="사유")],
        exits=[Exit(*HYNIX, reason="퇴출")],
    )
    recorded = [
        recorded_row(
            "000660",
            side="sell",
            created=MONDAY,
            reference=412_000.0,
            sent_at=datetime(2026, 9, 28, tzinfo=UTC),
        ),
        recorded_row(
            "005930", side="buy", created=TUESDAY, sent_at=datetime(2026, 9, 29, tzinfo=UTC)
        ),
    ]
    sell_limit, _ = limit_and_trigger(412_000.0, 2, "sell")
    buy_limit, _ = limit_and_trigger(78_000.0, 1, "buy")
    outstanding = [
        pending(stock_code="000660", order_side="sell", price=sell_limit, amount=10),
        pending(stock_code="005930", price=buy_limit, amount=52),
    ]
    fakes = Fakes(
        monkeypatch,
        model=plan,
        users=polled(account(cash=0.0, pending_orders=outstanding)),
        recorded={11: recorded},
    )

    fakes.run(now=noon(TUESDAY), log=events)

    # Tuesday is the sell ladder's second session and the buy ladder's first.
    assert events.of("recorded_orders")[0]["ladder_day"] == {"sell": 2, "buy": 1}


def test_a_sell_that_takes_two_sessions_does_not_cost_the_buy_two(monkeypatch):
    """The buy gets its own three sessions from the day it was placed."""
    events = Events()
    recorded = [
        recorded_row(
            "005930", side="buy", created=WEDNESDAY, sent_at=datetime(2026, 9, 30, tzinfo=UTC)
        ),
    ]
    limit, _ = limit_and_trigger(78_000.0, 1, "buy")
    fakes = Fakes(
        monkeypatch,
        users=polled(account(cash=0.0, pending_orders=[pending(price=limit, amount=52)])),
        recorded={11: recorded},
    )

    fakes.run(now=noon(FRIDAY), log=events)

    # Wednesday, Thursday, Friday: the buy's own third rung, not a spent ladder.
    assert events.of("recorded_orders")[0]["ladder_day"] == {"buy": 3}
    assert events.of("ladder_step")[0]["steps"][0]["band"] == PRICE_BANDS[2]
