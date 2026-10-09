import json
from time import monotonic

from ktb_core.logging import StructuredLogger
from ktb_core.queue import Queue
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from portfolio_rebalancer.orders import envelope
from portfolio_rebalancer.snapshot import Snapshot, User

SNAPSHOT_TYPE = "account.snapshot"
FAILURE_TYPE = "account.snapshot.rejected"
FAILURE_GROUP = "ai-server"
POLL_SECONDS = 1


class Envelope(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(alias="eventId", min_length=1)
    correlation_id: str | None = Field(default=None, alias="correlationId")
    occurred_at: str | None = Field(default=None, alias="occurredAt")
    type: str
    payload: dict[str, object]


class Accounts:
    def __init__(
        self,
        queue: Queue,
        failures: Queue,
        log: StructuredLogger,
        correlation_id: str,
        *,
        budget: float,
    ) -> None:
        self._queue = queue
        self._failures = failures
        self._log = log
        self._correlation_id = correlation_id
        self._budget = budget
        self._users: list[User] = []
        self._seen: set[str] = set()
        self.rejected = 0

    def users(self) -> list[User]:
        deadline = monotonic() + self._budget
        while True:
            if monotonic() >= deadline:
                self._log.warning("drain_budget_exceeded", budget_seconds=self._budget)
                break
            messages = self._queue.receive(wait=POLL_SECONDS)
            if not messages:
                break
            for message in messages:
                if self._accept(message.id, message.body):
                    self._queue.delete(message.receipt)
        return self._users

    def _reject(
        self, log: StructuredLogger, reason: str, detail: str, event_id: str | None
    ) -> bool:
        self.rejected += 1
        payload = {"reason": reason, "detail": detail, "rejected_event_id": event_id}
        failure_id, body = envelope(FAILURE_TYPE, payload, self._correlation_id)
        try:
            message_id = self._failures.send(body, group=FAILURE_GROUP, dedup=failure_id)
        except Exception as error:
            log.error(
                "snapshot_rejection_unreported",
                result="failed",
                reason=reason,
                detail=detail,
                error=f"{type(error).__name__}: {error}",
            )
            return False
        log.warning(
            "snapshot_rejected",
            result="rejected",
            reason=reason,
            detail=detail,
            failure_event_id=failure_id,
            failure_sqs_message_id=message_id,
        )
        return True

    def _accept(self, message_id: str, body: str) -> bool:
        log = self._log.bind(sqs_message_id=message_id)
        try:
            message = Envelope.model_validate(json.loads(body))
        except (ValidationError, ValueError) as error:
            return self._reject(log, "malformed_envelope", str(error), None)
        log = log.bind(event_id=message.event_id, correlation_id=message.correlation_id)
        if message.type != SNAPSHOT_TYPE:
            log.warning("snapshot_ignored", result="failed", type=message.type)
            return False
        if message.event_id in self._seen:
            log.info("snapshot_duplicate", result="duplicate")
            return True
        try:
            snapshot = Snapshot.model_validate(message.payload)
        except ValidationError as error:
            return self._reject(log, "malformed_payload", str(error), message.event_id)
        if not snapshot.users:
            return self._reject(log, "empty_snapshot", "no users in the snapshot", message.event_id)
        self._seen.add(message.event_id)
        self._users = snapshot.users
        log.info(
            "snapshot_applied",
            result="applied",
            user_count=len(snapshot.users),
            pending_orders=sum(len(a.pending_orders) for u in snapshot.users for a in u.accounts),
        )
        return True

    def forget(self, order_ids: set[int]) -> None:
        self._users = [
            user.model_copy(
                update={
                    "accounts": [
                        account.model_copy(
                            update={
                                "pending_orders": [
                                    pending
                                    for pending in account.pending_orders
                                    if pending.order_id not in order_ids
                                ]
                            }
                        )
                        for account in user.accounts
                    ]
                }
            )
            for user in self._users
        ]
