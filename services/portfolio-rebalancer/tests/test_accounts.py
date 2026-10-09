import json

import pytest
from ktb_core.logging import get_logger
from portfolio_rebalancer.accounts import FAILURE_TYPE, SNAPSHOT_TYPE, Accounts

LOG = get_logger("test")


def snapshot(cash=1_000_000, pending=(), user_id=1):
    return {
        "users": [
            {
                "user_id": user_id,
                "accounts": [
                    {
                        "account_id": 11,
                        "is_active": True,
                        "cash_balance": cash,
                        "stocks": [{"stock_code": "005930", "quantity": 10, "total_cost": 1000.0}],
                        "pending_orders": [
                            {
                                "order_id": order_id,
                                "stock_code": "005930",
                                "order_side": "buy",
                                "order_type": "limit",
                                "order_status": "pending",
                                "limit_price": 95_000,
                                "quantity": 2,
                            }
                            for order_id in pending
                        ],
                    }
                ],
            }
        ]
    }


def envelope(payload, event_id="e1", type=SNAPSHOT_TYPE):
    return json.dumps(
        {
            "eventId": event_id,
            "correlationId": "c1",
            "occurredAt": "2026-10-09T00:00:00Z",
            "type": type,
            "payload": payload,
        }
    )


class Msg:
    def __init__(self, body, id="m1", receipt="r1"):
        self.id = id
        self.body = body
        self.receipt = receipt


class FakeQueue:
    def __init__(self, *batches):
        self._batches = list(batches)
        self.deleted = []

    def send(self, body, *, group, dedup):
        raise AssertionError("the account queue is consume-only")

    def receive(self, *, limit=10, wait=20):
        return self._batches.pop(0) if self._batches else []

    def delete(self, receipt):
        self.deleted.append(receipt)


class FailureQueue:
    def __init__(self, boom=False):
        self.sent = []
        self._boom = boom

    def send(self, body, *, group, dedup):
        if self._boom:
            raise RuntimeError("no network")
        self.sent.append({"body": json.loads(body), "group": group, "dedup": dedup})
        return f"f{len(self.sent)}"

    def receive(self, *, limit=10, wait=20):
        raise AssertionError("the failure queue is publish-only")

    def delete(self, receipt):
        raise AssertionError("the failure queue is publish-only")


def _accounts(*batches, budget=5, failures=None):
    queue = FakeQueue(*batches)
    failures = failures if failures is not None else FailureQueue()
    return Accounts(queue, failures, LOG, "run-1", budget=budget), queue, failures


def test_a_snapshot_becomes_the_users_the_tick_trades():
    accounts, queue, failures = _accounts([Msg(envelope(snapshot(pending=[77])))])

    users = accounts.users()

    assert [u.user_id for u in users] == [1]
    account = users[0].accounts[0]
    assert (account.cash_balance, account.stocks[0].quantity) == (1_000_000, 10)
    assert [o.order_id for o in account.pending_orders] == [77]
    assert queue.deleted == ["r1"]


def test_the_latest_snapshot_in_the_queue_wins():
    accounts, _, failures = _accounts(
        [
            Msg(envelope(snapshot(cash=100), event_id="old"), id="m1", receipt="r1"),
            Msg(envelope(snapshot(cash=900), event_id="new"), id="m2", receipt="r2"),
        ]
    )

    assert accounts.users()[0].accounts[0].cash_balance == 900


def test_a_redelivered_event_is_acknowledged_without_being_applied_again():
    accounts, queue, failures = _accounts(
        [Msg(envelope(snapshot(cash=500)))],
        [Msg(envelope(snapshot(cash=900), event_id="later"), id="m2", receipt="r2")],
        [Msg(envelope(snapshot(cash=500)), id="m3", receipt="r3")],
    )

    users = accounts.users()

    assert users[0].accounts[0].cash_balance == 900
    assert queue.deleted == ["r1", "r2", "r3"]


@pytest.mark.parametrize("body", ["not json", '{"eventId": "e1"}', '{"type": "x"}'])
def test_a_malformed_message_is_reported_and_then_deleted(body):
    accounts, queue, failures = _accounts([Msg(body)])

    assert accounts.users() == []
    assert accounts.rejected == 1
    sent = failures.sent[0]["body"]
    assert sent["type"] == FAILURE_TYPE
    assert sent["payload"]["reason"] == "malformed_envelope"
    assert sent["correlationId"] == "run-1"
    assert queue.deleted == ["r1"]


def test_an_empty_snapshot_is_reported_and_leaves_no_users():
    accounts, queue, failures = _accounts([Msg(envelope({"users": []}))])

    assert accounts.users() == []
    assert failures.sent[0]["body"]["payload"]["reason"] == "empty_snapshot"
    assert failures.sent[0]["body"]["payload"]["rejected_event_id"] == "e1"
    assert queue.deleted == ["r1"]


def test_an_empty_snapshot_does_not_replace_a_good_one():
    accounts, _, failures = _accounts(
        [Msg(envelope(snapshot(cash=900)))],
        [Msg(envelope({"users": []}, event_id="e2"), id="m2", receipt="r2")],
    )

    assert accounts.users()[0].accounts[0].cash_balance == 900
    assert accounts.rejected == 1


def test_a_rejection_that_cannot_be_published_leaves_the_message():
    accounts, queue, _ = _accounts([Msg(envelope({"users": []}))], failures=FailureQueue(boom=True))

    assert accounts.users() == []
    assert queue.deleted == []


def test_an_unknown_event_type_is_left_alone_without_a_rejection():
    accounts, queue, failures = _accounts([Msg(envelope(snapshot(), type="account.closed"))])

    assert accounts.users() == []
    assert queue.deleted == []
    assert failures.sent == []


def test_a_payload_that_is_not_a_snapshot_is_reported_and_deleted():
    accounts, queue, failures = _accounts([Msg(envelope({"users": "nope"}))])

    assert accounts.users() == []
    assert failures.sent[0]["body"]["payload"]["reason"] == "malformed_payload"
    assert queue.deleted == ["r1"]


def test_an_empty_queue_yields_no_users_and_reports_nothing():
    accounts, queue, failures = _accounts()

    assert accounts.users() == []
    assert queue.deleted == []
    assert failures.sent == []


def test_a_second_read_keeps_the_snapshot_when_nothing_new_arrived():
    accounts, _, failures = _accounts([Msg(envelope(snapshot(cash=777)))])

    first = accounts.users()
    second = accounts.users()

    assert first == second
    assert second[0].accounts[0].cash_balance == 777


def test_the_drain_stops_at_its_budget():
    accounts, queue, failures = _accounts([Msg(envelope(snapshot()))], budget=0)

    assert accounts.users() == []
    assert queue.deleted == []


def test_forgetting_a_cancelled_order_drops_it_and_leaves_cash_and_holdings():
    accounts, _, failures = _accounts([Msg(envelope(snapshot(cash=777, pending=[77, 78])))])
    accounts.users()

    accounts.forget({77})

    account = accounts.users()[0].accounts[0]
    assert [o.order_id for o in account.pending_orders] == [78]
    assert account.cash_balance == 777
    assert account.stocks[0].quantity == 10


def test_forgetting_with_no_snapshot_is_a_no_op():
    accounts, _, failures = _accounts()

    accounts.forget({77})

    assert accounts.users() == []
