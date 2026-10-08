import json
from unittest.mock import Mock

import pytest
from report_builder import worker
from report_builder.report import Evidence, Report, Thought

MESSAGE = {
    "competition_id": 12,
    "participant_id": 34,
    "user_id": 56,
    "portfolio_reason_ids": ["00000000-0000-0000-0000-000000000001"],
}


@pytest.fixture
def dependencies(monkeypatch):
    auth = Mock()
    auth.request.return_value = Mock(status_code=200)
    engine = Mock()
    structured = Mock()
    monkeypatch.setattr(
        worker,
        "load_evidence",
        Mock(return_value=Evidence(reasons=[], news=[{"title": "N", "summary": "S"}])),
    )
    monkeypatch.setattr(
        worker,
        "build_report",
        Mock(
            return_value=Report(
                news=[{"title": "N", "summary": "S"}],
                thoughts=[Thought(title="T", text="X")],
            )
        ),
    )
    return auth, engine, structured


@pytest.mark.parametrize(
    "changes",
    [
        {"competition_id": "bad"},
        {"participant_id": None},
        {"user_id": "bad"},
        {"portfolio_reason_ids": []},
        {"portfolio_reason_ids": ["bad-uuid"]},
    ],
)
def test_invalid_message_is_not_deleted(changes, dependencies):
    auth, engine, structured = dependencies
    sqs = Mock()

    worker.process_message(
        {
            "Body": json.dumps({**MESSAGE, **changes}),
            "ReceiptHandle": "receipt",
            "_queue_url": "https://sqs.example/queue",
        },
        sqs,
        auth,
        engine,
        structured,
    )

    sqs.delete_message.assert_not_called()
    auth.request.assert_not_called()


def test_posts_report_and_deletes_after_success(dependencies):
    auth, engine, structured = dependencies
    sqs = Mock()
    message = {
        "Body": json.dumps(MESSAGE),
        "ReceiptHandle": "receipt",
        "_queue_url": "https://sqs.example/queue",
    }

    worker.process_message(message, sqs, auth, engine, structured)

    auth.request.assert_called_once_with(
        "POST",
        "/api/v1/competitions/12/report/34",
        "56",
        json={"news": [{"title": "N", "summary": "S"}], "thoughts": [{"title": "T", "text": "X"}]},
    )
    sqs.delete_message.assert_called_once_with(
        QueueUrl="https://sqs.example/queue", ReceiptHandle="receipt"
    )


@pytest.mark.parametrize("failure", ["query", "llm", "callback"])
def test_processing_failures_do_not_delete(failure, dependencies):
    auth, engine, structured = dependencies
    sqs = Mock()
    if failure == "query":
        worker.load_evidence.side_effect = RuntimeError("query")
    elif failure == "llm":
        worker.build_report.side_effect = RuntimeError("llm")
    else:
        auth.request.side_effect = RuntimeError("callback")

    worker.process_message(
        {
            "Body": json.dumps(MESSAGE),
            "ReceiptHandle": "receipt",
            "_queue_url": "https://sqs.example/queue",
        },
        sqs,
        auth,
        engine,
        structured,
    )

    sqs.delete_message.assert_not_called()


def test_delete_failure_propagates(dependencies):
    auth, engine, structured = dependencies
    sqs = Mock()
    sqs.delete_message.side_effect = RuntimeError("delete")

    with pytest.raises(RuntimeError, match="delete"):
        worker.process_message(
            {
                "Body": json.dumps(MESSAGE),
                "ReceiptHandle": "receipt",
                "_queue_url": "https://sqs.example/queue",
            },
            sqs,
            auth,
            engine,
            structured,
        )


def test_processing_timeout_stays_below_visibility_timeout():
    assert worker.PROCESSING_TIMEOUT < 300
    assert worker.OPENROUTER_TIMEOUT == 240
    assert worker.BACKEND_TIMEOUT == 10
