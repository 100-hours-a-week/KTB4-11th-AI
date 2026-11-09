from unittest.mock import MagicMock, Mock

import pytest
from report_builder import __main__ as main


@pytest.mark.parametrize("queue_setting", ["reports", "https://sqs.example.com/123/reports"])
def test_bootstrap_accepts_queue_name_or_url(monkeypatch, queue_setting):
    queue_url = "https://sqs.example.com/123/reports"
    for name, value in {
        "POSTGRES_DSN": "postgresql+psycopg://localhost/reports",
        "LLM_API_KEY": "secret",
        "LLM_MODEL": "model",
        "BACKEND_BASE_URI": "https://backend.example.com",
        "BACKEND_JWT_SECRET": "x" * 32,
        "BACKEND_JWT_ISSUER": "backend",
        "SQS_QUEUE_URL": queue_setting,
    }.items():
        monkeypatch.setenv(f"REPORT_BUILDER_{name}", value)
    sqs = Mock()
    sqs.get_queue_url.return_value = {"QueueUrl": queue_url}
    sqs.receive_message.side_effect = KeyboardInterrupt
    boto_client = Mock(return_value=sqs)
    monkeypatch.setattr(main.boto3, "client", boto_client)
    engine = Mock()
    monkeypatch.setattr(main.sa, "create_engine", Mock(return_value=engine))
    http_client = MagicMock()
    monkeypatch.setattr(main.httpx, "Client", http_client)
    monkeypatch.setattr(main, "structured_generator", Mock())
    monkeypatch.setattr(main, "setup_logging", Mock())

    with pytest.raises(KeyboardInterrupt):
        main.main()

    if queue_setting == "reports":
        sqs.get_queue_url.assert_called_once_with(QueueName="reports")
    else:
        sqs.get_queue_url.assert_not_called()
    boto_client.assert_called_once()
    assert boto_client.call_args.args == ("sqs",)
    sqs.receive_message.assert_called_once_with(
        QueueUrl=queue_url,
        MaxNumberOfMessages=1,
        WaitTimeSeconds=20,
        VisibilityTimeout=300,
    )
    engine.dispose.assert_called_once_with()
    http_client.return_value.__exit__.assert_called_once()
