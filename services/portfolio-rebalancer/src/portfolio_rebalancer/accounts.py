import json
from datetime import UTC, datetime
from time import monotonic
from typing import Literal, NoReturn

from ktb_core.logging import StructuredLogger
from ktb_core.queue import Queue
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from portfolio_rebalancer.orders import envelope
from portfolio_rebalancer.snapshot import Snapshot, User
from portfolio_rebalancer.snapshot_store import SnapshotStore, StoredSnapshot

SNAPSHOT_TYPE = "account.snapshot"
FAILURE_TYPE = "account.snapshot.rejected"
FETCH_FAILURE_TYPE = "account.snapshot.failed"
FAILURE_GROUP = "ai-server"
POLL_SECONDS = 20


class AccountsError(RuntimeError):
    pass


class Envelope(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    event_id: str = Field(alias="eventId", min_length=1)
    correlation_id: str | None = Field(default=None, alias="correlationId")
    occurred_at: AwareDatetime = Field(alias="occurredAt")
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
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
        state: SnapshotStore,
        max_age: float = 900,
    ) -> None:
        self._queue = queue
        self._failures = failures
        self._log = log.bind(queue_role="accounts", direction="consume")
        self._correlation_id = correlation_id
        self._budget = budget
        self._users: list[User] = []
        self._state = state
        self._max_age = max_age
        self._current: StoredSnapshot | None = None
        self._claimed: set[str] = set()
        self.rejected = 0
        self.failed = 0

    def users(self) -> list[User]:
        if self._current is None:
            try:
                saved = self._state.load()
                if saved is not None and saved.usable:
                    self._users = Snapshot.model_validate(saved.payload).users
                    self._current = saved
                    self._log.info(
                        "snapshot_restored",
                        event_id=saved.event_id,
                        correlation_id=saved.correlation_id,
                        result="restored",
                    )
            except Exception as error:
                raise self._failure("snapshot_restore_failed", type(error).__name__) from error
        deadline = monotonic() + self._budget
        while True:
            if monotonic() >= deadline:
                raise self._failure("drain_budget_exceeded", f"budget_seconds={self._budget}")
            try:
                messages = self._queue.receive(wait=POLL_SECONDS)
            except Exception as error:
                raise self._failure("receive_failed", f"{type(error).__name__}: {error}") from error
            if monotonic() >= deadline:
                raise self._failure("drain_budget_exceeded", f"budget_seconds={self._budget}")
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
        if self._current is None:
            raise self._failure(
                "snapshot_missing", "no fresh reusable snapshot; publish a new snapshot"
            )
        if (datetime.now(UTC) - self._current.occurred_at).total_seconds() > self._max_age:
            raise self._failure("snapshot_expired", "publish a fresh snapshot")
        return self._users

    def before_publish(self) -> None:
        if self._current is None:
            raise self._failure("snapshot_missing", "cannot publish without a snapshot")
        if (datetime.now(UTC) - self._current.occurred_at).total_seconds() > self._max_age:
            raise self._failure("snapshot_expired", "publish a fresh snapshot")
        if self._current.event_id in self._claimed:
            return
        try:
            claimed = self._state.claim(self._current.event_id)
        except Exception as error:
            raise self._failure("snapshot_claim_failed", type(error).__name__) from error
        if not claimed:
            raise self._failure("snapshot_claim_failed", "snapshot already used or superseded")
        self._claimed.add(self._current.event_id)
        self._log.info(
            "snapshot_claimed",
            event_id=self._current.event_id,
            correlation_id=self._current.correlation_id,
            result="claimed",
        )

    def _report(
        self,
        log: StructuredLogger,
        reason: str,
        detail: str,
        event_id: str | None,
        *,
        failure_type: str,
    ) -> bool:
        payload = {
            "reason": reason,
            "detail": detail,
            "rejected_event_id": event_id,
            "source_sqs_message_id": log.bound.get("sqs_message_id"),
            "source_correlation_id": log.bound.get("correlation_id"),
        }
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
    ) -> NoReturn:
        self.rejected += 1
        if not self._report(log, reason, detail, event_id, failure_type=FAILURE_TYPE):
            raise AccountsError("snapshot_rejection_unreported")
        raise AccountsError(f"snapshot_rejected: {reason}")

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
            self._reject(log, "malformed_envelope", detail, None)
        log = log.bind(
            event_id=message.event_id, correlation_id=message.correlation_id or self._correlation_id
        )
        if message.type != SNAPSHOT_TYPE:
            log.warning("snapshot_ignored", result="failed", type=message.type)
            self._reject(
                log, "unsupported_type", "unsupported snapshot event type", message.event_id
            )
        try:
            snapshot = Snapshot.model_validate(message.payload)
        except ValidationError as error:
            detail = json.dumps(error.errors(include_input=False, include_url=False))
            self._reject(log, "malformed_payload", detail, message.event_id)
        age = (datetime.now(UTC) - message.occurred_at).total_seconds()
        try:
            saved, result = self._state.accept(
                message.event_id,
                message.occurred_at,
                snapshot.model_dump(),
                message.correlation_id,
                valid_time=-30 <= age <= self._max_age,
            )
        except Exception as error:
            raise self._failure(
                "snapshot_store_failed", type(error).__name__, log, message.event_id
            ) from error
        if result == "invalid_time":
            self._reject(
                log, "snapshot_time_invalid", "timestamp outside freshness window", message.event_id
            )
        if result != "applied":
            log.info(f"snapshot_{result}", result=result)
            if saved is not None and saved.usable:
                self._current = saved
                self._users = Snapshot.model_validate(saved.payload).users
            return True, log, message.event_id
        self._current = saved
        self._users = snapshot.users
        log.info(
            "snapshot_applied",
            result="applied",
            user_count=len(snapshot.users),
            pending_orders=sum(len(a.pending_orders) for u in snapshot.users for a in u.accounts),
        )
        return True, log, message.event_id
