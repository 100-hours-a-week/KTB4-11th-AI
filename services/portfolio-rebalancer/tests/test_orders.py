import json

import pytest
from portfolio_rebalancer.orders import CANCEL_TYPE, PLACE_TYPE, Orders, PublishError

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


def test_a_cancel_carries_the_order_it_cancels_and_the_run_as_correlation():
    queue = FakeQueue()

    published = Orders(queue, RUN).cancel(7, 11, 77)

    sent = queue.sent[0]
    assert sent["body"]["type"] == CANCEL_TYPE
    assert sent["body"]["correlationId"] == RUN
    assert sent["body"]["payload"] == {
        "user_id": 7,
        "account_id": 11,
        "order_id": 77,
        "status": "cancelled",
    }
    assert published["sqs_message_id"] == "m1"
    assert published["event_id"] == sent["body"]["eventId"]


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

    Orders(queue, RUN).cancel(7, 11, 77)

    assert queue.sent[0]["dedup"] == queue.sent[0]["body"]["eventId"]


def test_the_group_is_the_account_so_a_cancel_orders_before_its_replacement():
    queue = FakeQueue()
    orders = Orders(queue, RUN)

    orders.cancel(7, 11, 77)
    orders.place(7, 11, BODY, [77])

    assert [sent["group"] for sent in queue.sent] == ["11", "11"]
    assert [sent["body"]["type"] for sent in queue.sent] == [CANCEL_TYPE, PLACE_TYPE]


def test_two_accounts_land_in_different_groups():
    queue = FakeQueue()
    orders = Orders(queue, RUN)

    orders.place(7, 11, BODY, [])
    orders.place(7, 12, BODY, [])

    assert [sent["group"] for sent in queue.sent] == ["11", "12"]


def test_every_message_gets_its_own_event_id():
    queue = FakeQueue()
    orders = Orders(queue, RUN)

    orders.cancel(7, 11, 77)
    orders.cancel(7, 11, 78)

    assert queue.sent[0]["dedup"] != queue.sent[1]["dedup"]


def test_a_korean_reason_is_not_escaped():
    queue = FakeQueue()

    Orders(queue, RUN).place(7, 11, {**BODY, "reason": "사요"}, [])

    assert queue.sent[0]["body"]["payload"]["reason"] == "사요"


@pytest.mark.parametrize("call", ["cancel", "place"])
def test_a_send_failure_becomes_a_publish_error(call):
    orders = Orders(FakeQueue(boom=True), RUN)

    with pytest.raises(PublishError, match="RuntimeError: no network"):
        orders.cancel(7, 11, 77) if call == "cancel" else orders.place(7, 11, BODY, [])


def test_a_failed_publish_keeps_the_event_identity_for_logging():
    with pytest.raises(PublishError) as error:
        Orders(FakeQueue(boom=True), RUN).cancel(7, 11, 77)

    assert error.value.fields["event_id"]
    assert error.value.fields["correlation_id"] == RUN
    assert error.value.fields["sqs_message_id"] is None


def test_a_published_order_keeps_the_correlation_identity_for_logging():
    published = Orders(FakeQueue(), RUN).place(7, 11, BODY, [])

    assert published["correlation_id"] == RUN


def test_cancel_carries_the_backend_cancelled_status_contract():
    queue = FakeQueue()
    Orders(queue, RUN).cancel(7, 11, 77)

    assert queue.sent[0]["body"]["payload"]["status"] == "cancelled"
