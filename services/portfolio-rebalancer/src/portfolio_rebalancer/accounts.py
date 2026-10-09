import json
from time import monotonic

from ktb_core.logging import StructuredLogger
from ktb_core.queue import Queue
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from portfolio_rebalancer.orders import envelope
from portfolio_rebalancer.snapshot import Snapshot, User

SNAPSHOT_TYPE = "account.snapshot"
FAILURE_TYPE = "account.snapshot.rejected"
FETCH_FAILURE_TYPE = "account.snapshot.failed"
FAILURE_GROUP = "ai-server"
POLL_SECONDS = 1


class AccountsError(RuntimeError):
    pass


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
        self._log = log.bind(queue_role="accounts", direction="consume")
        self._correlation_id = correlation_id
        self._budget = budget
        self._users: list[User] = []
        self._seen: set[str] = set()
        self.rejected = 0
        self.failed = 0

    def users(self) -> list[User]:
        deadline = monotonic() + self._budget
        while True:
            if monotonic() >= deadline:
                raise self._failure("drain_budget_exceeded", f"budget_seconds={self._budget}")
            try:
                messages = self._queue.receive(wait=POLL_SECONDS)
            except Exception as error:
                raise self._failure("receive_failed", f"{type(error).__name__}: {error}") from error
            if not messages:
                break
            for message in messages:
                handled, log, event_id = self._accept(message.id, message.body)
                if handled:
                    try:
                        self._queue.delete(message.receipt)
                    except Exception as error:
                        raise self._failure(
                            "acknowledgement_failed",
                            f"{type(error).__name__}: {error}",
                            log,
                            event_id,
                        ) from error
                    log.info("snapshot_acknowledged", result="acknowledged")
        if not self._users:
            if self.rejected:
                raise AccountsError("snapshot_missing: no valid snapshot")
            raise self._failure("snapshot_missing", "no valid snapshot received for this tick")
        return self._users

    def _report(
        self,
        log: StructuredLogger,
        reason: str,
        detail: str,
        event_id: str | None,
        *,
        failure_type: str,
    ) -> bool:
        payload = {"reason": reason, "detail": detail, "rejected_event_id": event_id}
        failure_id, body = envelope(failure_type, payload, self._correlation_id)
        fields = {
            "queue_role": "failures",
            "direction": "publish",
            "event_id": failure_id,
            "correlation_id": self._correlation_id,
            "source_sqs_message_id": log.bound.get("sqs_message_id"),
            "source_event_id": event_id,
            "source_correlation_id": log.bound.get("correlation_id"),
            "reason": reason,
            "detail": detail,
            "failure_event_id": failure_id,
            "failure_correlation_id": self._correlation_id,
        }
        try:
            message_id = self._failures.send(body, group=FAILURE_GROUP, dedup=failure_id)
        except Exception as error:
            log.error(
                "snapshot_rejection_unreported",
                **fields,
                result="failed",
                failure_result="failed",
                sqs_message_id=None,
                failure_sqs_message_id=None,
                error=f"{type(error).__name__}: {error}",
            )
            return False
        rejected = failure_type == FAILURE_TYPE
        (log.warning if rejected else log.error)(
            "snapshot_rejected" if rejected else "snapshot_failed",
            **fields,
            result="rejected" if rejected else "failed",
            failure_result="published",
            sqs_message_id=message_id,
            failure_sqs_message_id=message_id,
        )
        return True

    def _failure(
        self,
        reason: str,
        detail: str,
        log: StructuredLogger | None = None,
        event_id: str | None = None,
    ) -> AccountsError:
        self.failed += 1
        self._report(
            log
            if log is not None
            else self._log.bind(sqs_message_id=None, correlation_id=self._correlation_id),
            reason,
            detail,
            event_id,
            failure_type=FETCH_FAILURE_TYPE,
        )
        return AccountsError(reason)

    def _reject(
        self, log: StructuredLogger, reason: str, detail: str, event_id: str | None
    ) -> bool:
        self.rejected += 1
        if not self._report(log, reason, detail, event_id, failure_type=FAILURE_TYPE):
            raise AccountsError("snapshot_rejection_unreported")
        return True

    def _accept(self, message_id: str, body: str) -> tuple[bool, StructuredLogger, str | None]:
        log = self._log.bind(sqs_message_id=message_id)
        try:
            message = Envelope.model_validate(json.loads(body))
        except (ValidationError, ValueError) as error:
            log = log.bind(event_id=None, correlation_id=self._correlation_id)
            detail = (
                json.dumps(error.errors(include_input=False, include_url=False))
                if isinstance(error, ValidationError)
                else str(error)
            )
            return self._reject(log, "malformed_envelope", detail, None), log, None
        log = log.bind(
            event_id=message.event_id, correlation_id=message.correlation_id or self._correlation_id
        )
        if message.type != SNAPSHOT_TYPE:
            log.warning("snapshot_ignored", result="failed", type=message.type)
            return False, log, message.event_id
        if message.event_id in self._seen:
            log.info("snapshot_duplicate", result="duplicate")
            return True, log, message.event_id
        try:
            snapshot = Snapshot.model_validate(message.payload)
        except ValidationError as error:
            detail = json.dumps(error.errors(include_input=False, include_url=False))
            return (
                self._reject(log, "malformed_payload", detail, message.event_id),
                log,
                message.event_id,
            )
        if not snapshot.users:
            return (
                self._reject(log, "empty_snapshot", "no users in the snapshot", message.event_id),
                log,
                message.event_id,
            )
        self._seen.add(message.event_id)
        self._users = snapshot.users
        log.info(
            "snapshot_applied",
            result="applied",
            user_count=len(snapshot.users),
            pending_orders=sum(len(a.pending_orders) for u in snapshot.users for a in u.accounts),
        )
        return True, log, message.event_id
