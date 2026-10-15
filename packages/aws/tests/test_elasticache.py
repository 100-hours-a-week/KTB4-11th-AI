import asyncio
from urllib.parse import parse_qs

import botocore.session
import pytest
from botocore.exceptions import NoCredentialsError
from ktb_aws.elasticache import ElastiCacheIAMProvider


def test_iam_token_is_a_presigned_connect_url_for_the_serverless_cache(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    user, token = ElastiCacheIAMProvider("backend", "my-cache", "ap-northeast-2").get_credentials()

    assert user == "backend"
    assert token.startswith("my-cache/?")
    query = parse_qs(token.split("?", 1)[1])
    assert query["Action"] == ["connect"]
    assert query["User"] == ["backend"]
    assert query["ResourceType"] == ["ServerlessCache"]
    assert query["X-Amz-Expires"] == ["900"]
    assert "X-Amz-Signature" in query


def test_iam_token_includes_session_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "session-token")

    _, token = ElastiCacheIAMProvider("backend", "my-cache", "ap-northeast-2").get_credentials()

    assert parse_qs(token.split("?", 1)[1])["X-Amz-Security-Token"] == ["session-token"]


def test_async_credentials_return_the_user_and_signed_token(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

    user, token = asyncio.run(
        ElastiCacheIAMProvider("backend", "my-cache", "ap-northeast-2").get_credentials_async()
    )

    assert user == "backend"
    assert "X-Amz-Signature=" in token


def test_missing_aws_credentials_cannot_sign_a_token(monkeypatch):
    session = botocore.session.get_session()
    monkeypatch.setattr(session, "get_credentials", lambda: None)
    monkeypatch.setattr(botocore.session, "get_session", lambda: session)
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", "http://127.0.0.1:9/credentials")

    with pytest.raises(NoCredentialsError):
        ElastiCacheIAMProvider("backend", "my-cache", "ap-northeast-2").get_credentials()
