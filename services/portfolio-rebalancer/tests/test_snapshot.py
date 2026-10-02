from portfolio_rebalancer.snapshot import Snapshot

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
                            "current_stock_price": 200000,
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
    assert account.pending_orders[0].current_stock_price == 200000
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
