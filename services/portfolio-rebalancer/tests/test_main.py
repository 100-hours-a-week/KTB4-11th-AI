import json

import httpx
import pytest
from portfolio_rebalancer import __main__ as entry
from portfolio_rebalancer.portfolio import Explanation, Portfolio, Target
from portfolio_rebalancer.snapshot import User

REQUIRED = {
    "PORTFOLIO_REBALANCER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "PORTFOLIO_REBALANCER_QUESTDB_CONF": "ws::addr=localhost:9000;",
    "PORTFOLIO_REBALANCER_BACKEND_URL": "http://backend",
    "PORTFOLIO_REBALANCER_BACKEND_JWT_SECRET": "s" * 32,
    "PORTFOLIO_REBALANCER_BACKEND_JWT_ISSUER": "river-be",
}
WHY = Explanation(reason="사요", reasonings=[{"label": "근거", "body": "사요"}])
PORTFOLIO = Portfolio(
    id=5,
    targets=[Target(stock_code="005930", weight=0.5, exiting=False, buy=WHY, sell=WHY)],
    leftovers={},
    names={"005930": "삼성전자"},
)
USERS = [
    User.model_validate(
        {
            "user_id": 1,
            "accounts": [
                {
                    "account_id": 11,
                    "is_active": True,
                    "cash_balance": 1_000_000,
                    "stocks": [{"stock_code": "000660", "quantity": 1, "total_cost": 0}],
                    "pending_orders": [],
                },
                {
                    "account_id": 12,
                    "is_active": True,
                    "cash_balance": 1_000_000,
                    "stocks": [],
                    "pending_orders": [],
                },
            ],
        }
    ),
    User(user_id=2, accounts=[]),
]


class FakeBackend:
    def __init__(self, client, secret, issuer, fail_account=None):
        self.placed = []
        self.fail_account = fail_account

    def users(self):
        return USERS

    def place(self, user_id, account_id, order):
        if account_id == self.fail_account:
            request = httpx.Request("POST", "http://backend")
            raise httpx.HTTPStatusError(
                "boom", request=request, response=httpx.Response(400, text="bad", request=request)
            )
        self.placed.append((user_id, account_id, order.stock_code, order.quantity))


@pytest.fixture
def env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(entry, "load_portfolio", lambda engine: PORTFOLIO)
    asked = {}

    def closes(conf, codes):
        asked["codes"] = codes
        return {"005930": 100_000}

    monkeypatch.setattr(entry, "last_closes", closes)
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


def test_every_account_is_rebalanced_and_the_run_exits_zero(env, monkeypatch, capsys):
    backends = _use(monkeypatch)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 0
    assert backends[0].placed == [(1, 12, "005930", 4)]
    assert env["codes"] == {"005930", "000660"}
    events = _events(capsys.readouterr().out)
    assert events[-1]["sent"] == 1
    assert events[-1]["failed"] == 0
    assert any(e["message"] == "no_close" and e["stock_codes"] == ["000660"] for e in events)


def test_a_failed_order_is_logged_the_rest_sent_and_the_run_exits_one(env, monkeypatch, capsys):
    backends = _use(monkeypatch, fail_account=12)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 1
    assert backends[0].placed == []
    failed = next(e for e in _events(capsys.readouterr().out) if e["message"] == "order_failed")
    assert failed["account_id"] == 12
    assert failed["status"] == 400
    assert failed["body"] == "bad"


def test_no_explained_portfolio_exits_zero_without_calling_the_backend(env, monkeypatch, capsys):
    monkeypatch.setattr(entry, "load_portfolio", lambda engine: None)
    backends = _use(monkeypatch)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 0
    assert backends == []
    assert _events(capsys.readouterr().out)[-1]["outcome"] == "no_portfolio"


def test_the_secret_never_reaches_the_log(env, monkeypatch, capsys):
    _use(monkeypatch)

    with pytest.raises(SystemExit):
        entry.main()

    assert "s" * 32 not in capsys.readouterr().out


def test_a_stranded_holding_is_logged_as_leftover_without_reason(env, monkeypatch, capsys):
    _use(monkeypatch)

    with pytest.raises(SystemExit):
        entry.main()

    events = _events(capsys.readouterr().out)
    leftover_warn = next(
        (e for e in events if e["message"] == "leftover_without_reason" and e["account_id"] == 11),
        None,
    )
    assert leftover_warn is not None
    assert leftover_warn["stock_codes"] == ["000660"]

    leftover_warn_12 = next(
        (e for e in events if e["message"] == "leftover_without_reason" and e["account_id"] == 12),
        None,
    )
    assert leftover_warn_12 is None
