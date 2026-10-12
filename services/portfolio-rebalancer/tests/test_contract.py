import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from portfolio_rebalancer.accounts import Accounts, Envelope
from portfolio_rebalancer.orders import Orders
from portfolio_rebalancer.snapshot import Snapshot

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "docs/contracts").is_dir())
EXAMPLES = json.loads((ROOT / "docs/contracts/rebalancer-sqs-v2.json").read_text())


@pytest.mark.parametrize("message", EXAMPLES)
def test_shared_examples_match_the_message_envelope(message):
    accepted = Envelope.model_validate(message)

    assert accepted.schema_version == 1
    assert accepted.event_id == message["eventId"]
    if accepted.type == "account.snapshot":
        assert Snapshot.model_validate(accepted.payload).model_dump() == accepted.payload


@pytest.mark.parametrize("message", [m for m in EXAMPLES if m["type"] == "account.snapshot"])
def test_shared_account_examples_are_accepted_and_persisted(message, snapshot_state):
    from ktb_core.logging import get_logger
    from ktb_core.queue import Message

    body = {**message, "occurredAt": datetime.now(UTC).isoformat()}
    messages = [Message("source", json.dumps(body), "receipt")]
    deleted = []

    class Queue:
        def receive(self, **kwargs):
            batch = messages[:]
            messages.clear()
            return batch

        def delete(self, receipt):
            assert snapshot_state.load().event_id == body["eventId"]
            deleted.append(receipt)

    accounts = Accounts(
        Queue(), None, get_logger("contract"), "run", budget=5, state=snapshot_state
    )
    assert accounts.users() == Snapshot.model_validate(body["payload"]).users
    assert deleted == ["receipt"]


def test_published_order_payloads_match_the_shared_backend_examples():
    sent = []

    class Queue:
        def send(self, body, *, group, dedup):
            message = json.loads(body)
            assert group == "11"
            assert dedup == message["eventId"]
            sent.append(message)
            return "sqs-id"

    orders = Orders(Queue(), "ai-run-1")
    place = next(m for m in EXAMPLES if m["type"] == "order.place")["payload"]
    body = {
        k: v for k, v in place.items() if k not in {"user_id", "account_id", "replaces_order_ids"}
    }
    orders.place(1, 11, body, [77])

    for published in sent:
        expected = next(m for m in EXAMPLES if m["type"] == published["type"])
        assert published["payload"] == expected["payload"]
        assert published["correlationId"] == expected["correlationId"]
