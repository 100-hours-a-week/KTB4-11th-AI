import subprocess
import sys
from unittest.mock import Mock

import pytest
from competition import __main__ as entry
from competition.settings import Settings


def test_worker_imports_without_builder_or_generation_dependencies():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.abc
import sys

class IntakeOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = {'portfolio_builder', 'langchain', 'langchain_openrouter', 'numpy', 'talib'}
        if fullname.split('.')[0] in blocked:
            raise ImportError(f'generation dependency requested: {fullname}')

sys.meta_path.insert(0, IntakeOnly())
import competition.__main__
from competition.settings import Settings
Settings(postgres_dsn='postgresql+psycopg://ktb:test@localhost/ktb_test',
         queue_url='https://sqs.example/settings.fifo', jev_api_key='secret',
         jev_choice_min_confidence=0.6)
""",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_worker_polls_only_intake_with_its_own_configuration(monkeypatch):
    settings = Settings(
        postgres_dsn="postgresql+psycopg://ktb:test@localhost/ktb_test",
        queue_url="https://sqs.example/settings.fifo",
        jev_api_key="secret",
        jev_choice_min_confidence=0.6,
        visibility_timeout=600,
    )
    monkeypatch.setattr(entry, "Settings", lambda: settings)
    sqs = Mock()
    sqs.receive_message.side_effect = [
        {"Messages": [{"MessageId": "m", "Body": "{}", "ReceiptHandle": "r"}]},
        SystemExit(0),
    ]
    monkeypatch.setattr(entry.boto3, "client", lambda *args, **kwargs: sqs)
    engine = Mock()
    monkeypatch.setattr(entry.sa, "create_engine", lambda dsn: engine)

    with pytest.raises(SystemExit):
        entry.main()

    assert sqs.receive_message.call_args.kwargs == {
        "QueueUrl": "https://sqs.example/settings.fifo",
        "MaxNumberOfMessages": 1,
        "WaitTimeSeconds": 20,
        "VisibilityTimeout": 600,
    }
    sqs.get_queue_url.assert_not_called()
    sqs.delete_message.assert_not_called()
    engine.dispose.assert_called_once()


def test_worker_passes_each_received_message_to_intake(monkeypatch):
    settings = Settings(
        postgres_dsn="postgresql+psycopg://ktb:test@localhost/ktb_test",
        queue_url="https://sqs.example/settings.fifo",
        jev_api_key="secret",
        jev_choice_min_confidence=0.6,
    )
    monkeypatch.setattr(entry, "Settings", lambda: settings)
    sqs = Mock()
    sqs.receive_message.side_effect = [
        {"Messages": [{"MessageId": "m", "Body": "{}", "ReceiptHandle": "r"}]},
        SystemExit(0),
    ]
    monkeypatch.setattr(entry.boto3, "client", lambda *args, **kwargs: sqs)
    monkeypatch.setattr(entry.sa, "create_engine", lambda dsn: Mock())

    def accept(message, queue, engine, settings, log):
        queue.delete(message.receipt)

    monkeypatch.setattr(entry, "process_message", accept)

    with pytest.raises(SystemExit):
        entry.main()

    sqs.delete_message.assert_called_once_with(QueueUrl=settings.queue_url, ReceiptHandle="r")
