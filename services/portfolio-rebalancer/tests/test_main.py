import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from portfolio_rebalancer import __main__ as entry
from portfolio_rebalancer.holidays import KST
from portfolio_rebalancer.portfolio import Explanation, Portfolio, Target, Unready
from portfolio_rebalancer.snapshot import User

REQUIRED = {
    "PORTFOLIO_REBALANCER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "PORTFOLIO_REBALANCER_QUESTDB_CONF": "ws::addr=localhost:9000;",
    "PORTFOLIO_REBALANCER_BACKEND_URL": "http://backend",
    "PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET": "s" * 32,
    "PORTFOLIO_REBALANCER_BACKEND_JWT_ISSUER": "river-be",
}
WEDNESDAY_NOON = datetime(2026, 10, 14, 12, tzinfo=KST)
WHY = Explanation(reason="사요", reasonings=[{"label": "근거", "body": "사요"}])
PORTFOLIO = Portfolio(
    id=5,
    cash_weight=0.5,
    targets=[Target(stock_code="005930", weight=0.5, exiting=False, buy=WHY, sell=WHY)],
    leftovers={},
    names={"005930": "삼성전자"},
)


def account(account_id, stocks=(), pending=(), active=True, cash=1_000_000):
    return {
        "account_id": account_id,
        "is_active": active,
        "cash_balance": cash,
        "stocks": [{"stock_code": c, "quantity": q, "total_cost": 0} for c, q in stocks],
        "pending_orders": [
            {
                "order_id": order_id,
                "stock_code": "005930",
                "order_side": "buy",
                "order_type": "limit",
                "order_status": "pending",
                "limit_price": 95_000,
                "quantity": 2,
                "current_stock_price": 100_000,
            }
            for order_id in pending
        ],
    }


def users(*accounts):
    return [
        User.model_validate({"user_id": 1, "accounts": list(accounts)}),
        User(user_id=2, accounts=[]),
    ]


USERS = users(account(11, stocks=[("000660", 1)]), account(12))


class FakeBackend:
    def __init__(
        self,
        client,
        secret,
        issuer,
        users=USERS,
        refreshed=None,
        fail_account=None,
        fail_cancel=None,
    ):
        self.calls = []
        self.placed = []
        self.cancelled = []
        self._snapshots = [users, refreshed or users]
        self.fail_account = fail_account
        self.fail_cancel = fail_cancel

    def users(self):
        self.calls.append(("users",))
        return self._snapshots[min(len(self.calls_of("users")) - 1, 1)]

    def calls_of(self, kind):
        return [c for c in self.calls if c[0] == kind]

    def _boom(self, method):
        request = httpx.Request(method, "http://backend")
        raise httpx.HTTPStatusError(
            "boom", request=request, response=httpx.Response(409, text="bad", request=request)
        )

    def cancel(self, user_id, account_id, order_id):
        if order_id == self.fail_cancel:
            self._boom("PATCH")
        self.calls.append(("cancel", account_id, order_id))
        self.cancelled.append((user_id, account_id, order_id))

    def place(self, user_id, account_id, order):
        if account_id == self.fail_account:
            self._boom("POST")
        self.calls.append(("place", account_id, order.stock_code))
        self.placed.append((user_id, account_id, order.stock_code, order.quantity))


def clock(monkeypatch, now):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr(entry, "datetime", Frozen)


@pytest.fixture
def env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(entry, "load_portfolio", lambda engine: PORTFOLIO)
    clock(monkeypatch, WEDNESDAY_NOON)
    asked = {}

    def closes(conf, codes, now):
        asked["codes"] = codes
        asked["now"] = now
        return {"005930": [100_000.0] * 20}

    monkeypatch.setattr(entry, "daily_closes", closes)
    monkeypatch.setattr(entry, "latest_prices", lambda conf, codes: {"005930": 100_000.0})
    return asked


def _events(out):
    return [json.loads(line) for line in out.splitlines()]


def _use(monkeypatch, **kwargs):
    backends = []

    def make(client, secret, issuer):
        backend = FakeBackend(client, secret, issuer, **kwargs)
        backends.append(backend)
        return backend

    monkeypatch.setattr(entry, "Backend", make)
    return backends


def _main():
    with pytest.raises(SystemExit) as exit_:
        entry.main()
    return exit_.value.code


def test_every_account_is_rebalanced_on_the_ladder_and_the_run_exits_zero(env, monkeypatch, capsys):
    backends = _use(monkeypatch)

    assert _main() == 0
    assert backends[0].placed == [(1, 11, "005930", 4), (1, 12, "005930", 4)]
    assert env["codes"] == {"005930", "000660"}
    events = _events(capsys.readouterr().out)
    sent = next(e for e in events if e["message"] == "order_sent")
    assert (sent["order_type"], sent["limit_price"], sent["trigger"]) == ("limit", 100_000, None)
    assert {"sma", "sigma", "alpha", "lower_bound", "upper_bound", "price"} <= sent.keys()
    received = [e for e in events if e["message"] == "users_received"]
    assert [e["user_count"] for e in received] == [2]
    end = events[-1]
    assert (end["sent"], end["failed"], end["limit"], end["market"]) == (2, 0, 2, 0)
    assert (end["runs_left"], end["week_runs"], end["last_run"]) == (18, 35, False)
    missing = {e["missing"] for e in events if e["message"] == "no_price"}
    assert missing == {"daily_closes", "latest_price"}


def test_pending_orders_are_cancelled_before_placing(env, monkeypatch, capsys):
    backends = _use(
        monkeypatch, users=users(account(11, pending=[77, 78])), refreshed=users(account(11))
    )

    assert _main() == 0
    assert backends[0].cancelled == [(1, 11, 77), (1, 11, 78)]
    events = _events(capsys.readouterr().out)
    cancelled = [e for e in events if e["message"] == "order_cancelled"]
    assert [(e["order_id"], e["limit_price"], e["quantity"]) for e in cancelled] == [
        (77, 95_000, 2),
        (78, 95_000, 2),
    ]
    assert events[-1]["cancelled"] == 2
    received = [e for e in events if e["message"] == "users_received"]
    assert [e["user_count"] for e in received] == [2, 2]
    kinds = [c[0] for c in backends[0].calls]
    assert kinds == ["users", "cancel", "cancel", "users", "place"]


def test_a_failed_cancel_skips_the_account_and_exits_one(env, monkeypatch, capsys):
    backends = _use(
        monkeypatch, users=users(account(11, pending=[77]), account(12)), fail_cancel=77
    )

    assert _main() == 1
    assert backends[0].placed == [(1, 12, "005930", 4)]
    events = _events(capsys.readouterr().out)
    failed = next(e for e in events if e["message"] == "cancel_failed")
    assert (failed["account_id"], failed["order_id"], failed["status"]) == (11, 77, 409)
    assert events[-1]["cancel_failed"] == 1


def test_an_inactive_account_is_not_cancelled(env, monkeypatch):
    backends = _use(monkeypatch, users=users(account(11, pending=[77], active=False)))

    assert _main() == 0
    assert backends[0].cancelled == []
    assert backends[0].placed == []


def test_a_failed_order_is_logged_the_rest_sent_and_the_run_exits_one(env, monkeypatch, capsys):
    backends = _use(monkeypatch, fail_account=12)

    assert _main() == 1
    assert backends[0].placed == [(1, 11, "005930", 4)]
    failed = next(e for e in _events(capsys.readouterr().out) if e["message"] == "order_failed")
    assert (failed["account_id"], failed["status"], failed["body"]) == (12, 409, "bad")
    assert failed["order_type"] == "limit"
    assert failed["stock_name"] == "삼성전자"
    assert failed["reason"] == "사요"


def test_the_last_run_of_the_week_sends_market_orders(env, monkeypatch, capsys):
    clock(monkeypatch, datetime(2026, 10, 16, 15, tzinfo=KST))
    _use(monkeypatch)

    assert _main() == 0
    events = _events(capsys.readouterr().out)
    sent = [e for e in events if e["message"] == "order_sent"]
    assert {(e["order_type"], e["trigger"]) for e in sent} == {("market", "last_run")}
    assert events[-1]["last_run"] is True


def test_test_mode_executes_on_a_market_holiday(env, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_REBALANCER_TEST_MODE", "true")
    clock(monkeypatch, datetime(2026, 10, 9, 10, tzinfo=KST))
    backends = _use(monkeypatch)

    assert _main() == 0
    assert backends[0].calls_of("users") == [("users",)]


def test_default_mode_skips_a_market_holiday(env, monkeypatch, capsys):
    clock(monkeypatch, datetime(2026, 10, 9, 10, tzinfo=KST))
    backends = _use(monkeypatch)

    assert _main() == 0
    assert backends == []
    assert _events(capsys.readouterr().out)[-1]["outcome"] == "market_closed"


def test_an_empty_user_response_is_logged_as_zero(env, monkeypatch, capsys):
    _use(monkeypatch, users=[])

    assert _main() == 0

    events = _events(capsys.readouterr().out)
    received = [e for e in events if e["message"] == "users_received"]
    assert [e["user_count"] for e in received] == [0]


def test_a_calendar_ending_within_a_month_is_warned(env, monkeypatch, capsys):
    monkeypatch.setattr(entry, "COVERED_THROUGH", WEDNESDAY_NOON.date() + timedelta(days=10))
    _use(monkeypatch)

    _main()

    warn = next(e for e in _events(capsys.readouterr().out) if e["message"] == "holidays_expiring")
    assert (warn["covered_through"], warn["days_left"]) == ("2026-10-24", 10)


def test_no_explained_portfolio_exits_zero_without_calling_the_backend(env, monkeypatch, capsys):
    monkeypatch.setattr(entry, "load_portfolio", lambda engine: None)
    backends = _use(monkeypatch)

    assert _main() == 0
    assert backends == []
    assert _events(capsys.readouterr().out)[-1]["outcome"] == "no_portfolio"


@pytest.mark.parametrize(
    ("status", "code", "level"),
    [("explanation_pending", 0, "INFO"), ("explanation_failed", 1, "ERROR")],
)
def test_an_unready_latest_portfolio_sends_nothing(env, monkeypatch, capsys, status, code, level):
    monkeypatch.setattr(entry, "load_portfolio", lambda engine: Unready(id=8, status=status))
    backends = _use(monkeypatch)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == code
    assert backends == []
    last = _events(capsys.readouterr().out)[-1]
    assert (last["outcome"], last["portfolio_id"], last["level"]) == (status, 8, level)


def test_the_secret_never_reaches_the_log(env, monkeypatch, capsys):
    _use(monkeypatch)

    _main()

    assert "s" * 32 not in capsys.readouterr().out


def test_a_stranded_holding_is_logged_as_leftover_without_reason(env, monkeypatch, capsys):
    _use(monkeypatch)

    _main()

    events = _events(capsys.readouterr().out)
    stranded = [e for e in events if e["message"] == "leftover_without_reason"]
    assert [(e["account_id"], e["stock_codes"]) for e in stranded] == [(11, ["000660"])]


def test_the_clock_reaches_the_daily_closes_query(env, monkeypatch):
    _use(monkeypatch)

    _main()

    assert env["now"] == WEDNESDAY_NOON.astimezone(UTC)


def test_trades_are_sized_from_the_snapshot_after_cancelling(env, monkeypatch):
    filled = users(account(11, stocks=[("005930", 4)], cash=600_000))
    backends = _use(monkeypatch, users=users(account(11, pending=[77])), refreshed=filled)

    assert _main() == 0
    assert backends[0].cancelled == [(1, 11, 77)]
    assert backends[0].placed == []


def test_an_account_still_pending_after_cancelling_is_skipped(env, monkeypatch, capsys):
    backends = _use(
        monkeypatch,
        users=users(account(11, pending=[77])),
        refreshed=users(account(11, pending=[79])),
    )

    assert _main() == 0
    assert backends[0].placed == []
    warn = next(
        e for e in _events(capsys.readouterr().out) if e["message"] == "pending_after_cancel"
    )
    assert (warn["account_id"], warn["order_ids"]) == (11, [79])
