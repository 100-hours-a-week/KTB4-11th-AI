from unittest.mock import Mock

import httpx
import pytest
from portfolio_builder.competition.backend import Backend


def test_completion_posts_the_portfolio_under_the_users_subject(_settings):
    auth = Mock()
    backend = Backend(auth)

    backend.completed(_settings(), 42)

    method, path, subject = auth.request.call_args.args
    assert (method, path, subject) == ("POST", "/api/v1/competitions/10/portfolio", "100")
    assert auth.request.call_args.kwargs["json"] == {
        "participant_id": 1,
        "status": "completed",
        "portfolio_id": 42,
    }


def test_failure_posts_the_error(_settings):
    auth = Mock()

    Backend(auth).failed(_settings(), "boom")

    assert auth.request.call_args.kwargs["json"] == {
        "participant_id": 1,
        "status": "failed",
        "error": "boom",
    }


def test_a_transport_error_propagates(_settings):
    auth = Mock(request=Mock(side_effect=httpx.ConnectError("down")))

    with pytest.raises(httpx.ConnectError):
        Backend(auth).completed(_settings(), 42)
