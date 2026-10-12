import json

import pytest
from portfolio_rebalancer.orders import PLACE_TYPE, Orders, PublishError

RUN = "run-1"
BODY = {"stock_code": "005930", "order_side": "buy", "order_type": "limit", "quantity": 3}


class FakeQueue:
    def __init__(self, boom=False):
        self.sent = []
        self._boom = boom

    def send(self, body, *, group, dedup):
        if self._boom:
            raise RuntimeError("no network")
        self.sent.append({"body": json.loads(body), "group": group, "dedup": dedup})
        return f"m{len(self.sent)}"

    def receive(self, *, limit=10, wait=20):
        raise AssertionError("the order queue is publish-only")

    def delete(self, receipt):
        raise AssertionError("the order queue is publish-only")


def test_a_place_carries_the_request_body_and_what_it_replaces():
    queue = FakeQueue()

    Orders(queue, RUN).place(7, 11, BODY, [77, 78])

    payload = queue.sent[0]["body"]["payload"]
    assert queue.sent[0]["body"]["type"] == PLACE_TYPE
    assert payload["replaces_order_ids"] == [77, 78]
    assert payload["stock_code"] == "005930"
    assert (payload["user_id"], payload["account_id"]) == (7, 11)


def test_the_dedup_id_is_the_event_id():
    queue = FakeQueue()

    Orders(queue, RUN).place(7, 11, BODY, [])

    assert queue.sent[0]["dedup"] == queue.sent[0]["body"]["eventId"]


def test_orders_for_one_account_share_a_fifo_group():
    queue = FakeQueue()
    orders = Orders(queue, RUN)
    orders.place(7, 11, BODY, [])
    orders.place(7, 11, BODY, [77])

    assert [sent["group"] for sent in queue.sent] == ["11", "11"]
    assert [sent["body"]["type"] for sent in queue.sent] == [PLACE_TYPE, PLACE_TYPE]


def test_two_accounts_land_in_different_groups():
    queue = FakeQueue()
    orders = Orders(queue, RUN)

    orders.place(7, 11, BODY, [])
    orders.place(7, 12, BODY, [])

    assert [sent["group"] for sent in queue.sent] == ["11", "12"]


def test_every_message_gets_its_own_event_id():
    queue = FakeQueue()
    orders = Orders(queue, RUN)

    orders.place(7, 11, BODY, [])
    orders.place(7, 11, BODY, [77])

    assert queue.sent[0]["dedup"] != queue.sent[1]["dedup"]


def test_a_korean_reason_is_not_escaped():
    queue = FakeQueue()

    Orders(queue, RUN).place(7, 11, {**BODY, "reason": "사요"}, [])

    assert queue.sent[0]["body"]["payload"]["reason"] == "사요"


def test_a_send_failure_becomes_a_publish_error():
    orders = Orders(FakeQueue(boom=True), RUN)

    with pytest.raises(PublishError, match="RuntimeError: no network"):
        orders.place(7, 11, BODY, [])


def test_a_failed_publish_keeps_the_event_identity_for_logging():
    with pytest.raises(PublishError) as error:
        Orders(FakeQueue(boom=True), RUN).place(7, 11, BODY, [])

    assert error.value.fields["event_id"]
    assert error.value.fields["correlation_id"] == RUN
    assert error.value.fields["sqs_message_id"] is None


def test_a_published_order_keeps_the_correlation_identity_for_logging():
    published = Orders(FakeQueue(), RUN).place(7, 11, BODY, [])

    assert published["correlation_id"] == RUN


def test_cancel_then_replace_share_the_account_fifo_group():
    queue = FakeQueue()
    orders = Orders(queue, RUN)
    orders.cancel(7, 11, 77)
    orders.place(7, 11, BODY, [77])
    assert [m["body"]["type"] for m in queue.sent] == ["order.cancel", "order.place"]
    assert [m["group"] for m in queue.sent] == ["11", "11"]
    assert queue.sent[0]["body"]["payload"] == {
        "user_id": 7,
        "account_id": 11,
        "order_id": 77,
        "status": "cancelled",
    }
    assert all(m["dedup"] == m["body"]["eventId"] for m in queue.sent)
    assert queue.sent[0]["dedup"] != queue.sent[1]["dedup"]


def test_cancel_publish_failure_keeps_its_event_identity():
    with pytest.raises(PublishError) as error:
        Orders(FakeQueue(boom=True), RUN).cancel(7, 11, 77)
    assert error.value.fields["event_id"]
    assert error.value.fields["correlation_id"] == RUN
