from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Message:
    id: str
    body: str
    receipt: str


class Queue(Protocol):
    def send(self, body: str, *, group: str, dedup: str) -> str: ...

    def receive(self, *, limit: int = 10, wait: int = 20) -> list[Message]: ...

    def delete(self, receipt: str) -> None: ...


class SqsQueue:
    def __init__(self, client: Any, url: str) -> None:
        self._client = client
        self._url = url

    def send(self, body: str, *, group: str, dedup: str) -> str:
        response = self._client.send_message(
            QueueUrl=self._url,
            MessageBody=body,
            MessageGroupId=group,
            MessageDeduplicationId=dedup,
        )
        return response["MessageId"]

    def receive(self, *, limit: int = 10, wait: int = 20) -> list[Message]:
        response = self._client.receive_message(
            QueueUrl=self._url,
            MaxNumberOfMessages=limit,
            WaitTimeSeconds=wait,
        )
        return [
            Message(message["MessageId"], message["Body"], message["ReceiptHandle"])
            for message in response.get("Messages", ())
        ]

    def delete(self, receipt: str) -> None:
        self._client.delete_message(QueueUrl=self._url, ReceiptHandle=receipt)
