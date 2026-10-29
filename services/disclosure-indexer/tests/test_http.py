import io
import json
from urllib.error import HTTPError

import pytest


def test_modal_result_redirect_reuses_request_without_resubmitting(monkeypatch):
    from disclosure_indexer.inference import post_json

    methods = []

    def respond(self, request, timeout):
        methods.append(request.get_method())
        if len(methods) == 1:
            raise HTTPError(request.full_url, 303, "See Other", {"Location": "?result=abc"}, None)
        return io.BytesIO(json.dumps({"ready": True}).encode())

    monkeypatch.setattr("urllib.request.OpenerDirector.open", respond)
    assert post_json("https://worker.modal.run", {}, {"Modal-Secret": "secret"}, 30) == {
        "ready": True
    }
    assert methods == ["POST", "GET"]


def test_redirect_does_not_reset_total_timeout(monkeypatch):
    from disclosure_indexer.inference import post_json

    now = [0.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])

    def respond(self, request, timeout):
        now[0] = 151
        raise HTTPError(request.full_url, 303, "See Other", {"Location": "?result=abc"}, None)

    monkeypatch.setattr("urllib.request.OpenerDirector.open", respond)
    with pytest.raises(TimeoutError):
        post_json("https://worker.modal.run", {}, {"Modal-Secret": "secret"}, 150)


def test_redirect_never_leaks_authentication_to_another_host(monkeypatch):
    from disclosure_indexer.inference import post_json

    def respond(self, request, timeout):
        raise HTTPError(
            request.full_url, 303, "See Other", {"Location": "https://other.example"}, None
        )

    monkeypatch.setattr("urllib.request.OpenerDirector.open", respond)
    with pytest.raises(ValueError, match="origin"):
        post_json("https://worker.modal.run", {}, {"Modal-Secret": "secret"}, 150)
