import json
import uuid
from datetime import UTC, datetime

from ktb_core.queue import Queue

CANCEL_TYPE = "order.cancel"
PLACE_TYPE = "order.place"


class PublishError(Exception):
    def __init__(
        self, message: str, *, event_id: str | None = None, correlation_id: str | None = None
    ) -> None:
        super().__init__(message)
        self.fields = {
            "event_id": event_id,
            "correlation_id": correlation_id,
            "sqs_message_id": None,
        }


def envelope(event_type: str, payload: dict, correlation_id: str) -> tuple[str, str]:
    event_id = str(uuid.uuid4())
    body = json.dumps(
        {
            "eventId": event_id,
            "correlationId": correlation_id,
            "occurredAt": datetime.now(UTC).isoformat(),
            "type": event_type,
            "payload": payload,
        },
        ensure_ascii=False,
    )
    return event_id, body


class Orders:
    def __init__(self, queue: Queue, correlation_id: str) -> None:
        self._queue = queue
        self._correlation_id = correlation_id

    def _publish(self, event_type: str, account_id: int, payload: dict) -> dict[str, str]:
        event_id, body = envelope(event_type, payload, self._correlation_id)
        try:
            message_id = self._queue.send(body, group=str(account_id), dedup=event_id)
        except Exception as error:
            raise PublishError(
                f"{type(error).__name__}: {error}",
                event_id=event_id,
                correlation_id=self._correlation_id,
            ) from error
        return {
            "event_id": event_id,
            "sqs_message_id": message_id,
            "correlation_id": self._correlation_id,
        }

    def cancel(self, user_id: int, account_id: int, order_id: int) -> dict[str, str]:
        return self._publish(
            CANCEL_TYPE,
            account_id,
            {
                "user_id": user_id,
                "account_id": account_id,
                "order_id": order_id,
                "status": "cancelled",
            },
        )

    def place(
        self, user_id: int, account_id: int, body: dict, replaces: list[int]
    ) -> dict[str, str]:
        return self._publish(
            PLACE_TYPE,
            account_id,
            {
                "user_id": user_id,
                "account_id": account_id,
                "replaces_order_ids": replaces,
                **body,
            },
        )
