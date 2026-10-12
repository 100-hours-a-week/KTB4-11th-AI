import json
from pathlib import Path

from portfolio_rebalancer.orders import Orders

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "docs/contracts").is_dir())
EXAMPLES = json.loads((ROOT / "docs/contracts/rebalancer-sqs-v2.json").read_text())


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


def test_published_http_failure_matches_the_shared_backend_example():
    sent = []

    class Queue:
        def send(self, body, *, group, dedup):
            message = json.loads(body)
            assert group == "ai-server"
            assert dedup == message["eventId"]
            sent.append(message)
            return "failure-id"

    expected = next(m for m in EXAMPLES if m["type"] == "account.snapshot.failed")
    Orders(Queue(), "ai-run-1").report_failure(
        expected["payload"]["reason"], expected["payload"]["detail"]
    )
    assert sent[0]["payload"] == expected["payload"]
    assert sent[0]["correlationId"] == expected["correlationId"]
