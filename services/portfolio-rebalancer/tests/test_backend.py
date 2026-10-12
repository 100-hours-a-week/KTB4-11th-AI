import json as json_module
from collections import Counter
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import httpx
import jwt
import pytest
from ktb_core.logging import get_logger
from portfolio_rebalancer.__main__ import _cancel_all
from portfolio_rebalancer.accounts import Accounts
from portfolio_rebalancer.backend import Backend, order_request_body
from portfolio_rebalancer.portfolio import Explanation
from portfolio_rebalancer.rebalance import Order, Pricing
from portfolio_rebalancer.snapshot import Account

PRICING = Pricing(
    order_type="limit",
    limit_price=74_100,
    trigger=None,
    price=78_000,
    sma=78_000,
    sigma=1_950,
    alpha=1_950,
    lower_bound=74_100,
    upper_bound=81_900,
)
ORDER = Order(
    stock_code="005930",
    stock_name="삼성전자",
    side="buy",
    quantity=3,
    explanation=Explanation(
        id=UUID("550e8400-e29b-41d4-a716-446655440000"),
        reason="사요",
        reasonings=[{"label": "HBM", "body": "늘었어요."}],
    ),
    pricing=PRICING,
    holding_weight_after_trade_percent=19.5,
    holding_weight_limit_percent=24.0,
)


def test_a_limit_buy_carries_its_price_quantity_and_explanation():
    body = order_request_body(ORDER)

    assert (body["stock_code"], body["stock_name"]) == ("005930", "삼성전자")
    assert (body["order_side"], body["order_type"]) == ("buy", "limit")
    assert (body["limit_price"], body["quantity"]) == (74_100, 3)
    assert (body["is_upper_triggered"], body["is_lower_triggered"]) == (False, False)
    assert body["reason"] == "사요"
    assert body["reason_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert body["reasoning"] == [{"label": "HBM", "body": "늘었어요."}]


@pytest.mark.parametrize(
    ("quantity", "after", "limit"), [(3, 19.5, 24.0), (1, 0.0, 0.0), (10, 41.2, 48.0)]
)
def test_a_sell_carries_its_weights(quantity, after, limit):
    sell = ORDER.model_copy(
        update={
            "side": "sell",
            "quantity": quantity,
            "holding_weight_after_trade_percent": after,
            "holding_weight_limit_percent": limit,
        }
    )

    body = order_request_body(sell)

    assert body["order_side"] == "sell"
    assert body["quantity"] == quantity
    assert body["holding_weight_after_trade_percent"] == after
    assert body["holding_weight_limit_percent"] == limit


@pytest.mark.parametrize(("side", "upper", "lower"), [("buy", True, False), ("sell", False, True)])
def test_a_market_order_flags_the_bound_its_side_escapes_through(side, upper, lower):
    market = PRICING.model_copy(
        update={"order_type": "market", "limit_price": None, "trigger": "last_run"}
    )

    body = order_request_body(ORDER.model_copy(update={"side": side, "pricing": market}))

    assert (body["order_type"], body["limit_price"]) == ("market", None)
    assert (body["is_upper_triggered"], body["is_lower_triggered"]) == (upper, lower)


@pytest.mark.parametrize("invalid_csrf", [False, True])
def test_cancel_uses_existing_http_auth_and_status_contract(invalid_csrf, snapshot_state):
    requests = []
    csrf_issued = 0
    patch_count = 0

    def respond(request):
        nonlocal csrf_issued, patch_count
        assert not snapshot_state.load().usable
        requests.append(request)
        if request.url.path == "/api/v1/auth/csrf":
            csrf_issued += 1
            return httpx.Response(
                200,
                json={"token": f"masked-{csrf_issued}", "header_name": "X-XSRF-TOKEN"},
                headers={"set-cookie": f"XSRF-TOKEN=raw-{csrf_issued}; Path=/; Secure"},
            )
        assert request.method == "PATCH"
        assert request.url.path == "/api/v1/accounts/11/orders/77"
        patch_count += 1
        if invalid_csrf and patch_count == 1:
            return httpx.Response(403, json={"code": "INVALID_CSRF_TOKEN"})
        return httpx.Response(204)

    account = Account.model_validate(
        {
            "account_id": 11,
            "is_active": True,
            "cash_balance": 100,
            "stocks": [],
            "pending_orders": [
                {
                    "order_id": 77,
                    "stock_code": "005930",
                    "order_side": "buy",
                    "order_type": "limit",
                    "order_status": "pending",
                    "limit_price": 100,
                    "quantity": 1,
                }
            ],
        }
    )
    messages = [
        [
            SimpleNamespace(
                id="snapshot",
                receipt="receipt",
                body=json_module.dumps(
                    {
                        "eventId": "snapshot",
                        "type": "account.snapshot",
                        "occurredAt": datetime.now(UTC).isoformat(),
                        "payload": {"users": [{"user_id": 7, "accounts": [account.model_dump()]}]},
                    }
                ),
            )
        ]
    ]
    queue = SimpleNamespace(
        receive=lambda **kwargs: messages.pop(0) if messages else [], delete=lambda receipt: None
    )
    accounts = Accounts(queue, None, get_logger("test"), "run", budget=5, state=snapshot_state)
    accounts.users()
    counts = Counter()
    accounts.before_publish()
    with httpx.Client(base_url="http://backend", transport=httpx.MockTransport(respond)) as client:
        backend = Backend(client, "s" * 32, "river-be")
        assert _cancel_all(backend, get_logger("test"), 7, account, counts)

    assert counts["cancelled"] == 1
    assert csrf_issued == (2 if invalid_csrf else 1)
    patch = requests[-1]
    assert patch.content == b'{"status":"cancelled"}'
    cookies = dict(part.strip().split("=", 1) for part in patch.headers["cookie"].split(";"))
    claims = jwt.decode(cookies["access_token"], "s" * 32, algorithms=["HS256"], issuer="river-be")
    assert (claims["sub"], claims["actor"]) == ("7", "AI")
    assert patch.headers["x-xsrf-token"] == f"masked-{csrf_issued}"
    assert all("users/ai-server" not in r.url.path for r in requests)
