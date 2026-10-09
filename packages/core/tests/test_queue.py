from ktb_core.queue import SqsQueue


class FakeSqs:
    def __init__(self, pages=()):
        self.sent = []
        self.deleted = []
        self.received = []
        self._pages = list(pages)

    def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return {"MessageId": f"m{len(self.sent)}"}

    def receive_message(self, **kwargs):
        self.received.append(kwargs)
        return self._pages.pop(0) if self._pages else {}

    def delete_message(self, **kwargs):
        self.deleted.append(kwargs)


def test_send_passes_the_group_and_dedup_ids_and_returns_the_message_id():
    client = FakeSqs()

    message_id = SqsQueue(client, "https://q/orders.fifo").send(
        '{"eventId": "e1"}', group="41", dedup="e1"
    )

    assert message_id == "m1"
    assert client.sent == [
        {
            "QueueUrl": "https://q/orders.fifo",
            "MessageBody": '{"eventId": "e1"}',
            "MessageGroupId": "41",
            "MessageDeduplicationId": "e1",
        }
    ]


def test_receive_maps_the_sqs_shape_and_long_polls():
    client = FakeSqs([{"Messages": [{"MessageId": "m9", "Body": "{}", "ReceiptHandle": "r9"}]}])

    messages = SqsQueue(client, "https://q/accounts.fifo").receive(limit=5, wait=1)

    assert [(m.id, m.body, m.receipt) for m in messages] == [("m9", "{}", "r9")]
    assert client.received == [
        {
            "QueueUrl": "https://q/accounts.fifo",
            "MaxNumberOfMessages": 5,
            "WaitTimeSeconds": 1,
        }
    ]


def test_an_empty_response_has_no_messages_key():
    assert SqsQueue(FakeSqs(), "https://q/accounts.fifo").receive() == []


def test_delete_acknowledges_by_receipt():
    client = FakeSqs()

    SqsQueue(client, "https://q/accounts.fifo").delete("r9")

    assert client.deleted == [{"QueueUrl": "https://q/accounts.fifo", "ReceiptHandle": "r9"}]
