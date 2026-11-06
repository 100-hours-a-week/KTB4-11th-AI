from dataclasses import dataclass
from typing import Any, Protocol

from botocore.config import Config

POLL_CONFIG = Config(connect_timeout=10, read_timeout=25, retries={"max_attempts": 1})


@dataclass(frozen=True)
class Message:
    id: str
    body: str
    receipt: str


class Queue(Protocol):
    def send(self, body: str, *, group: str | None = ..., dedup: str | None = ...) -> str: ...

    def receive(
        self, *, limit: int = ..., wait: int = ..., visibility: int | None = ...
    ) -> list[Message]: ...

    def delete(self, receipt: str) -> None: ...


class SqsQueue:
    def __init__(self, client: Any, url_or_name: str) -> None:
        self._client = client
        self._url = (
            url_or_name
            if url_or_name.startswith("http")
            else client.get_queue_url(QueueName=url_or_name)["QueueUrl"]
        )

    @property
    def url(self) -> str:
        return self._url

    def send(self, body: str, *, group: str | None = None, dedup: str | None = None) -> str:
        arguments: dict[str, Any] = {"QueueUrl": self._url, "MessageBody": body}
        if group is not None:
            arguments["MessageGroupId"] = group
        if dedup is not None:
            arguments["MessageDeduplicationId"] = dedup
        return self._client.send_message(**arguments)["MessageId"]

    def receive(
        self, *, limit: int = 10, wait: int = 20, visibility: int | None = None
    ) -> list[Message]:
        arguments: dict[str, Any] = {
            "QueueUrl": self._url,
            "MaxNumberOfMessages": limit,
            "WaitTimeSeconds": wait,
        }
        if visibility is not None:
            arguments["VisibilityTimeout"] = visibility
        response = self._client.receive_message(**arguments)
        return [
            Message(message["MessageId"], message["Body"], message["ReceiptHandle"])
            for message in response.get("Messages", ())
        ]

    def delete(self, receipt: str) -> None:
        self._client.delete_message(QueueUrl=self._url, ReceiptHandle=receipt)
