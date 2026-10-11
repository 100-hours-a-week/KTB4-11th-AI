import json
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from ktb_core.logging import get_logger, setup_logging
from portfolio_rebalancer.accounts import FAILURE_TYPE, SNAPSHOT_TYPE, Accounts, AccountsError

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


def envelope(payload, event_id="e1", type=SNAPSHOT_TYPE, occurred_at=None):
    return json.dumps(
        {
            "eventId": event_id,
            "correlationId": "c1",
            "occurredAt": occurred_at or datetime.now(UTC).isoformat(),
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


def _accounts(*batches, budget=5, failures=None, state=None, max_age=900):
    from portfolio_rebalancer.snapshot_store import SnapshotStore, metadata, states

    if state is None:
        engine = sa.create_engine("sqlite://")
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(states.insert().values(id=1, usable=False))
        state = SnapshotStore(engine)
    setup_logging("INFO", service_name="portfolio-rebalancer")
    queue = FakeQueue(*batches)
    failures = failures if failures is not None else FailureQueue()
    return (
        Accounts(queue, failures, LOG, "run-1", budget=budget, state=state, max_age=max_age),
        queue,
        failures,
    )


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
def test_a_malformed_message_is_reported_and_left_for_retry(body):
    accounts, queue, failures = _accounts([Msg(body)])

    with pytest.raises(AccountsError, match="snapshot_rejected"):
        accounts.users()
    assert accounts.rejected == 1
    sent = failures.sent[0]["body"]
    assert sent["type"] == FAILURE_TYPE
    assert sent["payload"]["reason"] == "malformed_envelope"
    assert sent["correlationId"] == "run-1"
    assert queue.deleted == []


def test_an_empty_snapshot_replaces_a_previous_nonempty_snapshot():
    accounts, queue, failures = _accounts(
        [Msg(envelope(snapshot(cash=900)))],
        [Msg(envelope({"users": []}, event_id="e2"), id="m2", receipt="r2")],
    )

    assert accounts.users() == []
    assert accounts.rejected == 0
    assert queue.deleted == ["r1", "r2"]
    assert failures.sent == []


def test_a_rejection_that_cannot_be_published_leaves_the_message():
    accounts, queue, _ = _accounts(
        [Msg(envelope({"users": "invalid"}))], failures=FailureQueue(boom=True)
    )

    with pytest.raises(AccountsError, match="snapshot_rejection_unreported"):
        accounts.users()
    assert queue.deleted == []


def test_an_unknown_event_type_is_reported_and_left_for_retry():
    accounts, queue, failures = _accounts([Msg(envelope(snapshot(), type="account.closed"))])

    with pytest.raises(AccountsError, match="snapshot_rejected"):
        accounts.users()
    assert queue.deleted == []
    assert failures.sent[0]["body"]["type"] == "account.snapshot.rejected"
    assert accounts.rejected == 1


def test_a_payload_that_is_not_a_snapshot_is_reported_and_left_for_retry():
    accounts, queue, failures = _accounts([Msg(envelope({"users": "nope"}))])

    with pytest.raises(AccountsError, match="snapshot_rejected"):
        accounts.users()
    assert failures.sent[0]["body"]["payload"]["reason"] == "malformed_payload"
    assert queue.deleted == []


def test_an_empty_queue_reports_a_failed_snapshot_fetch():
    accounts, queue, failures = _accounts()

    with pytest.raises(AccountsError, match="snapshot_missing"):
        accounts.users()
    assert queue.deleted == []
    assert failures.sent[0]["body"]["payload"]["reason"] == "snapshot_missing"


def test_a_second_read_keeps_the_snapshot_when_nothing_new_arrived():
    accounts, _, failures = _accounts([Msg(envelope(snapshot(cash=777)))])

    first = accounts.users()
    second = accounts.users()

    assert first == second
    assert second[0].accounts[0].cash_balance == 777


def test_the_drain_stops_at_its_budget():
    accounts, queue, failures = _accounts([Msg(envelope(snapshot()))], budget=0)

    with pytest.raises(AccountsError, match="drain_budget_exceeded"):
        accounts.users()
    assert queue.deleted == []
    assert failures.sent[0]["body"]["payload"]["reason"] == "drain_budget_exceeded"


def test_a_second_read_without_updates_keeps_pending_orders_cash_and_holdings():
    accounts, _, failures = _accounts([Msg(envelope(snapshot(cash=777, pending=[77, 78])))])
    accounts.users()

    account = accounts.users()[0].accounts[0]
    assert [o.order_id for o in account.pending_orders] == [77, 78]
    assert account.cash_balance == 777
    assert account.stocks[0].quantity == 10


def test_a_receive_failure_is_reported_and_stops_the_tick(monkeypatch, capsys):
    setup_logging("INFO", service_name="portfolio-rebalancer")
    accounts, queue, failures = _accounts()

    def fail(**kwargs):
        raise RuntimeError("SQS unavailable")

    monkeypatch.setattr(queue, "receive", fail)
    with pytest.raises(RuntimeError, match="receive_failed"):
        accounts.users()

    failure = failures.sent[0]["body"]
    assert failure["type"] == "account.snapshot.failed"
    assert failure["payload"]["reason"] == "receive_failed"
    assert failure["correlationId"] == "run-1"
    assert queue.deleted == []
    event = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert event["reason"] == "receive_failed"
    assert event["failure_sqs_message_id"] == "f1"
    assert event["failure_event_id"] == failure["eventId"]


def test_a_delete_failure_is_reported_with_the_source_event(monkeypatch):
    accounts, queue, failures = _accounts([Msg(envelope(snapshot()))])

    def fail(receipt):
        raise RuntimeError("delete failed")

    monkeypatch.setattr(queue, "delete", fail)
    with pytest.raises(RuntimeError, match="acknowledgement_failed"):
        accounts.users()

    failure = failures.sent[0]["body"]
    assert failure["type"] == "account.snapshot.failed"
    assert failure["payload"]["reason"] == "acknowledgement_failed"
    assert failure["payload"]["rejected_event_id"] == "e1"


def test_no_snapshot_is_reported_instead_of_succeeding_with_no_accounts():
    accounts, queue, failures = _accounts()

    with pytest.raises(RuntimeError, match="snapshot_missing"):
        accounts.users()

    assert failures.sent[0]["body"]["payload"]["reason"] == "snapshot_missing"
    assert queue.deleted == []


def test_a_new_tick_recovers_an_unused_snapshot():
    accounts, queue, failures = _accounts([Msg(envelope(snapshot()))])
    assert accounts.users()
    next_tick = Accounts(queue, failures, LOG, "run-2", budget=5, state=accounts._state)

    assert next_tick.users() == accounts.users()
    assert failures.sent == []


def test_validation_failures_do_not_echo_the_snapshot_input(capsys):
    setup_logging("INFO", service_name="portfolio-rebalancer")
    accounts, _, failures = _accounts(
        [Msg(envelope({"users": [{"password": "private-input-do-not-log"}]}))]
    )
    try:
        accounts.users()
    except RuntimeError:
        pass

    assert "private-input-do-not-log" not in json.dumps(failures.sent)
    assert "private-input-do-not-log" not in capsys.readouterr().out


@pytest.mark.parametrize("boom", [False, True])
def test_failure_notification_logs_identify_the_publish_and_preserve_source(boom, capsys):
    setup_logging("INFO", service_name="portfolio-rebalancer")
    accounts, queue, failures = _accounts(
        [Msg(envelope({"users": "invalid"}))], failures=FailureQueue(boom=boom)
    )

    with pytest.raises(AccountsError):
        accounts.users()

    event = next(
        e
        for e in (json.loads(line) for line in capsys.readouterr().out.splitlines())
        if e["message"] in {"snapshot_rejected", "snapshot_rejection_unreported"}
    )
    assert (event["queue_role"], event["direction"]) == ("failures", "publish")
    assert (
        event["source_sqs_message_id"],
        event["source_event_id"],
        event["source_correlation_id"],
    ) == ("m1", "e1", "c1")
    assert event["event_id"] == event["failure_event_id"]
    assert event["correlation_id"] == "run-1"
    assert event["sqs_message_id"] == (None if boom else "f1")
    assert event["failure_result"] == ("failed" if boom else "published")
    assert queue.deleted == []


@pytest.mark.parametrize(
    "body", [envelope({"users": "invalid"}), envelope(snapshot(), type="account.closed")]
)
def test_a_failure_stops_processing_later_messages_in_the_same_fifo_batch(body):
    accounts, queue, failures = _accounts(
        [
            Msg(body),
            Msg(envelope(snapshot(), event_id="e2"), id="m2", receipt="r2"),
        ]
    )

    with pytest.raises(AccountsError, match="snapshot_rejected"):
        accounts.users()

    assert queue.deleted == []
    assert len(failures.sent) == 1


def test_accounts_use_twenty_second_long_polling(monkeypatch):
    accounts, queue, _ = _accounts([Msg(envelope(snapshot()))])
    waits = []
    receive = queue.receive

    def record(*, limit=10, wait=20):
        waits.append(wait)
        return receive(limit=limit, wait=wait)

    monkeypatch.setattr(queue, "receive", record)
    accounts.users()
    assert waits == [20, 20]


def test_a_long_poll_cannot_apply_messages_after_the_drain_deadline(monkeypatch):
    ticks = iter([0.0, 0.0, 61.0])
    monkeypatch.setattr("portfolio_rebalancer.accounts.monotonic", lambda: next(ticks))
    accounts, queue, failures = _accounts([Msg(envelope(snapshot()))], budget=60)

    with pytest.raises(AccountsError, match="drain_budget_exceeded"):
        accounts.users()

    assert queue.deleted == []
    assert failures.sent[0]["body"]["payload"]["reason"] == "drain_budget_exceeded"


def test_empty_users_is_a_valid_acknowledged_snapshot():
    accounts, queue, failures = _accounts([Msg(envelope({"users": []}))])

    assert accounts.users() == []
    assert queue.deleted == ["r1"]
    assert failures.sent == []


def test_claimed_snapshot_is_not_restored_after_restart():
    first, _, _ = _accounts([Msg(envelope(snapshot()))])
    first.users()
    first.before_publish()
    second, queue, failures = _accounts(state=first._state)

    with pytest.raises(AccountsError, match="snapshot_missing"):
        second.users()
    assert queue.deleted == []
    assert failures.sent[-1]["body"]["payload"]["reason"] == "snapshot_missing"


def test_duplicate_snapshot_does_not_rearm_claimed_state_after_restart():
    body = envelope(snapshot())
    first, _, _ = _accounts([Msg(body)])
    first.users()
    first.before_publish()
    second, queue, _ = _accounts([Msg(body, receipt="r2")], state=first._state)

    with pytest.raises(AccountsError, match="snapshot_missing"):
        second.users()
    assert queue.deleted == ["r2"]


def test_storage_failure_does_not_acknowledge_snapshot(monkeypatch):
    accounts, queue, failures = _accounts([Msg(envelope(snapshot()))])

    def fail(*args, **kwargs):
        raise RuntimeError("DB unavailable")

    monkeypatch.setattr(accounts._state, "accept", fail)
    with pytest.raises(AccountsError, match="snapshot_store_failed"):
        accounts.users()
    assert queue.deleted == []
    assert failures.sent[-1]["body"]["payload"]["reason"] == "snapshot_store_failed"


def test_recovery_failure_is_reported(monkeypatch):
    accounts, queue, failures = _accounts()

    def fail():
        raise RuntimeError("DB unavailable")

    monkeypatch.setattr(accounts._state, "load", fail)
    with pytest.raises(AccountsError, match="snapshot_restore_failed"):
        accounts.users()
    assert failures.sent[-1]["body"]["payload"]["reason"] == "snapshot_restore_failed"


@pytest.mark.parametrize("offset", [-901, 31])
def test_out_of_window_snapshot_is_rejected_without_ack(offset):
    at = (datetime.now(UTC) + timedelta(seconds=offset)).isoformat()
    accounts, queue, failures = _accounts([Msg(envelope(snapshot(), occurred_at=at))])

    with pytest.raises(AccountsError, match="snapshot_time_invalid"):
        accounts.users()
    assert queue.deleted == []
    assert failures.sent[0]["body"]["payload"]["reason"] == "snapshot_time_invalid"


@pytest.mark.parametrize("at", [None, "2026-10-11T01:00:00", "invalid"])
def test_snapshot_requires_a_timezone_aware_timestamp(at):
    data = json.loads(envelope(snapshot()))
    data["occurredAt"] = at
    accounts, queue, _ = _accounts([Msg(json.dumps(data))])

    with pytest.raises(AccountsError, match="malformed_envelope"):
        accounts.users()
    assert queue.deleted == []


def test_expired_recovery_reports_failure():
    first, _, _ = _accounts([Msg(envelope(snapshot()))])
    first.users()
    second, _, failures = _accounts(state=first._state, max_age=0)

    with pytest.raises(AccountsError, match="snapshot_expired"):
        second.users()
    assert failures.sent[-1]["body"]["payload"]["reason"] == "snapshot_expired"


def test_a_late_snapshot_cannot_roll_back_account_state():
    now = datetime.now(UTC)
    newer = envelope(snapshot(cash=900), "new", occurred_at=now.isoformat())
    older = envelope(
        snapshot(cash=100), "old", occurred_at=(now - timedelta(seconds=1)).isoformat()
    )
    accounts, queue, _ = _accounts([Msg(newer), Msg(older, receipt="r2")])

    assert accounts.users()[0].accounts[0].cash_balance == 900
    assert queue.deleted == ["r1", "r2"]


def test_another_process_claiming_state_prevents_publish():
    accounts, _, failures = _accounts([Msg(envelope(snapshot()))])
    accounts.users()
    assert accounts._state.claim("e1")

    with pytest.raises(AccountsError, match="snapshot_claim_failed"):
        accounts.before_publish()
    assert failures.sent[-1]["body"]["payload"]["reason"] == "snapshot_claim_failed"


def test_same_process_can_publish_multiple_orders_from_one_claim():
    accounts, _, failures = _accounts([Msg(envelope(snapshot()))])
    accounts.users()
    accounts.before_publish()
    accounts.before_publish()

    assert not accounts._state.load().usable
    assert failures.sent == []


def test_failure_payload_identifies_source_message_and_correlation():
    accounts, _, failures = _accounts([Msg(envelope({"users": "invalid"}))])
    with pytest.raises(AccountsError):
        accounts.users()

    payload = failures.sent[-1]["body"]["payload"]
    assert payload["source_sqs_message_id"] == "m1"
    assert payload["source_correlation_id"] == "c1"


def test_explicit_unsupported_schema_version_is_rejected():
    data = json.loads(envelope(snapshot()))
    data["schemaVersion"] = 2
    accounts, queue, _ = _accounts([Msg(json.dumps(data))])

    with pytest.raises(AccountsError, match="malformed_envelope"):
        accounts.users()
    assert queue.deleted == []


def test_expired_committed_duplicate_is_acknowledged_before_fresh_state(monkeypatch):
    base = datetime.now(UTC)
    old = envelope(snapshot(cash=100), "old", occurred_at=base.isoformat())
    first, queue, _ = _accounts([Msg(old)])

    def fail(receipt):
        raise RuntimeError("delete failed")

    monkeypatch.setattr(queue, "delete", fail)
    with pytest.raises(AccountsError, match="acknowledgement_failed"):
        first.users()

    advanced = base + timedelta(seconds=901)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return advanced.astimezone(tz)

    monkeypatch.setattr("portfolio_rebalancer.accounts.datetime", Clock)
    fresh = envelope(snapshot(cash=900), "new", occurred_at=advanced.isoformat())
    second, redelivered, failures = _accounts(
        [Msg(old, receipt="duplicate"), Msg(fresh, receipt="fresh")], state=first._state
    )

    assert second.users()[0].accounts[0].cash_balance == 900
    assert redelivered.deleted == ["duplicate", "fresh"]
    assert failures.sent == []


def test_expired_outdated_snapshot_is_acknowledged_without_rollback():
    now = datetime.now(UTC)
    newer = envelope(snapshot(cash=900), "new", occurred_at=now.isoformat())
    older = envelope(
        snapshot(cash=100), "old", occurred_at=(now - timedelta(seconds=901)).isoformat()
    )
    accounts, queue, failures = _accounts([Msg(newer), Msg(older, receipt="old")])

    assert accounts.users()[0].accounts[0].cash_balance == 900
    assert queue.deleted == ["r1", "old"]
    assert failures.sent == []
