import pytest
from portfolio_rebalancer.snapshot import Snapshot
from pydantic import ValidationError

EXAMPLE = {
    "users": [
        {
            "user_id": 1,
            "accounts": [
                {
                    "account_id": 11,
                    "account_name": "AI 계좌",
                    "is_active": True,
                    "cash_balance": 1000000,
                    "stocks": [{"stock_code": "005930", "total_cost": 1000000.00, "quantity": 10}],
                    "pending_orders": [
                        {
                            "order_id": 3,
                            "stock_code": "005930",
                            "order_side": "sell",
                            "order_status": "pending",
                            "order_type": "limit",
                            "limit_price": 250000,
                            "quantity": 2,
                        }
                    ],
                }
            ],
        },
        {"user_id": 2, "accounts": []},
    ]
}


def test_the_backend_example_parses():
    snapshot = Snapshot.model_validate(EXAMPLE)

    account = snapshot.users[0].accounts[0]
    assert account.cash_balance == 1000000
    assert account.stocks[0].quantity == 10
    assert account.pending_orders[0].order_side == "sell"
    assert account.pending_orders[0].quantity == 2
    assert snapshot.users[1].accounts == []


def test_a_market_order_has_no_limit_price():
    order = {**EXAMPLE["users"][0]["accounts"][0]["pending_orders"][0]}
    order.update(order_type="market", order_side="buy", limit_price=None)
    payload = {
        "users": [
            {
                "user_id": 1,
                "accounts": [{**EXAMPLE["users"][0]["accounts"][0], "pending_orders": [order]}],
            }
        ]
    }

    pending = Snapshot.model_validate(payload).users[0].accounts[0].pending_orders[0]

    assert pending.limit_price is None


@pytest.mark.parametrize("risk_level", [1, 2, 3, 4, 5])
def test_polling_retains_investment_profile_and_free_text(risk_level):
    profile = {"risk_level": risk_level, "strategy": "배당을 우선하되 반도체는 제외"}
    user = {**EXAMPLE["users"][0], "investment_profile": profile}

    snapshot = Snapshot.model_validate({"users": [user]})

    assert snapshot.users[0].investment_profile.model_dump() == profile
    assert Snapshot.model_validate(EXAMPLE).users[0].investment_profile is None


@pytest.mark.parametrize("risk_level", [0, 6, True, 1.5, "3"])
def test_polling_rejects_invalid_risk_levels(risk_level):
    user = {**EXAMPLE["users"][0], "investment_profile": {"risk_level": risk_level}}

    with pytest.raises(ValidationError):
        Snapshot.model_validate({"users": [user]})
